"""Aider image paste flow integration test (#23 / #95).

Extends #69's image-paste-hint coverage with Aider-specific paths:
  (a) defaults.json:aider.argv.image_paste_template wraps {path} with
      a hint that nudges the model toward 'describe what you see
      before editing'.
  (b) Manual VM smoke: paste PNG, verify clipboard text matches
      template, verify aider's chat history records a Read tool
      dispatch on the path. Documented in tests/manual/README.md;
      headless tests below cover the dispatch logic in isolation.
  (c) Compare Claude vs Aider: same TEMPLATE MECHANISM, different
      default phrasing — Claude=bare-path (vision native),
      Copilot=`Read it`, Aider=`describe what you see`.

Headless tests follow the test_image_paste_hint.py pattern. The Ctrl+
Shift+V → clipboard.set_text → paste_clipboard GTK path is exercised
by the manual smoke checklist; here we pin the data-flow contracts
that determine WHAT bytes land in the terminal feed.
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


# ─── (a) defaults.json: Aider's template shape ────────────────────────────


def test_aider_image_paste_template_present_with_path_placeholder():
    """Aider's template MUST include {path} so format() substitutes.
    Without it, image paste sends a static string with no path —
    aider can't open the file."""
    reg = ProviderRegistry(config=load_providers_config())
    template = reg.get("aider")._argv_spec.get("image_paste_template")
    assert isinstance(template, str) and template, (
        f"aider lost image_paste_template: {template!r}"
    )
    assert "{path}" in template, (
        f"aider template missing {{path}} placeholder: {template!r}"
    )


def test_aider_image_paste_template_nudges_describe_or_inspect():
    """Aider's hint must use language that prompts the model to
    'inspect' / 'describe' the image rather than just acknowledge.
    qwen-coder is text-only, so the model needs an explicit 'open
    the file via Read tool' cue."""
    reg = ProviderRegistry(config=load_providers_config())
    template = reg.get("aider")._argv_spec["image_paste_template"]
    lower = template.lower()
    nudges = ("describe", "look", "view", "inspect", "read")
    assert any(verb in lower for verb in nudges), (
        f"aider template should nudge inspection; got {template!r}"
    )


def test_aider_image_paste_template_mentions_editing_or_code_context():
    """Aider's specific value-add over the generic Copilot template:
    'before editing' / 'before coding' framing — aider's whole UX is
    edit-then-respond, so the hint should name that explicitly. Pin
    the editing-context language so a future template update doesn't
    accidentally drop the cue."""
    reg = ProviderRegistry(config=load_providers_config())
    template = reg.get("aider")._argv_spec["image_paste_template"]
    lower = template.lower()
    edit_cue = any(w in lower for w in ("edit", "code", "change"))
    assert edit_cue, (
        f"aider template lost the editing/code framing: {template!r}"
    )


def test_aider_image_paste_template_formats_real_path_correctly():
    """End-to-end pure: real defaults template + sample path → the
    expected hint string. Mirrors test_copilot_template_formats_real_
    path_correctly from #69."""
    reg = ProviderRegistry(config=load_providers_config())
    template = reg.get("aider")._argv_spec["image_paste_template"]
    out = format_image_paste_hint(template, "/tmp/screenshot.png")
    assert "/tmp/screenshot.png" in out
    # Document the canonical default verbatim (matches defaults.json):
    # 'User provided image: {path} — describe what you see before
    #  editing any code.'
    assert out.startswith("User provided image: /tmp/screenshot.png")


# ─── (c) Compare Claude / Copilot / Aider — intent, not default text ─────


def test_three_providers_have_three_distinct_template_strategies():
    """The template strategies are intentionally different per
    provider. Pin them all so a 'unify all templates' refactor has
    to be explicit about flattening Claude's null."""
    reg = ProviderRegistry(config=load_providers_config())
    claude_tpl = reg.get("claude")._argv_spec.get("image_paste_template")
    copilot_tpl = reg.get("copilot")._argv_spec.get("image_paste_template")
    aider_tpl = reg.get("aider")._argv_spec.get("image_paste_template")

    # Claude: null (vision-native, bare path)
    assert claude_tpl is None
    # Copilot + Aider: non-null, both with {path}
    assert isinstance(copilot_tpl, str) and "{path}" in copilot_tpl
    assert isinstance(aider_tpl, str) and "{path}" in aider_tpl
    # And they are NOT identical strings — different intent per provider
    assert copilot_tpl != aider_tpl, (
        "Copilot + Aider templates collapsed into the same string — "
        "lose the per-provider phrasing intent"
    )


def test_aider_and_copilot_templates_share_identical_path_substitution_path():
    """Both providers go through the same `format_image_paste_hint`
    helper — their {path} substitution is byte-identical in mechanism,
    only the surrounding prose differs. This test pins the mechanism
    parity (so a future helper change touches both providers
    consistently)."""
    reg = ProviderRegistry(config=load_providers_config())
    cop_tpl = reg.get("copilot")._argv_spec["image_paste_template"]
    aid_tpl = reg.get("aider")._argv_spec["image_paste_template"]
    sample = "/home/u/copied/img.png"
    cop_out = format_image_paste_hint(cop_tpl, sample)
    aid_out = format_image_paste_hint(aid_tpl, sample)
    # Same path appears VERBATIM in both outputs — proves substitution
    # mechanism is provider-agnostic.
    assert sample in cop_out
    assert sample in aid_out


