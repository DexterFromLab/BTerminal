"""Security: tasks done parameter binding (SQL injection prevention)
(#56 / #128, audit § 6.7 #29).

Threat model: a malicious task_id (e.g. seeded via direct
SQL or imported from a compromised CTX export) contains SQL
injection payload. If the `tasks` CLI's UPDATE statement
interpolates the task_id via string concatenation instead of
parameterized binding, arbitrary SQL executes against the
user's CTX DB — could DROP TABLE, exfiltrate data via UNION
SELECT, or mutate PRAGMA settings.

Defense: every SQL op in `tasks` CLI uses sqlite3's
parameterized binding (`?` placeholder + tuple of values).
sqlite3 driver escapes the values internally; injection
attempts land as literal strings in the WHERE clause and
match nothing.

Three decision branches:
  (a) DROP TABLE — `'; DROP TABLE tasks;--` payload.
  (b) UNION SELECT — `' UNION SELECT * FROM rules_config;--`.
  (c) PRAGMA mutation — `'; PRAGMA journal_mode=DELETE;--`.

All three should be rejected (or, technically, treated as
literal task_ids that match no row → no rowcount → "not
found" message).

Manual VM smoke (seed crafted task_id via direct SQL, observe
ctx CLI behaviour) is documented in tests/manual/README.md.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
TASKS_CLI = REPO_ROOT / "tools" / "tasks"


def _seed_db(db_path: Path, project: str = "myproj"):
    """Build a minimal CTX DB matching the `tasks` CLI's
    expected schema (tasks + task_claims + sessions/contexts/
    shared so other ctx ops don't choke)."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            CREATE TABLE tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                task_id TEXT NOT NULL,
                description TEXT NOT NULL,
                status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project, task_id)
            );
            CREATE TABLE task_config (
                project TEXT PRIMARY KEY,
                autorun INTEGER DEFAULT 0
            );
            CREATE TABLE task_claims (
                project TEXT NOT NULL,
                task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                PRIMARY KEY (project, task_id, session_id)
            );
            CREATE TABLE sessions (
                name TEXT PRIMARY KEY,
                description TEXT,
                work_dir TEXT
            );
            CREATE TABLE contexts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                key TEXT NOT NULL,
                value TEXT NOT NULL,
                UNIQUE(project, key)
            );
            CREATE TABLE shared (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL
            );
            CREATE TABLE rules_config (
                project TEXT PRIMARY KEY,
                inject_every INTEGER DEFAULT 20,
                refresh_every INTEGER DEFAULT 50
            );
        """)
        # Seed a legitimate task so we can verify the table
        # survives + that legit ops still work after injection
        # attempts.
        conn.execute(
            "INSERT INTO tasks (project, task_id, description) "
            "VALUES (?, ?, ?)",
            (project, "real-task", "a real task"),
        )
        conn.execute(
            "INSERT OR REPLACE INTO rules_config "
            "(project, inject_every, refresh_every) "
            "VALUES (?, ?, ?)",
            (project, 100, 200),
        )
        conn.commit()
    finally:
        conn.close()


def _run_tasks(args, home: Path) -> subprocess.CompletedProcess:
    """Invoke the `tasks` CLI with args. Use isolated HOME so
    DB_PATH resolves to home/.claude-context/context.db."""
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        [sys.executable, str(TASKS_CLI)] + args,
        env=env,
        capture_output=True, text=True, timeout=10,
    )


def _table_exists(db_path: Path, name: str) -> bool:
    conn = sqlite3.connect(str(db_path))
    try:
        row = conn.execute(
            "SELECT name FROM sqlite_master WHERE type = 'table' "
            "AND name = ?",
            (name,),
        ).fetchone()
        return row is not None
    finally:
        conn.close()


# ─── Branch (a): DROP TABLE attempts ────────────────────────────────────


def test_drop_table_payload_in_task_id_does_not_drop_table(tmp_path):
    """Crafted task_id with `'; DROP TABLE tasks;--` payload.
    Pin: tasks table SURVIVES — parameterized binding rejects
    the SQL syntax inside a value position."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    payload = "x'; DROP TABLE tasks;--"
    result = _run_tasks(
        ["done", "myproj", payload], home=tmp_path)

    # CLI exits cleanly (rowcount==0 → "not found" message)
    assert result.returncode == 0
    # Table SURVIVES
    assert _table_exists(db_path, "tasks"), (
        "DROP TABLE injection succeeded — tasks table gone"
    )
    # Real task still present
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT task_id FROM tasks WHERE project = ?", ("myproj",)
    ).fetchall()
    conn.close()
    assert len(rows) == 1
    assert rows[0][0] == "real-task"


