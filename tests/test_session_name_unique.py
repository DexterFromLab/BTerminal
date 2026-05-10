"""Pin tests for #108 — sidebar session name uniqueness validation.

Two enforcement points:
1. ClaudeCodeDialog.validate() — UI add/edit dialog rejects duplicate
   names against parent app's claude_manager.
2. POST /api/sessions/ai REST endpoint — returns 409 when name already
   in use (blocks REST test fixtures from creating duplicates).
"""
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
DIALOG = REPO_ROOT / "bterminal" / "ui" / "dialogs" / "claude_code.py"
DEBUG_REST = REPO_ROOT / "bterminal" / "debug_rest.py"


def test_dialog_stashes_editing_session_for_validate():
    """Pin: ClaudeCodeDialog.__init__ must stash `session` arg as
    self._editing_session so validate() can skip-self when checking
    duplicates in edit mode."""
    src = DIALOG.read_text()
    assert "self._editing_session = session" in src
    assert "self._parent_app = parent" in src


def test_dialog_validate_rejects_duplicate_name():
    """Pin: validate() iterates parent.claude_manager.all() and
    rejects when a same-name session exists (skipping self by id
    in edit mode)."""
    src = DIALOG.read_text()
    fn_idx = src.find("def validate(self):")
    assert fn_idx > 0, "validate() method not found"
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    # Must call show_error with "already in use" message
    assert "already in use" in body
    # Must iterate manager.all() to find duplicates
    assert "claude_manager.all()" in body
    # Must skip self by id in edit mode
    assert "editing_id" in body
    assert 's.get("id") == editing_id' in body
    # Must add error css class to entry for visual feedback
    assert "entry_name.get_style_context().add_class(\"error\")" in body


def test_dialog_validate_clears_error_class_on_retry():
    """Pin: every validate() call MUST start by removing 'error' class
    so the red border clears when the user fixes the name."""
    src = DIALOG.read_text()
    fn_idx = src.find("def validate(self):")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "remove_class(\"error\")" in body


def test_rest_endpoint_rejects_duplicate_name():
    """Pin: POST /api/sessions/ai returns 409 when name already in
    use (otherwise test fixtures can pile up duplicate sessions)."""
    src = DEBUG_REST.read_text()
    fn_idx = src.find("def _route_session_add_ai")
    fn_end = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    # Must check against ai_manager.all() before insertion
    assert "ai_manager.all()" in body
    # Must return 409 conflict (not 200) on duplicate
    assert "409" in body
    assert "already in use" in body


def test_rest_check_runs_BEFORE_insertion():
    """Pin: duplicate check must run BEFORE ai_manager.add(entry)
    — otherwise the add succeeds and then we return 409 with the
    duplicate already saved."""
    src = DEBUG_REST.read_text()
    fn_idx = src.find("def _route_session_add_ai")
    fn_end = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    # Position of duplicate check must precede `def _add`
    dup_pos = body.find("for existing in app.ai_manager.all()")
    add_pos = body.find("def _add():")
    assert dup_pos > 0, "no duplicate check found"
    assert add_pos > 0
    assert dup_pos < add_pos, (
        "duplicate check must come BEFORE _add() definition"
    )
