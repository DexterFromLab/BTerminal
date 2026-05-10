"""Aider stats reader (#22 / #94).

Reads `.aider.chat.history.md` — Aider's per-project rolling chat
history written as Markdown — and surfaces token counts to the
SessionStatsBar widget. Cost stays at $0 because Aider dispatches
off-process to a local LLM (Ollama, llama.cpp, etc.) and doesn't
record per-call USD into the chat log.

The actual parsing logic lives in `AiderProvider.parse_session_stats`
(bterminal/providers/aider.py) — it knows the markdown grammar
(`#### ` user turns, `Tokens: N sent, M received` markers, model
attribution from `--model` mentions). This reader is a thin adapter
that converts the provider's `SessionStats` shape into the widget's
`TokenStats` shape so the existing widget code works unchanged.

Token format examples Aider writes (parsed by AiderProvider):
  > Tokens: 1.5k sent, 234 received.
  Tokens: 12,345 sent, 5678 received.

Read pattern is one-shot scan per call — same as CopilotStatsReader at
T3.2 baseline. A future tail-f optimization would mirror what T4.1
plans for Copilot, but the public interface stays identical so the
widget never needs to change.
"""
from __future__ import annotations

from typing import Optional

from bterminal.ui.stats.base import AbstractStatsReader, TokenStats


class AiderStatsReader(AbstractStatsReader):
    """Reads <project_dir>/.aider.chat.history.md via the registered
    AiderProvider's parse_session_stats helper.

    The provider arg in the constructor is optional — when None, the
    reader looks up "aider" in the global registry. Tests can pass an
    explicit provider to stay isolated from the singleton.
    """

    def __init__(self, project_dir: str, provider=None):
        self.project_dir = project_dir.rstrip("/") if project_dir else ""
        self._provider = provider  # may be None — resolved lazily

    def _get_provider(self):
        """Return the AiderProvider instance (constructor arg or
        registry lookup). Cached after first resolution so the
        registry import doesn't pay per-call cost."""
        if self._provider is not None:
            return self._provider
        from bterminal.providers import get_registry
        try:
            self._provider = get_registry().get("aider")
        except (KeyError, AttributeError):
            self._provider = None
        return self._provider

    def _log_path(self) -> Optional[str]:
        """Resolve the .aider.chat.history.md path via the provider's
        session_log_glob template — single source of truth for the
        path format. Returns None when project_dir is empty."""
        if not self.project_dir:
            return None
        prov = self._get_provider()
        if prov is None:
            return None
        return prov.session_log_glob(self.project_dir)

    # ─── Token accumulation ────────────────────────────────────────────────

    def read_session_tokens(self) -> TokenStats:
        """Parse .aider.chat.history.md and return token counts.

        Returns an empty TokenStats when:
          - project_dir is empty (SSH/local tabs)
          - the chat history file doesn't exist yet (fresh session
            before any user turn)
          - the provider's parser found no Tokens: markers (older
            aider versions or model that doesn't report them)

        The widget renders an empty TokenStats as 'in: 0 · out: 0',
        which is the expected pre-first-prompt state.
        """
        out = TokenStats()
        path = self._log_path()
        if not path:
            return out
        prov = self._get_provider()
        if prov is None:
            return out

        ps = prov.parse_session_stats(path)
        # AIDER reports input/output via parse_session_stats; cache
        # tokens stay at 0 because Aider's chat history doesn't
        # distinguish them. Model attribution falls through.
        out.input = ps.input_tokens
        out.output = ps.output_tokens
        out.responses = ps.response_count
        out.model = ps.model or ""
        # first_ts / last_ts: aider's markdown log doesn't include
        # explicit timestamps. Leaving both as None makes the widget
        # hide the duration label, same fallback as Copilot when
        # session.start hasn't fired yet.
        return out

    # read_session_cost inherits the AbstractStatsReader default of
    # 0.0 — Aider's capabilities.cost_in_log is False, so we never
    # try to extract a USD figure (the chat log doesn't contain one).
    # Widget code is responsible for rendering 0.0 as 'n/a' when the
    # provider has cost_in_log=False (see widget.py).

    # read_plan_usage inherits None — Aider has no remote billing API
    # (capabilities.usage_api is False, local LLM).


__all__ = [
    "AiderStatsReader",
]
