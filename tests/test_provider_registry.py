"""Unit tests for ProviderRegistry — T1.5.

Covers: register/get/all/has/names/default_provider, auto-load from
config dict, unknown provider raises, default fallback when configured
default isn't registered, module-level singleton (get_registry +
reset_registry), unknown provider names in config are skipped with a
stderr warning, custom provider class registration via
register_provider_class.
"""
from __future__ import annotations

import json

import pytest

from bterminal.providers import (
    AIProvider,
    ClaudeProvider,
    CopilotProvider,
    ProviderCapabilities,
    ProviderDisplay,
    ProviderRegistry,
    SessionStats,
    get_registry,
    load_providers_config,
    register_provider_class,
    reset_registry,
)


@pytest.fixture
def fresh_registry():
    """Build a registry from current defaults.json (no user override)."""
    return ProviderRegistry(config=load_providers_config())


@pytest.fixture(autouse=True)
def _reset_module_singleton():
    """Each test starts with a clean module-level singleton."""
    reset_registry()
    yield
    reset_registry()


# ─── Auto-load from config ───────────────────────────────────────────────────

def test_registry_loads_claude_and_copilot_from_defaults(fresh_registry):
    assert fresh_registry.has("claude")
    assert fresh_registry.has("copilot")
    assert fresh_registry.has("aider")  # task #3 / #75
    assert isinstance(fresh_registry.get("claude"), ClaudeProvider)
    assert isinstance(fresh_registry.get("copilot"), CopilotProvider)


def test_registry_names_sorted(fresh_registry):
    # Alphabetical: aider before claude before copilot (task #3).
    assert fresh_registry.names() == ["aider", "claude", "copilot"]


def test_registry_all_returns_instances_sorted(fresh_registry):
    instances = fresh_registry.all()
    assert len(instances) == 3  # task #3 added aider
    assert instances[0].name == "aider"
    assert instances[1].name == "claude"
    assert instances[2].name == "copilot"


# ─── get / has / KeyError ────────────────────────────────────────────────────

def test_register_and_get(fresh_registry):
    """register() replaces or adds, get() returns it."""
    class _Custom(ClaudeProvider):
        name = "custom"

    cfg = load_providers_config()["providers"]["claude"]
    fresh_registry.register(_Custom(cfg))
    assert fresh_registry.has("custom")
    assert fresh_registry.get("custom").name == "custom"


def test_unknown_provider_raises_keyerror(fresh_registry):
    # 'aider' is now bundled (#3) — use a future provider name for the
    # "unknown" scenario.
    with pytest.raises(KeyError) as exc:
        fresh_registry.get("future-cli-2030")
    assert "future-cli-2030" in str(exc.value)
    assert "claude" in str(exc.value)  # error message lists registered names


def test_register_replaces_existing(fresh_registry):
    """Registering a provider with same name overwrites prior instance."""
    cfg = load_providers_config()["providers"]["claude"]
    new_claude = ClaudeProvider(cfg)
    fresh_registry.register(new_claude)
    assert fresh_registry.get("claude") is new_claude


# ─── default_provider ────────────────────────────────────────────────────────

def test_default_provider_returns_configured_default(fresh_registry):
    """defaults.json says default_provider = "claude"."""
    p = fresh_registry.default_provider()
    assert p.name == "claude"


def test_default_provider_falls_back_when_configured_missing():
    """User sets default_provider to a future-only name → fall back
    to the first registered alphabetically."""
    config = load_providers_config()
    config["default_provider"] = "future-cli-2030"  # not registered
    reg = ProviderRegistry(config=config)
    p = reg.default_provider()
    # Alphabetical first: aider (after #3) — was 'claude' pre-#3.
    assert p.name == "aider"


def test_default_provider_can_be_overridden_via_config():
    config = load_providers_config()
    config["default_provider"] = "copilot"
    reg = ProviderRegistry(config=config)
    assert reg.default_provider().name == "copilot"


def test_empty_registry_default_raises():
    reg = ProviderRegistry(config={"providers": {}, "default_provider": "x"})
    with pytest.raises(RuntimeError):
        reg.default_provider()


