"""E2E test for BUG#10 — Pull failed dialog leaks raw ANSI escape
codes from ollama's progress animation.

User report (manual QA, 2026-05-10): with the model name 'ddd' (made
up), the resulting Pull failed dialog shows:

    ?2026h ?25l ?1Gpulling manifest ?? ?K ?25h ?2026l ?2026h ?25l
    ?1Gpulling manifest ?? ?K ?25h ?2026l ?2026h ?25l ?1Gpulling
    manifest .. ?K ?25h ?2026l
    [...several more lines of garbled cursor codes...]
    Error: pull model manifest: file does not exist

The actual error is the last line; everything above is `ollama pull`'s
TTY progress animation re-rendered as text after capture. These are:
  CSI ?2026h / ?2026l    — synchronized output begin/end
  CSI ?25l / ?25h        — hide/show cursor
  CSI 1G                 — move cursor to column 1
  CSI K                  — erase line

`bterminal/ollama_client.py:pull_model` runs `subprocess.run([
"ollama", "pull", name], capture_output=True, text=True)` and returns
`result.stderr or result.stdout` raw — no ANSI strip. Then
`_pull_model_blocking` packs this into `Gtk.MessageDialog.secondary_text`
which renders the escape codes as `?2026h` etc. (Pango doesn't
interpret terminal control sequences, but it doesn't strip them
either — they appear as literal text where the CSI byte (0x9B or
ESC[) is invalid UTF-8, hence the `?` substitution.)

Fix: strip ANSI before returning. A 5-line regex is enough:
`re.sub(r'\x1b\[[\d;?]*[a-zA-Z]', '', output)`.

The test pins both layers:
  (a) Static: ollama_client has an ANSI-stripping helper, and
      pull_model uses it on the captured output.
  (b) Behavioural: feed a realistic ANSI-loaded output through
      pull_model's normalisation path, assert no escape sequences
      survive.
"""
from __future__ import annotations

import os
import re
import subprocess
import shutil
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OLLAMA_CLIENT_PY = REPO_ROOT / "bterminal" / "ollama_client.py"


# Realistic ollama failure output with ANSI animation codes,
# pulled from `_e2e_live_monitor` capture of a real failure on
# 2026-05-10. Each `\x1b[?2026h` block is one progress redraw.
ANSI_FAILURE_FIXTURE = (
    "\x1b[?2026h\x1b[?25l\x1b[1Gpulling manifest \x1b[K\x1b[?25h\x1b[?2026l"
    "\x1b[?2026h\x1b[?25l\x1b[1Gpulling manifest \x1b[K\x1b[?25h\x1b[?2026l"
    "\x1b[?2026h\x1b[?25l\x1b[1Gpulling manifest ..\x1b[K\x1b[?25h\x1b[?2026l"
    "\x1b[?2026h\x1b[?25l\x1b[1Gpulling manifest ..\x1b[K\x1b[?25h\x1b[?2026l"
    "\x1b[?2026h\x1b[?25l\x1b[1Gpulling manifest \x1b[K\x1b[?25h\x1b[?2026l"
    "\nError: pull model manifest: file does not exist\n"
)


# Common terminal escape sequence shapes that must never survive
# the formatter. We cover both the raw ESC byte and the `?2026`
# style stripped artifact users currently see.
ANSI_PATTERNS = [
    re.compile(r"\x1b\["),       # CSI introducer
    re.compile(r"\?2026[hl]"),   # synchronized output toggle
    re.compile(r"\?25[lh]"),     # cursor hide/show
    re.compile(r"\[1G"),         # column-1 cursor move
]


# ── Static checks ────────────────────────────────────────────────────────


def test_ollama_client_has_ansi_strip_helper():
    """Pin: a helper that strips ANSI escapes must exist in
    ollama_client (or imported into it). Without a helper to point
    pull_model at, the bug recurs at every refactor."""
    src = OLLAMA_CLIENT_PY.read_text(encoding="utf-8")
    # Accept any of these forms
    has_helper = any(token in src for token in (
        "_strip_ansi",
        "strip_ansi",
        # inline regex sub is OK if it's the standard pattern
        r"re.sub(r'\x1b",
        r're.sub(r"\x1b',
    ))
    assert has_helper, (
        f"`{OLLAMA_CLIENT_PY.name}` has no ANSI-strip helper or "
        f"inline strip. pull_model returns raw subprocess output, "
        f"so cursor-control codes from ollama's TTY progress leak "
        f"into the failure dialog."
    )


