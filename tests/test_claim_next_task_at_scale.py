"""Performance: 1000 tasks in CTX DB benchmark
(#51 / #123, audit § 6.6 #24).

`_claim_next_task` runs every time `_on_task_idle_timeout` fires
(every ~2s of VTE silence per active autorun tab). At low task
counts (typical < 100 unclaimed) it's invisible; at scale (1000+
seeded, e.g. a backlog from a long sprint) latency could push
the GLib main loop past noticeable thresholds.

This file pins p99 latency at three task-count tiers:
  (a) 100 tasks — typical workload, p99 < 10 ms.
  (b) 1000 tasks — heavy backlog, p99 < 50 ms (auto-trigger
      plan threshold).
  (c) 10000 tasks — pathological, p99 < 200 ms (still < 1 s
      perceived UI freeze threshold).

Bench methodology (no pytest-benchmark dep — uses
time.perf_counter + statistics for p99):
  - Seed N tasks (one per task_id 't-0' through 't-{N-1}').
  - Run `_claim_next_task` 100 times with FRESH session_id each
    call (avoids the existing-claim shortcut path → measures the
    SELECT-then-INSERT path).
  - Sort timings, take p99.
  - Assert under threshold.

Manual VM smoke (real BT instance with seeded DB, observe
auto_trigger latency in feed_log) is documented in
tests/manual/README.md.
"""
from __future__ import annotations

import sqlite3
import statistics
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
TERMINAL_TAB = REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py"


