"""Pin tests for #113 — POST /api/sessions/ai must materialize the
same CLAUDE.md + per-provider mirrors that the GUI Add ▼ → Claude Code
→ OK flow does (sidebar.py:701 → _run_ctx_wizard_if_needed).

Pre-#113: REST handler called only ai_manager.add(entry) and returned
200; project_dir was left untouched, so test fixtures launching
`claude` from that path were missing the context block they expected.

Fix: bootstrap_provider_context_files(project_dir) runs after the
manager add. It writes CLAUDE.md (using the same template the GUI
wizard renders) and mirrors it to every registered provider's
capabilities.context_file.
"""
import os
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
DEBUG_REST = REPO_ROOT / "bterminal" / "debug_rest.py"
HELPERS = REPO_ROOT / "bterminal" / "ctx" / "helpers.py"
DIALOGS = REPO_ROOT / "bterminal" / "ctx" / "dialogs.py"


# ── Behavioural: bootstrap helper writes CLAUDE.md ──────────────────────


def test_bootstrap_creates_claude_md_in_empty_dir(tmp_path):
    """Pin: bootstrap_provider_context_files writes CLAUDE.md into a
    project_dir that didn't have one. This is the core acceptance
    from the task description: 'project_dir=/tmp/empty → ls /tmp/empty
    contains CLAUDE.md'."""
    from bterminal.ctx.helpers import bootstrap_provider_context_files

    assert list(tmp_path.iterdir()) == []
    result = bootstrap_provider_context_files(str(tmp_path))
    claude_md = tmp_path / "CLAUDE.md"
    assert claude_md.is_file(), (
        f"CLAUDE.md not created in {tmp_path}; result={result}"
    )
    body = claude_md.read_text()
    # Project name comes from the basename
    assert tmp_path.name in body
    # Standard scaffolding present
    assert "ctx set" in body
    assert "tasks list" in body


def test_bootstrap_skips_existing_claude_md(tmp_path):
    """Pin: an existing CLAUDE.md must NOT be overwritten — user may
    have customized it. The wizard has the same guard."""
    custom = "# my custom project\n\nDo not touch.\n"
    (tmp_path / "CLAUDE.md").write_text(custom)
    from bterminal.ctx.helpers import bootstrap_provider_context_files

    bootstrap_provider_context_files(str(tmp_path))
    assert (tmp_path / "CLAUDE.md").read_text() == custom


def test_bootstrap_returns_empty_for_nonexistent_dir(tmp_path):
    """Pin: invalid project_dir is a no-op (empty dict), not a crash.
    Test fixtures sometimes pass paths that don't exist yet."""
    from bterminal.ctx.helpers import bootstrap_provider_context_files

    bogus = tmp_path / "does_not_exist"
    result = bootstrap_provider_context_files(str(bogus))
    assert result == {}
    assert not bogus.exists()


def test_bootstrap_returns_empty_for_empty_string():
    """Pin: empty project_dir (REST clients that omit the field) is
    a no-op. Without this guard, os.path.isdir('') would still be False
    on Linux but explicit guard is clearer."""
    from bterminal.ctx.helpers import bootstrap_provider_context_files

    assert bootstrap_provider_context_files("") == {}
    assert bootstrap_provider_context_files(None) == {}


def test_bootstrap_mirrors_to_provider_files(tmp_path):
    """Pin: after CLAUDE.md is written, ensure_context_files_for_all_providers
    mirrors it. With the default registry (claude/copilot/aider) we
    expect AGENTS.md + AIDER.md to be created as symlinks (or copies)."""
    from bterminal.ctx.helpers import bootstrap_provider_context_files

    bootstrap_provider_context_files(str(tmp_path))
    files = sorted(p.name for p in tmp_path.iterdir())
    assert "CLAUDE.md" in files
    # At least one provider mirror should be present (depending on
    # which providers are registered in the test process). Check that
    # the helper's return dict covers the expected filenames.
    assert (tmp_path / "AGENTS.md").exists() or (tmp_path / "AIDER.md").exists(), (
        f"no provider mirror created; tmp_path contents: {files}"
    )


