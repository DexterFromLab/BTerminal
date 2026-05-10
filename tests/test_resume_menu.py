"""Tests for the 'Resume last session' context-menu item (task #61).

Pure-helper coverage of session_supports_resume_menu + the spawn-side
contract that force_options={'resume': True} reaches the cloned config
without mutating the input.

Decision tree:
  (a) menu item present for Claude (capabilities.resume_flag=True)
  (b) absent / gated when capabilities.resume_flag=False
  (c) spawn passes resume=True even when session.provider_options.resume==False
  (d) saved session config (input dict) unchanged after invocation
"""
from __future__ import annotations

import sys
from copy import deepcopy
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.ui.sidebar import session_supports_resume_menu


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


# ─── (a) (b) Gating helper ──────────────────────────────────────────────────


def test_resume_menu_visible_for_claude_session():
    reg = ProviderRegistry(config=load_providers_config())
    sess = {"name": "x", "provider": "claude", "project_dir": "/t"}
    assert session_supports_resume_menu(sess, reg) is True


def test_resume_menu_visible_for_copilot_when_capability_true():
    """Default Copilot config has resume_flag=true so the gating
    helper says 'show'. If that turns out to be wrong in practice
    (Copilot's --resume has different semantics), the fix is to
    flip resume_flag=false in defaults.json — this test then becomes
    the regression check that the gating actually hides the item."""
    reg = ProviderRegistry(config=load_providers_config())
    sess = {"name": "x", "provider": "copilot", "project_dir": "/t"}
    assert session_supports_resume_menu(sess, reg) is True


def test_resume_menu_hidden_when_provider_capability_false():
    """Take Copilot's config, flip resume_flag=false — gate fires."""
    cfg = load_providers_config()
    cfg["providers"]["copilot"]["capabilities"]["resume_flag"] = False
    reg = ProviderRegistry(config=cfg)
    sess = {"name": "x", "provider": "copilot", "project_dir": "/t"}
    assert session_supports_resume_menu(sess, reg) is False


def test_resume_menu_hidden_for_unknown_provider():
    """Forward-compat session naming a future-version provider — the
    gate returns False (no item) instead of raising KeyError."""
    reg = ProviderRegistry(config=load_providers_config())
    sess = {"name": "x", "provider": "future-cli-2030", "project_dir": "/t"}
    assert session_supports_resume_menu(sess, reg) is False


def test_resume_menu_hidden_for_empty_session():
    reg = ProviderRegistry(config=load_providers_config())
    assert session_supports_resume_menu(None, reg) is False
    assert session_supports_resume_menu({}, reg) is False


def test_resume_menu_legacy_session_without_provider_field_defaults_to_claude():
    """Legacy session without `provider` defaults to claude (which has
    resume_flag=true), so the menu shows up."""
    reg = ProviderRegistry(config=load_providers_config())
    sess = {"name": "legacy", "project_dir": "/t"}
    assert session_supports_resume_menu(sess, reg) is True


# ─── (c) (d) Spawn-side contract: force_options + immutability ──────────────


def _make_app_stub():
    from bterminal import app as app_mod
    stub = MagicMock(spec=["open_claude_tab", "open_ai_tab_one_off"])
    stub.open_ai_tab_one_off = app_mod.BTerminalApp.open_ai_tab_one_off.__get__(stub)
    return stub


def test_force_resume_overrides_saved_resume_false():
    """User saved session with provider_options.resume=False; the
    Resume menu item must spawn with resume=True regardless."""
    app = _make_app_stub()
    config = {
        "name": "MyClaude", "provider": "claude", "project_dir": "/t",
        "provider_options": {"resume": False, "skip_permissions": True},
    }

    app.open_ai_tab_one_off(config, force_options={"resume": True})

    cloned = app.open_claude_tab.call_args[0][0]
    assert cloned["provider_options"]["resume"] is True, (
        f"force_options must override saved resume=False; "
        f"got {cloned['provider_options']}"
    )
    # Other options preserved
    assert cloned["provider_options"]["skip_permissions"] is True


def test_force_resume_does_not_mutate_input_config():
    """Input config dict (which is the SAME object held by
    claude_manager) must not be touched. Otherwise resume=True would
    persist to ai_sessions.json on next save."""
    app = _make_app_stub()
    config = {
        "name": "x", "provider": "claude", "project_dir": "/t",
        "provider_options": {"resume": False, "sudo": True},
    }
    snapshot = deepcopy(config)

    app.open_ai_tab_one_off(config, force_options={"resume": True})

    assert config == snapshot, (
        f"force_options leaked into input config: {config} != {snapshot}"
    )
    # Sub-dict identity preserved (deep clone applied)
    cloned = app.open_claude_tab.call_args[0][0]
    assert cloned["provider_options"] is not config["provider_options"]


def test_force_options_works_alongside_provider_override():
    """Run-as Copilot + force resume in the same call — both apply."""
    app = _make_app_stub()
    config = {
        "name": "x", "provider": "claude", "project_dir": "/t",
        "provider_options": {"resume": False},
    }
    app.open_ai_tab_one_off(
        config, override_provider="copilot",
        force_options={"resume": True},
    )

    cloned = app.open_claude_tab.call_args[0][0]
    assert cloned["provider"] == "copilot"
    assert cloned["provider_options"]["resume"] is True


def test_force_options_handles_legacy_config_without_provider_options():
    """Legacy session without provider_options dict — force_options
    creates one rather than crashing on the missing key."""
    app = _make_app_stub()
    config = {"name": "legacy", "provider": "claude", "project_dir": "/t"}

    app.open_ai_tab_one_off(config, force_options={"resume": True})
    cloned = app.open_claude_tab.call_args[0][0]
    assert cloned["provider_options"] == {"resume": True}
    # Input config still has no provider_options
    assert "provider_options" not in config


def test_force_options_can_set_multiple_keys():
    """force_options is a dict, not a single flag — exercise the
    general case so future menu items (e.g. plan-mode shortcut) can
    use the same plumbing."""
    app = _make_app_stub()
    config = {"name": "x", "provider": "copilot", "project_dir": "/t",
              "provider_options": {"plan_mode": False, "skip_permissions": False}}

    app.open_ai_tab_one_off(
        config, force_options={"plan_mode": True, "skip_permissions": True},
    )

    cloned = app.open_claude_tab.call_args[0][0]
    assert cloned["provider_options"]["plan_mode"] is True
    assert cloned["provider_options"]["skip_permissions"] is True


def test_no_force_options_keeps_saved_provider_options():
    """When no overrides specified, saved provider_options carry through
    untouched — confirms force_options is purely additive overlay."""
    app = _make_app_stub()
    config = {"name": "x", "provider": "claude", "project_dir": "/t",
              "provider_options": {"resume": False, "skip_permissions": True}}

    app.open_ai_tab_one_off(config)
    cloned = app.open_claude_tab.call_args[0][0]
    assert cloned["provider_options"] == {
        "resume": False, "skip_permissions": True,
    }
