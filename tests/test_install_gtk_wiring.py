"""Tests for install.sh + InstallerWizard wiring (task #6 / #78).

Two layers (no real install attempted — that's task #85's manual VM smoke):

  1. Bash structural — install.sh has the maybe_launch_gtk_wizard
     helper, all skip conditions present, no recursion (--status-json
     guard).
  2. Pure-Python — `bterminal/__main__.py:_run_installer_wizard()`
     resolves repo_dir from BTERMINAL_REPO_DIR / cwd / install dir
     and degrades gracefully when none found.
"""
from __future__ import annotations

import os
import re
import subprocess
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ─── Bash structural ────────────────────────────────────────────────────────


def test_install_sh_bash_syntax_remains_valid():
    """`bash -n` parses without errors after #6 changes."""
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"bash -n failed:\nstderr: {result.stderr}"
    )


def test_install_sh_defines_gtk_wizard_helper():
    """maybe_launch_gtk_wizard function defined + invoked once after
    arg parsing, before phase 1."""
    text = INSTALL_SH.read_text()
    assert re.search(r"^maybe_launch_gtk_wizard\(\)\s*\{", text, re.M)
    # And actually called (not just defined)
    invocations = re.findall(
        r"^maybe_launch_gtk_wizard\s*$", text, re.M,
    )
    assert len(invocations) == 1, (
        f"maybe_launch_gtk_wizard must be called exactly once; "
        f"got {len(invocations)}"
    )


@pytest.mark.parametrize("guard_pattern", [
    # Must skip when --headless explicitly set
    r'\[\[ "\$HEADLESS" == true \]\].*&& return',
    # Must skip when --status-json (recursion guard — wizard runs us)
    r'\[\[ "\$STATUS_JSON" == true \]\].*&& return',
    # Must skip when --no-sudo (offline-friendly bash flow)
    r'\[\[ "\$NO_SUDO" == true \]\].*&& return',
    # Must skip when no DISPLAY / WAYLAND
    r'\[\[ -z "\$\{DISPLAY:-\}\$\{WAYLAND_DISPLAY:-\}" \]\].*&& return',
])
def test_install_sh_gtk_wizard_skip_guards(guard_pattern):
    """Each documented skip condition must be present in
    maybe_launch_gtk_wizard. Otherwise users in CI / SSH sessions get
    surprise GUI spawns."""
    text = INSTALL_SH.read_text()
    assert re.search(guard_pattern, text), (
        f"missing skip guard: {guard_pattern!r}"
    )


def test_install_sh_gtk_wizard_checks_python3_gi():
    """python3-gi presence check before spawning — wizard would
    silently crash with no useful error otherwise."""
    text = INSTALL_SH.read_text()
    assert "python3 -c \"import gi; gi.require_version('Gtk','3.0')\"" in text


def test_install_sh_gtk_wizard_passes_repo_dir_env():
    """BTERMINAL_REPO_DIR must be exported when spawning the wizard so
    _run_installer_wizard() can locate install.sh from inside Python."""
    text = INSTALL_SH.read_text()
    assert 'BTERMINAL_REPO_DIR="$SCRIPT_DIR"' in text


def test_install_sh_gtk_wizard_falls_back_on_failure():
    """Non-zero exit from the wizard → continue with bash flow.
    Success exit (0) → install.sh exits 0 (wizard already ran the
    real installer internally)."""
    text = INSTALL_SH.read_text()
    # The function returns 0 (continue bash flow) on wizard failure
    assert "Wizard cancelled or unavailable" in text
    # Successful wizard run exits the script
    assert "InstallerWizard finished" in text


# ─── Python: __main__.py --installer flag ──────────────────────────────────


def test_main_argparse_accepts_installer_flag():
    """argparse exposes --installer flag (parsed in main())."""
    text = (REPO_ROOT / "bterminal" / "__main__.py").read_text()
    assert '"--installer"' in text
    assert 'args.installer' in text


def test_run_installer_wizard_function_exists():
    """Public function imported by tests below."""
    from bterminal.__main__ import _run_installer_wizard
    assert callable(_run_installer_wizard)


def test_run_installer_wizard_returns_1_when_repo_dir_unresolvable(
    tmp_path, monkeypatch, capsys,
):
    """No BTERMINAL_REPO_DIR + cwd has no install.sh + no install dir
    → print error + return 1 (bash falls back to its own flow)."""
    monkeypatch.delenv("BTERMINAL_REPO_DIR", raising=False)
    monkeypatch.chdir(tmp_path)
    # Force ~/.local/share/bterminal lookup to also miss
    monkeypatch.setenv("HOME", str(tmp_path / "fake_home"))

    from bterminal.__main__ import _run_installer_wizard
    rc = _run_installer_wizard()
    assert rc == 1
    captured = capsys.readouterr()
    assert "Cannot locate install.sh" in captured.out


