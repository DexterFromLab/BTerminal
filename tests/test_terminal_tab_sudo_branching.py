"""Unit tests for TerminalTab sudo prologue branching (BUG#31d).

Pure-function tests on TerminalTab._build_spawn_script — no GTK widgets,
no DISPLAY required. We stub a provider object that implements just the
fields _build_spawn_script reads (build_argv, capabilities.supports_sudo).
"""
from __future__ import annotations

import sys
from pathlib import Path
from types import SimpleNamespace

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bterminal.ui.terminal_tab import TerminalTab


def _make_provider(*, supports_sudo: bool, argv=("claude",)):
    """Minimal provider stub satisfying _build_spawn_script's contract."""
    return SimpleNamespace(
        capabilities=SimpleNamespace(supports_sudo=supports_sudo),
        build_argv=lambda config, intro_prompt: list(argv),
    )


def _make_config(sudo: bool):
    return {"provider_options": {"sudo": sudo}, "project_dir": None}


def test_spawn_script_uses_shared_when_cache_set():
    """askpass_path provided → SUDO_ASKPASS export, NO read-loop prompt."""
    provider = _make_provider(supports_sudo=True)
    config = _make_config(sudo=True)
    askpass_path = "/tmp/bt-askpass-shared-XYZ"

    script = TerminalTab._build_spawn_script(
        provider, config, intro_prompt="", askpass_path=askpass_path,
    )

    # shlex.quote leaves safe paths unquoted; assert the path is exported
    # exactly once under SUDO_ASKPASS=
    assert f"export SUDO_ASKPASS={askpass_path}\n" in script
    assert "read -rsp" not in script.split("Sudo cache expired")[0], (
        "Read-loop must NOT appear before the cache-expired fallback branch"
    )
    assert "sudo -A true" in script  # pre-check exists
    assert "Sudo cache expired" in script  # graceful fallback hint
    assert "claude" in script  # CLI argv survived


def test_spawn_script_falls_back_to_interactive_when_cache_empty():
    """askpass_path=None + sudo=True → legacy interactive read-loop prologue."""
    provider = _make_provider(supports_sudo=True)
    config = _make_config(sudo=True)

    script = TerminalTab._build_spawn_script(
        provider, config, intro_prompt="", askpass_path=None,
    )

    assert "Enter sudo password:" in script
    assert "export SUDO_ASKPASS=\"$ASKPASS\"" in script
    # No shared-askpass artefacts
    assert "Sudo cache expired" not in script
    assert "bt-askpass-shared" not in script


def test_spawn_script_no_prologue_when_supports_sudo_false():
    """Provider without supports_sudo capability → no prologue at all,
    even when config requests sudo (legacy Claude back-compat)."""
    provider = _make_provider(supports_sudo=False)
    config = _make_config(sudo=True)

    script = TerminalTab._build_spawn_script(
        provider, config, intro_prompt="", askpass_path="/tmp/whatever",
    )

    assert "SUDO_ASKPASS" not in script
    assert "Enter sudo password:" not in script
    assert script.startswith("claude\nexec bash\n") or script == (
        "claude\nexec bash\n"
    )


def test_spawn_script_no_prologue_when_sudo_opt_false():
    """opts['sudo']=False → no prologue, askpass_path is ignored."""
    provider = _make_provider(supports_sudo=True)
    config = _make_config(sudo=False)

    script = TerminalTab._build_spawn_script(
        provider, config, intro_prompt="",
        askpass_path="/tmp/bt-askpass-shared-XYZ",
    )

    assert "SUDO_ASKPASS" not in script
    assert "Enter sudo password:" not in script
    assert "Sudo cache expired" not in script
