"""Tests for Copilot plan mode toggle — T4.4.

Two layers:
  - argv builder: `plan_mode=True` adds `--plan` to the argv when the
    capability is enabled; otherwise the flag is suppressed.
  - dialog schema: `("plan_mode", "checkbox", ...)` entry appears
    only when capability is True.
"""
from __future__ import annotations

import json
import stat

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.providers.copilot import CopilotProvider


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry()
    yield
    reset_registry()


def _make_executable(path):
    path.write_text("#!/bin/sh\necho fake\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


@pytest.fixture
def provider_with_fake_binary(tmp_path):
    fake = tmp_path / "copilot"
    _make_executable(fake)
    cfg = json.loads(json.dumps(load_providers_config()["providers"]["copilot"]))
    cfg["binary"]["search_paths"] = [str(fake)]
    return CopilotProvider(cfg), str(fake)


# ─── argv builder ───────────────────────────────────────────────────────────

def test_argv_with_plan_mode(provider_with_fake_binary):
    """T4.4 acceptance: plan_mode=True adds --plan to argv."""
    provider, fake = provider_with_fake_binary
    argv = provider.build_argv(
        {"provider_options": {"plan_mode": True}}, "",
    )
    assert "--plan" in argv


def test_argv_without_plan_mode_omits_flag(provider_with_fake_binary):
    """Default state (plan_mode not set) → no --plan."""
    provider, _ = provider_with_fake_binary
    argv = provider.build_argv({}, "")
    assert "--plan" not in argv


def test_argv_plan_mode_via_legacy_top_level(provider_with_fake_binary):
    """Legacy schema (top-level keys) also accepted via opts fallback."""
    provider, _ = provider_with_fake_binary
    argv = provider.build_argv({"plan_mode": True}, "")
    assert "--plan" in argv


def test_argv_plan_mode_suppressed_when_capability_disabled(tmp_path):
    """Defensive forward-compat: provider_options.plan_mode requested
    but capability=False → flag suppressed."""
    cfg = json.loads(json.dumps(load_providers_config()["providers"]["copilot"]))
    fake = tmp_path / "copilot"
    _make_executable(fake)
    cfg["binary"]["search_paths"] = [str(fake)]
    cfg["capabilities"]["plan_mode"] = False
    p = CopilotProvider(cfg)
    argv = p.build_argv({"plan_mode": True}, "")
    assert "--plan" not in argv


def test_argv_combines_plan_with_other_flags(provider_with_fake_binary):
    """Plan mode coexists with other Copilot flags."""
    provider, _ = provider_with_fake_binary
    argv = provider.build_argv(
        {
            "provider_options": {
                "skip_permissions": True,
                "plan_mode": True,
                "model": "claude-sonnet-4-5",
            },
        },
        "Hello",
    )
    assert "--plan" in argv
    assert "--yolo" in argv
    assert "--model" in argv
    assert argv[argv.index("--model") + 1] == "claude-sonnet-4-5"


def test_argv_plan_flag_precedes_intro_prompt(provider_with_fake_binary):
    """Order sanity: --plan appears before the -i/-p intro arg."""
    provider, _ = provider_with_fake_binary
    argv = provider.build_argv(
        {"provider_options": {"plan_mode": True}}, "intro text",
    )
    plan_idx = argv.index("--plan")
    # -i is the intro flag for interactive mode (default)
    assert "-i" in argv
    intro_idx = argv.index("-i")
    assert plan_idx < intro_idx


# ─── Dialog schema ──────────────────────────────────────────────────────────

def test_schema_includes_plan_mode_when_capability_enabled():
    """T4.4 default: plan_mode capability=True → schema has entry."""
    provider = CopilotProvider(load_providers_config()["providers"]["copilot"])
    schema = provider.get_dialog_schema()
    keys = [entry[0] for entry in schema]
    assert "plan_mode" in keys
    plan_entry = next(e for e in schema if e[0] == "plan_mode")
    assert plan_entry[1] == "checkbox"
    assert "plan mode" in plan_entry[2].lower()
    # Label mentions --plan flag
    assert "--plan" in plan_entry[2]


def test_schema_omits_plan_mode_when_capability_disabled():
    """Defensive: capability=False suppresses the schema entry."""
    cfg = json.loads(json.dumps(load_providers_config()["providers"]["copilot"]))
    cfg["capabilities"]["plan_mode"] = False
    p = CopilotProvider(cfg)
    schema = p.get_dialog_schema()
    keys = [entry[0] for entry in schema]
    assert "plan_mode" not in keys


def test_schema_order_preserves_known_layout():
    """Order of fields after task #54 (2026-05-07): skip_permissions,
    plan_mode, allowed_tools. The model combo previously between
    skip_permissions and plan_mode was removed — see #54 for rationale."""
    provider = CopilotProvider(load_providers_config()["providers"]["copilot"])
    schema = provider.get_dialog_schema()
    keys = [entry[0] for entry in schema]
    assert keys == ["skip_permissions", "plan_mode", "allowed_tools", "image_paste_template"]


def test_claude_schema_does_not_have_plan_mode():
    """Claude provider doesn't expose Copilot's plan-mode toggle."""
    from bterminal.providers.claude import ClaudeProvider
    p = ClaudeProvider(load_providers_config()["providers"]["claude"])
    schema = p.get_dialog_schema()
    keys = [entry[0] for entry in schema]
    assert "plan_mode" not in keys


# ─── Capability + R4.2 schema routing ──────────────────────────────────────

def test_default_capability_plan_mode_is_true():
    """T4.4 baseline acceptance: capability flipped True in defaults.json."""
    caps = load_providers_config()["providers"]["copilot"]["capabilities"]
    assert caps["plan_mode"] is True


def test_plan_mode_routed_into_provider_options():
    """Caller passes plan_mode flag; _split_provider_options_from_data
    moves it into provider_options like other Copilot flags."""
    from bterminal.ui.dialogs.ai_session import _split_provider_options_from_data
    flat = {
        "name": "x", "project_dir": "/tmp",
        "provider": "copilot",
        "plan_mode": True,
        "skip_permissions": True,
    }
    out = _split_provider_options_from_data(flat)
    assert out["provider_options"]["plan_mode"] is True
    assert "plan_mode" not in out
