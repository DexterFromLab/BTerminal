"""REST integration tests for /api/tabs/ai/{provider} (T2.8).

Spawns BTerminal as a subprocess with a HOME pre-seeded with two AI
sessions (claude + copilot) so the new provider-aware endpoint and
the legacy /api/tabs/claude alias can be exercised end-to-end.

The fixture mirrors test_per_tab_plugin_gating.py's pattern:
self-contained subprocess + ephemeral port, license pre-seeded via
the shared helper, sessions written directly into ai_sessions.json.
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

REPO_ROOT = Path(__file__).parent.parent


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
def bterminal_with_ai_sessions():
    """Spawn BTerminal with ai_sessions.json pre-seeded (claude + copilot)."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-rest-ai-")
    seed_license(home)

    # Seed ai_sessions.json with one Claude and one Copilot session.
    cfg_dir = Path(home) / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    ai_file = cfg_dir / "ai_sessions.json"
    ai_file.write_text(json.dumps([
        {
            "id": "claude-1",
            "name": "MyClaudeProj",
            "provider": "claude",
            "project_dir": "/tmp/myclaude",
            "color": "#89b4fa",
            "provider_options": {"resume": False, "skip_permissions": True},
        },
        {
            "id": "copilot-1",
            "name": "MyCopilotProj",
            "provider": "copilot",
            "project_dir": "/tmp/mycopilot",
            "color": "#a6e3a1",
            "provider_options": {"skip_permissions": True},
        },
        {
            # Same name as Claude session, different provider — for
            # the strict-match test (T2.8 requires provider+name match)
            "id": "copilot-2",
            "name": "Shared",
            "provider": "copilot",
            "project_dir": "/tmp/shared",
        },
        {
            "id": "claude-2",
            "name": "Shared",
            "provider": "claude",
            "project_dir": "/tmp/shared-claude",
        },
    ]))

    # Make sure project_dir paths exist so spawn doesn't fail
    for proj in ("/tmp/myclaude", "/tmp/mycopilot", "/tmp/shared",
                 "/tmp/shared-claude"):
        Path(proj).mkdir(parents=True, exist_ok=True)

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

    token = (Path(home) / ".config" / "bterminal" / "debug_token").read_text().strip()

    try:
        yield {"base": base, "token": token, "home": home, "proc": proc}
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


def _post(base, token, path, body):
    """Helper: POST JSON, return (status, body_dict)."""
    req = urllib.request.Request(
        f"{base}{path}",
        data=json.dumps(body).encode(),
        headers={
            "Authorization": f"Bearer {token}",
            "Content-Type": "application/json",
        },
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        try:
            payload = json.loads(exc.read())
        except (json.JSONDecodeError, OSError):
            payload = {"error": exc.reason}
        return exc.code, payload


# ─── New provider-aware endpoint ─────────────────────────────────────────────

def test_open_claude_via_new_endpoint(bterminal_with_ai_sessions):
    base = bterminal_with_ai_sessions["base"]
    token = bterminal_with_ai_sessions["token"]
    status, body = _post(
        base, token,
        "/api/tabs/ai/claude",
        {"config_name": "MyClaudeProj"},
    )
    assert status == 200
    assert body["ok"] is True
    assert isinstance(body["idx"], int)


def test_open_copilot_via_new_endpoint(bterminal_with_ai_sessions):
    base = bterminal_with_ai_sessions["base"]
    token = bterminal_with_ai_sessions["token"]
    status, body = _post(
        base, token,
        "/api/tabs/ai/copilot",
        {"config_name": "MyCopilotProj"},
    )
    assert status == 200
    assert body["ok"] is True


def test_new_endpoint_strict_provider_match(bterminal_with_ai_sessions):
    """Two sessions named "Shared" exist (claude + copilot). The new
    endpoint must match by BOTH name AND provider — asking for the
    Copilot one returns the Copilot session, not the Claude one."""
    base = bterminal_with_ai_sessions["base"]
    token = bterminal_with_ai_sessions["token"]
    status, body = _post(
        base, token,
        "/api/tabs/ai/copilot",
        {"config_name": "Shared"},
    )
    assert status == 200, body


def test_new_endpoint_unknown_provider_returns_404(bterminal_with_ai_sessions):
    base = bterminal_with_ai_sessions["base"]
    token = bterminal_with_ai_sessions["token"]
    status, body = _post(
        base, token,
        "/api/tabs/ai/totally-fake",
        {"config_name": "MyClaudeProj"},
    )
    assert status == 404
    assert "totally-fake" in body.get("error", "")


def test_new_endpoint_session_not_found_returns_404(bterminal_with_ai_sessions):
    base = bterminal_with_ai_sessions["base"]
    token = bterminal_with_ai_sessions["token"]
    status, body = _post(
        base, token,
        "/api/tabs/ai/claude",
        {"config_name": "DoesNotExist"},
    )
    assert status == 404


def test_new_endpoint_provider_mismatch_returns_404(bterminal_with_ai_sessions):
    """MyClaudeProj exists but as a Claude session — asking for it as
    a Copilot session must 404 (strict provider filter)."""
    base = bterminal_with_ai_sessions["base"]
    token = bterminal_with_ai_sessions["token"]
    status, body = _post(
        base, token,
        "/api/tabs/ai/copilot",
        {"config_name": "MyClaudeProj"},
    )
    assert status == 404


def test_new_endpoint_missing_config_name_returns_400(bterminal_with_ai_sessions):
    base = bterminal_with_ai_sessions["base"]
    token = bterminal_with_ai_sessions["token"]
    status, body = _post(base, token, "/api/tabs/ai/claude", {})
    assert status == 400


# ─── T4.6.1: legacy /api/tabs/claude removed ────────────────────────────────

def test_legacy_claude_endpoint_returns_404(bterminal_with_ai_sessions):
    """T4.6.1 (2026-05-07): the pre-T2.8 `/api/tabs/claude` route was
    removed. REST consumers must POST to `/api/tabs/ai/claude` instead.
    Hitting the legacy path returns 404 from the dispatcher."""
    base = bterminal_with_ai_sessions["base"]
    token = bterminal_with_ai_sessions["token"]
    status, _ = _post(
        base, token,
        "/api/tabs/claude",
        {"config_name": "MyClaudeProj"},
    )
    assert status == 404
