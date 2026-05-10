"""Tests for TerminalTab.ai_config + claude_config backward-compat — T1.8.

The constructor accepts both `ai_config=` (canonical) and `claude_config=`
(deprecated alias). The .claude_config property returns ai_config only
when provider == "claude", else None — so legacy code paths that gate
on `if tab.claude_config:` silently skip Copilot tabs without crashing.

Tests skip GTK construction by exercising the property/setter logic on
a minimal stand-in (the only attribute touched is ai_config).
"""
from __future__ import annotations

import pytest

from bterminal.ui.terminal_tab import TerminalTab


class _Stub:
    """Stand-in that gets the same property mechanics as TerminalTab.

    We can't easily instantiate TerminalTab in unit tests (it spawns a
    Vte.Terminal under GTK). Instead we copy the property descriptor
    onto a stub class and exercise the get/set logic directly — that's
    what actually carries the alias semantics.
    """
    claude_config = TerminalTab.claude_config


def test_ai_config_assignment_visible_through_legacy_alias():
    s = _Stub()
    s.ai_config = {"name": "x", "provider": "claude"}
    assert s.claude_config == {"name": "x", "provider": "claude"}


def test_legacy_alias_returns_none_for_copilot_provider():
    """Pre-T2 code paths that gate on claude_config skip Copilot tabs."""
    s = _Stub()
    s.ai_config = {"name": "y", "provider": "copilot"}
    assert s.claude_config is None
    # but ai_config is preserved
    assert s.ai_config == {"name": "y", "provider": "copilot"}


def test_legacy_alias_returns_config_for_implicit_claude():
    """Legacy session entries without `provider` field → assume claude."""
    s = _Stub()
    s.ai_config = {"name": "z"}  # no provider field
    assert s.claude_config == {"name": "z"}


def test_legacy_alias_handles_none_ai_config():
    s = _Stub()
    s.ai_config = None
    assert s.claude_config is None


def test_legacy_setter_writes_to_ai_config():
    """Plugins or tests setting tab.claude_config = X land on ai_config."""
    s = _Stub()
    s.claude_config = {"name": "via-setter", "provider": "claude"}
    assert s.ai_config == {"name": "via-setter", "provider": "claude"}


def test_constructor_signature_accepts_both_kwargs():
    """Sanity: TerminalTab.__init__ keyword-only signature has both
    `ai_config` and `claude_config` so the call sites can migrate
    incrementally without breaking."""
    import inspect
    sig = inspect.signature(TerminalTab.__init__)
    assert "ai_config" in sig.parameters
    assert "claude_config" in sig.parameters
