"""TDD: tasks move command — written BEFORE implementation.

All tests here should FAIL until:
  - `tasks move <project> <task_id> <target_position>` is added to tools/tasks
  - The command renumbers all same-status peers so positions stay contiguous
  - It is registered in COMMANDS and listed in help text

Behaviour spec:
  - target_position is 1-based; clamp below 1 → 1, above count → last
  - Operates within the same-status group (open vs done)
  - After move, ALL peers get sequential integers (no gaps)
  - Output: "[myproj] Task <id> moved to position <n>"
  - Unknown task_id → exit 1 + "Error: Task ... not found"
  - Missing args → exit 1 + usage hint
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


# ─── Helpers (mirrors test_tasks_reorder.py) ────────────────────────────────


def _seed_db(db_path: Path, project: str = "myproj",
             tasks: list[tuple] | None = None) -> None:
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL,
            task_id TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            position INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(project, task_id)
        );
        CREATE TABLE task_config (project TEXT PRIMARY KEY, autorun INTEGER DEFAULT 0);
        CREATE TABLE task_claims (
            project TEXT NOT NULL, task_id TEXT NOT NULL, session_id TEXT NOT NULL,
            PRIMARY KEY (project, task_id, session_id)
        );
        CREATE TABLE sessions (name TEXT PRIMARY KEY, description TEXT, work_dir TEXT);
        CREATE TABLE contexts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL,
            key TEXT NOT NULL, value TEXT NOT NULL, UNIQUE(project, key)
        );
        CREATE TABLE shared (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE rules_config (
            project TEXT PRIMARY KEY, inject_every INTEGER DEFAULT 20,
            refresh_every INTEGER DEFAULT 50
        );
    """)
    if tasks:
        for pos, (task_id, desc, status) in enumerate(tasks, start=1):
            conn.execute(
                "INSERT INTO tasks (project, task_id, description, status, position) "
                "VALUES (?, ?, ?, ?, ?)",
                (project, task_id, desc, status, pos),
            )
    conn.commit()
    conn.close()


def _run(args: list[str], home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        [sys.executable, str(TASKS_CLI)] + args,
        env=env, capture_output=True, text=True, timeout=10,
    )


def _ordered_open_ids(db_path: Path, project: str) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT task_id FROM tasks WHERE project = ? AND status = 'open' "
        "ORDER BY position NULLS LAST, task_id",
        (project,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


def _positions(db_path: Path, project: str) -> dict[str, int | None]:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT task_id, position FROM tasks WHERE project = ?", (project,)
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


# ─── Source-level checks ─────────────────────────────────────────────────────


def test_move_command_registered_in_commands_dict():
    """Source check: 'move' key appears in the COMMANDS dict."""
    src = TASKS_CLI.read_text()
    assert '"move"' in src or "'move'" in src, (
        "'move' not registered in COMMANDS dict in tools/tasks"
    )


def test_move_function_exists_in_source():
    """Source check: cmd_move function is defined."""
    src = TASKS_CLI.read_text()
    assert "def cmd_move" in src, "cmd_move function not found in tools/tasks"


def test_move_uses_parameterized_sql():
    """Source check: cmd_move uses ? placeholders, not f-string SQL."""
    src = TASKS_CLI.read_text()
    idx = src.find("def cmd_move")
    assert idx != -1, "cmd_move not found"
    next_def = src.find("\ndef ", idx + 1)
    body = src[idx: next_def if next_def != -1 else len(src)]
    assert 'f"UPDATE' not in body and "f'UPDATE" not in body, (
        "f-string SQL in cmd_move — injection risk"
    )


# ─── Core move behaviour ─────────────────────────────────────────────────────


def test_move_to_front(tmp_path):
    """tasks move myproj 3 1 makes task 3 the first open task."""
    db_dir = tmp_path / ".claude-context"; db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
    ])

    result = _run(["move", "myproj", "3", "1"], home=tmp_path)
    assert result.returncode == 0, f"move failed: {result.stderr}"

    order = _ordered_open_ids(db_path, "myproj")
    assert order[0] == "3", f"Expected task '3' at position 1, got order: {order}"
    assert order == ["3", "1", "2"], f"Unexpected order: {order}"


