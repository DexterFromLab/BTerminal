"""Pin tests for tools/_e2e_live_monitor.sh — live screenshot + log
streaming framework for VM E2E tests (#156).

Verifies:
  - script syntax (bash -n)
  - --help renders without errors
  - start/status/tag/stop lifecycle in MONITOR_NO_VM=1 mode (no ssh)
  - state file written + cleaned
  - frames/log-stream/monitor.log directory layout created
  - tag command produces a screenshot file
  - duplicate-start guard (refuses if state file exists)

Tests run locally without ssh, without VM, without xdotool. Real VM
runs covered by per-menu E2E scripts (#157-#162) which use this
framework as helper.
"""
import os
import shutil
import subprocess
import time
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "_e2e_live_monitor.sh"


@pytest.fixture
def monitor_env(tmp_path):
    """Return a clean env dict + cleanup fixture for the monitor."""
    state_file = tmp_path / ".monitor_state"
    session_root = tmp_path / "sessions"
    env = {
        **os.environ,
        "MONITOR_NO_VM": "1",
        "STATE_FILE": str(state_file),
        "SESSION_DIR_ROOT": str(session_root),
        "MONITOR_INTERVAL_SEC": "1",
    }
    yield env
    # teardown — best-effort stop if a test left it running
    if state_file.exists():
        subprocess.run(
            [str(SCRIPT), "stop"], env=env,
            capture_output=True, timeout=5,
        )


def _run(args, env, timeout=10):
    return subprocess.run(
        [str(SCRIPT), *args], env=env,
        capture_output=True, text=True, timeout=timeout,
    )


def test_script_exists_and_executable():
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK), "must be executable"


def test_script_passes_bash_syntax_check():
    res = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr


def test_help_command_renders():
    res = subprocess.run(
        [str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=5,
    )
    assert res.returncode == 0
    assert "live screenshot" in res.stdout.lower()
    assert "start" in res.stdout
    assert "stop" in res.stdout
    assert "tag" in res.stdout


def test_unknown_command_exits_2(monitor_env):
    res = _run(["bogus_subcommand"], monitor_env)
    assert res.returncode == 2
    assert "Unknown command" in res.stderr


def test_status_when_not_running_exits_1(monitor_env):
    res = _run(["status"], monitor_env)
    assert res.returncode == 1
    assert "stopped" in res.stdout


def test_stop_when_not_running_exits_2(monitor_env):
    res = _run(["stop"], monitor_env)
    assert res.returncode == 2
    assert "no monitor running" in res.stderr.lower()


def test_tag_when_not_running_exits_2(monitor_env):
    res = _run(["tag", "X"], monitor_env)
    assert res.returncode == 2


def test_start_creates_session_dir_and_state(monitor_env):
    res = _run(["start"], monitor_env)
    assert res.returncode == 0, res.stderr
    session_dir = Path(res.stdout.strip())
    assert session_dir.is_dir()
    assert (session_dir / "frames").is_dir()
    assert (session_dir / "log-stream.txt").is_file()
    assert (session_dir / "monitor.log").is_file()
    state_file = Path(monitor_env["STATE_FILE"])
    assert state_file.is_file()
    state_content = state_file.read_text()
    assert "SESSION_DIR=" in state_content
    assert "FRAMES_PID=" in state_content
    assert "LOGS_PID=" in state_content


def test_status_after_start_reports_running(monitor_env):
    _run(["start"], monitor_env)
    res = _run(["status"], monitor_env)
    assert res.returncode == 0
    assert "running" in res.stdout
    assert "session_dir=" in res.stdout
    assert "frames_pid=" in res.stdout
    assert "logs_pid=" in res.stdout


def test_double_start_refused(monitor_env):
    _run(["start"], monitor_env)
    res = _run(["start"], monitor_env)
    assert res.returncode == 2
    assert "already running" in res.stderr.lower()


def test_tag_creates_marker_file(monitor_env):
    start_res = _run(["start"], monitor_env)
    session_dir = Path(start_res.stdout.strip())
    res = _run(["tag", "license_dialog"], monitor_env)
    assert res.returncode == 0
    tag_path = Path(res.stdout.strip())
    assert tag_path.is_file()
    assert tag_path.parent == session_dir
    assert "license_dialog" in tag_path.name
    assert tag_path.suffix == ".png"


def test_stop_removes_state_file(monitor_env):
    _run(["start"], monitor_env)
    state_file = Path(monitor_env["STATE_FILE"])
    assert state_file.is_file()
    res = _run(["stop"], monitor_env)
    assert res.returncode == 0
    assert not state_file.exists(), "state file must be removed"


def test_full_lifecycle_smoke(monitor_env):
    """Integration: start → status → tag(2x) → stop → state gone."""
    start_res = _run(["start"], monitor_env)
    session_dir = Path(start_res.stdout.strip())
    assert session_dir.is_dir()

    assert _run(["status"], monitor_env).returncode == 0

    t1 = _run(["tag", "phase1"], monitor_env)
    t2 = _run(["tag", "phase2"], monitor_env)
    assert t1.returncode == 0 and t2.returncode == 0
    tags = list(session_dir.glob("tag-*.png"))
    assert len(tags) == 2

    stop_res = _run(["stop"], monitor_env)
    assert stop_res.returncode == 0

    # After stop: status returns 1 ("stopped")
    assert _run(["status"], monitor_env).returncode == 1


def test_script_documents_required_helpers_for_e2e_runners():
    """Pin: helper script must document key flags so #157-#162 know how
    to compose with it (start/tag/stop/status pattern + env vars)."""
    src = SCRIPT.read_text()
    for token in ("VM_HOST", "VM_LOG_DIR", "MONITOR_INTERVAL_SEC",
                  "STATE_FILE", "SESSION_DIR_ROOT", "MONITOR_NO_VM"):
        assert token in src, f"missing env var doc/use: {token}"
    for cmd in ("start", "stop", "tag", "status"):
        assert cmd in src, f"missing subcommand: {cmd}"
