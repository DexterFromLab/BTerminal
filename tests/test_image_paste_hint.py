"""Tests for per-provider image-paste vision hint (task #69, 2026-05-07).

Covers:
  - format_image_paste_hint pure helper (Bare path / format / defensive)
  - defaults.json wires templates correctly per provider
  - terminal_tab._format_image_paste_for_provider dispatches to the
    right template based on tab.ai_config.provider

The actual GTK paste path (Ctrl+Shift+V → clipboard.set_text →
paste_clipboard) is not unit-testable cleanly; the manual smoke
plan in the task description covers it.
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bterminal.helpers import format_image_paste_hint
from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


# ─── format_image_paste_hint pure ───────────────────────────────────────────


def test_none_template_returns_bare_path():
    assert format_image_paste_hint(None, "/tmp/x.png") == "/tmp/x.png"


def test_empty_template_returns_bare_path():
    assert format_image_paste_hint("", "/tmp/x.png") == "/tmp/x.png"


def test_template_with_path_placeholder_substitutes():
    out = format_image_paste_hint(
        "User provided image: {path} — Read it before responding.",
        "/home/u/copied_images/abc.png",
    )
    assert out == (
        "User provided image: /home/u/copied_images/abc.png — "
        "Read it before responding."
    )


def test_template_without_path_placeholder_returns_template_as_is():
    """Defensive: a static template without {path} (someone wrote a
    constant prompt) should land in the prompt verbatim, not crash
    with KeyError. The path is dropped, but that's an explicit user
    choice (they removed {path}). Same behavior as test 4 below for
    {nonexistent}."""
    out = format_image_paste_hint("Look at the screenshot.", "/tmp/x.png")
    assert out == "Look at the screenshot."


def test_template_with_unknown_placeholder_returns_template_as_is():
    """Defensive: KeyError on .format(path=...) when template has
    {nonexistent} → return template literal rather than crashing."""
    out = format_image_paste_hint(
        "Wat: {nonexistent_key}", "/tmp/x.png",
    )
    assert out == "Wat: {nonexistent_key}"


def test_template_with_path_placeholder_only_inserts_path():
    out = format_image_paste_hint("{path}", "/tmp/x.png")
    assert out == "/tmp/x.png"


def test_template_at_start_then_path():
    out = format_image_paste_hint("Look: {path}", "/tmp/x.png")
    assert out == "Look: /tmp/x.png"


# ─── defaults.json: per-provider templates wired correctly ──────────────────


def test_claude_image_paste_template_is_null():
    """Claude doesn't need a hint — Anthropic's prompt engineering
    auto-dispatches Read on bare paths. Template stays null so
    _format_image_paste_for_provider returns bare path."""
    reg = ProviderRegistry(config=load_providers_config())
    spec = reg.get("claude")._argv_spec
    assert spec.get("image_paste_template") is None


def test_copilot_image_paste_template_includes_read_instruction():
    """Copilot needs an imperative cue so the model deterministically
    calls Read. Template MUST include {path} placeholder + a phrase
    that nudges 'inspect the image' (any of: read/look/view/inspect)."""
    reg = ProviderRegistry(config=load_providers_config())
    template = reg.get("copilot")._argv_spec.get("image_paste_template")
    assert isinstance(template, str) and template
    assert "{path}" in template, (
        f"copilot template must keep the {{path}} placeholder; got {template!r}"
    )
    lower = template.lower()
    assert any(verb in lower for verb in ("read", "look", "view", "inspect")), (
        f"copilot template should nudge the model to inspect the image; "
        f"got {template!r}"
    )


def test_copilot_template_formats_real_path_correctly():
    """End-to-end pure: take the real defaults template, format
    against a sample path, verify output matches the documented
    smoke-test expectation in task #69."""
    reg = ProviderRegistry(config=load_providers_config())
    template = reg.get("copilot")._argv_spec["image_paste_template"]
    out = format_image_paste_hint(template, "/tmp/screenshot.png")
    assert "/tmp/screenshot.png" in out
    # Default template explicitly says "Read it" — see task #69 plan.
    assert "Read it" in out


# ─── terminal_tab._format_image_paste_for_provider dispatch ────────────────


def _stub_tab(ai_config=None):
    """Bare TerminalTab stub — only need .ai_config for the helper.
    Avoids importing Vte / GTK boot."""
    from bterminal.ui.terminal_tab import TerminalTab
    tab = MagicMock(spec=TerminalTab)
    tab.ai_config = ai_config
    # Bind the real method so the test exercises the actual dispatch
    tab._format_image_paste_for_provider = \
        TerminalTab._format_image_paste_for_provider.__get__(tab)
    return tab


