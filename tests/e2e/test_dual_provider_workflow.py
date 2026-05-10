"""Tier 4 acceptance — dual-provider auto-trigger workflow (T4.8).

End-to-end verification of the full Tier 4 promise: a Claude tab and
a Copilot tab can coexist in the same BTerminal instance, both
participate in the auto-trigger flow, and each picks up its own task
atomically. Replaces the manual smoke step from the implementation
plan ("add task, wait 11s, both tabs receive [AUTO-TRIGGER]") — uses
`/api/tabs/<idx>/force_idle` so the test runs in seconds instead of
real wall clock.

T4.8.1 (2026-05-07): originally pytest.mark.skip'ed because BT
startup appeared to hang (30s timeout, "alive but no REST"). Root
cause turned out NOT to be the suspected pytest/xvfb-run/process-group
interaction — it was that the fixture's _seed_ctx_db pre-seeded an
incomplete CTX schema using the legacy `sessions.project_dir` column,
while CtxManagerPanel.refresh() now reads `sessions.work_dir` AND
queries the `contexts` table at startup. BT was crashing with
sqlite3.OperationalError, but the fixture had stdout=DEVNULL so the
traceback was invisible — only "process exited" was observable, which
looked like a startup race. Fix: complete CTX schema (matches the
canonical `ctx` CLI's init_db) + capture stdout into a log so future
crashes surface in pytest output.
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
CLAUDE_SCENARIO = REPO_ROOT / "tests" / "scenarios" / "claude_basic.json"
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


def _seed_ctx_db(home, project_name, project_dir, task_ids):
    """Build a CTX SQLite DB with autorun=1 and N open tasks for project.

    BTerminal's _on_task_idle_timeout reads ~/.claude-context/context.db
    via CTX_DB const. With HOME pre-pointed at our isolated tmpdir,
    the CTX DB lives at <home>/.claude-context/context.db.

    T4.8.1: schema must mirror the canonical `ctx` CLI's init_db
    (sessions.work_dir not project_dir; contexts/shared/summaries/rules*
    tables present). CtxManagerPanel.refresh() runs at app startup and
    reads from both `sessions` and `contexts`; an incomplete schema
    causes a sqlite3.OperationalError before debug-REST binds.
    """
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
        # Register the project so _resolve_ctx_project_name returns
        # `project_name` for `project_dir`.
        conn.execute(
            "INSERT OR REPLACE INTO sessions (name, description, work_dir) "
            "VALUES (?, ?, ?)",
            (project_name, "T4.8 acceptance test project", project_dir),
        )
        # Enable autorun
        conn.execute(
            "INSERT OR REPLACE INTO task_config (project, autorun) "
            "VALUES (?, 1)",
            (project_name,),
        )
        # Seed open tasks
        for tid in task_ids:
            conn.execute(
                "INSERT INTO tasks (project, task_id, description, status) "
                "VALUES (?, ?, ?, 'open')",
                (project_name, tid, f"E2E task {tid}"),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def bterminal_dual_with_tasks():
    """BTerminal subprocess with both mock providers + 2 AI sessions
    sharing one project + 2 unclaimed tasks + autorun=1."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-tier4-")
    seed_license(home)

    # Mock binaries for both providers
    fake_bin = Path(home) / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    for name in ("claude", "copilot"):
        target = fake_bin / name
        shutil.copy(str(MOCK_SRC), str(target))
        target.chmod(target.stat().st_mode
                      | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Project dir + AI sessions: one Claude + one Copilot, same project.
    project_dir = Path(home) / "myproj"
    project_dir.mkdir(parents=True, exist_ok=True)
    project_name = "myproj"

    cfg_dir = Path(home) / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ai_sessions.json").write_text(json.dumps([
        {
            "id": "claude-1",
            "name": "ClaudeSession",
            "provider": "claude",
            "project_dir": str(project_dir),
            "color": "#89b4fa",
            "provider_options": {"skip_permissions": True},
        },
        {
            "id": "copilot-1",
            "name": "CopilotSession",
            "provider": "copilot",
            "project_dir": str(project_dir),
            "color": "#a6e3a1",
            "provider_options": {"skip_permissions": True},
        },
    ]))

    # CTX DB: 2 unclaimed tasks + autorun=1
    _seed_ctx_db(
        home, project_name, str(project_dir),
        task_ids=["t-claude", "t-copilot"],
    )

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
        # T4.8.1: surface BOTH streams. Python tracebacks (e.g. sqlite3
        # errors during startup) go to stdout via __main__.on_activate.
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


# ─── T4.8 acceptance scenarios ──────────────────────────────────────────────


