"""Pytest pinning for tools/test_install_full_chain_vm.sh.

The shell script itself runs against a real VM (gated by ssh
vm-test). Here we pin its structure + decision branches so a
refactor can't silently drop coverage.
"""
from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_install_full_chain_vm.sh"


# ─── Script shape ───────────────────────────────────────────────────


def test_runner_exists_and_is_executable():
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111


def test_runner_bash_syntax_valid():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_runner_help_returns_zero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "stub-injection" in result.stdout.lower() or \
        "stub" in result.stdout.lower()


def test_runner_unknown_flag_exits_nonzero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--bogus"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0


# ─── Decision-branch coverage pins ─────────────────────────────────


def test_runner_covers_stub_injection_branch():
    """Pin (a): runner has a stub-injection mode that pre-seeds
    a broken claude.exe and asserts validate_npm_cli detects it."""
    text = SCRIPT.read_text()
    assert "(a) Stub detection" in text
    # Specifically reproduces the bug: stub WITHOUT +x bit
    assert "chmod 0644" in text
    # Asserts on detection: keyword "broken" + "will reinstall"
    assert "broken" in text
    assert "will reinstall" in text


def test_runner_covers_mock_npm_branch():
    """Pin (b): runner has a mock-npm mode that injects a fake
    npm wrapper materializing working binaries — bypasses
    network so CI can run without npm registry access."""
    text = SCRIPT.read_text()
    assert "(b) Mock-npm" in text
    assert "@anthropic-ai/claude-code" in text
    assert "@github/copilot" in text


def test_runner_covers_structured_log_branch():
    """Pin (c): runner asserts install.log timestamp + level
    structure (OK/WARN/INFO/VALIDATE)."""
    text = SCRIPT.read_text()
    assert "(c) Structured" in text
    # ISO-8601 timestamp regex
    assert "[0-9]{4}-[0-9]{2}-[0-9]{2}T" in text
    # Specifically asserts VALIDATE entries for each AI provider
    assert "VALIDATE\\] Claude Code" in text or \
           "[VALIDATE] Claude Code" in text


def test_runner_covers_errors_json_branch():
    """Pin (d): runner validates install_errors.json schema."""
    text = SCRIPT.read_text()
    assert "(d) install_errors.json" in text
    assert "errors" in text and "warnings" in text
    assert "bterminal_version" in text


def test_runner_covers_bt_spawn_branch():
    """Pin (e): runner spawns BT under xvfb-run and asserts
    no-crash exit code (e1) plus all 5 CLI tool symlinks
    installed (e2). REST health is intentionally pinned via
    e2e/test_smoke_battery.py instead — background xvfb-run
    + curl polling is race-prone here."""
    text = SCRIPT.read_text()
    assert "(e) Post-install BT spawn" in text
    assert "xvfb-run" in text
    assert "BT_SPAWN_OK" in text
    assert "(e1) BT spawned" in text
    assert "(e2) all 5 CLI tool symlinks" in text


def test_runner_supports_skip_bt_spawn_flag():
    """Pin: --skip-bt-spawn for environments without xvfb."""
    text = SCRIPT.read_text()
    assert "--skip-bt-spawn" in text
    assert "SKIP_BT_SPAWN" in text


def test_runner_supports_modes_subset():
    """Pin: --modes flag for running subset of decision branches."""
    text = SCRIPT.read_text()
    assert "--modes" in text
    assert "MODES" in text


def test_runner_writes_per_step_logs():
    """Pin: each VM command writes to a named log file in
    smoke-logs/install-fullchain/ — required for CI debugging."""
    text = SCRIPT.read_text()
    assert "LOG_DIR=" in text
    assert "smoke-logs/install-fullchain" in text
    assert ".stdout.log" in text
    assert ".stderr.log" in text


def test_runner_emits_pass_fail_summary():
    """Pin: Summary block with pass/fail counts + failed-step
    list. Without this, `set -uo pipefail` makes silent failures
    indistinguishable from successes in CI logs."""
    text = SCRIPT.read_text()
    assert "Total:" in text
    assert "passed:" in text
    assert "failed:" in text
    assert "FAIL_LIST" in text


# ─── Opt-in real-VM run ────────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("BTERMINAL_VM_TESTS") != "1",
    reason="Real VM smoke — set BTERMINAL_VM_TESTS=1 + ensure vm-test alias",
)
def test_install_full_chain_real_run():
    """End-to-end: actually execute the runner against the VM.
    Skipped by default. Runs in ~60s with all 5 branches."""
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True, text=True, timeout=240,
    )
    assert result.returncode == 0, (
        f"full-chain VM smoke failed exit={result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-3000:]}\n"
        f"--- stderr ---\n{result.stderr[-1500:]}"
    )
