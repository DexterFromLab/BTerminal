"""Stats reader abstraction (T3.1).

Splits the monolithic _SessionStatsReader (legacy stats.py) into a
provider-agnostic AbstractStatsReader ABC + per-provider concrete
implementations (ClaudeStatsReader in claude.py; CopilotStatsReader
lands in T3.2).

Dataclasses:
    TokenStats  — accumulated token counts + timing for one session.
    PlanUsage   — 5h/7d window data from a plan-usage API (Claude only).

ABC:
    AbstractStatsReader — read_session_tokens / read_plan_usage /
                          read_session_cost interface used by
                          SessionStatsBar widget.
"""
from __future__ import annotations

from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from typing import Optional


@dataclass
class TokenStats:
    """Accumulated session tokens — provider-agnostic shape.

    The widget reads these fields directly. Tokens are integer counts;
    timestamps are timezone-aware datetimes (UTC) or None when the
    session log has no events yet.
    """

    input: int = 0
    output: int = 0
    cache_read: int = 0
    cache_write: int = 0
    responses: int = 0
    model: str = ""
    first_ts: Optional[datetime] = None
    last_ts: Optional[datetime] = None


@dataclass
class PlanUsage:
    """Plan-usage windows from a provider's billing API.

    `five_hour` / `seven_day` are dicts with at least `utilization`
    (0.0-100.0) and `resets_at` (epoch float or ISO-8601 string), or
    None if the API didn't return that window.
    """

    five_hour: Optional[dict] = None
    seven_day: Optional[dict] = None


class AbstractStatsReader(ABC):
    """Reader interface used by SessionStatsBar.

    Concrete implementations live next to the provider whose log
    format / billing API they understand.
    """

    project_dir: str

    @abstractmethod
    def read_session_tokens(self) -> TokenStats:
        """Parse the session log file (Claude JSONL, Copilot
        events.jsonl, ...) and return accumulated token counts."""

    def read_plan_usage(self) -> Optional[PlanUsage]:
        """Return plan-usage windows or None.

        Default: None — providers without a plan-usage API leave the
        gauge blank. Override in providers like Claude that expose it.
        """
        return None

    def read_session_cost(self, stats: TokenStats) -> float:
        """Compute USD cost from token counts.

        Default: 0.0 — providers without per-token pricing leave the
        cost label at $0. Override in providers that ship a pricing
        table (Claude does so via defaults.json[providers.claude.pricing]).
        """
        return 0.0


__all__ = [
    "AbstractStatsReader",
    "PlanUsage",
    "TokenStats",
]
