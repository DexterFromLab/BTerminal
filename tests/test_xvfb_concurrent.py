"""Pytest wrapper for tools/test_xvfb_concurrent.sh
(#62 / #134, audit § 6.8 #35).

Source-level checks pin the contract between the bash runner
and the underlying xvfb-run usage. The opt-in real run
(BTERMINAL_VM_TESTS=1) actually spawns N xvfb processes —
~3-5s per run, gated.

Realne odkrycie pinned by branch (d): `xvfb-run -a` is NOT
concurrent-safe. Multiple parallel calls all see the same
"free" server number candidate before any has acquired its
lock. Workaround: explicit `--server-num=$N` per worker.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_xvfb_concurrent.sh"


# ─── Bash runner shape ─────────────────────────────────────────────────────


def test_runner_script_exists_and_executable():
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111


def test_runner_script_bash_syntax_valid():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_runner_script_help_returns_zero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    for flag in ("--modes", "--remote"):
        assert flag in result.stdout, f"help missing {flag}"


def test_runner_script_unknown_flag_exits_nonzero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--bogus"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0


# ─── Decision branch coverage ──────────────────────────────────────────────


def test_runner_covers_2_parallel_branch():
    """Sub-mode (a): canonical 2-parallel test."""
    text = SCRIPT.read_text()
    assert "[a] 2 parallel xvfb-run sessions" in text
    # Spawn 2 workers
    assert "spawn_n_xvfb 2" in text


def test_runner_covers_5_parallel_branch():
    """Sub-mode (b): 5-parallel stress simulating CI shards."""
    text = SCRIPT.read_text()
    assert "[b] 5 parallel xvfb-run sessions" in text
    assert "spawn_n_xvfb 5" in text
    # Plus checks zero duplicate DISPLAYs
    assert "zero duplicate" in text or "duplicate DISPLAY" in text


def test_runner_covers_preset_display_branch():
    """Sub-mode (c): pre-set DISPLAY=:99 override test."""
    text = SCRIPT.read_text()
    assert "[c] DISPLAY=:99 pre-set" in text
    # Pre-set value MUST NOT be honored
    assert ":99" in text


def test_runner_covers_negative_pin_for_xvfb_a_race():
    """Sub-mode (d): pin that `xvfb-run -a` IS race-prone.
    Important regression marker — if this passes (5 unique
    displays under `-a`), xvfb-run upstream has been fixed
    and the runner can switch back from `--server-num` to `-a`."""
    text = SCRIPT.read_text()
    assert "[d] Pinning xvfb-run -a race" in text
    # The negative assertion: < 5 unique
    assert 'lt "5"' in text or "< 5" in text


# ─── Workaround uses explicit --server-num ─────────────────────────────────


def test_workaround_uses_explicit_server_num():
    """Pin: the canonical helper uses `--server-num=$server_num`
    (PID-based offset) instead of `-a`. Without explicit numbers,
    parallel spawns hit the race documented in branch (d)."""
    text = SCRIPT.read_text()
    assert "--server-num=" in text


def test_workaround_uses_pid_based_offset():
    """Pin: server_num = `200 + ($$ % 50) + i`. PID offset
    ensures concurrent test runs (e.g. CI pipelines on a
    shared VM) don't collide."""
    text = SCRIPT.read_text()
    assert "$$" in text  # PID-based offset somewhere
    # And the formula uses arithmetic on PID
    assert "$$ % 50" in text or "$$ %50" in text


def test_workaround_distinct_from_a_flag_mode():
    """Pin: helper supports BOTH modes (`auto` for the negative
    pin, `explicit` for the workaround). `mode="explicit"` is
    the default."""
    text = SCRIPT.read_text()
    assert 'mode="${4:-explicit}"' in text or \
        "mode='${4:-explicit}'" in text
    # Auto mode supported for negative pin
    assert '"$mode" == "auto"' in text or \
        "'\"$mode\" == \"auto\"'" in text


# ─── Verification helpers ──────────────────────────────────────────────────


def test_runner_counts_unique_displays_via_sort():
    """Pin: `count_unique_displays` uses `sort -u` + `grep -c
    '^:'` to count distinct DISPLAY values. Without the regex,
    a blank line would inflate the count."""
    text = SCRIPT.read_text()
    assert "sort -u" in text
    # The grep filter for `:N` lines
    assert "grep -c '^:'" in text or \
        'grep -c "^:"' in text


def test_runner_emits_pass_fail_summary():
    text = SCRIPT.read_text()
    assert "Total:" in text
    assert "passed" in text and "failed" in text
    assert "FAIL_LIST" in text


# ─── Remote VM mode supported ──────────────────────────────────────────────


def test_runner_supports_remote_flag():
    """Pin: `--remote vm-test` runs the same suite via SSH on
    a remote box. Critical for CI: tests can run on VM without
    needing local xvfb."""
    text = SCRIPT.read_text()
    assert "--remote)" in text or "--remote " in text
    # Use ssh under remote mode
    assert "ssh" in text


def test_run_cmd_helper_dispatches_local_or_remote():
    """Pin: `run_cmd` chooses between `bash -c` (local) and
    `ssh ... bash -c` (remote) based on `$REMOTE`. Without
    this dispatch, the test would always run locally."""
    text = SCRIPT.read_text()
    fn_idx = text.find("run_cmd() {")
    assert fn_idx > 0
    body_end = text.find("\n}\n", fn_idx)
    body = text[fn_idx:body_end + 2]
    assert "$REMOTE" in body
    assert "bash -c" in body
    assert "ssh" in body


# ─── Local smoke against actual xvfb (opt-in) ──────────────────────────────


@pytest.mark.skipif(
    not shutil.which("xvfb-run"),
    reason="xvfb-run not installed locally",
)
def test_local_smoke_2_parallel_yields_unique_displays(tmp_path):
    """Run the 2-parallel branch locally — verify the
    --server-num workaround actually picks distinct DISPLAYs.
    Skipped if xvfb-run not installed."""
    result = subprocess.run(
        ["bash", str(SCRIPT), "--modes", "a"],
        capture_output=True, text=True, timeout=30,
    )
    assert result.returncode == 0, (
        f"local smoke failed: {result.stdout}\n{result.stderr}"
    )
    # Specifically assert the OK marker for branch (a)
    assert "(a) 2 parallel" in result.stdout
    assert "OK" in result.stdout


# ─── Opt-in real-VM run ────────────────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("BTERMINAL_VM_TESTS") != "1",
    reason="VM-bound test — set BTERMINAL_VM_TESTS=1 + ensure vm-test alias",
)
def test_xvfb_concurrent_real_run_passes():
    """End-to-end real-VM xvfb concurrent smoke. Runs all 4
    branches (a/b/c/d) on the VM. Skipped by default — gated
    by BTERMINAL_VM_TESTS=1."""
    result = subprocess.run(
        ["bash", str(SCRIPT), "--remote", "vm-test"],
        capture_output=True, text=True, timeout=120,
    )
    assert result.returncode == 0, (
        f"VM xvfb smoke failed exit={result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-2000:]}\n"
        f"--- stderr ---\n{result.stderr[-1000:]}"
    )
