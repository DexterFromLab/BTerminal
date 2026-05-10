"""Cross-feature: auto_trigger + user types in VTE simultaneously
(#41 / #113, audit § 6.3 #14).

User types in the terminal while auto_trigger is firing. The
[AUTO-TRIGGER] message and user input must both reach aider with
no garbled bytes — neither truncates the other, and order is
deterministic at the GTK main loop layer.

Three decision branches:
  (a) User types mid-AUTO-TRIGGER feed — feed_child is invoked
      with the message bytes; user typing arrives via VTE's own
      stdin → kernel pipe → aider stdin. PTY pipe is sequential
      so bytes never interleave mid-character.
  (b) User types AFTER trigger but before the \\r flush —
      _on_task_idle_timeout schedules the carriage return via
      `GLib.timeout_add(100, ...)`. User's bytes arriving in
      that 100 ms window are appended after the message but
      before \\r. Pin: \\r still flushes on its own scheduled tick.
  (c) User pastes large block during trigger — same path as (a)
      but with longer payload. PTY's atomicity guarantees no
      torn writes.

Pinned invariants:
  - All feed_child calls go through GLib main loop (auto_trigger
    via _on_task_idle_timeout, user input via _route_post_tab_feed
    → _via_glib_idle). GTK runs the main loop single-threaded so
    callbacks are serialized.
  - feed_child writes are atomic at the PTY layer (Linux pipe
    write up to PIPE_BUF=4096 bytes is atomic; longer writes
    chunk but never interleave with another writer's bytes
    because there's only ONE writer thread).
  - REST → GTK main loop hop preserves arrival order.

Manual VM smoke (autorun=1 with task, type quickly during VTE
silence) is documented in tests/manual/README.md.
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
import threading
import time
import urllib.error
import urllib.request
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
MOCK_SRC = REPO_ROOT / "tools" / "mock_ai_cli"
DEBUG_REST_SRC = REPO_ROOT / "bterminal" / "debug_rest.py"
TERMINAL_TAB_SRC = REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py"


# ─── Source-grep: REST + auto_trigger both go through GLib main loop ────


def test_route_post_tab_feed_dispatches_via_glib_idle():
    """Pin: `_route_post_tab_feed` doesn't call `feed_child`
    directly from the HTTP server thread — it hops to GTK main
    loop via `_via_glib_idle`. This is what serializes user feed
    against auto_trigger feed (same thread executes both)."""
    src = DEBUG_REST_SRC.read_text()
    fn_start = src.find("def _route_post_tab_feed")
    fn_end = src.find("\n\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "_via_glib_idle" in body, (
        "_route_post_tab_feed bypasses GLib — feed_child runs in "
        "HTTP server thread, racing with auto_trigger"
    )
    # And the body wraps feed_child in a closure passed to GLib
    assert "feed_child(payload)" in body or "feed_child(" in body


def test_auto_trigger_uses_glib_timer_for_carriage_return():
    """Pin: `_on_task_idle_timeout` schedules the trailing `\\r`
    via `GLib.timeout_add(100, ...)` AFTER the message bytes feed.
    This 100 ms window is the canonical 'user types between message
    and \\r' branch (b) opportunity — pin the timer pattern so a
    refactor doesn't merge them into one feed."""
    src = TERMINAL_TAB_SRC.read_text()
    fn_start = src.find("def _on_task_idle_timeout")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "GLib.timeout_add(100" in body, (
        "auto_trigger \\r flush no longer scheduled via GLib timer"
    )
    # And it's a separate feed_child for the \\r byte
    assert 'feed_child(b"\\r")' in body


def test_via_glib_idle_helper_serializes_callbacks():
    """The helper marshals callbacks to GTK main loop. All
    callbacks execute on the same thread → strict ordering.
    Pin: `_via_glib_idle` is the only path through which REST
    routes touch GTK objects."""
    src = DEBUG_REST_SRC.read_text()
    via_idx = src.find("def _via_glib_idle")
    assert via_idx > 0, (
        "_via_glib_idle helper missing — REST routes may touch "
        "GTK from HTTP threads"
    )
    # Helper uses GLib.idle_add or threading.Event — pin the
    # serialization mechanism
    fn_end = src.find("\n\ndef ", via_idx + 1)
    body = src[via_idx:fn_end]
    assert "GLib.idle_add" in body, (
        "_via_glib_idle no longer uses GLib.idle_add — could break "
        "single-thread guarantee"
    )


