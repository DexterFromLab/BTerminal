"""Unit tests for AISessionDialog (T2.5).

GTK construction is hard to test without a display, so the bulk of
coverage targets the pure helpers extracted into bterminal.ui.dialogs.
ai_session module-level. The dialog class itself gets a smoke import
+ subclass relation test — actual visual flow is verified by manual
smoke per Tier 2 acceptance (T2.12).

Helpers tested:
    _build_provider_combo_items(registry)
    _split_provider_options_from_data(data)
    _flatten_session_for_legacy_dialog(session)
"""
from __future__ import annotations

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.ui.dialogs.ai_session import (
    AISessionDialog,
    _PROVIDER_OPTION_KEYS,
    _build_provider_combo_items,
    _flatten_session_for_legacy_dialog,
    _split_provider_options_from_data,
)
from bterminal.ui.dialogs.claude_code import ClaudeCodeDialog


@pytest.fixture(autouse=True)
def _reset_registry_singleton():
    reset_registry()
    yield
    reset_registry()


# ─── _build_provider_combo_items ─────────────────────────────────────────────

def test_build_provider_combo_items_with_default_registry():
    reg = ProviderRegistry(config=load_providers_config())
    items = _build_provider_combo_items(reg)
    names = [n for n, _l in items]
    # Sorted alphabetically by ProviderRegistry.all()
    # Task #3 / #75: aider added as 3rd bundled provider, alphabetical
    assert names == ["aider", "claude", "copilot"]


def test_build_provider_combo_items_label_format():
    reg = ProviderRegistry(config=load_providers_config())
    items = dict(_build_provider_combo_items(reg))
    # Label is "{icon} {long_label}" — both pieces present
    assert items["claude"] == "✨ Claude Code"
    assert items["copilot"] == "🤖 GitHub Copilot CLI"


def test_build_provider_combo_items_empty_registry():
    """Empty registry produces an empty list — dialog should still
    show (just no rows) rather than crashing."""
    reg = ProviderRegistry(config={"providers": {}, "default_provider": "claude"})
    items = _build_provider_combo_items(reg)
    assert items == []


# ─── _split_provider_options_from_data ───────────────────────────────────────

def test_split_provider_options_wraps_legacy_keys():
    data = {
        "name": "x", "project_dir": "/tmp",
        "resume": True, "skip_permissions": True, "sudo": False,
    }
    result = _split_provider_options_from_data(data)
    assert result["name"] == "x"
    assert result["project_dir"] == "/tmp"
    assert result["provider_options"] == {
        "resume": True, "skip_permissions": True, "sudo": False,
    }
    # Legacy keys removed from top-level
    assert "resume" not in result
    assert "skip_permissions" not in result
    assert "sudo" not in result


def test_split_provider_options_empty_when_no_legacy_keys():
    data = {"name": "x", "project_dir": "/tmp"}
    result = _split_provider_options_from_data(data)
    # No provider_options key when there's nothing to wrap
    assert "provider_options" not in result
    assert result == data


def test_split_provider_options_merges_with_existing():
    data = {
        "name": "x",
        "provider_options": {"json_output": True},
        "resume": True,
    }
    result = _split_provider_options_from_data(data)
    assert result["provider_options"] == {
        "json_output": True, "resume": True,
    }


def test_split_provider_options_does_not_mutate_input():
    """Defensive: caller's dict stays intact."""
    data = {"name": "x", "resume": True}
    snapshot = dict(data)
    _split_provider_options_from_data(data)
    assert data == snapshot


def test_split_provider_options_keys_constant_complete():
    """Sanity: every claude_code.py field that maps to provider state
    lives in _PROVIDER_OPTION_KEYS so the wrapper sees them."""
    expected = {"resume", "continue", "skip_permissions", "sudo", "model",
                "headless", "json_output", "allowed_tools", "plan_mode",
                "image_paste_template",
                # Task #3 (#75): Aider local-LLM endpoint override.
                "local_endpoint_url", "api_key"}
    assert set(_PROVIDER_OPTION_KEYS) == expected


# ─── _flatten_session_for_legacy_dialog ──────────────────────────────────────

def test_flatten_returns_none_for_none_input():
    """Add-mode (no session) → None passes through unchanged."""
    assert _flatten_session_for_legacy_dialog(None) is None


def test_flatten_unfolds_provider_options_to_top_level():
    """Edit-mode session with R4.2 nested options must unfold so the
    parent dialog's checkboxes pick up the values from top-level keys."""
    session = {
        "name": "x", "project_dir": "/tmp",
        "provider": "claude",
        "provider_options": {
            "resume": True, "skip_permissions": True, "sudo": False,
        },
    }
    flat = _flatten_session_for_legacy_dialog(session)
    assert flat["resume"] is True
    assert flat["skip_permissions"] is True
    assert flat["sudo"] is False
    # provider_options key removed after unfolding
    assert "provider_options" not in flat
    # Other keys preserved
    assert flat["name"] == "x"
    assert flat["provider"] == "claude"