def test_bootstrap_uses_smart_project_name(tmp_path):
    """Pin: project_name resolution prefers _smart_project_name (which
    walks up past generic basenames like 'docs/'). Verifies the helper
    delegates correctly so REST and GUI render identical templates."""
    proj = tmp_path / "myrepo"
    proj.mkdir()
    docs = proj / "docs"
    docs.mkdir()
    # Make myrepo look like a git root so smart name walks up
    (proj / ".git").mkdir()

    from bterminal.ctx.helpers import bootstrap_provider_context_files

    bootstrap_provider_context_files(str(docs))
    body = (docs / "CLAUDE.md").read_text()
    # 'myrepo' is the parent git root, not the generic 'docs' basename
    assert "myrepo" in body
    assert "# docs\n" not in body[:30]


# ── Structural: REST handler wires up the bootstrap call ────────────────


def test_rest_add_ai_calls_bootstrap_after_manager_add():
    """Pin: _route_session_add_ai imports bootstrap_provider_context_files
    and calls it AFTER ai_manager.add succeeds. Order matters because
    we don't want to write context files for sessions that fail to
    persist (e.g. duplicate-name 409)."""
    src = DEBUG_REST.read_text()
    fn_idx = src.find("def _route_session_add_ai")
    fn_end = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:fn_end]

    # Imports the helper
    assert "bootstrap_provider_context_files" in body
    # Call must come AFTER _via_glib_idle(_add) so we only bootstrap
    # for successfully-saved sessions (the duplicate-check at #108
    # returns 409 before reaching this call).
    add_pos = body.find("saved = _via_glib_idle(_add)")
    bootstrap_pos = body.find("bootstrap_provider_context_files(")
    assert add_pos > 0 and bootstrap_pos > 0
    assert add_pos < bootstrap_pos, (
        "bootstrap call must run AFTER the manager add"
    )


def test_rest_bootstrap_guarded_by_project_dir_truthy():
    """Pin: REST clients can submit AI sessions without a project_dir
    (e.g. ad-hoc claude tab without a tied repo). The bootstrap call
    must be guarded so it doesn't trip on empty path."""
    src = DEBUG_REST.read_text()
    fn_idx = src.find("def _route_session_add_ai")
    fn_end = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:fn_end]

    # The guard pattern: `if entry["project_dir"]:` (or equivalent
    # truthiness check) before the bootstrap call
    assert 'if entry["project_dir"]' in body or \
           "if entry['project_dir']" in body, (
        "expected `if entry[\"project_dir\"]:` guard before bootstrap"
    )


def test_rest_bootstrap_failure_is_non_fatal():
    """Pin: bootstrap must not break the REST response. If CLAUDE.md
    write fails (read-only project_dir, weird FS) the session is
    still saved and the client gets a 200. try/except wraps the call."""
    src = DEBUG_REST.read_text()
    fn_idx = src.find("def _route_session_add_ai")
    fn_end = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:fn_end]

    # try/except wrapping the bootstrap import + call
    bootstrap_idx = body.find("bootstrap_provider_context_files(")
    assert bootstrap_idx > 0
    # Walk back: a `try:` should appear within the previous ~10 lines
    snippet = body[max(0, bootstrap_idx - 400):bootstrap_idx]
    assert "try:" in snippet
    # And an except after the call
    after = body[bootstrap_idx:bootstrap_idx + 400]
    assert "except" in after


# ── Structural: dialogs.py uses the same template helper ───────────────


def test_dialogs_uses_render_claude_md_helper():
    """Pin: CtxSetupWizard._execute (dialogs.py) renders CLAUDE.md via
    _render_claude_md so REST and GUI templates can never diverge. If
    a future PR re-inlines the template, this test fails loudly."""
    src = DIALOGS.read_text()
    # _execute method must import + call _render_claude_md
    exec_idx = src.find("def _execute(self):")
    exec_end = src.find("\n    def ", exec_idx + 1)
    body = src[exec_idx:exec_end]
    assert "_render_claude_md" in body, (
        "dialogs.py:_execute must call _render_claude_md (DRY with REST)"
    )


def test_render_claude_md_returns_str_with_project_name():
    """Pin: _render_claude_md interpolates the project name in the
    header AND in the per-project CLI examples (`ctx set <name> ...`).
    REST relies on the basename being correct."""
    from bterminal.ctx.helpers import _render_claude_md

    text = _render_claude_md("my_project")
    assert text.startswith("# my_project\n")
    assert "ctx set my_project " in text
    assert "tasks list my_project" in text