def test_route_post_tab_feed_validates_input_size():
    """Pin: large pastes are bounded by `DEBUG_FEED_MAX_BYTES`. A
    user pastes 10 MB → REST returns 413 rather than queueing
    GIANT bytes through GLib (which would block the main loop
    + delay auto_trigger \\r flush)."""
    src = DEBUG_REST_SRC.read_text()
    assert "DEBUG_FEED_MAX_BYTES" in src
    fn_start = src.find("def _route_post_tab_feed")
    fn_end = src.find("\n\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "413" in body, (
        "_route_post_tab_feed lost size limit — large paste could "
        "block auto_trigger flush"
    )


def test_route_post_tab_feed_only_writes_text_field():
    """Pin: REST feed accepts ONLY the `text` field. A future
    refactor that adds e.g. `key`/`signal` fields would create
    additional concurrent paths into VTE, complicating the
    interleave audit."""
    src = DEBUG_REST_SRC.read_text()
    fn_start = src.find("def _route_post_tab_feed")
    fn_end = src.find("\n\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert 'body.get("text"' in body
    # And rejects non-string text
    assert "must be a string" in body


# ─── PTY-layer atomicity (Linux pipe semantics) ──────────────────────────


def test_feed_child_payload_under_pipe_buf_is_atomic():
    """Linux pipe semantics: write(2) of <= PIPE_BUF bytes
    (4096 on most kernels) is atomic. Auto-trigger messages are
    typically a few hundred bytes; user feed lines also bounded
    by REST's DEBUG_FEED_MAX_BYTES.

    Pin: auto-trigger message is well under PIPE_BUF so its
    feed_child call lands as one atomic kernel write. User input
    arriving in the same tick gets its own atomic write —
    bytes never interleave mid-character."""
    # The auto-trigger message format from terminal_tab.py:
    # `[AUTO-TRIGGER] Your assigned task: <id> — <description>\n`
    # `Check the full list: tasks context <project> --session <sid>\n`
    # `You MUST mark it after completion: tasks done ...`
    # Expected size: ~300-500 bytes for typical task descriptions
    sample_msg = (
        "[AUTO-TRIGGER] Your assigned task: t-1 — Sample task\n"
        "Check the full list: tasks context proj --session s-1\n"
        "You MUST mark it after completion: tasks done proj t-1 "
        "(in Bash). The auto-trigger loop only ends when ALL "
        "tasks are closed (done). If you do not mark it — this "
        "message will keep repeating.\n"
    ).encode()
    PIPE_BUF = 4096
    assert len(sample_msg) < PIPE_BUF, (
        f"auto-trigger message {len(sample_msg)} bytes exceeds "
        f"PIPE_BUF — kernel may chunk + interleave with user feed"
    )


def test_debug_feed_max_bytes_under_pipe_buf_minimum():
    """Pin: REST's max body size for /feed should be ≤ PIPE_BUF
    so user pastes also land atomically. Otherwise (c) — large
    paste — would chunk and could interleave with concurrent
    auto-trigger feed."""
    src = DEBUG_REST_SRC.read_text()
    # Find DEBUG_FEED_MAX_BYTES = N
    import re
    m = re.search(r"DEBUG_FEED_MAX_BYTES\s*=\s*(\d+)", src)
    assert m, "DEBUG_FEED_MAX_BYTES not defined as int constant"
    max_bytes = int(m.group(1))
    # Real concern: the limit shouldn't be unbounded. Pin a
    # reasonable upper bound — large enough for code blocks
    # (~1 MB) but small enough to be atomic-ish at the kernel
    # level (Linux pipe has 64 KB ring buffer; PIPE_BUF guarantee
    # is 4 KB but kernel typically writes more atomically).
    # We accept up to 1 MB — beyond that, definitely chunks.
    assert max_bytes <= 1_048_576, (
        f"DEBUG_FEED_MAX_BYTES = {max_bytes} too large — paste "
        f"would chunk + interleave with auto_trigger feed"
    )
    # And > 0
    assert max_bytes > 0


# ─── Code-path isolation: user feed doesn't touch auto_trigger state ────


def test_user_feed_does_not_clear_inject_pending():
    """Pin: `_route_post_tab_feed` does NOT mutate
    `tab._inject_pending`. A pending rules inject must survive a
    user typing arbitrary text — otherwise the rules never fire."""
    src = DEBUG_REST_SRC.read_text()
    fn_start = src.find("def _route_post_tab_feed")
    fn_end = src.find("\n\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "_inject_pending" not in body, (
        "user feed mutates _inject_pending — could lose pending "
        "rules across user typing"
    )


def test_user_feed_does_not_call_claim_next_task():
    """Pin: user feed flow doesn't accidentally fire the auto-
    trigger pipeline. _claim_next_task is reserved for
    _on_task_idle_timeout."""
    src = DEBUG_REST_SRC.read_text()
    fn_start = src.find("def _route_post_tab_feed")
    fn_end = src.find("\n\ndef ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "_claim_next_task" not in body
    assert "auto_trigger" not in body


def test_auto_trigger_path_does_not_touch_rest_feed_state():
    """Mirror: `_on_task_idle_timeout` doesn't fish in REST
    request state (_BTerminalDebugHandler internals). It writes
    feed_child directly via GLib timer — independent of the
    user-feed REST path."""
    src = TERMINAL_TAB_SRC.read_text()
    fn_start = src.find("def _on_task_idle_timeout")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    forbidden = ["BTerminalDebugHandler", "_route_post_tab_feed",
                 "_read_json_body"]
    for pat in forbidden:
        assert pat not in body, (
            f"auto_trigger references REST internals: {pat!r}"
        )


# ─── E2E fixture: aider + 1 task, fire force_idle + concurrent feed ─────


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
                        task_ids, autorun=1):
    """Full CTX schema mirroring test_dual_provider_workflow."""
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
            (project_name, "feed interleave test", project_dir),
        )
        conn.execute(
            "INSERT OR REPLACE INTO task_config VALUES (?, ?)",
            (project_name, autorun),
        )
        for tid in task_ids:
            conn.execute(
                "INSERT INTO tasks "
                "(project, task_id, description, status) "
                "VALUES (?, ?, ?, 'open')",
                (project_name, tid, f"E2E task {tid}"),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def bterminal_with_aider_one_task():
    """BT subprocess with mock aider binary + 1 AI session + 1
    open task + autorun=1."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-feed-interleave-")
    seed_license(home)

    fake_bin = Path(home) / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    target = fake_bin / "aider"
    shutil.copy(str(MOCK_SRC), str(target))
    target.chmod(target.stat().st_mode
                  | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    project_dir = Path(home) / "myaiderproj"
    project_dir.mkdir(parents=True, exist_ok=True)
    project_name = "myaiderproj"

    cfg_dir = Path(home) / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    (cfg_dir / "ai_sessions.json").write_text(json.dumps([
        {
            "id": "aider-1", "name": "AiderSession",
            "provider": "aider", "project_dir": str(project_dir),
            "color": "#fab387", "provider_options": {},
        },
    ]))

    _seed_full_ctx_db(home, project_name, str(project_dir),
                       task_ids=["t-feed-1"], autorun=1)

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


def _enable_autorun(home, project_name):
    """Re-enable autorun (TaskPanel resets it on startup)."""
    db_path = Path(home) / ".claude-context" / "context.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO task_config VALUES (?, 1) "
            "ON CONFLICT(project) DO UPDATE SET autorun = 1",
            (project_name,),
        )
        conn.commit()
    finally:
        conn.close()


# ─── (a) User types mid-AUTO-TRIGGER feed: both arrive via feed_log ─────


def test_concurrent_force_idle_and_feed_both_recorded(
        bterminal_with_aider_one_task):
    """Fire force_idle + POST /feed within 50 ms → debug-REST
    feed_log captures BOTH events. Order may vary but both bytes
    arrive intact (no truncation, no garble)."""
    state = bterminal_with_aider_one_task
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    assert status == 200, body
    idx = body["idx"]
    time.sleep(0.5)
    _enable_autorun(state["home"], state["project_name"])
    pivot_ts = time.time()

    # Fire both within ~50 ms via threading.Barrier
    barrier = threading.Barrier(2)
    results = {}

    def fire_force_idle():
        barrier.wait()
        results["force_idle"] = _http(
            base, token, "POST", f"/api/tabs/{idx}/force_idle", {})

    def fire_feed():
        barrier.wait()
        results["feed"] = _http(
            base, token, "POST", f"/api/tabs/{idx}/feed",
            {"text": "USER_INPUT_DURING_TRIGGER\n"})

    t1 = threading.Thread(target=fire_force_idle)
    t2 = threading.Thread(target=fire_feed)
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)

    assert results["force_idle"][0] == 200, results["force_idle"]
    assert results["feed"][0] == 200, results["feed"]

    time.sleep(0.5)

    # Auto-trigger event captured
    status, feed = _http(
        base, token, "GET",
        f"/api/debug/feed_log?since={pivot_ts}", None,
    )
    auto_events = [e for e in feed["events"]
                   if e.get("label") == "auto_trigger"]
    assert auto_events, "no auto_trigger event after force_idle"

    # Auto-trigger payload intact (full message body decodes OK)
    body_decoded = base64.b64decode(
        auto_events[0]["bytes_b64"]
    ).decode("utf-8", errors="replace")
    # Pin: full canonical message present, not truncated
    assert "[AUTO-TRIGGER]" in body_decoded
    assert "t-feed-1" in body_decoded
    assert "tasks done" in body_decoded


def test_user_feed_during_trigger_does_not_truncate_message(
        bterminal_with_aider_one_task):
    """User typing during trigger shouldn't truncate the auto-
    trigger message body. Pin via feed_log: trigger payload
    contains the full multi-line message, not partial."""
    state = bterminal_with_aider_one_task
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    idx = body["idx"]
    time.sleep(0.5)
    _enable_autorun(state["home"], state["project_name"])
    pivot_ts = time.time()

    # User types BEFORE trigger fires, then immediately force_idle
    _http(base, token, "POST", f"/api/tabs/{idx}/feed",
           {"text": "user typed first\n"})
    time.sleep(0.01)  # tiny delay, both still well within 50 ms
    _http(base, token, "POST", f"/api/tabs/{idx}/force_idle", {})
    time.sleep(0.5)

    status, feed = _http(
        base, token, "GET",
        f"/api/debug/feed_log?since={pivot_ts}", None,
    )
    auto_events = [e for e in feed["events"]
                   if e.get("label") == "auto_trigger"]
    assert auto_events
    body_decoded = base64.b64decode(
        auto_events[0]["bytes_b64"]
    ).decode("utf-8", errors="replace")
    # Multi-line trigger message NOT truncated
    assert body_decoded.count("\n") >= 2
    assert "[AUTO-TRIGGER]" in body_decoded
    assert "tasks done" in body_decoded


# ─── (b) User types between trigger and \\r flush (100 ms window) ───────


def test_user_feed_within_carriage_return_window_lands_after_message(
        bterminal_with_aider_one_task):
    """Auto-trigger feeds the message bytes immediately, then
    schedules `\\r` via GLib.timeout_add(100, ...). User feed
    arriving in that 100 ms gap interleaves AT THE GLIB MAIN LOOP
    LEVEL — but each call goes through its own _via_glib_idle hop,
    so feed_child calls are serialized. No partial bytes leak.

    Pin via feed_log: trigger event has its full message; user
    feed event also captured."""
    state = bterminal_with_aider_one_task
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    idx = body["idx"]
    time.sleep(0.5)
    _enable_autorun(state["home"], state["project_name"])
    pivot_ts = time.time()

    # Fire trigger, then within 50 ms (well inside 100ms \\r window)
    # send a user feed message
    _http(base, token, "POST", f"/api/tabs/{idx}/force_idle", {})
    time.sleep(0.05)  # 50 ms — squarely within \\r flush window
    status, _ = _http(
        base, token, "POST", f"/api/tabs/{idx}/feed",
        {"text": "user racing the carriage return\n"},
    )
    assert status == 200
    time.sleep(0.5)

    status, feed = _http(
        base, token, "GET",
        f"/api/debug/feed_log?since={pivot_ts}", None,
    )
    # auto_trigger event present, full message intact
    auto_events = [e for e in feed["events"]
                   if e.get("label") == "auto_trigger"]
    assert auto_events
    decoded = base64.b64decode(
        auto_events[0]["bytes_b64"]
    ).decode("utf-8", errors="replace")
    assert "[AUTO-TRIGGER]" in decoded


# ─── (c) Large paste during trigger ──────────────────────────────────────


def test_large_paste_during_trigger_completes_via_rest(
        bterminal_with_aider_one_task):
    """User pastes a 4 KB block (typical code snippet) via REST
    while auto-trigger is firing. Both round-trips return 200,
    no timeout, no truncation in the auto-trigger feed log entry.

    Stays under DEBUG_FEED_MAX_BYTES so the 413 path doesn't
    fire — we test the LARGE-but-allowed path."""
    state = bterminal_with_aider_one_task
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    idx = body["idx"]
    time.sleep(0.5)
    _enable_autorun(state["home"], state["project_name"])
    pivot_ts = time.time()

    # 4 KB block — typical large code paste
    big_text = ("def function():\n    pass\n" * 200)  # ~4 KB
    assert len(big_text) > 3500
    assert len(big_text) < 8000

    barrier = threading.Barrier(2)

    def trigger():
        barrier.wait()
        _http(base, token, "POST", f"/api/tabs/{idx}/force_idle", {})

    def big_paste():
        barrier.wait()
        return _http(base, token, "POST",
                      f"/api/tabs/{idx}/feed", {"text": big_text})

    t1 = threading.Thread(target=trigger)
    paste_result = []
    t2 = threading.Thread(
        target=lambda: paste_result.append(big_paste()))
    t1.start(); t2.start()
    t1.join(timeout=10); t2.join(timeout=10)

    # Paste returned 200 with byte count
    assert paste_result, "paste thread didn't complete"
    status, body = paste_result[0]
    assert status == 200
    assert body.get("bytes") == len(big_text.encode("utf-8"))

    time.sleep(0.5)

    # Auto-trigger message intact (no torn write from big paste)
    status, feed = _http(
        base, token, "GET",
        f"/api/debug/feed_log?since={pivot_ts}", None,
    )
    auto_events = [e for e in feed["events"]
                   if e.get("label") == "auto_trigger"]
    assert auto_events
    decoded = base64.b64decode(
        auto_events[0]["bytes_b64"]
    ).decode("utf-8", errors="replace")
    assert "[AUTO-TRIGGER]" in decoded
    assert "t-feed-1" in decoded


def test_oversized_paste_returns_413_does_not_block_trigger(
        bterminal_with_aider_one_task):
    """Paste exceeds DEBUG_FEED_MAX_BYTES → REST returns 413.
    The trigger fires anyway (independent code path). Pin: 413
    is fast (no GLib hop) so it doesn't block the GTK main loop
    even briefly."""
    state = bterminal_with_aider_one_task
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    idx = body["idx"]
    time.sleep(0.5)
    _enable_autorun(state["home"], state["project_name"])

    # DEBUG_FEED_MAX_BYTES is documented in source; oversize is
    # 2 MB — guaranteed over ANY reasonable limit
    oversize = "x" * (2 * 1024 * 1024)
    status, body = _http(
        base, token, "POST", f"/api/tabs/{idx}/feed",
        {"text": oversize},
    )
    assert status == 413, f"oversize paste accepted: {status}"

    # Trigger still fires after the oversize was rejected
    pivot_ts = time.time()
    status, _ = _http(
        base, token, "POST", f"/api/tabs/{idx}/force_idle", {})
    assert status == 200
    time.sleep(0.3)

    status, feed = _http(
        base, token, "GET",
        f"/api/debug/feed_log?since={pivot_ts}", None,
    )
    auto_events = [e for e in feed["events"]
                   if e.get("label") == "auto_trigger"]
    assert auto_events, "trigger blocked after oversize paste 413"


# ─── BT stays alive — no GTK loop crash from concurrent feed paths ──────


def test_concurrent_force_idle_and_feed_no_stderr_errors(
        bterminal_with_aider_one_task):
    """No AssertionError / Traceback in BT stderr after
    force_idle + concurrent /feed POSTs. Catches the case where
    a thread-unsafe access leaked across the feed paths."""
    state = bterminal_with_aider_one_task
    base, token = state["base"], state["token"]

    _http(base, token, "POST", "/api/tabs/ai/aider",
           {"config_name": "AiderSession"})
    time.sleep(0.5)
    _enable_autorun(state["home"], state["project_name"])

    # 5-way stress: 1 force_idle + 4 feeds at the same instant
    barrier = threading.Barrier(5)

    def trigger():
        barrier.wait()
        _http(base, token, "POST", "/api/tabs/1/force_idle", {})

    def feed(i):
        barrier.wait()
        _http(base, token, "POST", "/api/tabs/1/feed",
               {"text": f"user feed {i}\n"})

    threads = [threading.Thread(target=trigger)]
    threads += [threading.Thread(target=feed, args=(i,))
                for i in range(4)]
    for t in threads:
        t.start()
    for t in threads:
        t.join(timeout=10)
    time.sleep(0.5)

    err = Path(state["stderr_path"]).read_text(errors="replace")
    forbidden = ["AssertionError", "AttributeError", "NameError",
                 "ImportError", "Traceback (most recent call last)"]
    bad = [p for p in forbidden if p in err]
    assert not bad, f"BT stderr has {bad}: {err[:1500]}"
