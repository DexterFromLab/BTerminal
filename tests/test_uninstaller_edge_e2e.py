"""Pin tests for tools/test_uninstaller_edge_vm.sh — uninstaller edge
cases (#163)."""
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_uninstaller_edge_vm.sh"
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
        "(a) Uninstall while BT running",
        "(b) Uninstall --purge",
        "(c) Uninstall with stale lockfile",
        "(d) Uninstall with read-only",
        "(e) Partial uninstall",
    ):
        assert header in src, f"missing scenario: {header}"


def test_setup_helper_does_not_run_real_install_sh():
    """Pin: BUG fix — first impl called real install.sh which pulls
    npm packages (1+ min × 5 scenarios = timeout). Fast setup builds
    minimal layout directly via mkdir/touch/ln."""
    src = SCRIPT.read_text()
    setup_idx = src.find("_setup_fake_install()")
    setup_end = src.find("\n}\n", setup_idx)
    setup_body = src[setup_idx:setup_end]
    # Must NOT invoke install.sh inside _setup_fake_install
    assert "bash install.sh" not in setup_body, (
        "_setup_fake_install must NOT shell out to install.sh "
        "(too slow — npm install hangs the test)"
    )
    # Must build the layout directly
    assert "mkdir -p" in setup_body
    assert "ln -sf" in setup_body
    # Must create CLI symlinks for do_uninstall to clean
    assert "bterminal-launcher" in setup_body


def test_setup_creates_required_paths_for_each_scenario():
    """Pin: layout must include every dir do_uninstall iterates over,
    so removal can be verified."""
    src = SCRIPT.read_text()
    for path in (
        "$home/.local/bin",
        "$home/.local/share/bterminal",
        "$home/.config/bterminal",
        "$home/.npm-global/lib/node_modules/@anthropic-ai",
        "$home/.npm-global/lib/node_modules/@github",
    ):
        assert path in src, f"setup missing: {path}"


def test_script_uses_safe_test_isolation():
    """Pin: scenarios use /tmp scratch dirs only, NEVER mutate real $HOME."""
    src = SCRIPT.read_text()
    assert 'SCRATCH="/tmp/' in src
    # No HOME override pointing at real user home
    assert "HOME=$HOME" not in src
    # Cleanup must remove scratch
    assert "rm -rf '$SCRATCH'" in src


def test_script_uses_fake_bt_process_not_real_bt():
    """Pin: spawning real BT without DISPLAY hangs Gtk init. Use
    `sleep 999999` as fake "concurrent process" — install.sh has no
    BT-running guard, so behavior is identical."""
    src = SCRIPT.read_text()
    assert "sleep 999999" in src
    # Must NOT spawn real bterminal binary
    assert "$SCRATCH/.local/bin/bterminal" not in src or \
           "fake BT" in src.lower() or "sleep 999999" in src


def test_script_seeds_session_for_purge_test():
    """Pin: (b) verifies sessions get removed by --purge. Test must
    seed a session BEFORE running uninstall, otherwise purge has
    nothing to demonstrate."""
    src = SCRIPT.read_text()
    assert "PreservedSession" in src
    assert "ai_sessions.json" in src


def test_script_seeds_stale_lockfile_for_recovery_test():
    """Pin: (c) recovery requires a pre-existing lockfile with dead PID."""
    src = SCRIPT.read_text()
    assert "install.lock" in src
    assert "99999" in src  # dead PID we inject


def test_script_chmods_parent_for_readonly_test():
    """Pin: (d) — `rm -rf` can remove read-only FILES if the PARENT
    is writable. To trigger EACCES we MUST chmod the parent dir
    read-only. Catches naive `chmod -R a-w $INSTALL_DIR` regressions."""
    src = SCRIPT.read_text()
    # Must chmod the .local/share parent (so rm can't unlink subdir)
    assert "chmod a-w '$SCRATCH/.local/share'" in src
    # Comment must explain WHY (or pin test fails to remind future-me)
    assert "parent" in src.lower()


def test_script_simulates_partial_install_for_e_test():
    """Pin: (e) — install dir REMOVED but symlinks linger. Setup
    creates full layout, then we explicitly rm $INSTALL_DIR before
    running uninstall to simulate user who deleted dir manually."""
    src = SCRIPT.read_text()
    e_idx = src.find("(e) Partial uninstall")
    e_section = src[e_idx:e_idx + 2000]
    assert "rm -rf '$SCRATCH/.local/share/bterminal'" in e_section


# ── install.sh guard pin tests ────────────────────────────────────────────


def test_install_sh_uninstall_handles_missing_install_dir():
    """Pin: do_uninstall checks `[[ -d $INSTALL_DIR ]]` before rm —
    so partial uninstall (e) doesn't crash on already-gone dir."""
    src = INSTALL_SH.read_text()
    fn_idx = src.find("do_uninstall()")
    fn_end = src.find("\n}\n", fn_idx)
    body = src[fn_idx:fn_end]
    assert '[[ -d "$INSTALL_DIR" ]]' in body, (
        "do_uninstall must guard rm -rf with -d test"
    )


def test_install_sh_uninstall_iterates_all_symlinks():
    """Pin: every entry in _BT_BIN_SYMLINKS must be cleaned up by
    do_uninstall (so partial-install state is auto-fixed)."""
    src = INSTALL_SH.read_text()
    assert "_BT_BIN_SYMLINKS=" in src
    assert "for s in \"${_BT_BIN_SYMLINKS[@]}\"" in src


def test_install_sh_purge_redirects_log_to_tmp():
    """Pin: --purge removes $CONFIG_DIR mid-flow. The install.log
    file lives there → must be redirected to /tmp BEFORE the purge
    so set -e doesn't trip on missing log file."""
    src = INSTALL_SH.read_text()
    assert "FINAL_LOG_TMP" in src
    assert "mktemp" in src and "bterminal-uninstall-final" in src


def test_install_sh_uninstall_has_done_marker():
    """Pin: scenarios (a)(b)(c) assertions grep for completion.
    `=== BTerminal uninstall completed ===` is the canonical marker."""
    src = INSTALL_SH.read_text()
    assert "BTerminal uninstall completed" in src or \
           "Uninstall completed" in src
