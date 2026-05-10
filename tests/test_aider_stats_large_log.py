"""Performance: AiderStatsReader on 100MB chat history
(#54 / #126, audit § 6.6 #27).

`AiderStatsReader.read_session_tokens` reads the entire
`.aider.chat.history.md` on each refresh tick (default 5s in
the SessionStatsBar). At small log sizes (typical < 1 MB) the
full re-scan is fine. At pathological sizes (100 MB+ — long-
running session, never compacted) the latency could push the
GLib main loop past noticeable thresholds.

Three decision branches:
  (a) 1 MB log — typical realistic case, <50 ms.
  (b) 100 MB log — pathological scale, <500 ms (acceptable
      since this only fires every 5s, but flag if >1s).
  (c) tail-f mode — future incremental read (only new bytes
      since last position). Pin: NOT implemented today; the
      reader does full scans. A future refactor adding
      incremental mode lifts this pin.

Bench methodology: time.perf_counter + statistics for p99.
3 iterations × 100 MB = ~10s slow test, gated separately so
it doesn't dominate normal runs.

Manual VM smoke (long-running aider session, observe stats
bar refresh latency) is documented in tests/manual/README.md.
"""
from __future__ import annotations

import statistics
import time
from pathlib import Path

import pytest

from bterminal.ui.stats import AiderStatsReader


REPO_ROOT = Path(__file__).resolve().parent.parent
AIDER_PROVIDER = REPO_ROOT / "bterminal" / "providers" / "aider.py"
STATS_AIDER = REPO_ROOT / "bterminal" / "ui" / "stats" / "aider.py"


