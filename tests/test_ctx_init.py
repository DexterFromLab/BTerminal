"""Unit tests for ensure_agents_md_alongside_claude — T2.9.

T2.9 mirrors CLAUDE.md → AGENTS.md so GitHub Copilot CLI (which reads
AGENTS.md as its repo instructions) sees the same content as Claude
Code (which reads CLAUDE.md). The function tries os.symlink first,
falls back to shutil.copy when the filesystem lacks symlink support.
"""
from __future__ import annotations

import os

import pytest

from bterminal.ctx.helpers import ensure_agents_md_alongside_claude


def _seed_claude_md(project_dir, content="# project\n\ncontext"):
    claude_md = project_dir / "CLAUDE.md"
    claude_md.write_text(content)
    return claude_md


# ─── Happy path: symlink ────────────────────────────────────────────────────

def test_creates_agents_md_symlink(tmp_path):
    """On a symlink-capable FS, AGENTS.md is created as a symbolic link."""
    _seed_claude_md(tmp_path)
    result = ensure_agents_md_alongside_claude(tmp_path)
    assert result == "symlink"
    agents_md = tmp_path / "AGENTS.md"
    assert agents_md.is_symlink()
    # Relative target so moving the directory doesn't break the link
    assert os.readlink(str(agents_md)) == "CLAUDE.md"


def test_symlink_resolves_to_same_content(tmp_path):
    """Reading AGENTS.md returns CLAUDE.md's content."""
    _seed_claude_md(tmp_path, content="# myproj\n\nINTERESTING CONTEXT")
    ensure_agents_md_alongside_claude(tmp_path)
    agents_md = tmp_path / "AGENTS.md"
    assert "INTERESTING CONTEXT" in agents_md.read_text()


def test_accepts_pathlib_path_input(tmp_path):
    """Function accepts both str and Path input."""
    _seed_claude_md(tmp_path)
    # Path object directly
    result = ensure_agents_md_alongside_claude(tmp_path)
    assert result == "symlink"
    # Cleanup + retry with str
    (tmp_path / "AGENTS.md").unlink()
    result = ensure_agents_md_alongside_claude(str(tmp_path))
    assert result == "symlink"


# ─── Idempotency ────────────────────────────────────────────────────────────

def test_existing_agents_md_left_untouched(tmp_path):
    """If user has customized AGENTS.md, do not overwrite."""
    _seed_claude_md(tmp_path)
    custom = tmp_path / "AGENTS.md"
    custom.write_text("# my custom Copilot instructions\n")
    result = ensure_agents_md_alongside_claude(tmp_path)
    assert result == "exists"
    assert custom.read_text() == "# my custom Copilot instructions\n"
    assert not custom.is_symlink()


def test_existing_symlink_left_untouched(tmp_path):
    """If AGENTS.md is already a symlink, leave it alone (idempotent
    second run after first one already created it)."""
    _seed_claude_md(tmp_path)
    ensure_agents_md_alongside_claude(tmp_path)
    # Second call — should be no-op
    result = ensure_agents_md_alongside_claude(tmp_path)
    assert result == "exists"


def test_existing_broken_symlink_left_untouched(tmp_path):
    """A broken symlink (target deleted) still counts as 'existing' —
    the user may have deliberately broken it. Don't replace silently."""
    (tmp_path / "AGENTS.md").symlink_to("CLAUDE.md")  # CLAUDE.md doesn't exist
    # path.lexists is True for broken symlinks
    assert os.path.lexists(str(tmp_path / "AGENTS.md"))
    _seed_claude_md(tmp_path)
    result = ensure_agents_md_alongside_claude(tmp_path)
    assert result == "exists"


# ─── Edge cases ─────────────────────────────────────────────────────────────

def test_no_source_when_claude_md_missing(tmp_path):
    """Without CLAUDE.md, function reports no_source and creates nothing."""
    result = ensure_agents_md_alongside_claude(tmp_path)
    assert result == "no_source"
    assert not (tmp_path / "AGENTS.md").exists()


def test_fallback_to_copy_when_symlink_fails(tmp_path, monkeypatch):
    """Cross-FS / FAT-style scenario: os.symlink raises OSError → copy."""
    _seed_claude_md(tmp_path, content="copy-fallback content")

    real_symlink = os.symlink

    def _fake_symlink(target, dest, *args, **kwargs):
        raise OSError("Operation not supported on this filesystem")

    monkeypatch.setattr(os, "symlink", _fake_symlink)
    result = ensure_agents_md_alongside_claude(tmp_path)
    assert result == "copy"
    agents_md = tmp_path / "AGENTS.md"
    assert agents_md.is_file()
    assert not agents_md.is_symlink()
    assert "copy-fallback content" in agents_md.read_text()


def test_failed_when_both_symlink_and_copy_fail(tmp_path, monkeypatch):
    """Both raise → return 'failed' (non-fatal — caller may warn)."""
    _seed_claude_md(tmp_path)
    monkeypatch.setattr(os, "symlink",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("nope")))
    import shutil
    monkeypatch.setattr(shutil, "copy",
                        lambda *a, **kw: (_ for _ in ()).throw(OSError("also nope")))
    # Re-import to pick up monkeypatched shutil
    monkeypatch.setattr("bterminal.ctx.helpers.shutil", shutil)
    result = ensure_agents_md_alongside_claude(tmp_path)
    assert result == "failed"


# ─── Cross-cutting: function is non-destructive ─────────────────────────────

def test_does_not_modify_claude_md(tmp_path):
    """CLAUDE.md is read-only from this function's perspective."""
    claude_md = _seed_claude_md(tmp_path, content="ORIGINAL")
    mtime_before = claude_md.stat().st_mtime
    ensure_agents_md_alongside_claude(tmp_path)
    assert claude_md.read_text() == "ORIGINAL"
    assert claude_md.stat().st_mtime == mtime_before
