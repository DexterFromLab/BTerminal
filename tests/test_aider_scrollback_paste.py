"""Cross-feature: scrollback + paste in non-alt-screen mode
(#42 / #114, audit § 6.3 #15).

Aider runs in the main VTE screen (no alt-screen entry). When the
user scrolls up to read history, then pastes an image, the paste
must:
  1. Land at VTE bottom (the cursor row), NOT at the scroll
     position the user is currently viewing.
  2. Auto-scroll the viewport to bottom so the user sees what
     they just pasted.
  3. Preserve scrollback above — the historical lines the user
     was reading don't get clobbered.

These behaviors come from VTE's two scroll-policy properties:
  - `scroll-on-output = False`: AI output streams DON'T jerk the
    viewport. User can read scrollback uninterrupted.
  - `scroll-on-keystroke = True`: Any input (paste, type, key)
    auto-scrolls to bottom. This makes the input visible
    immediately.

Three decision branches:
  (a) Scrolled state — user has scrolled up N lines. Paste lands
      at bottom; viewport snaps down.
  (b) Frozen scrollback — `scroll-on-output=False` ensures the
      viewport stays put while AI streams. User reads history
      undisturbed.
  (c) Auto-scroll re-engages on paste — `scroll-on-keystroke=True`
      bounces back to bottom on input.

Pre-#114 baseline: aider HAD a `set scrollback friendly` mode
attempted via TERM=xterm-mono hack (#66-#68), but it was reverted
because it interfered with aider's TUI rendering. Pin: aider runs
in plain main-screen mode and relies on VTE's standard scroll
policies — no alt-screen workaround.

Manual VM smoke (scroll up in aider tab, paste image, observe)
is documented in tests/manual/README.md.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
TERMINAL_TAB = REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py"
DEFAULTS_JSON = REPO_ROOT / "bterminal" / "providers" / "defaults.json"
AIDER_PROVIDER = REPO_ROOT / "bterminal" / "providers" / "aider.py"


# ─── Branch (b): frozen scrollback — output doesn't auto-scroll ─────────


def test_terminal_tab_disables_scroll_on_output():
    """Pin: VTE.set_scroll_on_output(False) at TerminalTab init.
    Without this, every line of AI output yanks the viewport to
    bottom — user can't read scrollback while AI streams."""
    src = TERMINAL_TAB.read_text()
    assert "set_scroll_on_output(False)" in src, (
        "TerminalTab no longer sets scroll-on-output=False — "
        "AI output will jerk the viewport during scroll-back read"
    )


def test_terminal_tab_enables_scroll_on_keystroke():
    """Pin: VTE.set_scroll_on_keystroke(True). Any keystroke or
    paste auto-scrolls to bottom. This is what makes paste land
    at cursor row and become visible immediately even when user
    was scrolled up."""
    src = TERMINAL_TAB.read_text()
    assert "set_scroll_on_keystroke(True)" in src, (
        "TerminalTab no longer sets scroll-on-keystroke=True — "
        "paste lands at bottom but viewport stays at scroll pos"
    )


def test_scroll_policies_set_at_init_not_per_provider():
    """Both scroll-policy calls happen in `__init__`, not in
    spawn_ai_cli or per-provider hook. Pin so they apply uniformly
    to all providers (Claude, Copilot, Aider)."""
    src = TERMINAL_TAB.read_text()
    # Find __init__ and check both scroll calls live there
    init_idx = src.find("def __init__(self,")
    assert init_idx > 0
    # Find the next def (end of __init__)
    next_def = src.find("\n    def ", init_idx + 1)
    init_body = src[init_idx:next_def]
    assert "set_scroll_on_output(False)" in init_body
    assert "set_scroll_on_keystroke(True)" in init_body


# ─── Branch (a): scrolled state — VTE has finite scrollback buffer ──────


def test_scrollback_buffer_is_generous():
    """Pin: SCROLLBACK_LINES is large enough that 100 lines of
    AI output (typical session) easily fits + the user can
    scroll up freely. Without enough scrollback, the user's
    'historical read' position would fall off the top while
    AI keeps streaming."""
    src = TERMINAL_TAB.read_text()
    # SCROLLBACK_LINES is imported from config — check that
    # constant exists with sensible value
    config_src = (REPO_ROOT / "bterminal" / "config.py").read_text()
    m = re.search(r"SCROLLBACK_LINES\s*=\s*(\d+)", config_src)
    assert m, "SCROLLBACK_LINES not defined as int in config.py"
    n = int(m.group(1))
    assert n >= 1000, (
        f"SCROLLBACK_LINES = {n} too small — user can't read more "
        f"than {n} lines back"
    )


