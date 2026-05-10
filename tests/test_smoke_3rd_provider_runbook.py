"""Source-level checks for tools/smoke_3rd_provider.sh (#12 / #84).

Doesn't actually run the smoke (that needs a VM); validates the
runbook's structure so a CI-style invocation catches regressions
in the smoke script + manual README.
"""
from __future__ import annotations

import re
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SMOKE_SH = REPO_ROOT / "tools" / "smoke_3rd_provider.sh"
RUNBOOK = REPO_ROOT / "tests" / "manual" / "README.md"


def test_smoke_script_exists_and_executable():
    assert SMOKE_SH.is_file()
    # Must be executable on disk (chmod +x in #12 setup)
    mode = SMOKE_SH.stat().st_mode
    assert mode & 0o111, "tools/smoke_3rd_provider.sh must have +x bit"


def test_smoke_script_bash_syntax_valid():
    """`bash -n` parses without errors."""
    result = subprocess.run(
        ["bash", "-n", str(SMOKE_SH)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"bash -n failed:\n{result.stderr}"
    )


def test_smoke_script_help_returns_zero():
    """`./smoke_3rd_provider.sh --help` exits 0 + describes flags."""
    result = subprocess.run(
        ["bash", str(SMOKE_SH), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "--skip-llama" in result.stdout
    assert "--no-wipe" in result.stdout


@pytest.mark.parametrize("flag", [
    "--skip-llama", "--no-wipe", "--help", "-h",
])
def test_smoke_script_handles_known_flags(flag):
    text = SMOKE_SH.read_text()
    assert flag in text, f"smoke script must handle {flag} flag"


def test_smoke_script_unknown_flag_exits_nonzero():
    result = subprocess.run(
        ["bash", str(SMOKE_SH), "--bogus-flag"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0


@pytest.mark.parametrize("phase", [
    "Preflight", "Cleaning", "Syncing", "install.sh --headless",
    "Verifying install layout", "Ollama", "BTerminal + Aider session",
    "Image paste template",
])
def test_smoke_script_announces_each_phase(phase):
    """Each of the 8 phases (0..7) prints a recognizable banner so
    users can correlate output with the README phases table."""
    text = SMOKE_SH.read_text()
    assert phase in text, f"phase banner {phase!r} missing"


def test_smoke_script_uses_vm_sync_helper():
    """smoke_3rd_provider re-uses tools/vm_sync.sh — the canonical
    rsync wrapper. Avoids drift if VM_PATH layout changes."""
    text = SMOKE_SH.read_text()
    assert "vm_sync.sh" in text


def test_smoke_script_passes_status_json_flag_to_install():
    """install.sh invocation MUST include --status-json so phase 3
    can verify the terminal `{phase: done, progress: 100}` event."""
    text = SMOKE_SH.read_text()
    assert "--status-json" in text
    assert '"phase": "done"' in text
    assert '"progress": 100' in text


def test_smoke_script_polls_health_endpoint_with_401_acceptance():
    """BT's debug-REST returns 401 (auth wall) before token is read —
    the health probe MUST accept both 200 AND 401 as 'process up'."""
    text = SMOKE_SH.read_text()
    assert '"401"' in text or "401" in text


def test_smoke_script_cleans_up_subprocess_on_exit():
    """Phase 6 spawns BT in background — the script must kill it
    afterwards so the VM doesn't accumulate orphan processes across
    runs."""
    text = SMOKE_SH.read_text()
    assert "kill" in text and "/tmp/bt-smoke.pid" in text


def test_smoke_script_emits_final_pass_fail_summary():
    """Final block must enumerate failed phases by name + exit non-zero
    when any failed."""
    text = SMOKE_SH.read_text()
    assert "Failed phases:" in text
    assert "PASS_COUNT" in text
    assert "FAIL_COUNT" in text


# ─── Runbook documentation ─────────────────────────────────────────────────


def test_runbook_exists():
    assert RUNBOOK.is_file()


def test_runbook_lists_smoke_3rd_provider():
    """Inventory table must include the smoke script."""
    text = RUNBOOK.read_text()
    assert "smoke_3rd_provider.sh" in text
    assert "5–10 min" in text or "5-10 min" in text


def test_runbook_documents_slow_marker_modes():
    """Per task #88 — runbook is the canonical answer to 'where did
    my slow tests go?'. Three modes documented: default, -m slow,
    -m 'not slow', e2e/."""
    text = RUNBOOK.read_text()
    assert "pytest -m slow" in text
    assert "pytest -m \"not slow\"" in text or "pytest -m 'not slow'" in text
    assert "tests/e2e/" in text


def test_runbook_phases_table_matches_script_phases():
    """The README's phase table (0..7) must reference the same
    bash phase numbers as the script's `[N/7]` echos."""
    text = RUNBOOK.read_text()
    for n in range(0, 8):
        assert f"| {n} |" in text, f"runbook phases table missing row {n}"


def test_runbook_documents_when_to_run():
    """README needs a 'when to run' section so future contributors
    know whether they need to fire the smoke for a given change."""
    text = RUNBOOK.read_text()
    assert "## When to run" in text or "## When to run" in text.lower() \
        or "when to run" in text.lower()


def test_runbook_documents_common_failure_modes():
    """Each phase has a known failure mode + fix — listed so users
    don't have to spelunk through git log."""
    text = RUNBOOK.read_text()
    assert "Common failure modes" in text
    # At least 4 failure scenarios (one per high-fragility phase)
    bullet_count = text.count("\n- **Phase ")
    assert bullet_count >= 4, (
        f"expected ≥4 failure-mode bullets, found {bullet_count}"
    )