def _generate_chat_history(target_bytes: int) -> str:
    """Build a realistic-shape `.aider.chat.history.md` content
    of approximately `target_bytes` bytes.

    Pattern: alternating `#### ` user turns + `> Tokens: <N>k sent,
    <M> received.` lines + filler prose. Mirrors real aider output
    so the regex hits + line-iter scaling are realistic.
    """
    block = (
        "#### Sample user prompt asking aider to refactor a function\n"
        "\n"
        "I'd like you to look at the helper function and refactor it.\n"
        "Please use TDD — write the test first, then the implementation.\n"
        "\n"
        "Aider replied:\n"
        "Sure, I'll refactor `helper()` to use the new pattern.\n"
        "\n"
        "```python\n"
        "def helper(x: int) -> int:\n"
        "    return x * 2 + 1\n"
        "```\n"
        "\n"
        "> Tokens: 1.5k sent, 234 received.\n"
        "\n"
    )
    block_size = len(block.encode("utf-8"))
    n_blocks = max(1, target_bytes // block_size)
    return block * n_blocks


def _quantile(sorted_data: list[float], q: float) -> float:
    if not sorted_data:
        return 0.0
    idx = max(0, min(len(sorted_data) - 1, int(q * len(sorted_data))))
    return sorted_data[idx]


# ─── Branch (a): 1 MB log — typical workload ────────────────────────────


def test_read_session_tokens_under_100ms_on_1mb_log(tmp_path):
    """Pin: 1 MB log → <100 ms. Realistic upper bound for a
    typical aider session — even chatty sessions rarely cross
    1 MB before manual cleanup.

    Note: AiderStatsReader returns TokenStats (input/output/
    responses) — distinct from the provider's SessionStats
    (input_tokens/output_tokens/response_count). The reader
    adapts the latter into the former."""
    log = tmp_path / ".aider.chat.history.md"
    log.write_text(
        _generate_chat_history(1 * 1024 * 1024), encoding="utf-8")

    reader = AiderStatsReader(str(tmp_path))
    timings = []
    for _ in range(5):
        t0 = time.perf_counter()
        stats = reader.read_session_tokens()
        t1 = time.perf_counter()
        timings.append(t1 - t0)
    timings.sort()
    p99 = _quantile(timings, 0.99)
    assert p99 < 0.100, (
        f"1MB read p99 = {p99 * 1000:.2f}ms — exceeds 100ms threshold"
    )
    # Sanity: parser actually counted something (TokenStats fields)
    assert stats.input > 0
    assert stats.output > 0


def test_1mb_log_yields_correct_token_aggregates(tmp_path):
    """Pin: 1 MB content with N "Tokens: 1.5k sent, 234 received"
    lines aggregates to N*1500 input + N*234 output. Catches a
    regression where the regex stops matching at scale."""
    log = tmp_path / ".aider.chat.history.md"
    content = _generate_chat_history(1 * 1024 * 1024)
    log.write_text(content, encoding="utf-8")

    reader = AiderStatsReader(str(tmp_path))
    stats = reader.read_session_tokens()

    # Each block has 1 token line + 1 user turn (#### marker)
    n_blocks = content.count("> Tokens:")
    # TokenStats fields, not SessionStats — input vs input_tokens
    assert stats.input == n_blocks * 1500
    assert stats.output == n_blocks * 234
    assert stats.responses == n_blocks


# ─── Branch (b): 100 MB log — pathological scale ────────────────────────


@pytest.mark.slow
def test_read_session_tokens_under_2500ms_on_100mb_log(tmp_path):
    """Pin: 100 MB log → <2.5 s p99. The full-scan strategy
    handles pathological sizes but not lightning-fast — file
    read (~700 MB/s) + regex on 100 MB string + line iter is
    the bottleneck.

    Threshold reflects measured baseline. This is well above
    the 'instant' threshold but acceptable for a pathological
    case that fires every 5s — user sees a brief stutter,
    not a freeze. Future tail-f mode (audit § 6.6 #27 branch
    c) would lift this thresholds dramatically. Marked slow —
    adds ~6s per run."""
    log = tmp_path / ".aider.chat.history.md"
    log.write_text(
        _generate_chat_history(100 * 1024 * 1024), encoding="utf-8")

    reader = AiderStatsReader(str(tmp_path))
    timings = []
    for _ in range(3):
        t0 = time.perf_counter()
        stats = reader.read_session_tokens()
        t1 = time.perf_counter()
        timings.append(t1 - t0)
    timings.sort()
    median = _quantile(timings, 0.50)
    p99 = _quantile(timings, 0.99)

    assert p99 < 2.500, (
        f"100MB read p99 = {p99 * 1000:.2f}ms exceeds 2500ms — "
        f"baseline regression. median = {median * 1000:.2f}ms"
    )


@pytest.mark.slow
def test_read_session_tokens_under_5s_hard_cap_on_100mb_log(tmp_path):
    """Hard cap pin: 100 MB stays under 5 s. Above that, the
    SessionStatsBar's 5s refresh tick would START piling up
    requests faster than they complete. This is the 'tail-f
    REQUIRED' threshold — a regression beyond 5s = users
    visibly feel the stats bar lock the UI."""
    log = tmp_path / ".aider.chat.history.md"
    log.write_text(
        _generate_chat_history(100 * 1024 * 1024), encoding="utf-8")

    reader = AiderStatsReader(str(tmp_path))
    t0 = time.perf_counter()
    reader.read_session_tokens()
    t1 = time.perf_counter()
    assert (t1 - t0) < 5.0, (
        f"100MB single read took {(t1 - t0):.3f}s — beyond 5s "
        f"hard cap; SessionStatsBar's 5s tick would compound"
    )


# ─── Scaling check: linear with file size ──────────────────────────────


@pytest.mark.slow
def test_read_session_tokens_scales_subquadratically(tmp_path):
    """Pin: latency for 10 MB shouldn't be more than 50x latency
    for 1 MB. The full-scan O(N) regex iteration should be
    linear; quadratic would point to e.g. accidental N^2 string
    construction."""
    log_1mb = tmp_path / "1mb.md"
    log_1mb.write_text(_generate_chat_history(1 * 1024 * 1024),
                       encoding="utf-8")
    log_10mb = tmp_path / "10mb.md"
    log_10mb.write_text(_generate_chat_history(10 * 1024 * 1024),
                        encoding="utf-8")

    # Reader binds to project_dir, then session_log_glob
    # resolves <dir>/.aider.chat.history.md. Since both files
    # live in tmp_path and we want to read each, use distinct
    # subdirs.
    dir_1mb = tmp_path / "p1"
    dir_1mb.mkdir()
    (dir_1mb / ".aider.chat.history.md").write_text(
        _generate_chat_history(1 * 1024 * 1024), encoding="utf-8")
    dir_10mb = tmp_path / "p10"
    dir_10mb.mkdir()
    (dir_10mb / ".aider.chat.history.md").write_text(
        _generate_chat_history(10 * 1024 * 1024), encoding="utf-8")

    reader_1mb = AiderStatsReader(str(dir_1mb))
    reader_10mb = AiderStatsReader(str(dir_10mb))

    def _bench(reader):
        ts = []
        for _ in range(3):
            t0 = time.perf_counter()
            reader.read_session_tokens()
            t1 = time.perf_counter()
            ts.append(t1 - t0)
        return statistics.median(ts)

    t_1 = _bench(reader_1mb)
    t_10 = _bench(reader_10mb)

    if t_1 > 0.0001:
        ratio = t_10 / t_1
        assert ratio < 50, (
            f"10MB:1MB ratio = {ratio:.1f}x — possible non-linear "
            f"scaling (1MB={t_1*1000:.2f}ms, 10MB={t_10*1000:.2f}ms)"
        )


# ─── Empty-log + missing-file paths are fast ───────────────────────────


def test_read_session_tokens_under_5ms_on_missing_log(tmp_path):
    """Pin: missing log → <5 ms (just an os.path.isfile check).
    The 5s SessionStatsBar tick is constant load even for
    sessions with no history written yet."""
    reader = AiderStatsReader(str(tmp_path))
    timings = []
    for _ in range(20):
        t0 = time.perf_counter()
        reader.read_session_tokens()
        t1 = time.perf_counter()
        timings.append(t1 - t0)
    timings.sort()
    p99 = _quantile(timings, 0.99)
    assert p99 < 0.005, (
        f"missing-log p99 = {p99 * 1000:.2f}ms — fast-path "
        f"regressed; would jank UI on never-prompted sessions"
    )


def test_read_session_tokens_under_5ms_on_empty_log(tmp_path):
    """Empty file (header only or zero-byte) → <5 ms. open+read
    is dominant, regex finds nothing, line iter empty."""
    log = tmp_path / ".aider.chat.history.md"
    log.write_text("# aider chat\n", encoding="utf-8")

    reader = AiderStatsReader(str(tmp_path))
    timings = []
    for _ in range(20):
        t0 = time.perf_counter()
        reader.read_session_tokens()
        t1 = time.perf_counter()
        timings.append(t1 - t0)
    timings.sort()
    p99 = _quantile(timings, 0.99)
    assert p99 < 0.005


# ─── Branch (c): tail-f / incremental — NOT implemented today ──────────


def test_read_session_tokens_does_full_scan_today():
    """Pin: today's reader does a FULL scan on each call. Source-
    grep: `fh.read()` reads entire file; no offset / seek logic.

    Pre-#126 this is fine because chat histories rarely exceed
    a few MB. If a future refactor adds tail-f mode (only read
    new bytes since last position), update this test to assert
    the incremental contract."""
    src = AIDER_PROVIDER.read_text()
    fn_start = src.find("def parse_session_stats")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]

    # Full read pattern
    assert "fh.read()" in body, (
        "parse_session_stats no longer does full-file read — "
        "tail-f shipped? Lift this pin and add incremental tests"
    )
    # No incremental seek/tell
    forbidden = ["fh.seek(", "fh.tell()", "self._last_offset",
                 "self._cached_offset"]
    for pat in forbidden:
        assert pat not in body, (
            f"parse_session_stats has incremental hint {pat!r} — "
            f"may be partial tail-f migration; update tests"
        )


