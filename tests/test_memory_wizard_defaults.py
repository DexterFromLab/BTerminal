"""Pin tests for BUG#29 — memory_wizard self-contradiction defaults.

Before the fix:
  tools/memory_wizard:856 returned (20, 50)   (in _configure_intervals)
  tools/memory_wizard:1082 returned (100, 200) (in _read_inject_config)
Same script, same project state, two different default pairs depending
on which entry point the caller used. This left users staring at
conflicting numbers without any audit trail.

These tests pin all three paths (`_configure_intervals` initial values,
`_read_inject_config` no-DB, `_read_inject_config` no-row) to the
canonical constants from bterminal.providers.ctx_defaults.
"""
from __future__ import annotations

import importlib.util
import sqlite3
from importlib.machinery import SourceFileLoader
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from bterminal.providers.ctx_defaults import (  # noqa: E402
    DEFAULT_INJECT_EVERY,
    DEFAULT_REFRESH_EVERY,
)


def _load_memory_wizard():
    """Import tools/memory_wizard as a module so we can call private
    helpers and inspect module-level constants. The script has no .py
    extension so we need an explicit SourceFileLoader."""
    here = REPO_ROOT / "tools" / "memory_wizard"
    loader = SourceFileLoader("_memory_wizard_under_test", str(here))
    spec = importlib.util.spec_from_loader(
        "_memory_wizard_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


# ─── Module-level constants agree with the package ──────────────────────


def test_memory_wizard_imports_canonical_defaults():
    """Pin: the wizard must read DEFAULT_INJECT_EVERY from
    bterminal.providers.ctx_defaults. The path-bootstrap fallback (when
    the package isn't importable) must match the same literal."""
    mod = _load_memory_wizard()
    assert mod.DEFAULT_INJECT_EVERY == DEFAULT_INJECT_EVERY
    assert mod.DEFAULT_REFRESH_EVERY == DEFAULT_REFRESH_EVERY


# ─── _read_inject_config — three branches ──────────────────────────────


def test_read_inject_config_no_db_returns_canonical_defaults(tmp_path):
    """Pin: branch 1 — CTX_DB doesn't exist. Before fix this returned
    (100, 200); only by coincidence did it agree with the UI here.
    After fix it returns the constant explicitly."""
    mod = _load_memory_wizard()
    fake_db = tmp_path / "does_not_exist.db"
    with patch.object(mod, "CTX_DB", fake_db):
        got = mod._read_inject_config("any_project")
    assert got == (DEFAULT_INJECT_EVERY, DEFAULT_REFRESH_EVERY)


def test_read_inject_config_db_exists_no_row_returns_canonical_defaults(
        tmp_path):
    """Pin: branch 2 — CTX_DB exists but no row for this project. This
    was the same `return 100, 200` line; pinning it here so a future
    refactor that splits the branches doesn't drift one of them."""
    db_path = tmp_path / "context.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE rules_config ("
            " project TEXT PRIMARY KEY,"
            " inject_every INTEGER NOT NULL,"
            " refresh_every INTEGER NOT NULL)"
        )
    mod = _load_memory_wizard()
    with patch.object(mod, "CTX_DB", db_path):
        got = mod._read_inject_config("missing_project")
    assert got == (DEFAULT_INJECT_EVERY, DEFAULT_REFRESH_EVERY)


def test_read_inject_config_returns_existing_row_unchanged(tmp_path):
    """Pin: when the user has saved values (project, 50, 80) via
    Memory panel Apply, the wizard must surface THOSE — never
    overwrite with defaults. Reproduces the worst-case BUG#29
    failure mode: wizard 'forgetting' the user's tuning."""
    db_path = tmp_path / "context.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE rules_config ("
            " project TEXT PRIMARY KEY,"
            " inject_every INTEGER NOT NULL,"
            " refresh_every INTEGER NOT NULL)"
        )
        conn.execute(
            "INSERT INTO rules_config (project, inject_every, refresh_every)"
            " VALUES (?, ?, ?)",
            ("proj_with_saved", 50, 80),
        )
        conn.commit()
    mod = _load_memory_wizard()
    with patch.object(mod, "CTX_DB", db_path):
        got = mod._read_inject_config("proj_with_saved")
    assert got == (50, 80)


# ─── _configure_intervals starting values ──────────────────────────────


def test_configure_intervals_initial_values_match_canonical_defaults(
        tmp_path):
    """Pin: when `_configure_intervals` runs against a project with no
    rules_config row, the initial 'Current: inject every N prompts'
    line printed to the user must reflect the canonical default, not
    the legacy 20/50 hardcode. We can't easily capture stdout from
    an interactive `input()` call, so we patch input to immediately
    decline the change and read the returned tuple."""
    db_path = tmp_path / "context.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "CREATE TABLE rules_config ("
            " project TEXT PRIMARY KEY,"
            " inject_every INTEGER NOT NULL,"
            " refresh_every INTEGER NOT NULL)"
        )
    mod = _load_memory_wizard()
    # Decline interactive prompts → function returns current values.
    with patch.object(mod, "CTX_DB", db_path), \
         patch("builtins.input", return_value=""):
        got = mod._configure_intervals("any_project")
    assert got == (DEFAULT_INJECT_EVERY, DEFAULT_REFRESH_EVERY)


# ─── Negative: no stale literals lingering in the source ───────────────


def test_no_legacy_2050_hardcoded_pair_remains_in_memory_wizard():
    """Pin: defensive — ensure the literal pair (20, 50) doesn't
    sneak back into memory_wizard via copy-paste. If a future
    contributor reintroduces it, this test catches it. The check
    is intentionally narrow (the exact tuple form `20, 50`) so it
    doesn't flag unrelated occurrences."""
    src = (REPO_ROOT / "tools" / "memory_wizard").read_text(encoding="utf-8")
    # Must not have either `= 20, 50` or `return 20, 50` etc.
    forbidden = ["= 20, 50", "return 20, 50", "(20, 50)"]
    for needle in forbidden:
        assert needle not in src, (
            f"BUG#29 regressed — '{needle}' found in tools/memory_wizard. "
            f"Use DEFAULT_INJECT_EVERY / DEFAULT_REFRESH_EVERY instead."
        )
