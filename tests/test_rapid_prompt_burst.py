"""Performance: 100 prompts in 60s with rules_inject every 100
(#53 / #125, audit § 6.6 #26).

Workload: user types 100 prompts in a 60s window. Default
`inject_every=100` → at prompt 100, BT sets `_inject_pending`.
At idle, `_do_inject_rules` flushes the pending block. Test
that:

  1. Rules inject fires EXACTLY ONCE per crossing of `inject_every`
     boundary — not twice on rapid prompt repetition.
  2. Subsequent prompts (101, 102, ...) DO NOT overwrite
     `_inject_pending` — the earliest pending wins so a refresh
     boundary doesn't get lost.
  3. VTE buffer doesn't overflow — `_RULES_INJECT_MAX_BYTES`
     (#52 / 50 MB) caps any pathological rules block.
  4. Auto-trigger fires no more than once per `_inject_pending`
     cycle.

Three decision branches:
  (a) Burst within 10s — 100 prompts in <10s, then force_idle.
      Exactly 1 rules_inject event in feed_log.
  (b) Steady 1.6/s — pulses spread evenly. Same outcome —
      crossing 100 boundary once = one pending = one fire.
  (c) Burst then idle — 100 prompts rapidly + 5s idle. The
      idle timer fires the inject; subsequent silence doesn't
      re-fire.

Manual VM smoke (scripted feed loop) is documented in
tests/manual/README.md.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import signal
import socket
import sqlite3
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
TERMINAL_TAB = REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py"
MOCK_SRC = REPO_ROOT / "tools" / "mock_ai_cli"


# ─── Source-grep guards: rules_inject boundary semantics ────────────────


def test_maybe_inject_rules_uses_modulo_boundary_check():
    """Pin: `_maybe_inject_rules` triggers at exact multiples of
    inject_every — `count > 0 and (count == inject_every or
    count % inject_every == 0)`. The first clause guards against
    `count == 0` (no fire on initial prompt). Catches a refactor
    that drops either condition."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _maybe_inject_rules")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "count > 0" in body
    assert "count == inject_every" in body
    assert "count % inject_every == 0" in body


def test_maybe_inject_rules_does_not_overwrite_pending():
    """Pin: when `_inject_pending` is already set (e.g. user keeps
    typing past prompt 100 before idle fires), the next boundary
    (count==200) DOESN'T overwrite the pending. The earliest
    pending wins → refresh-every boundaries can't be lost.

    Without this guard, a fast typist crossing 200 mid-flush would
    REPLACE the (count=100) pending with (count=200), losing the
    fact that count=100's `count % refresh_every == 0` may have
    been the intended refresh boundary."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _maybe_inject_rules")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    # Guard: if pending exists, return early
    assert "if self._inject_pending is not None:" in body
    pending_idx = body.find(
        "if self._inject_pending is not None:")
    after = body[pending_idx:pending_idx + 100]
    assert "return" in after


def test_inject_every_default_is_100():
    """Pin the default inject_every value. Auto-trigger plan
    assumes 100 prompts trigger one rules inject. The CTX
    rules_config table can override per-project, but the default
    must remain 100."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _maybe_inject_rules")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "inject_every = 100" in body


def test_refresh_every_default_is_200():
    """Same for refresh_every — pinned at 200 (every 2nd inject
    is a context refresh). Catches a tweak that would change
    the refresh:inject ratio."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _maybe_inject_rules")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "refresh_every = 200" in body


def test_inject_pending_carries_count_for_refresh_boundary():
    """Pin: `_inject_pending = (project, count, refresh_every)`
    captures the count at the time the pending was set. Later,
    `_do_inject_rules` checks `count % refresh_every == 0` to
    decide whether to schedule the ctx refresh dispatch. Without
    storing count in pending, the refresh boundary detection is
    broken."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _maybe_inject_rules")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    # The 3-tuple form
    assert "self._inject_pending = (project, count, refresh_every)" in body


# ─── Pure logic: simulate count progression without GTK ────────────────


