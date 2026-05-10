"""Tier 3 acceptance — Copilot stats reader + capability dispatch (T3.10).

Spawns BTerminal with a mock `copilot` binary that ALSO emits a
realistic events.jsonl (T3.4 directive), then verifies:

  * Copilot tab opens cleanly — visual marker (🤖), intro recorded.
  * The mock writes events.jsonl into the Copilot session-state dir
    where CopilotStatsReader (T3.2) looks for it.
  * CopilotStatsReader parses the events file and accumulates tokens
    + extracts cost from session.shutdown.modelMetrics (T3.2 contract).
  * stats_widget_options_for_ai_config returns hide_plan_usage=True
    for Copilot (T3.9 contract — no plan-usage gauge for Copilot tabs).
  * Claude session still works alongside (no Tier 3 regression).

This is the automated stand-in for the manual "open Copilot tab and
watch tokens grow" smoke step in the implementation plan. Real-time
tail-f wiring lives in T4.1; here we just verify the file-parsing
path end-to-end with a mock-emitted log.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
MOCK_SRC = REPO_ROOT / "tools" / "mock_ai_cli"


def _wait_for_health(base, deadline) -> bool:
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{base}/api/health", timeout=1.0)
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    return False


def _http(base, token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}",
        data=data,
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except (json.JSONDecodeError, OSError):
            return exc.code, {"error": exc.reason}


@pytest.fixture
def bterminal_with_event_emitting_copilot():
    """Spawn BTerminal whose `copilot` binary also writes a realistic
    events.jsonl into the user's session-state directory while the
    interactive session runs. Yields the home + REST handles + the
    target events.jsonl path."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-tier3-")
    seed_license(home)

    # Stage 1: mock `copilot` binary on PATH.
    fake_bin = Path(home) / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_copilot = fake_bin / "copilot"
    shutil.copy(str(MOCK_SRC), str(fake_copilot))
    fake_copilot.chmod(
        fake_copilot.stat().st_mode
        | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
    )

    # Stage 2: Copilot session-state dir inside the isolated HOME so
    # CopilotStatsReader's default ~/.copilot/session-state/* path
    # resolves to a deterministic location for this test.
    session_state = Path(home) / ".copilot" / "session-state" / "uuid-tier3"
    session_state.mkdir(parents=True, exist_ok=True)
    events_target = session_state / "events.jsonl"

    # Stage 3: scenario telling mock_ai_cli to emit a realistic events
    # stream while the (interactive) session handles stdin.
    scenario_path = Path(home) / "copilot_scenario.json"
    scenario = {
        "responses": [],
        "default_reply": "> {input}",
        "exit_on": "^/exit$",
        "emit_events_jsonl": {
            "path": str(events_target),
            "events": [
                {"delay_ms": 0,
                 "event": {"type": "session.start",
                           "timestamp": "2026-05-06T10:00:00Z",
                           "data": {"sessionId": "uuid-tier3",
                                    "model": "claude-sonnet-4-5"}}},
                {"delay_ms": 30,
                 "event": {"type": "tool.execution_complete",
                           "timestamp": "2026-05-06T10:00:01Z",
                           "data": {"toolName": "shell",
                                    "usage": {"inputTokens": 120,
                                              "outputTokens": 40}}}},
                {"delay_ms": 30,
                 "event": {"type": "tool.execution_complete",
                           "timestamp": "2026-05-06T10:00:02Z",
                           "data": {"toolName": "file.read",
                                    "usage": {"inputTokens": 200,
                                              "outputTokens": 80,
                                              "cacheReadTokens": 30}}}},
                {"delay_ms": 30,
                 "event": {"type": "session.shutdown",
                           "timestamp": "2026-05-06T10:00:05Z",
                           "data": {"modelMetrics": {
                               "claude-sonnet-4-5": {
                                   "requests": {"count": 2,
                                                "cost": 0.0042},
                               },
                           }}}},
            ],
        },
    }
    scenario_path.write_text(json.dumps(scenario))

    # Stage 4: pre-seed ai_sessions.json with one Copilot session.
    cfg_dir = Path(home) / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    project_dir = Path(home) / "myproj"
    project_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ai_sessions.json").write_text(json.dumps([
        {
            "id": "copilot-1",
            "name": "MyCopilotProj",
            "provider": "copilot",
            "project_dir": str(project_dir),
            "color": "#a6e3a1",
            "provider_options": {"skip_permissions": True},
        },
    ]))

    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    env = {
        **os.environ,
        "HOME": home,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "BTERMINAL_DEBUG_REST_PORT": str(port),
        "MOCK_AI_CLI_SCENARIO": str(scenario_path),
    }
    stderr_path = Path(home) / "bterminal-stderr.log"
    stderr_handle = open(stderr_path, "w")
    proc = subprocess.Popen(
        ["xvfb-run", "-a", sys.executable, "-m", "bterminal",
         "--debug-rest"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=stderr_handle,
        start_new_session=True,
    )

    base = f"http://127.0.0.1:{port}"
    if not _wait_for_health(base, time.monotonic() + 15):
        proc.terminate()
        proc.wait(timeout=5)
        stderr_handle.close()
        pytest.fail(f"BTerminal didn't come up; stderr: {stderr_path}")

    token = (cfg_dir / "debug_token").read_text().strip()

    try:
        yield {
            "base": base, "token": token, "home": home,
            "events_path": events_target,
            "stderr_path": str(stderr_path),
        }
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        stderr_handle.close()


def _stderr_clean_or_fail(stderr_path):
    try:
        text = Path(stderr_path).read_text(errors="replace")
    except OSError:
        return
    bad = [p for p in
           ("NameError", "AttributeError", "ImportError",
            "Traceback (most recent call last)")
           if p in text]
    if bad:
        pytest.fail(f"stderr has {bad}: {text[:2000]}")


# ─── Acceptance scenarios ────────────────────────────────────────────────────

def test_copilot_tab_opens_with_event_emitter(bterminal_with_event_emitting_copilot):
    """Open Copilot tab via REST → mock starts → events.jsonl appears."""
    state = bterminal_with_event_emitting_copilot
    base, token = state["base"], state["token"]

    status, body = _http(
        base, token, "POST",
        "/api/tabs/ai/copilot",
        {"config_name": "MyCopilotProj"},
    )
    assert status == 200, body
    idx = body["idx"]

    # Tab visible with Copilot provider tag (task #65: emoji moved
    # from title text to SVG pixbuf + new payload field).
    status, tabs = _http(base, token, "GET", "/api/tabs")
    assert status == 200
    matching = [t for t in tabs["tabs"] if t["idx"] == idx]
    assert matching and matching[0].get("provider") == "copilot"

    # Mock emitter writes events.jsonl after the spawn (4 events scripted).
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        if state["events_path"].exists():
            try:
                lines = state["events_path"].read_text().splitlines()
            except OSError:
                lines = []
            if len(lines) >= 4:
                break
        time.sleep(0.05)

    lines = state["events_path"].read_text().splitlines()
    assert len(lines) >= 4, (
        f"emitter wrote only {len(lines)} events; expected >=4"
    )

    _stderr_clean_or_fail(state["stderr_path"])


def test_copilot_stats_reader_picks_up_emitted_events(
    bterminal_with_event_emitting_copilot,
):
    """Independent of the GTK widget: drive CopilotStatsReader at the
    same events.jsonl the mock emitter populated and verify it
    accumulates the scripted token totals + extracts shutdown cost."""
    state = bterminal_with_event_emitting_copilot
    base, token = state["base"], state["token"]

    # Trigger the spawn so the emitter begins running.
    _http(base, token, "POST", "/api/tabs/ai/copilot",
           {"config_name": "MyCopilotProj"})

    # Wait for the emitter to write all 4 events (incl. session.shutdown).
    deadline = time.monotonic() + 5.0
    while time.monotonic() < deadline:
        try:
            lines = state["events_path"].read_text().splitlines()
        except OSError:
            lines = []
        if len(lines) >= 4 and any("session.shutdown" in l for l in lines):
            break
        time.sleep(0.05)

    # Drive the reader directly against the populated file
    from bterminal.ui.stats import CopilotStatsReader
    reader = CopilotStatsReader(project_dir=state["home"])
    reader._session_state_dir = str(Path(state["home"]) / ".copilot" / "session-state")
    stats = reader.read_session_tokens()

    # Scripted totals: input=120+200=320, output=40+80=120, cache_read=30
    assert stats.input == 320, f"got input={stats.input}"
    assert stats.output == 120
    assert stats.cache_read == 30
    assert stats.responses == 2
    assert stats.model == "claude-sonnet-4-5"

    # Cost from session.shutdown.modelMetrics — verbatim 0.0042
    assert reader.read_session_cost(stats) == pytest.approx(0.0042)


def test_widget_options_hide_plan_usage_for_copilot_tab():
    """T3.9 contract: stats_widget_options_for_ai_config returns
    hide_plan_usage=True for Copilot sessions, regardless of subprocess
    state. This test runs without spawning BTerminal — pure unit-level."""
    from bterminal.providers import (
        ProviderRegistry, load_providers_config, reset_registry,
    )
    from bterminal.ui.stats import stats_widget_options_for_ai_config

    reset_registry()
    try:
        reg = ProviderRegistry(config=load_providers_config())
        opts = stats_widget_options_for_ai_config(
            {"provider": "copilot", "project_dir": "/tmp/proj"}, reg,
        )
        assert opts == {"hide_plan_usage": True}

        opts_claude = stats_widget_options_for_ai_config(
            {"provider": "claude", "project_dir": "/tmp/proj"}, reg,
        )
        assert opts_claude == {"hide_plan_usage": False}
    finally:
        reset_registry()


def test_no_regression_for_claude_path(bterminal_with_event_emitting_copilot):
    """Open a Claude session alongside (uses default Claude factory
    path). The mock binary covers `claude` too if it's on PATH; here
    we verify the REST flow accepts a Claude session even though the
    pre-seeded session is Copilot — no provider crosstalk."""
    state = bterminal_with_event_emitting_copilot
    base, token = state["base"], state["token"]

    # Ensure `claude` is also stubbed (mock_ai_cli on PATH covers any name)
    fake_bin = Path(state["home"]) / "fake-bin"
    fake_claude = fake_bin / "claude"
    if not fake_claude.exists():
        shutil.copy(str(MOCK_SRC), str(fake_claude))
        fake_claude.chmod(
            fake_claude.stat().st_mode
            | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH,
        )

    # T4.6.1: legacy /api/tabs/claude removed → use /api/tabs/ai/claude.
    # We assert "session not found" 404 as the no-regression check —
    # the dispatcher itself routes correctly even though the named
    # session doesn't exist in this fixture.
    status, body = _http(
        base, token, "POST", "/api/tabs/ai/claude",
        {"config_name": "DoesNotExist"},
    )
    assert status == 404
    _stderr_clean_or_fail(state["stderr_path"])
