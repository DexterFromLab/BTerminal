"""Cross-feature: AIDER.md symlink resilience after deletion
(#43 / #115, audit § 6.3 #16).

The ctx wizard's `ensure_context_files_for_all_providers` is
invoked at every wizard finalize. Common scenarios:
  - User accidentally `rm AIDER.md` (deletes the symlink) →
    next wizard run should recreate it.
  - User accidentally `rm CLAUDE.md` (the source) → next wizard
    run sees no_source for AIDER.md, intentional broken symlink
    is left alone.
  - Both gone → next wizard run reports no_source for both,
    AIDER.md NOT created.

Three decision branches mapped to the helper's actual return
codes:
  (a) symlink target removed (CLAUDE.md gone) but symlink exists
      (AIDER.md → CLAUDE.md broken) → 'exists' (intentional-but-
      broken, leave alone — caller will retry once CLAUDE.md
      regenerates).
  (b) Both removed → 'no_source' for AIDER.md.
  (c) Only symlink removed (CLAUDE.md still there) → 'symlink'
      (recreated).

These pin the resilience contract — wizard re-runs are
idempotent and recover gracefully from any deletion sequence.

Manual VM smoke (`rm AIDER.md; ctx wizard`) is documented in
tests/manual/README.md.
"""
from __future__ import annotations

import os
from pathlib import Path

import pytest

from bterminal.ctx.helpers import (
    ensure_context_file_alongside_claude,
    ensure_context_files_for_all_providers,
)


def _seed_claude_md(project_dir, content="# project\n\ncontext"):
    (project_dir / "CLAUDE.md").write_text(content)


# ─── Branch (c): only symlink removed, source still there ───────────────


def test_recreate_symlink_after_user_deletes_aider_md(tmp_path):
    """User does `rm AIDER.md` (removes symlink). Next ctx wizard
    finalize → symlink recreated as fresh AIDER.md → CLAUDE.md."""
    _seed_claude_md(tmp_path)
    # First wizard run: creates symlink
    result1 = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result1 == "symlink"
    aider_md = tmp_path / "AIDER.md"
    assert aider_md.is_symlink()

    # User deletes AIDER.md
    aider_md.unlink()
    assert not aider_md.exists()
    assert not aider_md.is_symlink()

    # Second wizard run: recreates the symlink
    result2 = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result2 == "symlink"
    assert aider_md.is_symlink()
    assert os.readlink(str(aider_md)) == "CLAUDE.md"
    # Content matches CLAUDE.md
    assert "context" in aider_md.read_text()


def test_recreate_via_dispatcher_after_aider_md_deletion(tmp_path):
    """Same scenario via the registry-driven dispatcher
    `ensure_context_files_for_all_providers`. Wizard's actual
    finalize call. AIDER.md returns 'symlink' on the recreate;
    AGENTS.md (already exists from first run) returns 'exists'."""
    _seed_claude_md(tmp_path)
    ensure_context_files_for_all_providers(tmp_path)

    # User deletes ONLY AIDER.md
    (tmp_path / "AIDER.md").unlink()
    assert not (tmp_path / "AIDER.md").exists()
    # AGENTS.md still there
    assert (tmp_path / "AGENTS.md").is_symlink()

    # Re-run dispatcher
    results = ensure_context_files_for_all_providers(tmp_path)
    assert results.get("AIDER.md") == "symlink"
    assert results.get("AGENTS.md") == "exists"
    assert results.get("CLAUDE.md") == "self"
    # Both context files present
    assert (tmp_path / "AIDER.md").is_symlink()
    assert (tmp_path / "AGENTS.md").is_symlink()


# ─── Branch (a): symlink target gone, symlink itself remains ────────────


def test_broken_symlink_pointing_to_claude_md_left_intact(tmp_path):
    """User accidentally `rm CLAUDE.md` (deletes source). AIDER.md
    is now a broken symlink pointing to nonexistent CLAUDE.md.

    Pre-#92 contract (re-pinned here for #115): the link is
    INTENTIONALLY broken — points at the right name, just
    temporarily missing. Helper returns 'exists' and leaves it
    alone. When CLAUDE.md regenerates (next ctx wizard), the
    symlink resolves automatically."""
    _seed_claude_md(tmp_path)
    ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    aider_md = tmp_path / "AIDER.md"
    assert aider_md.is_symlink()

    # User deletes CLAUDE.md → AIDER.md now broken
    (tmp_path / "CLAUDE.md").unlink()
    assert os.path.lexists(aider_md)  # symlink still there
    assert not aider_md.exists()  # but resolves to nothing

    # Helper sees broken symlink → 'exists' (intentional, leave alone)
    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "exists"
    # Still pointing at CLAUDE.md (no relink to something else)
    assert os.readlink(str(aider_md)) == "CLAUDE.md"


