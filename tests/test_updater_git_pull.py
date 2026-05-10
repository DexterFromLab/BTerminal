"""Tests for V1 — dirty-tree-safe git pull (`_git_pull_with_autostash`).

Replaces the bare `git pull` in `_do_update` (which failed on
uncommitted local changes — see image bug from 2026-05-06) with a
stash → pull → pop pipeline. Tests use real `git` against tmp_path
repos so the subprocess plumbing is exercised end-to-end without GTK.
"""
from __future__ import annotations

import os
import subprocess
from pathlib import Path

import pytest

from bterminal.updater import (
    _git_pull_with_autostash,
    _git_repo_is_dirty,
)


def _run(cmd, cwd):
    """Helper: subprocess.run wrapper with text capture."""
    return subprocess.run(
        cmd, cwd=cwd, capture_output=True, text=True, timeout=30, check=False,
    )


def _git_init_with_user(cwd):
    _run(["git", "init", "-b", "master"], cwd)
    _run(["git", "config", "user.email", "test@example.com"], cwd)
    _run(["git", "config", "user.name", "Test"], cwd)
    _run(["git", "config", "commit.gpgsign", "false"], cwd)


@pytest.fixture
def upstream_repo(tmp_path):
    """Bare-bones upstream repo with one commit (master = a.txt v1)."""
    upstream = tmp_path / "upstream"
    upstream.mkdir()
    _git_init_with_user(str(upstream))
    (upstream / "a.txt").write_text("v1\n")
    _run(["git", "add", "a.txt"], str(upstream))
    _run(["git", "commit", "-m", "v1"], str(upstream))
    return upstream


@pytest.fixture
def clone_repo(tmp_path, upstream_repo):
    """Clone of upstream that the updater treats as REPO_DIR."""
    clone = tmp_path / "clone"
    _run(["git", "clone", str(upstream_repo), str(clone)], str(tmp_path))
    _run(["git", "config", "user.email", "test@example.com"], str(clone))
    _run(["git", "config", "user.name", "Test"], str(clone))
    _run(["git", "config", "commit.gpgsign", "false"], str(clone))
    # `git pull origin master` needs a configured remote — clone does this.
    return clone


def _push_remote_update(upstream, filename, content, msg):
    """Add a new commit to the upstream so a clone has something to pull."""
    (upstream / filename).write_text(content)
    _run(["git", "add", filename], str(upstream))
    _run(["git", "commit", "-m", msg], str(upstream))


# ─── _git_repo_is_dirty ──────────────────────────────────────────────────────


def test_clean_repo_is_not_dirty(clone_repo):
    assert _git_repo_is_dirty(str(clone_repo)) is False


def test_modified_file_makes_repo_dirty(clone_repo):
    (clone_repo / "a.txt").write_text("local change\n")
    assert _git_repo_is_dirty(str(clone_repo)) is True


def test_untracked_file_makes_repo_dirty(clone_repo):
    (clone_repo / "new.txt").write_text("new\n")
    assert _git_repo_is_dirty(str(clone_repo)) is True


def test_non_git_dir_returns_false(tmp_path):
    """Defensive: non-repo path → False, no exception."""
    plain = tmp_path / "plain"
    plain.mkdir()
    assert _git_repo_is_dirty(str(plain)) is False


# ─── _git_pull_with_autostash — happy paths ─────────────────────────────────


def test_clean_repo_pulls_directly(clone_repo, upstream_repo):
    """Nothing to stash — pull goes through, no stash created."""
    _push_remote_update(upstream_repo, "b.txt", "v1\n", "add b.txt")
    out = _git_pull_with_autostash(str(clone_repo))
    assert out["ok"] is True
    assert out["stashed"] is False
    assert out["stash_popped"] is False
    assert out["error"] is None
    assert (clone_repo / "b.txt").exists()


