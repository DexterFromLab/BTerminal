"""Failure mode: ollama daemon dies mid-session (#28 / #100, audit § 6.1 #1).

When `ollama serve` dies while an Aider tab is active, BT must:
  1. Surface a typed error from openai_compat (not a bare crash)
  2. Keep .aider.chat.history.md coherent (no truncated mid-token write)
  3. Render stats bar 'n/a' for cost (NOT NaN) — AiderStatsReader
     stays robust against partial logs
  4. Recover when `ollama serve` resumes (next prompt works again)

Three decision branches:
  (a) HTTP connection refused → typed APIError(URLError wrapper)
  (b) HTTP timeout mid-stream → no partial JSON in chat log
  (c) Reconnect after restart → chat log appends cleanly

VM-bound smoke (kill ollama, send ping, restart, send again) is
documented in tests/manual/README.md and runs through
tools/test_aider_real_model.sh when ollama is available. Headless
tests below cover the dispatch + reader robustness without a daemon.
"""
from __future__ import annotations

import json
import socket
import urllib.error
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock, patch

import pytest

from bterminal.openai_compat import (
    APIError,
    AuthError,
    RateLimitError,
    ServerError,
    call_chat_completion,
)
from bterminal.providers import get_registry, reset_registry
from bterminal.ui.stats import (
    AiderStatsReader,
    SessionStatsBar,
)
from bterminal.ui.stats.base import TokenStats


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


# ─── (a) HTTP connection refused → APIError ──────────────────────────────


def test_connection_refused_raises_api_error():
    """When ollama daemon isn't accepting connections, urllib raises
    URLError(ConnectionRefusedError); openai_compat wraps it as
    APIError with a 'Transport error' message (no status code).
    Aider sessions calling openai_compat see this and can show a
    user-friendly toast."""
    refused = urllib.error.URLError(
        ConnectionRefusedError(111, "Connection refused"))

    with patch("urllib.request.urlopen", side_effect=refused):
        with pytest.raises(APIError) as exc_info:
            call_chat_completion(
                base_url="http://localhost:11434/v1",
                api_key="dummy",
                model="qwen2.5-coder:0.5b",
                messages=[{"role": "user", "content": "ping"}],
            )
    err = exc_info.value
    assert "Transport error" in str(err)
    assert "localhost:11434" in str(err)
    # Bare APIError (not subclass) — caller distinguishes from
    # AuthError/RateLimitError to know it's a transport issue.
    assert not isinstance(err, (AuthError, RateLimitError, ServerError))


def test_connection_refused_does_not_leak_partial_response():
    """Defensive: when urlopen raises before any read(), the error
    body must be empty (not None, not a stale buffer from a prior
    call). Without this, retry logic could attribute prior content
    to the new failure."""
    refused = urllib.error.URLError(ConnectionRefusedError())
    with patch("urllib.request.urlopen", side_effect=refused):
        with pytest.raises(APIError) as exc_info:
            call_chat_completion(
                base_url="http://localhost:11434/v1",
                api_key="dummy", model="x",
                messages=[{"role": "user", "content": "x"}],
            )
    # body attr present but empty — caller can safely check
    err = exc_info.value
    assert getattr(err, "body", "") == ""


def test_dns_failure_also_surfaces_as_api_error():
    """nameserver unreachable / DNS failure → URLError(gaierror).
    Same path as connection refused — APIError. Catches the case
    where someone changes ollama host to a typo'd domain."""
    dns_fail = urllib.error.URLError(
        socket.gaierror(-2, "Name or service not known"))
    with patch("urllib.request.urlopen", side_effect=dns_fail):
        with pytest.raises(APIError):
            call_chat_completion(
                base_url="http://no-such-host.invalid:11434/v1",
                api_key="dummy", model="x",
                messages=[{"role": "user", "content": "x"}],
            )


# ─── (b) HTTP timeout mid-stream → no partial JSON ───────────────────────


