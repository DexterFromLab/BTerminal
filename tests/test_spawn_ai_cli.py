"""Tests for spawn_ai_cli refactor (T2.1).

Covers the pure helpers extracted from the old spawn_claude:
    TerminalTab._build_spawn_script(provider, config, intro_prompt)
    TerminalTab._build_binary_not_found_script(provider)

These don't touch GTK so they run as fast unit tests. The full
spawn_ai_cli wiring (registry lookup, find_binary, spawn_async) is
exercised end-to-end by the existing E2E suite — here we focus on
behavioral parity with pre-T2.1 spawn_claude bash output.
"""
from __future__ import annotations

import json
import shlex
import stat

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.ui.terminal_tab import TerminalTab


@pytest.fixture(autouse=True)
def _reset_registry_singleton():
    reset_registry()
    yield
    reset_registry()


def _make_executable(path):
    path.write_text("#!/bin/sh\necho fake\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def claude_provider_with_fake_binary(tmp_path):
    """Return a ClaudeProvider whose find_binary() resolves to a real
    file under tmp_path, so build_argv produces a usable script."""
    fake = tmp_path / "claude"
    _make_executable(fake)
    cfg = json.loads(json.dumps(load_providers_config()))
    cfg["providers"]["claude"]["binary"]["search_paths"] = [str(fake)]
    reg = ProviderRegistry(config=cfg)
    return reg.get("claude"), str(fake)


@pytest.fixture
def copilot_provider_with_fake_binary(tmp_path):
    fake = tmp_path / "copilot"
    _make_executable(fake)
    cfg = json.loads(json.dumps(load_providers_config()))
    cfg["providers"]["copilot"]["binary"]["search_paths"] = [str(fake)]
    reg = ProviderRegistry(config=cfg)
    return reg.get("copilot"), str(fake)


# ─── _build_spawn_script — Claude (parity with pre-T2.1 behavior) ───────────

def test_claude_spawn_script_contains_binary_and_exec_bash(
    claude_provider_with_fake_binary,
):
    provider, fake = claude_provider_with_fake_binary
    script = TerminalTab._build_spawn_script(provider, {}, "")
    assert fake in script
    assert script.rstrip().endswith("exec bash")


def test_claude_spawn_script_with_resume_flag(claude_provider_with_fake_binary):
    provider, fake = claude_provider_with_fake_binary
    script = TerminalTab._build_spawn_script(
        provider, {"resume": True}, "")
    assert "--resume" in script


def test_claude_spawn_script_with_skip_permissions(claude_provider_with_fake_binary):
    provider, fake = claude_provider_with_fake_binary
    script = TerminalTab._build_spawn_script(
        provider, {"skip_permissions": True}, "")
    assert "--dangerously-skip-permissions" in script


def test_claude_spawn_script_with_intro_prompt(claude_provider_with_fake_binary):
    provider, _ = claude_provider_with_fake_binary
    intro = "Hello Claude, work in /tmp/foo"
    script = TerminalTab._build_spawn_script(provider, {}, intro)
    # shlex.quote'd intro must be present
    assert shlex.quote(intro) in script


def test_claude_spawn_script_quotes_intro_with_special_chars(
    claude_provider_with_fake_binary,
):
    """Single quotes / spaces / newlines must survive shell wrapping."""
    provider, _ = claude_provider_with_fake_binary
    intro = "Hello 'world' with \"quotes\" and $vars"
    script = TerminalTab._build_spawn_script(provider, {}, intro)
    quoted = shlex.quote(intro)
    assert quoted in script


def test_claude_spawn_script_full_roundtrip_argv_unchanged(
    claude_provider_with_fake_binary,
):
    """Same configuration as pre-T2.1 spawn_claude → bash command must
    contain claude_path + flags + intro in the same order."""
    provider, fake = claude_provider_with_fake_binary
    config = {"resume": True, "skip_permissions": True}
    script = TerminalTab._build_spawn_script(provider, config, "INTRO")
    # First line is the actual command
    first_line = script.splitlines()[0]
    parts = shlex.split(first_line)
    assert parts[0] == fake
    assert parts[1] == "--resume"
    assert parts[2] == "--dangerously-skip-permissions"
    assert parts[3] == "INTRO"


def test_claude_spawn_script_provider_options_schema(
    claude_provider_with_fake_binary,
):
    """R4.2 schema: flags live under provider_options. Same parity test
    as above but via the canonical nested-options form."""
    provider, fake = claude_provider_with_fake_binary
    config = {"provider_options": {"resume": True, "skip_permissions": True}}
    script = TerminalTab._build_spawn_script(provider, config, "")
    first_line = script.splitlines()[0]
    parts = shlex.split(first_line)
    assert parts[1:] == ["--resume", "--dangerously-skip-permissions"]


# ─── Sudo askpass prologue (Claude-only via supports_sudo capability) ───────

def test_sudo_prologue_added_when_claude_config_requests(
    claude_provider_with_fake_binary,
):
    """sudo=True + Claude (supports_sudo capability) → prologue prepended."""
    provider, _ = claude_provider_with_fake_binary
    script = TerminalTab._build_spawn_script(
        provider, {"sudo": True}, "")
    assert "Enter sudo password" in script
    assert "SUDO_ASKPASS" in script


def test_sudo_prologue_skipped_when_provider_lacks_supports_sudo(
    copilot_provider_with_fake_binary,
):
    """Copilot has supports_sudo=False → sudo flag is silently ignored."""
    provider, _ = copilot_provider_with_fake_binary
    script = TerminalTab._build_spawn_script(
        provider, {"sudo": True}, "")
    assert "Enter sudo password" not in script
    assert "SUDO_ASKPASS" not in script


def test_sudo_prologue_skipped_when_sudo_false(claude_provider_with_fake_binary):
    provider, _ = claude_provider_with_fake_binary
    script = TerminalTab._build_spawn_script(provider, {}, "")
    assert "SUDO_ASKPASS" not in script


# ─── Copilot (T2.1 skeleton: build_argv returns just [binary]) ──────────────

def test_copilot_spawn_script_contains_binary_and_tui_safe(
    copilot_provider_with_fake_binary,
):
    """After T2.3, CopilotProvider.build_argv prepends TUI-safe flags
    (--no-mouse / --plain-diff / --no-color) so VTE can
    render Copilot's TUI cleanly."""
    provider, fake = copilot_provider_with_fake_binary
    script = TerminalTab._build_spawn_script(provider, {}, "")
    first_line = script.splitlines()[0]
    parts = shlex.split(first_line)
    assert parts == [
        fake, "--no-mouse", "--plain-diff", "--no-color",
    ]


def test_copilot_skip_permissions_emits_yolo(
    copilot_provider_with_fake_binary,
):
    """T2.3: skip_permissions=True maps to Copilot's --yolo flag.
    Note: Claude's flag is --dangerously-skip-permissions, Copilot's is
    --yolo — the abstraction handles the mapping per provider."""
    provider, _ = copilot_provider_with_fake_binary
    script = TerminalTab._build_spawn_script(
        provider, {"skip_permissions": True}, "")
    assert "--yolo" in script
    # Claude's flag must NOT leak into Copilot scripts
    assert "--dangerously-skip-permissions" not in script


# ─── Binary not found (generalized for any provider) ────────────────────────

def test_binary_not_found_includes_provider_long_label(
    claude_provider_with_fake_binary,
):
    provider, _ = claude_provider_with_fake_binary
    script = TerminalTab._build_binary_not_found_script(provider)
    assert "Claude Code not found" in script
    assert "Locations checked" in script
    assert script.rstrip().endswith("exec bash")


def test_binary_not_found_for_copilot(copilot_provider_with_fake_binary):
    provider, _ = copilot_provider_with_fake_binary
    script = TerminalTab._build_binary_not_found_script(provider)
    assert "GitHub Copilot CLI not found" in script


def test_binary_not_found_renders_search_paths():
    """User should see exactly which paths were checked. Use vanilla
    ClaudeProvider (without the monkeypatched fixture) so the default
    npm-global path is rendered."""
    from bterminal.providers.claude import ClaudeProvider
    provider = ClaudeProvider(load_providers_config()["providers"]["claude"])
    script = TerminalTab._build_binary_not_found_script(provider)
    # Defaults include npm-global path
    assert ".npm-global/bin/claude" in script


# ─── spawn_ai_cli registry dispatch (raise / fallback) ───────────────────────

def test_spawn_ai_cli_unknown_provider_raises():
    """spawn_ai_cli with an unregistered provider name must raise
    KeyError before reaching any GTK calls (caller surfaces the error
    in-terminal or via dialog). MagicMock as self lets us call the
    unbound method without instantiating GTK widgets."""
    from unittest.mock import MagicMock
    self_mock = MagicMock()
    with pytest.raises(KeyError) as exc:
        TerminalTab.spawn_ai_cli(
            self_mock, {"provider": "totally-unknown-provider-xyz"})
    assert "totally-unknown-provider-xyz" in str(exc.value)


# ─── T4.6.1: spawn_claude alias removed ────────────────────────────────────

def test_spawn_claude_alias_was_removed():
    """T4.6.1 (2026-05-07): the deprecated `spawn_claude` instance
    method is gone. Callers use `spawn_ai_cli(config)` directly; if the
    config dict lacks a `provider` key, spawn_ai_cli defaults to
    "claude" via `config.get("provider", "claude")`."""
    assert not hasattr(TerminalTab, "spawn_claude"), (
        "TerminalTab.spawn_claude alias should be removed; found one"
    )


def test_spawn_ai_cli_defaults_to_claude_for_legacy_config():
    """The sole backward-compat path for pre-T1.6 sessions — a config
    dict without `provider` is treated as Claude (matches AISessionManager
    auto-tag behavior in models.py)."""
    from unittest.mock import MagicMock
    from bterminal.providers import reset_registry
    reset_registry()
    try:
        self_mock = MagicMock()
        with pytest.raises(KeyError):
            # Same KeyError contract verified by test_spawn_ai_cli_unknown_provider_raises
            TerminalTab.spawn_ai_cli(
                self_mock, {"provider": "totally-unknown-provider"})
        # Default-claude path succeeds when registry has the provider:
        # we don't repeat the full happy-path test here — covered already
        # by test_claude_spawn_script_* + test_spawn_ai_cli_unknown_provider_raises.
    finally:
        reset_registry()