def test_scrollback_lines_passed_to_vte():
    """Pin: VTE.set_scrollback_lines(SCROLLBACK_LINES) is called.
    Without this, VTE uses its default (which may be tiny on
    some installs)."""
    src = TERMINAL_TAB.read_text()
    assert "set_scrollback_lines(SCROLLBACK_LINES)" in src


# ─── Branch (c): paste auto-scrolls via keystroke policy ────────────────


def test_paste_clipboard_image_path_uses_feed_child_or_paste_clipboard():
    """The paste flow goes through feed_child (or VTE's
    paste_clipboard, which itself feeds bytes). Both trigger the
    scroll-on-keystroke policy → viewport auto-scrolls to bottom.

    Pin: _paste_clipboard_image_path delivers bytes via VTE's
    standard input mechanism, NOT a custom write that bypasses
    keystroke-scroll."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _paste_clipboard_image_path")
    assert fn_start > 0
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    # Either path triggers scroll-on-keystroke
    has_feed = "feed_child" in body
    has_paste = "paste_clipboard" in body or "set_text" in body
    assert has_feed or has_paste, (
        "paste flow lost its VTE-input delivery — wouldn't "
        "trigger scroll-on-keystroke auto-scroll"
    )


def test_paste_does_not_explicitly_disable_scroll_on_paste():
    """Negative pin: paste flow does NOT set
    scroll-on-keystroke=False or scroll-to-bottom=False. Without
    this guard, a hypothetical 'preserve scroll position on
    paste' tweak would silently break user expectation."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _paste_clipboard_image_path")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    forbidden = [
        "set_scroll_on_keystroke(False)",
        "set_scroll_on_output(True)",  # would yank during AI output
        "scrollbar.set_value(",  # manual scroll override
    ]
    for pat in forbidden:
        assert pat not in body, (
            f"paste flow contains {pat!r} — overrides scroll policy"
        )


# ─── No alt-screen workaround for Aider ─────────────────────────────────


def test_aider_argv_does_not_trigger_alt_screen():
    """Pin: AiderProvider's build_argv flags don't include any
    alt-screen mode triggers. Aider runs in main screen with VTE's
    standard scrollback. Pre-#114 baseline (#66-#68): TERM=xterm-mono
    hack was attempted to force alt-screen-friendly behavior, then
    reverted — the audit doc § 10 captures the lesson."""
    src = AIDER_PROVIDER.read_text()
    forbidden = [
        "--alt-screen", "--alternate-screen",
        # TERM hacks reverted in #66-#68
        "TERM=xterm-mono", "TERM=screen",
    ]
    for pat in forbidden:
        assert pat not in src, (
            f"AiderProvider re-introduced alt-screen hack: {pat!r}"
        )


def test_aider_no_smcup_rmcup_emission_in_argv():
    """Pin: aider doesn't pass any flag that would request
    smcup/rmcup terminfo entries (which switch to alt-screen).
    --no-stream / --no-show-model-warnings are the canonical
    'main-screen-friendly' flags pinned."""
    src = AIDER_PROVIDER.read_text()
    # Find tui_safe defaults
    tui_safe_idx = src.find('"tui_safe"')
    assert tui_safe_idx > 0
    # Look at the line that follows for the flag list
    line_end = src.find("\n", tui_safe_idx)
    next_lines = src[tui_safe_idx:src.find("\n)", line_end)]
    # Pin canonical flags
    assert "--no-stream" in next_lines
    assert "--no-show-model-warnings" in next_lines
    # No smcup-style hacks
    assert "smcup" not in src
    assert "rmcup" not in src


def test_defaults_json_aider_no_alt_screen_flag():
    """Pin: defaults.json for aider has no `alt_screen` capability
    flag set to True. Capability for 'opts into alt screen' would
    influence dispatch — pin absent."""
    text = DEFAULTS_JSON.read_text()
    # No alt_screen capability key (we never added it)
    assert '"alt_screen"' not in text


# ─── Capability-level pin: aider doesn't request scroll-policy override ─


