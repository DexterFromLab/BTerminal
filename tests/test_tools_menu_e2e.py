"""Pin tests for tools/test_tools_menu_vm.sh — Tools menu E2E (#159)."""
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_tools_menu_vm.sh"


def test_script_exists_and_executable():
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)


def test_script_passes_bash_syntax_check():
    res = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr


def test_script_documents_all_4_subtests():
    src = SCRIPT.read_text()
    for header in (
        "(a) Tools → Check for updates",
        "(b) Tools → Errata",
        "(c) Tools → Diagnostics",
        "(d) Tools → Install dependencies",
    ):
        assert header in src, f"missing header: {header}"


def test_script_uses_tools_menu_navigation():
    """Pin: Tools is the 3rd menubar item — F10 + Right + Right enters it."""
    src = SCRIPT.read_text()
    assert "F10 Right Right Return" in src, (
        "must enter Tools via F10 + Right×2 + Return"
    )


def test_script_dismiss_dialog_does_not_use_alt_f4():
    """Pin: Alt+F4 closes the BT main window once the modal dismisses,
    killing the entire test run. Confirmed in iteration when (a)
    dismiss with alt+F4 → BT exited → (b)(c)(d) all failed.
    Use Esc + Return (Return = default Close button) instead."""
    src = SCRIPT.read_text()
    fn_idx = src.find("_dismiss_dialog()")
    fn_end = src.find("\n}\n", fn_idx + 1)
    fn_body = src[fn_idx:fn_end]
    assert "alt+F4" not in fn_body, (
        "Alt+F4 BANNED in _dismiss_dialog — would close BT main window"
    )
    assert "Escape" in fn_body
    assert "Return" in fn_body, (
        "Return needed for Checking-for-updates dialog (Esc ignored)"
    )


def test_script_checks_installer_wizard_NOT_error_dialog():
    """Pin: bug #148 (Cannot locate install.sh) regression catch.
    Test must distinguish wizard window from error dialog."""
    src = SCRIPT.read_text()
    assert "Cannot locate" in src or "Cannot" in src, (
        "must explicitly check for 'Cannot locate install.sh' regression"
    )
    assert "Installer" in src
    assert "Welcome" in src or "Step 1" in src


def test_script_checks_all_4_dialog_titles():
    """Pin: each sub-test verifies a specific window title appears.
    Catches regressions where a dialog opens but with different/empty title."""
    src = SCRIPT.read_text()
    for title in ("Checking for updates", "errata", "Diagnostics", "Installer"):
        assert title in src, f"missing dialog title check: {title}"


def test_script_uses_live_monitor():
    src = SCRIPT.read_text()
    assert "_e2e_live_monitor.sh" in src
    assert '"$MONITOR" tag' in src
    for tag in ("01a-updates-dialog", "02b-errata-dialog",
                "03c-diagnostics-dialog", "04d-installer-wizard"):
        assert tag in src, f"missing tag: {tag}"


def test_script_supports_respawn_flag():
    src = SCRIPT.read_text()
    assert "VM_RESPAWN" in src