def test_format_for_provider_returns_bare_path_for_no_ai_config():
    """SSH / local tabs (ai_config=None) get bare path back —
    no provider resolution, no template."""
    tab = _stub_tab(ai_config=None)
    out = tab._format_image_paste_for_provider("/tmp/x.png")
    assert out == "/tmp/x.png"


def test_format_for_provider_returns_bare_path_for_claude_session():
    """Claude session: provider's template is null → bare path."""
    tab = _stub_tab(ai_config={"provider": "claude", "name": "x"})
    out = tab._format_image_paste_for_provider("/tmp/x.png")
    assert out == "/tmp/x.png"


def test_format_for_provider_wraps_for_copilot_session():
    """Copilot session: template formatted with the path."""
    tab = _stub_tab(ai_config={"provider": "copilot", "name": "y"})
    out = tab._format_image_paste_for_provider("/home/u/img.png")
    assert "/home/u/img.png" in out
    assert "Read" in out
    assert out != "/home/u/img.png"  # template applied, not bare path


def test_format_for_provider_falls_back_to_bare_for_unknown_provider():
    """Forward-compat: a session naming a provider not in the registry
    must return bare path rather than crash with KeyError. This lets
    saved configs from a future BTerminal version fail gracefully."""
    tab = _stub_tab(
        ai_config={"provider": "future-cli-2030", "name": "z"})
    out = tab._format_image_paste_for_provider("/tmp/x.png")
    assert out == "/tmp/x.png"


def test_format_for_provider_handles_legacy_session_without_provider():
    """Legacy session config without `provider` key defaults to
    claude — bare path comes back."""
    tab = _stub_tab(ai_config={"name": "legacy", "project_dir": "/t"})
    out = tab._format_image_paste_for_provider("/tmp/x.png")
    assert out == "/tmp/x.png"


# ─── Task #70: global kill-switch ───────────────────────────────────────────


def test_format_for_provider_respects_global_disable_toggle(monkeypatch):
    """When _OPTIONS['image_paste_hint_enabled'] == False, even a
    Copilot session paste falls back to bare path. User-controlled
    kill-switch from Options dialog (#70)."""
    from bterminal import config
    monkeypatch.setitem(config._OPTIONS, "image_paste_hint_enabled", False)

    tab = _stub_tab(ai_config={"provider": "copilot", "name": "y"})
    out = tab._format_image_paste_for_provider("/home/u/img.png")
    assert out == "/home/u/img.png", (
        f"global toggle off should bypass per-provider template; got {out!r}"
    )


def test_format_for_provider_default_toggle_is_on(monkeypatch):
    """Sanity: default _OPTIONS schema has image_paste_hint_enabled=True
    — fresh installs MUST get the Copilot wrap out of the box. Toggle
    is opt-out, not opt-in."""
    from bterminal.config import _OPTIONS_DEFAULTS
    assert _OPTIONS_DEFAULTS["image_paste_hint_enabled"] is True


def test_format_for_provider_toggle_off_keeps_claude_at_bare_path(monkeypatch):
    """Sanity: turning off the toggle for Claude tabs is a no-op
    (Claude template is already null → bare path either way)."""
    from bterminal import config
    monkeypatch.setitem(config._OPTIONS, "image_paste_hint_enabled", False)

    tab = _stub_tab(ai_config={"provider": "claude", "name": "x"})
    out = tab._format_image_paste_for_provider("/tmp/x.png")
    assert out == "/tmp/x.png"


# ─── Task #70: options.json roundtrip ───────────────────────────────────────


def test_options_json_roundtrip_with_image_paste_hint(tmp_path, monkeypatch):
    """Save options.json with the new key + reload — value preserved.
    Forward-compat: reading an old options.json (without the key)
    falls back to the default True via _load_options merge."""
    import json
    from bterminal import config

    opts_file = tmp_path / "options.json"
    monkeypatch.setattr(config, "OPTIONS_FILE", str(opts_file))

    # Write OFF, reload, verify
    payload = dict(config._OPTIONS_DEFAULTS)
    payload["image_paste_hint_enabled"] = False
    opts_file.write_text(json.dumps(payload))
    reloaded = config._load_options()
    assert reloaded["image_paste_hint_enabled"] is False

    # Write a "legacy" file missing the new key — defaults merge fills it
    legacy = {k: v for k, v in config._OPTIONS_DEFAULTS.items()
              if k != "image_paste_hint_enabled"}
    opts_file.write_text(json.dumps(legacy))
    reloaded = config._load_options()
    assert reloaded["image_paste_hint_enabled"] is True, (
        "default for the new key should fill in when options.json predates it"
    )