def test_drop_table_payload_treated_as_literal_task_id(tmp_path):
    """The injection string lands in the WHERE clause as a
    literal task_id. No row matches → rowcount==0 → "not found"
    output. Pin literal-treatment semantics."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    _seed_db(db_dir / "context.db")

    payload = "x'; DROP TABLE tasks;--"
    result = _run_tasks(
        ["done", "myproj", payload], home=tmp_path)
    assert "not found" in result.stdout.lower()


def test_drop_other_tables_payload_also_blocked(tmp_path):
    """Pin: payload trying to DROP a different sensitive table
    (rules_config). Same defense — parameterized binding."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    payload = "x'; DROP TABLE rules_config;--"
    _run_tasks(["done", "myproj", payload], home=tmp_path)

    assert _table_exists(db_path, "rules_config")
    assert _table_exists(db_path, "tasks")


# ─── Branch (b): UNION SELECT exfiltration ─────────────────────────────


def test_union_select_payload_does_not_exfiltrate(tmp_path):
    """Pin: `' UNION SELECT key, value, ... FROM shared;--` in
    task_id position. UNION-style payload would only matter if
    task_id were interpolated into a SELECT — but `tasks done`
    does UPDATE, so UNION is irrelevant. Still, pin no
    exfiltration occurs."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    # Seed sensitive value in shared table
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO shared (key, value) VALUES (?, ?)",
        ("API_KEY", "sk-secret-token"),
    )
    conn.commit()
    conn.close()

    payload = "x' UNION SELECT key, value, NULL FROM shared;--"
    result = _run_tasks(
        ["done", "myproj", payload], home=tmp_path)

    # No leak to stdout/stderr
    combined = result.stdout + result.stderr
    assert "sk-secret-token" not in combined, (
        "UNION SELECT injection leaked API_KEY value"
    )
    # Tables still intact
    assert _table_exists(db_path, "shared")


def test_union_in_list_command_does_not_exfiltrate(tmp_path):
    """`tasks list` does SELECT — closer to UNION territory.
    But `project` arg is parameterized too. Pin: no leak."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO shared (key, value) VALUES (?, ?)",
        ("PASSWORD", "very-secret"),
    )
    conn.commit()
    conn.close()

    payload = "myproj' UNION SELECT key, value, NULL FROM shared;--"
    result = _run_tasks(["list", payload], home=tmp_path)
    combined = result.stdout + result.stderr
    assert "very-secret" not in combined


# ─── Branch (c): PRAGMA mutation ───────────────────────────────────────


def test_pragma_payload_does_not_change_journal_mode(tmp_path):
    """Pin: payload `'; PRAGMA journal_mode=DELETE;--` in
    task_id. Sqlite ignores PRAGMA inside parameterized values
    — value is the entire literal string, no escape. Pin:
    journal_mode unchanged (stays WAL — set by `tasks` CLI's
    get_db())."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    # First, force WAL mode (matching what `tasks` CLI does)
    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA journal_mode=WAL")
    conn.close()

    payload = "x'; PRAGMA journal_mode=DELETE;--"
    _run_tasks(["done", "myproj", payload], home=tmp_path)

    # Journal mode still WAL (or what `tasks` set, not DELETE)
    conn = sqlite3.connect(str(db_path))
    mode = conn.execute("PRAGMA journal_mode").fetchone()[0]
    conn.close()
    # `tasks` CLI sets WAL on every connect via get_db() —
    # so this stays WAL. Mode change attempt failed.
    assert mode.lower() == "wal", (
        f"journal_mode changed to {mode!r} — PRAGMA injection "
        f"succeeded"
    )


def test_pragma_user_version_unchanged(tmp_path):
    """Pin: a future migration system might rely on user_version
    PRAGMA — pin that injection doesn't mutate it."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    conn = sqlite3.connect(str(db_path))
    conn.execute("PRAGMA user_version = 42")
    conn.commit()
    conn.close()

    payload = "x'; PRAGMA user_version = 99;--"
    _run_tasks(["done", "myproj", payload], home=tmp_path)

    conn = sqlite3.connect(str(db_path))
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    conn.close()
    assert version == 42


# ─── Source-grep: every SQL op uses parameterized binding ──────────────


def test_tasks_cli_does_not_use_string_concatenation_in_sql():
    """Pin: source has NO `f"...{task_id}..."` / `% task_id` /
    `+ task_id` patterns inside SQL strings. All values land
    as `?` placeholders + tuple binding."""
    src = TASKS_CLI.read_text()

    # No f-strings in execute() args
    forbidden = [
        'f"UPDATE',
        "f'UPDATE",
        'f"DELETE',
        "f'DELETE",
        'f"INSERT',
        "f'INSERT",
        'f"SELECT',
        "f'SELECT",
    ]
    for pat in forbidden:
        assert pat not in src, (
            f"tasks CLI uses f-string SQL: {pat!r} — injection "
            f"vulnerability"
        )

    # No % formatting on SQL
    assert "% task_id" not in src
    assert "% project" not in src

    # No + concat with user input into SQL
    assert "task_id +" not in src or '" + task_id +' not in src
    assert "+ project +" not in src


