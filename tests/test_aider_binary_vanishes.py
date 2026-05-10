"""Failure mode: aider binary disappears mid-session (#29 / #101,
audit § 6.1 #2).

When `aider` is removed from PATH while an Aider tab is active
(e.g. user runs `pipx uninstall aider-chat` in another terminal),
BT must:

  1. Detect the missing binary at next spawn attempt
     (find_binary() returns None) and route to the in-terminal
     'binary not found' error script.
  2. Show a deterministic, parseable error to the user — not a
     silent hang. The script lists the search paths checked and
     hints at how to fix it.
  3. Keep the tab alive (`exec bash` after the error message) so
     the user can install + retry without restarting BT.
  4. Allow re-spawn once the binary is back — find_binary()
     returns the new path on subsequent calls (no caching of the
     prior absent state).

Three decision branches from auto-trigger:
  (a) PTY EOF detected — the bash script's `exec bash` keeps PTY
      alive so VTE doesn't EOF the tab; tested via spawn-script
      shape inspection.
  (b) BT shows 'binary missing' message — deterministic banner +
      checked-paths list + fix hint.
  (c) Tab can be re-spawned — find_binary() is stateless across
      calls; restoring the binary means next call returns it.

VM-bound smoke (`pipx uninstall aider-chat`, send prompt, observe
log + stats) is documented in tests/manual/README.md and runs
through tools/test_aider_real_model.sh when ollama+aider available.
Headless tests below pin the dispatch logic without GTK.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


# ─── (a) find_binary() returns None when nothing on disk ────────────────


def test_aider_find_binary_returns_none_when_path_empty(tmp_path,
                                                          monkeypatch):
    """Pre-condition for the whole failure scenario: when no aider
    binary exists at any of the configured search paths, find_binary
    returns None. Without this, build_argv would emit malformed
    argv and spawn would crash on execvp."""
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")

    # Empty PATH so shutil.which finds nothing
    monkeypatch.setenv("PATH", str(tmp_path / "no-such-bin"))
    # Override search paths to a guaranteed-missing dir
    aider._binary_spec["search_paths"] = [  # noqa: SLF001
        str(tmp_path / "missing" / "aider"),
    ]
    assert aider.find_binary() is None


def test_aider_build_argv_returns_empty_when_binary_missing(tmp_path,
                                                              monkeypatch):
    """build_argv's first action is binary = self.find_binary().
    When None → return []. Caller (TerminalTab._build_spawn_script)
    detects empty argv and routes to the not-found script."""
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    monkeypatch.setenv("PATH", str(tmp_path / "no-such-bin"))
    aider._binary_spec["search_paths"] = [  # noqa: SLF001
        str(tmp_path / "missing" / "aider"),
    ]
    argv = aider.build_argv(
        {"project_dir": "/tmp/p", "provider_options": {}},
        intro_prompt="",
    )
    assert argv == [], (
        f"build_argv should return [] when binary missing, got {argv!r}"
    )


# ─── (b) BT shows deterministic 'binary missing' error script ────────────


def test_build_binary_not_found_script_for_aider_lists_search_paths():
    """The not-found script is a bash printf sequence that BT shoves
    into VTE so the user sees what was checked + how to fix.
    It MUST list every search path from defaults.json so a missing
    pipx install location is obvious to debug."""
    from bterminal.ui.terminal_tab import TerminalTab
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")

    script = TerminalTab._build_binary_not_found_script(aider)
    # Provider's display name must appear in the banner
    assert "Aider" in script, f"banner missing 'Aider': {script[:200]!r}"
    # Every search path from defaults.json must appear so the user
    # sees what was checked
    for path in (
        "~/.local/bin/aider",
        "~/.local/pipx/venvs/aider-chat/bin/aider",
        "/usr/local/bin/aider",
        "/usr/bin/aider",
        "/opt/homebrew/bin/aider",
    ):
        assert path in script, (
            f"not-found script missing search path {path!r}"
        )


def test_not_found_script_includes_fix_hint():
    """The user-visible message tells them what to do next:
    re-run the installer, install manually, or check PATH. Pin
    that the hint stays in the script — degrading to a bare error
    forces support tickets."""
    from bterminal.ui.terminal_tab import TerminalTab
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    script = TerminalTab._build_binary_not_found_script(aider)
    assert "To fix:" in script
    assert "install.sh" in script
    assert "PATH" in script


def test_not_found_script_has_red_color_banner():
    """ANSI red banner ('\\033[1;31m━━━ ... ━━━') makes the error
    visually distinct from normal output. Pin so a refactor doesn't
    accidentally drop the color codes."""
    from bterminal.ui.terminal_tab import TerminalTab
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    script = TerminalTab._build_binary_not_found_script(aider)
    assert "\\033[1;31m" in script  # bold red ANSI
    assert "━━━" in script  # box-drawing banner


def test_not_found_script_keeps_pty_alive_with_exec_bash():
    """(a) PTY EOF detection — the script ENDS with `exec bash`
    so VTE doesn't see immediate EOF after printing the error.
    Without this, the tab would close itself + the user couldn't
    fix anything in-place."""
    from bterminal.ui.terminal_tab import TerminalTab
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    script = TerminalTab._build_binary_not_found_script(aider)
    assert script.rstrip().endswith("exec bash"), (
        f"script must end with `exec bash` to keep PTY alive; got: "
        f"...{script[-200:]!r}"
    )


def test_build_spawn_script_falls_back_to_not_found_when_argv_empty():
    """Defensive double-check: even if a caller passes around the
    Provider directly without calling find_binary first,
    _build_spawn_script's `if not argv:` branch routes to the
    same error script. Belt + suspenders for (b) error visibility."""
    from bterminal.ui.terminal_tab import TerminalTab
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")

    # Force build_argv to return [] (binary missing scenario)
    with patch.object(aider, "build_argv", return_value=[]):
        script = TerminalTab._build_spawn_script(
            aider, {"project_dir": "/tmp/x", "provider_options": {}},
            intro_prompt="",
        )
    # Same not-found script — same banner
    assert "Aider not found" in script
    assert "exec bash" in script.rstrip().split("\n")[-1] or \
        script.rstrip().endswith("exec bash")


# ─── (c) Re-spawn after binary restored — no stale state ────────────────


def test_find_binary_returns_new_path_after_binary_appears(
        tmp_path, monkeypatch):
    """find_binary is stateless — calling it once when the binary
    is missing, then again after restoration, returns the path.
    Without this, re-spawning the tab after `pipx install aider-chat`
    would still see 'binary missing'.

    Note: empty PATH so shutil.which fallback in find_binary doesn't
    pick up the host's aider during the 'missing' phase."""
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))

    fake_aider = tmp_path / "aider"
    aider._binary_spec["search_paths"] = [str(fake_aider)]  # noqa: SLF001

    # Phase 1: binary missing
    assert aider.find_binary() is None

    # Phase 2: user reinstalls
    fake_aider.write_text("#!/bin/bash\necho aider\n")
    fake_aider.chmod(0o755)

    # Subsequent call sees the new path
    assert aider.find_binary() == str(fake_aider)


