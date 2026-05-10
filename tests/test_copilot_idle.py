"""Tests for Copilot idle detection — T4.1.

Pure helper `evaluate_idle_state(lines, current_time, timeout_s)`
plus the `_CopilotIdleMonitor` thread wrapper. Tests don't rely on
GTK or a real Copilot binary — they drive the state machine over
synthetic events.jsonl content and verify dispatch + lifecycle.
"""
from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime, timezone
from pathlib import Path

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.providers.copilot import (
    CopilotProvider,
    _CopilotIdleMonitor,
    evaluate_idle_state,
    _IDLE_ACTIVE_TYPES,
    _IDLE_PERMANENT_TYPES,
    _IDLE_TERMINAL_TYPES,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry()
    yield
    reset_registry()


def _ev(ev_type, ts="2026-05-06T10:00:00Z", **data):
    """Helper: build one events.jsonl line as a dict for testing."""
    return {"type": ev_type, "timestamp": ts, "data": data}


def _serialize(events):
    return [json.dumps(e) for e in events]


# ─── evaluate_idle_state — pure helper ──────────────────────────────────────


def test_no_events_returns_not_idle():
    """Empty log → not idle ('no_events' reason). The monitor shouldn't
    fire its callback for fresh sessions that haven't logged anything."""
    state = evaluate_idle_state([], current_time=time.time())
    assert state["idle"] is False
    assert state["reason"] == "no_events"
    assert state["permanent"] is False


def test_idle_after_complete_silence():
    """Last event is tool.execution_complete + elapsed > timeout → idle."""
    # Event 11s ago, timeout 10s → idle.
    ts = "2026-05-06T10:00:00Z"
    lines = _serialize([
        _ev("tool.execution_start", ts="2026-05-06T09:59:55Z"),
        _ev("tool.execution_complete", ts=ts),
    ])
    # current_time = 11 seconds after event ts
    event_epoch = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    state = evaluate_idle_state(lines, current_time=event_epoch + 11,
                                 timeout_s=10.0)
    assert state["idle"] is True
    assert state["reason"] == "quiet_after_complete"
    assert state["permanent"] is False
    assert state["last_event_type"] == "tool.execution_complete"


def test_not_idle_during_active_tool():
    """Last event is tool.execution_start (no matching complete yet)."""
    lines = _serialize([
        _ev("tool.execution_start", ts="2026-05-06T10:00:00Z"),
    ])
    state = evaluate_idle_state(lines, current_time=time.time(),
                                 timeout_s=10.0)
    assert state["idle"] is False
    assert state["reason"] == "active"


def test_warming_up_within_timeout():
    """Complete event arrived recently — should NOT trigger idle yet."""
    ts = "2026-05-06T10:00:00Z"
    lines = _serialize([
        _ev("tool.execution_complete", ts=ts),
    ])
    event_epoch = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()
    state = evaluate_idle_state(lines, current_time=event_epoch + 3,
                                 timeout_s=10.0)
    assert state["idle"] is False
    assert state["reason"] == "warming_up"


def test_session_shutdown_terminates():
    """Shutdown event → idle=True permanent (caller stops monitor)."""
    lines = _serialize([
        _ev("tool.execution_complete", ts="2026-05-06T10:00:00Z"),
        _ev("session.shutdown", ts="2026-05-06T10:01:00Z"),
    ])
    state = evaluate_idle_state(lines, current_time=time.time(),
                                 timeout_s=10.0)
    assert state["idle"] is True
    assert state["reason"] == "shutdown"
    assert state["permanent"] is True


def test_recovers_from_truncated_jsonl():
    """Malformed lines mixed with valid ones — accumulator survives."""
    raw = [
        "{not valid json",
        json.dumps(_ev("tool.execution_complete",
                        ts="2026-05-06T10:00:00Z")),
        "  ",  # whitespace
        "garbage",
    ]
    event_epoch = datetime.fromisoformat(
        "2026-05-06T10:00:00+00:00",
    ).timestamp()
    state = evaluate_idle_state(raw, current_time=event_epoch + 15,
                                 timeout_s=10.0)
    assert state["idle"] is True
    assert state["last_event_type"] == "tool.execution_complete"


def test_unknown_event_type_treated_as_active():
    """prompt.user / prompt.assistant aren't terminal — keep waiting."""
    lines = _serialize([
        _ev("tool.execution_complete", ts="2026-05-06T10:00:00Z"),
        _ev("prompt.user", ts="2026-05-06T10:01:00Z"),
    ])
    state = evaluate_idle_state(lines, current_time=time.time(),
                                 timeout_s=10.0)
    # last_type is prompt.user — not in _IDLE_TERMINAL_TYPES → active
    assert state["idle"] is False
    assert state["reason"] == "active"


def test_skips_non_dict_events():
    """JSON list / string at line scope → skipped, no crash."""
    raw = [
        json.dumps(["not a dict"]),
        json.dumps("string"),
        json.dumps(_ev("tool.execution_complete", ts="2026-05-06T10:00:00Z")),
    ]
    event_epoch = datetime.fromisoformat(
        "2026-05-06T10:00:00+00:00",
    ).timestamp()
    state = evaluate_idle_state(raw, current_time=event_epoch + 15,
                                 timeout_s=10.0)
    assert state["idle"] is True


def test_event_without_timestamp_treated_as_idle_immediately():
    """Defensive: complete event without ts → best-effort idle=True."""
    lines = _serialize([
        {"type": "tool.execution_complete", "data": {}},
    ])
    state = evaluate_idle_state(lines, current_time=time.time(),
                                 timeout_s=10.0)
    assert state["idle"] is True


# ─── _CopilotIdleMonitor — synchronous poll_once ────────────────────────────


def _write_events(tmp_path, events):
    path = tmp_path / "events.jsonl"
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")
    return path


def test_poll_once_fires_callback_on_first_idle(tmp_path):
    """First idle transition fires the callback; subsequent polls
    while idle don't (latched)."""
    ts = "2026-05-06T10:00:00Z"
    path = _write_events(tmp_path, [
        _ev("tool.execution_complete", ts=ts),
    ])
    event_epoch = datetime.fromisoformat(ts.replace("Z", "+00:00")).timestamp()

    fired = []

    def cb(state):
        fired.append(state)

    monitor = _CopilotIdleMonitor(
        events_path=str(path),
        on_idle_callback=cb,
        timeout_s=10.0,
        clock=lambda: event_epoch + 11,
    )
    state1 = monitor.poll_once()
    state2 = monitor.poll_once()

    assert state1["idle"] is True
    assert state2["idle"] is True
    assert len(fired) == 1, "callback should fire once, not twice"


def test_poll_once_re_fires_after_active_event(tmp_path):
    """If session goes back to active and idle again, callback re-fires."""
    path = tmp_path / "events.jsonl"
    fired = []

    def cb(state):
        fired.append(state["last_event_type"])

    epoch = 1700000000.0
    monitor = _CopilotIdleMonitor(
        events_path=str(path),
        on_idle_callback=cb,
        timeout_s=10.0,
        clock=lambda: epoch + 11,
    )

    # Phase 1: complete + 11s elapsed → idle
    path.write_text(json.dumps(_ev(
        "tool.execution_complete",
        ts=datetime.fromtimestamp(epoch, tz=timezone.utc).isoformat()
            .replace("+00:00", "Z"),
    )) + "\n")
    monitor.poll_once()
    assert len(fired) == 1

    # Phase 2: tool starts again → active, latch resets
    path.write_text(
        json.dumps(_ev("tool.execution_complete",
                        ts=datetime.fromtimestamp(epoch, tz=timezone.utc)
                        .isoformat().replace("+00:00", "Z"))) + "\n"
        + json.dumps(_ev("tool.execution_start",
                          ts=datetime.fromtimestamp(epoch + 1,
                                                     tz=timezone.utc)
                          .isoformat().replace("+00:00", "Z"))) + "\n"
    )
    state = monitor.poll_once()
    assert state["idle"] is False

    # Phase 3: another complete → idle again, callback re-fires
    path.write_text(
        path.read_text()
        + json.dumps(_ev("tool.execution_complete",
                          ts=datetime.fromtimestamp(epoch, tz=timezone.utc)
                          .isoformat().replace("+00:00", "Z"))) + "\n"
    )
    monitor.poll_once()
    assert len(fired) == 2


def test_poll_once_routes_through_signal_via(tmp_path):
    """When `signal_via` is provided, it receives (callback, state)."""
    path = _write_events(tmp_path, [
        _ev("tool.execution_complete", ts="2026-05-06T10:00:00Z"),
    ])

    intercepts = []

    def signal_via(fn, arg):
        intercepts.append((fn, arg))

    fired = []

    def cb(state):
        fired.append(state)

    epoch = datetime.fromisoformat(
        "2026-05-06T10:00:00+00:00",
    ).timestamp()
    monitor = _CopilotIdleMonitor(
        events_path=str(path),
        on_idle_callback=cb,
        timeout_s=10.0,
        clock=lambda: epoch + 15,
        signal_via=signal_via,
    )
    monitor.poll_once()
    assert len(intercepts) == 1
    assert intercepts[0][0] is cb
    # When using signal_via, the callback isn't invoked directly
    assert fired == []


def test_poll_once_no_log_returns_no_events(tmp_path):
    monitor = _CopilotIdleMonitor(
        events_path=str(tmp_path / "missing.jsonl"),
        on_idle_callback=lambda s: None,
    )
    state = monitor.poll_once()
    assert state["reason"] == "no_events"
    assert state["idle"] is False


# ─── _CopilotIdleMonitor — thread lifecycle ─────────────────────────────────


def test_monitor_starts_and_stops_thread(tmp_path):
    path = _write_events(tmp_path, [
        _ev("tool.execution_start", ts="2026-05-06T10:00:00Z"),
    ])
    monitor = _CopilotIdleMonitor(
        events_path=str(path),
        on_idle_callback=lambda s: None,
        poll_interval_s=0.05,
    )
    assert not monitor.is_running()
    monitor.start()
    time.sleep(0.1)
    assert monitor.is_running()
    monitor.stop(join_timeout=2.0)
    assert not monitor.is_running()


def test_monitor_stops_after_session_shutdown(tmp_path):
    """Permanent idle (session.shutdown) → thread exits on its own."""
    path = _write_events(tmp_path, [
        _ev("session.shutdown", ts="2026-05-06T10:00:00Z"),
    ])
    fired = []
    monitor = _CopilotIdleMonitor(
        events_path=str(path),
        on_idle_callback=lambda s: fired.append(s),
        poll_interval_s=0.02,
    )
    monitor.start()
    # Wait up to 1s for thread to terminate after seeing shutdown.
    for _ in range(50):
        if not monitor.is_running():
            break
        time.sleep(0.02)
    assert not monitor.is_running()
    assert len(fired) == 1
    assert fired[0]["reason"] == "shutdown"


def test_start_is_idempotent(tmp_path):
    """Calling start() twice doesn't spawn a second thread."""
    path = _write_events(tmp_path, [
        _ev("tool.execution_start", ts="2026-05-06T10:00:00Z"),
    ])
    monitor = _CopilotIdleMonitor(
        events_path=str(path),
        on_idle_callback=lambda s: None,
        poll_interval_s=0.1,
    )
    monitor.start()
    first_thread = monitor._thread
    monitor.start()  # should be a no-op
    assert monitor._thread is first_thread
    monitor.stop()


# ─── CopilotProvider integration ────────────────────────────────────────────


def test_provider_create_idle_monitor(tmp_path):
    """Factory method on CopilotProvider returns a working monitor."""
    cfg = load_providers_config()["providers"]["copilot"]
    provider = CopilotProvider(cfg)

    path = _write_events(tmp_path, [
        _ev("tool.execution_complete", ts="2026-05-06T10:00:00Z"),
    ])
    epoch = datetime.fromisoformat(
        "2026-05-06T10:00:00+00:00",
    ).timestamp()

    fired = []
    monitor = provider.create_idle_monitor(
        events_path=str(path),
        on_idle_callback=lambda s: fired.append(s),
    )
    monitor._clock = lambda: epoch + 15
    state = monitor.poll_once()
    assert state["idle"] is True
    assert len(fired) == 1


def test_provider_detect_idle_with_no_log_returns_true(tmp_path):
    """Without any events.jsonl on disk, detect_idle is best-effort True."""
    cfg = load_providers_config()["providers"]["copilot"]
    provider = CopilotProvider(cfg)
    # session_id "missing-uuid" → resolved path won't exist
    assert provider.detect_idle(
        terminal=None, session_id="missing-uuid", timeout_s=10.0,
    ) is True


def test_provider_resolve_events_path_with_session_id():
    """Path template uses {session_id} placeholder."""
    cfg = load_providers_config()["providers"]["copilot"]
    provider = CopilotProvider(cfg)
    path = provider._resolve_events_path("uuid-abc")
    assert path is not None
    assert "uuid-abc" in path
    assert path.endswith("events.jsonl")


def test_idle_event_type_consts_are_what_we_expect():
    """Sanity that constants match the documented behavior — keeps
    the L2 follow-up review tractable."""
    assert "tool.execution_complete" in _IDLE_TERMINAL_TYPES
    assert "session.shutdown" in _IDLE_PERMANENT_TYPES
    assert "tool.execution_start" in _IDLE_ACTIVE_TYPES