def _enable_autorun(home, project_name):
    """Re-enable autorun after BT startup. T4.8.1: TaskPanel.__init__
    runs `_reset_all_autorun()` (tasks.py:216) which forces autorun=0
    for ALL projects on startup — deliberate UX so the user always
    explicitly opts in via the panel's Start button. Tests have to
    simulate that opt-in by writing the row themselves AFTER the GTK
    panel has finished initializing, otherwise force_idle returns
    without firing because `task_config.autorun==0`.
    """
    db_path = Path(home) / ".claude-context" / "context.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO task_config (project, autorun) VALUES (?, 1) "
            "ON CONFLICT(project) DO UPDATE SET autorun = 1",
            (project_name,),
        )
        conn.commit()
    finally:
        conn.close()


def test_auto_trigger_fires_in_both_tabs(bterminal_dual_with_tasks):
    """The Tier 4 promise: open Claude + Copilot tabs sharing one
    project with 2 unclaimed tasks. Force idle on each — both tabs
    must receive an `auto_trigger` feed event (i.e. both fired the
    [AUTO-TRIGGER] message into their VTE).
    """
    state = bterminal_dual_with_tasks
    base, token = state["base"], state["token"]

    # Step 1: open Claude tab via the new endpoint
    status, body = _http(base, token, "POST",
                          "/api/tabs/ai/claude",
                          {"config_name": "ClaudeSession"})
    assert status == 200, body
    claude_idx = body["idx"]

    # Step 2: open Copilot tab
    status, body = _http(base, token, "POST",
                          "/api/tabs/ai/copilot",
                          {"config_name": "CopilotSession"})
    assert status == 200, body
    copilot_idx = body["idx"]
    assert copilot_idx != claude_idx

    # Step 3: snapshot feed_log baseline so we count only new events
    time.sleep(0.5)  # let intro_prompt records settle

    # T4.8.1: re-enable autorun (panel reset it on startup).
    _enable_autorun(state["home"], state["project_name"])

    pivot_ts = time.time()

    # Step 4: force idle on Claude tab → fires auto_trigger (claims t-claude)
    status, body = _http(base, token, "POST",
                          f"/api/tabs/{claude_idx}/force_idle")
    assert status == 200, body

    # Step 5: force idle on Copilot tab → fires auto_trigger (claims t-copilot)
    status, body = _http(base, token, "POST",
                          f"/api/tabs/{copilot_idx}/force_idle")
    assert status == 200, body

    # Step 6: wait for the GLib idle dispatch + record_feed flush, then
    # query feed_log for events post-pivot.
    time.sleep(0.5)
    status, feed = _http(base, token, "GET",
                          f"/api/debug/feed_log?since={pivot_ts}")
    assert status == 200, feed
    auto_events = [e for e in feed["events"]
                   if e.get("label") == "auto_trigger"]

    assert len(auto_events) == 2, (
        f"Expected 2 auto_trigger events (one per tab); got "
        f"{len(auto_events)}: {[e.get('label') for e in feed['events']]}"
    )

    # T4.8.1: record_feed currently doesn't propagate tab_idx (all
    # callers in terminal_tab.py use the default -1). The signal that
    # both tabs fired is that BOTH seeded task IDs were claimed —
    # decode the messages and assert each task appears in exactly
    # one event.
    bodies = [base64.b64decode(e["bytes_b64"]).decode("utf-8", errors="replace")
              for e in auto_events]
    claude_hits = sum("t-claude" in b for b in bodies)
    copilot_hits = sum("t-copilot" in b for b in bodies)
    assert claude_hits == 1 and copilot_hits == 1, (
        f"Each task must be claimed by exactly one tab. "
        f"t-claude hits={claude_hits}, t-copilot hits={copilot_hits}\n"
        f"bodies: {bodies}"
    )

    _stderr_clean_or_fail(state["stderr_path"])


