"""E2E test for BUG#11 — Pull failed dialog dumps full stdout
instead of mapping known ollama errors to user-friendly text.

User report (manual QA, 2026-05-10): pulling 'ddd' produced a Pull
failed dialog with hundreds of characters of progress noise, ending
with `Error: pull model manifest: file does not exist`. The user
needs to scroll through repeated `?2026h ?25l ?1Gpulling manifest`
chunks (BUG#10) to find the meaningful line.

Even after stripping ANSI (BUG#10), the raw error string is still
not actionable for non-technical users. "pull model manifest: file
does not exist" doesn't say WHICH model failed or what to do next.
The fix maps known ollama error patterns to user-friendly Polish/
English messages that:
  - Mention the model name (so the user knows which input failed)
  - Suggest next action (check spelling, run daemon, free disk)
  - Stay under ~200 characters total

This test pins the contract:
  (a) A parsing helper exists (e.g. `_friendly_pull_error(stderr,
      model_name) -> str`)
  (b) For each known error pattern, the helper returns a short
      message containing the model name and an action hint
  (c) The integration with `pull_model` uses the helper before
      returning to the dialog
"""
from __future__ import annotations

import re
import subprocess
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OLLAMA_CLIENT_PY = REPO_ROOT / "bterminal" / "ollama_client.py"


# Realistic ollama error transcripts. Each pair: (stderr_fixture,
# (must_include_substrings, must_NOT_include_substrings))
ERROR_FIXTURES = {
    "model_not_found": (
        # Common case: typo or non-existent model name
        "pulling manifest \npulling manifest \npulling manifest .."
        "\nError: pull model manifest: file does not exist\n",
        # Friendly message must include model name + 'not found' or PL equivalent
        ("model_name_marker",
         ["not found", "nie istnieje", "doesn't exist", "nie znaleziono"]),
    ),
    "daemon_down": (
        "Error: Post \"http://127.0.0.1:11434/api/pull\": "
        "dial tcp 127.0.0.1:11434: connect: connection refused\n",
        ("daemon",
         ["daemon", "ollama serve", "uruchom", "running",
          "not running", "connection refused"]),
    ),
    "disk_full": (
        "pulling 8eb9b7a08323... 100%\n"
        "verifying sha256 digest\n"
        "Error: write /home/user/.ollama/models/blobs/sha256-...: "
        "no space left on device\n",
        ("disk",
         ["disk", "space", "miejsca", "no space"]),
    ),
}


# ── Static: helper exists + pull_model uses it ──────────────────────────


def test_friendly_error_helper_exists():
    """Pin: a function/method that turns raw ollama stderr into a
    user-friendly summary must exist somewhere in `ollama_client`.
    Names accepted: `_friendly_pull_error`, `friendly_pull_error`,
    `format_pull_error`, `_format_pull_error`."""
    src = OLLAMA_CLIENT_PY.read_text(encoding="utf-8")
    has_helper = any(name in src for name in (
        "_friendly_pull_error",
        "friendly_pull_error",
        "_format_pull_error",
        "format_pull_error",
        "_pull_error_message",
    ))
    assert has_helper, (
        f"`{OLLAMA_CLIENT_PY.name}` lacks a friendly-error helper. "
        f"Add one (suggested name: `_friendly_pull_error(stderr, "
        f"model_name) -> str`) that maps:\n"
        f"  - 'file does not exist' → 'Model X not found...'\n"
        f"  - 'connection refused' → 'Ollama daemon not running...'\n"
        f"  - 'no space left' → 'Disk full...'\n"
        f"and falls back to the last `Error:` line for unknown cases."
    )


def test_pull_model_uses_friendly_error_helper():
    """Pin: pull_model body must call the helper between capturing
    stderr and returning to the caller. Otherwise the dialog still
    sees the raw dump."""
    src = OLLAMA_CLIENT_PY.read_text(encoding="utf-8")
    start = src.find("def pull_model")
    assert start > 0
    end = src.find("\ndef ", start + 1)
    body = src[start:end] if end > start else src[start:]

    has_friendly_call = any(name in body for name in (
        "_friendly_pull_error", "friendly_pull_error",
        "_format_pull_error", "format_pull_error",
        "_pull_error_message",
    ))
    assert has_friendly_call, (
        f"pull_model does not call the friendly-error helper. "
        f"Body:\n{body[:600]}"
    )


# ── Behavioural: round-trip through pull_model with mocked stderr ────────


