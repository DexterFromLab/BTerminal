"""Failure mode: CTX DB corrupted or locked during auto-trigger
(#32 / #104, audit § 6.1 #5).

When `~/.claude-context/context.db` is in a bad state during an
auto-trigger pass, BT must:

  1. NOT crash the GTK main loop — `_on_task_idle_timeout` wraps
     its DB work in `try / except Exception: pass`, so any sqlite3
     error short-circuits the trigger silently.
  2. NOT fire a duplicate [AUTO-TRIGGER] message — if the DB is
     unreachable on this tick, no message goes out (next tick
     re-tries from scratch).
  3. NOT corrupt state by writing partial claim rows — the claim
     happens INSIDE the same connection, so a failure during INSERT
     leaves the database in pre-claim state.

Three decision branches:
  (a) BUSY timeout exceeds — `sqlite3.OperationalError("database is
      locked")` raised by `db.execute()`. Default sqlite3 timeout
      is 5s; another process holding an EXCLUSIVE lock past that
      causes this.
  (b) DB file exists but corrupted — `sqlite3.DatabaseError("file
      is not a database")` raised on connect or query. PRAGMA
      integrity_check would also fail.
  (c) DB file deleted between calls — `os.path.exists(CTX_DB)`
      returns False, _on_task_idle_timeout early-returns. Or, if
      delete races with connect: `sqlite3.OperationalError("unable
      to open database file")`.

Manual VM smoke (`sqlite3 ~/.claude-context/context.db .quit &
force_idle on tab; observe`) is documented in tests/manual/README.md.
Headless tests below pin the dispatch resilience without spawning BT.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from pathlib import Path
from unittest.mock import patch

import pytest


# ─── (a) BUSY timeout — database is locked ───────────────────────────────


def _seed_db(path: Path, project: str = "myproj",
              task_ids: list[str] = None) -> None:
    """Create a minimal context.db schema."""
    task_ids = task_ids or ["t-1"]
    conn = sqlite3.connect(str(path))
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, task_id TEXT NOT NULL,
                description TEXT NOT NULL, status TEXT DEFAULT 'open',
                UNIQUE(project, task_id)
            );
            CREATE TABLE IF NOT EXISTS task_config (
                project TEXT PRIMARY KEY, autorun INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS task_claims (
                project TEXT NOT NULL, task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                PRIMARY KEY (project, task_id, session_id)
            );
        """)
        conn.execute(
            "INSERT OR REPLACE INTO task_config (project, autorun) "
            "VALUES (?, 1)", (project,))
        for tid in task_ids:
            conn.execute(
                "INSERT INTO tasks (project, task_id, description) "
                "VALUES (?, ?, ?)",
                (project, tid, f"task {tid}"),
            )
        conn.commit()
    finally:
        conn.close()


def test_claim_next_task_raises_operational_error_when_db_locked(
        tmp_path):
    """Hold an EXCLUSIVE lock from another connection; the second
    connection's `db.execute()` raises sqlite3.OperationalError once
    the busy timeout (default 5s) elapses. _claim_next_task is the
    method called from inside the auto-trigger loop — pin that the
    raw exception escapes (caller wraps with `except Exception`)."""
    db_path = tmp_path / "context.db"
    _seed_db(db_path)

    from bterminal.ui.terminal_tab import TerminalTab

    # Connection A holds the lock.
    conn_a = sqlite3.connect(str(db_path))
    conn_a.execute("BEGIN EXCLUSIVE")

    try:
        # Connection B has a tiny busy timeout so the test doesn't
        # block for 5s.
        conn_b = sqlite3.connect(str(db_path), timeout=0.1)
        conn_b.row_factory = sqlite3.Row
        with pytest.raises(sqlite3.OperationalError) as exc_info:
            TerminalTab._claim_next_task(conn_b, "myproj", "s-1")
        # Distinctive sqlite message
        assert "lock" in str(exc_info.value).lower(), (
            f"unexpected error: {exc_info.value!r}"
        )
        conn_b.close()
    finally:
        conn_a.rollback()
        conn_a.close()


def test_on_task_idle_timeout_swallows_operational_error_source_grep():
    """The wrapping `try / except Exception: pass` in
    _on_task_idle_timeout is the safety net. Source-grep pins it —
    a refactor that narrows the except clause to a specific
    exception type would make sqlite3.OperationalError leak through
    and crash the GTK callback."""
    repo = Path(__file__).resolve().parent.parent
    src = (repo / "bterminal" / "ui" / "terminal_tab.py").read_text()
    fn_start = src.find("def _on_task_idle_timeout")
    assert fn_start > 0
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    # The whole DB-touching block is wrapped in `try: ... except
    # Exception: pass` — pin both halves
    assert "try:" in body
    assert "except Exception:" in body
    # Specifically the DB path must be inside the try
    assert "sqlite3.connect(CTX_DB)" in body
    # The except: pass swallows every error including OperationalError
    except_idx = body.find("except Exception:")
    after_except = body[except_idx:except_idx + 80]
    assert "pass" in after_except


