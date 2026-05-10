"""Tests for stats reader strategy split — T3.1.

Verifies that the package layout (bterminal/ui/stats/) preserves the
public API the rest of the codebase depends on, and that the new
AbstractStatsReader contract is sound + ClaudeStatsReader is a 1:1
behavioral port of the pre-T3 _SessionStatsReader.
"""
from __future__ import annotations

import dataclasses
import json
from datetime import datetime, timezone

import pytest

from bterminal.ui.stats import (
    AbstractStatsReader,
    ClaudeStatsReader,
    PlanUsage,
    SessionStatsBar,
    TokenStats,
)
from bterminal.ui.stats.base import AbstractStatsReader as _BaseABC


# ─── Dataclass shapes ───────────────────────────────────────────────────────

def test_token_stats_defaults_zero():
    s = TokenStats()
    assert s.input == 0
    assert s.output == 0
    assert s.cache_read == 0
    assert s.cache_write == 0
    assert s.responses == 0
    assert s.model == ""
    assert s.first_ts is None
    assert s.last_ts is None


def test_token_stats_is_mutable():
    s = TokenStats()
    s.input = 100
    s.model = "claude-sonnet-4-6"
    assert s.input == 100
    assert s.model == "claude-sonnet-4-6"


def test_plan_usage_defaults_none():
    p = PlanUsage()
    assert p.five_hour is None
    assert p.seven_day is None


# ─── ABC contract ───────────────────────────────────────────────────────────

def test_abstract_reader_cannot_be_instantiated():
    with pytest.raises(TypeError):
        AbstractStatsReader()  # type: ignore[abstract]


def test_concrete_reader_must_implement_read_session_tokens():
    """Subclass without read_session_tokens → TypeError on construction."""
    class _BadReader(AbstractStatsReader):
        pass

    with pytest.raises(TypeError):
        _BadReader()  # type: ignore[abstract]


def test_concrete_reader_minimal_works():
    """Minimal subclass providing the abstract method."""
    class _MinReader(AbstractStatsReader):
        project_dir = "/tmp"

        def read_session_tokens(self) -> TokenStats:
            return TokenStats(input=42)

    r = _MinReader()
    assert r.read_session_tokens().input == 42
    # Defaults flow through
    assert r.read_plan_usage() is None
    assert r.read_session_cost(TokenStats()) == 0.0


# ─── ClaudeStatsReader behavior ─────────────────────────────────────────────

@pytest.fixture
def claude_reader_with_log(tmp_path, monkeypatch):
    """Build a ClaudeStatsReader pointing at a tmp project_dir whose
    JSONL log has been seeded with two assistant events."""
    project_dir = "/tmp/myproj"
    sanitized = "-tmp-myproj"
    log_dir = tmp_path / sanitized
    log_dir.mkdir()
    log_file = log_dir / "abc.jsonl"
    log_file.write_text("\n".join(json.dumps(e) for e in [
        {"timestamp": "2026-05-06T10:00:00Z",
         "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                     "usage": {"input_tokens": 100, "output_tokens": 50,
                               "cache_read_input_tokens": 20,
                               "cache_creation_input_tokens": 30}}},
        {"timestamp": "2026-05-06T10:01:00Z",
         "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                     "usage": {"input_tokens": 200, "output_tokens": 80}}},
    ]) + "\n")
    monkeypatch.setattr("bterminal.ui.stats.claude._CLAUDE_PROJECTS_DIR",
                        str(tmp_path))
    return ClaudeStatsReader(project_dir)


def test_claude_reader_accumulates_tokens(claude_reader_with_log):
    s = claude_reader_with_log.read_session_tokens()
    assert s.input == 300
    assert s.output == 130
    assert s.cache_read == 20
    assert s.cache_write == 30
    assert s.responses == 2
    assert s.model == "claude-sonnet-4-6"


def test_claude_reader_records_first_last_ts(claude_reader_with_log):
    s = claude_reader_with_log.read_session_tokens()
    assert s.first_ts is not None
    assert s.last_ts is not None
    assert s.first_ts <= s.last_ts


