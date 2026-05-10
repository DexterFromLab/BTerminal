"""Unit tests for bterminal.providers.base — T1.1.

Covers:
- ProviderCapabilities defaults (everything False/None)
- ProviderDisplay required fields + frozen
- SessionStats defaults (zero tokens, zero cost)
- AIProvider ABC cannot be instantiated directly
- Minimal concrete subclass works
- Default implementations (fetch_plan_usage, detect_idle, get_dialog_schema)
"""
from __future__ import annotations

import dataclasses

import pytest

from bterminal.providers.base import (
    AIProvider,
    ProviderCapabilities,
    ProviderDisplay,
    SessionStats,
)


# ─── ProviderCapabilities ────────────────────────────────────────────────────

def test_capabilities_defaults_all_disabled():
    caps = ProviderCapabilities()
    # boolean flags default False → safe baseline (provider must opt-in)
    for f in dataclasses.fields(caps):
        if f.type is bool or f.default is False:
            assert getattr(caps, f.name) is False, f"{f.name} should default to False"
    # path templates default None
    assert caps.session_log_path is None
    assert caps.usage_api_url is None
    assert caps.oauth_creds_file is None
    assert caps.context_file is None
    assert caps.ready_marker is None
    assert caps.default_model is None


def test_capabilities_can_set_individual_flags():
    caps = ProviderCapabilities(
        intro_prompt=True,
        session_log=True,
        session_log_path="~/.foo/{session_id}.jsonl",
        stats_bar=True,
    )
    assert caps.intro_prompt is True
    assert caps.session_log is True
    assert caps.session_log_path == "~/.foo/{session_id}.jsonl"
    assert caps.stats_bar is True
    # untouched flags remain False
    assert caps.task_auto_trigger is False


def test_capabilities_frozen():
    caps = ProviderCapabilities(intro_prompt=True)
    with pytest.raises(dataclasses.FrozenInstanceError):
        caps.intro_prompt = False  # type: ignore[misc]


# ─── ProviderDisplay ─────────────────────────────────────────────────────────

def test_display_requires_all_fields():
    with pytest.raises(TypeError):
        ProviderDisplay()  # type: ignore[call-arg]


def test_display_frozen():
    d = ProviderDisplay(icon="✨", short_label="Claude",
                        long_label="Claude Code", color="#89b4fa")
    with pytest.raises(dataclasses.FrozenInstanceError):
        d.icon = "🤖"  # type: ignore[misc]


# ─── SessionStats ────────────────────────────────────────────────────────────

def test_session_stats_defaults_zero():
    s = SessionStats()
    assert s.input_tokens == 0
    assert s.output_tokens == 0
    assert s.cache_creation_tokens == 0
    assert s.cache_read_tokens == 0
    assert s.cost_usd == 0.0
    assert s.response_count == 0
    assert s.model is None


def test_session_stats_mutable():
    s = SessionStats()
    s.input_tokens = 100
    s.cost_usd = 0.05
    assert s.input_tokens == 100
    assert s.cost_usd == 0.05


# ─── AIProvider ABC ──────────────────────────────────────────────────────────

def test_ai_provider_cannot_be_instantiated_directly():
    with pytest.raises(TypeError):
        AIProvider()  # type: ignore[abstract]


class _DummyProvider(AIProvider):
    """Minimal viable subclass for testing default behavior."""

    name = "dummy"
    display = ProviderDisplay(icon="?", short_label="Dummy",
                              long_label="Dummy Provider", color="#888888")
    capabilities = ProviderCapabilities()
    pricing: dict = {}

    def find_binary(self):
        return "/bin/true"

    def build_argv(self, config, intro_prompt):
        return ["/bin/true"]

    def session_log_glob(self, project_dir):
        return None

    def parse_session_stats(self, log_path):
        return SessionStats()


def test_concrete_provider_can_be_instantiated():
    p = _DummyProvider()
    assert p.name == "dummy"
    assert p.display.short_label == "Dummy"
    assert p.find_binary() == "/bin/true"
    assert p.build_argv({}, "") == ["/bin/true"]
    assert p.session_log_glob("/tmp") is None
    assert isinstance(p.parse_session_stats("/dev/null"), SessionStats)


def test_default_fetch_plan_usage_returns_none():
    p = _DummyProvider()
    assert p.fetch_plan_usage() is None


def test_default_detect_idle_returns_true():
    p = _DummyProvider()
    assert p.detect_idle(terminal=None, session_id=None) is True
    assert p.detect_idle(terminal=None, session_id="abc", timeout_s=5.0) is True


def test_default_dialog_schema_empty():
    p = _DummyProvider()
    assert p.get_dialog_schema() == []


# ─── Public API surface ──────────────────────────────────────────────────────

def test_package_exports_core_symbols():
    """`from bterminal.providers import X` works for the 4 core symbols."""
    from bterminal import providers
    assert providers.AIProvider is AIProvider
    assert providers.ProviderCapabilities is ProviderCapabilities
    assert providers.ProviderDisplay is ProviderDisplay
    assert providers.SessionStats is SessionStats
