"""Provider-aware intro prompt header — T1.9.

Verifies:
- _build_intro_prompt(project_name) keeps the legacy "Claude" header
  when provider_label is not passed (backward-compat).
- _build_intro_prompt(project_name, provider_label=X) uses X verbatim.
- _compute_intro_prompt_for_tab pulls provider.display.long_label from
  the registry based on tab.ai_config["provider"], falling back to
  "Claude" when the provider isn't registered.

Tests don't construct GTK widgets — they exercise the pure functions
directly with monkeypatched ctx/tools/rules helpers so the assertion
focuses on the header sentence only.
"""
from __future__ import annotations

import types

import pytest

from bterminal.providers import reset_registry
from bterminal.ui.dialogs import claude_code as cc_module


@pytest.fixture(autouse=True)
def _silence_intro_prompt_helpers(monkeypatch):
    """Stub the four data sources so _build_intro_prompt produces a
    predictable string focused on the header sentence under test."""
    monkeypatch.setattr(cc_module, "_fetch_ctx_output", lambda p: "")
    monkeypatch.setattr(cc_module, "_tools_help", lambda p: "TOOLS_PLACEHOLDER")
    monkeypatch.setattr(cc_module, "_fetch_rules_block", lambda p: "")
    monkeypatch.setattr(cc_module, "_read_global_rules", lambda: [])
    # Disable language hint so it doesn't appear in assertions
    monkeypatch.setattr(cc_module, "_OPTIONS",
                        {"tell_ai_language": False}, raising=False)
    yield


@pytest.fixture(autouse=True)
def _reset_registry_singleton():
    reset_registry()
    yield
    reset_registry()


# ─── _build_intro_prompt direct ──────────────────────────────────────────────

def test_build_intro_prompt_default_label_keeps_legacy_wording():
    """No provider_label arg → header still says 'SSH/Claude terminal'
    (backward-compat for any pre-T1.9 caller)."""
    prompt = cc_module._build_intro_prompt("myproject")
    assert "SSH/Claude terminal" in prompt


def test_build_intro_prompt_uses_explicit_provider_label():
    prompt = cc_module._build_intro_prompt(
        "myproject", provider_label="GitHub Copilot CLI")
    assert "SSH/GitHub Copilot CLI terminal" in prompt
    assert "SSH/Claude terminal" not in prompt


def test_build_intro_prompt_uses_claude_code_label_for_claude_provider():
    prompt = cc_module._build_intro_prompt(
        "myproject", provider_label="Claude Code")
    assert "SSH/Claude Code terminal" in prompt


# ─── _compute_intro_prompt_for_tab integration ──────────────────────────────

def _stub_app():
    """Minimal stand-in for BTerminalApp — the intro builder only
    touches `_plugins` and `sidecar_manifests`."""
    app = types.SimpleNamespace()
    app._plugins = {}
    app.sidecar_manifests = {}
    return app


def _stub_tab(provider, project_dir="/tmp/x"):
    """Stand-in for TerminalTab — only ai_config + enabled_plugins read."""
    tab = types.SimpleNamespace()
    tab.ai_config = {"provider": provider, "project_dir": project_dir,
                     "prompt": ""}
    tab.enabled_plugins = None
    return tab


def test_compute_intro_uses_long_label_for_claude(monkeypatch, tmp_path):
    """Claude tab → header should use long_label "Claude Code"."""
    monkeypatch.setattr(
        "bterminal.ctx.helpers._resolve_ctx_project_name",
        lambda d: "myproject",
    )
    from bterminal.helpers import _compute_intro_prompt_for_tab
    prompt = _compute_intro_prompt_for_tab(_stub_app(), _stub_tab("claude"))
    assert "SSH/Claude Code terminal" in prompt


def test_compute_intro_uses_long_label_for_copilot(monkeypatch):
    """Copilot tab → header should use long_label "GitHub Copilot CLI"."""
    monkeypatch.setattr(
        "bterminal.ctx.helpers._resolve_ctx_project_name",
        lambda d: "myproject",
    )
    from bterminal.helpers import _compute_intro_prompt_for_tab
    prompt = _compute_intro_prompt_for_tab(_stub_app(), _stub_tab("copilot"))
    assert "SSH/GitHub Copilot CLI terminal" in prompt
    assert "SSH/Claude" not in prompt


def test_compute_intro_unknown_provider_falls_back_to_claude(monkeypatch):
    """tab with an unknown future provider name → header falls back to
    'Claude' (graceful, no crash). Pre-#3 used 'aider' as the unknown
    name; aider is now bundled, so this scenario uses a future name."""
    monkeypatch.setattr(
        "bterminal.ctx.helpers._resolve_ctx_project_name",
        lambda d: "myproject",
    )
    from bterminal.helpers import _compute_intro_prompt_for_tab
    prompt = _compute_intro_prompt_for_tab(
        _stub_app(), _stub_tab("future-cli-2030"))
    assert "SSH/Claude terminal" in prompt


def test_compute_intro_no_ai_config_returns_just_custom_prompt(monkeypatch):
    """Tab without ai_config (e.g. SSH or local shell tab) → no header,
    only custom_prompt is returned (which is empty here)."""
    from bterminal.helpers import _compute_intro_prompt_for_tab
    tab = types.SimpleNamespace()
    tab.ai_config = None
    tab.enabled_plugins = None
    prompt = _compute_intro_prompt_for_tab(_stub_app(), tab)
    assert prompt == ""


def test_compute_intro_implicit_claude_when_provider_field_missing(monkeypatch):
    """Pre-T1.6 session entries without `provider` field → assume claude."""
    monkeypatch.setattr(
        "bterminal.ctx.helpers._resolve_ctx_project_name",
        lambda d: "myproject",
    )
    from bterminal.helpers import _compute_intro_prompt_for_tab
    tab = types.SimpleNamespace()
    tab.ai_config = {"project_dir": "/tmp/x", "prompt": ""}
    tab.enabled_plugins = None
    prompt = _compute_intro_prompt_for_tab(_stub_app(), tab)
    assert "SSH/Claude Code terminal" in prompt
