"""Pin tests for BUG#28 — rules_config defaults consistency.

Drift between Memory panel (UI), terminal_tab (runtime), and tools/ctx
(CLI used by intro prompt builder) caused the user-reported symptom:
Memory panel claimed "Inject rules every: 100" but the intro prompt
header said "co 20 promptów". These tests pin all four call sites to
the same canonical value from bterminal.providers.ctx_defaults.
"""
from __future__ import annotations

import importlib.util
import os
import re
import sqlite3
import subprocess
import sys
from importlib.machinery import SourceFileLoader
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from bterminal.providers.ctx_defaults import (  # noqa: E402
    DEFAULT_INJECT_EVERY,
    DEFAULT_REFRESH_EVERY,
)


# ─── The constants themselves ─────────────────────────────────────────────


def test_canonical_defaults_are_100_and_200():
    """Pin: 100/200 is the user-facing pair. Memory panel originally
    hardcoded these (before the BUG#28 refactor) and they're the
    values users expect to see in the spinner."""
    assert DEFAULT_INJECT_EVERY == 100
    assert DEFAULT_REFRESH_EVERY == 200


# ─── tools/ctx CLI mirrors the package constants ──────────────────────────


def _load_ctx_module():
    """Load tools/ctx as a Python module so we can read its constants."""
    here = REPO_ROOT / "tools" / "ctx"
    loader = SourceFileLoader("_ctx_under_test", str(here))
    spec = importlib.util.spec_from_loader("_ctx_under_test", loader)
    mod = importlib.util.module_from_spec(spec)
    loader.exec_module(mod)
    return mod


def test_tools_ctx_uses_same_defaults_as_package():
    """Pin: tools/ctx must read the same DEFAULT_INJECT_EVERY constant
    as the rest of BT. Whether it gets there via package import (dev /
    typical install) or its embedded literal fallback (degraded install
    where the package isn't on sys.path), the value must be 100/200."""
    ctx = _load_ctx_module()
    assert ctx.DEFAULT_INJECT_EVERY == DEFAULT_INJECT_EVERY
    assert ctx.DEFAULT_REFRESH_EVERY == DEFAULT_REFRESH_EVERY


# ─── Schema DEFAULT clauses match the constants ───────────────────────────


def test_sqlite_schema_has_no_default_on_inject_columns():
    """Pin (BUG#31): the schema must NOT carry a DEFAULT clause on
    inject_every / refresh_every. Every caller passes explicit values;
    a DEFAULT would silently swallow a future regression where
    someone wrote `INSERT INTO rules_config (project) VALUES (?)` and
    the row would drift from DEFAULT_INJECT_EVERY without anyone
    noticing. NOT NULL without DEFAULT makes that mistake loud."""
    ctx_src = (REPO_ROOT / "tools" / "ctx").read_text(encoding="utf-8")
    # SQL comments contain '(?)' which trips up balanced-paren regexes.
    # Slice the CREATE … `);` block manually instead.
    start = ctx_src.find("CREATE TABLE IF NOT EXISTS rules_config")
    assert start >= 0, "rules_config CREATE TABLE not found"
    # Find the `);` that closes THIS CREATE — naive `find()` would catch
    # an earlier `)`. We look for `\n        );` which marks the end of
    # the table body at the source's indentation.
    end = ctx_src.find("\n        );", start)
    assert end > start, "rules_config CREATE TABLE end not found"
    body = ctx_src[start:end]
    inject_line = next(
        (ln for ln in body.splitlines()
         if "inject_every" in ln and not ln.lstrip().startswith("--")),
        None,
    )
    assert inject_line, f"inject_every column not found in body:\n{body}"
    assert "DEFAULT" not in inject_line.upper(), (
        f"BUG#31 regressed — DEFAULT clause is back on inject_every. "
        f"Got line: {inject_line!r}"
    )


# ─── Auto-UPSERT on first rules inject ────────────────────────────────────


def _ctx_invoke(args, env_extra=None):
    """Run `tools/ctx` as a subprocess with HOME redirected to tmp."""
    env = os.environ.copy()
    if env_extra:
        env.update(env_extra)
    return subprocess.run(
        [sys.executable, str(REPO_ROOT / "tools" / "ctx"), *args],
        env=env, capture_output=True, text=True, timeout=10,
    )


