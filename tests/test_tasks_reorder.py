"""TDD: tasks reorder command — written BEFORE implementation.

All tests here should FAIL until:
  - tasks table gains a `position` INTEGER column (migration in init_db)
  - `tasks reorder <project> <task_id> up|down` command is added to tools/tasks
  - sorting in cmd_list / cmd_context respects `position` before `task_id`

Behaviour spec:
  - `tasks reorder <project> <task_id> up`   — swap position with previous task
  - `tasks reorder <project> <task_id> down` — swap position with next task
  - First task + up  → "already first" message, no change
  - Last task  + down → "already last" message, no change
  - Unknown task_id  → error message, exit 1
  - Unknown direction → error message, exit 1
  - Only open tasks participate in positional order relative to each other;
    done tasks keep their position but are always displayed last in list/context.
  - `tasks list` output respects new order after reorder
  - `tasks context` NEXT TASK reflects new order after reorder
  - Newly added tasks get position = max_existing_position + 1
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


# ─── Helpers ────────────────────────────────────────────────────────────


def _seed_db(db_path: Path, project: str = "myproj", tasks: list[tuple] | None = None):
    """Create schema + seed tasks. tasks = [(task_id, description, status), ...]"""
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
        CREATE TABLE shared (key TEXT PRIMARY KEY, value TEXT NOT NULL);
        CREATE TABLE rules_config (
            project TEXT PRIMARY KEY,
            inject_every INTEGER DEFAULT 20,
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


def _get_positions(db_path: Path, project: str) -> dict[str, int | None]:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT task_id, position FROM tasks WHERE project = ? ORDER BY position",
        (project,),
    ).fetchall()
    conn.close()
    return {r[0]: r[1] for r in rows}


def _ordered_task_ids(db_path: Path, project: str) -> list[str]:
    """Return task_ids sorted by position (then task_id as tiebreak)."""
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT task_id, position FROM tasks WHERE project = ? "
        "ORDER BY position NULLS LAST, task_id",
        (project,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


# ─── Schema: position column exists ─────────────────────────────────────


def test_init_db_creates_position_column(tmp_path):
    """After init_db(), tasks table must have a `position` column."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    _run(["list", "noproject"], home=tmp_path)  # triggers init_db()
    db_path = db_dir / "context.db"
    conn = sqlite3.connect(str(db_path))
    cols = [r[1] for r in conn.execute("PRAGMA table_info(tasks)").fetchall()]
    conn.close()
    assert "position" in cols, f"position column missing from tasks table; columns: {cols}"


def test_add_new_task_assigns_position(tmp_path):
    """tasks add assigns an ascending position to each new task."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[])

    _run(["add", "myproj", "first task"], home=tmp_path)
    _run(["add", "myproj", "second task"], home=tmp_path)
    _run(["add", "myproj", "third task"], home=tmp_path)

    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT position FROM tasks WHERE project='myproj' ORDER BY position"
    ).fetchall()
    conn.close()
    positions = [r[0] for r in rows]
    assert None not in positions, "Some tasks have NULL position after add"
    assert positions == sorted(positions), "Positions not monotonically increasing"
    assert len(set(positions)) == len(positions), "Duplicate positions found"


# ─── Basic reorder: up and down ─────────────────────────────────────────


def test_reorder_down_moves_task_later(tmp_path):
    """tasks reorder myproj 1 down swaps task 1 with task 2."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[("1", "alpha", "open"), ("2", "beta", "open"), ("3", "gamma", "open")])

    result = _run(["reorder", "myproj", "1", "down"], home=tmp_path)
    assert result.returncode == 0, f"reorder failed: {result.stderr}"

    order = _ordered_task_ids(db_path, "myproj")
    assert order.index("2") < order.index("1"), (
        f"task '1' should be after '2' after moving down, got order: {order}"
    )
    assert order.index("1") < order.index("3"), (
        f"task '1' should still be before '3', got order: {order}"
    )


