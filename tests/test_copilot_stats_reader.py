"""Tests for CopilotStatsReader — T3.2.

Covers events.jsonl parsing, token accumulation, cost extraction
(prefer session.shutdown's modelMetrics over pricing estimate),
graceful handling of malformed lines / missing files.

The shipped fixture (tests/fixtures/copilot_events.jsonl) is a
minimal-but-realistic 8-event session with one shutdown record;
T3.3 will replace it with a longer real-world-shaped sample.
"""
from __future__ import annotations

import json
import os
import shutil
from pathlib import Path

import pytest

from bterminal.ui.stats import CopilotStatsReader, TokenStats
from bterminal.ui.stats.copilot import _COPILOT_PRICING_DEFAULT

REPO_ROOT = Path(__file__).parent.parent
FIXTURE = REPO_ROOT / "tests" / "fixtures" / "copilot_events.jsonl"
FIXTURE_PARTIAL = REPO_ROOT / "tests" / "fixtures" / "copilot_events_partial.jsonl"


def _setup_session_dir(tmp_path, events_jsonl_src):
    """Stage a fake session-state directory with one events.jsonl, then
    return a CopilotStatsReader pointed at it."""
    session_state = tmp_path / "session-state"
    session_dir = session_state / "01JTZ-MOCK-001"
    session_dir.mkdir(parents=True)
    target = session_dir / "events.jsonl"
    if isinstance(events_jsonl_src, (str, Path)):
        shutil.copy(str(events_jsonl_src), str(target))
    else:
        # write list of dicts as JSONL
        target.write_text("\n".join(json.dumps(e) for e in events_jsonl_src) + "\n")

    reader = CopilotStatsReader(project_dir="/home/me/project")
    reader._session_state_dir = str(session_state)
    return reader, target


# ─── No log file ────────────────────────────────────────────────────────────

def test_no_log_file_returns_zero_stats(tmp_path):
    """No session-state/*/events.jsonl on disk → empty TokenStats."""
    reader = CopilotStatsReader("/tmp/x")
    reader._session_state_dir = str(tmp_path / "no-such-dir")
    s = reader.read_session_tokens()
    assert isinstance(s, TokenStats)
    assert s.input == 0
    assert s.output == 0
    assert s.responses == 0


def test_cost_falls_back_to_zero_when_no_log(tmp_path):
    reader = CopilotStatsReader("/tmp/x")
    reader._session_state_dir = str(tmp_path / "no-such-dir")
    cost = reader.read_session_cost(TokenStats(input=100, output=50))
    # No log → no shutdown event → fallback to pricing estimate
    rates = _COPILOT_PRICING_DEFAULT
    expected = (100 * rates["input"] + 50 * rates["output"]) / 1_000_000
    assert cost == pytest.approx(expected)


# ─── Token accumulation from fixture ────────────────────────────────────────

def test_accumulates_tokens_from_complete_events(tmp_path):
    """T3.3 fixture has 11 tool.execution_complete events → 11 responses,
    summed input/output/cache tokens. tool.execution_failed (1) and
    prompts (4) do NOT contribute to the response count."""
    reader, _ = _setup_session_dir(tmp_path, FIXTURE)
    s = reader.read_session_tokens()
    # Sums match README.md figures (T3.3 fixture spec)
    assert s.input == 2790
    assert s.output == 1230
    assert s.cache_read == 880
    assert s.cache_write == 400
    # Only tool.execution_complete bumps responses — failed event excluded
    assert s.responses == 11


def test_extracts_model_from_session_start(tmp_path):
    reader, _ = _setup_session_dir(tmp_path, FIXTURE)
    s = reader.read_session_tokens()
    assert s.model == "claude-sonnet-4-5"


def test_records_first_last_ts(tmp_path):
    reader, _ = _setup_session_dir(tmp_path, FIXTURE)
    s = reader.read_session_tokens()
    assert s.first_ts is not None
    assert s.last_ts is not None
    assert s.first_ts <= s.last_ts


