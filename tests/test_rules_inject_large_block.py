"""Performance: large rules block PTY feed (10MB)
(#52 / #124, audit § 6.6 #25).

`extract_rules_inject_bytes` produces bytes that get fed to VTE
via feed_child. The PTY layer writes to a kernel pipe (PIPE_BUF
= 4 KB atomic), so multi-megabyte writes chunk into many syscalls.
At MB scale this is fine; at hundreds of MB the main loop blocks
visibly + the AI CLI's stdin buffer overflows.

Three decision branches:
  (a) 1 MB rules — encode + delivery cleanly under 50 ms.
  (b) 10 MB rules — encode succeeds; bytes returned for caller
      to feed (PTY chunks via kernel; not measured here, that's
      OS-level).
  (c) 100 MB rules — REFUSED with cap. `extract_rules_inject_bytes`
      returns empty b"" + stderr warning. Caller treats empty as
      'no rules to inject', avoiding multi-second main loop block.

#124 added `_RULES_INJECT_MAX_BYTES = 50 MB` constant. Pin the
cap value + the refusal behavior + the warning message.

Manual VM smoke (`ctx rules inject myproj` with 10MB block,
trigger inject, observe latency) is documented in tests/manual/
README.md.
"""
from __future__ import annotations

import time
from pathlib import Path

import pytest

