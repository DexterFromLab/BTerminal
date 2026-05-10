"""Aider task_auto_trigger parity (#25 / #97).

Verifies that Aider tabs participate in the [AUTO-TRIGGER] flow with
the same observable behavior as Claude/Copilot tabs:

  Headline e2e (fixture-based):
    - spawn Aider tab in BT, seed CTX DB with autorun=1 + 1 task
    - force_idle on tab → [AUTO-TRIGGER] message fed to aider via
      feed_child, captured by the feed_log debug surface
    - task claimed atomically (no double-fire on repeated force_idle)

  Edge cases (pure unit / source-grep):
    (a) Idle detection without ready_marker — Aider's
        capabilities.ready_marker is None → falls back to VTE-silent
        debounce, identical to Copilot. Pin source contract.
    (b) Task claim atomic per tab — _claim_next_task is provider-
        agnostic SQL. Test directly without GTK.
    (c) Task done detection: BT relies on the AI calling `tasks done`
        via its Bash tool (not provider-specific output parsing).
        Pin that there's no per-provider 'task_done detected' branch.

Manual VM smoke: real qwen-coder session walking through 3 tasks via
auto-trigger is documented in tests/manual/README.md (referenced via
tools/test_aider_real_model.sh from #89). Headless tests below cover
every component up to the actual model dispatch.
"""
from __future__ import annotations

import base64
import json
import os
import shutil
import signal
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
MOCK_SRC = REPO_ROOT / "tools" / "mock_ai_cli"


# ─── (a) Idle-detection contract pinning (no ready_marker dispatch) ───────


def test_aider_capability_ready_marker_is_none():
    """Pinpoint: Aider has ready_marker=None — same as Copilot. Both
    rely on the VTE-silent debounce path in _idle_check_tick rather
    than a provider-specific 'system.stop'-style marker (Claude's)."""
    from bterminal.providers import get_registry
    aider = get_registry().get("aider")
    assert aider.capabilities.ready_marker is None


def test_copilot_capability_ready_marker_is_none_for_parity_baseline():
    """Parity baseline: Copilot also has ready_marker=None. If this
    flips, the assumption that Aider 'falls back like Copilot' breaks."""
    from bterminal.providers import get_registry
    copilot = get_registry().get("copilot")
    assert copilot.capabilities.ready_marker is None


def test_claude_capability_ready_marker_is_set_for_contrast():
    """Contrast pin: Claude has 'system.stop' as its ready_marker.
    Documents that the 3 providers split into two groups: marker-
    aware (Claude) and pure-VTE-silence (Copilot/Aider)."""
    from bterminal.providers import get_registry
    claude = get_registry().get("claude")
    assert claude.capabilities.ready_marker == "system.stop"


def test_idle_check_tick_does_not_branch_on_ready_marker():
    """Source-grep: _idle_check_tick is the dispatcher for
    auto_trigger / rules_inject firing. Today it polls VTE silence
    only — no per-provider ready_marker handling.

    If a future change introduces ready_marker dispatch for Claude
    (e.g. 'fire immediately when system.stop appears in VTE'), Aider
    must still work via the silence fallback. Test fails loud if
    someone adds a ready_marker check WITHOUT also adding a None-
    fallback that Aider/Copilot can use."""
    src_path = REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py"
    src = src_path.read_text()
    body_start = src.find("def _idle_check_tick")
    assert body_start > 0
    body_end = src.find("\n    def ", body_start + 1)
    body = src[body_start:body_end]
    assert "ready_marker" not in body, (
        "_idle_check_tick gained ready_marker dispatch — verify Aider "
        "+ Copilot still hit the VTE-silence fallback path"
    )


def test_idle_constants_used_for_all_providers():
    """The 2s quiet + 60s hard cap apply to every provider — no
    per-provider override. Pin both constants so a 'tune this for
    Aider only' patch fails this test loudly."""
    from bterminal.ui.terminal_tab import TerminalTab
    assert TerminalTab._IDLE_QUIET_SEC == 2.0
    assert TerminalTab._IDLE_HARD_CAP_SEC == 60.0


# ─── (b) _claim_next_task atomic — provider-agnostic SQL ─────────────────


