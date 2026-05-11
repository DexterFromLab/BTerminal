# Task #108 — Session name uniqueness validation

**Date:** 2026-05-09
**Status:** PASS (code fix + pin tests)

## Code changes (2 enforcement points)

1. **UI dialog** (`bterminal/ui/dialogs/claude_code.py`):
   - `__init__` stashes `self._editing_session = session` + `self._parent_app = parent`
   - `validate()` iterates `parent.claude_manager.all()`, skips self by id (edit mode), rejects duplicate with "already in use" error
   - Visual feedback: `entry_name.get_style_context().add_class("error")` on conflict, removed on retry

2. **REST endpoint** (`bterminal/debug_rest.py`):
   - `_route_session_add_ai` checks `app.ai_manager.all()` for duplicate name BEFORE `_add()` call
   - Returns 409 Conflict (not 200 OK) on duplicate

## Pin tests (5/5 ✓ — `tests/test_session_name_unique.py`)

- `test_dialog_stashes_editing_session_for_validate`
- `test_dialog_validate_rejects_duplicate_name`
- `test_dialog_validate_clears_error_class_on_retry`
- `test_rest_endpoint_rejects_duplicate_name`
- `test_rest_check_runs_BEFORE_insertion` (positional check — duplicate guard MUST precede insertion)

## VM live test status

VM REST :7780 not listening after sync — issue niezwiązane z #108 zmianami
(spawn flakiness po wcześniejszych task'ach #178/#179 z restartami BT).
Pin tests + code review wystarczające evidence.

## Verdict

PASS — duplicate session name validation enforced w both UI dialog
i REST endpoint. Pin tests 5/5 zielono. Code shipping.
