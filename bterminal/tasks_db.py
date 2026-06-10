"""Shared tasks-database helpers used by the GTK panel and the standalone CLI.

Imported by:
  - bterminal/ui/panels/tasks.py (the in-app TaskListPanel)
  - tools/tasks                  (the standalone CLI; falls back to inline
                                  copies if the bterminal package is not on
                                  sys.path)

Keep the public API minimal — drift between the two callers caused real
bugs in the past (see PR #6).
"""


def task_sort_key(task_id):
    """Natural sort key for hierarchical task IDs like 1, 1.a, 1.b, 2, 10."""
    parts = task_id.split(".")
    result = []
    for p in parts:
        try:
            result.append((0, int(p), ""))
        except ValueError:
            result.append((1, 0, p))
    return result


def migrate_add_position(db):
    """Add `position` column to tasks table (idempotent). Backfills NULLs."""
    cols = [r[1] for r in db.execute("PRAGMA table_info(tasks)").fetchall()]
    if "position" not in cols:
        db.execute("ALTER TABLE tasks ADD COLUMN position INTEGER")
        db.commit()
    backfill_positions(db)


def backfill_positions(db):
    """Assign sequential position to tasks with NULL position.

    Natural-sorted so (1, 1.a, 2, 3, 10) gets sensible order, not insertion.
    """
    projects = [
        r[0] for r in db.execute(
            "SELECT DISTINCT project FROM tasks WHERE position IS NULL"
        ).fetchall()
    ]
    for project in projects:
        null_rows = db.execute(
            "SELECT task_id FROM tasks WHERE project = ? AND position IS NULL",
            (project,),
        ).fetchall()
        max_row = db.execute(
            "SELECT MAX(position) FROM tasks WHERE project = ? AND position IS NOT NULL",
            (project,),
        ).fetchone()
        start = (max_row[0] or 0) + 1
        for i, tid in enumerate(
            sorted([r[0] for r in null_rows], key=task_sort_key), start=start
        ):
            db.execute(
                "UPDATE tasks SET position = ? WHERE project = ? AND task_id = ? AND position IS NULL",
                (i, project, tid),
            )
    if projects:
        db.commit()