def test_move_to_end(tmp_path):
    """tasks move myproj 1 99 makes task 1 the last open task."""
    db_dir = tmp_path / ".claude-context"; db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
    ])

    result = _run(["move", "myproj", "1", "99"], home=tmp_path)
    assert result.returncode == 0, f"move failed: {result.stderr}"

    order = _ordered_open_ids(db_path, "myproj")
    assert order[-1] == "1", f"Expected task '1' at last position, got order: {order}"
    assert order == ["2", "3", "1"], f"Unexpected order: {order}"


def test_move_to_middle(tmp_path):
    """tasks move myproj 4 2 places task 4 at position 2 of 4."""
    db_dir = tmp_path / ".claude-context"; db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
        ("4", "delta", "open"),
    ])

    result = _run(["move", "myproj", "4", "2"], home=tmp_path)
    assert result.returncode == 0, f"move failed: {result.stderr}"

    order = _ordered_open_ids(db_path, "myproj")
    assert order == ["1", "4", "2", "3"], f"Unexpected order after move to middle: {order}"


def test_move_clamp_below_1(tmp_path):
    """target_position < 1 is treated as 1 (move to front)."""
    db_dir = tmp_path / ".claude-context"; db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
    ])

    result = _run(["move", "myproj", "3", "0"], home=tmp_path)
    assert result.returncode == 0, f"move failed: {result.stderr}"

    order = _ordered_open_ids(db_path, "myproj")
    assert order[0] == "3", f"task '3' should be first after move to 0 (clamped to 1), got: {order}"


def test_move_clamp_above_max(tmp_path):
    """target_position > task count is treated as last."""
    db_dir = tmp_path / ".claude-context"; db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
    ])

    result = _run(["move", "myproj", "2", "100"], home=tmp_path)
    assert result.returncode == 0, f"move failed: {result.stderr}"

    order = _ordered_open_ids(db_path, "myproj")
    assert order[-1] == "2", f"task '2' should be last after move to 100 (clamped to end), got: {order}"


def test_move_positions_are_contiguous_after_move(tmp_path):
    """After move, all peers have sequential integer positions with no gaps."""
    db_dir = tmp_path / ".claude-context"; db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
        ("4", "delta", "open"),
    ])

    _run(["move", "myproj", "4", "2"], home=tmp_path)

    conn = sqlite3.connect(str(db_path))
    positions = sorted(
        r[0] for r in conn.execute(
            "SELECT position FROM tasks WHERE project='myproj' AND status='open'"
        ).fetchall()
    )
    conn.close()
    assert positions == list(range(1, len(positions) + 1)), (
        f"Positions not contiguous after move: {positions}"
    )


def test_move_within_open_only(tmp_path):
    """move operates within open tasks only; done tasks are unaffected."""
    db_dir = tmp_path / ".claude-context"; db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "done"),
        ("3", "gamma", "open"),
        ("4", "delta", "open"),
    ])

    result = _run(["move", "myproj", "4", "1"], home=tmp_path)
    assert result.returncode == 0

    conn = sqlite3.connect(str(db_path))
    open_order = [
        r[0] for r in conn.execute(
            "SELECT task_id FROM tasks WHERE project='myproj' AND status='open' "
            "ORDER BY position NULLS LAST, task_id"
        ).fetchall()
    ]
    done_pos_before = 2  # task 2 was seeded with position=2
    done_pos_after = conn.execute(
        "SELECT position FROM tasks WHERE project='myproj' AND task_id='2'"
    ).fetchone()[0]
    conn.close()

    assert open_order[0] == "4", f"task 4 should be first open task, got: {open_order}"
    assert done_pos_after == done_pos_before, (
        f"done task position should be unchanged: {done_pos_before} → {done_pos_after}"
    )