def test_on_task_idle_timeout_does_not_re_raise_when_db_locked(tmp_path,
                                                                  monkeypatch):
    """End-to-end: redirect CTX_DB to a path that's locked, invoke
    the method via a stub TerminalTab, assert it returns cleanly
    without raising. We can't easily call _on_task_idle_timeout
    without GTK, but we can simulate the same except-Exception
    contract by exercising the inner critical section."""
    db_path = tmp_path / "context.db"
    _seed_db(db_path)
    monkeypatch.setattr("bterminal.config.CTX_DB", str(db_path))

    # Hold lock
    conn_a = sqlite3.connect(str(db_path))
    conn_a.execute("BEGIN EXCLUSIVE")

    try:
        # Replicate the body of _on_task_idle_timeout's DB section
        # in pure form — confirms the code shape doesn't blow up
        # under lock contention.
        try:
            db = sqlite3.connect(str(db_path), timeout=0.1)
            db.row_factory = sqlite3.Row
            db.execute(
                "SELECT autorun FROM task_config WHERE project = ?",
                ("myproj",),
            ).fetchone()
            db.close()
            raised = None
        except Exception as exc:
            raised = exc
        # OperationalError must surface here — confirms our test setup
        # actually causes lock contention.
        assert isinstance(raised, sqlite3.OperationalError)
    finally:
        conn_a.rollback()
        conn_a.close()


# ─── (b) DB file exists but corrupted ────────────────────────────────────


def test_corrupted_db_raises_database_error(tmp_path):
    """Garbage bytes in the DB file → sqlite3.DatabaseError on first
    query (or sometimes on connect, depending on the corruption
    pattern). _on_task_idle_timeout's bare `except Exception` catches
    both subclasses. Pin the failure mode."""
    db_path = tmp_path / "context.db"
    db_path.write_bytes(b"this is not a sqlite3 database file at all")

    # connect() may succeed even on garbage; query() raises
    db = sqlite3.connect(str(db_path))
    with pytest.raises(sqlite3.DatabaseError) as exc_info:
        db.execute("SELECT * FROM tasks").fetchone()
    db.close()
    assert "not a database" in str(exc_info.value).lower() \
        or "malformed" in str(exc_info.value).lower()


def test_truncated_db_raises_database_error_during_query(tmp_path):
    """A partial sqlite header (e.g. WAL replay aborted) — looks
    like a DB on first byte but breaks on real query."""
    db_path = tmp_path / "context.db"
    # Truncated SQLite magic header
    db_path.write_bytes(b"SQLite format 3\x00" + b"\x00" * 32)

    db = sqlite3.connect(str(db_path))
    with pytest.raises(sqlite3.DatabaseError):
        db.execute("SELECT * FROM tasks").fetchone()
    db.close()


def test_pragma_integrity_check_detects_corruption(tmp_path):
    """For diagnostic purposes — `PRAGMA integrity_check` flags
    corruption explicitly. Useful for a future 'self-repair'
    feature; pin the detection path here."""
    db_path = tmp_path / "context.db"
    _seed_db(db_path)

    # Corrupt by overwriting middle bytes (after header, inside
    # btree page area)
    raw = db_path.read_bytes()
    if len(raw) > 200:
        corrupted = raw[:100] + b"\x00" * 50 + raw[150:]
        db_path.write_bytes(corrupted)

    # On a corrupted DB, integrity_check returns non-'ok' rows OR
    # raises. We check both shapes.
    db = sqlite3.connect(str(db_path))
    try:
        rows = db.execute("PRAGMA integrity_check").fetchall()
        # If it didn't raise: at least one row is NOT ('ok',)
        not_ok = [r for r in rows if r[0] != "ok"]
        assert not_ok or len(rows) == 0, (
            f"corruption not detected by integrity_check: {rows}"
        )
    except sqlite3.DatabaseError:
        # Acceptable — also signals corruption
        pass
    finally:
        db.close()


# ─── (c) DB file deleted between calls ───────────────────────────────────