def test_claude_reader_cost_uses_pricing_table(claude_reader_with_log):
    s = claude_reader_with_log.read_session_tokens()
    cost = claude_reader_with_log.read_session_cost(s)
    # Sonnet 4.6: input 3.0/M, output 15.0/M, cache_read 0.30/M, cache_write 3.75/M
    expected = (300 * 3.0 + 130 * 15.0 + 20 * 0.30 + 30 * 3.75) / 1_000_000
    assert cost == pytest.approx(expected)


def test_claude_reader_cost_unknown_model_uses_default(tmp_path, monkeypatch):
    monkeypatch.setattr("bterminal.ui.stats.claude._CLAUDE_PROJECTS_DIR",
                        str(tmp_path))
    r = ClaudeStatsReader("/tmp/x")
    s = TokenStats(input=1_000_000, model="claude-future-99")
    # Default rate: input=3.0 → 1M tokens × 3.0 / 1M = 3.0
    assert r.read_session_cost(s) == pytest.approx(3.0)


def test_claude_reader_no_log_file_returns_zero_stats(tmp_path, monkeypatch):
    monkeypatch.setattr("bterminal.ui.stats.claude._CLAUDE_PROJECTS_DIR",
                        str(tmp_path))
    r = ClaudeStatsReader("/tmp/no-such-project")
    s = r.read_session_tokens()
    assert s.input == 0
    assert s.responses == 0


def test_claude_reader_skips_malformed_lines(tmp_path, monkeypatch):
    project_dir = "/tmp/myproj"
    log_dir = tmp_path / "-tmp-myproj"
    log_dir.mkdir()
    (log_dir / "abc.jsonl").write_text(
        "{not json\n"
        + json.dumps({"message": {"role": "assistant",
                                  "usage": {"input_tokens": 7}}}) + "\n"
        + "\n"  # blank line
    )
    monkeypatch.setattr("bterminal.ui.stats.claude._CLAUDE_PROJECTS_DIR",
                        str(tmp_path))
    r = ClaudeStatsReader(project_dir)
    s = r.read_session_tokens()
    assert s.input == 7


# ─── SessionStatsBar reader injection ───────────────────────────────────────

def test_widget_uses_claude_reader_by_default(monkeypatch):
    """Without an explicit reader, SessionStatsBar instantiates a
    ClaudeStatsReader (legacy semantic). Widget construction is GTK-
    bound, so we patch __init__ to assert the reader type without
    actually building a Gtk.Box."""
    captured = {}

    real_init = SessionStatsBar.__init__

    def _spy(self, project_dir, reader=None):
        captured["reader"] = reader or ClaudeStatsReader(project_dir)
        # Don't call real_init — that needs a GTK display.

    monkeypatch.setattr(SessionStatsBar, "__init__", _spy)
    SessionStatsBar("/tmp/x")
    assert isinstance(captured["reader"], ClaudeStatsReader)


def test_widget_accepts_injected_reader(monkeypatch):
    """T3.5 dispatch will pass a CopilotStatsReader (T3.2) — the
    constructor honors the injected reader."""
    class _CustomReader(AbstractStatsReader):
        project_dir = "/tmp"

        def read_session_tokens(self) -> TokenStats:
            return TokenStats(input=999)

    captured = {}

    def _spy(self, project_dir, reader=None):
        captured["reader"] = reader

    monkeypatch.setattr(SessionStatsBar, "__init__", _spy)
    custom = _CustomReader()
    SessionStatsBar("/tmp/x", reader=custom)
    assert captured["reader"] is custom


# ─── Public API surface ─────────────────────────────────────────────────────

def test_public_api_re_exports_intact():
    """from bterminal.ui.stats import X — all 5 symbols present."""
    from bterminal.ui import stats as stats_pkg
    for name in ("AbstractStatsReader", "ClaudeStatsReader",
                 "PlanUsage", "SessionStatsBar", "TokenStats"):
        assert hasattr(stats_pkg, name), f"missing public symbol: {name}"


def test_legacy_module_constants_still_importable():
    """Tests / external tools that imported _STATS_PRICING etc. from
    the monolithic stats.py keep working via the package re-export."""
    from bterminal.ui.stats import _STATS_PRICING, _CLAUDE_PROJECTS_DIR
    assert "claude-sonnet-4-6" in _STATS_PRICING
    assert _CLAUDE_PROJECTS_DIR.endswith(".claude/projects")
