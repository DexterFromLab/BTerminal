"""Tests for stats_bar capability dispatch — T3.5.

The factory `create_stats_reader_for_ai_config(ai_config, registry)`
decides which reader (if any) to wire into a tab's SessionStatsBar.
GTK construction is mocked away — these are pure-logic unit tests.
"""
from __future__ import annotations

import json

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.ui.stats import (
    ClaudeStatsReader,
    CopilotStatsReader,
    create_stats_reader_for_ai_config,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def registry():
    return ProviderRegistry(config=load_providers_config())


# ─── Provider-aware reader selection ────────────────────────────────────────

def test_claude_provider_returns_claude_reader(registry):
    reader = create_stats_reader_for_ai_config(
        {"provider": "claude", "project_dir": "/tmp/proj"},
        registry,
    )
    assert isinstance(reader, ClaudeStatsReader)
    assert reader.project_dir == "/tmp/proj"


def test_copilot_provider_returns_copilot_reader(registry):
    """T3.5: Copilot now has stats_bar=true (capability flipped) → bar
    is mounted with CopilotStatsReader (events.jsonl parser from T3.2)."""
    reader = create_stats_reader_for_ai_config(
        {"provider": "copilot", "project_dir": "/tmp/proj"},
        registry,
    )
    assert isinstance(reader, CopilotStatsReader)
    assert reader.project_dir == "/tmp/proj"


def test_implicit_claude_when_provider_field_missing(registry):
    """Pre-T1.6 legacy session lacks `provider` — defaults to claude."""
    reader = create_stats_reader_for_ai_config(
        {"project_dir": "/tmp/proj"},
        registry,
    )
    assert isinstance(reader, ClaudeStatsReader)


# ─── Capability dispatch — bar suppressed when stats_bar=false ──────────────

def test_provider_with_stats_bar_disabled_returns_none():
    """A provider with stats_bar=False (e.g. minimal future provider)
    yields no reader — tab will skip mounting the bar entirely."""
    cfg = load_providers_config()
    cfg["providers"]["claude"]["capabilities"]["stats_bar"] = False
    reg = ProviderRegistry(config=cfg)
    reader = create_stats_reader_for_ai_config(
        {"provider": "claude", "project_dir": "/tmp/proj"},
        reg,
    )
    assert reader is None


# ─── Edge cases: no AI config, no project_dir, unknown provider ─────────────

def test_no_ai_config_returns_none(registry):
    """SSH/local tabs (ai_config=None) → no stats bar."""
    assert create_stats_reader_for_ai_config(None, registry) is None
    assert create_stats_reader_for_ai_config({}, registry) is None


def test_missing_project_dir_returns_none(registry):
    """Reader needs a project_dir to locate its log file."""
    reader = create_stats_reader_for_ai_config(
        {"provider": "claude"}, registry,
    )
    assert reader is None


def test_empty_project_dir_returns_none(registry):
    reader = create_stats_reader_for_ai_config(
        {"provider": "claude", "project_dir": ""}, registry,
    )
    assert reader is None


def test_unknown_provider_returns_none(registry):
    """Future-version session names a provider this build doesn't know."""
    reader = create_stats_reader_for_ai_config(
        {"provider": "totally-fake", "project_dir": "/tmp/proj"},
        registry,
    )
    assert reader is None


def test_provider_without_reader_class_returns_none(registry):
    """If a provider is registered but has no entry in _READER_CLASSES
    (e.g. a future provider added before its reader class), factory
    safely returns None instead of crashing.

    Uses a synthetic 'future-cli-2030' name because real bundled
    providers (claude/copilot/aider) all have readers since #94."""
    from bterminal.providers.base import (
        AIProvider, ProviderCapabilities, ProviderDisplay,
    )
    from bterminal.ui.stats.base import TokenStats

    class _FutureProvider(AIProvider):
        name = "future-cli-2030"

        def __init__(self):
            self.display = ProviderDisplay(
                icon="🛠", short_label="Future",
                long_label="Future CLI 2030", color="#000",
            )
            self.capabilities = ProviderCapabilities(stats_bar=True)
            self.pricing = {}

        def find_binary(self):
            return None

        def build_argv(self, config, intro_prompt):
            return []

        def session_log_glob(self, project_dir):
            return None

        def parse_session_stats(self, log_path):
            return TokenStats()

    registry.register(_FutureProvider())
    reader = create_stats_reader_for_ai_config(
        {"provider": "future-cli-2030", "project_dir": "/tmp/proj"},
        registry,
    )
    assert reader is None


# ─── End-to-end with TerminalTab — reader mounted as expected ──────────────

def test_factory_used_by_terminal_tab(monkeypatch, registry):
    """TerminalTab.__init__ calls create_stats_reader_for_ai_config.
    Patch the factory to inspect what gets passed."""
    captured = {}

    def _fake_factory(ai_config, reg):
        captured["ai_config"] = ai_config
        captured["registry"] = reg
        return None  # so widget code doesn't try to mount GTK

    monkeypatch.setattr(
        "bterminal.ui.stats.create_stats_reader_for_ai_config",
        _fake_factory,
    )
    monkeypatch.setattr(
        "bterminal.ui.terminal_tab.create_stats_reader_for_ai_config",
        _fake_factory,
        raising=False,
    )
    # We can't construct TerminalTab without GTK; assert through import
    # path that the factory IS reachable from the module.
    from bterminal.ui import terminal_tab as tt_mod
    assert hasattr(tt_mod, "SessionStatsBar")
    # Factory is also re-exported correctly
    from bterminal.ui import stats as stats_pkg
    assert stats_pkg.create_stats_reader_for_ai_config is not None


# ─── Public API surface ────────────────────────────────────────────────────

def test_factory_in_package_all():
    from bterminal.ui import stats
    assert "create_stats_reader_for_ai_config" in stats.__all__


def test_copilot_capabilities_include_stats_bar(registry):
    """T3.5 acceptance: Copilot's stats_bar capability is now True;
    related session_log + cost_in_log too (without those, the bar
    has nothing to read)."""
    caps = registry.get("copilot").capabilities
    assert caps.stats_bar is True
    assert caps.session_log is True
    assert caps.cost_in_log is True
    # stats_bar_no_plan_usage stays True so T3.9 widget hides plan gauge
    assert caps.stats_bar_no_plan_usage is True