def test_find_binary_returns_none_after_binary_removed(
        tmp_path, monkeypatch):
    """Mirror: existing binary, then deleted → next find_binary
    returns None (no caching of previously-resolved path).

    Empty PATH so shutil.which fallback can't pick up the host's
    aider after the test binary is removed."""
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))

    fake_aider = tmp_path / "aider"
    fake_aider.write_text("#!/bin/bash\necho aider\n")
    fake_aider.chmod(0o755)
    aider._binary_spec["search_paths"] = [str(fake_aider)]  # noqa: SLF001

    assert aider.find_binary() == str(fake_aider)

    # User uninstalls
    fake_aider.unlink()
    assert aider.find_binary() is None


def test_full_uninstall_reinstall_cycle_via_find_binary(
        tmp_path, monkeypatch):
    """End-to-end: present → missing → present, find_binary tracks
    every transition. Pins (c) — tab can be re-spawned without
    restart."""
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
    fake_aider = tmp_path / "aider"
    aider._binary_spec["search_paths"] = [str(fake_aider)]  # noqa: SLF001

    # Cycle 1: install
    fake_aider.write_text("#!/bin/bash\nexit 0\n")
    fake_aider.chmod(0o755)
    assert aider.find_binary() == str(fake_aider)

    # Cycle 2: uninstall
    fake_aider.unlink()
    assert aider.find_binary() is None

    # Cycle 3: reinstall (different path simulating pipx venv)
    pipx_path = tmp_path / "pipx-venvs-aider" / "aider"
    pipx_path.parent.mkdir()
    pipx_path.write_text("#!/bin/bash\nexit 0\n")
    pipx_path.chmod(0o755)
    aider._binary_spec["search_paths"] = [  # noqa: SLF001
        str(fake_aider), str(pipx_path)]
    assert aider.find_binary() == str(pipx_path)


# ─── REST surface: tab status reports correctly after binary loss ───────


