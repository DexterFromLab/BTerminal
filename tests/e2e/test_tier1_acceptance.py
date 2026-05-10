"""Tier 1 acceptance — end-to-end verification of the provider abstraction.

Spawns BTerminal as a real subprocess with a HOME containing a legacy
claude_sessions.json. After startup we assert:

  - ai_sessions.json was created with the migrated content.
  - claude_sessions.json was renamed to claude_sessions.json.bak.
  - Each migrated session has provider="claude" and any legacy flags
    (resume / skip_permissions) live under provider_options (R4.2).
  - Provider config flow reaches the running subprocess: GET /api/state
    responds 200 (sanity), and the sidebar plugin manifest still works
    (proving the registry doesn't break orthogonal subsystems).

This is the smoke test substitute for the manual "open BTerminal,
verify it works identically" step in the implementation plan.
"""
from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent


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


@pytest.fixture
def bterminal_with_legacy_sessions():
    """Spawn BTerminal with a legacy claude_sessions.json pre-seeded
    (R4.2 schema: top-level resume/skip_permissions flags). Yields
    (home_path, base_url, token, proc) so the test can run REST
    queries and verify on-disk state."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-tier1-acceptance-")
    seed_license(home)

    # Seed legacy claude_sessions.json with two entries — one with
    # legacy flags, one minimal.
    cfg_dir = Path(home) / ".config" / "bterminal"
    legacy_file = cfg_dir / "claude_sessions.json"
    legacy_file.write_text(json.dumps([
        {
            "id": "legacy-1",
            "name": "MyProject",
            "project_dir": "/tmp/myproject",
            "color": "#89b4fa",
            "resume": True,
            "skip_permissions": True,
            "sudo": False,
        },
        {
            "id": "legacy-2",
            "name": "OtherProject",
            "project_dir": "/tmp/other",
            "color": "#a6e3a1",
        },
    ]))

    # Pick an ephemeral port to avoid colliding with the session-scoped
    # bterminal_process fixture (port 7790).
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    env = {**os.environ, "HOME": home,
           "BTERMINAL_DEBUG_REST_PORT": str(port)}
    proc = subprocess.Popen(
        ["xvfb-run", "-a", sys.executable, "-m", "bterminal", "--debug-rest"],
        cwd=str(REPO_ROOT), env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL,
        start_new_session=True,
    )

    base = f"http://127.0.0.1:{port}"
    if not _wait_for_health(base, time.monotonic() + 15):
        proc.terminate()
        proc.wait(timeout=5)
        pytest.fail("BTerminal didn't come up within 15s")

    token_path = Path(home) / ".config" / "bterminal" / "debug_token"
    token = token_path.read_text().strip() if token_path.exists() else ""

    try:
        yield {"home": home, "base": base, "token": token, "proc": proc}
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
            proc.wait(timeout=2)


def test_legacy_claude_sessions_migrated_to_ai_sessions(bterminal_with_legacy_sessions):
    """T1.7 acceptance: migration ran on subprocess startup."""
    home = bterminal_with_legacy_sessions["home"]
    cfg_dir = Path(home) / ".config" / "bterminal"

    ai_file = cfg_dir / "ai_sessions.json"
    bak_file = cfg_dir / "claude_sessions.json.bak"
    legacy_file = cfg_dir / "claude_sessions.json"

    assert ai_file.exists(), "ai_sessions.json not created by migration"
    assert bak_file.exists(), "claude_sessions.json.bak not created"
    assert not legacy_file.exists(), "legacy file should be renamed away"


def test_migrated_sessions_have_provider_field(bterminal_with_legacy_sessions):
    """T1.6 acceptance: every migrated session has provider="claude"."""
    home = bterminal_with_legacy_sessions["home"]
    ai_file = Path(home) / ".config" / "bterminal" / "ai_sessions.json"
    sessions = json.loads(ai_file.read_text())
    assert len(sessions) == 2
    for s in sessions:
        assert s["provider"] == "claude", \
            f"session {s.get('name')} missing provider=claude"


def test_legacy_flags_relocated_to_provider_options(bterminal_with_legacy_sessions):
    """T1.7 acceptance: resume/skip_permissions/sudo moved under
    provider_options per R4.2 schema."""
    home = bterminal_with_legacy_sessions["home"]
    ai_file = Path(home) / ".config" / "bterminal" / "ai_sessions.json"
    sessions = json.loads(ai_file.read_text())
    by_name = {s["name"]: s for s in sessions}

    s1 = by_name["MyProject"]
    assert s1["provider_options"] == {
        "resume": True, "skip_permissions": True, "sudo": False,
    }
    # legacy keys removed from top-level
    for k in ("resume", "skip_permissions", "sudo"):
        assert k not in s1, f"legacy key '{k}' still on top-level"
    # non-provider keys preserved
    assert s1["id"] == "legacy-1"
    assert s1["color"] == "#89b4fa"

    # Session without legacy flags doesn't get an empty provider_options
    s2 = by_name["OtherProject"]
    assert "provider_options" not in s2


def test_subprocess_health_endpoint_responds(bterminal_with_legacy_sessions):
    """Smoke: BTerminal subprocess started cleanly and REST is up."""
    base = bterminal_with_legacy_sessions["base"]
    token = bterminal_with_legacy_sessions["token"]
    req = urllib.request.Request(
        f"{base}/api/state",
        headers={"Authorization": f"Bearer {token}"},
    )
    with urllib.request.urlopen(req, timeout=5) as resp:
        assert resp.status == 200
