"""Pin tests for BUG#31 — rules_config schema audit.

The schema used to carry `DEFAULT 20` / `DEFAULT 50` on the
inject_every / refresh_every columns. Empirically NO caller ever
relied on those: every INSERT in tools/ctx, memory.py, and
memory_wizard passes the values explicitly. The DEFAULT clause was
silent dead code — and worse, it disagreed with the Python-side
constants (DEFAULT_INJECT_EVERY=100 from BUG#28).

The fix:
  * remove the DEFAULT clauses (NOT NULL bare)
  * rely on `_rules_inject` auto-UPSERT (BUG#28) to seed missing rows
  * existing DBs from before the fix keep working: their existing
    rows have valid values; new INSERTs from BT are still explicit

These tests pin all three claims.
"""
from __future__ import annotations

import os
import sqlite3
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from bterminal.providers.ctx_defaults import (  # noqa: E402
    DEFAULT_INJECT_EVERY,
    DEFAULT_REFRESH_EVERY,
)


def _ctx_invoke(args, env_extra=None):
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "ctx"), *args],
        env=env, capture_output=True, text=True, timeout=10,
    )


# ─── New project auto-creates row ────────────────────────────────────────


def test_new_project_auto_creates_rules_config_row(tmp_path):
    """Pin: a project that has never been touched gets a rules_config
    row with the canonical defaults the first time `ctx rules inject`
    fires. This is what makes Memory panel + intro prompt agree
    without the user having to press Apply."""
    env = {"HOME": str(tmp_path)}
    _ctx_invoke(["set", "test_new_proj_audit", "k", "v"], env_extra=env)
    _ctx_invoke(["rules", "add", "test_new_proj_audit", "Pin rule"],
                env_extra=env)

    db_path = tmp_path / ".claude-context" / "context.db"
    # Pre-check: no row yet.
    with sqlite3.connect(db_path) as conn:
        before = conn.execute(
            "SELECT 1 FROM rules_config WHERE project = ?",
            ("test_new_proj_audit",),
        ).fetchone()
    assert before is None

    _ctx_invoke(["rules", "inject", "test_new_proj_audit"], env_extra=env)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT inject_every, refresh_every FROM rules_config "
            "WHERE project = ?",
            ("test_new_proj_audit",),
        ).fetchone()
    assert row == (DEFAULT_INJECT_EVERY, DEFAULT_REFRESH_EVERY)


# ─── Existing project is not touched ─────────────────────────────────────


def test_existing_project_row_preserved_across_inject(tmp_path):
    """Pin: a project with explicit Apply-saved values (e.g. 75, 150)
    must not be reset by a subsequent `ctx rules inject`. INSERT OR
    IGNORE is correct; INSERT OR REPLACE would silently wipe user
    tuning every prompt cycle."""
    env = {"HOME": str(tmp_path)}
    _ctx_invoke(["set", "existing_proj", "k", "v"], env_extra=env)
    _ctx_invoke(["rules", "add", "existing_proj", "rule"], env_extra=env)

    db_path = tmp_path / ".claude-context" / "context.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO rules_config (project, inject_every, refresh_every) "
            "VALUES (?, ?, ?)",
            ("existing_proj", 75, 150),
        )
        conn.commit()

    _ctx_invoke(["rules", "inject", "existing_proj"], env_extra=env)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT inject_every, refresh_every FROM rules_config "
            "WHERE project = ?",
            ("existing_proj",),
        ).fetchone()
    assert row == (75, 150)


# ─── Schema without DEFAULT rejects bare INSERTs ─────────────────────────


def test_schema_without_default_rejects_bare_insert(tmp_path):
    """Pin: with the DEFAULT clauses gone (BUG#31), a bare
    `INSERT INTO rules_config (project) VALUES (?)` must fail with
    a NOT NULL constraint. This is the *point* of the refactor —
    catch future code that forgets to pass explicit values, rather
    than silently storing a schema-time fallback that drifts from
    DEFAULT_INJECT_EVERY."""
    env = {"HOME": str(tmp_path)}
    _ctx_invoke(["set", "schema_test", "k", "v"], env_extra=env)

    db_path = tmp_path / ".claude-context" / "context.db"
    with sqlite3.connect(db_path) as conn:
        with pytest.raises(sqlite3.IntegrityError) as exc:
            conn.execute(
                "INSERT INTO rules_config (project) VALUES (?)",
                ("schema_test",),
            )
            conn.commit()
        assert "NOT NULL" in str(exc.value), (
            f"Expected NOT NULL constraint violation; got: {exc.value}"
        )


# ─── Migration safety: existing DBs from before the fix still work ───────


def test_existing_db_with_old_default_clauses_still_works(tmp_path):
    """Pin: a user upgrading BT brings their existing context.db along.
    Old schema had DEFAULT 20/50 (or DEFAULT 100/200 post-BUG#28).
    SQLite's `CREATE TABLE IF NOT EXISTS` is a no-op when the table
    already exists, so the schema doesn't change in-place — but the
    rows already in the DB stay valid (they have explicit values).
    New rows from BT also have explicit values. So legacy DBs work
    transparently."""
    db_path = tmp_path / "context.db"
    # Simulate a legacy DB built with the old schema that had DEFAULT 20/50.
    with sqlite3.connect(db_path) as conn:
        conn.executescript("""
            CREATE TABLE rules_config (
                project TEXT PRIMARY KEY,
                inject_every INTEGER NOT NULL DEFAULT 20,
                refresh_every INTEGER NOT NULL DEFAULT 50,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL,
                rule TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
            INSERT INTO rules_config (project, inject_every, refresh_every)
                VALUES ('legacy_proj', 100, 200);
            INSERT INTO rules (project, rule) VALUES ('legacy_proj', 'r1');
        """)
    # Now hand BT this DB and let the auto-UPSERT logic run.
    # We simulate the same INSERT OR IGNORE that _rules_inject performs:
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT OR IGNORE INTO rules_config "
            "(project, inject_every, refresh_every) VALUES (?, ?, ?)",
            ("legacy_proj", DEFAULT_INJECT_EVERY, DEFAULT_REFRESH_EVERY),
        )
        conn.execute(
            "INSERT OR IGNORE INTO rules_config "
            "(project, inject_every, refresh_every) VALUES (?, ?, ?)",
            ("fresh_proj", DEFAULT_INJECT_EVERY, DEFAULT_REFRESH_EVERY),
        )
        conn.commit()
        rows = sorted(conn.execute(
            "SELECT project, inject_every, refresh_every FROM rules_config"
        ).fetchall())
    assert rows == [
        ("fresh_proj", DEFAULT_INJECT_EVERY, DEFAULT_REFRESH_EVERY),
        ("legacy_proj", 100, 200),  # preserved
    ]
