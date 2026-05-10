"""Unit tests for ctx_helpers — project name resolution + ctx availability."""

import os
import sqlite3

import pytest

from bterminal import config
from bterminal.ctx import helpers as ctx_helpers
# ─── _smart_project_name ──────────────────────────────────────────────────────

def test_smart_project_name_empty_dir():
    assert ctx_helpers._smart_project_name("") == ""
    assert ctx_helpers._smart_project_name(None) == ""


def test_smart_project_name_normal_basename(tmp_path):
    """Non-generic basename → use it as-is."""
    proj = tmp_path / "MyApp"
    proj.mkdir()
    assert ctx_helpers._smart_project_name(str(proj)) == "MyApp"


def test_smart_project_name_walks_up_for_generic_names(tmp_path):
    """src/docs/lib basenames trigger walk to git root."""
    repo = tmp_path / "MyRepo"
    repo.mkdir()
    (repo / ".git").mkdir()
    src = repo / "src"
    src.mkdir()
    assert ctx_helpers._smart_project_name(str(src)) == "MyRepo"


def test_smart_project_name_no_git_root_keeps_basename(tmp_path):
    """Bug #56 (2026-05-07): when basename is in _GENERIC_SUBDIRS but
    NO .git is found anywhere up the tree, trust the basename.

    Previously this case fell through to the parent dir name, which
    was wrong for users who organize their work in folders like
    ~/Dokumenty/test, ~/Desktop/scratch — the parent dir is just a
    home-directory bucket, not a project. The 'walk up to project
    root' heuristic only makes sense WITH a git anchor; otherwise
    we have nothing meaningful to walk up to."""
    parent = tmp_path / "Container"
    parent.mkdir()
    src = parent / "src"
    src.mkdir()
    assert ctx_helpers._smart_project_name(str(src)) == "src"


def test_smart_project_name_users_documents_test_returns_test(tmp_path):
    """Direct regression for user's screenshot (2026-05-07): picking
    ~/Dokumenty/test as project_dir prefilled "Dokumenty" in the ctx
    wizard. After fix it must prefill "test" (the actual chosen folder)."""
    docs = tmp_path / "Dokumenty"
    docs.mkdir()
    test_dir = docs / "test"
    test_dir.mkdir()
    assert ctx_helpers._smart_project_name(str(test_dir)) == "test"


def test_smart_project_name_generic_basename_inside_git_walks_to_repo(tmp_path):
    """Sanity: the existing walk-up-to-git heuristic still works when
    a .git anchor is present. ~/projects/myrepo/tests → 'myrepo'."""
    repo = tmp_path / "myrepo"
    repo.mkdir()
    (repo / ".git").mkdir()
    tests_dir = repo / "tests"
    tests_dir.mkdir()
    assert ctx_helpers._smart_project_name(str(tests_dir)) == "myrepo"


def test_smart_project_name_strips_trailing_slash(tmp_path):
    proj = tmp_path / "MyTool"
    proj.mkdir()
    assert ctx_helpers._smart_project_name(str(proj) + "/") == "MyTool"


# ─── _resolve_ctx_project_name ────────────────────────────────────────────────

def test_resolve_no_db_falls_back_to_smart(tmp_path, monkeypatch):
    """No CTX_DB → fall through to _smart_project_name."""
    monkeypatch.setattr(ctx_helpers, "CTX_DB", str(tmp_path / "nonexistent.db"))
    proj = tmp_path / "MyProj"
    proj.mkdir()
    assert ctx_helpers._resolve_ctx_project_name(str(proj)) == "MyProj"


def test_resolve_uses_sessions_table_match(tmp_path, monkeypatch):
    """If sessions row matches work_dir, return its name."""
    db_path = tmp_path / "ctx.db"
    monkeypatch.setattr(ctx_helpers, "CTX_DB", str(db_path))
    db = sqlite3.connect(str(db_path))
    db.executescript("""
        CREATE TABLE sessions (name TEXT, work_dir TEXT);
        INSERT INTO sessions VALUES ('custom-name', '/some/dir');
    """)
    db.close()
    assert ctx_helpers._resolve_ctx_project_name("/some/dir") == "custom-name"
    # trailing slash should still match
    assert ctx_helpers._resolve_ctx_project_name("/some/dir/") == "custom-name"


def test_resolve_walks_up_to_parent_match(tmp_path, monkeypatch):
    """Subdir of a registered project resolves to that project's name."""
    db_path = tmp_path / "ctx.db"
    monkeypatch.setattr(ctx_helpers, "CTX_DB", str(db_path))
    db = sqlite3.connect(str(db_path))
    db.executescript("""
        CREATE TABLE sessions (name TEXT, work_dir TEXT);
        INSERT INTO sessions VALUES ('myproj', '/parent/proj');
    """)
    db.close()
    assert ctx_helpers._resolve_ctx_project_name("/parent/proj/sub") == "myproj"


def test_resolve_empty_dir_returns_none():
    assert ctx_helpers._resolve_ctx_project_name("") is None
    assert ctx_helpers._resolve_ctx_project_name(None) is None


# ─── _is_ctx_project_registered ───────────────────────────────────────────────

def test_is_registered_no_db(tmp_path, monkeypatch):
    monkeypatch.setattr(ctx_helpers, "CTX_DB", str(tmp_path / "nope.db"))
    assert ctx_helpers._is_ctx_project_registered("anything") is False


def test_is_registered_existing(tmp_path, monkeypatch):
    db_path = tmp_path / "ctx.db"
    monkeypatch.setattr(ctx_helpers, "CTX_DB", str(db_path))
    db = sqlite3.connect(str(db_path))
    db.executescript("""
        CREATE TABLE sessions (name TEXT);
        INSERT INTO sessions VALUES ('existing');
    """)
    db.close()
    assert ctx_helpers._is_ctx_project_registered("existing") is True
    assert ctx_helpers._is_ctx_project_registered("nope") is False