def test_broken_symlink_self_heals_when_claude_md_regenerates(tmp_path):
    """End-to-end of branch (a) recovery: broken symlink + CLAUDE.md
    regenerated → next wizard run sees the link works again,
    returns 'exists' (no rewrite needed)."""
    _seed_claude_md(tmp_path)
    ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    (tmp_path / "CLAUDE.md").unlink()
    assert not (tmp_path / "AIDER.md").exists()  # broken

    # Wizard regenerates CLAUDE.md (e.g. user fills the form again)
    _seed_claude_md(tmp_path, content="# regenerated\n\nnew context")
    # Now AIDER.md (still a symlink) resolves again
    aider_md = tmp_path / "AIDER.md"
    assert aider_md.exists()
    assert "regenerated" in aider_md.read_text()

    # Idempotent re-run: no rewrite
    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "exists"


# ─── Branch (b): both removed → no_source ───────────────────────────────


def test_both_files_removed_returns_no_source(tmp_path):
    """User does `rm AIDER.md CLAUDE.md`. Next wizard run → no
    CLAUDE.md to mirror, AIDER.md not created."""
    _seed_claude_md(tmp_path)
    ensure_context_file_alongside_claude(tmp_path, "AIDER.md")

    (tmp_path / "AIDER.md").unlink()
    (tmp_path / "CLAUDE.md").unlink()

    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "no_source"
    assert not (tmp_path / "AIDER.md").exists()


def test_both_removed_via_dispatcher(tmp_path):
    """Dispatcher version of branch (b): both source + mirrors
    removed, all providers report no_source for their context
    files; CLAUDE.md (self) still reports 'self'."""
    _seed_claude_md(tmp_path)
    ensure_context_files_for_all_providers(tmp_path)

    # Wipe all context files
    for fn in ("CLAUDE.md", "AIDER.md", "AGENTS.md"):
        (tmp_path / fn).unlink()

    results = ensure_context_files_for_all_providers(tmp_path)
    assert results.get("CLAUDE.md") == "self"
    assert results.get("AIDER.md") == "no_source"
    assert results.get("AGENTS.md") == "no_source"
    # No files materialized
    for fn in ("CLAUDE.md", "AIDER.md", "AGENTS.md"):
        assert not (tmp_path / fn).exists()


# ─── Edge: symlink replaced with regular file (user-customized) ─────────


def test_symlink_replaced_with_regular_file_treated_as_user_custom(
        tmp_path):
    """User does `rm AIDER.md; nano AIDER.md` to write their own.
    Next wizard run sees the regular file → 'exists' → leaves it
    alone (no clobber, even though it's not a symlink anymore)."""
    _seed_claude_md(tmp_path)
    ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    aider_md = tmp_path / "AIDER.md"
    aider_md.unlink()
    aider_md.write_text("# Aider-specific\n\nfoo bar")

    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "exists"
    # User content untouched
    assert aider_md.read_text() == "# Aider-specific\n\nfoo bar"
    assert not aider_md.is_symlink()


def test_regular_file_then_user_recreates_as_symlink_manually(tmp_path):
    """User does `rm AIDER.md && ln -s CLAUDE.md AIDER.md` (manual
    repair). Wizard sees the new symlink → 'exists', no double-
    create."""
    _seed_claude_md(tmp_path)
    ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    aider_md = tmp_path / "AIDER.md"
    aider_md.unlink()
    aider_md.symlink_to("CLAUDE.md")  # user manually re-links

    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "exists"
    assert aider_md.is_symlink()
    assert os.readlink(str(aider_md)) == "CLAUDE.md"


# ─── Cross-feature: dispatcher idempotency across multiple deletions ────