def _seed_minimal_ctx(tmp_path: Path, project: str, task_ids: list[str],
                       autorun: int = 1) -> Path:
    """Build a minimal CTX DB. Returns path to context.db."""
    db_path = tmp_path / "context.db"
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
        """)
        conn.execute(
            "INSERT OR REPLACE INTO task_config (project, autorun) "
            "VALUES (?, ?)", (project, autorun),
        )
        for tid in task_ids:
            conn.execute(
                "INSERT INTO tasks (project, task_id, description, status) "
                "VALUES (?, ?, ?, 'open')",
                (project, tid, f"E2E task {tid}"),
            )
        conn.commit()
    finally:
        conn.close()
    return db_path


def test_claim_next_task_returns_first_open_task(tmp_path):
    """One open task → first call claims it, returns dict shape with
    task_id + description. Provider-agnostic — no 'aider' branch."""
    db_path = _seed_minimal_ctx(tmp_path, "myproj", ["t-aider-1"])
    from bterminal.ui.terminal_tab import TerminalTab

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    task = TerminalTab._claim_next_task(conn, "myproj", "session-aider-1")
    conn.close()

    assert task is not None
    assert task["task_id"] == "t-aider-1"
    assert "E2E task" in task["description"]


def test_claim_next_task_atomic_no_double_fire_for_same_session(tmp_path):
    """Same session calling _claim_next_task twice gets the SAME task
    back — atomic per-session claim. Without this, an aider tab
    rapidly force_idle'ing would fire [AUTO-TRIGGER] for the same
    task twice."""
    db_path = _seed_minimal_ctx(tmp_path, "myproj", ["t-1", "t-2"])
    from bterminal.ui.terminal_tab import TerminalTab

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    first = TerminalTab._claim_next_task(conn, "myproj", "s-aider")
    second = TerminalTab._claim_next_task(conn, "myproj", "s-aider")
    conn.close()

    # Same session sees its already-claimed task on second call
    assert first["task_id"] == second["task_id"]


def test_claim_next_task_different_sessions_get_different_tasks(tmp_path):
    """Two parallel aider sessions on the same project → each claims
    a DIFFERENT task. The atomic SQL is the safety mechanism here."""
    db_path = _seed_minimal_ctx(tmp_path, "myproj", ["t-1", "t-2"])
    from bterminal.ui.terminal_tab import TerminalTab

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    a = TerminalTab._claim_next_task(conn, "myproj", "s-aider-a")
    b = TerminalTab._claim_next_task(conn, "myproj", "s-aider-b")
    conn.close()

    assert a["task_id"] != b["task_id"]


def test_claim_next_task_returns_none_when_all_claimed(tmp_path):
    """Single task already claimed by other session → next caller
    gets None. Aider tab observing this skips firing auto_trigger
    (the parent _on_task_idle_timeout returns False)."""
    db_path = _seed_minimal_ctx(tmp_path, "myproj", ["t-1"])
    from bterminal.ui.terminal_tab import TerminalTab

    conn = sqlite3.connect(str(db_path))
    conn.row_factory = sqlite3.Row
    TerminalTab._claim_next_task(conn, "myproj", "s-other")
    after = TerminalTab._claim_next_task(conn, "myproj", "s-aider")
    conn.close()
    assert after is None


# ─── (c) Task done detection: BT has no per-provider parser ──────────────


def test_terminal_tab_has_no_provider_specific_task_done_detector():
    """Plan punkt (c): BT doesn't grep aider's stdout looking for
    'task done' style replies. The completion mechanism is uniform
    across providers — the AI calls `tasks done <project> <id>` via
    its Bash tool, which directly mutates context.db.

    Pin: terminal_tab.py source has no `if provider == 'aider'`
    branches on output parsing, no `re.match('task done')` against
    aider-specific text. Catches accidental specialization."""
    src_path = REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py"
    src = src_path.read_text()
    # Disallowed shapes — branching on output parsing
    bad_patterns = [
        "task done", "task_done_pattern", "extract_task_done",
        'provider == "aider"', "provider == 'aider'",
        'provider_name == "aider"', "provider_name == 'aider'",
    ]
    for pat in bad_patterns:
        assert pat not in src.lower() if pat.islower() else pat not in src, (
            f"terminal_tab.py gained provider-specific output parsing: "
            f"matched {pat!r} — task done detection MUST stay uniform"
        )


def test_tasks_done_via_subprocess_path_is_provider_agnostic():
    """The `tasks` CLI tool that the AI invokes is provider-agnostic.
    Tests in tests/test_tab_label.py / test_dual_provider_workflow.py
    already exercise the SQL path. Here we only pin that nothing in
    bterminal/ does provider-aware task completion logic."""
    bterminal_root = REPO_ROOT / "bterminal"
    matches = []
    for py in bterminal_root.rglob("*.py"):
        text = py.read_text()
        for pat in ("aider_task_done", "AiderTaskComplete",
                     "_aider_done_marker"):
            if pat in text:
                matches.append((py.name, pat))
    assert not matches, (
        f"per-provider task-done logic appeared: {matches}"
    )


# ─── Auto-trigger message format parity ──────────────────────────────────


def test_auto_trigger_message_template_provider_agnostic():
    """The `[AUTO-TRIGGER] Your assigned task: ...` message body is
    composed in _on_task_idle_timeout from task_id + description +
    project — no provider name interpolated. Same string format for
    Claude/Copilot/Aider tabs.

    Asserts on the f-string composition lines (post-`message = (`)
    rather than the whole function body — docstrings legitimately
    reference Claude/Copilot for historical context (T3.6 baseline
    notes)."""
    src_path = REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py"
    src = src_path.read_text()
    fn_start = src.find("def _on_task_idle_timeout")
    assert fn_start > 0
    fn_end = src.find("\n    def ", fn_start + 1)
    fn_body = src[fn_start:fn_end]
    assert "[AUTO-TRIGGER]" in fn_body

    # Extract the f-string passed to feed_child — that's the actual
    # wire format. Anything between `message = (` and the closing `)`
    # is what reaches the AI CLI.
    msg_start = fn_body.find("message = (")
    assert msg_start > 0, "message composition block missing"
    msg_end = fn_body.find(")", msg_start)
    msg_block = fn_body[msg_start:msg_end]

    for taboo in ("aider", "claude", "copilot",
                   "Aider", "Claude", "Copilot"):
        assert taboo not in msg_block, (
            f"auto_trigger message format references {taboo!r} — "
            f"per-provider divergence in the wire format"
        )


# ─── E2E fixture: aider tab + autorun + task → force_idle → auto_trigger ──


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


def _seed_full_ctx_db(home: str, project_name: str, project_dir: str,
                        task_ids: list[str], autorun: int = 1) -> None:
    """Full CTX schema (mirrors test_dual_provider_workflow's helper).
    Includes sessions/contexts/shared/summaries/rules* so
    CtxManagerPanel.refresh() doesn't blow up at startup."""
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
            "INSERT OR REPLACE INTO sessions (name, description, work_dir) "
            "VALUES (?, ?, ?)",
            (project_name, "Aider auto_trigger test", project_dir),
        )
        conn.execute(
            "INSERT OR REPLACE INTO task_config (project, autorun) "
            "VALUES (?, ?)", (project_name, autorun),
        )
        for tid in task_ids:
            conn.execute(
                "INSERT INTO tasks (project, task_id, description, status) "
                "VALUES (?, ?, ?, 'open')",
                (project_name, tid, f"E2E task {tid}"),
            )
        conn.commit()
    finally:
        conn.close()


