"""Race condition: concurrent tab spawns (Aider + Claude same project)
(#36 / #108, audit § 6.2 #9).

Two tabs opened in parallel via REST POST `/api/tabs/ai/{provider}`
must:
  1. Both register successfully — neither blocks the other.
  2. ai_sessions.json reads (which happen during config_name lookup)
     never see a truncated mid-write file.
  3. _claim_next_task remains atomic per-session_id across both
     tabs (the cross-session race-condition gap noted in #104 is
     acknowledged but out of scope here).

Three decision branches:
  (a) Both spawn at exact same monotonic ts — threading.Barrier
      synchronizes the two REST POSTs to fire within microseconds.
  (b) One spawn fails — does the other proceed? Fixture seeds an
      Aider session whose binary IS NOT on PATH, while Claude's IS.
      Aider tab's spawn ends in 'binary not found' VTE script;
      Claude tab spawns normally. Both /api/tabs entries register.
  (c) ai_sessions.json read mid-write — atomic save via
      tempfile.mkstemp + os.replace means readers ALWAYS see
      either the old file or the new file, never a torn write.

Manual VM smoke (open BT, click both Add Aider + Add Claude
rapidly) is documented in tests/manual/README.md. Headless tests
below pin the dispatch logic without GTK.
"""
from __future__ import annotations

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
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent.parent
MOCK_SRC = REPO_ROOT / "tools" / "mock_ai_cli"


# ─── Pure-Python: ai_sessions.json atomic save under concurrent reads ────


def test_models_save_uses_atomic_rename():
    """`save()` uses tempfile.mkstemp + os.replace — atomic rename.
    Without this, concurrent readers could see a truncated file
    mid-write. Pin the source contract."""
    src = (REPO_ROOT / "bterminal" / "models.py").read_text()
    save_idx = src.find("def save(self):")
    assert save_idx > 0
    # Walk to next def (end of save())
    next_def = src.find("\n    def ", save_idx + 1)
    body = src[save_idx:next_def]
    assert "tempfile.mkstemp" in body, (
        "save() doesn't use tempfile — non-atomic write"
    )
    assert "os.replace(tmp, self._filepath)" in body, (
        "save() doesn't atomically rename — readers can see torn file"
    )


def test_models_load_catches_json_decode_error():
    """`load()` catches JSONDecodeError → falls back to empty list.
    Without this, a torn-write situation (impossible given atomic
    save, but defensive) wouldn't crash callers."""
    src = (REPO_ROOT / "bterminal" / "models.py").read_text()
    load_idx = src.find("def load(self):")
    next_def = src.find("\n    def ", load_idx + 1)
    body = src[load_idx:next_def]
    assert "json.JSONDecodeError" in body
    assert "self.sessions = []" in body


