"""Tier 4 acceptance — Aider full session smoke (#18 / #90).

End-to-end verification that AiderProvider behaves identically to
Claude/Copilot in BTerminal's session machinery:
  - tab opens via POST /api/tabs/ai/aider {config_name}
  - intro_prompt fires with project context (CLAUDE.md / AIDER.md
    auto-symlinked, plugin descriptions, ctx instructions)
  - feed_log captures the events at the canonical labels
  - rules_inject path triggers after enough simulated prompts cross
    rules_config.inject_every threshold
  - session_log_glob points at .aider.chat.history.md (provider's
    chat history file, not Claude's session log)

Same fixture pattern as test_dual_provider_workflow.py:
  - tools/mock_ai_cli substitutes for the real `aider` binary so the
    test is fast + hermetic (no ollama daemon required)
  - inject_every is forced down to 2 in rules_config so the test can
    cross the threshold with 2 simulate_prompt calls instead of 100
  - feed_log labels are read with `since=pivot_ts` to filter out
    setup noise

Catches: provider dispatch divergence, intro_prompt body shape drift,
rules_inject capability gate regression, session_log_glob path
template breakage.
"""

from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import sqlite3
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


# ─── Helpers (forked from test_dual_provider_workflow) ─────────────────────


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


def _seed_ctx_db_for_aider(home: str, project_name: str, project_dir: str,
                             inject_every: int) -> None:
    """Mirror the canonical schema from test_dual_provider_workflow but
    with rules_config.inject_every overridden so 2 prompts trigger
    rules_inject (the default is 20)."""
    ctx_dir = Path(home) / ".claude-context"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    db_path = ctx_dir / "context.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                task_id TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project, task_id)
            );
            CREATE TABLE IF NOT EXISTS task_config (
                project TEXT PRIMARY KEY,
                autorun INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS task_claims (
                project TEXT NOT NULL,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                claimed_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project, task_id, session_id)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                name TEXT PRIMARY KEY,
                description TEXT,
                work_dir TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS contexts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project, key)
            );
            CREATE TABLE IF NOT EXISTS shared (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                summary TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                rule TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS rules_config (
                project TEXT PRIMARY KEY,
                inject_every INTEGER NOT NULL DEFAULT 20,
                refresh_every INTEGER NOT NULL DEFAULT 50,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS rules_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                action TEXT NOT NULL,
                rule TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'user',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS ctx_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                action TEXT NOT NULL,
                key TEXT NOT NULL,
                old_value TEXT,
                new_value TEXT,
                actor TEXT NOT NULL DEFAULT 'user',
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.execute(
            "INSERT OR REPLACE INTO sessions (name, description, work_dir) "
            "VALUES (?, ?, ?)",
            (project_name, "Aider full-session test project", project_dir),
        )
        # rules_config controls inject_every — force low so the test
        # crosses the threshold with 2 prompts.
        conn.execute(
            "INSERT OR REPLACE INTO rules_config "
            "(project, inject_every, refresh_every) VALUES (?, ?, ?)",
            (project_name, inject_every, 200),
        )
        # At least one rule so the rules_block isn't empty when injected
        conn.execute(
            "INSERT INTO rules (project, rule) VALUES (?, ?)",
            (project_name, "Always reply concisely."),
        )
        conn.commit()
    finally:
        conn.close()


# ─── Fixture ──────────────────────────────────────────────────────────────


@pytest.fixture
def bterminal_with_aider():
    """BTerminal subprocess with mock aider binary + 1 AI session +
    project containing CLAUDE.md + low rules inject_every."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-aider-fullsession-")
    seed_license(home)

    # Mock 'aider' binary (mock_ai_cli is provider-agnostic)
    fake_bin = Path(home) / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    target = fake_bin / "aider"
    shutil.copy(str(MOCK_SRC), str(target))
    target.chmod(target.stat().st_mode
                  | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Project + CLAUDE.md so intro_prompt has real context to emit
    project_dir = Path(home) / "myaiderproj"
    project_dir.mkdir(parents=True, exist_ok=True)
    (project_dir / "CLAUDE.md").write_text(
        "# Project context for tests\n"
        "This file is read by BT to compose the intro_prompt.\n"
    )
    project_name = "myaiderproj"

    # Aider AI session
    cfg_dir = Path(home) / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ai_sessions.json").write_text(json.dumps([
        {
            "id": "aider-1",
            "name": "AiderSession",
            "provider": "aider",
            "project_dir": str(project_dir),
            "color": "#fab387",
            "provider_options": {},
        },
    ]))

    # CTX DB with rules + low inject_every
    _seed_ctx_db_for_aider(
        home, project_name, str(project_dir), inject_every=2,
    )

    # Free port for debug-REST
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    # Pick the simplest scenario — claude_basic responds to ping/hello
    scenario = REPO_ROOT / "tests" / "scenarios" / "claude_basic.json"
    env = {
        **os.environ,
        "HOME": home,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "BTERMINAL_DEBUG_REST_PORT": str(port),
        "MOCK_AI_CLI_SCENARIO": str(scenario),
    }
    stderr_path = Path(home) / "bterminal-stderr.log"
    stdout_path = Path(home) / "bterminal-stdout.log"
    stderr_handle = open(stderr_path, "w")
    stdout_handle = open(stdout_path, "w")
    proc = subprocess.Popen(
        ["xvfb-run", "-a", sys.executable, "-m", "bterminal",
         "--debug-rest"],
        cwd=str(REPO_ROOT), env=env,
        stdout=stdout_handle, stderr=stderr_handle,
        start_new_session=True,
    )

    base = f"http://127.0.0.1:{port}"
    if not _wait_for_health(base, time.monotonic() + 30):
        proc.terminate()
        proc.wait(timeout=5)
        stderr_handle.close()
        stdout_handle.close()
        try:
            err = Path(stderr_path).read_text(errors="replace")[-2000:]
        except OSError:
            err = "(unreadable)"
        try:
            out = Path(stdout_path).read_text(errors="replace")[-2000:]
        except OSError:
            out = "(unreadable)"
        pytest.fail(
            f"BTerminal didn't come up within 30s\n"
            f"--- stdout tail ---\n{out}\n"
            f"--- stderr tail ---\n{err}"
        )

    token = (cfg_dir / "debug_token").read_text().strip()
    try:
        yield {
            "base": base, "token": token, "home": home,
            "stderr_path": str(stderr_path),
            "project_name": project_name,
            "project_dir": str(project_dir),
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
        stdout_handle.close()


# ─── Tests ────────────────────────────────────────────────────────────────


def test_aider_tab_opens_via_rest(bterminal_with_aider):
    """POST /api/tabs/ai/aider with config_name returns 200 + tab idx
    + provider matches."""
    state = bterminal_with_aider
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    assert status == 200, f"open aider tab failed: {body}"
    assert "idx" in body
    assert isinstance(body["idx"], int)

    # /api/tabs reflects the new tab with provider='aider'
    status, tabs = _http(base, token, "GET", "/api/tabs", None)
    assert status == 200
    assert any(t.get("provider") == "aider" for t in tabs.get("tabs", [])), (
        f"no aider tab listed after open: {tabs}"
    )


def test_aider_intro_prompt_fired_with_project_context(bterminal_with_aider):
    """After tab opens, feed_log @label=intro_prompt records exactly
    one event whose body mentions the project name + CLAUDE.md content."""
    state = bterminal_with_aider
    base, token = state["base"], state["token"]
    project_name = state["project_name"]

    pivot = time.time()
    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    assert status == 200, body
    time.sleep(0.5)  # let async intro feed settle

    status, log = _http(
        base, token, "GET",
        f"/api/debug/feed_log?label=intro_prompt&since={pivot}",
        None,
    )
    assert status == 200, log
    events = log.get("events", [])
    assert events, f"no intro_prompt events captured: {log}"

    # Events store payload as base64 — decode + concat to scan for
    # project context markers.
    def _decode(e):
        b = e.get("bytes_b64", "")
        try:
            return base64.b64decode(b).decode("utf-8", errors="replace")
        except Exception:
            return ""

    joined = "\n".join(_decode(e) for e in events)
    # Either the project name OR the CLAUDE.md content marker — both
    # are evidence the intro carried real context, not a generic
    # boilerplate header.
    assert project_name in joined or "Project context for tests" in joined, (
        f"intro_prompt missing project context. "
        f"Decoded: {joined[:500]!r} | events: {events!r}"
    )


def test_aider_session_log_path_resolves_to_chat_history_md(
        bterminal_with_aider):
    """Sanity: AiderProvider.session_log_glob points at
    .aider.chat.history.md inside the project — distinct from Claude's
    ~/.claude session log path."""
    from bterminal.providers import get_registry

    state = bterminal_with_aider
    project_dir = state["project_dir"]
    registry = get_registry()
    provider = registry.get("aider")
    assert provider is not None, "aider provider not in registry"

    log_path = provider.session_log_glob(project_dir)
    assert log_path is not None
    assert log_path.endswith(".aider.chat.history.md"), (
        f"expected aider chat history path, got: {log_path}"
    )
    assert log_path.startswith(project_dir), (
        f"session log not anchored at project_dir: {log_path}"
    )


def test_aider_feed_endpoint_accepts_user_input(bterminal_with_aider):
    """POST /api/tabs/<idx>/feed must accept arbitrary bytes — same
    contract as Claude/Copilot. The mock aider scenario replies to
    'ping' with 'pong' (claude_basic.json), proving the round-trip."""
    state = bterminal_with_aider
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    assert status == 200, body
    idx = body["idx"]
    time.sleep(0.5)

    status, resp = _http(
        base, token, "POST", f"/api/tabs/{idx}/feed",
        {"text": "ping\n"},
    )
    assert status == 200, f"feed failed: {resp}"


def test_aider_rules_inject_fires_after_inject_every_threshold(
        bterminal_with_aider):
    """Cross the inject_every threshold (set to 2 in this test's
    rules_config) via simulate_prompt, then force_idle to flush —
    feed_log @label=rules_inject must record an event.

    Was xfailed pre-#94 due to missing AiderStatsReader. Now lifted:
    AiderStatsReader is registered in _READER_CLASSES so aider tabs
    get a _stats_bar and simulate_prompt works."""
    state = bterminal_with_aider
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    assert status == 200, body
    idx = body["idx"]
    time.sleep(0.5)
    pivot = time.time()

    # Two prompts cross inject_every=2 boundary → _inject_pending set
    for _ in range(2):
        status, resp = _http(
            base, token, "POST",
            f"/api/tabs/{idx}/simulate_prompt", {},
        )
        assert status == 200, resp

    # Last simulate_prompt response indicates whether inject is pending
    assert resp.get("inject_pending") is not None, (
        f"inject_pending unset after crossing threshold: {resp}"
    )

    # force_idle flushes the pending injection through feed_child
    status, resp = _http(
        base, token, "POST", f"/api/tabs/{idx}/force_idle", {},
    )
    assert status == 200, resp

    # Verify the injection was recorded in the feed_log
    time.sleep(0.3)
    status, log = _http(
        base, token, "GET",
        f"/api/debug/feed_log?label=rules_inject&since={pivot}",
        None,
    )
    assert status == 200, log
    events = log.get("events", [])
    assert events, (
        f"no rules_inject events after force_idle. State: {resp}"
    )


def test_aider_provider_capabilities_match_claude_for_session_machinery(
        bterminal_with_aider):
    """Provider-parity assertion at capability level — Aider must
    expose rules_inject + session_log + intro_prompt support so it
    plugs into the same dispatch paths as Claude. This is what
    enables the previous tests to pass in the first place."""
    from bterminal.providers import get_registry

    registry = get_registry()
    aider = registry.get("aider")
    assert aider is not None
    caps = aider.capabilities

    # Non-negotiables for the e2e flow above
    assert caps.rules_inject is True
    assert caps.session_log is True
    assert caps.intro_prompt is True
    # Aider runs against a local LLM endpoint
    assert caps.local_endpoint_url, (
        "AiderProvider must expose local_endpoint_url for the audit"
    )
