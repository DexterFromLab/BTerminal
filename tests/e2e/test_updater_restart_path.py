"""E2E test for BUG#15 — _restart_bterminal crashes the new process
with ModuleNotFoundError because os.execv was called with sys.argv
that has __main__.py path as argv[0].

Empirically verified 2026-05-10: running
    python3 /home/bartek/.local/share/bterminal/bterminal/__main__.py
from any cwd produces:
    ModuleNotFoundError: No module named 'bterminal'
on line 15 (`from bterminal import debug_rest`). Python puts the
script's directory on sys.path[0], but that directory IS the
bterminal/ package — `from bterminal import …` needs the PARENT
on the path.

Two correct restart paths after fix:
  1. ~/.local/bin/bterminal launcher (preferred): its shell wrapper
     `cd`s into install dir + runs `python3 -m bterminal`, so the
     package is found via cwd-on-path.
  2. Direct `python3 -m bterminal` (fallback when launcher missing):
     Python's -m flag adds CWD to sys.path before resolving the
     module, so `bterminal` is found in the install dir.

The test pins:
  - _restart_bterminal does NOT call os.execv with bare
    __main__.py invocation
  - launcher path preferred when present
  - falls back to `python -m bterminal` when launcher missing
  - sys.argv[0] is stripped (only user flags forwarded)
  - empirically: post-fix invocation does not crash
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
UPDATER_PY = REPO_ROOT / "bterminal" / "updater.py"


# ── Source-level guards ───────────────────────────────────────────────────


def _restart_body() -> str:
    src = UPDATER_PY.read_text(encoding="utf-8")
    start = src.find("def _restart_bterminal")
    assert start >= 0, "_restart_bterminal not found"
    end = src.find("\ndef ", start + 1)
    return src[start:end] if end > start else src[start:]


def test_restart_does_not_pass_bare_sys_argv_to_python():
    """Pin: the bug shape was `os.execv(sys.executable, [sys.executable]
    + sys.argv)`. After the fix this exact pattern must be gone."""
    body = _restart_body()
    # The bug-shape regex
    bad = re.search(
        r"os\.execv\(\s*sys\.executable\s*,\s*"
        r"\[\s*sys\.executable\s*\]\s*\+\s*sys\.argv\s*\)",
        body,
    )
    assert not bad, (
        "Bug-shape `os.execv(sys.executable, [sys.executable] + "
        "sys.argv)` still present — would crash the restart with "
        "ModuleNotFoundError because sys.argv[0] is __main__.py path."
    )


def test_restart_prefers_launcher_path():
    """Pin: the fix should look up ~/.local/bin/bterminal first and
    execv that as argv[0] of the new process. The launcher's shell
    wrapper handles cwd + `python -m bterminal` correctly."""
    body = _restart_body()
    assert "bterminal" in body and "bin" in body and ".local" in body, (
        "expected reference to ~/.local/bin/bterminal launcher path "
        f"in _restart_bterminal body:\n{body}"
    )
    # Either explicit string literal or os.path.expanduser
    has_launcher_ref = (
        "/.local/bin/bterminal" in body
        or "expanduser" in body and "~/.local/bin/bterminal" in body
    )
    assert has_launcher_ref, "no launcher path lookup in body"


def test_restart_has_python_m_bterminal_fallback():
    """Pin: when launcher is missing (e.g. broken install), fall back
    to `python3 -m bterminal` — this preserves the package import
    behaviour even without the shell wrapper."""
    body = _restart_body()
    assert '"-m"' in body and '"bterminal"' in body, (
        "expected `python -m bterminal` fallback args. Body:\n"
        f"{body[:400]}"
    )


def test_restart_strips_argv0():
    """Pin: sys.argv[0] (the script path) must be dropped; only
    user flags (sys.argv[1:]) are forwarded to the new process."""
    body = _restart_body()
    assert "sys.argv[1:]" in body, (
        "expected `sys.argv[1:]` (user flags only). Forwarding the "
        "whole sys.argv would re-introduce the original bug if "
        "launcher path also includes the bad argv[0]."
    )


# ── Behavioural: direct invocation of __main__.py reproduces the bug ─────


def test_direct_main_py_invocation_crashes_with_module_not_found():
    """Pin (negative): document the bug class. Running __main__.py
    as a script directly (the pre-fix exec shape) MUST fail with
    ModuleNotFoundError. If this test ever stops failing, Python's
    behaviour changed or someone added a sys.path bootstrap to
    __main__.py — both worth investigating."""
    install_dir = Path.home() / ".local" / "share" / "bterminal"
    main_py = install_dir / "bterminal" / "__main__.py"
    if not main_py.is_file():
        pytest.skip(f"no installed BT at {install_dir}")
    # Run from /tmp so cwd doesn't accidentally fix the import
    result = subprocess.run(
        [sys.executable, str(main_py)],
        cwd="/tmp", capture_output=True, text=True, timeout=5,
    )
    assert result.returncode != 0, (
        f"direct __main__.py invocation succeeded — bug premise wrong. "
        f"stdout: {result.stdout[:200]}"
    )
    assert "ModuleNotFoundError" in result.stderr, (
        f"expected ModuleNotFoundError, got:\n{result.stderr[:300]}"
    )
    assert "bterminal" in result.stderr, (
        f"error doesn't mention bterminal — different crash? "
        f"stderr:\n{result.stderr[:300]}"
    )


def test_python_m_bterminal_invocation_succeeds():
    """Pin (positive): the fallback `python -m bterminal` correctly
    imports the package and starts the app. We don't run the full
    GTK loop — just invoke with --help, which exits cleanly after
    argparse."""
    install_dir = Path.home() / ".local" / "share" / "bterminal"
    if not (install_dir / "bterminal" / "__init__.py").is_file():
        pytest.skip(f"no installed BT package at {install_dir}")
    result = subprocess.run(
        [sys.executable, "-m", "bterminal", "--help"],
        cwd=str(install_dir),
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"`python -m bterminal --help` failed:\n"
        f"stdout: {result.stdout[:200]}\nstderr: {result.stderr[:300]}"
    )


def test_launcher_invocation_succeeds():
    """Pin (positive): the launcher path correctly bootstraps too.
    This is what the fix uses preferentially."""
    launcher = Path.home() / ".local" / "bin" / "bterminal"
    if not (launcher.is_file() or launcher.is_symlink()):
        pytest.skip(f"no launcher at {launcher}")
    result = subprocess.run(
        [str(launcher), "--help"],
        cwd="/tmp",  # different cwd to prove it doesn't matter
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"launcher --help failed:\n"
        f"stdout: {result.stdout[:200]}\nstderr: {result.stderr[:300]}"
    )