def test_aider_capabilities_do_not_override_scroll_policy():
    """The ProviderCapabilities dataclass has no field for
    overriding VTE scroll policy. Pin source so a future field
    addition triggers explicit audit."""
    base_src = (REPO_ROOT / "bterminal" / "providers" / "base.py"
                ).read_text()
    forbidden = ["scroll_on_output", "scroll_on_keystroke",
                 "alt_screen_mode", "scrollback_lines"]
    for pat in forbidden:
        assert pat not in base_src, (
            f"ProviderCapabilities now has {pat!r} — provider can "
            f"override scroll policy, breaking branch (b) "
            f"'frozen scrollback' invariant"
        )


# ─── Scroll position semantics: paste auto-scroll is implicit ──────────


def test_terminal_init_does_not_pin_scroll_to_bottom_callback():
    """Pin: TerminalTab doesn't connect a 'scroll-to-bottom on
    every output' signal. That would defeat scroll-on-output=False.
    The auto-scroll behavior comes purely from
    scroll-on-keystroke=True applied to user input."""
    src = TERMINAL_TAB.read_text()
    init_idx = src.find("def __init__(self,")
    next_def = src.find("\n    def ", init_idx + 1)
    init_body = src[init_idx:next_def]
    # No explicit scroll-to-bottom signal/callback in __init__
    assert "scroll_to_bottom" not in init_body, (
        "TerminalTab.__init__ has explicit scroll_to_bottom — "
        "would defeat scroll-on-output=False contract"
    )
    # No connection to vadjustment changing
    assert "vadjustment" not in init_body or "set_vadj" not in init_body


def test_paste_image_to_ctx_path_independent_from_paste_to_terminal():
    """The 'paste image to ctx' menu action (right-click → 'Paste
    Image') is a separate flow that saves the image to the project
    without typing anything into VTE. Pin: it doesn't use
    feed_child, so it doesn't trigger scroll-on-keystroke."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _on_paste_image_to_ctx")
    if fn_start < 0:
        # Method may not exist in this build — that's fine
        return
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    # Doesn't feed bytes through VTE (it just saves to disk)
    # so scroll-on-keystroke doesn't fire — by design (the user
    # should stay at their reading position).
    assert "feed_child" not in body, (
        "_on_paste_image_to_ctx unexpectedly feeds bytes to VTE — "
        "would auto-scroll the viewport away from user's position"
    )


# ─── Integration: rules_inject + scrolled state ─────────────────────────


def test_rules_inject_uses_feed_child_so_keystroke_scroll_applies():
    """Pin: when auto-trigger or rules_inject fires while user is
    scrolled up, feed_child triggers scroll-on-keystroke=True →
    viewport snaps to bottom so user sees the injected message.

    Without this, injected rules would land off-screen below the
    user's scroll position and they'd miss them."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _do_inject_rules")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    # rules_inject uses feed_child — auto-scrolls via keystroke policy
    assert "feed_child" in body


def test_auto_trigger_uses_feed_child_so_keystroke_scroll_applies():
    """Same pin for auto-trigger [AUTO-TRIGGER] message — fires
    via feed_child → scroll-on-keystroke=True snaps to bottom."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _on_task_idle_timeout")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "feed_child" in body


# ─── Scroll position preserved across non-input events ──────────────────


def test_record_feed_does_not_trigger_scroll():
    """Pin: `record_feed()` (debug-REST capture) does NOT touch
    VTE state — it just buffers the event in memory. User scroll
    position survives debug-REST consumers. Without this, every
    automated /api/debug/feed_log read would jerk the user's
    viewport."""
    src = (REPO_ROOT / "bterminal" / "debug_rest.py").read_text()
    fn_start = src.find("def record_feed")
    if fn_start < 0:
        # Helper at module level — find as a top-level function
        fn_start = src.find("\ndef record_feed")
    assert fn_start > 0
    fn_end = src.find("\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    forbidden = ["feed_child", "set_value(",
                 "scroll_to_bottom", "vadjustment"]
    for pat in forbidden:
        assert pat not in body, (
            f"record_feed touches VTE / scroll: {pat!r}"
        )


# ─── Defensive: scroll policies don't get reset by spawn flow ───────────


def test_spawn_ai_cli_does_not_reset_scroll_policies():
    """Pin: spawn_ai_cli (called per-provider) doesn't re-set
    scroll-on-output / scroll-on-keystroke. If it did, a respawn
    (e.g. provider switch) could reset to VTE defaults and break
    the scrollback contract."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def spawn_ai_cli")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    # No re-setting of scroll policy during respawn
    assert "set_scroll_on_output" not in body
    assert "set_scroll_on_keystroke" not in body
