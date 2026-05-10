"""Tests for ProviderManager — enable/disable per bundled provider
(task #11 / #83).

Coverage:
  - registry.enabled() filters disabled_providers from _OPTIONS
  - registry.is_enabled(name) shows disabled / unknown / enabled
  - registry.default_provider() falls back when configured default
    is disabled
  - _build_provider_combo_items uses enabled() not all()
  - options.json forward-compat (missing key = empty list = all enabled)
  - safety net: 'all disabled' state can be reached at runtime but
    save logic refuses to persist it
"""
from __future__ import annotations

import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bterminal import config
from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.ui.dialogs.ai_session import _build_provider_combo_items


@pytest.fixture(autouse=True)
def _clean_state():
    reset_registry()
    saved = list(config._OPTIONS.get("disabled_providers", []))
    config._OPTIONS["disabled_providers"] = []
    yield
    config._OPTIONS["disabled_providers"] = saved
    reset_registry()


# ─── registry.enabled() / is_enabled() ─────────────────────────────────────


def test_enabled_defaults_returns_all_when_no_disabled_list():
    """Empty disabled_providers (default state) → enabled() ==
    all(). Forward-compat for upgraded options.json."""
    config._OPTIONS["disabled_providers"] = []
    reg = ProviderRegistry(config=load_providers_config())
    assert [p.name for p in reg.enabled()] == [p.name for p in reg.all()]


def test_enabled_filters_disabled_provider(monkeypatch):
    """User hid copilot in OptionsDialog → enabled() omits it."""
    monkeypatch.setitem(config._OPTIONS, "disabled_providers", ["copilot"])
    reg = ProviderRegistry(config=load_providers_config())
    enabled_names = [p.name for p in reg.enabled()]
    assert "copilot" not in enabled_names
    assert "claude" in enabled_names
    assert "aider" in enabled_names


def test_enabled_can_filter_multiple(monkeypatch):
    monkeypatch.setitem(config._OPTIONS, "disabled_providers",
                         ["copilot", "aider"])
    reg = ProviderRegistry(config=load_providers_config())
    assert [p.name for p in reg.enabled()] == ["claude"]


def test_enabled_returns_empty_when_all_disabled(monkeypatch):
    """Pathological state — all 3 disabled. enabled() returns []
    (caller surfaces error / saves logic blocks the write)."""
    monkeypatch.setitem(config._OPTIONS, "disabled_providers",
                         ["claude", "copilot", "aider"])
    reg = ProviderRegistry(config=load_providers_config())
    assert reg.enabled() == []


def test_is_enabled_returns_true_for_registered_and_enabled():
    reg = ProviderRegistry(config=load_providers_config())
    assert reg.is_enabled("claude") is True
    assert reg.is_enabled("aider") is True


def test_is_enabled_returns_false_for_disabled_provider(monkeypatch):
    monkeypatch.setitem(config._OPTIONS, "disabled_providers", ["copilot"])
    reg = ProviderRegistry(config=load_providers_config())
    assert reg.is_enabled("copilot") is False


def test_is_enabled_returns_false_for_unknown_name():
    """Forward-compat: 'aider3000' from a future config doesn't
    register a class → is_enabled returns False (not crash)."""
    reg = ProviderRegistry(config=load_providers_config())
    assert reg.is_enabled("future-cli-2030") is False


def test_all_does_not_filter_disabled(monkeypatch):
    """all() must remain LEGACY behaviour — returns every registered
    provider regardless of disabled list. Sidebar / tab labels rely
    on this so existing sessions for a now-hidden provider still
    render their icon + display."""
    monkeypatch.setitem(config._OPTIONS, "disabled_providers",
                         ["copilot", "aider"])
    reg = ProviderRegistry(config=load_providers_config())
    assert [p.name for p in reg.all()] == ["aider", "claude", "copilot"]


# ─── default_provider() fallback chain ─────────────────────────────────────


def test_default_provider_returns_configured_when_enabled():
    """Happy path: default_provider in config + enabled → returns it."""
    reg = ProviderRegistry(config=load_providers_config())
    assert reg.default_provider().name == "claude"  # bundled default


def test_default_provider_falls_back_when_default_disabled(monkeypatch):
    """User disabled the configured default → fall back to first
    alphabetically enabled provider."""
    monkeypatch.setitem(config._OPTIONS, "disabled_providers", ["claude"])
    reg = ProviderRegistry(config=load_providers_config())
    # alphabetically: aider < copilot — aider wins
    assert reg.default_provider().name == "aider"


