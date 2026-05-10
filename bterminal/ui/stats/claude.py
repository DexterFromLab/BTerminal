"""Claude Code stats reader (T3.1 — extracted from monolithic stats.py).

Port of legacy _SessionStatsReader + _STATS_PRICING + plan-usage
fetcher into a class implementing AbstractStatsReader. Behavior is
1:1 with pre-T3 code; the abstraction lets T3.2 add a Copilot reader
without touching the widget.

Reads:
  - Session log: ~/.claude/projects/<sanitized_cwd>/*.jsonl (newest)
  - OAuth token: ~/.claude/.credentials.json (claudeAiOauth.accessToken)
  - Usage API:  https://api.anthropic.com/api/oauth/usage (Bearer auth)

Caches the plan-usage response for 60s so the GTK 5s refresh tick
doesn't hammer the API. Cache is module-level (shared across all
ClaudeStatsReader instances — same OAuth account would return the
same data anyway).
"""
from __future__ import annotations

import glob
import json
import os
import re
import threading
import time as _time_mod
import urllib.request
from datetime import datetime, timezone
from typing import Optional

from bterminal.ui.stats.base import AbstractStatsReader, PlanUsage, TokenStats


# ─── Plan-usage cache (module-level so all readers share) ───────────────────

_CLAUDE_CREDENTIALS_FILE = os.path.expanduser("~/.claude/.credentials.json")
_CLAUDE_USAGE_API = "https://api.anthropic.com/api/oauth/usage"
_CLAUDE_OAUTH_BETA = "oauth-2025-04-20"

_usage_cache = {"data": None, "fetched_at": 0.0, "fetching": False}
_USAGE_TTL = 60.0


def _get_claude_oauth_token() -> Optional[str]:
    """Read OAuth access token from Claude credentials file.

    Returns None if the file is missing, malformed, or the token has
    expired.
    """
    try:
        with open(_CLAUDE_CREDENTIALS_FILE, encoding="utf-8") as fh:
            creds = json.load(fh)
        oauth = creds.get("claudeAiOauth", {})
        token = oauth.get("accessToken")
        expires = oauth.get("expiresAt", 0)
        if token and expires > _time_mod.time() * 1000:
            return token
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return None


