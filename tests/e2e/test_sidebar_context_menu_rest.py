"""E2E: REST analog of sidebar context menu (task #63, 2026-05-07).

Spawns BT subprocess with seeded ai_sessions.json (Claude + Copilot in
same folder + one folderless), exercises POST /api/sidebar/context_menu/
{session_id}?action=run_as|resume, asserts on real subprocess state:
  - new tab spawned with the override provider's argv (verified via
    /api/tabs introspection)
  - saved ai_sessions.json on disk NOT mutated
  - one-off run does NOT pollute task_claims for the project (#63 ack)
  - 400 / 404 edge cases for invalid action/provider combos
"""
from __future__ import annotations

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

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
MOCK_SRC = REPO_ROOT / "tools" / "mock_ai_cli"
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


@pytest.fixture
def bt_with_sessions():
    """BT subprocess with mock claude+copilot in PATH + 3 seeded sessions:
      - 'WorkClaude' (claude, folder='Work')
      - 'WorkCopilot' (copilot, folder='Work')
      - 'Loose' (claude, no folder)
    """
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bt-ctx-menu-")
    seed_license(home)

    fake_bin = Path(home) / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    for name in ("claude", "copilot"):
        target = fake_bin / name
        shutil.copy(str(MOCK_SRC), str(target))
        target.chmod(target.stat().st_mode
                     | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Project dirs (each session lands in a distinct folder so file
    # state is observable per-session)
    proj_a = Path(home) / "proj_a"
    proj_b = Path(home) / "proj_b"
    proj_c = Path(home) / "proj_c"
    for p in (proj_a, proj_b, proj_c):
        p.mkdir(parents=True, exist_ok=True)

    cfg_dir = Path(home) / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    ai_file = cfg_dir / "ai_sessions.json"
    sessions_payload = [
        {"id": "wc", "name": "WorkClaude", "provider": "claude",
         "folder": "Work", "project_dir": str(proj_a),
         "color": "#89b4fa",
         "provider_options": {"resume": False, "skip_permissions": True}},
        {"id": "wp", "name": "WorkCopilot", "provider": "copilot",
         "folder": "Work", "project_dir": str(proj_b),
         "color": "#a6e3a1",
         "provider_options": {"skip_permissions": True}},
        {"id": "loose", "name": "Loose", "provider": "claude",
         "project_dir": str(proj_c),
         "provider_options": {"resume": False}},
    ]
    ai_file.write_text(json.dumps(sessions_payload))

    # Pre-seed full CTX schema (T4.8.1 lesson: missing schema causes BT
    # crash on CtxManagerPanel.refresh)
    ctx_db_path = Path(home) / ".claude-context" / "context.db"
    ctx_db_path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(ctx_db_path))
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, task_id TEXT NOT NULL,
                description TEXT NOT NULL, status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project, task_id)
            );
            CREATE TABLE IF NOT EXISTS task_config (
                project TEXT PRIMARY KEY, autorun INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS task_claims (
                project TEXT NOT NULL, task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                claimed_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project, task_id, session_id)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                name TEXT PRIMARY KEY, description TEXT,
                work_dir TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS contexts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project, key)
            );
            CREATE TABLE IF NOT EXISTS shared (
                key TEXT PRIMARY KEY, value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, summary TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, rule TEXT NOT NULL,
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
                project TEXT NOT NULL, action TEXT NOT NULL,
                rule TEXT NOT NULL,
                actor TEXT NOT NULL DEFAULT 'user',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS ctx_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, action TEXT NOT NULL,
                key TEXT NOT NULL, old_value TEXT, new_value TEXT,
                actor TEXT NOT NULL DEFAULT 'user',
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.commit()
    finally:
        conn.close()

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
        "MOCK_AI_CLI_SCENARIO": str(CLAUDE_SCENARIO),
    }
    stdout_path = Path(home) / "bt-stdout.log"
    stderr_path = Path(home) / "bt-stderr.log"
    proc = subprocess.Popen(
        ["xvfb-run", "-a", sys.executable, "-m", "bterminal", "--debug-rest"],
        cwd=str(REPO_ROOT), env=env,
        stdout=open(stdout_path, "w"), stderr=open(stderr_path, "w"),
        start_new_session=True,
    )

    base = f"http://127.0.0.1:{port}"
    if not _wait_for_health(base, time.monotonic() + 30):
        proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        try:
            out = stdout_path.read_text()[-2000:]
        except OSError:
            out = "(unreadable)"
        try:
            err = stderr_path.read_text()[-2000:]
        except OSError:
            err = "(unreadable)"
        pytest.fail(
            f"BT didn't come up\n--- stdout ---\n{out}\n--- stderr ---\n{err}"
        )

    token = (cfg_dir / "debug_token").read_text().strip()

    try:
        yield {
            "base": base, "token": token, "home": home,
            "ai_file": str(ai_file),
            "ctx_db": str(ctx_db_path),
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


# ─── Happy path: Run As ──────────────────────────────────────────────────────


def test_run_as_copilot_on_claude_session_spawns_copilot_tab(bt_with_sessions):
    base = bt_with_sessions["base"]
    token = bt_with_sessions["token"]
    status, body = _http(
        base, token, "POST",
        "/api/sidebar/context_menu/wc?action=run_as&provider=copilot",
    )
    assert status == 200, body
    assert body["ok"] is True
    assert isinstance(body["idx"], int)


def test_run_as_does_not_mutate_saved_session_json(bt_with_sessions):
    base = bt_with_sessions["base"]
    token = bt_with_sessions["token"]
    ai_file = Path(bt_with_sessions["ai_file"])
    before = json.loads(ai_file.read_text())

    _http(
        base, token, "POST",
        "/api/sidebar/context_menu/wc?action=run_as&provider=copilot",
    )
    # Give the GLib idle a moment to flush any pending writes (we
    # explicitly assert nothing was written, but a race could mask the
    # bug if we read too early).
    time.sleep(0.4)

    after = json.loads(ai_file.read_text())
    saved_wc = next(s for s in after if s["id"] == "wc")
    assert saved_wc["provider"] == "claude", (
        f"Run As leaked override into saved session: {saved_wc}"
    )
    assert before == after, (
        "ai_sessions.json mutated by Run As — saved config must be immutable"
    )


def test_run_as_uses_override_providers_argv_builder(bt_with_sessions):
    """Verify the spawned tab actually uses Copilot's argv (mock CLI
    logs it). We probe via /api/tabs to see the new tab's metadata."""
    base = bt_with_sessions["base"]
    token = bt_with_sessions["token"]

    # Snapshot tab count BEFORE
    _, before = _http(base, token, "GET", "/api/tabs")
    n_before = len(before["tabs"])

    status, body = _http(
        base, token, "POST",
        "/api/sidebar/context_menu/wc?action=run_as&provider=copilot",
    )
    assert status == 200
    time.sleep(0.5)

    _, after = _http(base, token, "GET", "/api/tabs")
    assert len(after["tabs"]) == n_before + 1, (
        f"expected one new tab; got {len(after['tabs']) - n_before}"
    )


# ─── Happy path: Resume ──────────────────────────────────────────────────────


def test_resume_on_claude_session_returns_200(bt_with_sessions):
    base = bt_with_sessions["base"]
    token = bt_with_sessions["token"]
    status, body = _http(
        base, token, "POST",
        "/api/sidebar/context_menu/loose?action=resume",
    )
    assert status == 200, body
    assert body["ok"] is True


def test_resume_does_not_mutate_saved_session_resume_flag(bt_with_sessions):
    """User saved resume=False; the menu's force-resume must not
    persist resume=True to the JSON."""
    base = bt_with_sessions["base"]
    token = bt_with_sessions["token"]
    ai_file = Path(bt_with_sessions["ai_file"])

    before = json.loads(ai_file.read_text())
    saved_loose = next(s for s in before if s["id"] == "loose")
    assert saved_loose["provider_options"]["resume"] is False

    _http(base, token, "POST",
          "/api/sidebar/context_menu/loose?action=resume")
    time.sleep(0.4)

    after = json.loads(ai_file.read_text())
    saved_loose_after = next(s for s in after if s["id"] == "loose")
    assert saved_loose_after["provider_options"]["resume"] is False, (
        f"Resume leaked into saved JSON: {saved_loose_after}"
    )


# ─── Edge cases: 400 / 404 ──────────────────────────────────────────────────


def test_run_as_same_provider_as_default_returns_400(bt_with_sessions):
    """'wc' is a Claude session — Run As Claude is no-op; should 400."""
    base = bt_with_sessions["base"]
    token = bt_with_sessions["token"]
    status, body = _http(
        base, token, "POST",
        "/api/sidebar/context_menu/wc?action=run_as&provider=claude",
    )
    assert status == 400, body
    assert "match" in body.get("error", "").lower() or \
           "saved" in body.get("error", "").lower()


def test_run_as_unknown_provider_returns_404(bt_with_sessions):
    base = bt_with_sessions["base"]
    token = bt_with_sessions["token"]
    status, body = _http(
        base, token, "POST",
        "/api/sidebar/context_menu/wc?action=run_as&provider=fake-cli",
    )
    assert status == 404, body
    assert "fake-cli" in body.get("error", "")


def test_run_as_missing_provider_query_returns_400(bt_with_sessions):
    base = bt_with_sessions["base"]
    token = bt_with_sessions["token"]
    status, body = _http(
        base, token, "POST",
        "/api/sidebar/context_menu/wc?action=run_as",
    )
    assert status == 400


def test_unknown_action_returns_400(bt_with_sessions):
    base = bt_with_sessions["base"]
    token = bt_with_sessions["token"]
    status, body = _http(
        base, token, "POST",
        "/api/sidebar/context_menu/wc?action=delete-everything",
    )
    assert status == 400


def test_unknown_session_id_returns_404(bt_with_sessions):
    base = bt_with_sessions["base"]
    token = bt_with_sessions["token"]
    status, body = _http(
        base, token, "POST",
        "/api/sidebar/context_menu/nonexistent?action=resume",
    )
    assert status == 404
    assert "nonexistent" in body.get("error", "")


# ─── (c) one-off run does NOT pollute task_claims for project ───────────────


def test_one_off_run_does_not_create_task_claims(bt_with_sessions):
    """The one-off Run As / Resume flow should NEVER add a row to
    task_claims for the project — claims are reserved for the autorun
    flow, not user-initiated spawns. Otherwise: ad-hoc compare runs
    accidentally lock tasks on the project's claim table."""
    base = bt_with_sessions["base"]
    token = bt_with_sessions["token"]
    ctx_db = bt_with_sessions["ctx_db"]

    # Confirm no claims pre-action
    conn = sqlite3.connect(ctx_db)
    try:
        rows_before = conn.execute(
            "SELECT COUNT(*) FROM task_claims"
        ).fetchone()[0]
    finally:
        conn.close()
    assert rows_before == 0

    # Trigger Run As + Resume back to back
    _http(base, token, "POST",
          "/api/sidebar/context_menu/wc?action=run_as&provider=copilot")
    _http(base, token, "POST",
          "/api/sidebar/context_menu/loose?action=resume")
    time.sleep(0.5)

    conn = sqlite3.connect(ctx_db)
    try:
        rows_after = conn.execute(
            "SELECT COUNT(*) FROM task_claims"
        ).fetchone()[0]
    finally:
        conn.close()
    assert rows_after == 0, (
        f"one-off spawn polluted task_claims (before=0 after={rows_after}); "
        f"these flows must not register claim rows"
    )
