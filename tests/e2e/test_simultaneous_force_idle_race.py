"""Race condition: simultaneous force_idle on 2 tabs claiming from
same task pool (#37 / #109, audit § 6.2 #10).

Plan: seed 1 task in CTX DB, open Aider + Copilot tabs sharing
that project, fire force_idle on BOTH tabs in parallel via
concurrent.futures, assert exactly 1 tab claimed (atomic SQL
serialization), the other observes None and doesn't fire
[AUTO-TRIGGER].

Three decision branches:
  (a) Both fire within 50ms — threading.Barrier synchronizes the
      two REST POSTs to land within microseconds at the same
      sqlite3 transaction window.
  (b) DB BUSY error during claim — second caller's BEGIN
      IMMEDIATE may hit SQLITE_BUSY if the first hasn't
      COMMIT'd yet. Caught upstream by _on_task_idle_timeout's
      bare except. Pin: only 1 INSERT lands.
  (c) One tab's _on_task_idle_timeout sees existing claim from
      other tab — sequential calls (no race), the WHERE c.task_id
      IS NULL filter excludes the claimed task → second tab
      returns None cleanly.

Pre-#109 baseline (pinned by tests/e2e/test_concurrent_aider_
claude_spawn.py:test_concurrent_claim_threads_serialize_via_
begin_immediate): without BEGIN IMMEDIATE the SELECT-then-INSERT
window allowed both threads to see 'unclaimed' and both INSERT
per-session rows. #109 fix wraps the window in BEGIN IMMEDIATE.

Manual VM smoke (open BT, 2 tabs autorun=1, observe single
[AUTO-TRIGGER] event) is documented in tests/manual/README.md.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
MOCK_SRC = REPO_ROOT / "tools" / "mock_ai_cli"


# ─── Source-grep: BEGIN IMMEDIATE wrap landed in _claim_next_task ────────


def test_claim_next_task_uses_begin_immediate():
    """The #109 fix wraps the SELECT-then-INSERT window with
    BEGIN IMMEDIATE so concurrent callers serialize. Pin so a
    refactor that drops the lock re-introduces the cross-session
    race."""
    src = (REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py").read_text()
    fn_start = src.find("def _claim_next_task")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert 'db.execute("BEGIN IMMEDIATE")' in body, (
        "_claim_next_task no longer uses BEGIN IMMEDIATE — "
        "cross-session race re-introduced"
    )
    # And the existing-claim short-circuit also commits (closes
    # the transaction) so the connection isn't left in BEGIN
    # state for the next call
    assert body.count("db.commit()") >= 3, (
        "BEGIN IMMEDIATE not paired with commits on every exit "
        f"path: count={body.count('db.commit()')}"
    )
    # IntegrityError path rolls back
    assert "db.rollback()" in body


def test_claim_next_task_handles_already_in_transaction():
    """If the connection is already in a transaction (caller seeded
    state), BEGIN IMMEDIATE raises OperationalError. The fix
    catches it silently — the existing implicit txn provides
    serialization. Pin so a refactor that removes the catch
    breaks callers that seeded data first."""
    src = (REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py").read_text()
    fn_start = src.find("def _claim_next_task")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    # Find the EXECUTABLE BEGIN IMMEDIATE — the literal
    # `db.execute("BEGIN IMMEDIATE")` call (not docstring mentions).
    code_idx = body.find('db.execute("BEGIN IMMEDIATE")')
    assert code_idx > 0, "no executable BEGIN IMMEDIATE call found"
    after = body[code_idx:code_idx + 200]
    assert "except sqlite3.OperationalError" in after, (
        "BEGIN IMMEDIATE not guarded against 'already in txn' case"
    )


# ─── (c) Sequential calls: second sees claim, returns None ──────────────


def _seed_db(path, project="myproj", task_ids=None, autorun=1):
    task_ids = task_ids or ["t-1"]
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript("""
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, task_id TEXT NOT NULL,
                description TEXT NOT NULL, status TEXT DEFAULT 'open',
                UNIQUE(project, task_id)
            );
            CREATE TABLE task_config (project TEXT PRIMARY KEY,
                                        autorun INTEGER DEFAULT 0);
            CREATE TABLE task_claims (
                project TEXT NOT NULL, task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                PRIMARY KEY (project, task_id, session_id)
            );
        """)
        conn.execute(
            "INSERT OR REPLACE INTO task_config VALUES (?, ?)",
            (project, autorun),
        )
        for tid in task_ids:
            conn.execute(
                "INSERT INTO tasks (project, task_id, description) "
                "VALUES (?, ?, ?)",
                (project, tid, f"task {tid}"),
            )
        conn.commit()
    finally:
        conn.close()


def test_sequential_claim_second_call_returns_none(tmp_path):
    """(c) Sequential: aider claims t-1; copilot then calls
    _claim_next_task → WHERE c.task_id IS NULL excludes claimed
    rows → returns None. Pin baseline."""
    db_path = tmp_path / "context.db"
    _seed_db(db_path)

    from bterminal.ui.terminal_tab import TerminalTab

    db1 = sqlite3.connect(str(db_path))
    db1.row_factory = sqlite3.Row
    db2 = sqlite3.connect(str(db_path))
    db2.row_factory = sqlite3.Row

    aider = TerminalTab._claim_next_task(db1, "myproj", "s-aider")
    assert aider is not None
    assert aider["task_id"] == "t-1"

    copilot = TerminalTab._claim_next_task(db2, "myproj", "s-copilot")
    assert copilot is None, (
        f"copilot got task despite aider's prior claim: {copilot!r}"
    )

    db1.close(); db2.close()


# ─── (a) Both fire within 50ms — Barrier-synced thread race ─────────────


def test_concurrent_force_idle_serializes_via_begin_immediate(tmp_path):
    """(a) Two threads call _claim_next_task at the same instant
    (Barrier sync). #109 BEGIN IMMEDIATE serializes them →
    exactly 1 winner."""
    db_path = tmp_path / "context.db"
    _seed_db(db_path, task_ids=["t-only"])

    from bterminal.ui.terminal_tab import TerminalTab

    barrier = threading.Barrier(2)
    results = {}

    def worker(session_id):
        c = sqlite3.connect(str(db_path), timeout=2)
        c.row_factory = sqlite3.Row
        barrier.wait()
        try:
            results[session_id] = TerminalTab._claim_next_task(
                c, "myproj", session_id)
        except sqlite3.OperationalError as exc:
            results[session_id] = ("locked", str(exc))
        finally:
            c.close()

    t1 = threading.Thread(target=worker, args=("s-aider",))
    t2 = threading.Thread(target=worker, args=("s-copilot",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert len(results) == 2

    # EXACTLY 1 row in task_claims — race serialized by BEGIN
    # IMMEDIATE
    final = sqlite3.connect(str(db_path))
    rows = final.execute(
        "SELECT session_id FROM task_claims"
    ).fetchall()
    final.close()
    assert len(rows) == 1, (
        f"#109 fix regressed: {len(rows)} claims for one task"
    )

    # Exactly 1 winner with a Row object; the loser sees None or
    # ('locked', msg)
    winners = [sid for sid, res in results.items()
               if res is not None and not (
                   isinstance(res, tuple) and res[0] == "locked"
               )]
    assert len(winners) == 1


def test_higher_concurrency_preserves_single_winner(tmp_path):
    """Stress: 5 concurrent _claim_next_task calls for 1 task →
    still exactly 1 winner. Catches a regression where BEGIN
    IMMEDIATE works for 2 threads but races at higher concurrency
    (e.g. lock acquisition order issues)."""
    db_path = tmp_path / "context.db"
    _seed_db(db_path, task_ids=["t-popular"])

    from bterminal.ui.terminal_tab import TerminalTab

    n = 5
    barrier = threading.Barrier(n)
    results = {}

    def worker(session_id):
        c = sqlite3.connect(str(db_path), timeout=5)
        c.row_factory = sqlite3.Row
        barrier.wait()
        try:
            results[session_id] = TerminalTab._claim_next_task(
                c, "myproj", session_id)
        except sqlite3.OperationalError as exc:
            results[session_id] = ("locked", str(exc))
        finally:
            c.close()

    with ThreadPoolExecutor(max_workers=n) as pool:
        futures = [pool.submit(worker, f"s-{i}") for i in range(n)]
        for f in as_completed(futures):
            f.result()  # propagate any unexpected exception

    final = sqlite3.connect(str(db_path))
    rows = final.execute(
        "SELECT session_id FROM task_claims"
    ).fetchall()
    final.close()
    assert len(rows) == 1, (
        f"5-way race produced {len(rows)} claims — BEGIN IMMEDIATE "
        f"breaks at higher concurrency"
    )


# ─── (b) DB BUSY error path: second caller hits OperationalError ────────


def test_busy_error_path_returns_none_or_locked_marker(tmp_path):
    """When BEGIN IMMEDIATE hits SQLITE_BUSY (write lock held by
    another connection past busy_timeout), the second caller
    raises OperationalError. Caller's bare except catches it.

    Pin: race never produces multiple INSERTs; either serialization
    works (1 winner) or BUSY (caller propagates the exception
    upstream where the GLib timer's bare except absorbs it)."""
    db_path = tmp_path / "context.db"
    _seed_db(db_path, task_ids=["t-busy"])

    # Hold an EXCLUSIVE lock from connection A; connection B's
    # BEGIN IMMEDIATE in _claim_next_task hits BUSY immediately
    # (timeout=0.1s).
    conn_a = sqlite3.connect(str(db_path))
    conn_a.execute("BEGIN EXCLUSIVE")

    from bterminal.ui.terminal_tab import TerminalTab

    try:
        conn_b = sqlite3.connect(str(db_path), timeout=0.1)
        conn_b.row_factory = sqlite3.Row
        with pytest.raises(sqlite3.OperationalError) as exc_info:
            TerminalTab._claim_next_task(conn_b, "myproj", "s-b")
        assert "lock" in str(exc_info.value).lower()
        conn_b.close()
    finally:
        conn_a.rollback()
        conn_a.close()

    # No rows in task_claims — neither caller succeeded
    final = sqlite3.connect(str(db_path))
    rows = final.execute(
        "SELECT * FROM task_claims"
    ).fetchall()
    final.close()
    assert rows == []


# ─── E2E: simultaneous force_idle via REST → exactly 1 auto_trigger ─────


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
        f"{base}{path}", data=data,
        headers={"Authorization": f"Bearer {token}",
                  "Content-Type": "application/json"},
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


def _seed_full_ctx_db(home, project_name, project_dir, task_ids,
                        autorun=1):
    """Full CTX schema (mirrors test_dual_provider_workflow's
    helper) so CtxManagerPanel.refresh() doesn't blow up."""
    ctx_dir = Path(home) / ".claude-context"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    db_path = ctx_dir / "context.db"
    conn = sqlite3.connect(str(db_path))
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
                project TEXT NOT NULL, key TEXT NOT NULL,
                value TEXT NOT NULL,
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
                rule TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'user',
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
        conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(name, description, work_dir) VALUES (?, ?, ?)",
            (project_name, "race test", project_dir),
        )
        conn.execute(
            "INSERT OR REPLACE INTO task_config VALUES (?, ?)",
            (project_name, autorun),
        )
        for tid in task_ids:
            conn.execute(
                "INSERT INTO tasks "
                "(project, task_id, description, status) "
                "VALUES (?, ?, ?, 'open')",
                (project_name, tid, f"E2E task {tid}"),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def bterminal_with_aider_copilot_one_task():
    """BT subprocess with mock aider + copilot binaries + 2 AI
    sessions sharing one project + EXACTLY 1 unclaimed task."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-race-fidle-")
    seed_license(home)

    fake_bin = Path(home) / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    for name in ("aider", "copilot"):
        target = fake_bin / name
        shutil.copy(str(MOCK_SRC), str(target))
        target.chmod(target.stat().st_mode
                      | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    project_dir = Path(home) / "shared"
    project_dir.mkdir(parents=True, exist_ok=True)
    project_name = "shared"

    cfg_dir = Path(home) / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ai_sessions.json").write_text(json.dumps([
        {
            "id": "aider-1", "name": "AiderSession",
            "provider": "aider", "project_dir": str(project_dir),
            "color": "#fab387", "provider_options": {},
        },
        {
            "id": "copilot-1", "name": "CopilotSession",
            "provider": "copilot", "project_dir": str(project_dir),
            "color": "#a6e3a1", "provider_options": {},
        },
    ]))

    _seed_full_ctx_db(home, project_name, str(project_dir),
                       task_ids=["t-only-one"], autorun=1)

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    env = {
        **os.environ,
        "HOME": home,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "BTERMINAL_DEBUG_REST_PORT": str(port),
        "MOCK_AI_CLI_SCENARIO": str(REPO_ROOT / "tests" / "scenarios"
                                     / "claude_basic.json"),
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
        proc.terminate(); proc.wait(timeout=5)
        stderr_handle.close(); stdout_handle.close()
        try:
            err = Path(stderr_path).read_text(errors="replace")[-2000:]
        except OSError:
            err = "(unreadable)"
        try:
            out = Path(stdout_path).read_text(errors="replace")[-2000:]
        except OSError:
            out = "(unreadable)"
        pytest.fail(
            f"BT didn't come up\n--- stdout ---\n{out}\n"
            f"--- stderr ---\n{err}"
        )

    token = (cfg_dir / "debug_token").read_text().strip()
    try:
        yield {
            "base": base, "token": token, "home": home,
            "project_name": project_name,
            "project_dir": str(project_dir),
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
        stderr_handle.close(); stdout_handle.close()


def _enable_autorun(home, project_name):
    """Re-enable autorun after BT startup (TaskPanel.__init__
    resets autorun=0 on startup as deliberate UX)."""
    db_path = Path(home) / ".claude-context" / "context.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO task_config VALUES (?, 1) "
            "ON CONFLICT(project) DO UPDATE SET autorun = 1",
            (project_name,),
        )
        conn.commit()
    finally:
        conn.close()


def test_simultaneous_force_idle_yields_exactly_one_auto_trigger(
        bterminal_with_aider_copilot_one_task):
    """E2E headline #109: Aider + Copilot tabs share 1 task. Fire
    force_idle on both via concurrent.futures. Exactly 1 tab
    receives [AUTO-TRIGGER]; the other observes None and stays
    silent. Verifies the BEGIN IMMEDIATE serialization works
    end-to-end through the REST → GLib idle → DB path."""
    state = bterminal_with_aider_copilot_one_task
    base, token = state["base"], state["token"]

    # Open Aider tab
    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    assert status == 200, body
    aider_idx = body["idx"]
    # Open Copilot tab
    status, body = _http(base, token, "POST", "/api/tabs/ai/copilot",
                          {"config_name": "CopilotSession"})
    assert status == 200, body
    copilot_idx = body["idx"]

    time.sleep(0.5)
    _enable_autorun(state["home"], state["project_name"])
    pivot_ts = time.time()

    # Concurrent force_idle via concurrent.futures
    def fire(idx):
        return _http(base, token, "POST",
                      f"/api/tabs/{idx}/force_idle", {})

    with ThreadPoolExecutor(max_workers=2) as pool:
        futures = [pool.submit(fire, aider_idx),
                   pool.submit(fire, copilot_idx)]
        for f in as_completed(futures):
            status, _ = f.result()
            assert status == 200

    time.sleep(0.5)

    # Exactly 1 row in task_claims (the winner)
    db_path = Path(state["home"]) / ".claude-context" / "context.db"
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT session_id FROM task_claims "
        "WHERE task_id = 't-only-one'"
    ).fetchall()
    conn.close()
    assert len(rows) == 1, (
        f"#109 race regressed: {len(rows)} claims for t-only-one"
    )

    # Exactly 1 auto_trigger event in feed_log
    status, feed = _http(
        base, token, "GET",
        f"/api/debug/feed_log?since={pivot_ts}", None,
    )
    assert status == 200
    auto_events = [e for e in feed["events"]
                   if e.get("label") == "auto_trigger"]
    assert len(auto_events) == 1, (
        f"expected exactly 1 auto_trigger event under race; got "
        f"{len(auto_events)}"
    )

    # The fired event references the seeded task_id
    body_decoded = base64.b64decode(
        auto_events[0]["bytes_b64"]
    ).decode("utf-8", errors="replace")
    assert "t-only-one" in body_decoded


def test_simultaneous_force_idle_does_not_corrupt_stderr(
        bterminal_with_aider_copilot_one_task):
    """No AssertionError / Traceback in BT stderr after the race —
    the GLib main loop survived both concurrent force_idle calls."""
    state = bterminal_with_aider_copilot_one_task
    base, token = state["base"], state["token"]

    _http(base, token, "POST", "/api/tabs/ai/aider",
           {"config_name": "AiderSession"})
    _http(base, token, "POST", "/api/tabs/ai/copilot",
           {"config_name": "CopilotSession"})
    time.sleep(0.5)
    _enable_autorun(state["home"], state["project_name"])

    def fire(idx):
        return _http(base, token, "POST",
                      f"/api/tabs/{idx}/force_idle", {})

    with ThreadPoolExecutor(max_workers=2) as pool:
        list(pool.map(fire, [1, 2]))

    time.sleep(0.5)
    err = Path(state["stderr_path"]).read_text(errors="replace")
    forbidden = ["AssertionError", "AttributeError", "NameError",
                 "ImportError", "Traceback (most recent call last)"]
    bad = [p for p in forbidden if p in err]
    assert not bad, f"BT stderr has {bad}: {err[:1500]}"


# ─── Cross-cutting: same-session repeat call still returns same task ────


def test_same_session_repeated_force_idle_returns_same_task(tmp_path):
    """Pre-#109 invariant preserved: a single session calling
    _claim_next_task multiple times gets the SAME task back (first-
    branch existing-claim shortcut). BEGIN IMMEDIATE doesn't break
    this — its serialization is across sessions, not within one."""
    db_path = tmp_path / "context.db"
    _seed_db(db_path, task_ids=["t-1", "t-2"])

    from bterminal.ui.terminal_tab import TerminalTab

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row

    first = TerminalTab._claim_next_task(db, "myproj", "s-1")
    second = TerminalTab._claim_next_task(db, "myproj", "s-1")
    db.close()

    assert first["task_id"] == second["task_id"]
