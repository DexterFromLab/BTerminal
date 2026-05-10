"""Pytest wrapper for tools/test_install_summary.sh (task #62).

Runs the bash structural checks against install.sh as part of the
regular suite so pytest catches drift between bterminal.diagnostics
and install.sh's check_tool calls.
"""
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_install_summary.sh"


def test_install_sh_summary_block_structure():
    """install.sh emits a [SUMMARY] block + lists every dep from
    bterminal.diagnostics.DEPENDENCIES via check_tool."""
    if not SCRIPT.exists():
        pytest.skip(f"missing {SCRIPT}")
    if not SCRIPT.is_file():
        pytest.skip(f"{SCRIPT} not a file")
    SCRIPT.chmod(0o755)
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True, text=True, timeout=30,
    )
    if result.returncode != 0:
        pytest.fail(
            f"install.sh structural checks failed:\n"
            f"--- stdout ---\n{result.stdout}\n"
            f"--- stderr ---\n{result.stderr}"
        )
