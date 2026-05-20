"""bterminal.providers.claude — Claude Code provider implementation.

Thin wrapper around bterminal/providers/defaults.json[providers.claude].
All capabilities, pricing, argv flags, and binary search paths are
sourced from that config dict — the class only encapsulates BEHAVIOR
(binary lookup, argv construction, JSONL parsing, OAuth usage fetch).

Per docs/cli-provider-abstraction-implementation-plan.md task T1.3.
The class is a 1:1 port of:
    bterminal/helpers.py::_find_claude_path
    bterminal/ui/terminal_tab.py::spawn_claude (argv portion only)
    bterminal/ui/stats.py::_SessionStatsReader / _fetch_claude_usage
"""
from __future__ import annotations

import glob
import json
import os
import re
import shutil
import time
import urllib.request
from typing import Any, Optional

from bterminal.providers.base import (
    AIProvider,
    ProviderCapabilities,
    ProviderDisplay,
    SessionStats,
)


_DEFAULT_PRICE = {"input": 3.0, "output": 15.0,
                  "cache_read": 0.30, "cache_write": 3.75}


class ClaudeProvider(AIProvider):
    """Claude Code CLI provider.

    Constructed from the per-provider config slice (i.e.
    `load_providers_config()["providers"]["claude"]`).
    """

    name = "claude"

    def __init__(self, config: dict):
        self._config = config
        self.display = ProviderDisplay(**config["display"])
        self.capabilities = ProviderCapabilities(**config["capabilities"])
        self.pricing = config.get("pricing", {})
        self._argv_spec = config.get("argv", {})
        self._binary_spec = config.get("binary", {})

    # ─── Binary lookup ──────────────────────────────────────────────────────

    def find_binary(self) -> Optional[str]:
        """Locate Claude binary across configured paths + PATH fallback.

        Mirrors bterminal/helpers.py::_find_claude_path: expands ~,
        resolves * globs (e.g. ~/.nvm/versions/node/*/bin/claude),
        falls back to shutil.which with an augmented PATH so GUI
        launches that miss ~/.npm-global/bin still resolve.
        """
        for entry in self._binary_spec.get("search_paths", []):
            expanded = os.path.expanduser(entry)
            if "*" in expanded:
                # Newest match first — useful for ~/.nvm/versions/node/*/bin/...
                for p in sorted(glob.glob(expanded), reverse=True):
                    if os.path.isfile(p) and os.access(p, os.X_OK):
                        return p
            elif os.path.isfile(expanded) and os.access(expanded, os.X_OK):
                return expanded
        # PATH fallback with augmented search dirs (bash-only ~/.bashrc
        # extras don't reach GUI launches).
        extra = os.pathsep.join([
            os.path.expanduser("~/.npm-global/bin"),
            os.path.expanduser("~/.local/bin"),
        ])
        env_path = os.environ.get("PATH", "") + os.pathsep + extra
        return shutil.which(self.name, path=env_path)

    # ─── argv construction ──────────────────────────────────────────────────

    def build_argv(self, config: dict, intro_prompt: str) -> list[str]:
        """Build argv list for spawn (first element = binary path).

        config: session config dict. Reads `provider_options` (R4.2
                schema) first; falls back to top-level keys for legacy
                claude_sessions.json compatibility (resume,
                skip_permissions, model).
        intro_prompt: pre-rendered intro text (may be empty).
        """
        binary = self.find_binary()
        if not binary:
            return []  # caller handles "Claude not found" UX

        argv: list[str] = [binary]
        opts = config.get("provider_options") or config

        if opts.get("resume"):
            argv.extend(self._argv_spec.get("resume", ["--resume"]))
        elif opts.get("continue"):
            argv.extend(self._argv_spec.get("continue", ["--continue"]))

        if opts.get("skip_permissions"):
            argv.extend(self._argv_spec.get(
                "yolo", ["--dangerously-skip-permissions"]))

        model = opts.get("model")
        if model:
            template = self._argv_spec.get("model", ["--model", "{model}"])
            argv.extend(s.format(model=model) for s in template)

        # Intro prompt as positional arg (Claude convention)
        if intro_prompt and self.capabilities.intro_prompt:
            argv.append(intro_prompt)

        return argv

    # ─── Session log ────────────────────────────────────────────────────────

    @staticmethod
    def _sanitize_cwd(project_dir: str) -> str:
        """Sanitize project_dir for use in the JSONL path component.

        Mirrors the legacy logic in bterminal/ui/stats.py::_find_file
        (`re.sub(r'[^a-zA-Z0-9-]', '-', ...)`). Trailing slash stripped
        first so /foo/ and /foo produce the same key.
        """
        return re.sub(r'[^a-zA-Z0-9-]', '-', project_dir.rstrip("/"))

    def session_log_glob(self, project_dir: str) -> Optional[str]:
        """Return glob matching Claude's per-project JSONL session files."""
        if not self.capabilities.session_log:
            return None
        template = self.capabilities.session_log_path
        if not template:
            return None
        # Template uses {sanitized_cwd} and {session_id}; for glob we
        # don't know session_id yet → wildcard it.
        sanitized = self._sanitize_cwd(project_dir)
        path = template.format(sanitized_cwd=sanitized, session_id="*")
        return os.path.expanduser(path)

    def parse_session_stats(self, log_path: str) -> SessionStats:
        """Parse a JSONL session file and accumulate stats + cost.

        1:1 port of bterminal/ui/stats.py::_SessionStatsReader.read,
        but returns a SessionStats dataclass and computes cost from
        pricing config rather than the global _STATS_PRICING dict.
        """
        stats = SessionStats()
        try:
            with open(log_path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    msg = e.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("role") == "assistant":
                        stats.response_count += 1
                        if msg.get("model"):
                            stats.model = msg["model"]
                    usage = msg.get("usage", {})
                    if usage:
                        stats.input_tokens += usage.get("input_tokens", 0)
                        stats.output_tokens += usage.get("output_tokens", 0)
                        stats.cache_read_tokens += usage.get(
                            "cache_read_input_tokens", 0)
                        stats.cache_creation_tokens += usage.get(
                            "cache_creation_input_tokens", 0)
        except OSError:
            return stats
        stats.cost_usd = self.calculate_cost(stats)
        return stats

    def calculate_cost(self, stats: SessionStats) -> float:
        """USD cost from pricing[stats.model] (fallback Sonnet rates)."""
        rates = self.pricing.get(stats.model or "", _DEFAULT_PRICE)
        return (
            stats.input_tokens * rates.get("input", 0)
            + stats.output_tokens * rates.get("output", 0)
            + stats.cache_read_tokens * rates.get("cache_read", 0)
            + stats.cache_creation_tokens * rates.get("cache_write", 0)
        ) / 1_000_000

    # ─── Plan usage (5h / 7d windows from Anthropic OAuth) ──────────────────

    def fetch_plan_usage(self) -> Optional[dict]:
        """Fetch plan usage data from Anthropic OAuth endpoint.

        1:1 port of bterminal/ui/stats.py::_fetch_claude_usage.
        Returns the API response dict (5h + 7d windows + plan tier),
        or None if creds missing / token expired / network error.
        """
        if not self.capabilities.usage_api:
            return None
        creds_file = self.capabilities.oauth_creds_file
        usage_url = self.capabilities.usage_api_url
        if not creds_file or not usage_url:
            return None

        token = self._read_oauth_token(os.path.expanduser(creds_file))
        if not token:
            return None

        req = urllib.request.Request(
            usage_url,
            headers={
                "Content-Type": "application/json",
                "User-Agent": "claude-code/2.1.90",
                "Authorization": f"Bearer {token}",
                "anthropic-beta": "oauth-2025-04-20",
            },
        )
        try:
            with urllib.request.urlopen(req, timeout=10) as resp:
                data = json.loads(resp.read().decode())
                return data if "error" not in data else None
        except Exception:
            return None

    @staticmethod
    def _read_oauth_token(creds_path: str) -> Optional[str]:
        """Return Claude OAuth access token if present and unexpired."""
        try:
            with open(creds_path, encoding="utf-8") as fh:
                creds = json.load(fh)
            oauth = creds.get("claudeAiOauth", {})
            token = oauth.get("accessToken")
            expires_at = oauth.get("expiresAt", 0)
            if token and expires_at > time.time() * 1000:
                return token
        except (OSError, json.JSONDecodeError, KeyError):
            pass
        return None

    # ─── Dialog schema (T2.6) ──────────────────────────────────────────────

    def get_dialog_schema(self) -> list[tuple]:
        """Provider-specific fields rendered in AISessionDialog.

        Each tuple: (config_key, widget_type, label[, default|options]).
        Widget types: "checkbox" — 4th element is bool default (False if
        omitted); "combo" — 4th element is the options list; "text" — 4th
        element is placeholder string.
        """
        return [
            ("resume", "checkbox", "Resume last session (--resume)"),
            ("skip_permissions", "checkbox",
             "Skip permission prompts (--dangerously-skip-permissions)"),
            ("sudo", "checkbox", "Use sudo askpass", True),
        ]

    # ─── Idle detection (default OK for Claude — overridden later if T4 needs it) ─

    def detect_idle(self, terminal: Any, session_id: Optional[str],
                    timeout_s: float = 10.0) -> bool:
        """Claude default: trust caller's debounce timer.

        Future enhancement (post-MVP): tail-f the session JSONL for
        `system.stop` events and return True only when seen — mirrors
        the deterministic ready marker available via stream-json.
        """
        return True


__all__ = ["ClaudeProvider"]
