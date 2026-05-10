"""Pin tests for #109 — Diagnostics audit + AI provider rows.

Verifies that:
- audit_ai_providers() returns AIProviderStatus per claude/copilot/aider
- format_summary_text(statuses, ai_statuses=...) appends an
  'AI Providers:' section listing each provider name
- format_summary_text() w/o ai_statuses keeps legacy behaviour
- Missing providers get install hints (npm/pipx)
"""
from unittest.mock import patch

from bterminal.diagnostics import (
    AIProviderStatus,
    DepSpec,
    DepStatus,
    audit_ai_providers,
    format_summary_text,
)


# Stub deps — minimal for format_summary_text tests
_DEPS_STUB = (
    DepSpec(cmd="git", apt_pkg="git", label="git", tier="required"),
)
_STATUSES_STUB = [
    DepStatus(spec=_DEPS_STUB[0], present=True, path="/usr/bin/git",
              version="git version 2.43.0"),
]


def test_ai_provider_status_dataclass_fields():
    """Pin: AIProviderStatus has expected fields."""
    s = AIProviderStatus(name="claude", label="Claude Code")
    assert s.name == "claude"
    assert s.label == "Claude Code"
    assert s.present is False
    assert s.path == ""
    assert s.version == ""


def test_audit_ai_providers_returns_three_providers_by_default():
    """Pin: claude + copilot + aider are the canonical 3 providers."""
    with patch("bterminal.diagnostics._detect_ai_provider") as m:
        m.side_effect = lambda n: AIProviderStatus(
            name=n, label=n.title(), present=False,
        )
        result = audit_ai_providers()
    assert len(result) == 3
    names = [r.name for r in result]
    assert names == ["claude", "copilot", "aider"]


def test_audit_ai_providers_custom_names():
    """Pin: caller can pass subset/custom provider list."""
    with patch("bterminal.diagnostics._detect_ai_provider") as m:
        m.side_effect = lambda n: AIProviderStatus(name=n, label=n)
        result = audit_ai_providers(names=("claude",))
    assert len(result) == 1
    assert result[0].name == "claude"


def test_format_summary_text_legacy_no_ai_section():
    """Pin: w/o ai_statuses arg, output stays compatible (no 'AI Providers')."""
    text = format_summary_text(_STATUSES_STUB)
    assert "[SUMMARY]" in text
    assert "AI Providers" not in text


def test_format_summary_text_with_ai_section():
    """Pin: ai_statuses=[...] appends 'AI Providers:' section."""
    ai = [
        AIProviderStatus(name="claude", label="Claude Code",
                         present=True, path="/home/u/.local/bin/claude",
                         version="2.1.136 (Claude Code)"),
        AIProviderStatus(name="copilot", label="GitHub Copilot CLI",
                         present=True, path="/home/u/.local/bin/copilot",
                         version="GitHub Copilot CLI 1.0.44."),
        AIProviderStatus(name="aider", label="Aider (local LLM)",
                         present=False),
    ]
    text = format_summary_text(_STATUSES_STUB, ai_statuses=ai)
    assert "AI Providers:" in text
    # All 3 provider labels present
    assert "Claude Code" in text
    assert "GitHub Copilot CLI" in text
    assert "Aider (local LLM)" in text
    # Present providers show ✓ + version
    assert "✓" in text
    assert "2.1.136" in text
    assert "1.0.44" in text


def test_format_summary_text_missing_provider_shows_install_hint():
    """Pin: missing aider → 'pipx install aider-chat' hint visible."""
    ai = [
        AIProviderStatus(name="aider", label="Aider", present=False),
    ]
    text = format_summary_text(_STATUSES_STUB, ai_statuses=ai)
    assert "✗" in text
    assert "pipx install aider-chat" in text


def test_format_summary_text_missing_claude_shows_npm_hint():
    """Pin: missing claude → 'npm install -g @anthropic-ai/claude-code'."""
    ai = [
        AIProviderStatus(name="claude", label="Claude Code", present=False),
    ]
    text = format_summary_text(_STATUSES_STUB, ai_statuses=ai)
    assert "npm install -g @anthropic-ai/claude-code" in text


def test_format_summary_text_missing_copilot_shows_npm_hint():
    """Pin: missing copilot → 'npm install -g @github/copilot'."""
    ai = [
        AIProviderStatus(name="copilot", label="GitHub Copilot CLI",
                         present=False),
    ]
    text = format_summary_text(_STATUSES_STUB, ai_statuses=ai)
    assert "npm install -g @github/copilot" in text


def test_app_diagnostics_dialog_uses_ai_section():
    """Pin: bterminal/app.py:_show_diagnostics_dialog calls
    audit_ai_providers() and passes result to format_summary_text.
    Catches accidental refactor that drops the AI section."""
    from pathlib import Path
    src = (Path(__file__).resolve().parent.parent
           / "bterminal" / "app.py").read_text()
    fn_idx = src.find("def _show_diagnostics_dialog")
    fn_end = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:fn_end]
    assert "audit_ai_providers" in body
    assert "ai_statuses=ai_statuses" in body or "ai_statuses=" in body
