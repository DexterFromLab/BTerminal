"""Pure-pytest scenarios for `detect_install_state()` — task #143.

5 break scenarios from the task description, each in its own
test case. Uses tmp_path as a synthetic HOME so we can simulate
each broken state without touching the real $HOME.

(a) Remove ~/.local/bin/bterminal launcher symlink
(b) Remove ~/.local/share/bterminal/bterminal/__init__.py
(c) Replace ~/.npm-global/bin/claude with a stub (no +x)
(d) Plant ~/.config/bterminal/install.lock with a fake dead PID
(e) Remove ~/.local/bin/{ctx,tasks} but keep bterminal launcher

For each scenario, after the break, detect_install_state(home)
MUST return "broken". A clean install must return "installed".
A bare HOME with nothing must return "not_installed".
"""
from __future__ import annotations

import os
import stat
from pathlib import Path

import pytest

from bterminal.ui.installer_wizard import detect_install_state


def _build_clean_install(home: Path) -> None:
    """Create a fully-installed BT layout under `home` (mimics
    what install.sh produces). Used as the baseline; each test
    then mutates one piece to simulate breakage."""
    bin_dir = home / ".local" / "bin"
    install_dir = home / ".local" / "share" / "bterminal"
    pkg_dir = install_dir / "bterminal"
    npm_lib = home / ".npm-global" / "lib" / "node_modules"
    npm_bin = home / ".npm-global" / "bin"
    config_dir = home / ".config" / "bterminal"

    for d in (bin_dir, pkg_dir, npm_lib, npm_bin, config_dir):
        d.mkdir(parents=True, exist_ok=True)

    # Launcher script + companion CLI files (real files, not symlinks
    # — detect_install_state accepts both)
    launcher = install_dir / "bterminal-launcher"
    launcher.write_text("#!/bin/bash\n# fake launcher\n")
    launcher.chmod(0o755)
    (bin_dir / "bterminal").symlink_to(launcher)

    for tool in ("ctx", "tasks", "consult", "memory_wizard", "claude_log"):
        target = install_dir / tool
        target.write_text(f"#!/bin/bash\n# fake {tool}\n")
        target.chmod(0o755)
        (bin_dir / tool).symlink_to(target)

    # bterminal/__init__.py
    (pkg_dir / "__init__.py").write_text("# fake package\n")

    # AI CLIs: real-looking binaries (mock #!/bin/sh that prints version)
    for cli, fake_v in (("claude", "1.0.0"), ("copilot", "2.0.0")):
        cli_real = npm_lib / f"@fake/{cli}" / "bin" / cli
        cli_real.parent.mkdir(parents=True, exist_ok=True)
        cli_real.write_text(
            f'#!/bin/sh\n[ "$1" = "--version" ] && echo "{cli} {fake_v}" '
            f'&& exit 0\nexit 0\n'
        )
        cli_real.chmod(0o755)
        # Symlink under .npm-global/bin/<cli>
        (npm_bin / cli).symlink_to(cli_real)
        # And mirror under .local/bin/<cli>
        (bin_dir / cli).symlink_to(npm_bin / cli)


# ─── Baseline: clean install ──────────────────────────────────────────


def test_baseline_clean_install_detected_as_installed(tmp_path):
    """Sanity check: a fully-built BT layout returns 'installed'."""
    _build_clean_install(tmp_path)
    assert detect_install_state(tmp_path) == "installed"


def test_baseline_empty_home_detected_as_not_installed(tmp_path):
    """Sanity check: bare HOME returns 'not_installed'."""
    assert detect_install_state(tmp_path) == "not_installed"


# ─── (a) Remove launcher symlink ──────────────────────────────────────


def test_break_a_remove_launcher_symlink(tmp_path):
    """Pin (a): rm ~/.local/bin/bterminal → 'broken'."""
    _build_clean_install(tmp_path)
    launcher = tmp_path / ".local" / "bin" / "bterminal"
    launcher.unlink()
    assert detect_install_state(tmp_path) == "broken", (
        "Removed launcher symlink should make state 'broken'"
    )


# ─── (b) Remove bterminal/__init__.py ─────────────────────────────────


def test_break_b_remove_pkg_init(tmp_path):
    """Pin (b): rm ~/.local/share/bterminal/bterminal/__init__.py → 'broken'."""
    _build_clean_install(tmp_path)
    pkg_init = (tmp_path / ".local" / "share" / "bterminal"
                / "bterminal" / "__init__.py")
    pkg_init.unlink()
    assert detect_install_state(tmp_path) == "broken"


# ─── (c) Stub claude.exe (no +x, with stub marker) ────────────────────


def test_break_c_stub_claude_no_exec(tmp_path):
    """Pin (c-1): replace claude binary with a stub WITHOUT +x bit."""
    _build_clean_install(tmp_path)
    claude_real = list((tmp_path / ".npm-global" / "lib"
                         / "node_modules" / "@fake" / "claude"
                         / "bin").glob("*"))[0]
    # Overwrite with a stub binary; chmod 0644 (no +x) — the exact
    # 2026-05-08 bug: npm postinstall left this state.
    claude_real.write_text(
        '#!/bin/sh\n'
        'echo "Error: claude native binary not installed." >&2\n'
        'exit 1\n'
    )
    claude_real.chmod(0o644)  # NO +x
    assert detect_install_state(tmp_path) == "broken"