@pytest.fixture
def bterminal_with_aider_and_tasks():
    """BTerminal subprocess with mock aider binary + 1 AI session +
    CTX DB seeded with 2 unclaimed tasks + autorun=1.

    Mirrors bterminal_dual_with_tasks from test_dual_provider_workflow
    but for Aider-only configurations."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-aider-autotrigger-")
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
            "id": "aider-1",
            "name": "AiderSession",
            "provider": "aider",
            "project_dir": str(project_dir),
            "color": "#fab387",
            "provider_options": {},
        },
    ]))

    _seed_full_ctx_db(
        home, project_name, str(project_dir),
        task_ids=["t-aider-A", "t-aider-B"],
    )

    import socket
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
        ["xvfb-run", "-a", sys.executable, "-m", "bterminal", "--debug-rest"],
        cwd=str(REPO_ROOT), env=env,
        stdout=stdout_handle, stderr=stderr_handle,
        start_new_session=True,
    )

    base = f"http://127.0.0.1:{port}"
    if not _wait_for_health(base, time.monotonic() + 30):
        proc.terminate()
        proc.wait(timeout=5)
        stderr_handle.close()
        stdout_handle.close()
        try:
            err = Path(stderr_path).read_text(errors="replace")[-2000:]
        except OSError:
            err = "(unreadable)"
        try:
            out = Path(stdout_path).read_text(errors="replace")[-2000:]
        except OSError:
            out = "(unreadable)"
        pytest.fail(
            f"BTerminal didn't come up within 30s\n"
            f"--- stdout tail ---\n{out}\n"
            f"--- stderr tail ---\n{err}"
        )

    token = (cfg_dir / "debug_token").read_text().strip()
    try:
        yield {
            "base": base, "token": token, "home": home,
            "stderr_path": str(stderr_path),
            "project_name": project_name,
            "project_dir": str(project_dir),
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
        stderr_handle.close()
        stdout_handle.close()


def _enable_autorun(home, project_name):
    """Re-enable autorun after BT startup. TaskPanel.__init__ resets
    autorun=0 for all projects on startup (deliberate UX), so tests
    re-arm it via direct SQL after the panel finishes init."""
    db_path = Path(home) / ".claude-context" / "context.db"
    conn = sqlite3.connect(str(db_path))
    try:
        conn.execute(
            "INSERT INTO task_config (project, autorun) VALUES (?, 1) "
            "ON CONFLICT(project) DO UPDATE SET autorun = 1",
            (project_name,),
        )
        conn.commit()
    finally:
        conn.close()


def test_aider_force_idle_fires_auto_trigger_with_task_id(
        bterminal_with_aider_and_tasks):
    """E2E headline: open Aider tab → enable autorun → force_idle →
    auto_trigger feed event captured with the claimed task_id in
    its body. This is the core #97 promise."""
    state = bterminal_with_aider_and_tasks
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    assert status == 200, body
    aider_idx = body["idx"]

    time.sleep(0.5)  # let intro_prompt records settle
    _enable_autorun(state["home"], state["project_name"])
    pivot_ts = time.time()

    status, body = _http(
        base, token, "POST", f"/api/tabs/{aider_idx}/force_idle", {})
    assert status == 200, body
    time.sleep(0.5)

    status, feed = _http(
        base, token, "GET",
        f"/api/debug/feed_log?since={pivot_ts}", None,
    )
    assert status == 200, feed
    auto_events = [e for e in feed["events"]
                   if e.get("label") == "auto_trigger"]
    assert auto_events, (
        "No auto_trigger event captured for Aider tab — "
        f"all events: {[e.get('label') for e in feed['events']]}"
    )

    bodies = [base64.b64decode(e["bytes_b64"]).decode("utf-8", errors="replace")
              for e in auto_events]
    # One of the seeded tasks (t-aider-A or t-aider-B) appears in the
    # auto-trigger message body
    matched = any("t-aider-" in b for b in bodies)
    assert matched, (
        f"auto_trigger body missing seeded task_id: {bodies}"
    )