def test_flatten_no_provider_options_passes_through():
    """Legacy session without provider_options nested dict — copy unchanged."""
    session = {"name": "x", "resume": True, "skip_permissions": True}
    flat = _flatten_session_for_legacy_dialog(session)
    assert flat == session


def test_flatten_does_not_mutate_input():
    session = {
        "name": "x",
        "provider_options": {"resume": True},
    }
    snapshot = {
        "name": "x",
        "provider_options": {"resume": True},
    }
    _flatten_session_for_legacy_dialog(session)
    assert session == snapshot


def test_flatten_provider_options_wins_over_top_level_duplicate():
    """If somehow both top-level and provider_options have the same
    key, provider_options wins (it's the canonical R4.2 source)."""
    session = {
        "resume": False,                       # legacy / stale
        "provider_options": {"resume": True},  # canonical
    }
    flat = _flatten_session_for_legacy_dialog(session)
    assert flat["resume"] is True


# ─── AISessionDialog class identity ──────────────────────────────────────────

def test_ai_session_dialog_subclasses_claude_code_dialog():
    """Subclass relation lets every existing test/integration that
    references ClaudeCodeDialog work with AISessionDialog instances."""
    assert issubclass(AISessionDialog, ClaudeCodeDialog)


def test_ai_session_dialog_module_exports():
    """Public API surface: module exposes the dialog + 3 pure helpers."""
    from bterminal.ui.dialogs import ai_session
    assert hasattr(ai_session, "AISessionDialog")
    assert hasattr(ai_session, "_build_provider_combo_items")
    assert hasattr(ai_session, "_split_provider_options_from_data")
    assert hasattr(ai_session, "_flatten_session_for_legacy_dialog")


# ─── Round-trip: dialog data flow contract ───────────────────────────────────

def test_round_trip_legacy_session_to_r4_2_schema():
    """Edit-mode load (flatten) → save (split) is idempotent for a
    typical session — the data shape comes back canonical R4.2."""
    legacy = {
        "id": "abc", "name": "MyProject",
        "project_dir": "/tmp/proj", "color": "#89b4fa",
        "resume": True, "skip_permissions": True, "sudo": False,
        "prompt": "", "enabled_plugins": [],
    }
    flat = _flatten_session_for_legacy_dialog(legacy)
    # Simulate dialog edits — user added provider field via dropdown
    flat["provider"] = "claude"
    saved = _split_provider_options_from_data(flat)

    assert saved["provider"] == "claude"
    assert saved["provider_options"] == {
        "resume": True, "skip_permissions": True, "sudo": False,
    }
    assert saved["name"] == "MyProject"
    assert saved["project_dir"] == "/tmp/proj"
    assert saved["color"] == "#89b4fa"
    assert "resume" not in saved
    assert "skip_permissions" not in saved


# ─── Schema methods (T2.6) — provider-specific dialog fields ────────────────


def test_base_provider_dialog_schema_is_empty():
    """Default AIProvider.get_dialog_schema() returns []."""
    from bterminal.providers.base import AIProvider
    # Use a minimal subclass to instantiate the ABC
    from tests.test_providers_base import _DummyProvider
    p = _DummyProvider()
    assert p.get_dialog_schema() == []


def test_claude_provider_dialog_schema():
    """ClaudeProvider returns 3 checkboxes — resume / skip_permissions / sudo."""
    from bterminal.providers.claude import ClaudeProvider
    p = ClaudeProvider(load_providers_config()["providers"]["claude"])
    schema = p.get_dialog_schema()
    keys = [entry[0] for entry in schema]
    types = [entry[1] for entry in schema]
    assert keys == ["resume", "skip_permissions", "sudo"]
    assert all(t == "checkbox" for t in types)
    # Each entry has at least (key, type, label)
    for entry in schema:
        assert len(entry) >= 3
        assert isinstance(entry[2], str) and entry[2]


def test_copilot_provider_dialog_schema():
    """CopilotProvider after task #54 (2026-05-07): skip_permissions +
    plan_mode + allowed_tools. The model combo was removed because
    hardcoded model lists drift from reality fast (Sonnet 4.5 → 4.6
    → 4.7); switch models at runtime via the native `/model` slash
    command instead. Default capability granular_permissions=True so
    allowed_tools is present."""
    from bterminal.providers.copilot import CopilotProvider
    p = CopilotProvider(load_providers_config()["providers"]["copilot"])
    schema = p.get_dialog_schema()
    keys = [entry[0] for entry in schema]
    types = [entry[1] for entry in schema]
    assert keys == ["skip_permissions", "plan_mode", "allowed_tools", "image_paste_template"]
    assert types == ["checkbox", "checkbox", "textarea", "text"]