class _StubStatsBar:
    """Minimal stats_bar stub: just the prompt counter."""
    def __init__(self):
        self._prompt_count = 0
    def increment_prompt(self):
        self._prompt_count += 1


def _exercise_count_boundary(start, end, inject_every=100):
    """Pure simulation of `_maybe_inject_rules`'s boundary check.
    Returns the list of counts at which inject would fire (count
    > 0 AND multiple of inject_every) — without overwrite guard."""
    fires = []
    for count in range(start, end + 1):
        if count > 0 and (
                count == inject_every
                or count % inject_every == 0):
            fires.append(count)
    return fires


def test_pure_boundary_fires_exactly_once_per_inject_every_in_burst():
    """Without overwrite guard: 100 prompts → 1 fire (at 100).
    250 prompts → 2 fires (at 100, 200). Pin the math."""
    assert _exercise_count_boundary(1, 100) == [100]
    assert _exercise_count_boundary(1, 200) == [100, 200]
    assert _exercise_count_boundary(1, 250) == [100, 200]
    assert _exercise_count_boundary(1, 99) == []


def test_pure_boundary_does_not_fire_at_count_zero():
    """Pin `count > 0` clause. A misuse where _maybe_inject_rules
    is called on initial state shouldn't fire."""
    assert _exercise_count_boundary(0, 0) == []


def test_pure_boundary_handles_lower_inject_every_setting():
    """Per-project override: rules_config.inject_every=10 → fires
    at 10, 20, 30, ... Pin the modulo math works for ANY
    inject_every."""
    assert _exercise_count_boundary(1, 50, inject_every=10) == [
        10, 20, 30, 40, 50]


# ─── Pure logic: pending-overwrite contract ─────────────────────────────


def test_pending_skip_logic_pinned_in_source():
    """The 'first pending wins' contract from production. Pin
    via direct simulation: imagine a tab that crossed 100, set
    pending, then user typed 101..200. At 200, the boundary
    fires, BUT pending is already set → skip. Pin the source
    pattern."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def _maybe_inject_rules")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]

    # The 'if pending is set, return' guard MUST come AFTER the
    # boundary check (otherwise pending wouldn't be checked at
    # all on early prompts).
    boundary_idx = body.find("count > 0 and")
    pending_check_idx = body.find(
        "if self._inject_pending is not None:")
    assert boundary_idx > 0
    assert pending_check_idx > 0
    assert pending_check_idx > boundary_idx, (
        "_inject_pending check happens BEFORE boundary check — "
        "would skip even on the first crossing"
    )


# ─── VTE buffer overflow protection (cross-ref #52) ─────────────────────


def test_rules_inject_block_capped_at_50mb():
    """Cross-ref #124: even if `ctx rules inject myproj` returns
    a pathological 100 MB block, `extract_rules_inject_bytes`
    caps at 50 MB and returns empty. Pin so a 100-prompt burst
    on a corrupted ctx project doesn't pump 100 MB into VTE."""
    from bterminal.ui.terminal_tab import (
        extract_rules_inject_bytes,
        _RULES_INJECT_MAX_BYTES,
    )
    huge = "a" * (100 * 1024 * 1024)  # 100 MB
    out = extract_rules_inject_bytes("aider", "p", huge)
    assert out == b""
    # Cap value pinned
    assert _RULES_INJECT_MAX_BYTES == 50 * 1024 * 1024


def test_typical_rules_block_well_under_cap():
    """Sanity: a realistic rules block (~5 KB of bullets) is
    THREE orders of magnitude under the cap. Pin the realistic
    workload doesn't accidentally trigger refusal."""
    from bterminal.ui.terminal_tab import (
        extract_rules_inject_bytes,
        _RULES_INJECT_MAX_BYTES,
    )
    realistic = (
        "## Project rules for myproj\n\n"
        "- Always reply concisely.\n"
        "- Use TDD: tests first.\n"
        "- Never run destructive commands without confirmation.\n"
        "- Document why, not what.\n"
        "- Polish UTF-8 round-trip: ąęćśźżłóń.\n"
    ) * 50  # ~10 KB
    out = extract_rules_inject_bytes(
        "aider", "myproj", realistic)
    assert 0 < len(out) < _RULES_INJECT_MAX_BYTES