def test_aider_repeated_force_idle_does_not_double_fire_same_task(
        bterminal_with_aider_and_tasks):
    """Edge case (b) from auto-trigger plan: hitting force_idle
    repeatedly on the same Aider tab MUST NOT fire [AUTO-TRIGGER] for
    a different task each time — _claim_next_task's session-aware
    branch returns the same already-claimed task."""
    state = bterminal_with_aider_and_tasks
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    aider_idx = body["idx"]
    time.sleep(0.5)
    _enable_autorun(state["home"], state["project_name"])

    pivot_ts = time.time()
    # Three rapid force_idle calls
    for _ in range(3):
        _http(base, token, "POST",
               f"/api/tabs/{aider_idx}/force_idle", {})
        time.sleep(0.1)
    time.sleep(0.5)

    status, feed = _http(
        base, token, "GET",
        f"/api/debug/feed_log?since={pivot_ts}", None,
    )
    auto_events = [e for e in feed["events"]
                   if e.get("label") == "auto_trigger"]
    bodies = [base64.b64decode(e["bytes_b64"]).decode("utf-8", errors="replace")
              for e in auto_events]
    # Extract task_ids — every event must reference the SAME task,
    # not switch between t-aider-A and t-aider-B.
    task_ids = set()
    for b in bodies:
        if "t-aider-A" in b:
            task_ids.add("t-aider-A")
        elif "t-aider-B" in b:
            task_ids.add("t-aider-B")
    assert len(task_ids) <= 1, (
        f"Aider tab claimed multiple distinct tasks across rapid "
        f"force_idle: {task_ids} | bodies: {bodies}"
    )