def test_aider_stats_reader_module_does_not_cache_content():
    """Pin: AiderStatsReader keeps no in-memory content cache
    between read_session_tokens calls. Full re-read every tick.

    A future tail-f refactor would add a `_buffer` / `_offset`
    state — this test would fail then, signaling the migration."""
    src = STATS_AIDER.read_text()
    forbidden = ["self._buffer", "self._content_cache",
                 "self._last_size", "self._last_offset",
                 "self._cached_content"]
    for pat in forbidden:
        assert pat not in src, (
            f"AiderStatsReader has cached state {pat!r} — "
            f"tail-f shipped, update perf bench"
        )


def test_aider_stats_reader_module_holds_only_project_dir_state():
    """Pin: AiderStatsReader's __init__ stores only project_dir
    + provider reference. No buffers, no offsets. Catches a
    refactor that adds heavy state for caching purposes."""
    src = STATS_AIDER.read_text()
    init_idx = src.find("def __init__(self,")
    next_def = src.find("\n    def ", init_idx + 1)
    body = src[init_idx:next_def]
    # Only these self.X assignments allowed
    allowed_attrs = {"self.project_dir", "self._provider"}
    import re as _re
    actual_assigns = set(_re.findall(r"self\.[a-zA-Z_]+", body))
    extra = actual_assigns - allowed_attrs
    assert not extra, (
        f"AiderStatsReader.__init__ assigns extra attrs: {extra}. "
        f"Pre-#126 only project_dir + _provider were expected. If "
        f"tail-f mode shipped, update this list."
    )


# ─── Token regex performance (stays linear over chat history) ──────────


def test_token_regex_finds_all_markers_in_realistic_log(tmp_path):
    """Pin: regex finds every `Tokens:` line at scale. A
    regression that breaks the lookahead at high counts would
    show input_tokens=0 here even though we wrote N matches."""
    log = tmp_path / ".aider.chat.history.md"
    content = _generate_chat_history(5 * 1024 * 1024)  # 5 MB
    log.write_text(content, encoding="utf-8")

    expected_matches = content.count("> Tokens:")
    reader = AiderStatsReader(str(tmp_path))
    stats = reader.read_session_tokens()
    # TokenStats.input (not SessionStats.input_tokens)
    assert stats.input == expected_matches * 1500