def test_pull_model_strips_ansi_before_return():
    """Pin: pull_model body must reference the strip helper between
    capturing subprocess output and returning. If the helper exists
    but pull_model doesn't call it, the bug still manifests."""
    src = OLLAMA_CLIENT_PY.read_text(encoding="utf-8")
    start = src.find("def pull_model")
    assert start > 0, "pull_model not found"
    end = src.find("\ndef ", start + 1)
    body = src[start:end] if end > start else src[start:]

    # Must reference the strip helper OR have an inline ANSI regex
    has_strip_call = any(token in body for token in (
        "_strip_ansi",
        "strip_ansi(",
        r"re.sub(r'\x1b",
        r're.sub(r"\x1b',
    ))
    assert has_strip_call, (
        f"pull_model does not strip ANSI before returning. Body:\n"
        f"{body[:600]}"
    )


# ── Behavioural: feed ANSI fixture through the strip path ───────────────


def test_strip_helper_removes_all_known_escape_patterns():
    """Pin: import the strip helper (whatever its name) and run it
    on the ANSI failure fixture. None of the known patterns may
    survive."""
    from bterminal import ollama_client

    strip = (
        getattr(ollama_client, "_strip_ansi", None)
        or getattr(ollama_client, "strip_ansi", None)
    )
    if strip is None:
        pytest.skip("strip helper not implemented yet")

    cleaned = strip(ANSI_FAILURE_FIXTURE)
    surviving = []
    for pat in ANSI_PATTERNS:
        m = pat.search(cleaned)
        if m:
            surviving.append((pat.pattern, m.group(0)))
    assert not surviving, (
        f"ANSI patterns survived strip helper:\n  "
        + "\n  ".join(f"{p!r}: {s!r}" for p, s in surviving)
        + f"\n\nResulting text:\n{cleaned[:400]}"
    )


def test_pull_model_returns_clean_message_on_simulated_failure():
    """End-to-end: monkey-patch subprocess.run to return ollama's
    real ANSI-loaded failure output, call pull_model, assert the
    returned msg has no escape codes left."""
    from bterminal import ollama_client

    fake_completed = subprocess.CompletedProcess(
        args=["ollama", "pull", "doesnotexist"],
        returncode=1,
        stdout="",
        stderr=ANSI_FAILURE_FIXTURE,
    )
    with patch.object(ollama_client.subprocess, "run",
                      return_value=fake_completed), \
         patch.object(ollama_client, "is_cli_installed",
                      return_value=True):
        ok, msg = ollama_client.pull_model("doesnotexist")

    assert ok is False, "fake exit code 1 should produce ok=False"
    surviving = []
    for pat in ANSI_PATTERNS:
        m = pat.search(msg)
        if m:
            surviving.append((pat.pattern, m.group(0)))
    assert not surviving, (
        f"pull_model returned msg with ANSI residue.\n"
        f"Surviving patterns:\n  "
        + "\n  ".join(f"{p!r}: {s!r}" for p, s in surviving)
        + f"\n\nReturned msg:\n{msg!r}"
    )


def test_pull_model_preserves_meaningful_error_line():
    """Sanity: stripping must NOT also remove the actual error
    text. The `Error: pull model manifest: file does not exist`
    line must remain — it's what the user needs to act on."""
    from bterminal import ollama_client

    fake_completed = subprocess.CompletedProcess(
        args=["ollama", "pull", "doesnotexist"],
        returncode=1,
        stdout="",
        stderr=ANSI_FAILURE_FIXTURE,
    )
    with patch.object(ollama_client.subprocess, "run",
                      return_value=fake_completed), \
         patch.object(ollama_client, "is_cli_installed",
                      return_value=True):
        ok, msg = ollama_client.pull_model("doesnotexist")

    assert "Error: pull model manifest: file does not exist" in msg, (
        f"strip helper accidentally removed the meaningful error "
        f"line. Returned msg:\n{msg!r}"
    )