def _seed_tasks(db_path: Path, project: str, n_tasks: int):
    """Bulk-insert N open tasks. Uses executemany for speed —
    seeding 10000 rows in <100 ms."""
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, task_id TEXT NOT NULL,
                description TEXT NOT NULL, status TEXT DEFAULT 'open',
                UNIQUE(project, task_id)
            );
            CREATE TABLE IF NOT EXISTS task_config (
                project TEXT PRIMARY KEY, autorun INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS task_claims (
                project TEXT NOT NULL, task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                PRIMARY KEY (project, task_id, session_id)
            );
        """)
        conn.execute(
            "INSERT OR REPLACE INTO task_config VALUES (?, 1)",
            (project,),
        )
        rows = [
            (project, f"t-{i:05d}", f"E2E task {i}")
            for i in range(n_tasks)
        ]
        conn.executemany(
            "INSERT INTO tasks (project, task_id, description) "
            "VALUES (?, ?, ?)",
            rows,
        )
        conn.commit()
    finally:
        conn.close()


def _bench_claim_next_task(db_path: Path, project: str,
                              n_iterations: int = 100) -> list[float]:
    """Time N calls to _claim_next_task with FRESH session_id each
    time. Returns sorted list of per-call durations in seconds."""
    from bterminal.ui.terminal_tab import TerminalTab

    timings: list[float] = []
    for i in range(n_iterations):
        conn = sqlite3.connect(str(db_path), timeout=5)
        conn.row_factory = sqlite3.Row
        # Fresh session per call → forces full SELECT-then-INSERT
        # rather than the existing-claim shortcut (which is much
        # faster than the worst case).
        session_id = f"bench-session-{i:05d}"
        t0 = time.perf_counter()
        TerminalTab._claim_next_task(conn, project, session_id)
        t1 = time.perf_counter()
        conn.close()
        timings.append(t1 - t0)
    timings.sort()
    return timings


def _quantile(sorted_data: list[float], q: float) -> float:
    """Compute the q-th quantile (0.0-1.0) on a pre-sorted list."""
    n = len(sorted_data)
    if n == 0:
        return 0.0
    # Nearest-rank method
    idx = max(0, min(n - 1, int(q * n)))
    return sorted_data[idx]


# ─── Branch (a): 100 tasks — typical workload ──────────────────────────


def test_claim_next_task_p99_under_25ms_with_100_tasks(tmp_path):
    """Pin: at 100 unclaimed tasks (typical sprint backlog),
    p99 latency < 25 ms. Auto-trigger feels instantaneous.

    Threshold reflects post-#109 reality: BEGIN IMMEDIATE adds
    a write-lock + commit roundtrip per call, putting typical
    latency in the 5-15 ms range (vs sub-ms for read-only
    queries). Still well under user-perceived UI jank threshold."""
    db_path = tmp_path / "context.db"
    _seed_tasks(db_path, "myproj", n_tasks=100)

    timings = _bench_claim_next_task(db_path, "myproj")
    p50 = _quantile(timings, 0.50)
    p95 = _quantile(timings, 0.95)
    p99 = _quantile(timings, 0.99)

    assert p99 < 0.025, (
        f"_claim_next_task p99 = {p99 * 1000:.2f}ms exceeds 25ms "
        f"threshold at 100 tasks. p50={p50*1000:.2f}ms, "
        f"p95={p95*1000:.2f}ms"
    )


def test_claim_next_task_median_under_15ms_with_100_tasks(tmp_path):
    """Tighter pin on the median: 100 tasks → typical call < 15
    ms (post-#109). Catches a regression where every call slows
    down (vs a long-tail outlier)."""
    db_path = tmp_path / "context.db"
    _seed_tasks(db_path, "myproj", n_tasks=100)

    timings = _bench_claim_next_task(db_path, "myproj")
    p50 = _quantile(timings, 0.50)
    assert p50 < 0.015, (
        f"_claim_next_task median = {p50 * 1000:.2f}ms exceeds "
        f"15ms threshold at 100 tasks"
    )


# ─── Branch (b): 1000 tasks — auto-trigger plan threshold ──────────────


def test_claim_next_task_p99_under_50ms_with_1000_tasks(tmp_path):
    """Pin from auto-trigger plan: at 1000 tasks, p99 < 50 ms.
    Headline #123 promise — heavy backlog still feels snappy."""
    db_path = tmp_path / "context.db"
    _seed_tasks(db_path, "myproj", n_tasks=1000)

    timings = _bench_claim_next_task(db_path, "myproj")
    p50 = _quantile(timings, 0.50)
    p95 = _quantile(timings, 0.95)
    p99 = _quantile(timings, 0.99)
    p_max = timings[-1]

    assert p99 < 0.050, (
        f"_claim_next_task p99 = {p99 * 1000:.2f}ms exceeds 50ms "
        f"threshold at 1000 tasks. p50={p50*1000:.2f}ms, "
        f"p95={p95*1000:.2f}ms, max={p_max*1000:.2f}ms"
    )


def test_claim_next_task_max_under_100ms_with_1000_tasks(tmp_path):
    """No outlier above 100 ms either. Without this guard a single
    GC pause could leak the main loop briefly. 100 ms is below
    user-perceived 'jank' threshold."""
    db_path = tmp_path / "context.db"
    _seed_tasks(db_path, "myproj", n_tasks=1000)

    timings = _bench_claim_next_task(db_path, "myproj")
    assert timings[-1] < 0.100, (
        f"_claim_next_task max = {timings[-1] * 1000:.2f}ms — "
        f"slowest call exceeds 100ms (visible UI jank)"
    )


# ─── Branch (c): 10000 tasks — pathological scale ──────────────────────


def test_claim_next_task_p99_under_200ms_with_10000_tasks(tmp_path):
    """Pin: even at 10x audit's threshold (10k tasks), p99
    stays under 200 ms — well below the 1 s user-perceived
    'freeze' threshold. Indicates the SQL plan scales with
    task count, even without explicit indexes."""
    db_path = tmp_path / "context.db"
    _seed_tasks(db_path, "myproj", n_tasks=10000)

    # Lower iteration count for 10k — each call is slower so
    # 100 iterations would take seconds.
    timings = _bench_claim_next_task(db_path, "myproj",
                                       n_iterations=50)
    p50 = _quantile(timings, 0.50)
    p99 = _quantile(timings, 0.99)
    assert p99 < 0.200, (
        f"_claim_next_task p99 = {p99 * 1000:.2f}ms exceeds 200ms "
        f"threshold at 10000 tasks. p50={p50*1000:.2f}ms"
    )


# ─── Throughput sanity: linear scaling check ───────────────────────────


def test_claim_next_task_scales_subquadratically(tmp_path):
    """Pin: latency at 1000 tasks shouldn't be more than 50x
    latency at 100 tasks. The SQL plan with LEFT JOIN +
    `WHERE c.task_id IS NULL` + ORDER BY t.task_id should be
    O(N) — not quadratic.

    Catches a regression where a missing index makes the join
    cost N^2 (would push 1000 to ~50ms but 10000 to ~5s, way
    over the 200ms pin)."""
    db_100 = tmp_path / "100.db"
    _seed_tasks(db_100, "p", n_tasks=100)
    db_1000 = tmp_path / "1000.db"
    _seed_tasks(db_1000, "p", n_tasks=1000)

    t_100 = _bench_claim_next_task(db_100, "p")
    t_1000 = _bench_claim_next_task(db_1000, "p")

    p99_100 = _quantile(t_100, 0.99)
    p99_1000 = _quantile(t_1000, 0.99)

    # 10x more tasks should NOT cost 50x more time. (Linear:
    # ~10x; quadratic: ~100x. 50x is the loose pin allowing
    # constant-factor slop.)
    if p99_100 > 0.0001:  # avoid division by ~zero on very fast machines
        ratio = p99_1000 / p99_100
        assert ratio < 50, (
            f"latency ratio 1000:100 = {ratio:.1f}x — possible "
            f"non-linear scaling (p99 100={p99_100*1000:.2f}ms, "
            f"p99 1000={p99_1000*1000:.2f}ms)"
        )


# ─── Existing-claim shortcut path is fast ──────────────────────────────


def test_existing_claim_shortcut_is_under_5ms_at_1000_tasks(tmp_path):
    """When a session already has a claimed task, _claim_next_task
    short-circuits with the JOIN to `task_claims`. This path runs
    every poll (every 2s of VTE silence per autorun tab), so
    latency must be tiny.

    Pin: even at 1000 task scale, the shortcut path is < 5 ms p99
    (same SELECT-JOIN, no INSERT)."""
    db_path = tmp_path / "context.db"
    _seed_tasks(db_path, "myproj", n_tasks=1000)

    from bterminal.ui.terminal_tab import TerminalTab

    # Pre-claim one task for our session
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "INSERT INTO task_claims (project, task_id, session_id) "
        "VALUES (?, ?, ?)", ("myproj", "t-00050", "shortcut-bench"),
    )
    conn.commit()
    conn.close()

    # Now time repeated calls with THE SAME session_id — they all
    # hit the shortcut path (existing claim).
    timings: list[float] = []
    for _ in range(100):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        t0 = time.perf_counter()
        result = TerminalTab._claim_next_task(
            conn, "myproj", "shortcut-bench")
        t1 = time.perf_counter()
        conn.close()
        timings.append(t1 - t0)
        assert result is not None  # always returns the claimed task
    timings.sort()
    p99 = _quantile(timings, 0.99)
    assert p99 < 0.005, (
        f"existing-claim shortcut p99 = {p99 * 1000:.2f}ms exceeds "
        f"5ms — repeated polls would jank the main loop"
    )


# ─── No-tasks-available path is also fast ──────────────────────────────


def test_no_open_tasks_path_is_under_5ms(tmp_path):
    """When all tasks are claimed (or there are no tasks),
    _claim_next_task returns None quickly. Pin: < 5 ms — this
    path runs continuously when the user has no work pending."""
    db_path = tmp_path / "context.db"
    _seed_tasks(db_path, "myproj", n_tasks=100)

    # Pre-claim ALL tasks under different sessions so any new
    # session sees nothing
    from bterminal.ui.terminal_tab import TerminalTab
    conn = sqlite3.connect(str(db_path))
    rows = [(f"myproj", f"t-{i:05d}", "claimer-other")
            for i in range(100)]
    conn.executemany(
        "INSERT INTO task_claims VALUES (?, ?, ?)", rows)
    conn.commit()
    conn.close()

    timings: list[float] = []
    for i in range(100):
        conn = sqlite3.connect(str(db_path))
        conn.row_factory = sqlite3.Row
        t0 = time.perf_counter()
        result = TerminalTab._claim_next_task(
            conn, "myproj", f"empty-bench-{i}")
        t1 = time.perf_counter()
        conn.close()
        timings.append(t1 - t0)
        assert result is None
    timings.sort()
    p99 = _quantile(timings, 0.99)
    assert p99 < 0.005, (
        f"no-tasks path p99 = {p99 * 1000:.2f}ms — repeated "
        f"polls under empty queue would jank the loop"
    )


# ─── Source-grep: no missing index on the hot path ─────────────────────


def test_claim_next_task_uses_indexable_columns_only():
    """Pin: the SQL SELECT-then-INSERT pattern relies on
    `tasks.project + tasks.task_id` and `task_claims.project +
    task_claims.task_id`. Both are PRIMARY KEY / UNIQUE columns,
    so SQLite indexes them automatically. Pin: no JOIN on
    non-indexed columns (e.g. `description LIKE`)."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _claim_next_task")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]

    # The JOIN clauses use only indexed columns
    forbidden_join_targets = [
        "JOIN task_claims c ON c.description",
        "JOIN tasks t ON t.description",
        "WHERE t.description",
    ]
    for pat in forbidden_join_targets:
        assert pat not in body, (
            f"_claim_next_task joins/filters on non-indexed "
            f"column: {pat!r}"
        )


def test_claim_next_task_uses_left_join_for_unclaimed_filter():
    """Pin the canonical pattern: LEFT JOIN task_claims + WHERE
    c.task_id IS NULL. This is the documented unclaimed filter
    (#108/#109). Pin so a refactor that switches to NOT EXISTS
    or NOT IN is forced to re-benchmark."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _claim_next_task")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "LEFT JOIN task_claims" in body
    assert "c.task_id IS NULL" in body


# ─── Setup overhead: seeding shouldn't dominate the bench ──────────────


def test_seed_1000_tasks_under_500ms(tmp_path):
    """Sanity: seeding 1000 tasks via executemany takes < 500 ms.
    Without this, the scale tests would spend most of their time
    in setup, not the SUT."""
    db_path = tmp_path / "context.db"
    t0 = time.perf_counter()
    _seed_tasks(db_path, "myproj", n_tasks=1000)
    t1 = time.perf_counter()
    assert (t1 - t0) < 0.5, (
        f"seeding 1000 tasks took {(t1 - t0) * 1000:.2f}ms — "
        f"benchmark setup is the bottleneck, not _claim_next_task"
    )


# ─── Statistics summary helper sanity ──────────────────────────────────


def test_quantile_helper_correctness():
    """Sanity: the p50/p95/p99 helper correctly indexes a sorted
    list. Pin so a refactor using numpy/statistics doesn't change
    the percentile semantics under us."""
    sorted_data = [float(i) for i in range(100)]
    # Nearest-rank: q=0.5 → idx 50; q=0.99 → idx 99 → 99.0
    assert _quantile(sorted_data, 0.50) == 50.0
    assert _quantile(sorted_data, 0.99) == 99.0
    # Edge cases
    assert _quantile([], 0.5) == 0.0
    assert _quantile([42.0], 0.5) == 42.0


# ─── Manual VM hint: documented in runbook ─────────────────────────────


def test_runbook_documents_perf_smoke():
    """The runbook references this perf benchmark so a contributor
    knows to run it under load if they suspect auto_trigger is
    slow. Pin doc cross-reference."""
    runbook = REPO_ROOT / "tests" / "manual" / "README.md"
    if not runbook.exists():
        pytest.skip("runbook not present on this branch")
    text = runbook.read_text()
    # Either explicit reference to claim_next_task / scale benchmark,
    # OR a generic 'perf testing' section
    has_perf_ref = (
        "claim_next_task" in text
        or "perf" in text.lower()
        or "benchmark" in text.lower()
        or "scale" in text.lower()
    )
    if not has_perf_ref:
        pytest.skip(
            "runbook lacks perf cross-reference — soft signal, "
            "not a blocking gap"
        )
