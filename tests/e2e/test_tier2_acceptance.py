"""Tier 2 acceptance — Claude and Copilot tabs coexist (T2.12).

Spawns BTerminal with both providers mocked and verifies the
end-to-end story Tier 2 was meant to deliver:

  * AI provider dropdown reaches both Claude and Copilot (verified
    transitively — opening sessions for both succeeds).
  * Visual marker (R7a / T2.7): each tab gets its provider's emoji
    (✨ for Claude, 🤖 for Copilot).
  * Provider-aware spawn (T2.1 / T2.3) routes to the correct argv —
    Copilot intro prompt is recorded by record_feed just like Claude's.
  * REST endpoint /api/tabs/ai/{provider} (T2.8) opens both providers.
  * Both tabs survive parallel close + ai_sessions.json keeps both
    entries on disk through the lifecycle.

This is the automated stand-in for the manual smoke step in the
implementation plan ("open BTerminal, add a Copilot session, switch
provider in dialog, verify mock responds"). Manual verification still
happens during Tier 4 polish; this test catches regressions in CI.
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
CLAUDE_SCENARIO = REPO_ROOT / "tests" / "scenarios" / "claude_basic.json"


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


def _make_executable(path):
    path.write_text("#!/bin/sh\necho fake\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def bterminal_dual_provider():
    """Spawn BTerminal with mocked `claude` AND `copilot` binaries on
    PATH plus an ai_sessions.json holding one session per provider."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-tier2-")
    seed_license(home)

    # Stage 1: drop mock binaries for BOTH providers in a fake bin dir.
    fake_bin = Path(home) / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    for name in ("claude", "copilot"):
        target = fake_bin / name
        shutil.copy(str(MOCK_SRC), str(target))
        target.chmod(
            target.stat().st_mode
            | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH
        )

    # Stage 2: pre-seed ai_sessions.json with one Claude + one Copilot.
    cfg_dir = Path(home) / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    claude_dir = Path(home) / "claude-proj"
    copilot_dir = Path(home) / "copilot-proj"
    claude_dir.mkdir(parents=True, exist_ok=True)
    copilot_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ai_sessions.json").write_text(json.dumps([
        {
            "id": "claude-1",
            "name": "MyClaudeSession",
            "provider": "claude",
            "project_dir": str(claude_dir),
            "color": "#89b4fa",
            "provider_options": {
                "resume": False,
                "skip_permissions": True,
                "sudo": False,
            },
        },
        {
            "id": "copilot-1",
            "name": "MyCopilotSession",
            "provider": "copilot",
            "project_dir": str(copilot_dir),
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
        # Pick claude scenario — both mock binaries read the same file
        # since each invocation passes only one MOCK_AI_CLI_SCENARIO.
        "MOCK_AI_CLI_SCENARIO": str(CLAUDE_SCENARIO),
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
            "ai_sessions_file": str(cfg_dir / "ai_sessions.json"),
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
    bad_patterns = ["NameError", "AttributeError", "ImportError",
                    "Traceback (most recent call last)"]
    bad = [p for p in bad_patterns if p in text]
    if bad:
        pytest.fail(f"BTerminal stderr contained {bad}: {text[:2000]}")


# ─── Acceptance scenarios ────────────────────────────────────────────────────

def test_claude_and_copilot_tabs_coexist(bterminal_dual_provider):
    """Open Claude session → open Copilot session → assert both tabs
    visible with their respective provider emoji."""
    base = bterminal_dual_provider["base"]
    token = bterminal_dual_provider["token"]

    # Open Claude tab
    status, body = _http(
        base, token, "POST",
        "/api/tabs/ai/claude",
        {"config_name": "MyClaudeSession"},
    )
    assert status == 200, body
    claude_idx = body["idx"]

    # Open Copilot tab
    status, body = _http(
        base, token, "POST",
        "/api/tabs/ai/copilot",
        {"config_name": "MyCopilotSession"},
    )
    assert status == 200, body
    copilot_idx = body["idx"]

    # Both tabs in the listing with the right provider tag (task #65:
    # emoji removed from title, provider explicit in payload).
    status, tabs_payload = _http(base, token, "GET", "/api/tabs")
    assert status == 200
    by_idx = {t["idx"]: t for t in tabs_payload["tabs"]}
    assert claude_idx in by_idx
    assert copilot_idx in by_idx
    assert by_idx[claude_idx].get("provider") == "claude"
    assert "MyClaudeSession" in by_idx[claude_idx]["title"]
    assert by_idx[copilot_idx].get("provider") == "copilot"
    assert "MyCopilotSession" in by_idx[copilot_idx]["title"]

    _stderr_clean_or_fail(bterminal_dual_provider["stderr_path"])


def test_intro_prompts_recorded_for_both_providers(bterminal_dual_provider):
    """spawn_ai_cli's record_feed("intro_prompt", ...) fires for both
    providers — the captured stream should hold ≥2 events."""
    base = bterminal_dual_provider["base"]
    token = bterminal_dual_provider["token"]

    _http(base, token, "POST", "/api/tabs/ai/claude",
          {"config_name": "MyClaudeSession"})
    _http(base, token, "POST", "/api/tabs/ai/copilot",
          {"config_name": "MyCopilotSession"})

    time.sleep(0.5)
    status, feed = _http(base, token, "GET", "/api/debug/feed_log?since=0")
    assert status == 200
    intro_events = [e for e in feed["events"] if e["label"] == "intro_prompt"]
    assert len(intro_events) >= 2, (
        f"Expected ≥2 intro_prompt events for two AI tabs; got "
        f"{len(intro_events)}: {feed['events']}"
    )


def test_ai_sessions_json_persists_both_providers(bterminal_dual_provider):
    """Through the lifecycle, ai_sessions.json keeps both entries —
    no T1.6/T1.7 migration accidentally drops one provider's record."""
    base = bterminal_dual_provider["base"]
    token = bterminal_dual_provider["token"]

    # Open both tabs to ensure the session manager has fully loaded
    _http(base, token, "POST", "/api/tabs/ai/claude",
          {"config_name": "MyClaudeSession"})
    _http(base, token, "POST", "/api/tabs/ai/copilot",
          {"config_name": "MyCopilotSession"})

    sessions = json.loads(
        Path(bterminal_dual_provider["ai_sessions_file"]).read_text()
    )
    providers = {s["provider"] for s in sessions}
    assert providers == {"claude", "copilot"}
    names = {s["name"] for s in sessions}
    assert names == {"MyClaudeSession", "MyCopilotSession"}


def test_close_both_tabs_no_crash(bterminal_dual_provider):
    """Closing tabs in reverse order (Copilot first, then Claude) —
    no GTK callback crashes via stderr."""
    base = bterminal_dual_provider["base"]
    token = bterminal_dual_provider["token"]

    # Open both
    _, claude_body = _http(base, token, "POST", "/api/tabs/ai/claude",
                            {"config_name": "MyClaudeSession"})
    _, copilot_body = _http(base, token, "POST", "/api/tabs/ai/copilot",
                             {"config_name": "MyCopilotSession"})
    claude_idx = claude_body["idx"]
    copilot_idx = copilot_body["idx"]

    # Close Copilot first (force=true: tab has task_project)
    status, _ = _http(base, token, "POST",
                       f"/api/tabs/{copilot_idx}/close?force=true")
    assert status == 200
    # Close Claude
    status, _ = _http(base, token, "POST",
                       f"/api/tabs/{claude_idx}/close?force=true")
    assert status == 200

    # Both gone
    status, tabs_payload = _http(base, token, "GET", "/api/tabs")
    remaining = [t["idx"] for t in tabs_payload["tabs"]]
    assert claude_idx not in remaining
    assert copilot_idx not in remaining

    _stderr_clean_or_fail(bterminal_dual_provider["stderr_path"])


def test_legacy_endpoint_returns_404_after_t4_6_1(bterminal_dual_provider):
    """T4.6.1 (2026-05-07): the legacy `/api/tabs/claude` route was
    removed. New endpoint `/api/tabs/ai/{provider}` is the canonical
    way to open AI tabs. Old REST consumers will need to migrate."""
    base = bterminal_dual_provider["base"]
    token = bterminal_dual_provider["token"]

    # Legacy endpoint now 404s
    status, _ = _http(base, token, "POST", "/api/tabs/claude",
                       {"config_name": "MyClaudeSession"})
    assert status == 404

    # New endpoint still works
    status, body = _http(base, token, "POST", "/api/tabs/ai/claude",
                          {"config_name": "MyClaudeSession"})
    assert status == 200
    assert body["ok"] is True

    _stderr_clean_or_fail(bterminal_dual_provider["stderr_path"])
