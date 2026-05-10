"""E2E: open Copilot tab via REST with mock CLI scenario (T2.10).

Spawns BTerminal as a subprocess with:
  - License pre-seeded so the modal dialog doesn't block startup.
  - ai_sessions.json pre-seeded with one Copilot session.
  - tools/mock_ai_cli copied as `copilot` into a tmp bin dir on $PATH
    so CopilotProvider.find_binary() resolves it.
  - MOCK_AI_CLI_SCENARIO env pointing at copilot_basic.json so the
    mock responds to typical inputs (hello / ping / [AUTO-TRIGGER]).

Verifies the full Tier 2 path: REST endpoint → registry dispatch →
CopilotProvider.build_argv → spawn_ai_cli → tab created with the 🤖
visual marker, intro prompt recorded via record_feed, and clean close.
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
import urllib.parse
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
MOCK_SRC = REPO_ROOT / "tools" / "mock_ai_cli"
COPILOT_SCENARIO = REPO_ROOT / "tests" / "scenarios" / "copilot_basic.json"


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
    """Helper: REST call returning (status, json_dict). Empty body OK."""
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
def bterminal_with_copilot_mock():
    """Spawn BTerminal with a mock `copilot` binary on PATH and a
    seeded Copilot session. Yields (base, token, home, stderr_path)."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-copilot-e2e-")
    seed_license(home)

    # Stage 1: stub `copilot` binary in a fake bin dir.
    fake_bin = Path(home) / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    fake_copilot = fake_bin / "copilot"
    shutil.copy(str(MOCK_SRC), str(fake_copilot))
    fake_copilot.chmod(
        fake_copilot.stat().st_mode
        | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
    )

    # Stage 2: pre-seed ai_sessions.json with a Copilot session.
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

    # Stage 3: spawn the subprocess on an ephemeral port. Augment PATH
    # so spawn_ai_cli's find_binary() picks up the fake `copilot`.
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
        # Mock CLI scenario — applied to every spawn of `copilot`.
        "MOCK_AI_CLI_SCENARIO": str(COPILOT_SCENARIO),
    }

    stderr_path = Path(home) / "bterminal-stderr.log"
    stderr_handle = open(stderr_path, "w")
    proc = subprocess.Popen(
        ["xvfb-run", "-a", sys.executable, "-m", "bterminal", "--debug-rest"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=stderr_handle,
        start_new_session=True,
    )

    base = f"http://127.0.0.1:{port}"
    if not _wait_for_health(base, time.monotonic() + 15):
        proc.terminate()
        proc.wait(timeout=5)
        stderr_handle.close()
        pytest.fail(f"BTerminal didn't come up; stderr at {stderr_path}")

    token = (cfg_dir / "debug_token").read_text().strip()

    try:
        yield {
            "base": base, "token": token, "home": home,
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
    """Read subprocess stderr and fail the test if it contains
    Python-level errors that indicate broken refactors (NameError,
    AttributeError, ImportError). Mirrors bt_stderr_watcher logic
    used by smoke_battery."""
    try:
        text = Path(stderr_path).read_text(errors="replace")
    except OSError:
        return
    bad_patterns = ["NameError", "AttributeError", "ImportError",
                    "Traceback (most recent call last)"]
    bad = [p for p in bad_patterns if p in text]
    if bad:
        pytest.fail(
            f"BTerminal stderr contained {bad}: {text[:2000]}"
        )


# ─── Tests ──────────────────────────────────────────────────────────────────

def test_open_copilot_tab_via_rest(bterminal_with_copilot_mock):
    """Full T2.10 acceptance: REST → registry → spawn → tab visible
    with 🤖 marker → intro prompt recorded."""
    base = bterminal_with_copilot_mock["base"]
    token = bterminal_with_copilot_mock["token"]

    # Step 1: open Copilot tab via the new endpoint.
    status, body = _http(
        base, token, "POST",
        "/api/tabs/ai/copilot",
        {"config_name": "MyCopilotProj"},
    )
    assert status == 200, body
    assert body["ok"] is True
    idx = body["idx"]
    assert isinstance(idx, int)

    # Step 2: tab listed with copilot provider + name in title.
    # Task #65 (2026-05-07): emoji prefix removed from title since
    # the SVG pixbuf renders the provider icon separately. Verify via
    # the new `provider` field in /api/tabs payload.
    status, tabs_payload = _http(base, token, "GET", "/api/tabs")
    assert status == 200
    matching = [t for t in tabs_payload["tabs"] if t["idx"] == idx]
    assert len(matching) == 1, tabs_payload
    title = matching[0]["title"]
    assert "MyCopilotProj" in title
    assert matching[0]["provider"] == "copilot", (
        f"Expected Copilot provider tag: {matching[0]}"
    )

    # Step 3: intro prompt was recorded by spawn_ai_cli's record_feed.
    # Allow a brief delay so the feed write completes before we GET.
    time.sleep(0.5)
    status, feed = _http(
        base, token, "GET", "/api/debug/feed_log?since=0",
    )
    assert status == 200
    intro_events = [e for e in feed["events"] if e["label"] == "intro_prompt"]
    assert intro_events, (
        f"Expected at least one 'intro_prompt' feed event; "
        f"got labels: {[e['label'] for e in feed['events']]}"
    )

    _stderr_clean_or_fail(bterminal_with_copilot_mock["stderr_path"])


def test_close_copilot_tab_no_crash(bterminal_with_copilot_mock):
    """Closing a Copilot tab via REST must not raise NameError /
    AttributeError in any GTK callback (smoke for T2.7 visual marker
    + T2.1 spawn dispatch)."""
    base = bterminal_with_copilot_mock["base"]
    token = bterminal_with_copilot_mock["token"]

    status, body = _http(
        base, token, "POST",
        "/api/tabs/ai/copilot",
        {"config_name": "MyCopilotProj"},
    )
    assert status == 200
    idx = body["idx"]

    # Close tab. Tabs with project_dir (every Copilot session here) get
    # task_project set → /close returns 409 unless ?force=true.
    status, _ = _http(
        base, token, "POST",
        f"/api/tabs/{idx}/close?force=true",
    )
    assert status == 200

    # Tab is gone from /api/tabs.
    status, tabs_payload = _http(base, token, "GET", "/api/tabs")
    assert status == 200
    remaining = [t for t in tabs_payload["tabs"] if t["idx"] == idx]
    assert remaining == []

    _stderr_clean_or_fail(bterminal_with_copilot_mock["stderr_path"])


def test_copilot_tab_has_provider_specific_marker(bterminal_with_copilot_mock):
    """Cross-check vs Claude: Copilot tab carries provider='copilot'.
    Task #65 (2026-05-07): visual marker moved from emoji-in-title to
    SVG pixbuf + new `provider` field in the /api/tabs payload."""
    base = bterminal_with_copilot_mock["base"]
    token = bterminal_with_copilot_mock["token"]

    status, body = _http(
        base, token, "POST",
        "/api/tabs/ai/copilot",
        {"config_name": "MyCopilotProj"},
    )
    assert status == 200

    status, tabs_payload = _http(base, token, "GET", "/api/tabs")
    ai_tabs = [t for t in tabs_payload["tabs"] if t.get("type") == "claude"]
    assert any(t.get("provider") == "copilot" for t in ai_tabs)
    # No Claude tabs were opened
    assert not any(t.get("provider") == "claude" for t in ai_tabs)