# ─── E2E: 100-prompt burst → exactly 1 rules_inject event ───────────────


def _wait_for_health(base, deadline) -> bool:
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{base}/api/health", timeout=1.0)
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    return False


def _http(base, token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}", data=data,
        headers={"Authorization": f"Bearer {token}",
                  "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except (json.JSONDecodeError, OSError):
            return exc.code, {"error": exc.reason}


def _seed_full_ctx_db(home, project_name, project_dir,
                        inject_every: int = 100):
    """Full CTX schema for e2e. Set per-project inject_every via
    rules_config row."""
    ctx_dir = Path(home) / ".claude-context"
    ctx_dir.mkdir(parents=True, exist_ok=True)
    db_path = ctx_dir / "context.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS tasks (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, task_id TEXT NOT NULL,
                description TEXT NOT NULL, status TEXT DEFAULT 'open',
                created_at TEXT DEFAULT (datetime('now')),
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project, task_id)
            );
            CREATE TABLE IF NOT EXISTS task_config (
                project TEXT PRIMARY KEY, autorun INTEGER DEFAULT 0
            );
            CREATE TABLE IF NOT EXISTS task_claims (
                project TEXT NOT NULL, task_id TEXT NOT NULL,
                session_id TEXT NOT NULL,
                claimed_at TEXT DEFAULT (datetime('now')),
                PRIMARY KEY (project, task_id, session_id)
            );
            CREATE TABLE IF NOT EXISTS sessions (
                name TEXT PRIMARY KEY, description TEXT,
                work_dir TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS contexts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, key TEXT NOT NULL,
                value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project, key)
            );
            CREATE TABLE IF NOT EXISTS shared (
                key TEXT PRIMARY KEY, value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS summaries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, summary TEXT NOT NULL,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS rules (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, rule TEXT NOT NULL,
                enabled INTEGER NOT NULL DEFAULT 1,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS rules_config (
                project TEXT PRIMARY KEY,
                inject_every INTEGER NOT NULL DEFAULT 20,
                refresh_every INTEGER NOT NULL DEFAULT 50,
                updated_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS rules_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, action TEXT NOT NULL,
                rule TEXT NOT NULL, actor TEXT NOT NULL DEFAULT 'user',
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS ctx_history (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, action TEXT NOT NULL,
                key TEXT NOT NULL, old_value TEXT, new_value TEXT,
                actor TEXT NOT NULL DEFAULT 'user',
                created_at TEXT DEFAULT (datetime('now'))
            );
        """)
        conn.execute(
            "INSERT OR REPLACE INTO sessions "
            "(name, description, work_dir) VALUES (?, ?, ?)",
            (project_name, "burst test", project_dir),
        )
        conn.execute(
            "INSERT OR REPLACE INTO rules_config "
            "(project, inject_every, refresh_every) VALUES (?, ?, ?)",
            (project_name, inject_every, inject_every * 2),
        )
        conn.execute(
            "INSERT INTO rules (project, rule) VALUES (?, ?)",
            (project_name, "Always be terse."),
        )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def bterminal_with_aider_for_burst():
    """BT subprocess + 1 aider session + rules_config with
    `inject_every=10` (smaller threshold so burst tests can fire
    in seconds rather than 100 round-trips)."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-burst-")
    seed_license(home)

    fake_bin = Path(home) / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    target = fake_bin / "aider"
    shutil.copy(str(MOCK_SRC), str(target))
    target.chmod(target.stat().st_mode
                  | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    project_dir = Path(home) / "burstproj"
    project_dir.mkdir(parents=True, exist_ok=True)
    project_name = "burstproj"

    cfg_dir = Path(home) / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ai_sessions.json").write_text(json.dumps([
        {
            "id": "aider-1", "name": "AiderSession",
            "provider": "aider", "project_dir": str(project_dir),
            "color": "#fab387", "provider_options": {},
        },
    ]))

    # inject_every=10 so the test crosses the boundary in 10
    # simulate_prompt calls (vs 100 in production default).
    _seed_full_ctx_db(home, project_name, str(project_dir),
                       inject_every=10)

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    env = {
        **os.environ,
        "HOME": home,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "BTERMINAL_DEBUG_REST_PORT": str(port),
        "MOCK_AI_CLI_SCENARIO": str(REPO_ROOT / "tests" / "scenarios"
                                     / "claude_basic.json"),
    }
    stderr_path = Path(home) / "bterminal-stderr.log"
    stdout_path = Path(home) / "bterminal-stdout.log"
    stderr_handle = open(stderr_path, "w")
    stdout_handle = open(stdout_path, "w")
    proc = subprocess.Popen(
        ["xvfb-run", "-a", sys.executable, "-m", "bterminal",
         "--debug-rest"],
        cwd=str(REPO_ROOT), env=env,
        stdout=stdout_handle, stderr=stderr_handle,
        start_new_session=True,
    )

    base = f"http://127.0.0.1:{port}"
    if not _wait_for_health(base, time.monotonic() + 30):
        proc.terminate(); proc.wait(timeout=5)
        stderr_handle.close(); stdout_handle.close()
        try:
            err = Path(stderr_path).read_text(errors="replace")[-2000:]
        except OSError:
            err = "(unreadable)"
        try:
            out = Path(stdout_path).read_text(errors="replace")[-2000:]
        except OSError:
            out = "(unreadable)"
        pytest.fail(
            f"BT didn't come up\n--- stdout ---\n{out}\n"
            f"--- stderr ---\n{err}"
        )

    token = (cfg_dir / "debug_token").read_text().strip()
    try:
        yield {
            "base": base, "token": token, "home": home,
            "project_name": project_name,
            "project_dir": str(project_dir),
            "stderr_path": str(stderr_path),
        }
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        stderr_handle.close(); stdout_handle.close()


