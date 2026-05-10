"""Tests for the mock_ai_cli `emit_events_jsonl` directive — T3.4.

The directive lets a scenario instruct mock_ai_cli to write a list of
JSONL events to a target path with per-event `delay_ms` pauses,
mirroring how Copilot incrementally writes its events.jsonl. Used by
CopilotStatsReader / T4.1 idle-detection E2E tests.
"""
from __future__ import annotations

import json
import os
import shutil
import stat
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
MOCK_SRC = REPO_ROOT / "tools" / "mock_ai_cli"


def _make_executable(path):
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def fake_mock(tmp_path):
    """Stage tools/mock_ai_cli into tmp_path and return its path."""
    target = tmp_path / "mock_ai_cli"
    shutil.copy(str(MOCK_SRC), str(target))
    _make_executable(target)
    return target


def _build_scenario(events_path, events):
    return {
        "responses": [],
        "default_reply": "> {input}",
        "exit_on": "^/exit$",
        "emit_events_jsonl": {
            "path": str(events_path),
            "events": events,
        },
    }


def _run_mock(mock_bin, scenario_path, events_path, timeout=8.0,
              expect_lines=0, poll_timeout=3.0):
    """Run mock with the given scenario, optionally polling for the
    emit_events_jsonl emitter to finish writing `expect_lines` lines
    BEFORE sending /exit. Without that wait, the daemon emitter is
    killed when stdin closes, leading to flaky line counts under load.

    Returns (returncode, stdout, stderr).
    """
    env = {
        **os.environ,
        "MOCK_AI_CLI_SCENARIO": str(scenario_path),
    }
    proc = subprocess.Popen(
        [str(mock_bin)],
        stdin=subprocess.PIPE,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        env=env,
        text=True,
    )
    if expect_lines > 0:
        deadline = time.monotonic() + poll_timeout
        while time.monotonic() < deadline:
            try:
                lines = events_path.read_text().splitlines() if events_path.exists() else []
            except OSError:
                lines = []
            if len(lines) >= expect_lines:
                break
            time.sleep(0.01)
    stdout, stderr = proc.communicate(input="/exit\n", timeout=timeout)
    return proc.returncode, stdout, stderr


# ─── Basic emit semantics ───────────────────────────────────────────────────

def test_writes_events_in_order(tmp_path, fake_mock):
    """Mock writes each event as a JSONL line, in scenario order."""
    events_path = tmp_path / "events.jsonl"
    events = [
        {"delay_ms": 0, "event": {"type": "session.start", "data": {}}},
        {"delay_ms": 0, "event": {"type": "tool.execution_complete",
                                   "data": {"usage": {"inputTokens": 10}}}},
        {"delay_ms": 0, "event": {"type": "session.shutdown",
                                   "data": {"modelMetrics": {}}}},
    ]
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(_build_scenario(events_path, events)))

    rc, _, _ = _run_mock(fake_mock, scenario_path, events_path,
                          expect_lines=3)
    assert rc == 0

    lines = events_path.read_text().strip().splitlines()
    assert len(lines) == 3
    parsed = [json.loads(line) for line in lines]
    assert parsed[0]["type"] == "session.start"
    assert parsed[1]["type"] == "tool.execution_complete"
    assert parsed[2]["type"] == "session.shutdown"


def test_each_event_is_valid_jsonl(tmp_path, fake_mock):
    """Every line is parseable JSON with no extra whitespace beyond `\\n`."""
    events_path = tmp_path / "events.jsonl"
    events = [
        {"delay_ms": 0, "event": {"type": "x", "data": {"n": 1}}},
        {"delay_ms": 0, "event": {"type": "y", "data": {"n": 2}}},
    ]
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(_build_scenario(events_path, events)))

    _run_mock(fake_mock, scenario_path, events_path, expect_lines=2)

    raw = events_path.read_text()
    # Each line ends with a single newline
    assert raw.endswith("\n")
    for line in raw.splitlines():
        assert line.strip()  # no empty lines
        json.loads(line)  # must parse


