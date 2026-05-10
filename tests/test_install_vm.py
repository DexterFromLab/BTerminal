"""Pytest wrapper for tools/test_install_vm.sh (#13 / #85).

Source-level checks + an opt-in 'real VM run' that's skipped unless
BTERMINAL_VM_TESTS=1 is set in env. The opt-in keeps slow/sudo-needing
runs out of the default `pytest tests/` path.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_install_vm.sh"


# ─── Source-level checks ───────────────────────────────────────────────────


def test_install_vm_script_exists_and_executable():
    assert SCRIPT.is_file()
    mode = SCRIPT.stat().st_mode
    assert mode & 0o111


def test_install_vm_script_bash_syntax_valid():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_install_vm_script_help_returns_zero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    # All three documented flags must show up in help
    assert "--modes" in result.stdout
    assert "--skip-rollback" in result.stdout
    assert "--no-sudo" in result.stdout


def test_install_vm_script_unknown_flag_exits_nonzero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--bogus"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0


@pytest.mark.parametrize("mode_label", [
    "[A] install.sh --no-sudo",
    "[B] install.sh --headless --selected meld",
    "[C] install.sh --headless --selected llama",
])
def test_install_vm_announces_each_mode(mode_label):
    """Each of the 3 modes prints a recognizable banner."""
    text = SCRIPT.read_text()
    assert mode_label in text, f"mode banner missing: {mode_label!r}"


def test_install_vm_uses_vm_sync_helper():
    """Always sync first via the canonical helper."""
    text = SCRIPT.read_text()
    assert "vm_sync.sh" in text


def test_install_vm_asserts_canonical_post_install_layout():
    """assert_layout() helper validates bterminal/__init__.py + bin
    symlinks for ctx/tasks/consult/memory_wizard."""
    text = SCRIPT.read_text()
    assert "bterminal/__init__.py" in text
    for cli in ("ctx", "tasks", "consult", "memory_wizard"):
        assert f"~/.local/bin/{cli}" in text


def test_install_vm_validates_summary_and_success_marker():
    """Mode A/B asserts include both [SUMMARY] block + 'installed
    successfully' marker."""
    text = SCRIPT.read_text()
    assert "SUMMARY" in text
    assert "installed successfully" in text


def test_install_vm_mode_b_validates_status_json_stream():
    """Mode B passes --status-json so the runner verifies the JSON
    stream contains the terminal phase=done progress=100 event."""
    text = SCRIPT.read_text()
    assert '"phase": "done"' in text
    assert '"progress": 100' in text


def test_install_vm_mode_b_validates_selected_whitelist():
    """Mode B's --selected meld must demonstrably gate latex/pandoc
    via the 'not in --selected list' message — visible in install.sh
    output when the whitelist is honored."""
    text = SCRIPT.read_text()
    assert "not in --selected list" in text


def test_install_vm_rollback_test_present():
    """The rollback phase corrupts a mid-install path + verifies
    BACKUP_DIR restoration kicks in."""
    text = SCRIPT.read_text()
    assert "Rollback test" in text or "rollback" in text.lower()
    assert "BTERMINAL_ROLLBACK_OK" in text
    # Hashes pre/post for tamper verification
    assert "PRE_HASH" in text
    assert "POST_HASH" in text


def test_install_vm_skip_rollback_flag_supported():
    text = SCRIPT.read_text()
    assert "--skip-rollback" in text


# ─── #133 — pre-backup failure sub-modes ───────────────────────────────


def test_rollback_test_covers_pre_backup_phase_1_failure():
    """Pin sub-mode (a): rollback test injects `false` AFTER the
    phase 1 banner — before BACKUP_DIR is mktemp'd. Since
    BACKUP_DIR stays empty, _on_error fires the no-backup
    branch + emits FRESH_INSTALL_FAILED instead of ROLLBACK_OK."""
    text = SCRIPT.read_text()
    # Pin sub-mode label
    assert "rollback (a)" in text or "rollback-corrupt-1" in text
    # Phase 1 banner injection
    # Banner literal escaped in sed pattern as `\[1/7\]`
    assert "1/7" in text and "Checking runtime" in text
    # FRESH_INSTALL_FAILED marker must be checked
    assert "BTERMINAL_FRESH_INSTALL_FAILED" in text


def test_rollback_test_covers_pre_backup_phase_2_5_failure():
    """Pin sub-mode (b): phase 2.5 (Copilot check) also runs
    pre-backup. Same FRESH_INSTALL_FAILED outcome. Pin so a
    refactor that moves BACKUP_DIR earlier (e.g. before phase
    1) trips this test loudly."""
    text = SCRIPT.read_text()
    assert "rollback (b)" in text or "rollback-corrupt-25" in text
    # Phase 2.5 banner injection
    assert "2.5/7" in text and "Checking GitHub Copilot CLI" in text


