"""Tests for granular permissions UI — T4.3.

Two layers:
  - `is_valid_allowed_tool_rule` / `parse_allowed_tools_text` — pure
    helpers covering Copilot's --allow-tool / --deny-tool grammar.
  - `CopilotProvider.get_dialog_schema` — gates the textarea entry on
    the `granular_permissions` capability.

GTK rendering of the actual GtkTextView happens in T2.12 / T4.8 manual
smoke; here we verify the schema contract + grammar.
"""
from __future__ import annotations

import json

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.providers.copilot import CopilotProvider
from bterminal.ui.dialogs.ai_session import (
    is_valid_allowed_tool_rule,
    parse_allowed_tools_text,
)


@pytest.fixture(autouse=True)
def _reset_registry():
    reset_registry()
    yield
    reset_registry()


# ─── is_valid_allowed_tool_rule ─────────────────────────────────────────────

@pytest.mark.parametrize("rule", [
    "shell",
    "shell(rm)",
    "shell(rm -rf)",
    "shell(curl -fsSL https://example.com)",
    "My-MCP-Server",
    "web.fetch",
    "github.repo",
    "github.pr.create",
    "_internal_tool",   # underscore start → invalid (must start with letter)
])
def test_valid_rules_are_accepted_or_rejected(rule):
    """Sanity that the validator is consistent — _internal_tool fails
    because it starts with underscore (Copilot's docs require letter)."""
    expected_valid = rule != "_internal_tool"
    assert is_valid_allowed_tool_rule(rule) is expected_valid, rule


@pytest.mark.parametrize("invalid", [
    "",
    "   ",
    "shell(",          # unclosed paren
    "shell)",          # stray close
    "shell(rm)x",      # trailing junk
    "1tool",           # number-prefixed
    "tool name",       # spaces in token
    "tool()",          # empty args
    "(rm)",            # missing token
])
def test_invalid_rules_rejected(invalid):
    assert is_valid_allowed_tool_rule(invalid) is False


def test_validator_strips_whitespace():
    assert is_valid_allowed_tool_rule("  shell  ") is True
    assert is_valid_allowed_tool_rule("\tshell(rm)\n") is True


def test_validator_handles_none():
    assert is_valid_allowed_tool_rule(None) is False


# ─── parse_allowed_tools_text ───────────────────────────────────────────────

def test_parse_separates_valid_and_invalid():
    text = """
    shell(rm)
    1bad
    My-MCP-Server
    shell(
    """.strip()
    out = parse_allowed_tools_text(text)
    assert out["valid"] == ["shell(rm)", "My-MCP-Server"]
    assert len(out["invalid"]) == 2
    line_numbers = [n for n, _ in out["invalid"]]
    assert line_numbers == [2, 4]


def test_parse_skips_blank_lines_and_comments():
    text = "\n# this is a comment\n\nshell\n  # indented comment\nweb.fetch"
    out = parse_allowed_tools_text(text)
    assert out["valid"] == ["shell", "web.fetch"]
    assert out["invalid"] == []


def test_parse_handles_empty_string():
    out = parse_allowed_tools_text("")
    assert out == {"valid": [], "invalid": []}


def test_parse_handles_none():
    out = parse_allowed_tools_text(None)
    assert out == {"valid": [], "invalid": []}


def test_parse_returns_1_based_line_numbers():
    """Line numbers must be human-readable (1-based)."""
    text = "shell(\nbad)"
    out = parse_allowed_tools_text(text)
    assert out["invalid"][0][0] == 1
    assert out["invalid"][1][0] == 2


# ─── CopilotProvider.get_dialog_schema gating ────────────────────────────

def test_copilot_schema_includes_allowed_tools_when_granular_enabled():
    """T4.3 default: granular_permissions=True → schema has
    allowed_tools textarea entry."""
    cfg = load_providers_config()["providers"]["copilot"]
    p = CopilotProvider(cfg)
    schema = p.get_dialog_schema()
    keys = [entry[0] for entry in schema]
    assert "allowed_tools" in keys
    allowed_entry = next(e for e in schema if e[0] == "allowed_tools")
    assert allowed_entry[1] == "textarea"
    # 4-tuple: (key, type, label, placeholder/helptext)
    assert len(allowed_entry) == 4
    assert allowed_entry[2]
    assert allowed_entry[3]


def test_copilot_schema_omits_allowed_tools_when_capability_off():
    """Defensive: if a future config disables granular_permissions,
    the textarea isn't rendered."""
    cfg = json.loads(json.dumps(load_providers_config()["providers"]["copilot"]))
    cfg["capabilities"]["granular_permissions"] = False
    p = CopilotProvider(cfg)
    schema = p.get_dialog_schema()
    keys = [entry[0] for entry in schema]
    assert "allowed_tools" not in keys


def test_claude_schema_does_not_have_allowed_tools():
    """Claude doesn't support Copilot's granular --allow-tool syntax."""
    from bterminal.providers.claude import ClaudeProvider
    cfg = load_providers_config()["providers"]["claude"]
    p = ClaudeProvider(cfg)
    schema = p.get_dialog_schema()
    keys = [entry[0] for entry in schema]
    assert "allowed_tools" not in keys


def test_copilot_default_capability_granular_permissions_is_true():
    """T4.3 baseline acceptance: capability flipped True in defaults.json."""
    caps = load_providers_config()["providers"]["copilot"]["capabilities"]
    assert caps["granular_permissions"] is True


# ─── Public API surface ─────────────────────────────────────────────────────

def test_helpers_exported_from_ai_session_module():
    from bterminal.ui.dialogs.ai_session import __all__
    assert "is_valid_allowed_tool_rule" in __all__
    assert "parse_allowed_tools_text" in __all__


# ─── Round-trip: textarea text round-trips through R4.2 schema ─────────────

def test_allowed_tools_routed_into_provider_options():
    """Caller passes the textarea content; _split_provider_options_from_data
    moves it into provider_options like other Copilot flags."""
    from bterminal.ui.dialogs.ai_session import _split_provider_options_from_data
    flat = {
        "name": "x", "project_dir": "/tmp",
        "provider": "copilot",
        "skip_permissions": True,
        "allowed_tools": "shell(rm)\nMy-MCP-Server",
    }
    out = _split_provider_options_from_data(flat)
    assert out["provider_options"]["allowed_tools"] == "shell(rm)\nMy-MCP-Server"
    assert out["provider_options"]["skip_permissions"] is True
    assert "allowed_tools" not in out
    assert "skip_permissions" not in out
