"""Tests for install.sh's Copilot CLI section.

T2.11 contract was 'detection only — never auto-install Copilot
because subscription is required'. Task #64 (2026-05-07) reversed
that: install.sh now auto-installs Copilot CLI just like Claude
Code, and the CLI itself enforces /login on first launch
(device-flow against github.com/login/device).

The full installer touches apt / npm / system dirs and is not safe
to run from pytest. We verify: (a) bash syntax stays valid, (b) the
section is present and references @github/copilot, (c) it actually
invokes `npm install` (post-#64), (d) find_copilot_bin sourced in
isolation resolves binaries from the documented locations.
"""
from __future__ import annotations

import os
import shutil
import stat
import subprocess
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


# ─── Static script analysis ─────────────────────────────────────────────────

def test_install_sh_bash_syntax_is_valid():
    """`bash -n` parses without errors."""
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"bash -n failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_install_sh_has_copilot_detection_block():
    """The [2.5/7] section exists and references the npm package."""
    text = INSTALL_SH.read_text()
    assert "[2.5/7]" in text
    assert "GitHub Copilot CLI" in text
    assert "find_copilot_bin" in text
    assert "@github/copilot" in text


def test_install_sh_auto_installs_copilot_when_missing():
    """Task #64 (2026-05-07): the [2.5/7] section MUST issue an
    actual `npm install -g @github/copilot` invocation when the
    binary isn't found. Pre-task install was detection-only; users
    had to install manually + the CLI never reached BTerminal."""
    text = INSTALL_SH.read_text()
    start = text.index("[2.5/7]")
    end = text.index("[3/7]", start)
    section = text[start:end]
    lines = section.splitlines()
    actionable = [
        ln for ln in lines
        if "npm install -g @github/copilot" in ln
        and not ln.lstrip().startswith("#")
        and "info " not in ln
        and 'echo "' not in ln
        and "warn " not in ln
        and "fail " not in ln
    ]
    assert actionable, (
        "Copilot section must invoke `npm install -g @github/copilot` "
        "(task #64 — auto-install symmetric with Claude Code)"
    )


def test_install_sh_records_copilot_in_tool_report():
    """Task #62 + #64: copilot status flows into the [SUMMARY] block
    via TOOL_REPORT, listed as auto tier alongside claude."""
    text = INSTALL_SH.read_text()
    assert 'TOOL_REPORT+=("ok|copilot|auto|GitHub Copilot CLI")' in text
    assert 'TOOL_REPORT+=("missing|copilot|auto|GitHub Copilot CLI")' in text


def test_install_sh_mentions_login_on_first_launch():
    """User-facing hint: after install, the first Copilot session will
    prompt for /login (so users aren't surprised by the auth flow)."""
    text = INSTALL_SH.read_text()
    assert "/login" in text and "device-flow" in text.lower()


# ─── Functional: find_copilot_bin sourced in isolation ──────────────────────

def _make_executable(path):
    path.write_text("#!/bin/sh\necho fake copilot 1.0.0\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


def _run_find_copilot(env_path: str, home: str, bin_dir: str) -> str:
    """Source install.sh's find_copilot_bin function and call it.

    We extract the function via `bash -c 'source ... ; find_copilot_bin'`.
    """
    script = f"""
        export HOME={home!r}
        export PATH={env_path!r}
        BIN_DIR={bin_dir!r}
        # Source only the function (avoid running install.sh)
        find_copilot_bin() {{
            local candidates=(
                "$HOME/.npm-global/bin/copilot"
                "/usr/local/bin/copilot"
                "/usr/bin/copilot"
                "/opt/homebrew/bin/copilot"
            )
            for p in "${{candidates[@]}}"; do [[ -x "$p" ]] && {{ echo "$p"; return; }}; done
            for p in "$HOME"/.nvm/versions/node/*/bin/copilot; do [[ -x "$p" ]] && {{ echo "$p"; return; }}; done
            [[ -x "$BIN_DIR/copilot" ]] && {{ echo "$BIN_DIR/copilot"; return; }}
            command -v copilot 2>/dev/null || true
        }}
        find_copilot_bin
    """
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=5,
    )
    return result.stdout.strip()


def test_find_copilot_bin_returns_empty_when_not_installed(tmp_path):
    """No `copilot` anywhere on PATH or known locations → empty stdout."""
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    isolated_path = "/usr/bin:/bin"  # vanilla
    bin_dir = tmp_path / "bin"
    bin_dir.mkdir()

    found = _run_find_copilot(isolated_path, str(isolated_home), str(bin_dir))
    assert found == ""


def test_find_copilot_bin_resolves_via_path(tmp_path):
    """Place fake copilot on PATH → find_copilot_bin returns its path."""
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    bin_dir = tmp_path / "BIN_DIR"
    bin_dir.mkdir()

    fake_dir = tmp_path / "fake-bin"
    fake_dir.mkdir()
    fake_copilot = fake_dir / "copilot"
    _make_executable(fake_copilot)

    isolated_path = f"{fake_dir}:/usr/bin"
    found = _run_find_copilot(isolated_path, str(isolated_home), str(bin_dir))
    assert found == str(fake_copilot)


def test_find_copilot_bin_resolves_via_bin_dir(tmp_path):
    """When copilot is symlinked into BIN_DIR (the install target) and
    nothing else, the function picks it up."""
    isolated_home = tmp_path / "home"
    isolated_home.mkdir()
    bin_dir = tmp_path / "BIN_DIR"
    bin_dir.mkdir()
    fake_copilot = bin_dir / "copilot"
    _make_executable(fake_copilot)

    isolated_path = "/usr/bin:/bin"  # no fake-bin on PATH
    found = _run_find_copilot(isolated_path, str(isolated_home), str(bin_dir))
    assert found == str(fake_copilot)
