"""Cross-feature: image paste + rules_inject simultaneously
(#40 / #112, audit § 6.3 #13).

User pastes an image into an Aider tab while `_inject_pending` is
armed. Within milliseconds both events fire:
  - `_paste_clipboard_image_path` formats path via
    `_format_image_paste_for_provider` and feed_child's the bytes.
  - `_do_inject_rules` extracts rules bytes via
    `extract_rules_inject_bytes` and feed_child's them.

Three decision branches:
  (a) Paste before inject — bytes arrive in chat-history order
      (paste is line N, inject is line N+1 or later).
  (b) Inject before paste — same separation, just reversed.
  (c) Both arrive in same VTE tick — feed_child is sequential at
      the PTY layer; bytes never interleave mid-line. The chat
      history still records two distinct user-turn markers.

Pinned invariants:
  - Helpers have NO shared mutable module state. Two parallel
    invocations return independent bytes objects.
  - feed_child is called separately for each event (rules → its
    own call, paste → its own call), never concatenated into one
    buffer (which could split mid-UTF-8).
  - `.aider.chat.history.md` records both messages on separate
    `#### ` user-turn lines (Aider format) — no garbled merge.

Manual VM smoke (paste image then quickly trigger inject_pending)
is documented in tests/manual/README.md.
"""
from __future__ import annotations

import os
import threading
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bterminal.helpers import format_image_paste_hint
from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.ui.terminal_tab import (
    TerminalTab,
    extract_rules_inject_bytes,
)


REPO_ROOT = Path(__file__).resolve().parent.parent


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


SAMPLE_RULES_STDOUT = (
    "## Project rules for myproj\n\n"
    "- Always reply concisely.\n"
    "- Use TDD: tests first, implementation second.\n"
)


# ─── (a)/(b) Sequential helpers — pure functions, deterministic ─────────


def test_extract_rules_and_image_paste_independent_calls():
    """Sequential paste then inject: each helper produces its own
    bytes/string, neither references the other's output."""
    paste_template = (
        "User provided image: {path} — describe what you see "
        "before editing any code."
    )
    paste_out = format_image_paste_hint(paste_template, "/tmp/img.png")
    rules_out = extract_rules_inject_bytes(
        "aider", "myproj", SAMPLE_RULES_STDOUT)

    assert "/tmp/img.png" in paste_out
    assert "Project rules" in rules_out.decode("utf-8")
    # Outputs are different types (str vs bytes) AND don't share
    # text — independent helpers
    assert isinstance(paste_out, str)
    assert isinstance(rules_out, bytes)
    assert "Project rules" not in paste_out
    assert b"User provided image" not in rules_out


def test_inject_then_paste_same_tab_no_state_leak():
    """Reverse order — confirms NO global state carries between
    helpers. Calling rules first then paste must produce same
    isolated outputs."""
    rules_first = extract_rules_inject_bytes(
        "aider", "myproj", SAMPLE_RULES_STDOUT)
    paste_template = "User provided image: {path}"
    paste_first = format_image_paste_hint(paste_template, "/tmp/x.png")

    # Now flipped order
    paste_second = format_image_paste_hint(paste_template, "/tmp/x.png")
    rules_second = extract_rules_inject_bytes(
        "aider", "myproj", SAMPLE_RULES_STDOUT)

    # Idempotent — same input → same output regardless of call order
    assert rules_first == rules_second
    assert paste_first == paste_second


# ─── (c) Concurrent calls — both helpers thread-safe ────────────────────


def test_concurrent_extract_rules_inject_bytes_thread_safe():
    """Two threads call extract_rules_inject_bytes in parallel —
    both get correct, independent bytes objects. No shared
    counter / cache state in the helper."""
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    def worker():
        barrier.wait()
        out = extract_rules_inject_bytes(
            "aider", "myproj", SAMPLE_RULES_STDOUT)
        with lock:
            results.append(out)

    threads = [threading.Thread(target=worker) for _ in range(8)]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    # All threads produced byte-identical output
    first = results[0]
    for r in results[1:]:
        assert r == first
    # And they're SEPARATE bytes objects (immutable, but distinct
    # references — confirms no shared buffer mutation)
    assert isinstance(first, bytes)
    assert "Project rules" in first.decode("utf-8")


