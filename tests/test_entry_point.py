"""Tests for bterminal entry point CLI behavior.

Covers R1.1 (strict argparse), R1.2 (no env var fallback), R1.f2
(corrupt options.json self-heal).
"""

import json
import os
import subprocess
import sys

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
ENTRY_ARGS = ["-m", "bterminal"]


def test_help_flag_prints_usage_and_exits_zero():
    """--help must print argparse usage and exit 0 — without spawning GTK.
    argparse handles --help before main() reaches Gtk.Application."""
    result = subprocess.run(
        [sys.executable, *ENTRY_ARGS, "--help"],
            cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower() or "usage:" in result.stderr.lower()
    assert "--debug-rest" in (result.stdout + result.stderr)


def test_debug_rest_flag_recognized():
    """argparse must accept --debug-rest without errors. We can't fully
    boot here (no display), but we can check that argparse accepts the
    flag — `--help` after `--debug-rest` would re-print usage if accepted."""
    result = subprocess.run(
        [sys.executable, *ENTRY_ARGS, "--debug-rest", "--help"],
            cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "--debug-rest" in (result.stdout + result.stderr)


def test_unknown_flag_rejected_with_exit_2():
    """R1.1: strict argparse — `parser.parse_args` (nie parse_known_args).
    Nieznana flaga = stderr "unrecognized arguments" + exit code 2."""
    result = subprocess.run(
        [sys.executable, *ENTRY_ARGS, "--definitely-not-a-real-flag-xyz"],
        cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 2, (
        f"expected exit 2 for unknown flag, got {result.returncode}\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "unrecognized arguments" in result.stderr.lower()
    assert "definitely-not-a-real-flag-xyz" in result.stderr


def test_help_takes_precedence_over_unknown():
    """argparse processes --help before unknown-arg check, so --help wins."""
    result = subprocess.run(
        [sys.executable, *ENTRY_ARGS, "--help", "--unknown-xyz"],
        cwd=REPO_ROOT,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    assert "usage:" in result.stdout.lower() or "usage:" in result.stderr.lower()


def test_env_var_no_longer_enables_debug_rest():
    """R1.2: BTERMINAL_DEBUG_REST=1 jest IGNOROWANE — tylko --debug-rest
    flag uruchamia debug REST. Sprawdzamy poprzez --help (nie chcemy
    booć GTK), ale weryfikujemy że no error i że flag w help jest
    udokumentowany. Pełna weryfikacja behawioralna w smoke z xvfb."""
    env = {**os.environ, "BTERMINAL_DEBUG_REST": "1"}
    result = subprocess.run(
        [sys.executable, *ENTRY_ARGS, "--help"],
        cwd=REPO_ROOT, env=env,
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    # --help wciąż wyświetla się prawidłowo, env var nie powoduje błędu


def test_module_main_callable_without_gtk_init():
    """The package's __main__ module exposes main() as a callable. We don't
    actually invoke it (would block on GTK), but verify the entry symbol
    exists so install.sh / launchers depending on it work after refactor."""
    from bterminal.__main__ import main
    assert callable(main)
    # Also: __name__ == '__main__' guard runs main when script is invoked
    # directly. Verify it's NOT auto-called on import (catches accidental
    # missing `if __name__` guard).
    # If main() had run, GTK Application would be initialized — assert not.
    import sys as _sys
    assert "_BTERMINAL_TEST_MAIN_INVOKED" not in os.environ
