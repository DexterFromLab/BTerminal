"""Pin tests for tools/test_view_menu_vm.sh — View menu E2E (#158)."""
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_view_menu_vm.sh"


def test_script_exists_and_executable():
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)


def test_script_passes_bash_syntax_check():
    res = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr


def test_script_documents_all_subtests():
    src = SCRIPT.read_text()
    for header in (
        "(a) Ctrl+B toggle sidebar",
        "(b) Ctrl+G toggle Git panel",
        "(c) View → Toggle theme",
        "(d) Panel switchers",
    ):
        assert header in src, f"missing: {header}"


def test_script_uses_window_state_endpoint():
    """Pin: View menu E2E depends on /api/window/state. Catch
    accidental regression on debug_rest.py route table."""
    src = SCRIPT.read_text()
    assert "/api/window/state" in src
    assert "_get_window_state" in src
    for field in ("sidebar_visible", "git_visible", "theme",
                  "sidebar_active_panel"):
        assert field in src, f"missing state field probe: {field}"


def test_script_drives_via_keyboard_shortcut_for_a_b():
    """Pin: ctrl+b / ctrl+g exercise REAL global accelerators."""
    src = SCRIPT.read_text()
    assert "ctrl+b" in src
    assert "ctrl+g" in src


def test_script_drives_via_menu_for_theme_and_panels():
    """Pin: theme + 5x panel switchers go through the View menu via
    F10 → Right → Return chain. This catches regressions in menu
    structure (item order changes)."""
    src = SCRIPT.read_text()
    assert "F10 Right Return" in src, (
        "theme + panel switchers must enter View via F10+Right"
    )


def test_script_tests_all_5_panels():
    """Pin: every panel name from app.py menu is tested."""
    src = SCRIPT.read_text()
    for panel in ("sessions", "ctx", "consult", "tasks", "plugins"):
        assert f'_panel_test "{panel}"' in src, f"missing panel: {panel}"


def test_script_uses_live_monitor():
    src = SCRIPT.read_text()
    assert "_e2e_live_monitor.sh" in src
    assert '"$MONITOR" tag' in src
    assert '"$MONITOR" start' in src


def test_window_state_endpoint_present_in_debug_rest():
    """Pin: /api/window/state is registered in the route table. If the
    route is removed or renamed, the View menu E2E breaks silently
    (script falls back to "endpoint missing — old build" error)."""
    src = (REPO_ROOT / "bterminal" / "debug_rest.py").read_text()
    assert "_route_window_state" in src
    assert '"/api/window/state"' in src or "/api/window/state" in src
    # The endpoint must include all 4 fields the test reads
    for field in ("sidebar_visible", "sidebar_active_panel",
                  "git_visible", "theme"):
        assert field in src, f"endpoint missing field: {field}"


def test_window_state_returns_only_safe_data():
    """Pin: the endpoint must NOT mutate state — read-only contract.
    Catches accidental refactor that uses toggle_sidebar() to query."""
    src = (REPO_ROOT / "bterminal" / "debug_rest.py").read_text()
    fn_idx = src.find("def _route_window_state")
    fn_end = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    # Must not call toggle_*
    assert "toggle_sidebar" not in body
    assert "toggle_git_panel" not in body
    assert "set_visible_child_name" not in body  # mutation