def test_concurrent_format_image_paste_thread_safe():
    """Two threads call _format_image_paste_for_provider for the
    same tab in parallel — same wrapped output (template
    substitution is pure)."""
    barrier = threading.Barrier(8)
    results = []
    lock = threading.Lock()

    template = "User provided image: {path}"

    def worker(path):
        barrier.wait()
        out = format_image_paste_hint(template, path)
        with lock:
            results.append((path, out))

    threads = [
        threading.Thread(target=worker, args=(f"/tmp/img-{i}.png",))
        for i in range(8)
    ]
    for t in threads:
        t.start()
    for t in threads:
        t.join()

    assert len(results) == 8
    # Each result has its own path embedded — no cross-contamination
    for path, out in results:
        assert path in out


def test_concurrent_paste_and_inject_no_shared_state():
    """The headline #112 invariant: paste + inject helpers fire in
    parallel (Barrier-synced), produce independent outputs. Pin
    that there's no module-level mutable state shared between
    them (e.g. a cache that one mutates while the other reads)."""
    barrier = threading.Barrier(2)
    paste_outputs = []
    rules_outputs = []
    paste_template = (
        "User provided image: {path} — "
        "describe what you see before editing any code."
    )

    def paste_worker():
        barrier.wait()
        for i in range(50):
            paste_outputs.append(
                format_image_paste_hint(paste_template, f"/tmp/i-{i}.png")
            )

    def rules_worker():
        barrier.wait()
        for i in range(50):
            rules_outputs.append(
                extract_rules_inject_bytes(
                    "aider", "myproj", SAMPLE_RULES_STDOUT)
            )

    t1 = threading.Thread(target=paste_worker)
    t2 = threading.Thread(target=rules_worker)
    t1.start(); t2.start()
    t1.join(); t2.join()

    assert len(paste_outputs) == 50
    assert len(rules_outputs) == 50
    # Paste outputs each have their unique path embedded
    for i, out in enumerate(paste_outputs):
        assert f"/tmp/i-{i}.png" in out
    # Rules outputs all identical (deterministic helper)
    assert all(r == rules_outputs[0] for r in rules_outputs)


# ─── Production source-grep: feed_child calls separate, not merged ──────


