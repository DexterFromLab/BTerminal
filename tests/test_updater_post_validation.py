"""Updater post-update CLI validation pin (regression for
2026-05-08 stub-binary bug).

The bug: updater calls `install.sh --no-sudo`. Pre-fix, install.sh
in --no-sudo mode silently skipped Claude/Copilot when not yet
present, AND when present, used `[[ -x ]]` to detect "existing"
binaries — both of which left a stub claude.exe undetected.

The fix:
  1. install.sh now uses `find_claude_bin_loose` to also pick up
     stubs without +x bit, then runs validate_npm_cli on them.
  2. install.sh's structured install.log records [VALIDATE]
     entries that tools/test_update_vm.sh phase 6 grep-asserts.

These tests pin: (a) updater still calls `install.sh --no-sudo`,
(b) install.sh in `--no-sudo` runs the validator on existing
binaries (so updater inherits the stub-detection fix),
(c) tools/test_update_vm.sh has a phase 6 covering this.
"""
from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
UPDATER = REPO_ROOT / "bterminal" / "updater.py"
INSTALL_SH = REPO_ROOT / "install.sh"
UPDATE_VM_RUNNER = REPO_ROOT / "tools" / "test_update_vm.sh"


# ─── Updater calls install.sh --no-sudo ────────────────────────────


def test_updater_invokes_install_sh_with_no_sudo():
    """Pin: updater's _run() spawns `install.sh --no-sudo`. If
    this changes, the validation chain assumed by phase 6 below
    needs re-evaluation."""
    src = UPDATER.read_text()
    assert '"install.sh"' in src or "'install.sh'" in src
    assert "--no-sudo" in src


def test_updater_pipes_install_sh_output_to_dialog():
    """Pin: updater streams install.sh stdout into the GTK
    dialog so the user sees [VALIDATE] entries live, not just
    after the fact."""
    src = UPDATER.read_text()
    assert "subprocess.Popen" in src
    assert "stdout=subprocess.PIPE" in src
    assert "_append_line" in src


def test_updater_handles_install_sh_nonzero_exit():
    """Pin: updater treats install.sh exit != 0 as failure
    surface, with rollback marker recognition."""
    src = UPDATER.read_text()
    assert "proc.returncode" in src
    assert "BTERMINAL_ROLLBACK_OK" in src


# ─── install.sh --no-sudo path validates existing binaries ────────


def test_install_sh_no_sudo_still_runs_validate_for_existing():
    """Pin: even with --no-sudo, when an EXISTING_CLAUDE binary
    is found (loose or strict), validate_npm_cli runs. This is
    the path the updater hits."""
    src = INSTALL_SH.read_text()
    # Pre-validation block runs before --no-sudo gate
    no_sudo_idx = src.find('elif [[ "$NO_SUDO" == true ]]')
    validate_idx = src.find('validate_npm_cli "$EXISTING_CLAUDE"')
    assert validate_idx > 0
    assert validate_idx < no_sudo_idx, (
        "validate_npm_cli must run BEFORE the --no-sudo gate, "
        "otherwise the updater's --no-sudo invocation skips it."
    )


def test_install_sh_no_sudo_gate_only_blocks_FRESH_install():
    """Pin: the --no-sudo gate ONLY skips when NO existing binary
    exists. If existing is present (from previous install), the
    update path runs (npm install -g doesn't need sudo)."""
    src = INSTALL_SH.read_text()
    # The gate appears inside the elif of an if [[ -n "$EXISTING_CLAUDE" ]]
    # — meaning when EXISTING is present, the if-branch runs first.
    fragment = (
        'if [[ -n "$EXISTING_CLAUDE" ]]; then\n'
    )
    assert fragment in src
    # Ensure --no-sudo is the *elif*, not the *if*
    assert ('elif [[ "$NO_SUDO" == true ]]; then\n'
            '    warn "Claude Code not found') in src


# ─── tools/test_update_vm.sh phase 6 ───────────────────────────────


def test_update_vm_runner_has_phase_6():
    """Pin: tools/test_update_vm.sh has a phase 6 that runs
    install.sh --no-sudo against a pre-seeded existing binary
    and asserts [VALIDATE] in install.log."""
    text = UPDATE_VM_RUNNER.read_text()
    assert "Phase 6" in text or "phase 6" in text
    # Phase 6 must include the [VALIDATE] grep assertion
    assert "VALIDATE\\] Claude Code: OK" in text or \
        "[VALIDATE] Claude Code: OK" in text


def test_update_vm_runner_phase_6_pre_seeds_working_binary():
    """Pin: phase 6 pre-seeds a known-working claude binary so
    we can prove update doesn't degrade it."""
    text = UPDATE_VM_RUNNER.read_text()
    # The pre-seed step creates a +x mock claude with known version
    phase6_idx = text.find("phase6_setup")
    assert phase6_idx > 0
    phase6_block = text[phase6_idx:phase6_idx + 2000]
    assert "chmod +x" in phase6_block
    assert "9.9.9-pre-update" in phase6_block


def test_update_vm_runner_phase_6_asserts_binary_preserved():
    """Pin: phase 6 explicitly checks claude --version still
    works AFTER update. Without this, a regression that breaks
    the updater could pass phase 6 just because [VALIDATE]
    happened (but binary was downgraded)."""
    text = UPDATE_VM_RUNNER.read_text()
    phase6_idx = text.find("phase6_preserved")
    assert phase6_idx > 0
    phase6_block = text[phase6_idx:phase6_idx + 1000]
    assert "claude --version" in phase6_block


def test_update_vm_runner_phase_6_in_default_modes():
    """Pin: phase 6 runs by default (not opt-in). Must be in
    the default MODES list."""
    text = UPDATE_VM_RUNNER.read_text()
    assert 'MODES="1,2,3,4,5,6"' in text


def test_update_vm_runner_bash_syntax_valid():
    """Catch syntax errors before they hit a real run."""
    result = subprocess.run(
        ["bash", "-n", str(UPDATE_VM_RUNNER)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr
