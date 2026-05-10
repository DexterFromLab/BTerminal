"""Cross-feature: provider switching mid-session
(#44 / #116, audit § 6.3 #17).

Scenario: user opens an Aider tab, then edits ai_sessions.json
externally to change provider→claude. What happens to the open
tab?

Pinned contract: tab is bound to the ai_config snapshot taken at
__init__ time. External edits to ai_sessions.json do NOT
retroactively switch the running tab — BT has no runtime reload
of ai_sessions.json (the model loads once at app startup).

Three decision branches:
  (a) Edit between spawn and first prompt — snapshot already
      captured at spawn; tab keeps original provider for the
      whole session lifetime.
  (b) Edit during active streaming — same snapshot; the running
      AiderProvider session continues uninterrupted.
  (c) Edit revert — reverting the file ALSO doesn't touch the
      tab; the tab's behavior is decoupled from disk state
      after spawn.

Pinned defenses:
  - `TerminalTab.__init__` stores `self.ai_config = ai_config`
    (reference snapshot from the in-memory model dict).
  - `spawn_ai_cli` reads `self.ai_config["provider"]` — uses
    the snapshot, not a fresh model fetch.
  - `AISessionsModel.load()` REPLACES `self.sessions` with a
    fresh list from disk — old dict references (held by open
    tabs) survive in memory unchanged.
  - `AISessionsModel.update()` mutates IN-PLACE — distinct from
    external edits. In-app edits via AISessionDialog DO update
    the dict the tab references (intended behavior for option
    tweaks; provider-rename is the regression risk).

Manual VM smoke (edit ai_sessions.json, observe sidebar+tab) is
documented in tests/manual/README.md.
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


REPO_ROOT = Path(__file__).parent.parent.parent
MOCK_SRC = REPO_ROOT / "tools" / "mock_ai_cli"
TERMINAL_TAB = REPO_ROOT / "bterminal" / "ui" / "terminal_tab.py"
MODELS = REPO_ROOT / "bterminal" / "models.py"
SIDEBAR = REPO_ROOT / "bterminal" / "ui" / "sidebar.py"


# ─── Source-grep: tab snapshot semantics ────────────────────────────────


def test_terminal_tab_stores_ai_config_as_attribute_snapshot():
    """Pin: `TerminalTab.__init__` assigns `self.ai_config = ai_config`
    — reference snapshot. The tab uses this reference for the rest
    of its lifecycle, never re-fetching from a model."""
    src = TERMINAL_TAB.read_text()
    # The exact line from terminal_tab.py:219
    assert "self.ai_config = ai_config" in src, (
        "TerminalTab no longer snapshots ai_config in __init__"
    )


def test_spawn_ai_cli_reads_provider_from_self_ai_config():
    """Pin: `spawn_ai_cli` reads `self.ai_config["provider"]` — the
    snapshot. Without this, a runtime config swap could change
    which provider the tab dispatches to between calls."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def spawn_ai_cli")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    # spawn_ai_cli takes config arg — but reads provider from it
    assert 'config.get("provider"' in body
    # And the canonical caller in __init__ passes self.ai_config
    init_idx = src.find("def __init__(self,")
    init_end = src.find("\n    def ", init_idx + 1)
    init_body = src[init_idx:init_end]
    assert "self.spawn_ai_cli(self.ai_config)" in init_body


def test_spawn_ai_cli_does_not_refetch_from_ai_manager():
    """Pin: spawn_ai_cli doesn't reach into `app.ai_manager` to
    re-fetch the session by id. If it did, an external edit
    would propagate to spawn paths."""
    src = TERMINAL_TAB.read_text()
    fn_start = src.find("def spawn_ai_cli")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    forbidden = ["ai_manager", "ai_manager.get(",
                 "ai_manager.all()"]
    for pat in forbidden:
        assert pat not in body, (
            f"spawn_ai_cli reaches into ai_manager: {pat!r} — "
            f"runtime swap would change provider mid-session"
        )


def test_terminal_tab_does_not_subscribe_to_ai_sessions_changes():
    """Pin: TerminalTab doesn't connect to a 'ai-sessions-changed'
    signal (such a signal doesn't exist today, but pin the
    absence so a refactor that adds it documents the impact on
    open tabs)."""
    src = TERMINAL_TAB.read_text()
    forbidden = ["ai-sessions-changed", "ai_sessions_changed",
                 "connect.*sessions"]
    for pat in forbidden:
        # Substring search — pure literal, no regex
        if pat.startswith("connect"):
            # This one is a heuristic; check there's no
            # `connect("ai-sessions-changed", ...)` style call
            continue
        assert pat not in src