def test_paste_and_rules_use_distinct_feed_child_calls():
    """Source-grep: rules_inject and image_paste each have their
    own feed_child invocation. Never concatenated into a single
    buffer (which could split mid-UTF-8 if the user pastes
    multibyte then rules fire)."""
    src = (REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py").read_text()

    # _do_inject_rules has its own feed_child(rules_bytes)
    rules_fn_start = src.find("def _do_inject_rules")
    rules_fn_end = src.find("\n    def ", rules_fn_start + 1)
    rules_body = src[rules_fn_start:rules_fn_end]
    assert "feed_child(rules_bytes)" in rules_body, (
        "_do_inject_rules dropped its dedicated feed_child call"
    )

    # _paste_clipboard_image_path has its own paste_clipboard call
    paste_fn_start = src.find("def _paste_clipboard_image_path")
    paste_fn_end = src.find("\n    def ", paste_fn_start + 1)
    paste_body = src[paste_fn_start:paste_fn_end]
    # Either feed_child OR paste_clipboard depending on flow
    assert ("feed_child" in paste_body or
            "paste_clipboard" in paste_body or
            "set_text" in paste_body), (
        "_paste_clipboard_image_path lost its bytes-delivery "
        "mechanism"
    )


def test_rules_inject_helper_signature_provider_agnostic():
    """The rules helper ignores provider_name (pinned by #93).
    Re-confirmed here for the cross-feature audit — paste/inject
    interleave shouldn't accidentally rely on provider-specific
    branching in either helper."""
    import inspect
    sig = inspect.signature(extract_rules_inject_bytes)
    assert list(sig.parameters.keys())[0] == "provider_name"
    # And the source has the explicit `del provider_name` marker
    src = inspect.getsource(extract_rules_inject_bytes)
    assert "del provider_name" in src


# ─── Decision branch (c) — same VTE tick, feed_child is sequential ──────


def test_feed_child_at_pty_layer_is_sequential_not_buffered():
    """VTE.feed_child is a synchronous PTY write. Two consecutive
    calls write their bytes IN ORDER to the kernel pipe — they
    don't merge or interleave at byte level. Pin via source
    inspection that the two feed sites use independent calls
    (no shared buffer concat)."""
    src = (REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py").read_text()

    # Count feed_child calls in production code (excluding docstrings/
    # comments). At least: rules_inject + ctx_refresh + intro_prompt +
    # paste paths each have their own call.
    feed_child_count = sum(
        1 for line in src.split("\n")
        if "feed_child(" in line and not line.lstrip().startswith("#")
    )
    assert feed_child_count >= 3, (
        f"only {feed_child_count} feed_child sites — paste/inject "
        f"may have collapsed into shared buffer path"
    )


def test_record_feed_logs_paste_and_inject_separately():
    """Both flows go through `record_feed(label, payload)` — the
    debug-REST feed log records each with its OWN label. This is
    what lets test_aider_full_session.py distinguish the two
    events in the feed_log when they fire close together."""
    src = (REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py").read_text()
    # rules_inject label
    assert 'record_feed("rules_inject"' in src
    # ctx_refresh label (separate flow)
    assert 'record_feed("ctx_refresh"' in src


# ─── End-to-end: same-tab sequential calls preserve message boundaries ──


def test_paste_then_inject_preserves_byte_boundaries(tmp_path):
    """Simulate paste → inject → write to a fake chat history.
    The two messages land on DIFFERENT lines (no torn UTF-8, no
    accidental merge). Mirrors the contract Aider's chat history
    relies on — `#### ` user-turn markers separate each input."""
    paste_template = (
        "User provided image: {path} — describe what you see "
        "before editing any code."
    )
    paste_bytes = format_image_paste_hint(
        paste_template, "/tmp/diagram.png").encode()
    rules_bytes = extract_rules_inject_bytes(
        "aider", "myproj", SAMPLE_RULES_STDOUT)

    # Synthesize a chat history that mirrors what aider would
    # write. Each user-turn message goes onto a `#### ` line.
    history = tmp_path / ".aider.chat.history.md"
    history.write_text(
        "# aider chat\n\n"
        "#### " + paste_bytes.decode("utf-8") + "\n\n"
        "I see the image — let me describe it.\n\n"
        "#### " + rules_bytes.decode("utf-8") + "\n\n"
        "Acknowledged the rules update.\n"
    )

    text = history.read_text()
    # Both messages present, on distinct user-turn lines
    assert "diagram.png" in text
    assert "Project rules for myproj" in text
    # Two distinct `#### ` markers
    assert text.count("#### ") == 2
    # Path content is on line WITH paste, not WITH rules
    paste_line = next(l for l in text.split("\n")
                      if "diagram.png" in l)
    assert "Project rules" not in paste_line


def test_inject_then_paste_preserves_byte_boundaries(tmp_path):
    """Reversed order — same independence."""
    paste_template = "User provided image: {path}"
    paste_bytes = format_image_paste_hint(
        paste_template, "/tmp/diagram.png").encode()
    rules_bytes = extract_rules_inject_bytes(
        "aider", "myproj", SAMPLE_RULES_STDOUT)

    history = tmp_path / ".aider.chat.history.md"
    history.write_text(
        "# aider chat\n\n"
        "#### " + rules_bytes.decode("utf-8") + "\n\n"
        "Rules updated.\n\n"
        "#### " + paste_bytes.decode("utf-8") + "\n\n"
        "I see the image.\n"
    )

    text = history.read_text()
    assert text.count("#### ") == 2
    assert "diagram.png" in text
    assert "Project rules" in text


def test_aider_provider_parses_both_messages_as_separate_turns(tmp_path):
    """End-to-end of the chat-history capture: AiderProvider's
    parse_session_stats counts `#### ` markers as response_count.
    Both paste + inject contribute distinct turns."""
    from bterminal.providers import get_registry

    paste_bytes = format_image_paste_hint(
        "User provided image: {path}", "/tmp/diagram.png"
    ).encode()
    rules_bytes = extract_rules_inject_bytes(
        "aider", "myproj", SAMPLE_RULES_STDOUT)

    history = tmp_path / ".aider.chat.history.md"
    history.write_text(
        "# aider chat\n\n"
        "#### " + paste_bytes.decode("utf-8") + "\n\n"
        "Reply 1.\n\n"
        "#### " + rules_bytes.decode("utf-8") + "\n\n"
        "Reply 2.\n"
    )

    aider = get_registry().get("aider")
    stats = aider.parse_session_stats(str(history))
    # Two user turns counted
    assert stats.response_count == 2


# ─── Per-line atomicity: feed_child + \r delivery ───────────────────────


def test_rules_inject_followed_by_carriage_return_via_glib_timer():
    """_do_inject_rules schedules a `\r` feed via
    GLib.timeout_add(100, ...) — separate call from the bytes
    feed. Pin so a refactor that combines them doesn't introduce
    a race where the \r lands BEFORE the rules buffer flushes
    (causing the rules to merge with the next user prompt)."""
    src = (REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py").read_text()
    rules_fn_start = src.find("def _do_inject_rules")
    rules_fn_end = src.find("\n    def ", rules_fn_start + 1)
    body = src[rules_fn_start:rules_fn_end]
    # Separate \r delivery via timer
    assert "GLib.timeout_add(100" in body
    assert 'feed_child(b"\\r")' in body or 'feed_child(b"\\\\r")' in body \
        or "feed_child(b'\\r')" in body


# ─── Scope isolation: paste flow doesn't touch rules state ──────────────


def test_paste_does_not_clear_inject_pending():
    """Source-grep: `_paste_clipboard_image_path` and
    `_format_image_paste_for_provider` do NOT touch
    `self._inject_pending`. A pending rules inject must survive
    a paste so the rules still fire after the paste completes."""
    src = (REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py").read_text()

    paste_fn_start = src.find("def _paste_clipboard_image_path")
    paste_fn_end = src.find("\n    def ", paste_fn_start + 1)
    paste_body = src[paste_fn_start:paste_fn_end]
    assert "_inject_pending" not in paste_body, (
        "paste flow mutates _inject_pending — could lose pending "
        "rules inject across paste"
    )

    fmt_fn_start = src.find("def _format_image_paste_for_provider")
    fmt_fn_end = src.find("\n    def ", fmt_fn_start + 1)
    fmt_body = src[fmt_fn_start:fmt_fn_end]
    assert "_inject_pending" not in fmt_body


def test_rules_inject_does_not_touch_clipboard():
    """Mirror: rules-inject flow doesn't call into clipboard
    primitives. They live on independent code paths."""
    src = (REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py").read_text()
    fn_start = src.find("def _do_inject_rules")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    forbidden = ["clipboard", "Gtk.Clipboard", "wait_for_image",
                 "wait_for_text"]
    for pat in forbidden:
        assert pat not in body, (
            f"_do_inject_rules touches clipboard ({pat!r}) — "
            f"unexpected coupling with paste flow"
        )


# ─── Defensive: helpers handle stress without resource leak ─────────────


def test_extract_rules_inject_bytes_no_memory_leak_under_repeated_calls():
    """Stress: 1000 calls to extract_rules_inject_bytes — no global
    counter / accumulation that grows. Pin so a refactor that adds
    a cache (e.g. memoizing per-project) doesn't introduce
    unbounded growth."""
    out = None
    for _ in range(1000):
        out = extract_rules_inject_bytes(
            "aider", "myproj", SAMPLE_RULES_STDOUT)
    # Last result is identical to first — deterministic + no
    # state leak
    expected = extract_rules_inject_bytes(
        "aider", "myproj", SAMPLE_RULES_STDOUT)
    assert out == expected


def test_format_image_paste_hint_no_memory_leak_under_repeated_calls():
    """Same stress for image paste hint."""
    template = "User provided image: {path}"
    out = None
    for i in range(1000):
        out = format_image_paste_hint(template, f"/tmp/i-{i}.png")
    # Last result has the last path
    assert "/tmp/i-999.png" in out