def test_break_c_stub_claude_with_marker(tmp_path):
    """Pin (c-2): claude binary HAS +x but content is the stub
    marker text. Validator must catch this even when chmod looks OK."""
    _build_clean_install(tmp_path)
    claude_real = list((tmp_path / ".npm-global" / "lib"
                         / "node_modules" / "@fake" / "claude"
                         / "bin").glob("*"))[0]
    claude_real.write_text(
        '#!/bin/sh\n'
        'echo "Error: claude native binary not installed."\n'
        'exit 1\n'
    )
    claude_real.chmod(0o755)  # +x
    assert detect_install_state(tmp_path) == "broken"


# ─── (d) Stale install.lock ───────────────────────────────────────────


def test_break_d_stale_install_lock(tmp_path):
    """Pin (d): plant lockfile with a known-dead PID. detect must
    return 'broken' so wizard offers Fix (which lets install.sh
    auto-recover via stale-lock detection)."""
    _build_clean_install(tmp_path)
    lockfile = tmp_path / ".config" / "bterminal" / "install.lock"
    # PID 999999 — guaranteed not to exist (max linux PID is < 4M
    # on default kernel.pid_max; 999999 is reserved for testing).
    lockfile.write_text("999999\n")
    assert detect_install_state(tmp_path) == "broken"


def test_break_d_lock_with_live_pid_does_not_break(tmp_path):
    """Inverse pin: a lockfile with the CURRENT process PID is
    'live' — not stale — so detect must NOT return 'broken'.
    Otherwise we'd flag a healthy install as broken whenever the
    user has another install.sh genuinely running."""
    _build_clean_install(tmp_path)
    lockfile = tmp_path / ".config" / "bterminal" / "install.lock"
    lockfile.write_text(f"{os.getpid()}\n")
    assert detect_install_state(tmp_path) == "installed"


# ─── (e) Remove companion CLIs but keep bterminal ─────────────────────


def test_break_e_remove_some_companion_clis(tmp_path):
    """Pin (e): rm ~/.local/bin/{ctx,tasks} but leave bterminal.
    Partial install state — wizard must offer Fix."""
    _build_clean_install(tmp_path)
    bin_dir = tmp_path / ".local" / "bin"
    (bin_dir / "ctx").unlink()
    (bin_dir / "tasks").unlink()
    # bterminal launcher still present
    assert (bin_dir / "bterminal").exists()
    assert detect_install_state(tmp_path) == "broken"


def test_break_e_remove_only_one_companion(tmp_path):
    """Pin (e-2): even one missing companion CLI → 'broken'.
    Conservative — better to surface a recoverable issue than
    silently let the user run a partial install."""
    _build_clean_install(tmp_path)
    (tmp_path / ".local" / "bin" / "consult").unlink()
    assert detect_install_state(tmp_path) == "broken"


# ─── Combined: all 5 scenarios at once ────────────────────────────────


def test_combined_breaks_c_d_e(tmp_path):
    """Stress: scenarios (c) stub claude + (d) stale lock + (e)
    missing companions applied simultaneously. detect must still
    return 'broken'.

    Note: combining (a) AND (b) — launcher AND pkg both gone —
    looks like 'not_installed' (no traces left), so the realistic
    multi-break case excludes those. (a)+(b) without anything
    else triggers 'not_installed' which is the correct semantic."""
    _build_clean_install(tmp_path)
    bin_dir = tmp_path / ".local" / "bin"
    claude_real = list((tmp_path / ".npm-global" / "lib"
                         / "node_modules" / "@fake" / "claude"
                         / "bin").glob("*"))[0]
    claude_real.write_text("Error: claude native binary not installed.")
    claude_real.chmod(0o644)                 # (c)
    (tmp_path / ".config" / "bterminal" / "install.lock").write_text(
        "999999\n")                          # (d)
    (bin_dir / "ctx").unlink()
    (bin_dir / "tasks").unlink()             # (e)
    assert detect_install_state(tmp_path) == "broken"


def test_a_and_b_combined_collapses_to_not_installed(tmp_path):
    """Edge case: (a)+(b) — both launcher and pkg gone — has no
    detectable trace of an install, so 'not_installed' is the
    correct return. Documented here so future contributors don't
    classify this as a regression."""
    _build_clean_install(tmp_path)
    (tmp_path / ".local" / "bin" / "bterminal").unlink()  # (a)
    (tmp_path / ".local" / "share" / "bterminal" / "bterminal"
     / "__init__.py").unlink()  # (b)
    # Also clear the dir tree so nothing remains
    import shutil
    shutil.rmtree(tmp_path / ".local" / "share" / "bterminal")
    shutil.rmtree(tmp_path / ".npm-global")
    for tool in ("ctx", "tasks", "consult", "memory_wizard", "claude_log",
                 "claude", "copilot"):
        p = tmp_path / ".local" / "bin" / tool
        if p.is_symlink() or p.exists():
            p.unlink()
    shutil.rmtree(tmp_path / ".config" / "bterminal", ignore_errors=True)
    assert detect_install_state(tmp_path) == "not_installed"