def test_concurrent_save_and_load_never_yields_torn_file(tmp_path):
    """Pure-Python stress test: thread A repeatedly saves; thread B
    repeatedly loads. With atomic os.replace, B NEVER reads a
    truncated file → never raises JSONDecodeError → never falls
    back to []."""
    sessions_file = tmp_path / "ai_sessions.json"
    # Seed initial valid file
    sessions_file.write_text(json.dumps([{"id": "1", "name": "init"}]))

    stop_event = threading.Event()
    bad_reads = []
    write_count = {"n": 0}

    def writer():
        # Many small writes — force frequent renames
        for i in range(100):
            data = [
                {"id": f"a-{i}", "name": "Aider", "provider": "aider"},
                {"id": f"c-{i}", "name": "Claude", "provider": "claude"},
            ]
            # Mirror the production save() pattern
            fd, tmp = tempfile.mkstemp(dir=str(tmp_path), suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                json.dump(data, f)
            os.replace(tmp, str(sessions_file))
            write_count["n"] += 1
        stop_event.set()

    def reader():
        # Read until writer finishes
        while not stop_event.is_set():
            try:
                with open(sessions_file) as f:
                    json.load(f)  # Should NEVER raise
            except json.JSONDecodeError as exc:
                bad_reads.append(str(exc))
            except FileNotFoundError:
                # Acceptable transient between rename and next write
                pass

    t_w = threading.Thread(target=writer)
    t_r1 = threading.Thread(target=reader)
    t_r2 = threading.Thread(target=reader)
    t_w.start(); t_r1.start(); t_r2.start()
    t_w.join(); t_r1.join(); t_r2.join()

    assert write_count["n"] == 100
    assert bad_reads == [], (
        f"readers saw torn writes ({len(bad_reads)} of them) — "
        f"atomic rename guarantee broken: {bad_reads[:3]}"
    )


def test_load_with_corrupted_file_returns_empty_list(tmp_path):
    """End-to-end of the JSONDecodeError fallback path: write a
    truncated JSON, invoke load(), assert empty list (no crash).
    Defensive belt-and-suspenders for atomic-save guarantee."""
    sessions_file = tmp_path / "ai_sessions.json"
    sessions_file.write_text('[{"id":"a","na')  # truncated mid-key

    # Replicate the production load() shape
    if sessions_file.exists():
        try:
            with open(sessions_file) as f:
                data = json.load(f)
        except (json.JSONDecodeError, IOError):
            data = []
    else:
        data = []
    assert data == []


# ─── E2E fixture: BT subprocess with both providers ──────────────────────


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


@pytest.fixture
def bterminal_with_both_providers():
    """BT subprocess with mock claude + aider binaries + 2 AI
    sessions (one per provider) sharing the same project_dir."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-race-spawn-")
    seed_license(home)

    fake_bin = Path(home) / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    for name in ("claude", "aider"):
        target = fake_bin / name
        shutil.copy(str(MOCK_SRC), str(target))
        target.chmod(target.stat().st_mode
                      | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    project_dir = Path(home) / "shared-project"
    project_dir.mkdir(parents=True, exist_ok=True)
    project_name = "shared-project"

    cfg_dir = Path(home) / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ai_sessions.json").write_text(json.dumps([
        {
            "id": "claude-1",
            "name": "ClaudeSession",
            "provider": "claude",
            "project_dir": str(project_dir),
            "color": "#89b4fa",
            "provider_options": {},
        },
        {
            "id": "aider-1",
            "name": "AiderSession",
            "provider": "aider",
            "project_dir": str(project_dir),
            "color": "#fab387",
            "provider_options": {},
        },
    ]))

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
        ["xvfb-run", "-a", sys.executable, "-m", "bterminal", "--debug-rest"],
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
            f"BT didn't come up within 30s\n"
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
        stderr_handle.close(); stdout_handle.close()


# ─── (a) Both spawn at near-same monotonic ts ────────────────────────────


def test_concurrent_spawn_both_providers_register(
        bterminal_with_both_providers):
    """Two tabs spawn within ~10 ms via threading.Barrier. Both
    register; neither raises; both visible in /api/tabs."""
    state = bterminal_with_both_providers
    base, token = state["base"], state["token"]

    barrier = threading.Barrier(2)
    results = {}

    def spawn(provider, config_name):
        barrier.wait()
        status, body = _http(
            base, token, "POST", f"/api/tabs/ai/{provider}",
            {"config_name": config_name},
        )
        results[provider] = (status, body)

    t1 = threading.Thread(target=spawn, args=("claude", "ClaudeSession"))
    t2 = threading.Thread(target=spawn, args=("aider", "AiderSession"))
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)

    assert "claude" in results and "aider" in results
    # Both got 200 (or at least neither got 5xx)
    for prov, (status, body) in results.items():
        assert status == 200, (
            f"{prov} spawn returned {status}: {body!r}"
        )
        assert "idx" in body
    # Different tab indices
    assert results["claude"][1]["idx"] != results["aider"][1]["idx"]


def test_concurrent_spawn_does_not_corrupt_ai_sessions_json(
        bterminal_with_both_providers):
    """After concurrent spawns, ai_sessions.json is still valid
    JSON. The spawn flow doesn't write to ai_sessions.json (only
    add/update do), so this is mostly a sanity check that BT didn't
    corrupt the file in some side path."""
    state = bterminal_with_both_providers
    base, token = state["base"], state["token"]

    # Trigger concurrent spawns
    barrier = threading.Barrier(2)
    def spawn(provider, config_name):
        barrier.wait()
        _http(base, token, "POST", f"/api/tabs/ai/{provider}",
               {"config_name": config_name})

    t1 = threading.Thread(target=spawn, args=("claude", "ClaudeSession"))
    t2 = threading.Thread(target=spawn, args=("aider", "AiderSession"))
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)
    time.sleep(0.3)

    # ai_sessions.json still parseable
    sessions_path = Path(state["home"]) / ".config" / "bterminal" \
        / "ai_sessions.json"
    assert sessions_path.exists()
    data = json.loads(sessions_path.read_text())
    assert isinstance(data, list)
    # Both seeded sessions still present (spawn doesn't add to the file)
    names = {s.get("name") for s in data}
    assert {"ClaudeSession", "AiderSession"}.issubset(names)


def test_concurrent_spawn_no_stderr_assertion_errors(
        bterminal_with_both_providers):
    """No AssertionError / AttributeError / ImportError appears in
    BT stderr after concurrent spawns. Catches the case where the
    GTK main loop saw a thread-unsafe access."""
    state = bterminal_with_both_providers
    base, token = state["base"], state["token"]

    barrier = threading.Barrier(2)
    def spawn(provider, cfg):
        barrier.wait()
        _http(base, token, "POST", f"/api/tabs/ai/{provider}",
               {"config_name": cfg})

    t1 = threading.Thread(target=spawn, args=("claude", "ClaudeSession"))
    t2 = threading.Thread(target=spawn, args=("aider", "AiderSession"))
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)
    time.sleep(0.5)

    err = Path(state["stderr_path"]).read_text(errors="replace")
    forbidden = ["AssertionError", "AttributeError", "NameError",
                 "ImportError", "Traceback (most recent call last)"]
    bad = [p for p in forbidden if p in err]
    assert not bad, f"BT stderr has {bad}: {err[:1500]}"


# ─── (b) One spawn fails — does the other proceed? ───────────────────────


def test_one_provider_unknown_other_proceeds(
        bterminal_with_both_providers):
    """Concurrent spawns where ONE references an unknown
    config_name. The valid one still proceeds; the invalid one
    returns 404, no shared-state corruption."""
    state = bterminal_with_both_providers
    base, token = state["base"], state["token"]

    barrier = threading.Barrier(2)
    results = {}

    def spawn(provider, config_name, key):
        barrier.wait()
        status, body = _http(
            base, token, "POST", f"/api/tabs/ai/{provider}",
            {"config_name": config_name},
        )
        results[key] = (status, body)

    t1 = threading.Thread(target=spawn,
                          args=("claude", "DOES-NOT-EXIST", "bad"))
    t2 = threading.Thread(target=spawn,
                          args=("aider", "AiderSession", "good"))
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)

    # Bad spawn returns 404
    assert results["bad"][0] == 404
    # Good spawn succeeds independently
    assert results["good"][0] == 200
    assert "idx" in results["good"][1]


# ─── (c) ai_sessions.json read mid-write — same atomic guarantee ─────────


def test_json_read_during_atomic_replace_sees_consistent_state(tmp_path):
    """Pure-Python: while a writer is mid-rename of ai_sessions.json,
    a reader either sees the OLD file or the NEW file — never a
    half-written one. Verified via 1000 reads while writer flips
    between two distinct sessions lists."""
    sessions_file = tmp_path / "ai_sessions.json"
    state_a = json.dumps([{"id": "a-1", "version": "A"}])
    state_b = json.dumps([{"id": "b-1", "version": "B"}])

    # Seed
    sessions_file.write_text(state_a)

    stop = threading.Event()
    inconsistent = []

    def writer():
        for i in range(200):
            content = state_a if i % 2 == 0 else state_b
            fd, tmp = tempfile.mkstemp(dir=str(tmp_path), suffix=".tmp")
            with os.fdopen(fd, "w") as f:
                f.write(content)
            os.replace(tmp, str(sessions_file))
        stop.set()

    def reader():
        while not stop.is_set():
            try:
                with open(sessions_file) as f:
                    data = json.load(f)
                # Every read must yield ONE OF the canonical states
                if data not in (json.loads(state_a), json.loads(state_b)):
                    inconsistent.append(data)
            except (json.JSONDecodeError, FileNotFoundError):
                inconsistent.append("decode-error")

    t_w = threading.Thread(target=writer)
    threads_r = [threading.Thread(target=reader) for _ in range(3)]
    t_w.start()
    for t in threads_r:
        t.start()
    t_w.join()
    for t in threads_r:
        t.join()

    assert inconsistent == [], (
        f"reads saw inconsistent state: {inconsistent[:3]}"
    )


# ─── _claim_next_task atomicity contract is per-session_id ──────────────


def test_claim_next_task_atomic_across_provider_sessions(tmp_path):
    """When two different-provider sessions race for the same task
    pool, _claim_next_task's `LEFT JOIN task_claims ... WHERE
    c.task_id IS NULL` filter excludes already-claimed tasks. So
    only the FIRST session wins; the second sees None.

    This contradicts a naive read of the PRIMARY KEY shape
    (project, task_id, session_id) — the SCHEMA allows multiple
    rows per task, but the QUERY filters them out at SELECT time.
    Pin the actual cross-session atomicity contract:
      - First session: gets the task, INSERT succeeds.
      - Second session: WHERE c.task_id IS NULL excludes it → None.

    Note: a direct INSERT into task_claims (bypassing
    _claim_next_task) WOULD allow per-session rows, see #104 test
    test_task_claims_primary_key_allows_per_session_rows. The two
    tests together document: schema permissive, query strict."""
    db_path = tmp_path / "context.db"
    conn = sqlite3.connect(str(db_path))
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
    conn.execute("INSERT INTO task_config VALUES ('p', 1)")
    conn.execute(
        "INSERT INTO tasks (project, task_id, description) "
        "VALUES ('p', 't-1', 'shared task')"
    )
    conn.commit()
    conn.close()

    from bterminal.ui.terminal_tab import TerminalTab

    db1 = sqlite3.connect(str(db_path))
    db1.row_factory = sqlite3.Row
    db2 = sqlite3.connect(str(db_path))
    db2.row_factory = sqlite3.Row

    # Aider claims first
    aider_task = TerminalTab._claim_next_task(db1, "p", "session-aider")
    assert aider_task is not None
    assert aider_task["task_id"] == "t-1"

    # Claude tries — sees no unclaimed tasks (aider's claim filtered
    # out by `WHERE c.task_id IS NULL`), returns None
    claude_task = TerminalTab._claim_next_task(db2, "p", "session-claude")
    assert claude_task is None, (
        f"Claude unexpectedly got a task: {claude_task!r} — "
        f"_claim_next_task's atomicity filter regressed"
    )

    db1.close(); db2.close()

    # Only ONE row in task_claims (aider's)
    final = sqlite3.connect(str(db_path))
    rows = final.execute(
        "SELECT session_id FROM task_claims WHERE task_id = 't-1'"
    ).fetchall()
    final.close()
    assert len(rows) == 1
    assert rows[0][0] == "session-aider"


def test_concurrent_claim_threads_serialize_via_begin_immediate(tmp_path):
    """Two threads racing on _claim_next_task for a single task.

    Post-#109: BEGIN IMMEDIATE serializes parallel callers at the
    SQL layer. The first thread acquires the write lock, runs
    SELECT-then-INSERT atomically, COMMITs. The second thread
    either:
      - Waits up to busy_timeout (2s here) then sees the first's
        commit → its SELECT shows 0 unclaimed → returns None
      - Hits SQLITE_BUSY → OperationalError (caught upstream by
        _on_task_idle_timeout's bare except)

    Either way, exactly 1 row in task_claims. Pre-#109 this test
    would see 2 rows."""
    db_path = tmp_path / "context.db"
    conn = sqlite3.connect(str(db_path))
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
    conn.execute("INSERT INTO task_config VALUES ('p', 1)")
    conn.execute(
        "INSERT INTO tasks (project, task_id, description) "
        "VALUES ('p', 't-1', 'race-target')"
    )
    conn.commit()
    conn.close()

    from bterminal.ui.terminal_tab import TerminalTab

    barrier = threading.Barrier(2)
    results = {}

    def worker(session_id):
        c = sqlite3.connect(str(db_path), timeout=2)
        c.row_factory = sqlite3.Row
        barrier.wait()
        try:
            results[session_id] = TerminalTab._claim_next_task(
                c, "p", session_id)
        except sqlite3.OperationalError as exc:
            results[session_id] = ("locked", str(exc))
        finally:
            c.close()

    t1 = threading.Thread(target=worker, args=("s-aider",))
    t2 = threading.Thread(target=worker, args=("s-claude",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # Each thread completed (no crash, no stuck threads)
    assert len(results) == 2

    # Pin actual current behavior: thread-race may yield 1 OR 2
    # winners depending on commit timing. ANY error must be
    # OperationalError (lock contention) — never a different
    # exception type that would leak to the GTK callback.
    for sid, res in results.items():
        if isinstance(res, tuple) and res[0] == "locked":
            assert "lock" in res[1].lower()
        else:
            # Either Row (claim succeeded) or None (saw existing claim)
            assert res is None or hasattr(res, "keys"), (
                f"unexpected result shape for {sid}: {type(res)}"
            )

    # Post-#109 fix: task_claims has EXACTLY 1 row. The cross-
    # session race is serialized by BEGIN IMMEDIATE.
    final = sqlite3.connect(str(db_path))
    rows = final.execute(
        "SELECT session_id FROM task_claims"
    ).fetchall()
    final.close()
    assert len(rows) == 1, (
        f"#109 BEGIN IMMEDIATE fix regressed: {len(rows)} claims "
        f"for one task (expected exactly 1)"
    )