def test_reorder_up_moves_task_earlier(tmp_path):
    """tasks reorder myproj 3 up swaps task 3 with task 2."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[("1", "alpha", "open"), ("2", "beta", "open"), ("3", "gamma", "open")])

    result = _run(["reorder", "myproj", "3", "up"], home=tmp_path)
    assert result.returncode == 0, f"reorder failed: {result.stderr}"

    order = _ordered_task_ids(db_path, "myproj")
    assert order.index("3") < order.index("2"), (
        f"task '3' should be before '2' after moving up, got order: {order}"
    )
    assert order.index("1") < order.index("3"), (
        f"task '1' should still be before '3', got order: {order}"
    )


def test_reorder_up_down_is_reversible(tmp_path):
    """Move task down then up restores original order."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    tasks = [("1", "alpha", "open"), ("2", "beta", "open"), ("3", "gamma", "open")]
    _seed_db(db_path, tasks=tasks)
    original = _ordered_task_ids(db_path, "myproj")

    _run(["reorder", "myproj", "2", "down"], home=tmp_path)
    _run(["reorder", "myproj", "2", "up"], home=tmp_path)

    restored = _ordered_task_ids(db_path, "myproj")
    assert restored == original, f"Order not restored: {restored} vs {original}"


# ─── Boundary: first/last ────────────────────────────────────────────────


def test_reorder_up_on_first_task_prints_already_first(tmp_path):
    """Moving the first task up prints a 'already first' message."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[("1", "alpha", "open"), ("2", "beta", "open")])

    order_before = _ordered_task_ids(db_path, "myproj")
    first_id = order_before[0]

    result = _run(["reorder", "myproj", first_id, "up"], home=tmp_path)
    assert result.returncode == 0
    assert "already" in result.stdout.lower(), (
        f"Expected 'already first' message, got: {result.stdout!r}"
    )
    assert _ordered_task_ids(db_path, "myproj") == order_before, "Order changed unexpectedly"


def test_reorder_down_on_last_task_prints_already_last(tmp_path):
    """Moving the last task down prints a 'already last' message."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[("1", "alpha", "open"), ("2", "beta", "open")])

    order_before = _ordered_task_ids(db_path, "myproj")
    last_id = order_before[-1]

    result = _run(["reorder", "myproj", last_id, "down"], home=tmp_path)
    assert result.returncode == 0
    assert "already" in result.stdout.lower(), (
        f"Expected 'already last' message, got: {result.stdout!r}"
    )
    assert _ordered_task_ids(db_path, "myproj") == order_before, "Order changed unexpectedly"


def test_reorder_single_task_both_directions(tmp_path):
    """Single task: up and down both print 'already' message."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[("1", "only task", "open")])

    for direction in ("up", "down"):
        result = _run(["reorder", "myproj", "1", direction], home=tmp_path)
        assert result.returncode == 0
        assert "already" in result.stdout.lower(), (
            f"direction={direction}: expected 'already' message, got: {result.stdout!r}"
        )


# ─── Error cases ─────────────────────────────────────────────────────────


def test_reorder_unknown_task_id_exits_1(tmp_path):
    """Reordering non-existent task_id → exit 1 + error message."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    _seed_db(tmp_path / ".claude-context" / "context.db",
             tasks=[("1", "alpha", "open")])

    result = _run(["reorder", "myproj", "99", "up"], home=tmp_path)
    assert result.returncode == 1, f"Expected exit 1, got {result.returncode}"
    assert "not found" in result.stdout.lower() or "not found" in result.stderr.lower()


def test_reorder_invalid_direction_exits_1(tmp_path):
    """Invalid direction (not up/down) → exit 1."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    _seed_db(tmp_path / ".claude-context" / "context.db",
             tasks=[("1", "alpha", "open")])

    result = _run(["reorder", "myproj", "1", "sideways"], home=tmp_path)
    assert result.returncode == 1


def test_reorder_missing_args_exits_1(tmp_path):
    """Too few args → exit 1 + usage message."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()

    result = _run(["reorder", "myproj"], home=tmp_path)
    assert result.returncode == 1
    combined = result.stdout + result.stderr
    assert "usage" in combined.lower() or "reorder" in combined.lower()


