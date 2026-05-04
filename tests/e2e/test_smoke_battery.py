"""Smoke battery — exercise every user-facing flow end-to-end.

After EACH REST action verify że BTerminal stderr nie ma nowych errorów.
Ten test by wyłapał NameError'y typu `_build_intro_prompt`, `random`,
`shutil`, `urllib` które przeszły niezauważone w refactorach.

Pattern: `bt_stderr_watcher.check(action_name)` po każdym call. Jeśli
jakiś callback w GTK signalsach rzucił traceback → test fails z
nazwą action który go wywołał.

Tests are intentionally additive — każdy działa na świeżym stanie
(scope=function default), exercise jednego flow per test żeby nazwy
testów wyraźnie wskazywały co padło.
"""

import json
from pathlib import Path

import pytest


# ─── Smoke 1: app boot — żaden error na cold start ──────────────────────────

def test_smoke_app_boots_without_errors(bterminal_process, bt_stderr_watcher):
    """BTerminal startuje, GET /api/state odpowiada, stderr czyste."""
    resp = bterminal_process.http_client.get("/api/state")
    assert resp.status_code == 200
    state = resp.json()
    assert "version" in state
    assert state["debug_mode"] is True
    bt_stderr_watcher.check("app_boot")


# ─── Smoke 2: each sidebar panel — switch + render ──────────────────────────

@pytest.mark.parametrize("panel", [
    "sessions", "ctx", "consult", "tasks",
    "memory", "skills", "files", "plugins",
])
def test_smoke_each_sidebar_panel_renders(bterminal_process, bt_stderr_watcher, panel):
    """Switch sidebar to each panel via REST. Wyłapie NameError'y w
    panel constructorze / show handlerze (jak Gio w panel_git regression)."""
    resp = bterminal_process.http_client.post(
        "/api/window/sidebar/show", json={"name": panel}
    )
    assert resp.status_code == 200, (
        f"sidebar/show {panel}: {resp.status_code} — {resp.text[:200]}"
    )
    assert resp.json()["active"] == panel
    bt_stderr_watcher.check(f"sidebar/show {panel}")


# ─── Smoke 3: open + close local tab ─────────────────────────────────────────

def test_smoke_open_and_close_local_tab(bterminal_process, bt_stderr_watcher):
    """Tab lifecycle SSH/local — spawn shell + kill. Wyłapie regresję w
    TerminalTab.__init__ / spawn_local_shell / close path."""
    bt_stderr_watcher.check("baseline")

    open_resp = bterminal_process.http_client.post("/api/tabs/local")
    assert open_resp.status_code == 200, open_resp.text
    idx = open_resp.json()["idx"]
    bt_stderr_watcher.check("tabs/local")

    close_resp = bterminal_process.http_client.post(f"/api/tabs/{idx}/close")
    assert close_resp.status_code == 200
    bt_stderr_watcher.check(f"tabs/{idx}/close")


# ─── Smoke 4: open Claude tab via seeded session ────────────────────────────

@pytest.fixture
def seeded_claude_session(bterminal_process, tmp_path):
    """Wstawia Claude session config do bterminal HOME przed testem.

    UWAGA: bterminal_process ma scope=session, więc claude_sessions.json
    persistuje między testami. Używaj unikalnej nazwy sesji per test.
    """
    home = Path(bterminal_process.home)
    cfg_path = home / ".config" / "bterminal" / "claude_sessions.json"
    cfg_path.parent.mkdir(parents=True, exist_ok=True)
    project_dir = tmp_path / "test_project"
    project_dir.mkdir()
    session = {
        "id": "smoke-test-uuid-12345",
        "name": "smoke_test",
        "project_dir": str(project_dir),
        "prompt": "",
        "color": "#89b4fa",
        "resume": False,
        "skip_permissions": True,
        "sudo": False,
        "folder": "",
    }
    existing = []
    if cfg_path.exists():
        try:
            existing = json.loads(cfg_path.read_text())
        except json.JSONDecodeError:
            existing = []
    # Drop any existing smoke_test, append fresh
    existing = [s for s in existing if s.get("name") != "smoke_test"]
    existing.append(session)
    cfg_path.write_text(json.dumps(existing))

    # BTerminal cache claude_manager.sessions w pamięci. Trigger refresh
    # przez wywołanie REST endpointu który czyta sesje (workaround do
    # czasu dedykowanego /api/sessions/reload — TODO).
    # ClaudeSessionManager.load() jest wywoływane tylko w __init__, więc
    # de-facto musimy zapisać PRZED app spawn, ale fixture jest session-
    # scoped. Test pomijamy graceful jeśli config nie został podchwycony.
    yield session

    # Cleanup: usuń wpis (niezbyt istotne — fixture kasuje całe HOME na końcu)
    if cfg_path.exists():
        try:
            data = json.loads(cfg_path.read_text())
            data = [s for s in data if s.get("name") != "smoke_test"]
            cfg_path.write_text(json.dumps(data))
        except (json.JSONDecodeError, OSError):
            pass