def test_dispatcher_resilient_to_partial_deletions(tmp_path):
    """Mixed state: AIDER.md removed (symlink), AGENTS.md still
    present, CLAUDE.md still present. Dispatcher recreates the
    one that's missing, leaves the others alone."""
    _seed_claude_md(tmp_path)
    ensure_context_files_for_all_providers(tmp_path)

    # Remove ONLY aider — both copilot's AGENTS.md and CLAUDE.md
    # untouched
    (tmp_path / "AIDER.md").unlink()

    results = ensure_context_files_for_all_providers(tmp_path)
    assert results.get("AIDER.md") == "symlink"  # recreated
    assert results.get("AGENTS.md") == "exists"  # left alone
    assert results.get("CLAUDE.md") == "self"  # source

    # All present
    assert (tmp_path / "AIDER.md").is_symlink()
    assert (tmp_path / "AGENTS.md").is_symlink()
    assert (tmp_path / "CLAUDE.md").is_file()


def test_dispatcher_idempotent_with_repeated_deletions(tmp_path):
    """User deletes AIDER.md → wizard recreates → user deletes
    again → wizard recreates again. Idempotent across N cycles."""
    _seed_claude_md(tmp_path)

    for cycle in range(5):
        ensure_context_files_for_all_providers(tmp_path)
        aider_md = tmp_path / "AIDER.md"
        assert aider_md.is_symlink(), (
            f"cycle {cycle}: AIDER.md not a symlink"
        )
        # Verify content roundtrip via symlink
        assert "context" in aider_md.read_text()
        # Delete + repeat
        aider_md.unlink()


# ─── Stale-symlink-fix path also re-engages after re-delete (#92) ───────


def test_stale_symlink_repair_after_user_changes_target_then_deletes(
        tmp_path):
    """User scenario: original symlink AIDER.md → CLAUDE.md.
    User does `ln -sf OLD_CONTEXT.md AIDER.md` (relinks at
    different target), OLD_CONTEXT.md doesn't exist → stale
    broken symlink. Next wizard run repairs it back to CLAUDE.md
    via the 'fixed' return code."""
    _seed_claude_md(tmp_path)
    ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    aider_md = tmp_path / "AIDER.md"

    # User relinks to a non-existent file
    aider_md.unlink()
    aider_md.symlink_to("OLD_CONTEXT.md")
    assert not aider_md.exists()  # broken

    # Wizard's 'fixed' path repairs it
    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "fixed"
    assert os.readlink(str(aider_md)) == "CLAUDE.md"
    assert aider_md.exists()  # resolves now


def test_repaired_symlink_idempotent_on_repeat_runs(tmp_path):
    """After 'fixed' path repairs the symlink, subsequent runs
    return 'exists' (no double-fix loop)."""
    _seed_claude_md(tmp_path)
    aider_md = tmp_path / "AIDER.md"
    aider_md.symlink_to("STALE.md")  # broken from the start

    result1 = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result1 == "fixed"

    result2 = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result2 == "exists"

    result3 = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result3 == "exists"


# ─── No double-create when wizard finalizes twice without deletions ─────


def test_dispatcher_safe_for_multiple_finalizes_no_changes(tmp_path):
    """Wizard finalize → no deletion → finalize again. All
    providers report 'exists' (or 'self' for Claude). No file
    rewrites — confirms idempotency."""
    _seed_claude_md(tmp_path)
    first = ensure_context_files_for_all_providers(tmp_path)
    second = ensure_context_files_for_all_providers(tmp_path)
    third = ensure_context_files_for_all_providers(tmp_path)

    # First creates everything
    assert first.get("AIDER.md") == "symlink"
    assert first.get("AGENTS.md") == "symlink"

    # Second + third report exists (no rewrites)
    for results in (second, third):
        assert results.get("AIDER.md") == "exists"
        assert results.get("AGENTS.md") == "exists"
        assert results.get("CLAUDE.md") == "self"


# ─── Cross: source modification picked up via symlink resolution ────────


def test_claude_md_modifications_visible_via_aider_md_symlink(tmp_path):
    """User edits CLAUDE.md after the symlink was created.
    AIDER.md (the symlink) automatically reflects the change —
    no wizard re-run needed. Pin so a future refactor that
    switches to copy-mode by default is forced to re-document
    this contract."""
    _seed_claude_md(tmp_path, content="version 1")
    ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    aider_md = tmp_path / "AIDER.md"
    assert "version 1" in aider_md.read_text()

    # User edits CLAUDE.md directly
    (tmp_path / "CLAUDE.md").write_text("version 2 — updated")
    # Symlink resolves to new content automatically
    assert "version 2" in aider_md.read_text()
    # Helper rerun reports 'exists' (no rewrite needed)
    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "exists"