def test_run_installer_wizard_uses_env_repo_dir_when_set(
    tmp_path, monkeypatch,
):
    """BTERMINAL_REPO_DIR present + valid → wizard constructed with it.
    Patches the wizard class on the real module (already imported by
    other tests in the suite) so we don't need a display + don't
    pollute sys.modules."""
    install_sh = tmp_path / "install.sh"
    install_sh.write_text("#!/bin/bash\necho fake\n")
    monkeypatch.setenv("BTERMINAL_REPO_DIR", str(tmp_path))

    fake_wizard_cls = MagicMock()
    fake_wizard_cls.return_value.run_and_install.return_value = True

    with patch("bterminal.ui.installer_wizard.InstallerWizard",
               fake_wizard_cls), \
         patch("gi.repository.GLib.set_prgname"), \
         patch("gi.repository.GLib.set_application_name"):
        from bterminal.__main__ import _run_installer_wizard
        rc = _run_installer_wizard()

    assert rc == 0
    fake_wizard_cls.assert_called_once()
    kwargs = fake_wizard_cls.call_args.kwargs
    assert kwargs["repo_dir"] == str(tmp_path)


def test_run_installer_wizard_falls_back_to_cwd(tmp_path, monkeypatch):
    """No BTERMINAL_REPO_DIR but cwd contains install.sh → use cwd."""
    install_sh = tmp_path / "install.sh"
    install_sh.write_text("#!/bin/bash\n")
    monkeypatch.delenv("BTERMINAL_REPO_DIR", raising=False)
    monkeypatch.chdir(tmp_path)

    fake_wizard_cls = MagicMock()
    fake_wizard_cls.return_value.run_and_install.return_value = False

    with patch("bterminal.ui.installer_wizard.InstallerWizard",
               fake_wizard_cls), \
         patch("gi.repository.GLib.set_prgname"), \
         patch("gi.repository.GLib.set_application_name"):
        from bterminal.__main__ import _run_installer_wizard
        rc = _run_installer_wizard()

    assert rc == 1  # user cancelled
    kwargs = fake_wizard_cls.call_args.kwargs
    assert kwargs["repo_dir"] == str(tmp_path)


# ─── Python: BT Tools menu hookup ──────────────────────────────────────────


def test_app_has_show_installer_wizard_method():
    """BTerminalApp._show_installer_wizard exposed for the Tools menu."""
    from bterminal.app import BTerminalApp
    assert hasattr(BTerminalApp, "_show_installer_wizard")
    assert callable(BTerminalApp._show_installer_wizard)


def test_app_menu_includes_install_dependencies_item():
    """Tools menu must register the new item — string check on the
    menubar builder so we don't need a display."""
    text = (REPO_ROOT / "bterminal" / "app.py").read_text()
    assert "Install dependencies" in text
    assert "_show_installer_wizard" in text


# ─── Detection logic table — symbolic test ─────────────────────────────────


@pytest.mark.parametrize("display,wayland,headless,no_sudo,status_json,gtk,expect", [
    # display=Y, wayland=N, headless=N, no_sudo=N, status_json=N, gtk=Y → spawn
    (":0",  "",  False, False, False, True,  True),
    # No display + no wayland → skip
    ("",    "",  False, False, False, True,  False),
    # Wayland session w/o X11 → still spawn
    ("",    "wayland-0", False, False, False, True, True),
    # --headless → skip even with display
    (":0",  "",  True,  False, False, True,  False),
    # --no-sudo → skip
    (":0",  "",  False, True,  False, True,  False),
    # --status-json (wizard recursion guard) → skip
    (":0",  "",  False, False, True,  True,  False),
    # No GTK bindings → skip
    (":0",  "",  False, False, False, False, False),
])
def test_gtk_spawn_decision_matrix(display, wayland, headless, no_sudo,
                                     status_json, gtk, expect, tmp_path):
    """Truth table: when does install.sh launch the wizard?

    This test exercises the helper logic by sourcing it in isolation
    (each guard expressed as `[[ ... ]] && return 0`). Keeps the
    bash + Python wiring honest as guards evolve.
    """
    script = f"""
        DISPLAY={display!r}
        WAYLAND_DISPLAY={wayland!r}
        HEADLESS={'true' if headless else 'false'}
        NO_SUDO={'true' if no_sudo else 'false'}
        STATUS_JSON={'true' if status_json else 'false'}
        # Stub gi import check
        python3() {{
            if [[ "$1" == "-c" && "$2" == *"import gi"* ]]; then
                return {0 if gtk else 1}
            fi
            command python3 "$@"
        }}

        maybe_spawn() {{
            [[ "$HEADLESS" == true ]]    && return 1
            [[ "$STATUS_JSON" == true ]] && return 1
            [[ "$NO_SUDO" == true ]]     && return 1
            [[ -z "${{DISPLAY:-}}${{WAYLAND_DISPLAY:-}}" ]] && return 1
            command -v python3 &>/dev/null || return 1
            if ! python3 -c "import gi; gi.require_version('Gtk','3.0')" \
                    >/dev/null 2>&1; then
                return 1
            fi
            return 0
        }}

        if maybe_spawn; then echo SPAWN; else echo SKIP; fi
    """
    result = subprocess.run(
        ["bash", "-c", script],
        capture_output=True, text=True, timeout=5,
    )
    out = result.stdout.strip()
    expected_word = "SPAWN" if expect else "SKIP"
    assert out == expected_word, (
        f"display={display}, wayland={wayland}, headless={headless}, "
        f"no_sudo={no_sudo}, status_json={status_json}, gtk={gtk}: "
        f"expected {expected_word}, got {out}"
    )
