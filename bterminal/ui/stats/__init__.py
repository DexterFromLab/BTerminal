"""bterminal.ui.stats — session stats reader strategy + widget (T3.1).

Re-exports the public API so existing call sites
    from bterminal.ui.stats import SessionStatsBar
keep working without changes.

Public surface:
    SessionStatsBar      — GTK widget (widget.py)
    AbstractStatsReader  — provider-agnostic reader ABC (base.py)
    TokenStats / PlanUsage — dataclasses returned by readers
    ClaudeStatsReader    — Claude implementation (claude.py)

T3.2 will add CopilotStatsReader; T3.5 wires the registry to pick the
right reader for each tab's provider.
"""
from typing import Optional

from bterminal.ui.stats.base import (
    AbstractStatsReader,
    PlanUsage,
    TokenStats,
)
from bterminal.ui.stats.aider import AiderStatsReader
from bterminal.ui.stats.claude import ClaudeStatsReader
from bterminal.ui.stats.copilot import CopilotStatsReader
from bterminal.ui.stats.widget import SessionStatsBar


# T3.5 + #94: per-provider reader factory used by terminal_tab.py to
# decide whether to mount a SessionStatsBar at all + which reader to
# wire in. Aider added at #94 (was the gap that #18 + #19 documented).
_READER_CLASSES: dict[str, type] = {
    "aider": AiderStatsReader,
    "claude": ClaudeStatsReader,
    "copilot": CopilotStatsReader,
}


def create_stats_reader_for_ai_config(
    ai_config: Optional[dict],
    registry,
) -> Optional[AbstractStatsReader]:
    """Pick the right StatsReader for an AI session config.

    Returns None when any of these holds — caller suppresses the
    SessionStatsBar entirely:
      - ai_config is empty (no AI session, e.g. SSH/local tabs).
      - project_dir is missing (reader has no log to read).
      - provider isn't registered (unknown future-version provider).
      - provider.capabilities.stats_bar is False (provider opted out).
      - provider doesn't have a registered reader class yet (T5+ providers).

    Otherwise returns a fresh reader instance bound to project_dir.
    """
    if not ai_config:
        return None
    project_dir = ai_config.get("project_dir", "")
    if not project_dir:
        return None
    provider_name = ai_config.get("provider", "claude")
    try:
        provider = registry.get(provider_name)
    except (KeyError, AttributeError):
        return None
    if not provider.capabilities.stats_bar:
        return None
    cls = _READER_CLASSES.get(provider_name)
    if cls is None:
        return None
    return cls(project_dir)


def stats_widget_options_for_ai_config(
    ai_config: Optional[dict],
    registry,
) -> dict:
    """T3.9: companion to `create_stats_reader_for_ai_config` — returns
    keyword args for SessionStatsBar (currently just `hide_plan_usage`).

    Pattern: caller does
        reader = create_stats_reader_for_ai_config(ai_config, registry)
        if reader is not None:
            opts = stats_widget_options_for_ai_config(ai_config, registry)
            bar = SessionStatsBar(project_dir, reader=reader, **opts)
    """
    if not ai_config:
        return {}
    provider_name = ai_config.get("provider", "claude")
    try:
        provider = registry.get(provider_name)
    except (KeyError, AttributeError):
        return {}
    return {
        "hide_plan_usage": bool(
            getattr(provider.capabilities, "stats_bar_no_plan_usage", False)
        ),
        # #94: providers with cost_in_log=False (Aider — dispatching
        # off-process to a local LLM) get 'n/a' instead of '$0.0000'.
        # Claude/Copilot leave this False so the existing cost
        # accumulation rendering keeps working.
        "cost_unavailable": not bool(
            getattr(provider.capabilities, "cost_in_log", True)
        ),
    }


# Legacy module-level helpers — re-exported so any external test or
# tool that imported them from the monolithic stats.py keeps working.
from bterminal.ui.stats.claude import (  # noqa: F401
    _STATS_DEFAULT_PRICE,
    _STATS_PRICING,
    _CLAUDE_PROJECTS_DIR,
    _fetch_claude_usage,
    _get_claude_oauth_token,
    _get_usage_cache,
)

__all__ = [
    "AbstractStatsReader",
    "AiderStatsReader",
    "ClaudeStatsReader",
    "CopilotStatsReader",
    "PlanUsage",
    "SessionStatsBar",
    "TokenStats",
    "create_stats_reader_for_ai_config",
    "stats_widget_options_for_ai_config",
]
