"""Tests for compute_tab_label() — R7a visual provider marker (T2.7).

GTK rendering of the label widget is verified manually in T2.12 smoke
acceptance; here we focus on the pure logic that decides display text,
tooltip, and color so future regressions (provider swap, color
override) get caught at unit-test speed.
"""
from __future__ import annotations

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.ui.terminal_tab import (
    _DEFAULT_AI_FALLBACK_ICON,
    _LOCAL_TAB_ICON,
    _SSH_TAB_ICON,
    compute_tab_label,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def registry():
    return ProviderRegistry(config=load_providers_config())


# ─── AI tabs ────────────────────────────────────────────────────────────────

def test_claude_tab_uses_provider_icon(registry):
    """Claude provider's ✨ icon, no #N suffix when count=0."""
    out = compute_tab_label(
        ai_config={"provider": "claude"},
        session_name="MyProject",
        count=0,
        registry=registry,
    )
    assert out["display"] == "✨ MyProject"
    assert out["tooltip"] == "Claude Code: MyProject"
    # Color falls back to provider.display.color when session lacks one
    assert out["color"] == "#89b4fa"


def test_copilot_tab_uses_provider_icon(registry):
    out = compute_tab_label(
        ai_config={"provider": "copilot"},
        session_name="MyProject",
        count=0,
        registry=registry,
    )
    assert out["display"] == "🤖 MyProject"
    assert out["tooltip"] == "GitHub Copilot CLI: MyProject"
    assert out["color"] == "#a6e3a1"


def test_count_positive_appends_suffix(registry):
    """Multiple tabs with same name → ` #N` disambiguator."""
    out = compute_tab_label(
        ai_config={"provider": "claude"},
        session_name="MyProject",
        count=2,  # 2 siblings already → this is #3
        registry=registry,
    )
    assert out["display"] == "✨ MyProject #3"


def test_count_zero_no_suffix(registry):
    out = compute_tab_label(
        ai_config={"provider": "claude"},
        session_name="x",
        count=0,
        registry=registry,
    )
    assert out["display"] == "✨ x"


def test_session_color_overrides_provider_color(registry):
    """User-customized session.color wins over provider.display.color."""
    out = compute_tab_label(
        ai_config={"provider": "claude", "color": "#ff00ff"},
        session_name="x",
        count=0,
        registry=registry,
    )
    assert out["color"] == "#ff00ff"


def test_unknown_provider_falls_back_to_generic_icon():
    """Future-version session has provider="aider" but registry has
    only claude/copilot — fall back to generic 🤖, no crash."""
    reg = ProviderRegistry(config=load_providers_config())
    out = compute_tab_label(
        ai_config={"provider": "aider-not-registered"},
        session_name="x",
        count=0,
        registry=reg,
    )
    assert out["display"] == f"{_DEFAULT_AI_FALLBACK_ICON} x"
    assert "AI session" in out["tooltip"]


def test_no_registry_falls_back_to_generic_icon():
    """Defensive: if registry is None (unusual but possible during
    early init), don't crash — render generic icon."""
    out = compute_tab_label(
        ai_config={"provider": "claude"},
        session_name="x",
        count=0,
        registry=None,
    )
    assert out["display"].startswith(_DEFAULT_AI_FALLBACK_ICON)


def test_implicit_claude_provider_when_field_missing(registry):
    """Pre-T1.6 legacy session without `provider` key → defaults to Claude."""
    out = compute_tab_label(
        ai_config={"name": "x"},  # no provider field
        session_name="x",
        count=0,
        registry=registry,
    )
    # Defaults to claude → ✨
    assert out["display"] == "✨ x"


# ─── SSH and local tabs ─────────────────────────────────────────────────────

def test_ssh_tab_uses_lock_icon():
    out = compute_tab_label(
        ai_config=None,
        session_name="prod-server",
        kind="ssh",
    )
    assert out["display"] == f"{_SSH_TAB_ICON} prod-server"
    assert out["tooltip"] == "SSH: prod-server"
    assert out["color"] is None


def test_local_tab_uses_computer_icon():
    out = compute_tab_label(
        ai_config=None,
        session_name="Terminal",
        kind="local",
    )
    assert out["display"] == f"{_LOCAL_TAB_ICON} Terminal"
    assert out["tooltip"] == "Local terminal: Terminal"
    assert out["color"] is None


def test_ssh_count_disambiguator():
    out = compute_tab_label(
        ai_config=None,
        session_name="prod",
        count=1,
        kind="ssh",
    )
    assert out["display"] == f"{_SSH_TAB_ICON} prod #2"


# ─── Returned shape contract ────────────────────────────────────────────────

def test_returned_dict_has_expected_keys(registry):
    out = compute_tab_label(
        ai_config={"provider": "claude"},
        session_name="x",
        count=0,
        registry=registry,
    )
    assert set(out.keys()) == {"display", "tooltip", "color"}


def test_color_is_none_when_neither_session_nor_provider_has_color():
    """Edge case: registry-less + no session color → color stays None."""
    out = compute_tab_label(
        ai_config={"provider": "x"},
        session_name="y",
        count=0,
        registry=None,
    )
    assert out["color"] is None