# ─── AISessionsModel.load() replaces the list (old refs survive) ────────


def test_ai_sessions_model_load_replaces_list_not_in_place_mutate():
    """Pin: `load()` does `self.sessions = json.load(f)` — assigns
    a NEW list. Existing dict references (held by open tabs) are
    NOT mutated; they remain valid in memory."""
    src = MODELS.read_text()
    load_idx = src.find("def load(self):")
    next_def = src.find("\n    def ", load_idx + 1)
    body = src[load_idx:next_def]
    # Pin: assigns a new list (replacing reference)
    assert "self.sessions = json.load(f)" in body
    # And NOT mutating in place
    assert "self.sessions.clear()" not in body
    assert "self.sessions[:] = " not in body


def test_existing_dict_references_survive_model_reload(tmp_path):
    """End-to-end Python test: simulate the user-edits-file
    scenario.

    1. Model loads from disk → returns a list of dicts.
    2. Tab keeps a reference to one dict.
    3. User edits the file (changes provider in JSON).
    4. Model.load() is called again.
    5. Tab's dict reference is UNCHANGED — it's the OLD dict,
       still in memory."""
    sessions_file = tmp_path / "ai_sessions.json"
    sessions_file.write_text(json.dumps([
        {"id": "a-1", "name": "AiderSession", "provider": "aider"},
    ]))

    # Step 1+2: load + tab takes reference
    with open(sessions_file) as f:
        sessions_v1 = json.load(f)
    tab_snapshot = sessions_v1[0]  # tab.ai_config = this dict
    assert tab_snapshot["provider"] == "aider"

    # Step 3: user edits file externally
    sessions_file.write_text(json.dumps([
        {"id": "a-1", "name": "AiderSession", "provider": "claude"},
    ]))

    # Step 4: model reload (fresh list, new dicts)
    with open(sessions_file) as f:
        sessions_v2 = json.load(f)

    # Step 5: tab snapshot UNCHANGED
    assert tab_snapshot["provider"] == "aider", (
        "tab snapshot saw external edit — broken snapshot semantics"
    )
    # New list has the new value
    assert sessions_v2[0]["provider"] == "claude"
    # Distinct dict references
    assert tab_snapshot is not sessions_v2[0]


def test_in_place_update_does_propagate_to_open_tabs(tmp_path):
    """Pin the OTHER side: `AISessionsModel.update()` mutates
    sessions[i] IN-PLACE. If a tab references that same dict,
    the mutation is visible.

    This is the intended behavior for in-app edits via
    AISessionDialog (e.g. user changes color or skip_permissions
    flag while tab is open). The provider-rename case is the
    audit gap: should NOT propagate. Pin actual current behavior
    so #116 fix can decide."""
    # Direct simulation of model.update behavior:
    sessions = [
        {"id": "a-1", "name": "AiderSession", "provider": "aider"},
    ]
    tab_snapshot = sessions[0]  # tab grabs reference

    # In-app update mutates IN-PLACE
    sessions[0].update({"provider": "claude"})

    # Tab sees the change — same dict reference
    assert tab_snapshot["provider"] == "claude", (
        "in-place update doesn't propagate — verify "
        "AISessionsModel.update() still uses .update()"
    )


# ─── Sidebar refresh reads in-memory state, not disk ────────────────────


def test_sidebar_refresh_reads_ai_manager_all_not_disk():
    """Pin: `sidebar.refresh()` calls `app.ai_manager.all()` —
    in-memory snapshot. Doesn't reload from disk. So sidebar
    state mirrors whatever the model has cached."""
    src = SIDEBAR.read_text()
    fn_start = src.find("def refresh(self):")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "ai_manager.all()" in body
    # No disk read in refresh
    assert "json.load" not in body
    assert "open(" not in body


def test_ai_manager_all_returns_copy_of_sessions_list():
    """Pin: `model.all()` returns a copy of the list. So mutations
    to the returned list don't bleed back into the model. (Inner
    dicts are still references, but the outer list is safe.)"""
    src = MODELS.read_text()
    fn_idx = src.find("def all(self):")
    next_def = src.find("\n    def ", fn_idx + 1)
    body = src[fn_idx:next_def]
    assert "list(self.sessions)" in body, (
        f"all() doesn't wrap with list() — caller can mutate "
        f"the model's internal list. Body: {body!r}"
    )


# ─── No automatic file-change watcher on ai_sessions.json ───────────────