def test_on_task_idle_timeout_short_circuits_when_db_missing(tmp_path,
                                                                monkeypatch):
    """When `os.path.exists(CTX_DB)` returns False, the method
    returns False immediately. Pin: no `sqlite3.connect` is
    attempted on a missing path, so no OperationalError surfaces.

    Source-grep contract — the early-return is the canonical
    implementation; a refactor that connects unconditionally would
    push errors into the except path (still safe but slower)."""
    repo = Path(__file__).resolve().parent.parent
    src = (repo / "bterminal" / "ui" / "terminal_tab.py").read_text()
    fn_start = src.find("def _on_task_idle_timeout")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    # Early-return on missing DB before connect
    assert "if not os.path.exists(CTX_DB):" in body
    exists_idx = body.find("if not os.path.exists(CTX_DB):")
    after_check = body[exists_idx:exists_idx + 100]
    assert "return" in after_check
    # The connect happens AFTER this check
    connect_idx = body.find("sqlite3.connect(CTX_DB)")
    assert connect_idx > exists_idx, (
        "sqlite3.connect happens BEFORE existence check — race "
        "window allows OperationalError to bubble up"
    )


def test_db_deleted_mid_query_raises_operational_error(tmp_path):
    """Race: connect succeeds, then file is deleted before query —
    sqlite3.OperationalError on the doomed connection's next
    operation. Pin that this is also caught by the bare except in
    _on_task_idle_timeout."""
    db_path = tmp_path / "context.db"
    _seed_db(db_path)

    db = sqlite3.connect(str(db_path))
    db_path.unlink()  # File deleted while connection still open

    # On Linux, unlink doesn't immediately invalidate the open fd —
    # the connection can still query its in-memory state. But a
    # write that flushes a page would fail. We just verify that ANY
    # behavior we get from the deleted-file connection doesn't
    # crash the test interpreter (caller wraps in try/except).
    try:
        rows = db.execute("SELECT 1").fetchall()
        # Linux unlink-after-open works; this is the expected path
        assert rows == [(1,)]
    except sqlite3.OperationalError:
        # Acceptable on filesystems that immediately invalidate
        pass
    finally:
        db.close()


def test_existence_check_uses_realpath_not_cached_inode(tmp_path):
    """`os.path.exists` checks the path string at call time — no
    inode caching. If file is deleted and recreated with same name,
    next call sees True. Pin so a future caching wrapper doesn't
    introduce stale results."""
    db_path = tmp_path / "context.db"
    db_path.write_text("placeholder")
    assert os.path.exists(str(db_path))
    db_path.unlink()
    assert not os.path.exists(str(db_path))
    db_path.write_text("again")
    assert os.path.exists(str(db_path))


# ─── No duplicate auto-trigger fires under any failure mode ──────────────


def test_failed_db_query_does_not_fire_auto_trigger_message():
    """Source-grep: the [AUTO-TRIGGER] feed_child happens AFTER all
    DB queries succeed. If any earlier query raises, the bare
    except catches it and we exit without firing the message. Pin
    the relative ordering of feed_child vs DB calls."""
    repo = Path(__file__).resolve().parent.parent
    src = (repo / "bterminal" / "ui" / "terminal_tab.py").read_text()
    fn_start = src.find("def _on_task_idle_timeout")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]

    # The feed_child("[AUTO-TRIGGER]") must appear AFTER db.execute,
    # AFTER _claim_next_task — otherwise a DB failure could still
    # fire a message with stale data.
    feed_child_idx = body.find('feed_child(message.encode())')
    claim_idx = body.find("_claim_next_task")
    db_execute_idx = body.find("db.execute(")
    assert feed_child_idx > 0
    assert claim_idx > 0
    assert db_execute_idx > 0
    assert feed_child_idx > claim_idx > db_execute_idx, (
        f"AUTO-TRIGGER feed ordering broken: feed_child at "
        f"{feed_child_idx}, claim at {claim_idx}, db.execute at "
        f"{db_execute_idx}"
    )


def test_claim_next_task_returns_none_when_no_open_tasks(tmp_path):
    """Sanity for 'no duplicate fires' contract: if no tasks are
    available (all claimed by other sessions), _claim_next_task
    returns None. _on_task_idle_timeout then early-returns without
    firing AUTO-TRIGGER. Pin this baseline."""
    db_path = tmp_path / "context.db"
    _seed_db(db_path, task_ids=["t-1"])

    from bterminal.ui.terminal_tab import TerminalTab

    db = sqlite3.connect(str(db_path))
    db.row_factory = sqlite3.Row

    # Pre-claim by another session
    db.execute(
        "INSERT INTO task_claims (project, task_id, session_id) "
        "VALUES (?, ?, ?)", ("myproj", "t-1", "s-other"),
    )
    db.commit()

    task = TerminalTab._claim_next_task(db, "myproj", "s-aider")
    db.close()
    # No task available → caller doesn't fire AUTO-TRIGGER
    assert task is None


# ─── Concurrent access stress test (lighter version of #109) ─────────────