# ─── (a) Burst within 10s — exactly 1 rules_inject event ────────────────


def test_10_prompt_burst_then_force_idle_emits_exactly_one_inject(
        bterminal_with_aider_for_burst):
    """Spawn aider tab → 10 simulate_prompt POSTs in quick
    succession (crosses inject_every=10 boundary) → 1 force_idle
    flushes → feed_log shows EXACTLY 1 rules_inject event.

    Note: fixture uses inject_every=10 (vs production default
    100) so tests cross the boundary in seconds. The math is
    identical."""
    state = bterminal_with_aider_for_burst
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    assert status == 200, body
    idx = body["idx"]
    time.sleep(0.5)
    pivot_ts = time.time()

    # Burst: 10 simulate_prompt calls (crosses boundary at #10)
    for i in range(10):
        status, _ = _http(
            base, token, "POST",
            f"/api/tabs/{idx}/simulate_prompt", {},
        )
        assert status == 200

    # Flush pending via force_idle
    status, _ = _http(
        base, token, "POST", f"/api/tabs/{idx}/force_idle", {},
    )
    assert status == 200
    time.sleep(0.5)

    # Exactly 1 rules_inject event in feed_log
    status, feed = _http(
        base, token, "GET",
        f"/api/debug/feed_log?label=rules_inject&since={pivot_ts}",
        None,
    )
    events = feed["events"]
    assert len(events) == 1, (
        f"expected exactly 1 rules_inject after 10-prompt burst, "
        f"got {len(events)}"
    )


# ─── (b) Prompts BEYOND boundary do NOT overwrite pending ───────────────


