"""Tests for ensure_context_file_alongside_claude + the registry-driven
ensure_context_files_for_all_providers helper (#20 / #92).

Builds on the existing AGENTS.md tests in test_ctx_init.py:
  - generalize the symlink mechanism for any filename
  - Aider's AIDER.md follows the same flow
  - the registry-driven dispatcher walks every provider's
    capabilities.context_file
  - Claude's own CLAUDE.md is the SOURCE, never a mirror target
  - broken symlink fix path (new in #92, distinct from AGENTS.md
    test_existing_broken_symlink_left_untouched which pre-dates the
    repair logic)
"""
from __future__ import annotations

import os

import pytest

from bterminal.ctx.helpers import (
    ensure_context_file_alongside_claude,
    ensure_context_files_for_all_providers,
)


def _seed_claude_md(project_dir, content="# project\n\ncontext"):
    (project_dir / "CLAUDE.md").write_text(content)


# ─── ensure_context_file_alongside_claude — Aider's AIDER.md ──────────────


def test_aider_md_symlink_created_when_missing(tmp_path):
    """Spawn-time path: project has CLAUDE.md, no AIDER.md → BT
    creates AIDER.md → CLAUDE.md as a relative symlink."""
    _seed_claude_md(tmp_path)
    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "symlink"
    aider_md = tmp_path / "AIDER.md"
    assert aider_md.is_symlink()
    assert os.readlink(str(aider_md)) == "CLAUDE.md"


def test_aider_md_resolves_to_claude_md_content(tmp_path):
    """The symlink resolves to the same content as CLAUDE.md."""
    _seed_claude_md(tmp_path, content="# myproj\n\nAIDER SEES THIS TOO")
    ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert "AIDER SEES THIS TOO" in (tmp_path / "AIDER.md").read_text()


def test_aider_md_left_alone_when_user_customized(tmp_path):
    """If the user wrote their own AIDER.md (regular file, not symlink)
    BT leaves it alone — same idempotency contract as AGENTS.md."""
    _seed_claude_md(tmp_path)
    custom = tmp_path / "AIDER.md"
    custom.write_text("# Aider-specific instructions\n\nfoo bar baz")
    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "exists"
    assert custom.read_text() == "# Aider-specific instructions\n\nfoo bar baz"
    assert not custom.is_symlink()


def test_aider_md_existing_working_symlink_left_alone(tmp_path):
    """Idempotent run on a project where the symlink already exists +
    points to a real CLAUDE.md — second invocation is a no-op."""
    _seed_claude_md(tmp_path)
    ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    # Second run
    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "exists"
    assert (tmp_path / "AIDER.md").is_symlink()


def test_aider_md_no_source_when_claude_md_missing(tmp_path):
    """Without a CLAUDE.md to mirror, the function reports no_source."""
    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "no_source"
    assert not (tmp_path / "AIDER.md").exists()


# ─── Broken-symlink repair (#92 plan: "broken symlink fixed") ─────────────


def test_aider_md_broken_symlink_pointing_elsewhere_gets_repaired(tmp_path):
    """If AIDER.md is a stale symlink pointing at something OTHER than
    CLAUDE.md (e.g. a renamed legacy file) and CLAUDE.md exists, BT
    repoints it. This is the #92 'broken symlink fixed' contract."""
    _seed_claude_md(tmp_path)
    aider_md = tmp_path / "AIDER.md"
    aider_md.symlink_to("OLD_CONTEXT_FILE.md")  # broken — file doesn't exist
    assert aider_md.is_symlink()
    assert not aider_md.exists()  # symlink resolves nowhere

    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "fixed"
    assert aider_md.is_symlink()
    assert os.readlink(str(aider_md)) == "CLAUDE.md"
    # And it actually resolves now
    assert aider_md.exists()
    assert "context" in aider_md.read_text()


def test_aider_md_broken_link_to_claude_md_left_when_no_source(tmp_path):
    """Edge case: AIDER.md → CLAUDE.md but CLAUDE.md doesn't exist yet
    (e.g. user created the link first, CLAUDE.md generation pending).
    The link is intentional even if currently broken — leave it.
    Caller will retry once the source is generated."""
    aider_md = tmp_path / "AIDER.md"
    aider_md.symlink_to("CLAUDE.md")
    # No CLAUDE.md yet
    assert os.path.lexists(aider_md)
    assert not (tmp_path / "CLAUDE.md").exists()

    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    # Path 3: leave the intentional-but-currently-broken link alone
    assert result == "exists"
    assert os.readlink(str(aider_md)) == "CLAUDE.md"


# ─── Provider's own filename = source case (Claude) ──────────────────────


def test_claude_md_filename_returns_self_no_op(tmp_path):
    """If the provider's context_file IS 'CLAUDE.md' (Claude itself),
    return 'self' without doing anything — there's nothing to mirror,
    we ARE the source."""
    _seed_claude_md(tmp_path)
    # No additional file creation should happen
    files_before = set(os.listdir(tmp_path))
    result = ensure_context_file_alongside_claude(tmp_path, "CLAUDE.md")
    assert result == "self"
    files_after = set(os.listdir(tmp_path))
    assert files_before == files_after


def test_empty_filename_returns_self_no_op(tmp_path):
    """capabilities.context_file=None / empty string → 'self' no-op.
    This is the explicit fallback path for providers that don't
    declare a context file at all."""
    _seed_claude_md(tmp_path)
    for empty in (None, ""):
        result = ensure_context_file_alongside_claude(tmp_path, empty)
        assert result == "self"


# ─── Filesystem fallback parity with AGENTS.md ────────────────────────────


