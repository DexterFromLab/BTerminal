"""Rules-inject byte-for-byte parity across providers (#21 / #93).

R7b: the rules-injection feed format MUST be identical across all AI
providers. BTerminal stuffs `ctx rules inject <project>` stdout into
VTE.feed_child() the exact same way for Claude, Copilot, and Aider —
no per-provider wrapping, no different newline conventions, no
provider-specific prefix.

This test pins that contract via:
  1. extract_rules_inject_bytes() — pure helper. Same input → same
     bytes regardless of provider_name. The signature carries
     provider_name on purpose: someone tempted to specialize per
     provider has to either (a) make that branch explicit, which
     fails this test, or (b) document the divergence in capabilities.
  2. The same helper is invoked from _do_inject_rules (production),
     so the test isn't testing a parallel reality.
  3. Bonus: simulate the chat-history capture to assert that the
     rules block reaches the user-turn position on Aider's side
     (instead of being silently dropped at PTY boundary).

The "100 prompts in Aider session" manual VM check from the auto-
trigger plan is documented in tests/manual/README.md — when ollama
becomes available, that's the integration smoke. The unit-level
parity here is what catches dispatch divergence regardless of
ollama availability.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from bterminal.providers import get_registry
from bterminal.ui.terminal_tab import extract_rules_inject_bytes

REGISTRY = get_registry()
PROVIDERS = ["claude", "copilot", "aider"]


# A representative rules stdout — what `ctx rules inject myproj`
# typically produces. Mixed bullets + headings + trailing whitespace
# so the test exercises the strip() path.
SAMPLE_RULES_STDOUT = """## Project rules for myproj