def test_user_input_sql_ops_use_placeholder_binding():
    """Pin: every WHERE clause / VALUES tuple that takes user
    input uses `?` placeholder + tuple binding. Source-grep the
    canonical patterns rather than parsing each db.execute call
    (regex of nested SQL strings + closing parens is fragile)."""
    src = TASKS_CLI.read_text()

    # Canonical user-input WHERE clauses MUST use ?
    where_user_clauses = [
        "WHERE project = ? AND task_id = ?",
        "WHERE project = ?",
        "WHERE task_id = ?",
    ]
    found = [c for c in where_user_clauses if c in src]
    assert found, (
        f"no parameterized WHERE clauses found in tasks CLI — "
        f"likely refactored to string-concat. Patterns checked: "
        f"{where_user_clauses}"
    )

    # No user-input WHERE without ? — grep for `WHERE project =`
    # that ISN'T followed by `?`
    import re
    bad = re.findall(
        r"WHERE\s+\w+\s*=\s*[\"']", src)
    assert not bad, (
        f"WHERE clause uses string literal directly: {bad}"
    )


def test_no_string_concat_or_format_in_sql_ops():
    """Cross-check: grep the file for SQL-shaped strings that
    follow `f`/`%`/`+` patterns. Catches a regression where a
    rapid edit slipped concat into the SQL."""
    src = TASKS_CLI.read_text()
    # No f-string SQL
    assert 'f"UPDATE' not in src
    assert "f'UPDATE" not in src
    assert 'f"DELETE' not in src
    assert 'f"INSERT' not in src
    assert 'f"SELECT' not in src
    # No % SQL formatting
    assert "% (project, task_id)" not in src
    assert '" % task_id' not in src


def test_cmd_done_uses_parameterized_binding_explicitly():
    """Pin the canonical pattern in cmd_done — `?` placeholders +
    tuple `(project, task_id)`."""
    src = TASKS_CLI.read_text()
    fn_idx = src.find("def cmd_done")
    next_def = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_def]

    # UPDATE uses ?
    assert "WHERE project = ? AND task_id = ?" in body
    # The args tuple
    assert "(project, task_id)" in body
    # task_claims DELETE same pattern
    assert "DELETE FROM task_claims WHERE project = ? AND task_id = ?" in body


# ─── Cross: legit task_ids still work after injection attempt ──────────


def test_legit_task_done_still_works_after_injection_attempts(tmp_path):
    """Pin: 5 injection attempts in a row don't corrupt DB
    state — a legit `tasks done myproj real-task` afterwards
    still updates the row."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    payloads = [
        "x'; DROP TABLE tasks;--",
        "x' UNION SELECT * FROM shared;--",
        "x'; PRAGMA journal_mode=DELETE;--",
        "x' OR 1=1;--",
        "x'); DELETE FROM tasks; --",
    ]
    for p in payloads:
        _run_tasks(["done", "myproj", p], home=tmp_path)

    # Legit done still works
    result = _run_tasks(
        ["done", "myproj", "real-task"], home=tmp_path)
    assert result.returncode == 0
    assert "✓ done" in result.stdout

    # DB still clean
    conn = sqlite3.connect(str(db_path))
    row = conn.execute(
        "SELECT status FROM tasks WHERE task_id = ?",
        ("real-task",),
    ).fetchone()
    conn.close()
    assert row is not None
    assert row[0] == "done"


# ─── Branch (a) cross: tasks add command ───────────────────────────────


def test_add_with_crafted_payload_in_description_does_not_drop(tmp_path):
    """Pin: `tasks add myproj "<crafted>" "desc"` — the regex
    `^\\d+(\\.[a-z])?$` rejects non-numeric task_ids, so the
    crafted payload lands in DESCRIPTION (joined args[1:]).
    Either way: parameterized binding stores it as literal text;
    table survives."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    payload = "x'; DROP TABLE tasks;--"
    result = _run_tasks(
        ["add", "myproj", payload, "an injected task"],
        home=tmp_path,
    )
    # Add succeeds; crafted text routed to description, task_id
    # auto-generated
    assert result.returncode == 0

    # Tasks table still exists
    assert _table_exists(db_path, "tasks")

    # Row inserted — search by description containing the
    # injected payload literally
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT task_id, description FROM tasks "
        "WHERE description LIKE ? AND project = ?",
        (f"%{payload}%", "myproj"),
    ).fetchall()
    conn.close()
    assert rows, (
        f"crafted payload not stored as description literal — "
        f"may have been escaped differently, or DROP succeeded"
    )