# ─── Unknown provider names in config gracefully skipped ─────────────────────

def test_unknown_provider_name_in_config_is_skipped(capsys):
    """Future-version config has a provider name with no class
    registered → skip + warning, don't crash. (Pre-#3 used 'aider' as
    the example; aider is now bundled, so we use a future name.)"""
    config = load_providers_config()
    config["providers"]["future-cli-2030"] = {
        "display": {"icon": "🚀", "short_label": "FutureCLI",
                    "long_label": "Future CLI", "color": "#000"},
        "capabilities": {},
    }
    reg = ProviderRegistry(config=config)
    assert reg.has("claude")
    assert reg.has("copilot")
    assert reg.has("aider")  # bundled
    assert not reg.has("future-cli-2030")
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "future-cli-2030" in captured.err


def test_provider_instantiation_failure_is_skipped(capsys):
    """Bad config (missing required fields) → skip with warning."""
    config = {
        "default_provider": "claude",
        "providers": {
            "claude": {"display": {}, "capabilities": {}},  # missing fields
        },
    }
    reg = ProviderRegistry(config=config)
    assert not reg.has("claude")
    captured = capsys.readouterr()
    assert "WARN" in captured.err


# ─── register_provider_class ─────────────────────────────────────────────────

class _AiderProvider(AIProvider):
    name = "aider"

    def __init__(self, config):
        self._config = config
        self.display = ProviderDisplay(**config["display"])
        self.capabilities = ProviderCapabilities(**config["capabilities"])
        self.pricing = config.get("pricing", {})

    def find_binary(self):
        return None

    def build_argv(self, config, intro_prompt):
        return []

    def session_log_glob(self, project_dir):
        return None

    def parse_session_stats(self, log_path):
        return SessionStats()


def test_register_provider_class_enables_new_provider():
    """Registering a class lets the registry instantiate it for any
    matching entry in the config dict."""
    # Save the original class so the cleanup restores it (not pop —
    # `aider` is a bundled provider since #75; popping leaves later
    # tests seeing a registry without aider, breaking dispatcher
    # tests in test_context_file_per_provider.py).
    from bterminal.providers import _PROVIDER_CLASSES
    original_aider_class = _PROVIDER_CLASSES.get("aider")

    register_provider_class("aider", _AiderProvider)
    try:
        config = load_providers_config()
        config["providers"]["aider"] = {
            "display": {"icon": "🛠", "short_label": "Aider",
                        "long_label": "Aider", "color": "#f9e2af"},
            "capabilities": {"intro_prompt": True},
        }
        reg = ProviderRegistry(config=config)
        assert reg.has("aider")
        assert isinstance(reg.get("aider"), _AiderProvider)
        assert reg.get("aider").capabilities.intro_prompt is True
    finally:
        # Restore the bundled aider class so dispatcher tests in
        # other files (test_context_file_per_provider, etc.) keep
        # seeing AIDER.md as a registered context file.
        if original_aider_class is not None:
            _PROVIDER_CLASSES["aider"] = original_aider_class
        else:
            _PROVIDER_CLASSES.pop("aider", None)


# ─── Module-level singleton ──────────────────────────────────────────────────

def test_get_registry_returns_same_instance():
    a = get_registry()
    b = get_registry()
    assert a is b


def test_reset_registry_creates_fresh_instance():
    a = get_registry()
    reset_registry()
    b = get_registry()
    assert a is not b


def test_get_registry_loads_defaults():
    """Without USER_OVERRIDE_PATH file, registry loads defaults only."""
    reg = get_registry()
    assert reg.has("claude")
    assert reg.has("copilot")
    assert reg.default_provider().name == "claude"


def test_get_registry_picks_up_user_override(tmp_path, monkeypatch):
    """When USER_OVERRIDE_PATH points at a real file, it's merged."""
    user_file = tmp_path / "providers.json"
    user_file.write_text(json.dumps({"default_provider": "copilot"}))
    monkeypatch.setattr("bterminal.providers.USER_OVERRIDE_PATH", user_file)
    reset_registry()
    reg = get_registry()
    assert reg.default_provider().name == "copilot"
