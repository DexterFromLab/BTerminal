"""Capability dispatch for rules injection — T3.7.

The pure helper `should_inject_rules(ai_config, registry)` decides
whether `_maybe_inject_rules` proceeds with the periodic-injection
flow. Both Claude AND Copilot are opt-in at T3.7 baseline (PTY
feed_child works identically), but the capability flag lets future
providers — or per-user `providers.json` overrides — opt out.
"""
from __future__ import annotations

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.ui.terminal_tab import should_inject_rules


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def registry():
    return ProviderRegistry(config=load_providers_config())


# ─── Both providers inject (T3.7 acceptance) ────────────────────────────────

def test_claude_injects(registry):
    """Claude has rules_inject=True — pre-existing behavior preserved."""
    ai_config = {"provider": "claude", "project_dir": "/tmp/proj"}
    assert should_inject_rules(ai_config, registry) is True


def test_copilot_injects_after_t3_7(registry):
    """T3.7 acceptance: Copilot's rules_inject capability is now True
    (flipped from False at T1.4 baseline). Same PTY feed_child code
    path runs for both providers."""
    ai_config = {"provider": "copilot", "project_dir": "/tmp/proj"}
    assert should_inject_rules(ai_config, registry) is True


def test_both_providers_inject(registry):
    """Combined acceptance: both providers in defaults.json declare
    rules_inject=True."""
    for provider_name in ("claude", "copilot"):
        ai_config = {"provider": provider_name, "project_dir": "/tmp/proj"}
        assert should_inject_rules(ai_config, registry) is True, (
            f"{provider_name} should declare rules_inject=True at T3.7"
        )


def test_implicit_claude_when_provider_field_missing(registry):
    """Pre-T1.6 sessions without `provider` key default to Claude."""
    assert should_inject_rules({"project_dir": "/tmp"}, registry) is True


# ─── Capability override (per-user opt-out) ─────────────────────────────────

def test_capability_override_disables_claude_injection():
    """User can suppress periodic re-injection via providers.json
    override — capability flag is the single switch."""
    cfg = load_providers_config()
    cfg["providers"]["claude"]["capabilities"]["rules_inject"] = False
    reg = ProviderRegistry(config=cfg)
    ai_config = {"provider": "claude", "project_dir": "/tmp"}
    assert should_inject_rules(ai_config, reg) is False


def test_capability_override_disables_copilot_injection():
    cfg = load_providers_config()
    cfg["providers"]["copilot"]["capabilities"]["rules_inject"] = False
    reg = ProviderRegistry(config=cfg)
    ai_config = {"provider": "copilot", "project_dir": "/tmp"}
    assert should_inject_rules(ai_config, reg) is False


# ─── Tab-type gating ────────────────────────────────────────────────────────

def test_no_ai_config_returns_false(registry):
    """SSH / local tabs (ai_config=None) never inject rules."""
    assert should_inject_rules(None, registry) is False


def test_empty_ai_config_returns_false(registry):
    assert should_inject_rules({}, registry) is False


# ─── Defensive: unknown provider / no registry ──────────────────────────────

def test_unknown_provider_returns_false(registry):
    ai_config = {"provider": "totally-fake", "project_dir": "/tmp/proj"}
    assert should_inject_rules(ai_config, registry) is False


def test_none_registry_returns_false():
    ai_config = {"provider": "claude", "project_dir": "/tmp"}
    assert should_inject_rules(ai_config, None) is False


# ─── Module export sanity ───────────────────────────────────────────────────

def test_helper_exported_from_terminal_tab_module():
    from bterminal.ui import terminal_tab as tt
    assert hasattr(tt, "should_inject_rules")
    assert callable(tt.should_inject_rules)


# ─── Defaults are correct after T3.7 flip ───────────────────────────────────

def test_defaults_json_reflects_t3_7_flip():
    """Sanity that providers/defaults.json was actually edited."""
    caps = load_providers_config()["providers"]["copilot"]["capabilities"]
    assert caps["rules_inject"] is True