# ─── Task #71: per-session override ─────────────────────────────────────────


def test_session_override_template_wins_over_provider_default():
    """User set 'Take a careful look at: {path}' on a Copilot session.
    Pasting an image must use the user's phrasing, not the default
    'User provided image: ... Read it' template."""
    custom = "Take a careful look at: {path} — describe layout in 3 sentences."
    tab = _stub_tab(ai_config={
        "provider": "copilot", "name": "y",
        "provider_options": {"image_paste_template": custom},
    })
    out = tab._format_image_paste_for_provider("/tmp/img.png")
    assert out == (
        "Take a careful look at: /tmp/img.png — describe layout in 3 sentences."
    )


def test_session_override_empty_string_falls_back_to_provider_default():
    """User cleared the Entry → empty string. Empty is treated as
    'no override', falls back to provider default. Lets users blank
    the field without deleting the JSON key (otherwise the dialog
    would re-add it on next save)."""
    tab = _stub_tab(ai_config={
        "provider": "copilot", "name": "y",
        "provider_options": {"image_paste_template": ""},
    })
    out = tab._format_image_paste_for_provider("/tmp/img.png")
    assert "Read it before responding" in out  # default Copilot template
    assert "/tmp/img.png" in out


def test_session_override_works_for_claude_too():
    """Claude default is null (bare path). User override on a Claude
    session can ADD a custom hint where there was none. This is a
    valid use case: power user wants Claude to apply a project-
    specific framing for visual content."""
    custom = "Image attached: {path} — focus on UI elements only."
    tab = _stub_tab(ai_config={
        "provider": "claude", "name": "x",
        "provider_options": {"image_paste_template": custom},
    })
    out = tab._format_image_paste_for_provider("/tmp/x.png")
    assert out == (
        "Image attached: /tmp/x.png — focus on UI elements only."
    )


def test_session_override_bypasses_global_disable_toggle(monkeypatch):
    """If user explicitly set a custom template AND the global toggle
    is off, the session-level explicit choice wins. The kill-switch
    is for accidental defaults, not for overriding deliberate user
    config — toggling Options off should NOT silently strip a
    saved per-session override."""
    from bterminal import config
    monkeypatch.setitem(config._OPTIONS, "image_paste_hint_enabled", False)

    custom = "Look at {path}"
    tab = _stub_tab(ai_config={
        "provider": "copilot", "name": "y",
        "provider_options": {"image_paste_template": custom},
    })
    out = tab._format_image_paste_for_provider("/tmp/x.png")
    assert out == "Look at /tmp/x.png"


def test_session_override_does_not_apply_to_ssh_tab():
    """SSH/local tab without ai_config: no provider_options to read,
    bare path. Sanity that the override path doesn't leak."""
    tab = _stub_tab(ai_config=None)
    out = tab._format_image_paste_for_provider("/tmp/x.png")
    assert out == "/tmp/x.png"


def test_image_paste_template_is_in_provider_option_keys():
    """Dialog routing must persist `image_paste_template` into
    provider_options on save (otherwise the field would land at
    top-level and the loader's R4.2 schema would lose it)."""
    from bterminal.ui.dialogs.ai_session import _PROVIDER_OPTION_KEYS
    assert "image_paste_template" in _PROVIDER_OPTION_KEYS


def test_copilot_dialog_schema_exposes_image_paste_template_text_entry():
    """Session dialog gets a 'text' widget for image_paste_template
    on Copilot, with the provider's default as placeholder so the
    user sees what they're overriding."""
    from bterminal.providers.copilot import CopilotProvider
    p = CopilotProvider(load_providers_config()["providers"]["copilot"])
    schema = p.get_dialog_schema()
    entry = next((e for e in schema if e[0] == "image_paste_template"), None)
    assert entry is not None, (
        f"copilot schema missing image_paste_template; got {[e[0] for e in schema]}"
    )
    assert entry[1] == "text"
    # placeholder/default = the provider default template
    assert "User provided image" in entry[3]