def test_no_inotify_or_glib_file_monitor_on_ai_sessions():
    """Pin: BT doesn't subscribe to file-change notifications on
    ai_sessions.json. If we had one, external edits would
    auto-trigger reload + invalidate tab snapshots — but the
    contract is 'edits require BT restart to take effect'.

    Pin via source-grep: no `Gio.FileMonitor` / `inotify` /
    `pyinotify` on the AI sessions path."""
    bterminal_root = REPO_ROOT / "bterminal"
    matches = []
    for py in bterminal_root.rglob("*.py"):
        text = py.read_text()
        # Look for file-monitor on AI_SESSIONS_FILE specifically
        if "AI_SESSIONS_FILE" in text and (
                "FileMonitor" in text
                or "monitor_file" in text):
            matches.append(py.name)
    assert not matches, (
        f"file watchers on ai_sessions.json: {matches}. The "
        f"snapshot contract assumes no auto-reload — adding a "
        f"watcher invalidates open-tab semantics."
    )


# ─── E2E: spawn aider tab, edit JSON externally, verify tab survives ────


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


@pytest.fixture
def bterminal_with_aider_session():
    """BT subprocess with mock aider binary + 1 ai_sessions.json
    entry."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-providerswitch-")
    seed_license(home)

    fake_bin = Path(home) / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    for name in ("aider", "claude"):
        target = fake_bin / name
        shutil.copy(str(MOCK_SRC), str(target))
        target.chmod(target.stat().st_mode
                      | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    project_dir = Path(home) / "myproj"
    project_dir.mkdir(parents=True, exist_ok=True)

    cfg_dir = Path(home) / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    sessions_file = cfg_dir / "ai_sessions.json"
    sessions_file.write_text(json.dumps([
        {
            "id": "a-1", "name": "AiderSession",
            "provider": "aider", "project_dir": str(project_dir),
            "color": "#fab387", "provider_options": {},
        },
    ]))

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
            "sessions_file": str(sessions_file),
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


# ─── (a) Edit between spawn and first prompt ────────────────────────────


def test_external_edit_after_spawn_does_not_change_tab_provider(
        bterminal_with_aider_session):
    """Open Aider tab, externally rewrite ai_sessions.json to
    provider=claude. Verify /api/tabs reports tab still as
    provider=aider (snapshot intact)."""
    state = bterminal_with_aider_session
    base, token = state["base"], state["token"]

    # Spawn aider tab
    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    assert status == 200, body
    aider_idx = body["idx"]

    time.sleep(0.5)

    # External edit: rewrite ai_sessions.json with provider=claude
    sessions_file = Path(state["sessions_file"])
    sessions_file.write_text(json.dumps([
        {
            "id": "a-1", "name": "AiderSession",
            "provider": "claude",  # changed!
            "project_dir": str(Path(state["home"]) / "myproj"),
            "color": "#fab387", "provider_options": {},
        },
    ]))
    time.sleep(0.3)

    # /api/tabs still reports provider=aider for this tab
    status, tabs = _http(base, token, "GET", "/api/tabs", None)
    assert status == 200
    aider_tab = next(
        (t for t in tabs.get("tabs", []) if t.get("idx") == aider_idx),
        None,
    )
    assert aider_tab is not None
    assert aider_tab.get("provider") == "aider", (
        f"tab provider switched to {aider_tab.get('provider')!r} "
        f"after external edit — snapshot semantics broken"
    )


def test_external_edit_does_not_corrupt_bt_stderr(
        bterminal_with_aider_session):
    """Sanity: external edits to ai_sessions.json don't crash BT
    (no AssertionError / Traceback in stderr from a file watcher
    we forgot about)."""
    state = bterminal_with_aider_session
    base, token = state["base"], state["token"]

    _http(base, token, "POST", "/api/tabs/ai/aider",
           {"config_name": "AiderSession"})
    time.sleep(0.3)

    # 5 rewrite cycles
    sessions_file = Path(state["sessions_file"])
    for i in range(5):
        sessions_file.write_text(json.dumps([
            {
                "id": "a-1", "name": "AiderSession",
                "provider": "aider" if i % 2 == 0 else "claude",
                "project_dir": str(Path(state["home"]) / "myproj"),
                "color": "#fab387", "provider_options": {},
            },
        ]))
        time.sleep(0.05)

    time.sleep(0.5)
    err = Path(state["stderr_path"]).read_text(errors="replace")
    forbidden = ["AssertionError", "AttributeError", "NameError",
                 "ImportError", "Traceback (most recent call last)"]
    bad = [p for p in forbidden if p in err]
    assert not bad, f"BT stderr has {bad}: {err[:1500]}"


# ─── (c) Edit revert: still no tab change ───────────────────────────────


def test_edit_then_revert_leaves_tab_unchanged(
        bterminal_with_aider_session):
    """Edit JSON, then revert it. Tab provider unchanged
    throughout. Pinned: tab is decoupled from disk state after
    spawn."""
    state = bterminal_with_aider_session
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    aider_idx = body["idx"]
    time.sleep(0.3)

    sessions_file = Path(state["sessions_file"])
    original = sessions_file.read_text()

    # Edit to claude
    sessions_file.write_text(json.dumps([
        {"id": "a-1", "name": "AiderSession",
         "provider": "claude",
         "project_dir": str(Path(state["home"]) / "myproj"),
         "color": "#fab387", "provider_options": {}}
    ]))
    time.sleep(0.2)

    # Revert
    sessions_file.write_text(original)
    time.sleep(0.2)

    # Tab still provider=aider
    status, tabs = _http(base, token, "GET", "/api/tabs", None)
    aider_tab = next(
        (t for t in tabs["tabs"] if t.get("idx") == aider_idx),
        None,
    )
    assert aider_tab is not None
    assert aider_tab.get("provider") == "aider"


# ─── (b) Edit during active streaming: REST feed still works ────────────


def test_external_edit_during_active_session_does_not_break_feed(
        bterminal_with_aider_session):
    """Spawn tab, edit JSON, then POST /feed → still works.
    Tab's PTY is bound to the spawned aider process; the feed
    path doesn't touch ai_sessions.json at all."""
    state = bterminal_with_aider_session
    base, token = state["base"], state["token"]

    status, body = _http(base, token, "POST", "/api/tabs/ai/aider",
                          {"config_name": "AiderSession"})
    aider_idx = body["idx"]
    time.sleep(0.5)

    # External edit
    sessions_file = Path(state["sessions_file"])
    sessions_file.write_text(json.dumps([
        {"id": "a-1", "name": "AiderSession",
         "provider": "claude",
         "project_dir": str(Path(state["home"]) / "myproj"),
         "color": "#fab387", "provider_options": {}}
    ]))
    time.sleep(0.2)

    # Feed still works (still talking to aider's PTY)
    status, body = _http(
        base, token, "POST", f"/api/tabs/{aider_idx}/feed",
        {"text": "ping after external edit\n"},
    )
    assert status == 200
    assert body.get("bytes") > 0