def test_rules_inject_auto_creates_rules_config_row(tmp_path):
    """Pin: invoking `ctx rules inject` for a project that has never
    been configured must INSERT a rules_config row with the canonical
    defaults. Without this, Memory panel and tools/ctx fall back to
    their own constants (BUG#28). Auto-UPSERT removes the asymmetry."""
    env = {"HOME": str(tmp_path)}
    # Seed: create a project + add one rule (so rules_inject prints).
    _ctx_invoke(["set", "demo", "k", "v"], env_extra=env)
    _ctx_invoke(["rules", "add", "demo", "Always test on VM"],
                env_extra=env)

    # Before inject: no rules_config row for this project.
    db_path = tmp_path / ".claude-context" / "context.db"
    assert db_path.exists()
    with sqlite3.connect(db_path) as conn:
        before = conn.execute(
            "SELECT 1 FROM rules_config WHERE project = ?", ("demo",)
        ).fetchone()
    assert before is None, "test pre-condition: no rules_config row yet"

    # Invoke rules inject — should INSERT OR IGNORE.
    result = _ctx_invoke(["rules", "inject", "demo"], env_extra=env)
    assert result.returncode == 0, result.stderr or result.stdout

    # After inject: row exists with canonical defaults.
    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT inject_every, refresh_every FROM rules_config "
            "WHERE project = ?",
            ("demo",),
        ).fetchone()
    assert row is not None, (
        "auto-UPSERT failed — rules_config still has no row, so "
        "future intro prompts will keep showing the wrong N"
    )
    assert row[0] == DEFAULT_INJECT_EVERY
    assert row[1] == DEFAULT_REFRESH_EVERY


def test_rules_inject_does_not_overwrite_existing_config(tmp_path):
    """Pin: auto-UPSERT must use INSERT OR IGNORE (not REPLACE) —
    a project that has explicit Memory-panel-saved values like
    (project, 50, 100) must NOT be reset to (100, 200) by a subsequent
    intro prompt build."""
    env = {"HOME": str(tmp_path)}
    _ctx_invoke(["set", "demo2", "k", "v"], env_extra=env)
    _ctx_invoke(["rules", "add", "demo2", "Test rule"], env_extra=env)

    db_path = tmp_path / ".claude-context" / "context.db"
    with sqlite3.connect(db_path) as conn:
        conn.execute(
            "INSERT INTO rules_config (project, inject_every, refresh_every)"
            " VALUES (?, ?, ?)",
            ("demo2", 50, 80),
        )
        conn.commit()

    _ctx_invoke(["rules", "inject", "demo2"], env_extra=env)

    with sqlite3.connect(db_path) as conn:
        row = conn.execute(
            "SELECT inject_every, refresh_every FROM rules_config "
            "WHERE project = ?",
            ("demo2",),
        ).fetchone()
    assert row == (50, 80), (
        f"User's saved values were overwritten by auto-UPSERT — got {row}, "
        f"expected (50, 80)"
    )


# ─── English header in rules inject output ────────────────────────────────


def test_rules_inject_header_is_english(tmp_path):
    """Pin (BUG#28): header must be English. The previous PL hardcoded
    'PRZYPOMNIENIE REGUŁ ... (co N promptów)' was inconsistent with
    other CLI tools (tasks/consult/memory_wizard are English) AND with
    rules that users typically write in English."""
    env = {"HOME": str(tmp_path)}
    _ctx_invoke(["set", "demo3", "k", "v"], env_extra=env)
    _ctx_invoke(["rules", "add", "demo3", "Demo rule"], env_extra=env)

    result = _ctx_invoke(["rules", "inject", "demo3"], env_extra=env)
    assert result.returncode == 0
    assert "PRZYPOMNIENIE" not in result.stdout, (
        "old Polish header still in output:\n" + result.stdout
    )
    assert "RULES REMINDER" in result.stdout
    assert "every 100 prompts" in result.stdout