# ─── Cost: prefer session.shutdown over pricing estimate ────────────────────

def test_cost_uses_session_shutdown_model_metrics(tmp_path):
    """T3.3 fixture's shutdown event reports cost=0.0875; reader returns
    it verbatim instead of computing from token counts."""
    reader, _ = _setup_session_dir(tmp_path, FIXTURE)
    s = reader.read_session_tokens()
    cost = reader.read_session_cost(s)
    assert cost == pytest.approx(0.0875)


def test_cost_falls_back_to_pricing_estimate_when_no_shutdown(tmp_path):
    """Strip shutdown event from full fixture → reader estimates from
    token counts using Sonnet 4.5 rates."""
    events = [json.loads(line) for line in FIXTURE.read_text().splitlines() if line.strip()]
    events_no_shutdown = [e for e in events if e.get("type") != "session.shutdown"]
    reader, _ = _setup_session_dir(tmp_path, events_no_shutdown)
    s = reader.read_session_tokens()
    cost = reader.read_session_cost(s)
    # Estimate: input=2790, output=1230, cache_read=880, cache_write=400
    rates = _COPILOT_PRICING_DEFAULT
    expected = (
        2790 * rates["input"]
        + 1230 * rates["output"]
        + 880 * rates["cache_read"]
        + 400 * rates["cache_write"]
    ) / 1_000_000
    assert cost == pytest.approx(expected)


def test_partial_session_cost_uses_pricing_estimate(tmp_path):
    """T3.3 partial fixture (no session.shutdown yet) — cost is the
    pricing-based estimate over its 2 tool.execution_complete events."""
    reader, _ = _setup_session_dir(tmp_path, FIXTURE_PARTIAL)
    s = reader.read_session_tokens()
    # README.md totals: input=430, output=230, cache_read=40
    assert s.input == 430
    assert s.output == 230
    assert s.cache_read == 40
    assert s.cache_write == 0
    assert s.responses == 2

    cost = reader.read_session_cost(s)
    rates = _COPILOT_PRICING_DEFAULT
    expected = (
        430 * rates["input"]
        + 230 * rates["output"]
        + 40 * rates["cache_read"]
    ) / 1_000_000
    assert cost == pytest.approx(expected)


def test_partial_session_records_active_state(tmp_path):
    """Partial fixture reflects an active session — first/last_ts span
    the time before shutdown, model is set, no shutdown event seen."""
    reader, log_path = _setup_session_dir(tmp_path, FIXTURE_PARTIAL)
    s = reader.read_session_tokens()
    assert s.model == "claude-sonnet-4-5"
    assert s.first_ts is not None
    assert s.last_ts is not None
    # Confirm no shutdown record exists in this fixture
    raw = log_path.read_text()
    assert "session.shutdown" not in raw


def test_cost_sums_multiple_models_in_shutdown(tmp_path):
    """Real Copilot may have multiple models in modelMetrics. Sum them."""
    events = [
        {"type": "session.start", "timestamp": "2026-05-06T10:00:00Z",
         "data": {"sessionId": "x", "model": "claude-sonnet-4-5"}},
        {"type": "session.shutdown", "timestamp": "2026-05-06T10:01:00Z",
         "data": {"modelMetrics": {
             "claude-sonnet-4-5": {"requests": {"count": 5, "cost": 0.05}},
             "gpt-5": {"requests": {"count": 2, "cost": 0.10}},
         }}},
    ]
    reader, _ = _setup_session_dir(tmp_path, events)
    cost = reader.read_session_cost(TokenStats())
    assert cost == pytest.approx(0.15)


# ─── Robustness: malformed input ────────────────────────────────────────────