def test_reorder_no_args_exits_1(tmp_path):
    """No args after reorder → exit 1."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()

    result = _run(["reorder"], home=tmp_path)
    assert result.returncode == 1


# ─── Integration with list / context ─────────────────────────────────────


def test_list_output_reflects_reorder(tmp_path):
    """After reorder, tasks list shows tasks in new order."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
    ])

    _run(["reorder", "myproj", "3", "up"], home=tmp_path)
    _run(["reorder", "myproj", "3", "up"], home=tmp_path)

    result = _run(["list", "myproj"], home=tmp_path)
    lines = [l for l in result.stdout.splitlines() if l.strip()]
    task_lines = [l for l in lines if any(tid in l for tid in ("1", "2", "3"))]

    pos_3 = next((i for i, l in enumerate(task_lines) if "3" in l and "gamma" in l), None)
    pos_1 = next((i for i, l in enumerate(task_lines) if "1" in l and "alpha" in l), None)
    assert pos_3 is not None and pos_1 is not None
    assert pos_3 < pos_1, (
        f"task 3 (moved to top) should appear before task 1 in list output\n"
        f"list output:\n{result.stdout}"
    )


def test_context_next_task_reflects_reorder(tmp_path):
    """After reorder, tasks context picks the new first task as NEXT TASK."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
    ])

    _run(["reorder", "myproj", "3", "up"], home=tmp_path)
    _run(["reorder", "myproj", "3", "up"], home=tmp_path)

    result = _run(["context", "myproj"], home=tmp_path)
    next_line = next(
        (l for l in result.stdout.splitlines() if "NEXT TASK" in l), None
    )
    assert next_line is not None, f"No NEXT TASK line in context output:\n{result.stdout}"
    assert "3" in next_line and "gamma" in next_line, (
        f"Expected task 3 as NEXT TASK after moving it to top, got: {next_line!r}"
    )


# ─── Reorder skips done tasks ────────────────────────────────────────────


def test_reorder_down_skips_over_done_tasks(tmp_path):
    """Reorder swaps within open tasks only; done tasks at bottom don't interfere."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "done"),
        ("3", "gamma", "open"),
    ])

    # task 1 "down" should swap with task 3 (the next open task), not task 2 (done)
    result = _run(["reorder", "myproj", "1", "down"], home=tmp_path)
    assert result.returncode == 0

    # After reorder: among open tasks, gamma should come before alpha
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT task_id, position FROM tasks "
        "WHERE project='myproj' AND status='open' ORDER BY position",
        ).fetchall()
    conn.close()
    open_ids = [r[0] for r in rows]
    assert open_ids.index("3") < open_ids.index("1"), (
        f"Expected '3' before '1' among open tasks after moving '1' down, got: {open_ids}"
    )


def test_reorder_up_on_first_open_task_when_done_tasks_exist(tmp_path):
    """First open task moving up should say 'already first' even if done tasks exist above."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    _seed_db(db_path, tasks=[
        ("1", "alpha", "done"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
    ])

    result = _run(["reorder", "myproj", "2", "up"], home=tmp_path)
    assert result.returncode == 0
    assert "already" in result.stdout.lower(), (
        f"Expected 'already first' for first open task, got: {result.stdout!r}"
    )


# ─── help text includes reorder ──────────────────────────────────────────


def test_help_mentions_reorder():
    """tasks --help output lists the reorder command."""
    result = subprocess.run(
        [sys.executable, str(TASKS_CLI), "--help"],
        capture_output=True, text=True, timeout=5,
    )
    combined = result.stdout + result.stderr
    assert "reorder" in combined.lower(), (
        f"'reorder' not mentioned in help output:\n{combined}"
    )


# ─── Source: reorder registered in COMMANDS dict ─────────────────────────


def test_reorder_command_registered_in_commands_dict():
    """Source check: 'reorder' key appears in the COMMANDS dict."""
    src = TASKS_CLI.read_text()
    assert '"reorder"' in src or "'reorder'" in src, (
        "reorder not registered in COMMANDS dict in tools/tasks"
    )


def test_reorder_uses_parameterized_sql():
    """Source check: reorder implementation uses ? placeholders, not f-strings."""
    src = TASKS_CLI.read_text()
    reorder_idx = src.find("def cmd_reorder")
    assert reorder_idx != -1, "cmd_reorder function not found in tools/tasks"
    next_def = src.find("\ndef ", reorder_idx + 1)
    body = src[reorder_idx: next_def if next_def != -1 else len(src)]

    assert 'f"UPDATE' not in body
    assert "f'UPDATE" not in body
    assert "WHERE project = ? AND task_id = ?" in body or "position = ?" in body, (
        "cmd_reorder should use parameterized WHERE clauses"
    )