def test_auto_trigger_messages_reference_correct_tasks(
    bterminal_dual_with_tasks,
):
    """Each tab claims a different task atomically — the [AUTO-TRIGGER]
    message body identifies the claimed task_id."""
    state = bterminal_dual_with_tasks
    base, token = state["base"], state["token"]

    _, claude_body = _http(base, token, "POST",
                            "/api/tabs/ai/claude",
                            {"config_name": "ClaudeSession"})
    claude_idx = claude_body["idx"]
    _, copilot_body = _http(base, token, "POST",
                             "/api/tabs/ai/copilot",
                             {"config_name": "CopilotSession"})
    copilot_idx = copilot_body["idx"]

    time.sleep(0.5)
    _enable_autorun(state["home"], state["project_name"])
    pivot_ts = time.time()

    _http(base, token, "POST", f"/api/tabs/{claude_idx}/force_idle")
    _http(base, token, "POST", f"/api/tabs/{copilot_idx}/force_idle")
    time.sleep(0.5)

    _, feed = _http(base, token, "GET",
                     f"/api/debug/feed_log?since={pivot_ts}")
    auto_events = [e for e in feed["events"]
                   if e.get("label") == "auto_trigger"]
    assert len(auto_events) == 2

    # Decode bytes_b64 → text and check task IDs
    bodies = [base64.b64decode(ev["bytes_b64"]).decode("utf-8",
                                                       errors="replace")
              for ev in auto_events]

    # Both messages contain the [AUTO-TRIGGER] sentinel
    assert all("[AUTO-TRIGGER]" in body for body in bodies), bodies

    # Both seeded tasks were each claimed exactly once across the two
    # messages — proves atomic per-tab claim (no double-claim).
    # T4.8.1: tab_idx isn't propagated by record_feed (always -1), so
    # we identify the per-tab claim via the task_id in the message body.
    full = "\n".join(bodies)
    assert "t-claude" in full, f"t-claude not claimed: {bodies}"
    assert "t-copilot" in full, f"t-copilot not claimed: {bodies}"
    assert sum("t-claude" in b for b in bodies) == 1, bodies
    assert sum("t-copilot" in b for b in bodies) == 1, bodies


def test_no_third_trigger_when_no_more_tasks(bterminal_dual_with_tasks):
    """After both tasks are claimed, a third force_idle on either tab
    must NOT emit a fresh auto_trigger (no more open tasks)."""
    state = bterminal_dual_with_tasks
    base, token = state["base"], state["token"]

    _, c_body = _http(base, token, "POST", "/api/tabs/ai/claude",
                       {"config_name": "ClaudeSession"})
    _, p_body = _http(base, token, "POST", "/api/tabs/ai/copilot",
                       {"config_name": "CopilotSession"})
    claude_idx = c_body["idx"]
    copilot_idx = p_body["idx"]

    # First round: each tab claims its task
    time.sleep(0.5)
    _http(base, token, "POST", f"/api/tabs/{claude_idx}/force_idle")
    _http(base, token, "POST", f"/api/tabs/{copilot_idx}/force_idle")
    time.sleep(0.5)

    # Pivot AFTER first round so we count only new events
    pivot_ts = time.time()

    # Second round: no unclaimed tasks left → no new auto_trigger
    _http(base, token, "POST", f"/api/tabs/{claude_idx}/force_idle")
    _http(base, token, "POST", f"/api/tabs/{copilot_idx}/force_idle")
    time.sleep(0.5)

    _, feed = _http(base, token, "GET",
                     f"/api/debug/feed_log?since={pivot_ts}")
    auto_events = [e for e in feed["events"]
                   if e.get("label") == "auto_trigger"]
    assert auto_events == [], (
        f"Unexpected auto_trigger after tasks claimed: {auto_events}"
    )

    _stderr_clean_or_fail(state["stderr_path"])


def test_intro_prompts_recorded_for_both_providers(bterminal_dual_with_tasks):
    """Sanity that both providers ran spawn_ai_cli's record_feed —
    extends T2.10 / T3.10 / Tier2-acceptance to the dual-tab case."""
    state = bterminal_dual_with_tasks
    base, token = state["base"], state["token"]

    _http(base, token, "POST", "/api/tabs/ai/claude",
           {"config_name": "ClaudeSession"})
    _http(base, token, "POST", "/api/tabs/ai/copilot",
           {"config_name": "CopilotSession"})

    time.sleep(0.5)
    _, feed = _http(base, token, "GET", "/api/debug/feed_log?since=0")
    intro_events = [e for e in feed["events"]
                    if e.get("label") == "intro_prompt"]
    assert len(intro_events) >= 2, intro_events

    # T4.8.1: record_feed currently doesn't propagate tab_idx
    # (terminal_tab.py callers all use the default -1). The "two
    # different tabs spawned" signal here is two intro_prompt events
    # whose decoded payloads differ — the Claude prompt header mentions
    # the Claude provider's long_label, the Copilot prompt mentions
    # GitHub Copilot CLI's. _compute_intro_prompt_for_tab pulls
    # provider.display.long_label from the registry (helpers.py:127).
    bodies = [base64.b64decode(e["bytes_b64"]).decode("utf-8",
                                                       errors="replace")
              for e in intro_events]
    body_blob = "\n".join(bodies)
    assert "Claude Code" in body_blob, bodies
    assert "GitHub Copilot CLI" in body_blob, bodies
