"""Copilot stats reader (T3.2).

Reads GitHub Copilot CLI's per-session event log
(`~/.copilot/session-state/<uuid>/events.jsonl`) — a chronological
JSONL stream of `session.start`, `tool.execution_start`,
`tool.execution_complete`, and `session.shutdown` events.

Token counts are accumulated from `tool.execution_complete` events.
Cost prefers the canonical value from `session.shutdown.modelMetrics
.<model>.requests.cost` when present; otherwise falls back to a
pricing estimate using Sonnet 4.5 rates (Copilot's default backend).

Implementation note: T3.2 is a one-shot file parser — every call to
read_session_tokens() re-scans the entire log. T4.1 will switch to a
tail-f thread that incrementally consumes new events; the public API
(read_session_tokens / read_session_cost / read_plan_usage) stays
identical so the widget needs no changes.

Plan-usage capability stays False (Copilot has no public usage API
analogous to Claude's /api/oauth/usage), so read_plan_usage() inherits
the default `None` from the base class.
"""
from __future__ import annotations

import glob
import json
import os
from datetime import datetime
from typing import Optional

from bterminal.ui.stats.base import AbstractStatsReader, TokenStats


# Copilot doesn't publish per-token pricing — Sonnet 4.5 is its default
# backend, so we approximate partial-session cost using its rates.
# Final-session cost comes from session.shutdown's modelMetrics block.
_COPILOT_PRICING_DEFAULT = {
    "input": 3.0,
    "output": 15.0,
    "cache_read": 0.30,
    "cache_write": 3.75,
}

_COPILOT_SESSION_STATE_DIR = os.path.expanduser("~/.copilot/session-state")


class CopilotStatsReader(AbstractStatsReader):
    """Reads ~/.copilot/session-state/<uuid>/events.jsonl.

    Caches the resolved log path; if the cached file disappears
    (session moved/deleted), re-resolves on next read. The `project_dir`
    constructor arg is kept on `self.project_dir` for parity with
    ClaudeStatsReader and future per-project filtering — at T3.2
    baseline we just pick the newest events.jsonl on disk.
    """

    def __init__(self, project_dir: str):
        self.project_dir = project_dir.rstrip("/") if project_dir else ""
        self._cached_path: Optional[str] = None
        # Override-able from outside for tests / multi-version support.
        self._session_state_dir = _COPILOT_SESSION_STATE_DIR

    # ─── Log file resolution ────────────────────────────────────────────────

    def _find_log_file(self) -> Optional[str]:
        if self._cached_path and os.path.isfile(self._cached_path):
            return self._cached_path
        pattern = os.path.join(self._session_state_dir, "*", "events.jsonl")
        files = glob.glob(pattern)
        if not files:
            return None
        # Newest by mtime — corresponds to the active or most recent session.
        # Per-project filtering would require correlating session UUID to
        # cwd via session-store.db (T4.5 work).
        self._cached_path = max(files, key=os.path.getmtime)
        return self._cached_path

    # ─── Token accumulation ────────────────────────────────────────────────

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
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    self._process_event(event, stats)
        except OSError:
            return stats
        return stats

    def _process_event(self, event: dict, stats: TokenStats) -> None:
        """Update `stats` in place based on a single events.jsonl event."""
        if not isinstance(event, dict):
            return

        # Timestamps appear on every event — track session bounds.
        ts_str = event.get("timestamp", "")
        if ts_str:
            try:
                ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                if stats.first_ts is None or ts < stats.first_ts:
                    stats.first_ts = ts
                if stats.last_ts is None or ts > stats.last_ts:
                    stats.last_ts = ts
            except ValueError:
                pass

        evt_type = event.get("type", "")
        data = event.get("data") or {}
        if not isinstance(data, dict):
            return

        if evt_type == "session.start":
            model = data.get("model")
            if isinstance(model, str) and model:
                stats.model = model

        elif evt_type == "tool.execution_complete":
            stats.responses += 1
            usage = data.get("usage") or {}
            if isinstance(usage, dict):
                # Copilot uses camelCase — different from Claude's
                # snake_case (input_tokens vs inputTokens). We accept
                # both because the format isn't formally locked yet.
                stats.input += int(
                    usage.get("inputTokens",
                              usage.get("input_tokens", 0)) or 0
                )
                stats.output += int(
                    usage.get("outputTokens",
                              usage.get("output_tokens", 0)) or 0
                )
                stats.cache_read += int(
                    usage.get("cacheReadTokens",
                              usage.get("cache_read_input_tokens", 0)) or 0
                )
                stats.cache_write += int(
                    usage.get("cacheWriteTokens",
                              usage.get("cacheCreationTokens",
                                        usage.get("cache_creation_input_tokens",
                                                  0))) or 0
                )

    # ─── Cost: prefer shutdown's modelMetrics, else estimate ───────────────

    def read_session_cost(self, stats: TokenStats) -> float:
        """USD cost.

        If the log contains `session.shutdown` with `modelMetrics`,
        sum its `<model>.requests.cost` (canonical Copilot reporting).
        Otherwise estimate from accumulated token counts using
        Sonnet 4.5 rates — that's the closest real number for an
        in-progress session before shutdown writes the final cost.
        """
        path = self._find_log_file()
        if path:
            final = self._extract_final_cost(path)
            if final is not None:
                return final

        rates = _COPILOT_PRICING_DEFAULT
        m = 1_000_000
        return (
            stats.input * rates["input"] / m
            + stats.output * rates["output"] / m
            + stats.cache_read * rates["cache_read"] / m
            + stats.cache_write * rates["cache_write"] / m
        )

    def _extract_final_cost(self, path: str) -> Optional[float]:
        """Scan events.jsonl for session.shutdown's modelMetrics. Sum
        cost across all models in the dict. Returns None when no
        shutdown event is present (active session)."""
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    if event.get("type") != "session.shutdown":
                        continue
                    data = event.get("data") or {}
                    metrics = data.get("modelMetrics") or {}
                    if not isinstance(metrics, dict):
                        continue
                    total = 0.0
                    for _model_name, m in metrics.items():
                        if not isinstance(m, dict):
                            continue
                        reqs = m.get("requests") or {}
                        if isinstance(reqs, dict):
                            try:
                                total += float(reqs.get("cost", 0) or 0)
                            except (TypeError, ValueError):
                                pass
                    return total
        except OSError:
            pass
        return None


__all__ = [
    "CopilotStatsReader",
    "_COPILOT_PRICING_DEFAULT",
    "_COPILOT_SESSION_STATE_DIR",
]