def _fetch_claude_usage() -> Optional[dict]:
    """Hit Claude's plan-usage endpoint. Returns API JSON or None."""
    token = _get_claude_oauth_token()
    if not token:
        return None
    req = urllib.request.Request(
        _CLAUDE_USAGE_API,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "claude-code/2.1.90",
            "Authorization": f"Bearer {token}",
            "anthropic-beta": _CLAUDE_OAUTH_BETA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if "error" not in data:
                return data
    except Exception:
        pass
    return None


def _refresh_usage_cache() -> None:
    """Background-thread fetch; updates module cache in place."""
    if _usage_cache["fetching"]:
        return
    _usage_cache["fetching"] = True

    def _bg():
        try:
            data = _fetch_claude_usage()
            _usage_cache["data"] = data
            _usage_cache["fetched_at"] = _time_mod.time()
        finally:
            _usage_cache["fetching"] = False

    threading.Thread(target=_bg, daemon=True).start()


def _get_usage_cache() -> Optional[dict]:
    """Return cached usage dict, kicking off a background refresh if stale."""
    age = _time_mod.time() - _usage_cache["fetched_at"]
    if age > _USAGE_TTL:
        _refresh_usage_cache()
    return _usage_cache["data"]


# ─── Pricing table (USD per 1M tokens) ──────────────────────────────────────
#
# Mirrors the table embedded in providers/defaults.json[providers.claude.pricing]
# for backward-compat: legacy callers (the widget) still read from this dict
# directly via the reader's pricing property.

_STATS_PRICING = {
    "claude-opus-4-7":   {"input": 15.0,  "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "claude-opus-4-6":   {"input": 15.0,  "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "claude-sonnet-4-6": {"input":  3.0,  "output": 15.0, "cache_read": 0.30, "cache_write":  3.75},
    "claude-haiku-4-5":  {"input":  0.80, "output":  4.0, "cache_read": 0.08, "cache_write":  1.00},
    "claude-opus-4-5":   {"input": 15.0,  "output": 75.0, "cache_read": 1.50, "cache_write": 18.75},
    "claude-sonnet-4-5": {"input":  3.0,  "output": 15.0, "cache_read": 0.30, "cache_write":  3.75},
}
_STATS_DEFAULT_PRICE = {"input": 3.0, "output": 15.0,
                        "cache_read": 0.30, "cache_write": 3.75}

_CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")


# ─── Reader ─────────────────────────────────────────────────────────────────

class ClaudeStatsReader(AbstractStatsReader):
    """Reads Claude Code's per-session JSONL transcript and OAuth usage API."""

    def __init__(self, project_dir: str):
        self.project_dir = project_dir.rstrip("/")
        self._start = datetime.now(timezone.utc)
        self._cached_path: Optional[str] = None

    def _find_log_file(self) -> Optional[str]:
        """Locate the newest JSONL session file for this project_dir.

        Sanitizes project_dir to match Claude's directory naming under
        ~/.claude/projects/. Caches the resolved path; if the cached
        file is gone, re-resolves.
        """
        if self._cached_path and os.path.isfile(self._cached_path):
            return self._cached_path
        key = re.sub(r'[^a-zA-Z0-9-]', '-', self.project_dir)
        files = glob.glob(os.path.join(_CLAUDE_PROJECTS_DIR, key, "*.jsonl"))
        if not files:
            return None
        start_epoch = self._start.timestamp()
        recent = [f for f in files if os.path.getmtime(f) >= start_epoch]
        if recent:
            self._cached_path = max(recent, key=os.path.getmtime)
            return self._cached_path
        # Current session's JSONL may not exist yet — return newest but
        # don't cache (so a later mtime check upgrades to the real one).
        return max(files, key=os.path.getmtime)

    def read_session_tokens(self) -> TokenStats:
        stats = TokenStats()
        path = self._find_log_file()
        if not path:
            return stats
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts_str = e.get("timestamp", "")
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(
                                ts_str.replace("Z", "+00:00"))
                            if stats.first_ts is None or ts < stats.first_ts:
                                stats.first_ts = ts
                            if stats.last_ts is None or ts > stats.last_ts:
                                stats.last_ts = ts
                        except ValueError:
                            pass
                    msg = e.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("role") == "assistant":
                        stats.responses += 1
                        if msg.get("model"):
                            stats.model = msg["model"]
                    usage = msg.get("usage", {})
                    if usage:
                        stats.input += usage.get("input_tokens", 0)
                        stats.output += usage.get("output_tokens", 0)
                        stats.cache_read += usage.get(
                            "cache_read_input_tokens", 0)
                        stats.cache_write += usage.get(
                            "cache_creation_input_tokens", 0)
        except OSError:
            pass
        return stats

    def read_plan_usage(self) -> Optional[PlanUsage]:
        data = _get_usage_cache()
        if not data:
            return None
        return PlanUsage(
            five_hour=data.get("five_hour"),
            seven_day=data.get("seven_day"),
        )

    def read_session_cost(self, stats: TokenStats) -> float:
        rates = _STATS_PRICING.get(stats.model or "", _STATS_DEFAULT_PRICE)
        m = 1_000_000
        return (
            stats.input * rates["input"] / m
            + stats.output * rates["output"] / m
            + stats.cache_read * rates["cache_read"] / m
            + stats.cache_write * rates["cache_write"] / m
        )


__all__ = [
    "ClaudeStatsReader",
    # Module-level helpers re-exported for legacy callers / tests
    "_get_claude_oauth_token",
    "_fetch_claude_usage",
    "_get_usage_cache",
    "_STATS_PRICING",
    "_STATS_DEFAULT_PRICE",
    "_CLAUDE_PROJECTS_DIR",
]