def test_dirty_repo_stashes_and_pops(clone_repo, upstream_repo):
    """The exact bug from 2026-05-06: local edit + remote update → both
    survive (`git pull` would have failed with a merge-overwrite error)."""
    # Local change
    (clone_repo / "a.txt").write_text("local edit\n")
    # Remote change to a DIFFERENT file (no merge conflict on pop)
    _push_remote_update(upstream_repo, "b.txt", "remote\n", "add b")

    out = _git_pull_with_autostash(str(clone_repo))

    assert out["ok"] is True
    assert out["stashed"] is True
    assert out["stash_popped"] is True
    assert out["error"] is None
    # Local change preserved
    assert (clone_repo / "a.txt").read_text() == "local edit\n"
    # Remote change applied
    assert (clone_repo / "b.txt").exists()


def test_dirty_repo_with_untracked_file_survives_pull(clone_repo, upstream_repo):
    """`-u` flag in stash push captures untracked files too."""
    (clone_repo / "untracked.txt").write_text("untracked content\n")
    _push_remote_update(upstream_repo, "b.txt", "remote\n", "add b")

    out = _git_pull_with_autostash(str(clone_repo))

    assert out["ok"] is True
    assert out["stashed"] is True
    assert out["stash_popped"] is True
    # Untracked file restored after pull
    assert (clone_repo / "untracked.txt").read_text() == "untracked content\n"


# ─── _git_pull_with_autostash — error paths ─────────────────────────────────


def test_pop_conflict_keeps_stash_for_user(clone_repo, upstream_repo):
    """When local + remote both modify the SAME file, `git stash pop`
    surfaces a merge conflict — we keep the stash so the user can
    resolve it manually rather than dropping their work."""
    # Local + remote both modify a.txt → conflict on pop
    (clone_repo / "a.txt").write_text("local edit\n")
    _push_remote_update(upstream_repo, "a.txt", "remote edit\n",
                          "remote v2")

    out = _git_pull_with_autostash(str(clone_repo))

    assert out["ok"] is False
    assert out["stashed"] is True
    assert out["stash_popped"] is False
    assert out["error"] is not None
    assert "stash" in out["error"].lower()
    # Stash is still in the list — user can `git stash pop` manually
    stash_list = subprocess.run(
        ["git", "stash", "list"], cwd=str(clone_repo),
        capture_output=True, text=True, timeout=10,
    ).stdout
    assert "bterminal-auto-update" in stash_list


def test_pull_failure_restores_stash(clone_repo, upstream_repo, monkeypatch):
    """If pull itself fails (e.g. network down — simulated by pointing
    origin at a broken URL), the stash is restored so the user's local
    state isn't lost behind a stash entry."""
    (clone_repo / "a.txt").write_text("local edit\n")
    # Replace origin with a non-existent URL → pull fails
    _run(["git", "remote", "set-url", "origin",
          "/tmp/this-does-not-exist-anywhere"], str(clone_repo))

    out = _git_pull_with_autostash(str(clone_repo))

    assert out["ok"] is False
    assert out["error"] is not None
    assert "git pull failed" in out["error"]
    # Local change restored (not stuck in a stash) since pull never started.
    assert (clone_repo / "a.txt").read_text() == "local edit\n"
    # No leftover stash entry blocking the user.
    stash_list = subprocess.run(
        ["git", "stash", "list"], cwd=str(clone_repo),
        capture_output=True, text=True, timeout=10,
    ).stdout
    assert "bterminal-auto-update" not in stash_list


def test_clean_repo_with_pull_failure(clone_repo):
    """Clean repo + broken remote → pull fails, no stash created."""
    _run(["git", "remote", "set-url", "origin",
          "/tmp/this-does-not-exist"], str(clone_repo))
    out = _git_pull_with_autostash(str(clone_repo))
    assert out["ok"] is False
    assert out["stashed"] is False
    assert "git pull failed" in (out["error"] or "")


# ─── Module surface ─────────────────────────────────────────────────────────


def test_helpers_exported_from_updater_module():
    from bterminal import updater as mod
    assert callable(mod._git_repo_is_dirty)
    assert callable(mod._git_pull_with_autostash)