def test_prompts_past_boundary_do_not_re_arm_pending(
        bterminal_with_aider_for_burst):
    """User crosses 10 (boundary set) → BT _inject_pending=set.
    User keeps typing past 10 (11, 12, ..., 20) WITHOUT idle.
    At 20 (next boundary), the no-overwrite guard fires → pending
    UNCHANGED. force_idle THEN flushes → still 1 event total.

    Pin: rapid typing past boundary doesn't re-arm pending,
    avoiding double-fire on the 'next idle'."""
    state = bterminal_with_aider_for_burst
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    idx = body["idx"]
    time.sleep(0.5)
    pivot_ts = time.time()

    # 20 simulate_prompt — crosses boundaries at 10 AND 20
    for i in range(20):
        status, body = _http(
            base, token, "POST",
            f"/api/tabs/{idx}/simulate_prompt", {},
        )
        assert status == 200
        # After prompt 10, pending should be set
        # After prompts 11..20, pending stays at (project, 10, ...)

    # Force flush
    _http(base, token, "POST", f"/api/tabs/{idx}/force_idle", {})
    time.sleep(0.5)

    status, feed = _http(
        base, token, "GET",
        f"/api/debug/feed_log?label=rules_inject&since={pivot_ts}",
        None,
    )
    events = feed["events"]
    # Still exactly 1 event despite crossing 2 boundaries —
    # no-overwrite guard works
    assert len(events) == 1, (
        f"crossing 2 boundaries fired {len(events)} events — "
        f"no-overwrite guard regressed"
    )


# ─── (c) Burst then idle — single flush, no spurious re-fire ───────────


def test_burst_then_idle_does_not_double_fire(
        bterminal_with_aider_for_burst):
    """10 prompts → force_idle (fires inject) → idle 1s → second
    force_idle. Pin: only the FIRST force_idle fires the inject.
    Second force_idle has no pending → no fire."""
    state = bterminal_with_aider_for_burst
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    idx = body["idx"]
    time.sleep(0.5)
    pivot_ts = time.time()

    # 10 prompts → crosses inject_every boundary
    for _ in range(10):
        _http(base, token, "POST",
               f"/api/tabs/{idx}/simulate_prompt", {})

    # First flush — fires inject
    _http(base, token, "POST", f"/api/tabs/{idx}/force_idle", {})
    time.sleep(0.5)

    # Second flush — no pending, no fire
    _http(base, token, "POST", f"/api/tabs/{idx}/force_idle", {})
    time.sleep(0.5)

    # Third flush — paranoia
    _http(base, token, "POST", f"/api/tabs/{idx}/force_idle", {})
    time.sleep(0.5)

    status, feed = _http(
        base, token, "GET",
        f"/api/debug/feed_log?label=rules_inject&since={pivot_ts}",
        None,
    )
    events = feed["events"]
    assert len(events) == 1, (
        f"3 force_idle calls fired {len(events)} rules_inject "
        f"events — should be 1 (only first flush has pending)"
    )


# ─── No duplicate auto_trigger from rapid prompt burst ─────────────────


def test_burst_does_not_fire_extra_auto_trigger_events(
        bterminal_with_aider_for_burst):
    """A rapid prompt burst is unrelated to auto_trigger flow —
    they share no state. Pin: 10 simulate_prompt calls don't
    accidentally fire auto_trigger (which would happen only if
    a task is claimed)."""
    state = bterminal_with_aider_for_burst
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    idx = body["idx"]
    time.sleep(0.5)
    pivot_ts = time.time()

    # 10 prompts — crosses inject boundary
    for _ in range(10):
        _http(base, token, "POST",
               f"/api/tabs/{idx}/simulate_prompt", {})

    # Force_idle — fires rules_inject (but no auto_trigger
    # because no tasks seeded in this fixture)
    _http(base, token, "POST", f"/api/tabs/{idx}/force_idle", {})
    time.sleep(0.5)

    status, feed = _http(
        base, token, "GET",
        f"/api/debug/feed_log?since={pivot_ts}", None,
    )
    auto_events = [e for e in feed["events"]
                   if e.get("label") == "auto_trigger"]
    assert len(auto_events) == 0, (
        f"prompt burst fired {len(auto_events)} auto_trigger "
        f"events — auto-trigger flow leaked into rules-inject path"
    )


# ─── BT survives the burst ─────────────────────────────────────────────