def test_spawn_script_for_existing_binary_still_uses_exec_bash(tmp_path,
                                                                 monkeypatch):
    """Sanity / contrast: when binary IS present, the spawn script
    runs aider then `exec bash` to keep the tab alive after aider
    exits. Same PTY-keep-alive contract as the error script."""
    from bterminal.ui.terminal_tab import TerminalTab
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")

    fake_aider = tmp_path / "aider"
    fake_aider.write_text("#!/bin/bash\nexit 0\n")
    fake_aider.chmod(0o755)
    aider._binary_spec["search_paths"] = [str(fake_aider)]  # noqa: SLF001

    script = TerminalTab._build_spawn_script(
        aider, {"project_dir": str(tmp_path), "provider_options": {}},
        intro_prompt="",
    )
    # Aider invoked
    assert str(fake_aider) in script
    # PTY stays alive after aider exit
    assert "exec bash" in script


# ─── Integration with REST surface: /api/tabs reports state ─────────────


def test_rest_route_for_aider_tab_state_does_not_branch_on_binary_status():
    """Source-grep: bterminal/debug_rest.py's /api/tabs route doesn't
    have provider-specific 'is binary present' checks. The tab's
    state (running / exited / crashed) flows from VTE's child-exited
    signal, not from a runtime binary check. This means a tab that
    started fine and then loses its binary (mid-uninstall) still
    reports 'running' until the user actually invokes the now-dead
    binary — exactly the behavior the auto-trigger plan describes."""
    repo = Path(__file__).resolve().parent.parent
    src = (repo / "bterminal" / "debug_rest.py").read_text()
    # No provider-specific branches on binary state
    bad = ["aider_binary_alive", "is_aider_running",
           "aider.find_binary"]
    for pat in bad:
        assert pat not in src, (
            f"debug_rest.py has provider-specific runtime binary "
            f"check {pat!r} — would interfere with the failure flow"
        )


# ─── Cross-check with existing parity test (aiding the audit) ───────────


def test_build_argv_returns_empty_path_matches_parity_contract():
    """Cross-reference: tests/test_provider_parity.py asserted both
    Claude and Aider return [] when binary missing. Pin that
    contract here too — duplicate intent so #29's failure scenario
    has a self-contained pin even if parity test gets refactored."""
    reg = ProviderRegistry(config=load_providers_config())
    for provider_name in ("claude", "aider", "copilot"):
        provider = reg.get(provider_name)
        with patch.object(provider, "find_binary", return_value=None):
            argv = provider.build_argv(
                {"project_dir": "/tmp/p", "provider_options": {}},
                intro_prompt="",
            )
        assert argv == [], (
            f"{provider_name} build_argv didn't return [] for "
            f"missing binary"
        )


# ─── End-to-end orchestration: spawn → vanish → respawn ──────────────────


def test_lifecycle_install_use_uninstall_reinstall(tmp_path, monkeypatch):
    """Synthesize the full failure scenario as a single test:
    1. Install: fake aider exists → find_binary returns it
    2. Use: build_argv emits valid argv
    3. Uninstall: rm binary → find_binary returns None
    4. Failed re-spawn: build_argv returns [] → spawn falls back
       to not-found script
    5. Reinstall: binary back → find_binary picks it up
    6. Successful re-spawn: build_argv emits valid argv"""
    from bterminal.ui.terminal_tab import TerminalTab

    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    monkeypatch.setenv("PATH", str(tmp_path / "no-bin"))
    binary = tmp_path / "aider"
    aider._binary_spec["search_paths"] = [str(binary)]  # noqa: SLF001

    # 1+2. Install + use
    binary.write_text("#!/bin/bash\nexit 0\n")
    binary.chmod(0o755)
    assert aider.find_binary() == str(binary)
    argv1 = aider.build_argv(
        {"project_dir": "/tmp/p", "provider_options": {}},
        intro_prompt="hello",
    )
    assert argv1 and argv1[0] == str(binary)

    # 3+4. Uninstall + failed spawn
    binary.unlink()
    assert aider.find_binary() is None
    argv2 = aider.build_argv(
        {"project_dir": "/tmp/p", "provider_options": {}},
        intro_prompt="hello",
    )
    assert argv2 == []
    # Spawn script falls back to not-found
    script = TerminalTab._build_spawn_script(
        aider, {"project_dir": "/tmp/p", "provider_options": {}},
        intro_prompt="",
    )
    assert "Aider not found" in script
    assert script.rstrip().endswith("exec bash")

    # 5+6. Reinstall + successful spawn
    binary.write_text("#!/bin/bash\nexit 0\n")
    binary.chmod(0o755)
    assert aider.find_binary() == str(binary)
    argv3 = aider.build_argv(
        {"project_dir": "/tmp/p", "provider_options": {}},
        intro_prompt="hello",
    )
    assert argv3 and argv3[0] == str(binary)
    script_post = TerminalTab._build_spawn_script(
        aider, {"project_dir": "/tmp/p", "provider_options": {}},
        intro_prompt="hello",
    )
    # Real spawn script (NOT the not-found banner) — runs aider
    assert str(binary) in script_post
    assert "Aider not found" not in script_post