def _exercise_pull_model(stderr_text: str, model_name: str = "ddd"):
    """Run pull_model with subprocess + is_cli_installed mocked,
    return (ok, msg) tuple."""
    from bterminal import ollama_client

    fake = subprocess.CompletedProcess(
        args=["ollama", "pull", model_name],
        returncode=1,
        stdout="",
        stderr=stderr_text,
    )
    with patch.object(ollama_client.subprocess, "run",
                      return_value=fake), \
         patch.object(ollama_client, "is_cli_installed",
                      return_value=True):
        return ollama_client.pull_model(model_name)


def test_model_not_found_message_mentions_model_name_and_is_short():
    """End-to-end pin (the user-reported case): pulling a typo'd
    model name must produce a short, actionable message that
    includes the model name and a 'not found' / 'nie istnieje'
    indicator."""
    stderr_fixture, (_marker, ok_phrases) = ERROR_FIXTURES["model_not_found"]
    ok, msg = _exercise_pull_model(stderr_fixture, model_name="ddd")
    assert ok is False, "rc=1 should produce ok=False"

    # Length sanity — task acceptance threshold
    assert len(msg) < 200, (
        f"message too long ({len(msg)} chars). User saw a multi-line "
        f"dump. Trim to ~120 chars max for readability. "
        f"Got:\n{msg!r}"
    )

    # Must contain the model name so the user knows what failed
    assert "ddd" in msg, (
        f"message lacks the model name 'ddd' the user requested. "
        f"Got: {msg!r}"
    )

    # Must indicate not-found semantics
    msg_lower = msg.lower()
    has_phrase = any(p in msg_lower for p in ok_phrases)
    assert has_phrase, (
        f"message lacks any 'not found' / 'nie istnieje' indicator. "
        f"Expected one of {ok_phrases}.\n"
        f"Got: {msg!r}"
    )


def test_message_does_not_contain_full_progress_dump():
    """Pin: the friendly message must NOT echo the entire ollama
    progress stream. Specifically, no repeated 'pulling manifest'
    blocks should remain after parsing."""
    stderr_fixture, _ = ERROR_FIXTURES["model_not_found"]
    ok, msg = _exercise_pull_model(stderr_fixture, model_name="ddd")

    # 'pulling manifest' may appear ONCE as context, but not 3+
    # times (which is what the raw dump shows).
    occurrences = msg.count("pulling manifest")
    assert occurrences <= 1, (
        f"friendly message contains progress noise — "
        f"{occurrences} 'pulling manifest' occurrences. "
        f"Got:\n{msg!r}"
    )


def test_daemon_not_running_message_is_actionable():
    """Different error class: ollama daemon offline. Friendly
    message should hint at 'start daemon' or 'ollama serve'."""
    stderr_fixture, (_marker, ok_phrases) = ERROR_FIXTURES["daemon_down"]
    ok, msg = _exercise_pull_model(stderr_fixture, model_name="qwen2.5-coder:7b")

    assert len(msg) < 200, f"message too long: {len(msg)} chars"
    msg_lower = msg.lower()
    has_phrase = any(p in msg_lower for p in ok_phrases)
    assert has_phrase, (
        f"daemon-down message lacks daemon/start hint. Expected "
        f"one of {ok_phrases}. Got: {msg!r}"
    )


def test_disk_full_message_mentions_space():
    """Different error class: out-of-disk. Friendly message should
    mention 'space' / 'miejsca' / 'disk'."""
    stderr_fixture, (_marker, ok_phrases) = ERROR_FIXTURES["disk_full"]
    ok, msg = _exercise_pull_model(stderr_fixture, model_name="llama3.1:70b")

    assert len(msg) < 200, f"message too long: {len(msg)} chars"
    msg_lower = msg.lower()
    has_phrase = any(p in msg_lower for p in ok_phrases)
    assert has_phrase, (
        f"disk-full message lacks space/disk hint. Expected one of "
        f"{ok_phrases}. Got: {msg!r}"
    )


def test_unknown_error_falls_back_to_last_error_line():
    """For errors the helper doesn't recognise, the fallback should
    be the LAST `Error: ...` line — not the entire stdout dump."""
    weird = (
        "pulling manifest\n"
        "pulling manifest\n"
        "Error: undocumented oddball failure mode XYZ\n"
    )
    ok, msg = _exercise_pull_model(weird, model_name="some-model")
    assert len(msg) < 200, f"message too long: {len(msg)} chars"
    assert "undocumented oddball" in msg, (
        f"unknown error fallback didn't preserve the actual Error: "
        f"line. Got: {msg!r}"
    )
    # Must not include the 'pulling manifest' progress lines
    assert msg.count("pulling manifest") <= 1, (
        f"unknown-error fallback dumped progress noise: {msg!r}"
    )
