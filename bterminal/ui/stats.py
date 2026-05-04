"""SessionStatsBar — thin status bar for Claude Code session metrics.

Reads Claude's local JSONL session file (live token/cost) and the
~/.claude/.credentials.json OAuth token to fetch plan-usage windows
(5h / 7d) from the Anthropic API. Updates every 5s via GLib.timeout.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/stats.py` in a later migration etap.
"""

import glob
import json
import os
import re
import threading
import time as _time_mod
import urllib.request
from datetime import datetime, timezone

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk


# ─── Claude plan usage cache (shared across all tabs) ────────────────────────

_CLAUDE_CREDENTIALS_FILE = os.path.expanduser("~/.claude/.credentials.json")
_CLAUDE_USAGE_API = "https://api.anthropic.com/api/oauth/usage"
_CLAUDE_OAUTH_BETA = "oauth-2025-04-20"

_usage_cache = {"data": None, "fetched_at": 0.0, "fetching": False}
_USAGE_TTL = 60.0  # seconds


def _get_claude_oauth_token():
    """Read OAuth access token from Claude credentials file."""
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


def _fetch_claude_usage():
    """Fetch usage data from Claude API. Returns dict or None on failure."""
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


def _refresh_usage_cache():
    """Fetch usage in background thread and update cache."""
    if _usage_cache["fetching"]:
        return
    _usage_cache["fetching"] = True

    def _bg():
        data = _fetch_claude_usage()
        _usage_cache["fetching"] = False
        if data is not None:
            _usage_cache["data"] = data
            _usage_cache["fetched_at"] = _time_mod.time()

    threading.Thread(target=_bg, daemon=True).start()


def _get_usage_cache():
    """Return cached usage data, triggering background refresh if stale."""
    age = _time_mod.time() - _usage_cache["fetched_at"]
    if age > _USAGE_TTL:
        _refresh_usage_cache()
    return _usage_cache["data"]


def _fmt_reset_time(resets_at):
    """Format reset time as human-readable relative string.

    *resets_at* can be a Unix epoch (int/float) or an ISO-8601 string.
    """
    if isinstance(resets_at, str):
        try:
            dt = datetime.fromisoformat(resets_at)
            epoch = dt.timestamp()
        except (ValueError, TypeError):
            return "?"
    else:
        epoch = float(resets_at)
    diff = epoch - _time_mod.time()
    if diff <= 0:
        return "now"
    if diff < 3600:
        return f"{int(diff / 60)}min"
    hours = diff / 3600
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


# ─── SessionStatsBar (Claude Code session metrics) ───────────────────────────

_STATS_PRICING = {
    "claude-opus-4-6":   {"input": 15.0,  "output": 75.0,  "cache_read": 1.50,  "cache_write": 18.75},
    "claude-sonnet-4-6": {"input":  3.0,  "output": 15.0,  "cache_read": 0.30,  "cache_write":  3.75},
    "claude-haiku-4-5":  {"input":  0.80, "output":  4.0,  "cache_read": 0.08,  "cache_write":  1.00},
    "claude-opus-4-5":   {"input": 15.0,  "output": 75.0,  "cache_read": 1.50,  "cache_write": 18.75},
    "claude-sonnet-4-5": {"input":  3.0,  "output": 15.0,  "cache_read": 0.30,  "cache_write":  3.75},
}
_STATS_DEFAULT_PRICE = {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75}
_CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")


def _fmt_tok(n):
    if n >= 1_000_000: return f"{n / 1_000_000:.1f}M"
    if n >= 1_000: return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_dur(seconds):
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {sec:02d}s"


class _SessionStatsReader:
    """Reads Claude Code JSONL session file for live token/cost stats."""

    def __init__(self, project_dir):
        self._project_dir = project_dir.rstrip("/")
        self._start = datetime.now(timezone.utc)
        self._cached = None

    def _find_file(self):
        if self._cached and os.path.isfile(self._cached):
            return self._cached
        key = re.sub(r'[^a-zA-Z0-9-]', '-', self._project_dir)
        files = glob.glob(os.path.join(_CLAUDE_PROJECTS_DIR, key, "*.jsonl"))
        if not files:
            return None
        start_epoch = self._start.timestamp()
        recent = [f for f in files if os.path.getmtime(f) >= start_epoch]
        if recent:
            self._cached = max(recent, key=os.path.getmtime)
            return self._cached
        # Current session's JSONL may not exist yet — return newest but don't cache
        return max(files, key=os.path.getmtime)

    def read(self):
        result = {"model": "", "input": 0, "output": 0, "cache_read": 0,
                  "cache_write": 0, "responses": 0, "first_ts": None, "last_ts": None}
        path = self._find_file()
        if not path:
            return result
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
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if result["first_ts"] is None or ts < result["first_ts"]:
                                result["first_ts"] = ts
                            if result["last_ts"] is None or ts > result["last_ts"]:
                                result["last_ts"] = ts
                        except ValueError:
                            pass
                    msg = e.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("role") == "assistant":
                        result["responses"] += 1
                        if msg.get("model"):
                            result["model"] = msg["model"]
                    usage = msg.get("usage", {})
                    if usage:
                        result["input"] += usage.get("input_tokens", 0)
                        result["output"] += usage.get("output_tokens", 0)
                        result["cache_read"] += usage.get("cache_read_input_tokens", 0)
                        result["cache_write"] += usage.get("cache_creation_input_tokens", 0)
        except OSError:
            pass
        return result


