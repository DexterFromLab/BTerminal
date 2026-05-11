"""Pin tests for BUG#27 — aider intro_prompt delivery via PTY feed.

Pre-existing gap: AiderProvider's docstring declared
'intro_prompt=true (via PTY feed_child)' but no call site existed.
Claude/Copilot deliver the prompt as a positional argv element;
aider has no `--message-init`, so without a feed_child call the
intro prompt was simply lost on every aider spawn — verified by
smoke-logs/bug27-fix/01b (before) vs 02b (after).

Fix shape:
  - AIProvider.inject_intro_prompt(terminal, intro_prompt) — new
    overridable method on the base class, default no-op.
  - AiderProvider.inject_intro_prompt — schedules a 2000 ms
    GLib.timeout_add that calls terminal.feed_child(intro + b'\\n').
  - TerminalTab.spawn_ai_cli — calls provider.inject_intro_prompt
    after terminal.spawn_async (so Claude/Copilot are unchanged,
    aider gets the delayed feed).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

from bterminal.providers import (  # noqa: E402
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


def _aider_provider():
    cfg = load_providers_config()
    reg = ProviderRegistry(cfg)
    return reg.get("aider")


def _claude_provider():
    cfg = load_providers_config()
    reg = ProviderRegistry(cfg)
    return reg.get("claude")


# ─── Base class contract ─────────────────────────────────────────────────


def test_base_inject_intro_prompt_is_no_op_for_claude():
    """Pin: Claude/Copilot deliver intro_prompt via argv (build_argv
    appends it as the trailing positional). The base AIProvider's
    inject_intro_prompt must stay a no-op so we don't double-send:
    once via argv, once via feed_child. Reproduces the desired
    quietness for providers that already had a working delivery path."""
    provider = _claude_provider()
    fake_terminal = MagicMock()
    # Default base implementation returns None and touches nothing.
    assert provider.inject_intro_prompt(fake_terminal, "hello") is None
    fake_terminal.feed_child.assert_not_called()


def test_base_inject_intro_prompt_no_op_handles_empty_string():
    """Pin: empty intro_prompt → still no-op, no exception, no feed.
    spawn_ai_cli guards this too but the base class must be robust
    against direct calls (e.g. from tests / REST endpoints)."""
    provider = _claude_provider()
    fake_terminal = MagicMock()
    provider.inject_intro_prompt(fake_terminal, "")
    fake_terminal.feed_child.assert_not_called()


# ─── Aider override behaviour ────────────────────────────────────────────


def test_aider_inject_schedules_glib_timeout_with_intro_bytes():
    """Pin: aider's inject schedules a GLib.timeout_add(2000, fn) with
    a callback that feeds intro_prompt.encode('utf-8') + b'\\n'. We
    capture the scheduled fn and invoke it directly so the test
    doesn't need a running GLib main loop."""
    provider = _aider_provider()
    fake_terminal = MagicMock()
    intro = "BT welcome line\nplugin context"

    captured = {}

    def _fake_timeout_add(delay_ms, fn, *a, **kw):
        captured["delay"] = delay_ms
        captured["fn"] = fn
        return 12345  # GLib returns a non-zero source id

    with patch("gi.repository.GLib.timeout_add", side_effect=_fake_timeout_add):
        provider.inject_intro_prompt(fake_terminal, intro)

    assert captured["delay"] >= 1000, (
        "delay should be ≥1s to let aider's banner settle before feed; "
        f"got {captured.get('delay')}"
    )
    # The scheduled callback must call feed_child with intro + newline.
    fake_terminal.feed_child.assert_not_called()  # not yet, just scheduled
    captured["fn"]()
    fake_terminal.feed_child.assert_called_once()
    sent = fake_terminal.feed_child.call_args.args[0]
    assert sent.endswith(b"\n"), "trailing newline triggers aider's submit"
    assert intro.encode("utf-8") in sent


def test_aider_inject_empty_prompt_does_not_schedule():
    """Pin: empty intro_prompt → no GLib timer scheduled. Without this
    guard every aider spawn would queue a no-op feed in the GLib loop."""
    provider = _aider_provider()
    fake_terminal = MagicMock()

    with patch("gi.repository.GLib.timeout_add") as mock_to:
        provider.inject_intro_prompt(fake_terminal, "")
    mock_to.assert_not_called()
    fake_terminal.feed_child.assert_not_called()


def test_aider_inject_callback_does_not_repeat_timer():
    """Pin: the scheduled callback must return False so GLib doesn't
    fire it again on every interval. A True return would re-send
    intro_prompt every 2s forever — flooding aider with noise."""
    provider = _aider_provider()
    fake_terminal = MagicMock()
    captured = {}

    def _fake_to(delay_ms, fn, *a, **kw):
        captured["fn"] = fn
        return 1

    with patch("gi.repository.GLib.timeout_add", side_effect=_fake_to):
        provider.inject_intro_prompt(fake_terminal, "hi")
    result = captured["fn"]()
    assert result is False


def test_aider_inject_callback_swallows_feed_child_errors():
    """Pin: feed_child can raise (closed terminal, race with tab destroy).
    The timer callback must NOT propagate — it runs inside the GLib main
    loop, an unhandled exception would crash BT. Verified by feed_child
    raising, return value still False."""
    provider = _aider_provider()
    fake_terminal = MagicMock()
    fake_terminal.feed_child.side_effect = RuntimeError("dead pty")
    captured = {}

    def _fake_to(delay_ms, fn, *a, **kw):
        captured["fn"] = fn
        return 1

    with patch("gi.repository.GLib.timeout_add", side_effect=_fake_to):
        provider.inject_intro_prompt(fake_terminal, "hi")
    # Should not propagate the RuntimeError.
    assert captured["fn"]() is False


def test_aider_inject_records_feed_log_entry():
    """Pin: pin tests / debug-rest consumers tail /api/debug/feed_log
    for delivery proof. The aider override must call
    bterminal.debug_rest.record_feed with a distinct tag so observers
    can distinguish the post-spawn feed from the pre-spawn record_feed
    call (which has tag='intro_prompt')."""
    provider = _aider_provider()
    fake_terminal = MagicMock()
    captured = {}

    def _fake_to(delay_ms, fn, *a, **kw):
        captured["fn"] = fn
        return 1

    with patch("gi.repository.GLib.timeout_add", side_effect=_fake_to), \
         patch("bterminal.debug_rest.record_feed") as mock_record:
        provider.inject_intro_prompt(fake_terminal, "hello")
        captured["fn"]()  # fire the delayed feed

    assert mock_record.called
    tag = mock_record.call_args.args[0]
    assert tag == "intro_prompt_aider", (
        f"feed_log tag should be 'intro_prompt_aider', got {tag!r}"
    )