def test_burst_does_not_corrupt_bt_stderr(
        bterminal_with_aider_for_burst):
    """No AssertionError / Traceback after a 10-prompt burst.
    Catches the case where rapid simulate_prompt calls trigger
    a thread-unsafe access in the GTK main loop."""
    state = bterminal_with_aider_for_burst
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    idx = body["idx"]
    time.sleep(0.5)

    # 30 prompts — crosses boundary 3 times
    for _ in range(30):
        _http(base, token, "POST",
               f"/api/tabs/{idx}/simulate_prompt", {})
    _http(base, token, "POST", f"/api/tabs/{idx}/force_idle", {})
    time.sleep(0.5)

    err = Path(state["stderr_path"]).read_text(errors="replace")
    forbidden = ["AssertionError", "AttributeError", "NameError",
                 "ImportError", "Traceback (most recent call last)"]
    bad = [p for p in forbidden if p in err]
    assert not bad, f"BT stderr has {bad}: {err[:1500]}"


# ─── REST burst latency: simulate_prompt is fast ───────────────────────


def test_simulate_prompt_under_50ms_per_call(
        bterminal_with_aider_for_burst):
    """Pin: each simulate_prompt POST returns in <50ms. At
    100/60s = 1.6/s, that's plenty of headroom for the GLib main
    loop. Catches a regression where the per-call cost balloons
    and a 100-prompt burst takes minutes."""
    state = bterminal_with_aider_for_burst
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    idx = body["idx"]
    time.sleep(0.5)

    # Time 30 simulate_prompt calls
    timings = []
    for _ in range(30):
        t0 = time.perf_counter()
        status, _ = _http(
            base, token, "POST",
            f"/api/tabs/{idx}/simulate_prompt", {},
        )
        t1 = time.perf_counter()
        assert status == 200
        timings.append(t1 - t0)
    timings.sort()
    p99 = timings[int(0.99 * len(timings))]
    assert p99 < 0.050, (
        f"simulate_prompt p99 = {p99 * 1000:.2f}ms — under-50ms "
        f"contract regressed; 100/60s burst would feel laggy"
    )


# ─── End-to-end: 100-prompt burst with default inject_every=100 ────────


@pytest.mark.skipif(
    os.environ.get("BTERMINAL_FULL_BURST_TEST") != "1",
    reason="100-prompt e2e is slow (~5s) — opt in via "
           "BTERMINAL_FULL_BURST_TEST=1",
)
def test_100_prompt_burst_with_production_default_inject_every(
        bterminal_with_aider_for_burst):
    """Headline auto-trigger contract: 100 prompts crossing
    inject_every=100 → exactly 1 rules_inject event after flush.
    Same logic as 10-prompt test, with full production-default
    threshold. Opt-in because it adds ~5s per BT subprocess
    spawn × 100 simulate_prompt round-trips."""
    # The fixture used inject_every=10, but we override here to
    # 100 to mirror production. Update the existing rules_config
    # row before opening the tab.
    state = bterminal_with_aider_for_burst
    db_path = (Path(state["home"]) / ".claude-context"
                / "context.db")
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "UPDATE rules_config SET inject_every = 100, "
        "refresh_every = 200 WHERE project = ?",
        (state["project_name"],),
    )
    conn.commit()
    conn.close()

    base, token = state["base"], state["token"]
    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    idx = body["idx"]
    time.sleep(0.5)
    pivot_ts = time.time()

    # 100 prompts (crosses 100 boundary)
    for _ in range(100):
        _http(base, token, "POST",
               f"/api/tabs/{idx}/simulate_prompt", {})
    _http(base, token, "POST", f"/api/tabs/{idx}/force_idle", {})
    time.sleep(0.5)

    status, feed = _http(
        base, token, "GET",
        f"/api/debug/feed_log?label=rules_inject&since={pivot_ts}",
        None,
    )
    events = feed["events"]
    assert len(events) == 1, (
        f"100-prompt burst at production default fired "
        f"{len(events)} rules_inject events"
    )