def test_socket_timeout_during_request_raises_api_error():
    """Daemon accepted the connect() but stalled before responding.
    socket.timeout is an OSError → openai_compat wraps it. The
    surrounding aider invocation MUST NOT write a half-formed
    Tokens line into the chat history."""
    timeout_err = socket.timeout("timed out")
    with patch("urllib.request.urlopen", side_effect=timeout_err):
        with pytest.raises(APIError) as exc_info:
            call_chat_completion(
                base_url="http://localhost:11434/v1",
                api_key="dummy",
                model="qwen2.5-coder:0.5b",
                messages=[{"role": "user", "content": "ping"}],
                timeout=0.01,  # short — accelerates synthetic test
            )
    assert "Socket error" in str(exc_info.value) \
        or "Transport error" in str(exc_info.value)


def test_aider_stats_reader_handles_truncated_chat_history(tmp_path):
    """When ollama dies mid-write, aider's stdout flush may leave the
    chat history with a half-written `Tokens: 1.5k sent, ` line (no
    `received` part). AiderStatsReader.read_session_tokens must:
      - Not crash on the malformed line
      - Skip it gracefully (regex won't match → that pair contributes 0)
      - Still count any complete `Tokens:` lines that landed before"""
    truncated = (
        "# aider chat\n"
        "> Tokens: 1.5k sent, 234 received.\n"
        "\n"
        "#### First prompt\n"
        "Reply 1.\n"
        "\n"
        "#### Second prompt — ollama died here\n"
        "> Tokens: 2.5k sent, "  # truncated mid-line
    )
    (tmp_path / ".aider.chat.history.md").write_text(truncated)
    reader = AiderStatsReader(str(tmp_path))
    stats = reader.read_session_tokens()
    # Only the complete Tokens: line counts → 1500 input, 234 output
    assert stats.input == 1500
    assert stats.output == 234
    # responses: BOTH `#### ` markers count, even with truncated body
    assert stats.responses == 2


def test_aider_stats_reader_handles_empty_log_after_crash(tmp_path):
    """If aider crashed before writing any chat history (or wrote
    only the header), TokenStats stays at zeros. Widget renders
    `↑ 0 ↓ 0` — no NaN, no exception."""
    (tmp_path / ".aider.chat.history.md").write_text("# aider chat\n")
    reader = AiderStatsReader(str(tmp_path))
    stats = reader.read_session_tokens()
    assert stats.input == 0
    assert stats.output == 0
    assert stats.responses == 0
    # Cost reader returns 0.0 (Aider's cost_in_log=False)
    assert reader.read_session_cost(stats) == 0.0


def test_aider_stats_reader_handles_corrupted_utf8(tmp_path):
    """Mid-write daemon kill can leave a partial multi-byte UTF-8
    sequence at EOF. errors='replace' must keep the reader robust."""
    raw = (
        b"# aider chat\n"
        b"> Tokens: 1.0k sent, 100 received.\n"
        b"#### Reply\n"
        b"Status: \xc3"  # truncated 2-byte UTF-8 (was \xc3\xa9 = 'é')
    )
    log = tmp_path / ".aider.chat.history.md"
    log.write_bytes(raw)
    reader = AiderStatsReader(str(tmp_path))
    stats = reader.read_session_tokens()
    # Tokens line still parsed — partial UTF-8 doesn't break grep
    assert stats.input == 1000
    assert stats.output == 100


# ─── stats bar shows 'n/a' (not NaN) when daemon is dead ─────────────────