def test_rollback_test_covers_post_backup_phase_5_failure():
    """Pin sub-mode (c): the original rollback test path —
    phase 5 (Files install) is AFTER backup, so ROLLBACK_OK
    fires. Distinct outcome from (a)/(b)."""
    text = SCRIPT.read_text()
    assert "rollback (c)" in text or "phase-5" in text
    # Phase 5 banner injection (existing pattern)
    assert "5/7" in text and "Installing BTerminal files" in text
    # ROLLBACK_OK marker still expected for this branch
    assert "BTERMINAL_ROLLBACK_OK" in text


def test_pre_backup_failure_pins_no_rollback_ok_marker():
    """Pin: pre-backup failures (a) and (b) explicitly assert
    ROLLBACK_OK is NOT present. Without this guard, a regression
    that leaks BACKUP_DIR=anything could trigger ROLLBACK_OK
    even when nothing was backed up."""
    text = SCRIPT.read_text()
    # Both sub-modes (a) and (b) check that ROLLBACK_OK is absent
    assert "no ROLLBACK_OK marker" in text or \
        "no ROLLBACK_OK in phase" in text


def test_pre_backup_failure_checks_user_facing_message():
    """Pin sub-mode (a) verifies the user-facing 'fresh install'
    or 'Fix the error above' message — install.sh's no-backup
    branch in _on_error. Without this hint, users see only an
    exit code."""
    text = SCRIPT.read_text()
    # Either phrase acceptable
    assert "fresh install" in text or "Fix the error above" in text


def test_rollback_uses_three_distinct_log_files_for_sub_modes():
    """Pin: each sub-mode has its own log file so debug can
    locate the right output without grepping. Original
    `rollback.log` (phase-5 / sub-mode c), `rollback-phase1.log`
    (a), `rollback-phase25.log` (b)."""
    text = SCRIPT.read_text()
    assert "rollback.log" in text
    assert "rollback-phase1.log" in text
    assert "rollback-phase25.log" in text


def test_rollback_cleans_up_all_three_corrupted_scripts():
    """Pin: each sub-mode creates a /tmp/install_corrupt*.sh
    and cleans it up afterwards (vm_run rm -f). Without
    cleanup, repeat runs accumulate cruft."""
    text = SCRIPT.read_text()
    assert "rm -f /tmp/install_corrupt.sh" in text
    assert "rm -f /tmp/install_corrupt_1.sh" in text
    assert "rm -f /tmp/install_corrupt_25.sh" in text


def test_pre_backup_test_uses_no_sudo_for_isolation():
    """Pin: --no-sudo passed so the corrupt install can't
    accidentally apt-install something on the test box. Each
    sub-mode honors this."""
    text = SCRIPT.read_text()
    # Find each sub-mode's run block
    for run_marker in ("rollback-run\"", "rollback-run-1\"",
                        "rollback-run-25\""):
        idx = text.find(run_marker)
        assert idx > 0, (
            f"sub-mode runner block {run_marker} missing"
        )
        # Look at next 200 chars for `--no-sudo` flag
        block = text[idx:idx + 300]
        assert "--no-sudo" in block, (
            f"sub-mode {run_marker} doesn't use --no-sudo — could "
            f"escalate apt installs on the test box"
        )


def test_install_vm_outputs_per_run_logs_to_log_dir():
    """Each step's stderr captured to per-name files in LOG_DIR so
    failures are debuggable after the fact."""
    text = SCRIPT.read_text()
    assert "LOG_DIR" in text
    assert "stderr.log" in text


def test_install_vm_emits_pass_fail_summary():
    text = SCRIPT.read_text()
    assert "passed" in text and "failed" in text
    assert "FAIL_LIST" in text or "FAIL_PHASES" in text


# ─── Opt-in real-VM run ────────────────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("BTERMINAL_VM_TESTS") != "1",
    reason="VM-bound test — set BTERMINAL_VM_TESTS=1 + ensure vm-test alias",
)
def test_install_vm_real_run_passes():
    """End-to-end real-VM smoke. Only runs when explicitly opted in
    via BTERMINAL_VM_TESTS=1 (because it needs `vm-test` SSH alias +
    a freshly snapshotted Linux VM + ~5 min runtime).

    Failures here mean the install path on a clean machine is broken;
    catches packaging-level regressions invisible to unit tests."""
    result = subprocess.run(
        ["bash", str(SCRIPT), "--modes", "a,b", "--skip-rollback"],
        capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, (
        f"VM smoke failed exit={result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-3000:]}\n"
        f"--- stderr ---\n{result.stderr[-1500:]}"
    )