def test_copilot_schema_does_not_render_model_combo():
    """Regression for task #54 (2026-05-07): hardcoded model dropdown
    removed from the dialog schema. New sessions inherit whatever the
    Copilot CLI's own default is; saved sessions with an explicit
    `provider_options.model` are still honored at spawn (legacy
    backcompat) — they just aren't editable from the dialog."""
    from bterminal.providers.copilot import CopilotProvider
    p = CopilotProvider(load_providers_config()["providers"]["copilot"])
    schema = p.get_dialog_schema()
    model_entries = [e for e in schema if e[0] == "model"]
    assert model_entries == [], (
        f"task #54 expected zero model entries in Copilot schema; got "
        f"{model_entries}"
    )


def test_schema_keys_are_subset_of_provider_option_keys():
    """Sanity: every schema key gets routed into provider_options by
    get_data() — only works if the key is in _PROVIDER_OPTION_KEYS."""
    from bterminal.providers.claude import ClaudeProvider
    from bterminal.providers.copilot import CopilotProvider

    for cls, name in [(ClaudeProvider, "claude"), (CopilotProvider, "copilot")]:
        p = cls(load_providers_config()["providers"][name])
        schema_keys = {entry[0] for entry in p.get_dialog_schema()}
        assert schema_keys.issubset(set(_PROVIDER_OPTION_KEYS)), (
            f"{name} has schema key(s) outside _PROVIDER_OPTION_KEYS: "
            f"{schema_keys - set(_PROVIDER_OPTION_KEYS)}"
        )


# ─── Schema-driven get_data flow (simulated without GTK) ────────────────────


def _simulate_get_data(provider_name, base_data, schema_values):
    """Reproduce AISessionDialog.get_data() flow without GTK: drop
    parent's static provider keys, overlay schema-driven values, run
    through _split_provider_options_from_data."""
    base = dict(base_data)
    base["provider"] = provider_name
    for key in _PROVIDER_OPTION_KEYS:
        base.pop(key, None)
    base.update(schema_values)
    return _split_provider_options_from_data(base)


def test_simulated_get_data_for_claude_uses_schema_values():
    """Claude session: schema gives {resume, skip_permissions, sudo} —
    these end up in provider_options, parent's static values dropped."""
    base = {
        "name": "myproj", "project_dir": "/tmp",
        # Pretend parent's static checkboxes wrote these — we drop them
        "resume": False, "skip_permissions": False, "sudo": False,
    }
    schema_values = {
        "resume": True, "skip_permissions": True, "sudo": False,
    }
    saved = _simulate_get_data("claude", base, schema_values)
    assert saved["provider"] == "claude"
    assert saved["provider_options"] == {
        "resume": True, "skip_permissions": True, "sudo": False,
    }
    assert "resume" not in saved
    assert saved["name"] == "myproj"


def test_simulated_get_data_for_copilot_only_includes_schema_keys():
    """Copilot session: schema gives {skip_permissions, model} — parent's
    Claude-specific resume/sudo are dropped (not in schema)."""
    base = {
        "name": "x", "project_dir": "/tmp",
        # Parent's static checkboxes (Claude-flavored) — must be dropped
        "resume": True, "skip_permissions": False, "sudo": True,
    }
    schema_values = {
        "skip_permissions": True, "model": "claude-sonnet-4-5",
    }
    saved = _simulate_get_data("copilot", base, schema_values)
    assert saved["provider"] == "copilot"
    assert saved["provider_options"] == {
        "skip_permissions": True, "model": "claude-sonnet-4-5",
    }
    # Claude-specific keys must not leak into a Copilot session
    assert "resume" not in saved.get("provider_options", {})
    assert "sudo" not in saved.get("provider_options", {})


def test_round_trip_r4_2_session_unchanged():
    """Already-canonical R4.2 session: flatten → split returns the
    same shape (legacy keys back into provider_options)."""
    r4_2 = {
        "id": "abc", "name": "x", "project_dir": "/tmp",
        "provider": "copilot",
        "provider_options": {
            "resume": True, "skip_permissions": True,
            "model": "claude-sonnet-4-5",
        },
    }
    flat = _flatten_session_for_legacy_dialog(r4_2)
    saved = _split_provider_options_from_data(flat)
    assert saved["provider"] == "copilot"
    assert saved["provider_options"] == {
        "resume": True, "skip_permissions": True,
        "model": "claude-sonnet-4-5",
    }
    assert saved["id"] == "abc"