def test_claude_paste_returns_bare_path_unlike_aider():
    """Compare flow: same image, different observable. Claude → bare
    path (Anthropic SDK reads natively); Aider → wrapped hint."""
    reg = ProviderRegistry(config=load_providers_config())
    claude_tpl = reg.get("claude")._argv_spec.get("image_paste_template")
    aider_tpl = reg.get("aider")._argv_spec["image_paste_template"]
    img = "/tmp/test.png"

    assert format_image_paste_hint(claude_tpl, img) == img
    aider_out = format_image_paste_hint(aider_tpl, img)
    assert aider_out != img  # wrapped, not bare
    assert img in aider_out  # but still includes the path


# ─── _format_image_paste_for_provider dispatch — Aider session ────────────


def _stub_tab(ai_config=None):
    """TerminalTab spec'd MagicMock — exposes the method we want to
    test bound to a fake instance with just `.ai_config`."""
    from bterminal.ui.terminal_tab import TerminalTab
    tab = MagicMock(spec=TerminalTab)
    tab.ai_config = ai_config
    tab._format_image_paste_for_provider = \
        TerminalTab._format_image_paste_for_provider.__get__(tab)
    return tab


def test_format_for_provider_wraps_for_aider_session():
    """Aider session via _format_image_paste_for_provider: provider
    lookup happens at runtime, template applied to path."""
    tab = _stub_tab(ai_config={"provider": "aider", "name": "MyAider"})
    out = tab._format_image_paste_for_provider("/home/u/diagram.png")
    assert "/home/u/diagram.png" in out
    assert out != "/home/u/diagram.png"  # template applied
    # Same nudge contract as the template test
    lower = out.lower()
    assert any(verb in lower for verb in
                ("describe", "read", "look", "view", "inspect"))


def test_format_for_provider_aider_vs_claude_same_image_diverges():
    """Same image path, two different sessions — observable diff is
    exactly the template wrapping, nothing else."""
    img = "/tmp/img.png"
    aider_tab = _stub_tab(ai_config={"provider": "aider", "name": "a"})
    claude_tab = _stub_tab(ai_config={"provider": "claude", "name": "c"})

    aider_out = aider_tab._format_image_paste_for_provider(img)
    claude_out = claude_tab._format_image_paste_for_provider(img)

    assert claude_out == img  # bare path
    assert aider_out != img  # wrapped
    assert img in aider_out  # but path preserved


# ─── Session-level override (#71) parity ─────────────────────────────────


def test_aider_session_template_override_takes_precedence():
    """Per-session override beats provider default — same priority
    chain as Copilot/Claude. Verifies #71's user-customizable hint
    works for Aider too without per-provider hardcoding."""
    custom = "Custom Aider hint: study the screenshot at {path}, then revise main.py."
    tab = _stub_tab(ai_config={
        "provider": "aider",
        "name": "MyAider",
        "provider_options": {"image_paste_template": custom},
    })
    out = tab._format_image_paste_for_provider("/tmp/img.png")
    assert out == (
        "Custom Aider hint: study the screenshot at /tmp/img.png, "
        "then revise main.py."
    )


def test_aider_empty_session_override_falls_through_to_provider_default():
    """An empty-string override is treated as 'no override' — falls
    through to the provider default. Same fallthrough behavior as
    Copilot to avoid surprising users who clear the Entry expecting
    the bare-path behavior of Claude."""
    tab = _stub_tab(ai_config={
        "provider": "aider",
        "name": "MyAider",
        "provider_options": {"image_paste_template": ""},
    })
    out = tab._format_image_paste_for_provider("/tmp/img.png")
    # Falls through → provider default applies
    assert out != "/tmp/img.png"
    assert "/tmp/img.png" in out


# ─── Global toggle (#70) parity for Aider ────────────────────────────────


def test_global_toggle_off_returns_bare_path_even_for_aider(monkeypatch):
    """The image_paste_hint_enabled kill-switch in Options must apply
    to Aider just like Copilot. Without per-provider exemption, all
    template-using providers collapse to bare-path mode when the user
    flips this off."""
    from bterminal.config import _OPTIONS
    monkeypatch.setitem(_OPTIONS, "image_paste_hint_enabled", False)
    tab = _stub_tab(ai_config={"provider": "aider", "name": "MyAider"})
    out = tab._format_image_paste_for_provider("/tmp/img.png")
    assert out == "/tmp/img.png"


def test_global_toggle_off_does_not_block_session_override(monkeypatch):
    """Edge: explicit per-session override (#71) takes precedence
    even when the GLOBAL toggle is off — the user clearly opted in
    at the session level. Verified for Aider; mirrors the
    documented chain in _format_image_paste_for_provider docstring."""
    from bterminal.config import _OPTIONS
    monkeypatch.setitem(_OPTIONS, "image_paste_hint_enabled", False)
    tab = _stub_tab(ai_config={
        "provider": "aider",
        "name": "MyAider",
        "provider_options": {"image_paste_template": "Aider sees: {path}"},
    })
    out = tab._format_image_paste_for_provider("/tmp/img.png")
    # Session override fires regardless of global flag
    assert out == "Aider sees: /tmp/img.png"


# ─── Manual VM smoke documentation ────────────────────────────────────────


def test_manual_smoke_documented_in_runbook():
    """The 'paste PNG → verify clipboard → verify aider Read tool'
    flow needs a real GTK clipboard + a running ollama daemon, so
    it's a manual smoke. Pin that the runbook documents it so it
    doesn't get forgotten between releases."""
    runbook = REPO_ROOT / "tests" / "manual" / "README.md"
    text = runbook.read_text()
    # The smoke step is part of test_aider_real_model.sh (which the
    # runbook lists in its inventory) — documented as part of #89.
    assert "test_aider_real_model" in text or "image_paste" in text or \
        "Image paste" in text, (
        "Manual VM image-paste smoke step missing from runbook — "
        "add a one-liner referencing test_aider_real_model.sh + a "
        "'manually paste PNG' step"
    )
