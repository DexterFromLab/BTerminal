"""Tests for SessionStatsBar tokens-only mode (T3.9).

The widget now accepts `hide_plan_usage` to suppress the 5h/7d
gauge labels — Copilot's path since the provider has no public
plan-usage API. Pure helpers `_hidden_label_keys_for_options` and
`stats_widget_options_for_ai_config` cover the dispatch logic
without needing a GTK display.
"""
from __future__ import annotations

from unittest.mock import MagicMock

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.ui.stats import (
    SessionStatsBar,
    stats_widget_options_for_ai_config,
)
from bterminal.ui.stats.widget import _hidden_label_keys_for_options


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def registry():
    return ProviderRegistry(config=load_providers_config())


# ─── _hidden_label_keys_for_options pure helper ─────────────────────────────

def test_default_no_keys_hidden():
    assert _hidden_label_keys_for_options(False) == set()


def test_hide_plan_usage_hides_5h_7d_labels():
    keys = _hidden_label_keys_for_options(True)
    assert keys == {"s8", "usage_5h", "s9", "usage_7d"}


def test_hidden_keys_do_not_include_tokens_or_cost():
    """Tokens-only mode keeps {tokens | cost} visible — sanity."""
    keys = _hidden_label_keys_for_options(True)
    for must_show in ("tok_in", "tok_out", "cost", "model", "prompts",
                      "resp", "cache", "tok_h", "dur"):
        assert must_show not in keys


# ─── stats_widget_options_for_ai_config dispatch ───────────────────────────

def test_claude_session_no_hide(registry):
    """Claude has stats_bar_no_plan_usage=False → plan gauge stays.
    cost_in_log=True so cost label renders normally (#94 added the
    `cost_unavailable` companion key)."""
    opts = stats_widget_options_for_ai_config(
        {"provider": "claude", "project_dir": "/tmp/x"}, registry,
    )
    assert opts == {"hide_plan_usage": False, "cost_unavailable": False}


def test_copilot_session_hides_plan_usage(registry):
    """T3.9 acceptance: Copilot's stats_bar_no_plan_usage=True propagates.
    Copilot has cost_in_log=True → `cost_unavailable=False`."""
    opts = stats_widget_options_for_ai_config(
        {"provider": "copilot", "project_dir": "/tmp/x"}, registry,
    )
    assert opts == {"hide_plan_usage": True, "cost_unavailable": False}


def test_implicit_claude_no_hide(registry):
    """Pre-T1.6 sessions without `provider` field default to Claude."""
    opts = stats_widget_options_for_ai_config(
        {"project_dir": "/tmp/x"}, registry,
    )
    assert opts == {"hide_plan_usage": False, "cost_unavailable": False}


def test_no_ai_config_returns_empty_opts(registry):
    assert stats_widget_options_for_ai_config(None, registry) == {}
    assert stats_widget_options_for_ai_config({}, registry) == {}


def test_unknown_provider_returns_empty_opts(registry):
    """Future-version session names a provider this build doesn't know
    → no widget options (caller falls back to defaults)."""
    opts = stats_widget_options_for_ai_config(
        {"provider": "totally-fake", "project_dir": "/tmp/x"}, registry,
    )
    assert opts == {}


def test_capability_override_disables_hide():
    """If user flips off stats_bar_no_plan_usage in providers.json
    override, Copilot widget shows the plan gauge (placeholder dashes
    if reader returns None)."""
    cfg = load_providers_config()
    cfg["providers"]["copilot"]["capabilities"]["stats_bar_no_plan_usage"] = False
    reg = ProviderRegistry(config=cfg)
    opts = stats_widget_options_for_ai_config(
        {"provider": "copilot", "project_dir": "/tmp/x"}, reg,
    )
    assert opts == {"hide_plan_usage": False, "cost_unavailable": False}


# ─── SessionStatsBar respects hide_plan_usage in ctor ───────────────────────

def test_widget_default_does_not_hide_plan_usage(monkeypatch):
    """Without the flag, the constructor sets _hide_plan_usage=False —
    plan-usage labels get shown by GTK."""
    captured = {}

    def _spy(self, project_dir, reader=None, hide_plan_usage=False):
        captured["hide"] = hide_plan_usage

    monkeypatch.setattr(SessionStatsBar, "__init__", _spy)
    SessionStatsBar("/tmp/x")
    assert captured["hide"] is False


def test_widget_accepts_hide_plan_usage_flag(monkeypatch):
    captured = {}

    def _spy(self, project_dir, reader=None, hide_plan_usage=False):
        captured["hide"] = hide_plan_usage

    monkeypatch.setattr(SessionStatsBar, "__init__", _spy)
    SessionStatsBar("/tmp/x", hide_plan_usage=True)
    assert captured["hide"] is True


# ─── Update loop short-circuits plan_usage block when hidden ────────────────

def test_update_skips_plan_usage_when_hidden():
    """Run a synthetic _update with _hide_plan_usage=True — the reader's
    read_plan_usage MUST NOT be called (proves the early-return)."""
    from bterminal.ui.stats.widget import SessionStatsBar
    from bterminal.ui.stats.base import TokenStats

    fake_reader = MagicMock()
    fake_reader.read_session_tokens.return_value = TokenStats(
        input=100, output=50, model="claude-sonnet-4-5",
    )
    fake_reader.read_session_cost.return_value = 0.001
    fake_reader.read_plan_usage = MagicMock(return_value=None)

    # Stand-in object with the attributes _update reads — bypasses GTK ctor.
    fake_self = MagicMock()
    fake_self._reader = fake_reader
    fake_self._prompt_count = 0
    fake_self._hide_plan_usage = True
    fake_self._labels = {
        k: MagicMock() for k in (
            "dur", "prompts", "resp", "tok_in", "tok_out", "cache",
            "cost", "tok_h", "model", "usage_5h", "usage_7d",
        )
    }

    # Bound-method-style call
    SessionStatsBar._update(fake_self)
    fake_reader.read_session_tokens.assert_called_once()
    fake_reader.read_session_cost.assert_called_once()
    fake_reader.read_plan_usage.assert_not_called()


def test_update_calls_plan_usage_when_visible():
    """Without hide flag, plan_usage is fetched."""
    from bterminal.ui.stats.widget import SessionStatsBar
    from bterminal.ui.stats.base import TokenStats

    fake_reader = MagicMock()
    fake_reader.read_session_tokens.return_value = TokenStats()
    fake_reader.read_session_cost.return_value = 0.0
    fake_reader.read_plan_usage = MagicMock(return_value=None)

    fake_self = MagicMock()
    fake_self._reader = fake_reader
    fake_self._prompt_count = 0
    fake_self._hide_plan_usage = False
    fake_self._labels = {
        k: MagicMock() for k in (
            "dur", "prompts", "resp", "tok_in", "tok_out", "cache",
            "cost", "tok_h", "model", "usage_5h", "usage_7d",
        )
    }

    SessionStatsBar._update(fake_self)
    fake_reader.read_plan_usage.assert_called_once()


# ─── Public API surface ─────────────────────────────────────────────────────

def test_stats_widget_options_in_package_all():
    from bterminal.ui import stats
    assert "stats_widget_options_for_ai_config" in stats.__all__


def test_helper_exported_from_widget_module():
    from bterminal.ui.stats import widget
    assert hasattr(widget, "_hidden_label_keys_for_options")