def test_aider_md_falls_back_to_copy_when_symlink_unsupported(
        tmp_path, monkeypatch):
    """Cross-FS / FAT-style — symlink raises → copy fallback. Same
    parity as the existing AGENTS.md test in test_ctx_init.py."""
    _seed_claude_md(tmp_path, content="copy-fallback aider content")

    def _fake_symlink(target, dest, *args, **kwargs):
        raise OSError("symlinks not supported here")

    monkeypatch.setattr(os, "symlink", _fake_symlink)
    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "copy"
    aider_md = tmp_path / "AIDER.md"
    assert aider_md.is_file()
    assert not aider_md.is_symlink()
    assert "copy-fallback aider content" in aider_md.read_text()


def test_broken_symlink_repair_path_handles_unlink_failure(
        tmp_path, monkeypatch):
    """Defensive: if os.unlink during the repair path raises (rare —
    permission-locked file), the helper returns 'failed' rather than
    blowing up the whole ctx wizard."""
    _seed_claude_md(tmp_path)
    aider_md = tmp_path / "AIDER.md"
    aider_md.symlink_to("STALE.md")

    def _explode(*a, **kw):
        raise OSError("locked")

    monkeypatch.setattr(os, "unlink", _explode)
    result = ensure_context_file_alongside_claude(tmp_path, "AIDER.md")
    assert result == "failed"
    # Stale link still in place (we couldn't clear it)
    assert os.readlink(str(aider_md)) == "STALE.md"


# ─── Registry-driven dispatcher: ensure_context_files_for_all_providers ──


def test_dispatcher_creates_one_link_per_provider_with_context_file(tmp_path):
    """Walk the provider registry — every provider that declares
    capabilities.context_file gets its mirror created. With the
    bundled defaults Claude=CLAUDE.md (self), Copilot=AGENTS.md,
    Aider=AIDER.md → expect AGENTS.md + AIDER.md materialize."""
    _seed_claude_md(tmp_path, content="# all-providers context")

    results = ensure_context_files_for_all_providers(tmp_path)

    # Claude's CLAUDE.md → 'self' (no mirror created)
    assert results.get("CLAUDE.md") == "self"
    # Copilot's AGENTS.md → fresh symlink
    assert results.get("AGENTS.md") == "symlink"
    assert (tmp_path / "AGENTS.md").is_symlink()
    # Aider's AIDER.md → fresh symlink
    assert results.get("AIDER.md") == "symlink"
    assert (tmp_path / "AIDER.md").is_symlink()
    # Both mirrors resolve to the same source
    assert "all-providers context" in (tmp_path / "AGENTS.md").read_text()
    assert "all-providers context" in (tmp_path / "AIDER.md").read_text()


def test_dispatcher_idempotent_on_second_run(tmp_path):
    """Running twice produces 'exists' on the second call — proves
    the per-provider helper's idempotency holds when invoked through
    the dispatcher loop."""
    _seed_claude_md(tmp_path)
    ensure_context_files_for_all_providers(tmp_path)
    second = ensure_context_files_for_all_providers(tmp_path)
    # Every non-'self' result on the second run is 'exists'
    for fn, status in second.items():
        if fn == "CLAUDE.md":
            assert status == "self"
        else:
            assert status == "exists", (
                f"{fn} second-run status was {status!r} (expected 'exists')"
            )


def test_dispatcher_no_source_when_claude_md_missing(tmp_path):
    """Without CLAUDE.md, every provider reports no_source — caller
    knows nothing was mirrored."""
    results = ensure_context_files_for_all_providers(tmp_path)
    assert results.get("AGENTS.md") == "no_source"
    assert results.get("AIDER.md") == "no_source"
    assert results.get("CLAUDE.md") == "self"
    # No files created
    assert not (tmp_path / "AGENTS.md").exists()
    assert not (tmp_path / "AIDER.md").exists()


def test_dispatcher_skips_providers_without_context_file(tmp_path):
    """If a provider declares context_file=None / empty, the
    dispatcher skips it entirely (no entry in the result dict).
    Currently every bundled provider declares one, so this test
    constructs a stub provider via the registry override path."""
    from bterminal.providers import get_registry
    registry = get_registry()
    # All bundled providers HAVE a context_file — verify that
    for name in registry.names():
        prov = registry.get(name)
        assert prov.capabilities.context_file, (
            f"{name} no longer declares a context_file — update this "
            f"test or fix the registry"
        )


# ─── Explicit Claude=CLAUDE.md mirror is a no-op ──────────────────────────


def test_claude_provider_does_not_create_mirror_of_itself(tmp_path):
    """Pin: even if a future bug puts CLAUDE.md as a 'mirror target',
    the helper short-circuits with 'self' so we never accidentally
    create CLAUDE.md → CLAUDE.md (which would be a self-link loop)."""
    _seed_claude_md(tmp_path, content="dont touch me")
    result = ensure_context_file_alongside_claude(tmp_path, "CLAUDE.md")
    assert result == "self"
    # CLAUDE.md content untouched (not converted to a symlink to itself)
    assert (tmp_path / "CLAUDE.md").is_file()
    assert not (tmp_path / "CLAUDE.md").is_symlink()
    assert (tmp_path / "CLAUDE.md").read_text() == "dont touch me"


# ─── Backward-compat shim ─────────────────────────────────────────────────


def test_legacy_ensure_agents_md_shim_still_works(tmp_path):
    """The pre-#92 callers (and the existing test_ctx_init.py suite)
    use ensure_agents_md_alongside_claude. The shim must keep working
    so we don't break the world during the transition."""
    from bterminal.ctx.helpers import ensure_agents_md_alongside_claude
    _seed_claude_md(tmp_path)
    result = ensure_agents_md_alongside_claude(tmp_path)
    assert result == "symlink"
    assert (tmp_path / "AGENTS.md").is_symlink()
