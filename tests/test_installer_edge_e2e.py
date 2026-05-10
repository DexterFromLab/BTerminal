"""Pin tests for tools/test_installer_edge_vm.sh — installer edge cases (#162).

Validates the script structure + that install.sh actually has the
guards each test asserts. If install.sh regresses (e.g. someone
removes the trap or the flock), pin tests fire BEFORE the VM run.
"""
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_installer_edge_vm.sh"
INSTALL_SH = REPO_ROOT / "install.sh"


def test_script_exists_and_executable():
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)


def test_script_passes_bash_syntax_check():
    res = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr


def test_script_documents_all_5_scenarios():
    src = SCRIPT.read_text()
    for header in (
        "(a) Offline",
        "(b) SIGTERM mid-install",
        "(c) Corrupt LICENSE.md",
        "(d) Read-only target",
        "(e) Parallel install — flock",
    ):
        assert header in src, f"missing scenario: {header}"


def test_script_uses_safe_test_isolation():
    """Pin: scenarios must NOT mutate the VM's real $HOME or
    /home/michal/.config/bterminal — they use /tmp scratch dirs."""
    src = SCRIPT.read_text()
    assert "HOME=/tmp/" in src, (
        "must redirect HOME to /tmp scratch dir, never use real VM home"
    )
    # Must clean up scratch dirs
    assert "rm -rf /tmp/sigterm-home" in src or \
           "rm -rf /tmp/ro-home" in src


def test_script_locale_agnostic_error_check():
    """Pin: VM may have Polish locale → 'Brak dostępu' instead of
    'Permission denied'. Pattern must accept both."""
    src = SCRIPT.read_text()
    # Check for either explicit polish OR generic markers like
    # BTERMINAL_FRESH_INSTALL_FAILED
    assert "Brak dost" in src or "BTERMINAL_FRESH_INSTALL_FAILED" in src, (
        "scenario (d) must accept locale-agnostic failure markers"
    )


def test_script_distinguishes_running_sudo_from_doc_strings():
    """Pin: --no-sudo prints MANUAL install instructions that contain
    'sudo apt install ...' as documentation. The check must NOT
    flag those as actual sudo calls."""
    src = SCRIPT.read_text()
    # Look for stricter matcher (e.g. ^Running: sudo or "$ sudo apt")
    assert ("'^Running: sudo|^\\\\\\$ sudo apt'" in src or
            "Running: sudo" in src), (
        "must distinguish actual sudo calls from doc-string patterns"
    )


def test_script_uses_combined_grep_for_count_sums():
    """Pin: BUG fix — `grep -c file1 file2` outputs `file:N\\nfile:N`
    which makes integer comparison fail. Use `cat … | grep -c` for
    summed count."""
    src = SCRIPT.read_text()
    # Look for the fix pattern
    assert "cat /tmp/edge-flock-1.log /tmp/edge-flock-2.log | grep -c" in src, (
        "use cat-then-grep -c for multi-file traceback sum"
    )


# ── install.sh guard pin tests ────────────────────────────────────────────


def test_install_sh_has_flock_guard():
    """Pin: scenario (e) needs flock; verify install.sh actually has it."""
    src = INSTALL_SH.read_text()
    assert "flock -n 9" in src, "install.sh must use flock -n on FD 9"
    assert "BTERMINAL_INSTALL_LOCKED" in src, (
        "install.sh must emit BTERMINAL_INSTALL_LOCKED marker for "
        "consumers (BT GUI / E2E test grep)"
    )


def test_install_sh_has_signal_trap():
    """Pin: scenario (b) needs SIGINT/SIGTERM trap that triggers
    rollback. install.sh's `trap _on_interrupt INT TERM` is the
    canonical mechanism."""
    src = INSTALL_SH.read_text()
    assert "trap '_on_interrupt' INT TERM" in src or \
           ("_on_interrupt" in src and "trap" in src), (
        "install.sh must trap INT TERM with rollback handler"
    )


def test_install_sh_has_rollback_marker():
    """Pin: trap must emit a marker so test (b)/(d) can grep for it.
    BTERMINAL_INTERRUPT_NO_BACKUP / BTERMINAL_FRESH_INSTALL_FAILED."""
    src = INSTALL_SH.read_text()
    assert "BTERMINAL_INTERRUPT" in src
    assert "BTERMINAL_FRESH_INSTALL_FAILED" in src or \
           "FRESH_INSTALL_FAILED" in src


def test_install_sh_has_no_sudo_mode():
    """Pin: scenario (a) needs --no-sudo flag. Without it apt phase
    blocks on password prompt and test hangs."""
    src = INSTALL_SH.read_text()
    assert "--no-sudo" in src, "install.sh must support --no-sudo flag"


def test_install_sh_emits_summary_block():
    """Pin: assertions in (a) rely on '[SUMMARY]' or 'installed
    successfully' appearing on success path."""
    src = INSTALL_SH.read_text()
    has_summary = ("[SUMMARY]" in src or "installed successfully" in src)
    assert has_summary, (
        "install.sh must print summary marker on completion"
    )