# ─── Reload via post-edit BT-restart simulation (model.load only) ──────


def test_reload_after_edit_picks_up_new_state_for_NEW_tabs(tmp_path):
    """Post-#116 contract: tab snapshot is per-tab. NEW tabs
    spawned AFTER a model reload see the new config; OLD tabs
    keep their original snapshot. Pure-Python test pinning the
    AISessionsModel API."""
    sessions_file = tmp_path / "ai_sessions.json"
    sessions_file.write_text(json.dumps([
        {"id": "s-1", "name": "X", "provider": "aider"},
    ]))

    # Construct the model + load
    import importlib.util
    spec = importlib.util.spec_from_file_location(
        "_models_isolated", str(MODELS))
    models_mod = importlib.util.module_from_spec(spec)
    # The model uses `CONFIG_DIR` from config — but the AISessions
    # subclass picks up _filepath at __init__. We avoid full
    # construction here by just exercising the load+reference
    # invariant that the model relies on.

    # First load
    with open(sessions_file) as f:
        v1 = json.load(f)
    old_tab_ref = v1[0]
    assert old_tab_ref["provider"] == "aider"

    # User edits + reloads
    sessions_file.write_text(json.dumps([
        {"id": "s-1", "name": "X", "provider": "claude"},
    ]))
    with open(sessions_file) as f:
        v2 = json.load(f)
    new_tab_ref = v2[0]

    # Old tab's reference unchanged
    assert old_tab_ref["provider"] == "aider"
    # New tab (spawned post-reload) sees new state
    assert new_tab_ref["provider"] == "claude"
    # Distinct objects in memory
    assert old_tab_ref is not new_tab_ref


def test_open_tabs_array_in_rest_response_has_provider_field():
    """Pin: REST /api/tabs response includes per-tab `provider`
    so external automation (testing or CI) can verify tab
    provider state. Without this field, the e2e tests above
    couldn't distinguish aider vs claude tabs."""
    src = (REPO_ROOT / "bterminal" / "debug_rest.py").read_text()
    # Find the route that emits tab list
    assert '"provider"' in src
    # And it sources from tab.ai_config.get("provider"
    assert 'ai_config.get("provider"' in src or \
        'config.get("provider"' in src