# ─── tasks delete also parameterized ──────────────────────────────────


def test_delete_with_drop_payload_does_not_drop(tmp_path):
    """Cross-check: `tasks delete` uses same parameterized
    pattern. Pin."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    payload = "x'; DROP TABLE tasks;--"
    _run_tasks(["delete", "myproj", payload], home=tmp_path)
    assert _table_exists(db_path, "tasks")


# ─── tasks list with crafted project name ─────────────────────────────


def test_list_with_crafted_project_name_returns_no_results(tmp_path):
    """`tasks list` SELECTs WHERE project=?. Crafted project
    name lands literal — matches no rows. Pin no leak +
    table integrity."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    payload = "myproj' OR 1=1; --"
    result = _run_tasks(["list", payload], home=tmp_path)

    # tasks for the legitimate myproj NOT leaked under the
    # crafted project_name
    assert "real-task" not in result.stdout
    # Tables intact
    assert _table_exists(db_path, "tasks")


# ─── DB file mode (cross-ref #129) ─────────────────────────────────────


def test_ctx_db_file_path_is_user_isolated(tmp_path):
    """Pin: DB_PATH is `~/.claude-context/context.db` — per-user
    location. A multi-tenant attack would require local FS
    access. Catches a refactor moving it to `/tmp/...` or
    `/var/lib/...` (world-readable)."""
    src = TASKS_CLI.read_text()
    # DB_PATH is under HOME
    assert "Path.home()" in src
    assert ".claude-context" in src
    # NOT under /tmp or /var
    assert 'Path("/tmp' not in src
    assert 'Path("/var' not in src


# ─── Integer-typed args also safe ──────────────────────────────────────


def test_numeric_task_id_is_treated_as_string_via_binding(tmp_path):
    """Pin: task_id "1" (the canonical numeric form) goes
    through ? binding as a STRING. SQLite's TEXT column accepts
    it. No type-coercion shenanigan that would let an integer
    `1; DROP` slip through."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    # Seed a numeric-looking task_id
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO tasks (project, task_id, description) "
        "VALUES (?, ?, ?)",
        ("myproj", "42", "answer"),
    )
    conn.commit()
    conn.close()

    result = _run_tasks(
        ["done", "myproj", "42"], home=tmp_path)
    assert result.returncode == 0
    assert "✓ done" in result.stdout


# ─── Comment-injection variants ────────────────────────────────────────


def test_double_dash_comment_payload_blocked(tmp_path):
    """Pin: `--` SQL comment injection blocked. The payload
    inside ? binding is just text."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    payload = "real-task'-- "  # close quote + comment rest
    _run_tasks(["done", "myproj", payload], home=tmp_path)

    # Real task NOT marked done (literal task_id 'real-task'
    # has no quote or comment, so payload doesn't match)
    conn = sqlite3.connect(str(db_path))
    status = conn.execute(
        "SELECT status FROM tasks WHERE task_id = ?",
        ("real-task",),
    ).fetchone()[0]
    conn.close()
    assert status == "open"


def test_block_comment_payload_blocked(tmp_path):
    """Pin: `/* ... */` SQL block comment injection blocked."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    payload = "x'/*; DROP TABLE tasks;*/--"
    _run_tasks(["done", "myproj", payload], home=tmp_path)
    assert _table_exists(db_path, "tasks")


# ─── SQL-via-ATTACH escalation ────────────────────────────────────────


def test_attach_database_payload_blocked(tmp_path):
    """Pin: `ATTACH DATABASE` injection blocked. Without
    parameterized binding, an attacker could ATTACH a separate
    DB and copy data over. With binding, it lands as literal
    text in WHERE clause."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    payload = "x'; ATTACH DATABASE '/tmp/evil.db' AS evil;--"
    _run_tasks(["done", "myproj", payload], home=tmp_path)

    # Tables still intact + no /tmp/evil.db created
    assert _table_exists(db_path, "tasks")
    assert not Path("/tmp/evil.db").exists()


# ─── Unicode bypass attempts ──────────────────────────────────────────


def test_unicode_quote_smuggling_blocked(tmp_path):
    """Pin: U+2019 (right single quotation mark, looks like ')
    in payload — sqlite3 binding treats every Unicode char as
    literal. Pin no escape attempt succeeds."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path)

    # U+2019 instead of ASCII single quote
    payload = "x’; DROP TABLE tasks;--"
    _run_tasks(["done", "myproj", payload], home=tmp_path)
    assert _table_exists(db_path, "tasks")