class SessionStatsBar(Gtk.Box):
    """Thin status bar showing Claude Code session metrics."""

    def __init__(self, project_dir):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._reader = _SessionStatsReader(project_dir)
        self._prompt_count = 0
        self._timer = 0

        self.set_size_request(-1, 44)

        style = self.get_style_context()
        style.add_class("stats-bar")

        self._labels = {}
        fields = [
            ("dur",      "⏱ --:--",     "Session duration"),
            ("s1",       " │ ",          None),
            ("prompts",  "💬 0",         "Prompts sent"),
            ("s2",       " │ ",          None),
            ("resp",     "🤖 0",         "Responses received"),
            ("s3",       " │ ",          None),
            ("tok_in",   "↑ 0",          "Input tokens (incl. cache writes)"),
            ("s3b",      " ",            None),
            ("tok_out",  "↓ 0",          "Output tokens"),
            ("s4",       " │ ",          None),
            ("cache",    "📦 0%",        "Cache hit rate"),
            ("s5",       " │ ",          None),
            ("cost",     "💰 $0.00",     "Estimated cost"),
            ("s6",       " │ ",          None),
            ("tok_h",    "⚡ 0 tok/h",   "Tokens per hour (throughput)"),
            ("s7",       " │ ",          None),
            ("model",    "",             "Model used"),
            ("s8",       " │ ",          None),
            ("usage_5h", "🔋 5h –",      "Plan usage: current session (5h window)"),
            ("s9",       " ",            None),
            ("usage_7d", "7d –",         "Plan usage: weekly (7d window)"),
        ]
        for key, text, tooltip in fields:
            lbl = Gtk.Label(label=text)
            lbl.set_margin_start(4)
            lbl.set_margin_end(2)
            lbl.set_margin_top(4)
            lbl.set_margin_bottom(4)
            if tooltip:
                lbl.set_tooltip_text(tooltip)
                lbl.set_has_tooltip(True)
            self._labels[key] = lbl
            self.pack_start(lbl, False, False, 0)

        self.show_all()
        self._timer = GLib.timeout_add(5000, self._update)

    def increment_prompt(self):
        self._prompt_count += 1

    def stop(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _update(self):
        s = self._reader.read()
        M = 1_000_000
        price = _STATS_PRICING.get(s["model"], _STATS_DEFAULT_PRICE)
        cost = (s["input"] * price["input"] / M + s["output"] * price["output"] / M
                + s["cache_read"] * price["cache_read"] / M + s["cache_write"] * price["cache_write"] / M)
        dur = 0.0
        if s["first_ts"]:
            end = s["last_ts"] or datetime.now(timezone.utc)
            dur = (end - s["first_ts"]).total_seconds()
        total_tok = s["input"] + s["cache_write"] + s["cache_read"] + s["output"]
        tok_h = total_tok / (dur / 3600) if dur > 1 else 0
        total_in = s["input"] + s["cache_write"]
        cache_pct = int(s["cache_read"] / (total_in + s["cache_read"]) * 100) if (total_in + s["cache_read"]) else 0

        self._labels["dur"].set_text(f"⏱ {_fmt_dur(dur)}")
        self._labels["prompts"].set_text(f"💬 {self._prompt_count}")
        self._labels["resp"].set_text(f"🤖 {s['responses']}")
        self._labels["tok_in"].set_text(f"↑ {_fmt_tok(total_in)}")
        self._labels["tok_out"].set_text(f"↓ {_fmt_tok(s['output'])}")
        self._labels["cache"].set_text(f"📦 {cache_pct}%")
        self._labels["cost"].set_text(f"💰 ${cost:.4f}")
        self._labels["tok_h"].set_text(f"⚡ {_fmt_tok(int(tok_h))} tok/h")
        if s["model"]:
            self._labels["model"].set_text(s["model"].replace("claude-", "").replace("-2024", ""))

        usage = _get_usage_cache()
        for key, lbl_key in [("five_hour", "usage_5h"), ("seven_day", "usage_7d")]:
            prefix = "5h" if key == "five_hour" else "7d"
            info = usage.get(key) if usage else None
            icon = "🔋 " if key == "five_hour" else ""
            if not info:
                self._labels[lbl_key].set_text(f"{icon}{prefix} –")
                self._labels[lbl_key].set_tooltip_text(
                    "Plan usage: current session (5h window)" if key == "five_hour"
                    else "Plan usage: weekly (7d window)"
                )
            else:
                util = info.get("utilization", 0)
                # API returns percentage directly (e.g. 36.0 = 36%)
                pct = int(util) if util is not None else 0
                resets_at = info.get("resets_at")
                tip = f"{prefix}: {pct}% used"
                if resets_at:
                    tip += f" · resets in {_fmt_reset_time(resets_at)}"
                self._labels[lbl_key].set_text(f"{icon}{prefix} {pct}%")
                self._labels[lbl_key].set_tooltip_text(tip)

        return True