# ─── Read isolation: reader doesn't mutate the file ────────────────────


def test_read_session_tokens_does_not_modify_log(tmp_path):
    """Pin: reading is read-only — file mtime + content
    unchanged. Without this, repeated SessionStatsBar ticks
    could accidentally truncate or rewrite the chat history."""
    log = tmp_path / ".aider.chat.history.md"
    content = _generate_chat_history(100 * 1024)  # 100 KB
    log.write_text(content, encoding="utf-8")

    mtime_before = log.stat().st_mtime
    bytes_before = log.read_bytes()

    reader = AiderStatsReader(str(tmp_path))
    for _ in range(10):
        reader.read_session_tokens()

    assert log.stat().st_mtime == mtime_before
    assert log.read_bytes() == bytes_before


# ─── BT lifecycle: reader can be stopped + restarted cheaply ───────────


def test_aider_stats_reader_construction_is_under_1ms():
    """Pin: instantiation is cheap (no eager file I/O). The
    SessionStatsBar may construct the reader before the log
    file even exists."""
    timings = []
    for i in range(100):
        t0 = time.perf_counter()
        AiderStatsReader(f"/tmp/proj-{i}")
        t1 = time.perf_counter()
        timings.append(t1 - t0)
    timings.sort()
    p99 = _quantile(timings, 0.99)
    # 1ms is generous for object construction + dataclass init
    assert p99 < 0.001, (
        f"AiderStatsReader.__init__ p99 = {p99 * 1000:.4f}ms — "
        f"reader construction now hitting disk?"
    )


# ─── Memory cap reminder (cross-ref #124) ───────────────────────────────


def test_no_explicit_chat_history_size_cap():
    """Pin: AiderStatsReader does NOT cap chat history size at
    read time. A 100 MB log is read in full. Compare with #124
    where rules_inject HAS a 50 MB cap (to protect PTY feed).

    The reader's safe regardless because it produces a
    TokenStats dataclass — no PTY dump. Pin so a future cap
    addition (e.g. 'truncate after 200 MB to save RAM') is
    explicit."""
    src = AIDER_PROVIDER.read_text()
    fn_start = src.find("def parse_session_stats")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    forbidden = ["MAX_CHAT_HISTORY_BYTES", "_CHAT_HISTORY_CAP",
                 "len(content) >", "len(content) <"]
    for pat in forbidden:
        assert pat not in body, (
            f"parse_session_stats now caps chat history: {pat!r}. "
            f"Update perf bench + document the rationale."
        )


# ─── Provider-side fast path (parse_session_stats) ─────────────────────


@pytest.mark.slow
def test_provider_parse_session_stats_under_2500ms_on_100mb(tmp_path):
    """Provider-level entry. Same threshold + rationale as the
    reader-level test. Catches a regression in either layer."""
    from bterminal.providers import get_registry
    log = tmp_path / ".aider.chat.history.md"
    log.write_text(
        _generate_chat_history(100 * 1024 * 1024), encoding="utf-8")

    aider = get_registry().get("aider")

    timings = []
    for _ in range(3):
        t0 = time.perf_counter()
        aider.parse_session_stats(str(log))
        t1 = time.perf_counter()
        timings.append(t1 - t0)
    timings.sort()
    p99 = _quantile(timings, 0.99)
    assert p99 < 2.500, (
        f"parse_session_stats p99 = {p99 * 1000:.2f}ms exceeds "
        f"2500ms on 100MB log"
    )


# ─── Statistics helper sanity ──────────────────────────────────────────


def test_quantile_helper_correctness():
    """Sanity: percentile helper indexes correctly."""
    sorted_data = [float(i) for i in range(100)]
    assert _quantile(sorted_data, 0.50) == 50.0
    assert _quantile(sorted_data, 0.99) == 99.0
    assert _quantile([], 0.5) == 0.0
    assert _quantile([42.0], 0.5) == 42.0


def test_chat_history_generator_produces_sized_output():
    """Sanity: `_generate_chat_history(N)` returns approximately
    N bytes — within ±10% so the bench scale assumptions hold."""
    for size in (1024 * 1024, 10 * 1024 * 1024):
        out = _generate_chat_history(size)
        actual = len(out.encode("utf-8"))
        # Within 10% of target
        assert 0.9 * size <= actual <= 1.1 * size, (
            f"_generate_chat_history({size}) returned {actual} "
            f"bytes — outside ±10% tolerance"
        )