def test_widget_renders_n_a_with_partial_log(tmp_path):
    """SessionStatsBar._update with cost_unavailable=True (Aider
    contract) renders `💰 n/a` regardless of token state. Run
    against a partial chat log — the cost label must NOT become
    `💰 $nan` or crash."""
    log = tmp_path / ".aider.chat.history.md"
    # Realistic 'died mid-stream' state: 1 valid Tokens line, 1
    # incomplete user turn marker.
    log.write_text(
        "# aider chat\n"
        "> Tokens: 800 sent, 50 received.\n"
        "#### Prompt that didn't get a reply\n"
    )
    reader = AiderStatsReader(str(tmp_path))

    labels = {}
    for key in ["dur", "prompts", "resp", "tok_in", "tok_out", "cache",
                "cost", "tok_h", "model", "usage_5h", "usage_7d"]:
        labels[key] = SimpleNamespace(
            _text="",
            set_text=lambda txt, k=key: labels[k].__setattr__("_text", txt),
        )
    fake_self = SimpleNamespace(
        _reader=reader,
        _prompt_count=1,
        _hide_plan_usage=True,
        _cost_unavailable=True,
        _labels=labels,
    )
    SessionStatsBar._update(fake_self)

    assert labels["cost"]._text == "💰 n/a", (
        f"cost text drifted to {labels['cost']._text!r} (expected 'n/a')"
    )
    # No NaN in any rendered label
    for key, lbl in labels.items():
        assert "nan" not in lbl._text.lower(), (
            f"label {key} contains nan: {lbl._text!r}"
        )


def test_widget_does_not_divide_by_zero_when_no_duration(tmp_path):
    """tok/h calculation: dur < 1s → tok_h = 0. Without the dur > 1
    guard in widget._update, this would be ZeroDivisionError. Pin
    that guarded path stays — relevant when ollama dies before
    first_ts/last_ts get set in the reader."""
    (tmp_path / ".aider.chat.history.md").write_text(
        "# aider chat\n> Tokens: 100 sent, 50 received.\n#### x\n"
    )
    reader = AiderStatsReader(str(tmp_path))

    labels = {}
    for key in ["dur", "prompts", "resp", "tok_in", "tok_out", "cache",
                "cost", "tok_h", "model", "usage_5h", "usage_7d"]:
        labels[key] = SimpleNamespace(
            _text="",
            set_text=lambda txt, k=key: labels[k].__setattr__("_text", txt),
        )
    fake_self = SimpleNamespace(
        _reader=reader,
        _prompt_count=0,
        _hide_plan_usage=True,
        _cost_unavailable=True,
        _labels=labels,
    )
    # No exception
    SessionStatsBar._update(fake_self)
    assert "tok/h" in labels["tok_h"]._text


# ─── (c) Reconnect after `ollama serve` resumed ──────────────────────────