def test_skips_malformed_lines(tmp_path):
    """Mix valid + corrupt + empty lines — valid ones still counted."""
    raw = (
        "{not valid json at all\n"
        + json.dumps({
            "type": "tool.execution_complete",
            "timestamp": "2026-05-06T10:00:00Z",
            "data": {"usage": {"inputTokens": 42, "outputTokens": 10}},
        }) + "\n"
        + "\n"  # blank
        + "garbage line with no json\n"
    )
    session_state = tmp_path / "session-state"
    session_dir = session_state / "uuid-1"
    session_dir.mkdir(parents=True)
    (session_dir / "events.jsonl").write_text(raw)

    reader = CopilotStatsReader("/tmp/x")
    reader._session_state_dir = str(session_state)
    s = reader.read_session_tokens()
    assert s.input == 42
    assert s.output == 10
    assert s.responses == 1


def test_handles_event_without_data_field(tmp_path):
    """Defensive: event missing 'data' key shouldn't raise."""
    events = [
        {"type": "session.start", "timestamp": "2026-05-06T10:00:00Z"},
        {"type": "tool.execution_complete",
         "timestamp": "2026-05-06T10:00:01Z"},
    ]
    reader, _ = _setup_session_dir(tmp_path, events)
    s = reader.read_session_tokens()
    # responses incremented even without usage block
    assert s.responses == 1
    assert s.input == 0


def test_handles_data_field_not_a_dict(tmp_path):
    """If data isn't a dict (string, list, null), skip the event."""
    events = [
        {"type": "tool.execution_complete",
         "timestamp": "2026-05-06T10:00:00Z",
         "data": "string-not-dict"},
        {"type": "tool.execution_complete",
         "timestamp": "2026-05-06T10:00:01Z",
         "data": None},
    ]
    reader, _ = _setup_session_dir(tmp_path, events)
    s = reader.read_session_tokens()
    # No crash, no tokens accumulated
    assert s.input == 0


def test_accepts_snake_case_token_keys(tmp_path):
    """Some Copilot versions may emit snake_case keys (input_tokens).
    Reader handles both camelCase and snake_case for forward-compat."""
    events = [
        {"type": "tool.execution_complete",
         "timestamp": "2026-05-06T10:00:00Z",
         "data": {"usage": {
             "input_tokens": 100,
             "output_tokens": 50,
             "cache_read_input_tokens": 20,
             "cache_creation_input_tokens": 30,
         }}},
    ]
    reader, _ = _setup_session_dir(tmp_path, events)
    s = reader.read_session_tokens()
    assert s.input == 100
    assert s.output == 50
    assert s.cache_read == 20
    assert s.cache_write == 30


# ─── Plan usage: not supported (capability=False) ───────────────────────────

def test_plan_usage_returns_none(tmp_path):
    reader, _ = _setup_session_dir(tmp_path, FIXTURE)
    assert reader.read_plan_usage() is None


# ─── Public API surface ─────────────────────────────────────────────────────

def test_copilot_reader_exported_from_package():
    from bterminal.ui import stats
    assert hasattr(stats, "CopilotStatsReader")
    assert stats.CopilotStatsReader is CopilotStatsReader


def test_finds_newest_session_when_multiple_present(tmp_path):
    """Two events.jsonl files in different uuid dirs → newest mtime wins."""
    session_state = tmp_path / "session-state"
    older = session_state / "uuid-old"
    newer = session_state / "uuid-new"
    older.mkdir(parents=True)
    newer.mkdir(parents=True)

    older_log = older / "events.jsonl"
    newer_log = newer / "events.jsonl"
    older_log.write_text(json.dumps({
        "type": "tool.execution_complete",
        "timestamp": "2026-05-06T09:00:00Z",
        "data": {"usage": {"inputTokens": 999}},
    }) + "\n")
    newer_log.write_text(json.dumps({
        "type": "tool.execution_complete",
        "timestamp": "2026-05-06T10:00:00Z",
        "data": {"usage": {"inputTokens": 7}},
    }) + "\n")

    # Force newer mtime
    os.utime(str(newer_log), (10000.0, 10000.0))
    os.utime(str(older_log), (1000.0, 1000.0))

    reader = CopilotStatsReader("/tmp/x")
    reader._session_state_dir = str(session_state)
    s = reader.read_session_tokens()
    assert s.input == 7  # newest wins