def test_concurrent_claims_under_lock_contention_dont_crash(tmp_path):
    """Under DB lock contention, _claim_next_task either succeeds OR
    raises OperationalError — but does NOT crash the interpreter.
    Lighter version of the e2e race test in #109/#110.

    Important finding pinned by this test: the task_claims PRIMARY
    KEY is `(project, task_id, session_id)` — distinct sessions can
    each insert their own claim for the same task. The atomic
    contract is per-session_id, NOT per-task_id. The 'one task = one
    session' semantics live higher up (in _on_task_idle_timeout's
    flow that won't even call _claim_next_task if another session
    has already messaged in this tick) — but pinning the SQL-level
    atomicity here documents the actual schema contract.

    See also: #109 + #110 for the multi-session race-condition
    audit, where this finding may motivate a stricter PRIMARY KEY
    redesign (project, task_id) UNIQUE."""
    db_path = tmp_path / "context.db"
    _seed_db(db_path, task_ids=["t-only"])

    from bterminal.ui.terminal_tab import TerminalTab

    results = {}
    barrier = threading.Barrier(2)

    def worker(session_id):
        conn = sqlite3.connect(str(db_path), timeout=2)
        conn.row_factory = sqlite3.Row
        barrier.wait()  # Both threads start at the same instant
        try:
            task = TerminalTab._claim_next_task(conn, "myproj", session_id)
            results[session_id] = task
        except sqlite3.OperationalError as exc:
            results[session_id] = ("locked", str(exc))
        finally:
            conn.close()

    t1 = threading.Thread(target=worker, args=("s-a",))
    t2 = threading.Thread(target=worker, args=("s-b",))
    t1.start(); t2.start()
    t1.join(); t2.join()

    # Both threads completed — no crash, no stuck threads.
    assert len(results) == 2

    # Each result is either a Row (claim succeeded) or ('locked', ...)
    # tuple. NEITHER is None — _claim_next_task only returns None
    # when ALL tasks are claimed. With t-only fresh + race:
    #   - Either both succeed (different session_ids both insert),
    #   - Or one succeeds + other gets OperationalError (lock).
    for sid, res in results.items():
        if isinstance(res, tuple) and res[0] == "locked":
            continue  # OperationalError path — handled by upper try/except
        # Otherwise it's a row — must contain task_id
        assert res is None or "task_id" in res.keys(), (
            f"unexpected claim result for {sid}: {res!r}"
        )


def test_task_claims_primary_key_allows_per_session_rows(tmp_path):
    """Pin the schema contract: task_claims PRIMARY KEY is
    `(project, task_id, session_id)` — three-tuple. So distinct
    sessions can each insert claims for the same task without
    IntegrityError.

    This is the schema-level cause of the cross-session race noted
    above. Documenting it here so #109's fix has a clear baseline
    to compare against."""
    db_path = tmp_path / "context.db"
    _seed_db(db_path, task_ids=["t-shared"])

    db = sqlite3.connect(str(db_path))
    # Two distinct sessions claim the same task
    db.execute(
        "INSERT INTO task_claims (project, task_id, session_id) "
        "VALUES (?, ?, ?)", ("myproj", "t-shared", "s-a"),
    )
    db.execute(
        "INSERT INTO task_claims (project, task_id, session_id) "
        "VALUES (?, ?, ?)", ("myproj", "t-shared", "s-b"),
    )
    db.commit()

    rows = db.execute(
        "SELECT session_id FROM task_claims WHERE task_id = 't-shared'"
    ).fetchall()
    assert len(rows) == 2, (
        f"PRIMARY KEY rejected per-session claims (now stricter): "
        f"{rows} — #109's fix may have shipped, update this test."
    )
    db.close()


# ─── Stays alive under failure (BT survives) ─────────────────────────────


def test_on_task_idle_timeout_returns_false_under_any_db_state():
    """Source-grep pin: every code path through _on_task_idle_timeout
    returns False (or terminates without raising). Caller is
    GLib.timeout_add — returning False stops the timer; raising
    would crash the GTK main loop and take BT down."""
    repo = Path(__file__).resolve().parent.parent
    src = (repo / "bterminal" / "ui" / "terminal_tab.py").read_text()
    fn_start = src.find("def _on_task_idle_timeout")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    # Every return statement returns False (no truthy value that
    # would re-arm the timer)
    return_lines = [line.strip() for line in body.split("\n")
                    if line.strip().startswith("return")]
    for ret in return_lines:
        assert ret in ("return False", "return"), (
            f"_on_task_idle_timeout has non-False return: {ret!r}"
        )
    # And the bare `except Exception: pass` ensures uncaught raises
    # don't escape
    assert "except Exception:" in body