# ─── Output format ───────────────────────────────────────────────────────────


def test_move_output_message(tmp_path):
    """tasks move prints '[project] Task <id> moved to position <n>'."""
    db_dir = tmp_path / ".claude-context"; db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
    ])

    result = _run(["move", "myproj", "3", "1"], home=tmp_path)
    assert result.returncode == 0
    assert "3" in result.stdout, f"task id missing from output: {result.stdout!r}"
    assert "1" in result.stdout, f"target position missing from output: {result.stdout!r}"
    assert "myproj" in result.stdout, f"project missing from output: {result.stdout!r}"


# ─── Error cases ─────────────────────────────────────────────────────────────


def test_move_unknown_task_exits_1(tmp_path):
    """Moving non-existent task_id → exit 1 + error message."""
    db_dir = tmp_path / ".claude-context"; db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[("1", "alpha", "open")])

    result = _run(["move", "myproj", "99", "1"], home=tmp_path)
    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
    combined = result.stdout + result.stderr
    assert "not found" in combined.lower() or "error" in combined.lower(), (
        f"Expected error message, got: {combined!r}"
    )


def test_move_missing_args_exits_1(tmp_path):
    """Too few args → exit 1 + usage hint."""
    db_dir = tmp_path / ".claude-context"; db_dir.mkdir()

    result = _run(["move", "myproj", "1"], home=tmp_path)
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "usage" in combined.lower() or "move" in combined.lower()


def test_move_non_integer_position_exits_1(tmp_path):
    """Non-integer target_position → exit 1."""
    db_dir = tmp_path / ".claude-context"; db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[("1", "alpha", "open")])

    result = _run(["move", "myproj", "1", "abc"], home=tmp_path)
    assert result.returncode == 1


# ─── Integration with list / context ─────────────────────────────────────────


def test_list_output_reflects_move(tmp_path):
    """After move, tasks list shows tasks in new order."""
    db_dir = tmp_path / ".claude-context"; db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
    ])

    _run(["move", "myproj", "3", "1"], home=tmp_path)

    result = _run(["list", "myproj"], home=tmp_path)
    lines = result.stdout.splitlines()
    task_lines = [l for l in lines if any(t in l for t in ("alpha", "beta", "gamma"))]

    pos_3 = next((i for i, l in enumerate(task_lines) if "gamma" in l), None)
    pos_1 = next((i for i, l in enumerate(task_lines) if "alpha" in l), None)
    assert pos_3 is not None and pos_1 is not None, (
        f"Could not find task lines in output:\n{result.stdout}"
    )
    assert pos_3 < pos_1, (
        f"task 3 (moved to front) should appear before task 1\noutput:\n{result.stdout}"
    )


def test_context_next_task_reflects_move(tmp_path):
    """After move, tasks context picks the new first task as NEXT TASK."""
    db_dir = tmp_path / ".claude-context"; db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
    ])

    _run(["move", "myproj", "3", "1"], home=tmp_path)

    result = _run(["context", "myproj"], home=tmp_path)
    next_line = next(
        (l for l in result.stdout.splitlines() if "NEXT TASK" in l), None
    )
    assert next_line is not None, f"No NEXT TASK line:\n{result.stdout}"
    assert "3" in next_line and "gamma" in next_line, (
        f"Expected task 3 as NEXT TASK after move, got: {next_line!r}"
    )


# ─── Help text ───────────────────────────────────────────────────────────────


def test_help_mentions_move():
    """tasks --help output lists the move command."""
    result = subprocess.run(
        [sys.executable, str(TASKS_CLI), "--help"],
        capture_output=True, text=True, timeout=5,
    )
    combined = result.stdout + result.stderr
    assert "move" in combined.lower(), (
        f"'move' not mentioned in help output:\n{combined}"
    )
