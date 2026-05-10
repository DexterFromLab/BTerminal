"""Pin tests for tools/test_file_menu_vm.sh — File menu E2E (#157).

Validates the script structure (bash -n, key sub-tests present, helpers
documented). The actual VM run is exercised manually or by CI hosts that
have ssh access to vm-test; this test file just guards against script
regressions on hosts without VM.
"""
import os
import subprocess
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_file_menu_vm.sh"


def test_script_exists_and_executable():
    assert SCRIPT.is_file()
    assert os.access(SCRIPT, os.X_OK)


def test_script_passes_bash_syntax_check():
    res = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True,
    )
    assert res.returncode == 0, res.stderr


def test_script_documents_all_five_subtests():
    """Pin: each File menu item gets its own (a)..(e) header."""
    src = SCRIPT.read_text()
    for header in (
        "(a) File → New local tab",
        "(b) File → New SSH session",
        "(c) File → New Claude Code session",
        "(d) File → Options",
        "(e) File → Quit",
    ):
        assert header in src, f"missing header: {header}"


def test_script_uses_f10_not_alt_f():
    """Pin: F10 is required to enter menubar (Alt+F goes to VTE bash
    readline 'forward-word'). Catches regressions where someone reverts
    to Alt+F."""
    src = SCRIPT.read_text()
    assert "F10" in src, "must use F10 to enter menubar"
    # Strip block comments before forbidding alt+f literal
    code = "\n".join(
        line for line in src.splitlines()
        if not line.strip().startswith("#")
    )
    assert "alt+f" not in code.lower(), \
        "Alt+F goes to VTE bash readline, not menubar"


def test_script_uses_chained_xdotool_keys():
    """Pin: Down+Return must be sent as ONE chained xdotool call —
    multiple ssh hops between keys risk losing menu focus."""
    src = SCRIPT.read_text()
    assert "xdotool key --delay 100 Down Return" in src or \
           "xdotool key --delay 100 Return" in src, (
        "key chains must be single xdotool invocation"
    )


def test_script_integrates_with_live_monitor():
    """Pin: uses _e2e_live_monitor.sh (#156) for screenshot evidence."""
    src = SCRIPT.read_text()
    assert "_e2e_live_monitor.sh" in src
    assert "MONITOR" in src
    # Must call at least: start, tag, stop
    for cmd in ('"$MONITOR" tag', '"$MONITOR" start', '"$MONITOR" stop'):
        assert cmd in src, f"missing monitor call: {cmd}"


def test_script_uses_rest_assertions():
    """Pin: REST assertions are more reliable than visual-only."""
    src = SCRIPT.read_text()
    assert "_rest_health_ok" in src
    assert "_rest_load_token" in src
    assert "/api/tabs" in src
    assert "/api/health" in src


def test_script_supports_quick_and_respawn_flags():
    src = SCRIPT.read_text()
    assert "VM_QUICK" in src
    assert "VM_RESPAWN" in src


def test_script_focuses_precise_bt_window_pattern():
    """Pin: gnome-terminal w/ cwd=~/BTerminal also matches "BTerminal".
    Must use precise pattern that excludes shell windows."""
    src = SCRIPT.read_text()
    assert "BTerminal — Terminal" in src, (
        "must focus precise BT window, not gnome-terminal cwd matches"
    )


def test_script_cleanup_traps_monitor_stop():
    """Pin: trap EXIT must stop live monitor — otherwise bg ssh leaks."""
    src = SCRIPT.read_text()
    assert "trap " in src and "stop" in src
    assert "EXIT" in src