- Always reply concisely.
- Use TDD: tests first, implementation second.
- Never run destructive commands without confirmation.

   """  # trailing whitespace deliberately included


# ─── Byte-equality across providers ────────────────────────────────────────


def test_rules_inject_bytes_identical_across_three_providers():
    """The headline #93 invariant. Same rules stdout → same bytes for
    Claude / Copilot / Aider. If this fails, BT's PTY feed has
    diverged and at least one provider sees a different rules block."""
    bytes_per_provider = {
        name: extract_rules_inject_bytes(name, "myproj", SAMPLE_RULES_STDOUT)
        for name in PROVIDERS
    }
    claude_bytes = bytes_per_provider["claude"]
    for name, bs in bytes_per_provider.items():
        assert bs == claude_bytes, (
            f"{name} rules_inject bytes diverged from Claude.\n"
            f"  claude ({len(claude_bytes)} bytes): {claude_bytes!r}\n"
            f"  {name} ({len(bs)} bytes):    {bs!r}"
        )


@pytest.mark.parametrize("provider_name", PROVIDERS)
def test_rules_inject_bytes_strip_trailing_whitespace(provider_name):
    """Trailing whitespace from `ctx rules inject` stdout is stripped
    BEFORE encoding — without that, the rules block would have a
    visible blank line that some providers might interpret as a
    prompt boundary."""
    out = extract_rules_inject_bytes(
        provider_name, "myproj", SAMPLE_RULES_STDOUT)
    # Doesn't end with whitespace
    assert not out.endswith(b" ")
    assert not out.endswith(b"\n")
    # Last bullet's content is preserved (not over-stripped)
    assert b"Never run destructive commands" in out


@pytest.mark.parametrize("provider_name", PROVIDERS)
def test_rules_inject_bytes_preserves_inner_newlines(provider_name):
    """Inner \\n's (between bullets, headings) MUST survive — only
    leading/trailing whitespace gets stripped. Without inner newlines
    the rules block becomes one long unreadable line."""
    out = extract_rules_inject_bytes(
        provider_name, "myproj", SAMPLE_RULES_STDOUT)
    # 4 expected newlines: header + 3 bullets (≥3 \n in the content
    # remaining after the leading heading + bullets bullets)
    assert out.count(b"\n") >= 3, (
        f"{provider_name} dropped inner newlines: {out!r}"
    )


@pytest.mark.parametrize("provider_name", PROVIDERS)
def test_rules_inject_bytes_utf8_encoded(provider_name):
    """Pin the encoding — UTF-8. Non-ASCII chars in rules (Polish/UTF-8
    bullets, em-dashes, etc.) must round-trip correctly."""
    rules = "## Reguły\n\n- Bądź zwięzły — odpowiadaj krótko."
    out = extract_rules_inject_bytes(provider_name, "myproj", rules)
    decoded = out.decode("utf-8")
    assert "Bądź zwięzły" in decoded
    assert "—" in decoded


@pytest.mark.parametrize("provider_name", PROVIDERS)
def test_rules_inject_bytes_empty_input_yields_empty(provider_name):
    """Defensive: empty/whitespace-only stdout → empty bytes. The
    caller (_do_inject_rules) already early-returns on empty
    project_block, but if that ever changes, the helper must not
    crash."""
    for empty in ("", "   ", "\n\n\n", "\t  \n  "):
        out = extract_rules_inject_bytes(provider_name, "myproj", empty)
        assert out == b"", (
            f"{provider_name} non-empty for {empty!r}: {out!r}"
        )


# ─── Helper signature contract ────────────────────────────────────────────


def test_helper_signature_includes_provider_name_for_documentation():
    """The helper's first arg is provider_name. Removing it would let
    a future contributor silently introduce per-provider branches —
    keeping it in the signature documents 'this MUST stay agnostic'."""
    import inspect
    sig = inspect.signature(extract_rules_inject_bytes)
    params = list(sig.parameters.keys())
    assert params[0] == "provider_name", (
        f"helper signature drift: {params}"
    )


def test_helper_does_not_branch_on_provider_in_implementation():
    """Read the helper's source — it must not contain a `provider_name
    == 'claude'` style branch. The test reflects the production
    contract; if someone adds such a branch they have to delete this
    test deliberately."""
    src = inspect.getsource(extract_rules_inject_bytes)
    # No equality / membership tests against provider_name
    assert "provider_name ==" not in src
    assert "provider_name in" not in src
    assert "if provider_name" not in src


import inspect  # imported here so the test above can use it  # noqa: E402


# ─── Production parity: _do_inject_rules uses the helper ──────────────────


def test_do_inject_rules_in_production_calls_extract_rules_inject_bytes():
    """The rules_inject feed site in TerminalTab._do_inject_rules MUST
    delegate to extract_rules_inject_bytes — otherwise the parity
    test above could pass while production silently diverged.

    Source-grep is the cheapest way to assert this without spawning
    GTK + a real TerminalTab instance."""
    repo = Path(__file__).resolve().parent.parent
    text = (repo / "bterminal" / "ui" / "terminal_tab.py").read_text()
    # In _do_inject_rules's body: extract_rules_inject_bytes call
    body_start = text.find("def _do_inject_rules")
    assert body_start > 0
    body_end = text.find("\n    def ", body_start + 1)
    body = text[body_start:body_end]
    assert "extract_rules_inject_bytes" in body, (
        "_do_inject_rules no longer calls the parity helper — "
        "production rules_inject can now diverge from the test contract"
    )
    # And the bytes fed to record_feed + feed_child are the helper's output
    assert "record_feed(\"rules_inject\", rules_bytes)" in body
    assert "feed_child(rules_bytes)" in body


# ─── Capability gate parity (rules_inject=True for all 3) ────────────────


@pytest.mark.parametrize("provider_name", PROVIDERS)
def test_provider_declares_rules_inject_capability(provider_name):
    """Belt-and-braces with #19's parity matrix — if any provider
    flips rules_inject=False, the dispatch gate
    (should_inject_rules) returns False and _maybe_inject_rules
    early-exits. The bytes parity becomes irrelevant when the gate
    is closed."""
    cap = REGISTRY.get(provider_name).capabilities
    assert cap.rules_inject is True, (
        f"{provider_name}.rules_inject is False — gate closed, "
        f"rules_inject feed never fires for this provider"
    )


# ─── "Captured by chat history" simulation (Aider's .md log) ──────────────


def test_aider_chat_history_format_can_consume_rules_inject_bytes(tmp_path):
    """Aider writes user turns to .aider.chat.history.md prefixed with
    `#### `. Verify that the rules_inject bytes — which are NOT
    prefixed with `#### ` because they're a raw paste — can be
    detected when the chat history file is read back. This is the
    'rules block appears in chat history' bonus from the auto-trigger
    plan, simulated without an ollama daemon."""
    rules_bytes = extract_rules_inject_bytes(
        "aider", "myproj", SAMPLE_RULES_STDOUT)

    # Simulate aider's chat-history capture: the bytes get prefixed by
    # aider's `#### ` user-turn marker once flushed (we approximate
    # what Aider does so AiderProvider.parse_session_stats and any
    # downstream test can find the rules content).
    history = tmp_path / ".aider.chat.history.md"
    history.write_text(
        "# aider chat\n\n"
        "#### " + rules_bytes.decode("utf-8") + "\n\n"
        "**Aider:** Acknowledged.\n"
    )

    text = history.read_text()
    # Rules content shows up exactly as fed
    assert "Project rules for myproj" in text
    assert "Always reply concisely" in text
    # And it lives inside a user-turn marker block (Aider format)
    user_turn_re = re.compile(r"#### .*?Project rules", re.DOTALL)
    assert user_turn_re.search(text), (
        "rules block didn't land in a `#### ` user-turn block"
    )

    # The provider's own parser sees it as a response_count of >=1
    aider = REGISTRY.get("aider")
    stats = aider.parse_session_stats(str(history))
    assert stats.response_count >= 1, (
        f"AiderProvider.parse_session_stats didn't count the rules "
        f"turn as a user-response: {stats!r}"
    )


# ─── Negative: an unrelated bytes path doesn't accidentally match ─────────


def test_extract_rules_inject_bytes_distinct_from_ctx_refresh_format():
    """Ctx refresh has its own labelled wrapper
    (`=== project context refresh [proj] ===`). The rules-inject
    bytes do NOT include that label — pin the difference so the two
    feed paths can't be confused at the wire format level."""
    rules = "- be concise\n"
    out = extract_rules_inject_bytes("aider", "myproj", rules)
    assert b"project context refresh" not in out
    assert b"=== " not in out