def test_creates_parent_directories(tmp_path, fake_mock):
    """Path with non-existent parent dirs → mkdir -p before writing.

    Poll for the event to land before sending /exit, otherwise the
    daemon emitter can be killed mid-write."""
    events_path = tmp_path / "deep" / "nested" / "session-state" / "uuid" / "events.jsonl"
    events = [{"delay_ms": 0, "event": {"type": "session.start"}}]
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(_build_scenario(events_path, events)))

    env = {**os.environ, "MOCK_AI_CLI_SCENARIO": str(scenario_path)}
    proc = subprocess.Popen(
        [str(fake_mock)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, text=True,
    )
    try:
        deadline = time.monotonic() + 3.0
        while time.monotonic() < deadline:
            if events_path.exists() and events_path.stat().st_size > 0:
                break
            time.sleep(0.01)
    finally:
        proc.communicate(input="/exit\n", timeout=3.0)

    assert events_path.exists()
    assert json.loads(events_path.read_text().strip())["type"] == "session.start"


# ─── Timing semantics ───────────────────────────────────────────────────────

def test_delay_ms_introduces_pause(tmp_path, fake_mock):
    """Events with delay_ms appear after their delay — proves the
    emitter actually streams events over time (not all at once at
    launch). Critical for T4.1 idle detection tests that depend on
    inter-event gaps. We poll the file rather than measuring the
    overall mock runtime because /exit kills the daemon emitter."""
    events_path = tmp_path / "events.jsonl"
    events = [
        {"delay_ms": 0,   "event": {"type": "first"}},
        {"delay_ms": 300, "event": {"type": "second"}},
    ]
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(_build_scenario(events_path, events)))

    env = {**os.environ, "MOCK_AI_CLI_SCENARIO": str(scenario_path)}
    proc = subprocess.Popen(
        [str(fake_mock)],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE,
        stderr=subprocess.PIPE, env=env, text=True,
    )
    try:
        deadline = time.monotonic() + 3.0

        # Wait for first event
        first_ts = None
        while time.monotonic() < deadline:
            if events_path.exists() and events_path.stat().st_size > 0:
                first_ts = time.monotonic()
                break
            time.sleep(0.01)
        assert first_ts is not None, "first event never appeared"

        # Wait for second event
        second_ts = None
        while time.monotonic() < deadline:
            try:
                lines = events_path.read_text().splitlines()
            except OSError:
                lines = []
            if len(lines) >= 2:
                second_ts = time.monotonic()
                break
            time.sleep(0.01)
        assert second_ts is not None, "second event never appeared"

        gap = second_ts - first_ts
        # 300ms scripted delay must be observable (with some slack)
        assert gap >= 0.25, f"expected ≥0.25s gap, got {gap:.3f}s"
    finally:
        proc.communicate(input="/exit\n", timeout=3.0)

    lines = events_path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "first"
    assert json.loads(lines[1])["type"] == "second"


def test_emitter_does_not_block_stdin_loop(tmp_path, fake_mock):
    """Emitter runs in a daemon thread — mock keeps responding to
    stdin while events are being scheduled."""
    events_path = tmp_path / "events.jsonl"
    # Long delay so emitter is still scheduling when /exit arrives.
    events = [{"delay_ms": 5000, "event": {"type": "late"}}]
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(_build_scenario(events_path, events)))

    start = time.monotonic()
    rc, _, _ = _run_mock(fake_mock, scenario_path, events_path, timeout=3.0)
    elapsed = time.monotonic() - start

    # /exit should drain quickly even though emitter is mid-sleep.
    assert elapsed < 2.5, f"stdin loop blocked by emitter (took {elapsed}s)"
    assert rc == 0


# ─── Resilience ─────────────────────────────────────────────────────────────

def test_missing_directive_is_a_noop(tmp_path, fake_mock):
    """Scenarios without emit_events_jsonl behave as before."""
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps({
        "responses": [{"trigger": "ping", "reply": "pong"}],
        "exit_on": "^/exit$",
    }))
    rc, stdout, _ = _run_mock(fake_mock, scenario_path, tmp_path / "unused.jsonl")
    assert rc == 0
    # No events.jsonl created
    assert not (tmp_path / "unused.jsonl").exists()


def test_empty_events_list_is_a_noop(tmp_path, fake_mock):
    """emit_events_jsonl with events=[] silently does nothing."""
    events_path = tmp_path / "events.jsonl"
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps({
        "responses": [],
        "exit_on": "^/exit$",
        "emit_events_jsonl": {"path": str(events_path), "events": []},
    }))
    rc, _, _ = _run_mock(fake_mock, scenario_path, events_path)
    assert rc == 0
    assert not events_path.exists()


def test_directive_with_missing_path_is_a_noop(tmp_path, fake_mock):
    """Malformed directive (no path) → don't crash, don't emit."""
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps({
        "responses": [],
        "exit_on": "^/exit$",
        "emit_events_jsonl": {"events": [{"delay_ms": 0, "event": {}}]},
    }))
    rc, _, _ = _run_mock(fake_mock, scenario_path, tmp_path / "unused.jsonl")
    assert rc == 0


def test_emit_appends_to_existing_file(tmp_path, fake_mock):
    """Append mode — existing events stay; new events tack on."""
    events_path = tmp_path / "events.jsonl"
    events_path.write_text(json.dumps({"type": "preexisting"}) + "\n")

    events = [{"delay_ms": 0, "event": {"type": "new"}}]
    scenario_path = tmp_path / "scenario.json"
    scenario_path.write_text(json.dumps(_build_scenario(events_path, events)))

    _run_mock(fake_mock, scenario_path, events_path, expect_lines=2)

    lines = events_path.read_text().strip().splitlines()
    assert len(lines) == 2
    assert json.loads(lines[0])["type"] == "preexisting"
    assert json.loads(lines[1])["type"] == "new"


# ─── Bundled scenario file is valid ─────────────────────────────────────────

def test_bundled_copilot_with_events_scenario_is_valid_json():
    path = REPO_ROOT / "tests" / "scenarios" / "copilot_with_events.json"
    assert path.exists()
    data = json.loads(path.read_text())
    assert "emit_events_jsonl" in data
    spec = data["emit_events_jsonl"]
    assert "path" in spec
    assert isinstance(spec["events"], list)
    # Non-empty + each entry has the expected shape
    assert len(spec["events"]) >= 3
    for entry in spec["events"]:
        assert "event" in entry
        assert isinstance(entry["event"], dict)
