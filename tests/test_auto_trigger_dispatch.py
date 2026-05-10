"""Capability dispatch for auto-trigger — T3.6.

The pure helper `should_run_auto_trigger(ai_config, registry)` decides
whether `_on_task_idle_timeout` proceeds with the task-claim flow.
GTK isn't required for these tests — the helper is provider-aware
config logic only.

Acceptance:
  Claude provider → True (task_auto_trigger=True).
  Copilot at T3 baseline → False (T4.1 will flip to True via
    events.jsonl idle detection).
  No ai_config / unknown provider / SSH-or-local tab → False.
"""
from __future__ import annotations

import json

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.ui.terminal_tab import should_run_auto_trigger


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def registry():
    return ProviderRegistry(config=load_providers_config())


# ─── Per-provider behavior ──────────────────────────────────────────────────

def test_claude_fires(registry):
    """Claude has task_auto_trigger=True (the legacy auto-trigger path)."""
    ai_config = {"provider": "claude", "project_dir": "/tmp/proj"}
    assert should_run_auto_trigger(ai_config, registry) is True


def test_copilot_fires_after_t4_2(registry):
    """T4.2: events.jsonl idle monitor lands (T4.1) and the capability
    flips True — Copilot tabs now fire the auto-trigger dispatch."""
    ai_config = {"provider": "copilot", "project_dir": "/tmp/proj"}
    assert should_run_auto_trigger(ai_config, registry) is True


def test_implicit_claude_when_provider_field_missing(registry):
    """Pre-T1.6 sessions without `provider` key default to Claude."""
    assert should_run_auto_trigger({"project_dir": "/tmp"}, registry) is True


# ─── Tab-type gating ────────────────────────────────────────────────────────

def test_no_ai_config_returns_false(registry):
    """SSH / local tabs (ai_config=None) never auto-trigger."""
    assert should_run_auto_trigger(None, registry) is False


def test_empty_ai_config_returns_false(registry):
    """Empty dict counts the same as None — no AI session, no trigger."""
    assert should_run_auto_trigger({}, registry) is False


# ─── Unknown / future-version providers ─────────────────────────────────────

def test_unknown_provider_returns_false(registry):
    """Future-version session names a provider this build doesn't know."""
    ai_config = {"provider": "totally-fake", "project_dir": "/tmp/proj"}
    assert should_run_auto_trigger(ai_config, registry) is False


def test_none_registry_returns_false():
    """Defensive: None registry (early init / mock) → no trigger."""
    ai_config = {"provider": "claude", "project_dir": "/tmp"}
    assert should_run_auto_trigger(ai_config, None) is False


# ─── Capability override (per-config / future-version configs) ──────────────

def test_capability_override_disables_claude_trigger():
    """User can disable Claude's auto-trigger via providers.json override."""
    cfg = load_providers_config()
    cfg["providers"]["claude"]["capabilities"]["task_auto_trigger"] = False
    reg = ProviderRegistry(config=cfg)
    ai_config = {"provider": "claude", "project_dir": "/tmp"}
    assert should_run_auto_trigger(ai_config, reg) is False


def test_capability_override_disables_copilot_trigger():
    """T4.2 baseline has Copilot's task_auto_trigger=True — the user
    can opt out via providers.json override and the dispatch respects it."""
    cfg = load_providers_config()
    cfg["providers"]["copilot"]["capabilities"]["task_auto_trigger"] = False
    reg = ProviderRegistry(config=cfg)
    ai_config = {"provider": "copilot", "project_dir": "/tmp"}
    assert should_run_auto_trigger(ai_config, reg) is False


# ─── Integration: T4.1 monitor + T3.6 dispatch (T4.2 acceptance) ────────────


def test_copilot_fires_after_idle_with_monitor(tmp_path, registry):
    """T4.2 acceptance: events.jsonl idle monitor (T4.1) signals idle
    via callback; dispatch helper (T3.6) confirms the Copilot tab is
    eligible. End-to-end this is the path that triggers auto-trigger
    for Copilot tabs in production after T4.2."""
    import json
    import time
    from datetime import datetime, timezone

    from bterminal.providers.copilot import (
        CopilotProvider, _CopilotIdleMonitor,
    )

    # 1. Capability dispatch says Copilot is eligible (T3.6 + T4.2 flip).
    ai_config = {"provider": "copilot", "project_dir": "/tmp/proj"}
    assert should_run_auto_trigger(ai_config, registry) is True

    # 2. Idle monitor over a synthetic events.jsonl signals idle.
    events_path = tmp_path / "events.jsonl"
    ts = "2026-05-06T10:00:00Z"
    events_path.write_text(json.dumps({
        "type": "tool.execution_complete",
        "timestamp": ts,
        "data": {"toolName": "shell",
                 "usage": {"inputTokens": 50, "outputTokens": 20}},
    }) + "\n")

    fired = []
    monitor = _CopilotIdleMonitor(
        events_path=str(events_path),
        on_idle_callback=lambda state: fired.append(state),
        timeout_s=10.0,
        clock=lambda: datetime.fromisoformat(
            ts.replace("Z", "+00:00"),
        ).timestamp() + 15.0,
    )
    monitor.poll_once()
    assert len(fired) == 1
    assert fired[0]["idle"] is True
    assert fired[0]["reason"] == "quiet_after_complete"


def test_copilot_monitor_does_not_fire_when_dispatch_disabled(tmp_path):
    """If the user disables task_auto_trigger via override, the monitor
    can still detect idle but the dispatch gate (T3.6) blocks the
    actual trigger — the production wiring must check both."""
    cfg = load_providers_config()
    cfg["providers"]["copilot"]["capabilities"]["task_auto_trigger"] = False
    reg = ProviderRegistry(config=cfg)

    ai_config = {"provider": "copilot", "project_dir": "/tmp"}
    # Dispatch says NO regardless of monitor's signals
    assert should_run_auto_trigger(ai_config, reg) is False


# ─── Integration: pure-helper is module-exported ───────────────────────────

def test_helper_exported_from_terminal_tab_module():
    from bterminal.ui import terminal_tab as tt
    assert hasattr(tt, "should_run_auto_trigger")
    assert callable(tt.should_run_auto_trigger)