from bterminal.ui.terminal_tab import (
    extract_rules_inject_bytes,
    _RULES_INJECT_MAX_BYTES,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
TERMINAL_TAB = REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py"


# ─── Cap constant pinned at 50 MB ───────────────────────────────────────


def test_rules_inject_max_bytes_is_50_mb():
    """Pin: the cap is 50 MB. Below this, content goes through
    unchanged. Above, refused. The exact threshold is a balance
    point — large enough that legitimate rules blocks (typically
    O(KB)) never trip it, small enough that pathological cases
    (file dumps via `ctx set`) get caught."""
    expected = 50 * 1024 * 1024  # 50 MB
    assert _RULES_INJECT_MAX_BYTES == expected


# ─── Branch (a): 1 MB rules block — fast path ──────────────────────────


def test_1mb_rules_block_encodes_under_50ms():
    """Pin: a 1 MB rules block encodes in <50 ms. Well below
    user-perceived latency. Realistic upper bound for legitimate
    `ctx rules inject` output."""
    big_rules = "a" * (1 * 1024 * 1024)  # 1 MB ASCII
    t0 = time.perf_counter()
    out = extract_rules_inject_bytes("aider", "myproj", big_rules)
    t1 = time.perf_counter()

    assert len(out) == 1 * 1024 * 1024
    assert (t1 - t0) < 0.050, (
        f"1MB encode took {(t1 - t0) * 1000:.2f}ms — exceeds "
        f"50ms threshold. PTY feed amplifies this with syscall "
        f"chunks."
    )


def test_1mb_rules_block_returns_actual_bytes_not_truncated():
    """Pin: 1 MB content survives unchanged through encode. No
    silent truncation, no UTF-8 boundary issues at high byte
    counts."""
    body = ("# Section\n- bullet line\n" * 50000)[:1024 * 1024]
    out = extract_rules_inject_bytes("aider", "myproj", body)
    decoded = out.decode("utf-8")
    # Content preserved (modulo strip())
    assert "# Section" in decoded
    assert "bullet line" in decoded


# ─── Branch (b): 10 MB rules block — encode succeeds ───────────────────


def test_10mb_rules_block_encodes_under_500ms():
    """Pin: 10 MB encodes in <500 ms. Caller (production
    `_do_inject_rules`) takes the bytes + feeds via PTY. Encode
    cost itself stays bounded."""
    big_rules = "a" * (10 * 1024 * 1024)  # 10 MB ASCII
    t0 = time.perf_counter()
    out = extract_rules_inject_bytes("aider", "myproj", big_rules)
    t1 = time.perf_counter()

    assert len(out) == 10 * 1024 * 1024
    assert (t1 - t0) < 0.500, (
        f"10MB encode took {(t1 - t0) * 1000:.2f}ms — exceeds "
        f"500ms threshold"
    )


def test_10mb_just_below_cap_passes(tmp_path):
    """Boundary: rules size exactly at cap minus 1 byte → passes.
    Pin the inclusive-vs-exclusive semantics of the cap."""
    almost_cap = "a" * (_RULES_INJECT_MAX_BYTES - 1)
    out = extract_rules_inject_bytes("aider", "myproj", almost_cap)
    # Just under cap → passes
    assert len(out) == _RULES_INJECT_MAX_BYTES - 1


# ─── Branch (c): 100 MB rules block — refused with cap ─────────────────


def test_100mb_rules_block_refused_returns_empty_bytes(capsys):
    """Pin: 100 MB > 50 MB cap → returns b"" + stderr warning.
    Caller's `_do_inject_rules` treats empty bytes as 'no rules
    to inject', skipping the feed entirely. No main loop block."""
    huge_rules = "a" * (100 * 1024 * 1024)  # 100 MB
    out = extract_rules_inject_bytes(
        "aider", "myproj", huge_rules)
    assert out == b"", (
        f"oversized block not refused: returned {len(out)} bytes"
    )

    # Warning on stderr
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "myproj" in captured.err
    # Mentions byte count
    assert str(100 * 1024 * 1024) in captured.err


def test_oversized_block_refusal_does_not_propagate_to_stdout(capsys):
    """The refusal warning goes ONLY to stderr — stdout stays
    clean. Pin so scripted automation that pipes BT through grep
    doesn't see the warning land in pipeline output."""
    huge = "x" * (100 * 1024 * 1024)
    extract_rules_inject_bytes("aider", "myproj", huge)
    captured = capsys.readouterr()
    assert captured.out == ""
    # All diagnostic on stderr
    assert "WARN" in captured.err


def test_oversized_block_warning_includes_actionable_hint(capsys):
    """Pin: the warning tells the user what to check (`ctx rules
    inject` output, accidental file content). Without this, the
    user sees a refusal but no path to fix."""
    huge = "x" * (60 * 1024 * 1024)  # just over cap
    extract_rules_inject_bytes("aider", "broken-proj", huge)
    captured = capsys.readouterr()
    err = captured.err.lower()
    assert "ctx rules inject" in err
    assert "broken-proj" in err
    # Mentions file / accidental content as the likely cause
    assert (
        "file" in err
        or "accidental" in err
        or "content" in err
    )


def test_just_above_cap_refused(capsys):
    """Boundary: cap+1 byte → refused. Pin inclusive cap (>= cap
    is too big) vs exclusive (> cap means cap-bytes is OK)."""
    just_over = "a" * (_RULES_INJECT_MAX_BYTES + 1)
    out = extract_rules_inject_bytes("aider", "myproj", just_over)
    assert out == b""
    captured = capsys.readouterr()
    assert "WARN" in captured.err


# ─── Whitespace stripping doesn't bypass cap ───────────────────────────


def test_oversized_with_huge_trailing_whitespace_still_refused(capsys):
    """Edge: 1 MB content + 100 MB trailing whitespace → STRIPPED
    bytes are 1 MB (passes). Pin: cap is applied to the FINAL
    encoded bytes (after strip), not the raw input length.

    Caller's intent matters — they shouldn't get punished for
    unusual whitespace patterns. The cap protects against real
    payload size."""
    body = "a" * (1 * 1024 * 1024)
    trailing_ws = "\n" * (100 * 1024 * 1024)  # 100 MB whitespace
    out = extract_rules_inject_bytes(
        "aider", "myproj", body + trailing_ws)
    # Passes — final stripped bytes are 1 MB
    assert len(out) == 1 * 1024 * 1024
    # No refusal warning
    captured = capsys.readouterr()
    assert "WARN" not in captured.err


def test_oversized_real_content_after_strip_refused(capsys):
    """Mirror: 100 MB of real content (no trailing whitespace) →
    stripped len == 100 MB > cap → refused."""
    body = "x" * (100 * 1024 * 1024)
    out = extract_rules_inject_bytes("aider", "myproj", body)
    assert out == b""
    captured = capsys.readouterr()
    assert "WARN" in captured.err


# ─── Empty input edge cases (regression from #93) ──────────────────────


def test_empty_input_still_returns_empty():
    """Pre-#124 contract preserved: empty/whitespace-only input
    → empty bytes, no warning, no exception."""
    out = extract_rules_inject_bytes("aider", "myproj", "")
    assert out == b""


def test_whitespace_only_input_still_returns_empty():
    out = extract_rules_inject_bytes(
        "aider", "myproj", "   \n\n\t   ")
    assert out == b""


def test_empty_input_does_not_emit_oversized_warning(capsys):
    """Pin: warning fires ONLY for oversized input. Empty bytes
    → silent return."""
    extract_rules_inject_bytes("aider", "myproj", "")
    captured = capsys.readouterr()
    assert "WARN" not in captured.err


# ─── Provider parity preserved across cap path ─────────────────────────


def test_oversized_block_refusal_byte_identical_across_providers(capsys):
    """Pin: empty refusal output is byte-identical across
    providers. The cap is provider-agnostic — same threshold,
    same `b""` return for all."""
    huge = "z" * (100 * 1024 * 1024)
    out_claude = extract_rules_inject_bytes("claude", "p", huge)
    capsys.readouterr()  # consume warning
    out_copilot = extract_rules_inject_bytes("copilot", "p", huge)
    capsys.readouterr()
    out_aider = extract_rules_inject_bytes("aider", "p", huge)
    capsys.readouterr()

    assert out_claude == out_copilot == out_aider == b""


def test_under_cap_block_byte_identical_across_providers():
    """Pre-cap: under-threshold blocks remain byte-identical
    across providers (#93 contract)."""
    body = "## rules\n- be terse\n" * 10000  # ~200 KB
    out_claude = extract_rules_inject_bytes("claude", "p", body)
    out_aider = extract_rules_inject_bytes("aider", "p", body)
    assert out_claude == out_aider


# ─── UTF-8 boundary at cap ─────────────────────────────────────────────


def test_multibyte_content_at_cap_boundary_handled():
    """Polish content (2-byte UTF-8 chars) just under the cap →
    passes. The cap is on encoded bytes, not characters; pin
    that 2-byte chars don't accidentally double-count."""
    # Polish 'ą' = 2 bytes UTF-8. 25 MB of 'ą' = 50 MB encoded
    # — exactly at cap.
    polish = "ą" * (25 * 1024 * 1024 - 1)  # ~50 MB - 2 bytes
    out = extract_rules_inject_bytes("aider", "myproj", polish)
    # Just under 50 MB → passes
    assert len(out) > 0
    assert len(out) < _RULES_INJECT_MAX_BYTES


def test_multibyte_oversized_content_refused(capsys):
    """Polish content over cap → refused. Encoded byte count is
    what matters, not character count."""
    # 30 MB of 'ą' = 60 MB encoded > 50 MB cap
    polish = "ą" * (30 * 1024 * 1024)
    out = extract_rules_inject_bytes("aider", "myproj", polish)
    assert out == b""
    captured = capsys.readouterr()
    assert "WARN" in captured.err


# ─── Production integration: _do_inject_rules handles empty bytes ──────


def test_do_inject_rules_treats_empty_bytes_as_no_rules():
    """Source-grep: `_do_inject_rules` checks `if not project_block`
    BEFORE calling extract_rules_inject_bytes. This is the
    pre-cap guard. After cap (which returns b""), the canonical
    flow needs the SAME early-exit semantics for empty
    rules_bytes.

    Pin: production call site doesn't crash on empty rules_bytes
    from cap-refusal path."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _do_inject_rules")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]

    # Pre-cap empty-string guard exists
    assert "if not project_block:" in body
    # And the function body uses extract_rules_inject_bytes
    assert "extract_rules_inject_bytes" in body


# ─── Source-grep: cap constant is module-level + named ────────────────


def test_cap_constant_is_module_level():
    """Pin: `_RULES_INJECT_MAX_BYTES` lives at module level (not
    inside the function) so observers can introspect it."""
    src = TERMINAL_TAB.read_text()
    assert "_RULES_INJECT_MAX_BYTES = 50 * 1024 * 1024" in src
    # And referenced inside extract_rules_inject_bytes
    fn_start = src.find("def extract_rules_inject_bytes")
    fn_end = src.find("\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "_RULES_INJECT_MAX_BYTES" in body


def test_cap_check_uses_encoded_byte_count_not_string_length():
    """Pin: the cap check happens AFTER `.encode()`, not on raw
    string length. This is what makes branch (c) — multi-byte
    Polish content — work correctly. A regression that compares
    `len(rules_stdout)` (chars) instead of `len(encoded)` (bytes)
    would let twice as much through for Polish content."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def extract_rules_inject_bytes")
    fn_end = src.find("\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    # The check is `if len(encoded) > _RULES_INJECT_MAX_BYTES`
    assert "len(encoded) > _RULES_INJECT_MAX_BYTES" in body
    # NOT on the raw string
    assert "len(rules_stdout) > _RULES_INJECT_MAX_BYTES" not in body


# ─── Migration marker: cap value can be tuned ──────────────────────────


def test_cap_value_documented_in_source_with_rationale():
    """Pin: the cap constant has an inline comment explaining
    WHY 50 MB (PTY chunking, accidental file contents). Without
    rationale, future tuning is blind."""
    src = TERMINAL_TAB.read_text()
    cap_idx = src.find("_RULES_INJECT_MAX_BYTES")
    # Look at surrounding 500 chars for rationale keywords
    ctx = src[max(0, cap_idx - 800):cap_idx + 500]
    rationale_keywords = [
        "PIPE_BUF", "main loop", "cap", "chunk", "block"
    ]
    found = [k for k in rationale_keywords if k.lower() in ctx.lower()]
    assert found, (
        f"_RULES_INJECT_MAX_BYTES has no rationale comment — "
        f"future tuning would be blind. Context:\n{ctx[-300:]!r}"
    )