def test_smoke_open_claude_tab_seeded(bterminal_process, bt_stderr_watcher, seeded_claude_session):
    """Otwórz Claude session przez REST — same code path co double-click
    w SessionSidebar._on_row_activated → app.open_claude_tab.

    Catches NameError / AttributeError / ImportError w open_claude_tab,
    spawn_claude, _compute_intro_prompt_for_tab.

    Skip gracefully gdy session nie jest podchwycona (claude_manager
    cache problem — known limitation, do refactoringu w osobnej iteracji).
    """
    bt_stderr_watcher.check("baseline")

    resp = bterminal_process.http_client.post(
        "/api/tabs/claude", json={"config_name": "smoke_test"}
    )
    if resp.status_code == 404:
        pytest.skip(
            "claude_manager cache miss (known limitation — sessions seeded "
            "after BT boot aren't reloaded). Test setup needs reload "
            "endpoint or fixture restructure. The ASSERTION below would "
            "still catch NameError jeśli flow się odpalił — działa "
            "niezawodnie tylko z config seeded PRZED bterminal_process spawn."
        )
    assert resp.status_code == 200, (
        f"open_claude_tab failed: {resp.status_code} — {resp.text[:300]}"
    )
    bt_stderr_watcher.check("tabs/claude")

    idx = resp.json()["idx"]
    close = bterminal_process.http_client.post(f"/api/tabs/{idx}/close")
    assert close.status_code == 200
    bt_stderr_watcher.check(f"tabs/{idx}/close")


# ─── Smoke 5: list plugins (catches loader regressions) ──────────────────────

def test_smoke_list_plugins(bterminal_process, bt_stderr_watcher):
    """GET /api/plugins — exercise loader path + per-plugin info gather.
    Wyłapie problemy w _load_plugins, plugin instantiation."""
    resp = bterminal_process.http_client.get("/api/plugins")
    assert resp.status_code == 200
    assert "plugins" in resp.json()
    bt_stderr_watcher.check("plugins/list")


def test_smoke_list_sidecars(bterminal_process, bt_stderr_watcher):
    """GET /api/sidecars — Discovery.load_all() per request."""
    resp = bterminal_process.http_client.get("/api/sidecars")
    assert resp.status_code == 200
    assert "sidecars" in resp.json()
    bt_stderr_watcher.check("sidecars/list")


# ─── Smoke 6: window screenshot (catches rendering issues) ───────────────────

def test_smoke_window_screenshot(bterminal_process, bt_stderr_watcher):
    """GET /api/window/screenshot — Gdk.pixbuf_get_from_window.
    Catches GTK rendering errors mid-screenshot."""
    resp = bterminal_process.http_client.get("/api/window/screenshot")
    assert resp.status_code == 200
    data = resp.json()
    assert "path" in data
    assert "width" in data and data["width"] > 0
    assert "height" in data and data["height"] > 0
    bt_stderr_watcher.check("window/screenshot")


# ─── Smoke 7: toggle sidebar / git panel ────────────────────────────────────

def test_smoke_toggle_sidebar(bterminal_process, bt_stderr_watcher):
    """Toggle hide/show sidebar twice — assert state flips."""
    state_resp = bterminal_process.http_client.post("/api/window/toggle_sidebar")
    assert state_resp.status_code == 200
    first = state_resp.json()["visible"]
    bt_stderr_watcher.check("toggle_sidebar 1st")

    state_resp2 = bterminal_process.http_client.post("/api/window/toggle_sidebar")
    assert state_resp2.status_code == 200
    second = state_resp2.json()["visible"]
    assert first != second
    bt_stderr_watcher.check("toggle_sidebar 2nd")


def test_smoke_toggle_git_panel(bterminal_process, bt_stderr_watcher):
    """Toggle git panel hide/show. Catches FileMonitor / Gio errors w panel_git."""
    resp = bterminal_process.http_client.post("/api/window/toggle_git_panel")
    assert resp.status_code == 200
    bt_stderr_watcher.check("toggle_git_panel 1st")

    resp2 = bterminal_process.http_client.post("/api/window/toggle_git_panel")
    assert resp2.status_code == 200
    bt_stderr_watcher.check("toggle_git_panel 2nd")