def test_call_chat_completion_succeeds_after_transient_failure():
    """Simulate the 'ollama died, then came back' recovery — first
    call raises APIError(URLError), second call succeeds. Pin that
    openai_compat doesn't carry stale state between invocations."""
    refused = urllib.error.URLError(ConnectionRefusedError())
    success_response_body = json.dumps({
        "choices": [{"message": {"content": "PONG"}}],
        "model": "qwen2.5-coder:0.5b",
        "usage": {"prompt_tokens": 10, "completion_tokens": 1},
    }).encode()
    success_resp = MagicMock()
    success_resp.read.return_value = success_response_body

    call_count = {"n": 0}

    def fake_urlopen(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise refused
        return success_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        # First call: fails (daemon dead)
        with pytest.raises(APIError):
            call_chat_completion(
                base_url="http://localhost:11434/v1",
                api_key="dummy",
                model="qwen2.5-coder:0.5b",
                messages=[{"role": "user", "content": "ping"}],
            )
        # Second call: succeeds (daemon back)
        text, raw = call_chat_completion(
            base_url="http://localhost:11434/v1",
            api_key="dummy",
            model="qwen2.5-coder:0.5b",
            messages=[{"role": "user", "content": "ping"}],
        )
    assert text == "PONG"
    assert call_count["n"] == 2


def test_aider_stats_reader_picks_up_new_tokens_after_resume(tmp_path):
    """After ollama resume, aider continues writing the same chat
    history file. Reader must pick up newly-appended Tokens lines
    on the next 5s tick — no caching of prior result."""
    log = tmp_path / ".aider.chat.history.md"
    # Phase 1: pre-crash state
    log.write_text(
        "# aider chat\n"
        "> Tokens: 500 sent, 100 received.\n"
        "#### First reply\n"
    )
    reader = AiderStatsReader(str(tmp_path))
    stats1 = reader.read_session_tokens()
    assert stats1.input == 500
    assert stats1.output == 100

    # Phase 2: aider resumes, appends new Tokens line
    log.write_text(log.read_text() + (
        "#### Second prompt after resume\n"
        "> Tokens: 800 sent, 200 received.\n"
        "Reply text.\n"
    ))
    stats2 = reader.read_session_tokens()
    # Sums across BOTH Tokens lines
    assert stats2.input == 500 + 800 == 1300
    assert stats2.output == 100 + 200 == 300


# ─── Capability-level robustness: Aider says cost is unavailable ─────────


def test_aider_capability_cost_in_log_remains_false_under_failure():
    """Even when ollama dies, the capability flag doesn't change —
    Aider's cost_in_log is False by definition (off-process LLM).
    The widget rendering 'n/a' is correct, not a temporary fallback.
    Pinned so a misguided 'try to compute partial cost' patch can't
    silently leak NaN."""
    aider = get_registry().get("aider")
    assert aider.capabilities.cost_in_log is False


def test_openai_compat_error_classes_are_disjoint():
    """AuthError / RateLimitError / ServerError / APIError form a
    clean hierarchy — caller can `except APIError` to catch all,
    or branch on subclass for specific recovery (only ServerError
    benefits from retry; AuthError doesn't). Pin that
    URLError/socket.timeout always become bare APIError, never the
    subclasses, so retry logic doesn't loop on persistent transport
    failures."""
    refused = urllib.error.URLError(ConnectionRefusedError())
    with patch("urllib.request.urlopen", side_effect=refused):
        with pytest.raises(APIError) as exc_info:
            call_chat_completion(
                base_url="http://localhost:11434/v1",
                api_key="dummy", model="x",
                messages=[{"role": "user", "content": "x"}],
            )
    err = exc_info.value
    # NOT one of the HTTP-status subclasses
    assert type(err) is APIError, (
        f"transport failure became {type(err).__name__} — would "
        f"trigger wrong recovery path"
    )


# ─── End-to-end stub: the recovery sequence as a single test ─────────────


def test_full_disconnect_recovery_flow_produces_coherent_log(tmp_path):
    """Synthesizes the full failure → recovery cycle:
      1. Pre-crash: aider writes 1 valid Tokens line + 1 user turn
      2. Daemon dies: chat history truncated mid-line (no further
         writes)
      3. Reader reads the truncated state — returns valid TokenStats
         (skips the malformed line)
      4. Daemon resumes: aider appends a complete Tokens line
      5. Reader sees the new totals on next tick

    Pins the full robustness contract: at every snapshot, the
    reader returns a sane TokenStats — no exception, no NaN, no
    stale data."""
    log = tmp_path / ".aider.chat.history.md"
    reader = AiderStatsReader(str(tmp_path))

    # 1+2. Pre-crash + truncated state
    log.write_text(
        "# aider chat\n"
        "> Tokens: 1.0k sent, 200 received.\n"
        "#### Prompt 1\nReply 1.\n"
        "#### Prompt 2 (interrupted)\n"
        "> Tokens: 1.5k sent, "  # truncated
    )
    stats_during_crash = reader.read_session_tokens()
    assert stats_during_crash.input == 1000
    assert stats_during_crash.output == 200
    assert stats_during_crash.responses == 2  # both `#### ` markers

    # 4. Daemon resumes — append a complete reply with new Tokens line.
    # Real aider would NOT just patch the truncated line; it starts a
    # fresh chat block on retry. Mirror that with a clean append.
    log.write_text(log.read_text() + (
        "(connection lost — retrying)\n"
        "> Tokens: 800 sent, 100 received.\n"
        "Recovered reply.\n"
    ))
    stats_after_resume = reader.read_session_tokens()
    assert stats_after_resume.input == 1000 + 800
    assert stats_after_resume.output == 200 + 100
    # Cost stays at 0.0 (capability invariant) — no NaN
    assert reader.read_session_cost(stats_after_resume) == 0.0