def test_default_provider_falls_through_first_two_disabled(monkeypatch):
    """Default + alphabetic-first both disabled → next enabled
    in alphabetical order."""
    monkeypatch.setitem(config._OPTIONS, "disabled_providers",
                         ["claude", "aider"])
    reg = ProviderRegistry(config=load_providers_config())
    assert reg.default_provider().name == "copilot"


def test_default_provider_legacy_fallback_when_all_disabled(monkeypatch):
    """All disabled → fall back to first registered (legacy path) so
    the caller doesn't crash. UI surfaces 'no providers enabled'
    error dialog separately."""
    monkeypatch.setitem(config._OPTIONS, "disabled_providers",
                         ["claude", "copilot", "aider"])
    reg = ProviderRegistry(config=load_providers_config())
    # all() returns everything sorted; default falls back to first one
    assert reg.default_provider().name == "aider"


def test_default_provider_raises_when_no_providers_registered():
    """Empty config (forward-compat: future BT version disables all
    its bundled providers) → RuntimeError. Keeps legacy contract."""
    reg = ProviderRegistry(config={
        "providers": {}, "default_provider": "claude",
    })
    with pytest.raises(RuntimeError):
        reg.default_provider()


# ─── AISessionDialog combo filtering ───────────────────────────────────────


def test_combo_items_use_enabled_filter(monkeypatch):
    """Disabled providers vanish from the Add AI Session dropdown."""
    monkeypatch.setitem(config._OPTIONS, "disabled_providers", ["copilot"])
    reg = ProviderRegistry(config=load_providers_config())
    items = _build_provider_combo_items(reg)
    names = [n for n, _l in items]
    assert "copilot" not in names
    assert "aider" in names
    assert "claude" in names


def test_combo_items_returns_empty_when_all_disabled(monkeypatch):
    """All providers hidden → empty dropdown. Dialog code is
    expected to handle this (warning label, can't save)."""
    monkeypatch.setitem(config._OPTIONS, "disabled_providers",
                         ["claude", "copilot", "aider"])
    reg = ProviderRegistry(config=load_providers_config())
    assert _build_provider_combo_items(reg) == []


def test_combo_items_falls_back_to_all_for_stub_registry():
    """Forward-compat: tests / external callers may pass a registry
    stub without enabled() — combo helper falls back to all()."""
    class _Stub:
        def all(self):
            return []
    assert _build_provider_combo_items(_Stub()) == []


# ─── options.json roundtrip + forward-compat ───────────────────────────────


def test_disabled_providers_default_is_empty_list():
    """Default _OPTIONS schema has disabled_providers=[] so fresh
    installs have all 3 providers enabled out of the box."""
    assert config._OPTIONS_DEFAULTS["disabled_providers"] == []


def test_options_json_roundtrip_with_disabled_providers(tmp_path, monkeypatch):
    """Save with disabled list → reload → list preserved."""
    f = tmp_path / "options.json"
    monkeypatch.setattr(config, "OPTIONS_FILE", str(f))
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))

    payload = {
        **config._OPTIONS_DEFAULTS,
        "disabled_providers": ["copilot"],
    }
    config._save_options(payload)
    loaded = config._load_options()
    assert loaded["disabled_providers"] == ["copilot"]


def test_legacy_options_json_without_key_falls_back_to_default(
    tmp_path, monkeypatch,
):
    """Legacy options.json (pre-#11) lacks disabled_providers →
    _load_options merges default ([])."""
    f = tmp_path / "options.json"
    monkeypatch.setattr(config, "OPTIONS_FILE", str(f))
    monkeypatch.setattr(config, "CONFIG_DIR", str(tmp_path))
    legacy = {k: v for k, v in config._OPTIONS_DEFAULTS.items()
              if k != "disabled_providers"}
    f.write_text(json.dumps(legacy))
    loaded = config._load_options()
    assert loaded["disabled_providers"] == []


# ─── Source-level wiring asserts ───────────────────────────────────────────


def test_options_dialog_save_blocks_all_disabled():
    """Source-level: run_and_apply must contain the safety check
    (set(disabled_now) < all_names) so the user can't save a state
    that hides every provider — dropdown would be empty."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs"
           / "options.py").read_text()
    assert "set(disabled_now) < all_names" in src
    assert "disabled_providers" in src


def test_options_dialog_has_providers_expander():
    """OptionsDialog __init__ creates the AI Providers expander +
    lazy builder."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs"
           / "options.py").read_text()
    assert '_providers_expander = Gtk.Expander(label=_("AI Providers"))' in src
    assert "_lazy_build_providers" in src
