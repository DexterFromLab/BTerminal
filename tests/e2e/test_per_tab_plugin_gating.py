"""Regression test: per-tab enabled_plugins propagacja do intro prompt.

Bug history (2026-05-04): app.open_claude_tab ustawiało tab.enabled_plugins
PO TerminalTab.__init__, który już wywołał spawn_claude → intro prompt
liczony z None default = wszystkie pluginy included. User'owy uncheck
checkbox'a ClaudeCodeDialog był ignorowany.

Fix: pass enabled_plugins przez TerminalTab constructor.

Test: open Claude tab z enabled_plugins=[] → assert plugin context
NIE JEST w intro prompt.

T4.6.2 (2026-05-07): replaces the prior pytestmark.skipif gate with
a fixture-injected stub claude binary (same pattern as
test_provider_switching.py / test_dual_provider_workflow.py). The
fixture copies tools/mock_ai_cli into a per-test fake_bin directory
and prepends it to PATH, so spawn_ai_cli always finds an executable
regardless of whether the host has Claude Code installed under /usr
or under the user's ~/.local/bin. The intro prompt is computed and
record_feed'ed by terminal_tab.py BEFORE the binary is exec'd, so
once the spawn itself succeeds the intro_prompt feed event is emitted
and the gating assertions can fire.
"""

import base64
import json
import os
import shutil
import signal
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request

import pytest

REPO_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
MOCK_SRC = os.path.join(REPO_ROOT, "tools", "mock_ai_cli")
CLAUDE_SCENARIO = os.path.join(REPO_ROOT, "tests", "scenarios", "claude_basic.json")


@pytest.fixture
def isolated_bterminal():
    """Spawn fresh BTerminal z izolowanym HOME + plugin który dodaje
    rozpoznawalny session context. Yields client dict + cleanup."""
    home = tempfile.mkdtemp(prefix="bug_b_test-")
    plugins_dir = os.path.join(home, ".config/bterminal/plugins")
    os.makedirs(plugins_dir, exist_ok=True)
    # Plugin emitujący marker w get_session_context
    with open(os.path.join(plugins_dir, "loud_plugin.py"), "w") as f:
        f.write(
            "import gi\n"
            "gi.require_version('Gtk','3.0')\n"
            "from gi.repository import Gtk\n"
            "class LoudPlugin:\n"
            "    name='loud_plugin'; title='Loud'; default_in_session=True\n"
            "    description=''; author=''; version='0.0.1'\n"
            "    def activate(self,app):return Gtk.Box()\n"
            "    def deactivate(self): pass\n"
            "    def get_keyboard_shortcuts(self): return []\n"
            "    def on_sidebar_shown(self): pass\n"
            "    def get_session_context(self): return 'PLUGIN_CTX_MARKER_xyz'\n"
            "def create_plugin(app): return LoudPlugin()\n"
        )

    test_proj = os.path.join(home, "test_proj")
    os.makedirs(test_proj, exist_ok=True)
    # T4.6.1: canonical R4.2 schema (provider + provider_options).
    # Pre-T4.6.1 fixture seeded legacy claude_sessions.json and relied
    # on the T1.7 migration to add `provider="claude"`; that worked for
    # the legacy /api/tabs/claude name-only endpoint but the new
    # /api/tabs/ai/claude needs the field present from the start.
    sess_path = os.path.join(home, ".config/bterminal/ai_sessions.json")
    with open(sess_path, "w") as f:
        json.dump([
            {"id": "a", "name": "with_default", "provider": "claude",
             "project_dir": test_proj, "prompt": "", "color": "#000",
             "folder": "",
             "provider_options": {"resume": False,
                                  "skip_permissions": True, "sudo": False}},
            {"id": "b", "name": "with_empty", "provider": "claude",
             "project_dir": test_proj, "prompt": "", "color": "#000",
             "folder": "", "enabled_plugins": [],
             "provider_options": {"resume": False,
                                  "skip_permissions": True, "sudo": False}},
        ], f)

    # Pre-accept license so the subprocess doesn't open a modal GTK
    # dialog (see tests/_subprocess_helpers.py).
    from tests._subprocess_helpers import seed_license
    seed_license(home)

    # T4.6.2: stub claude binary in an isolated bin dir so spawn_ai_cli
    # finds an executable even when the host has no system-wide claude
    # install (e.g. VMs where Claude lives only under the dev user's
    # ~/.local/bin and isn't visible to a subprocess with HOME=tmpdir).
    fake_bin = os.path.join(home, "fake-bin")
    os.makedirs(fake_bin, exist_ok=True)
    fake_claude = os.path.join(fake_bin, "claude")
    shutil.copy(MOCK_SRC, fake_claude)
    st = os.stat(fake_claude)
    os.chmod(fake_claude,
             st.st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    # Bind to ephemeral port (unique per test invocation) — pytest may
    # run testów równolegle albo tuż po sobie, fixed port collides
    import socket
    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = str(s.getsockname()[1])
    s.close()
    env = {
        **os.environ,
        "HOME": home,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "BTERMINAL_DEBUG_REST_PORT": port,
        "MOCK_AI_CLI_SCENARIO": CLAUDE_SCENARIO,
    }
    proc = subprocess.Popen(
        ["xvfb-run", "-a", sys.executable, "-m", "bterminal", "--debug-rest"],
        cwd=REPO_ROOT, env=env,
        stdout=subprocess.DEVNULL, stderr=subprocess.PIPE,
        start_new_session=True,
    )
    base = f"http://127.0.0.1:{port}"
    deadline = time.monotonic() + 15
    ready = False
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{base}/api/health", timeout=1.0)
            ready = True
            break
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                ready = True
                break
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    if not ready:
        proc.terminate()
        pytest.fail(f"BTerminal didn't come up within 15s")

    token_path = os.path.join(home, ".config/bterminal/debug_token")
    token = open(token_path).read().strip()

    yield {"base": base, "token": token, "home": home, "proc": proc}

    try:
        os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
    except (ProcessLookupError, PermissionError):
        proc.terminate()
    try:
        proc.wait(timeout=3)
    except subprocess.TimeoutExpired:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
        except (ProcessLookupError, PermissionError):
            proc.kill()
        try:
            proc.wait(timeout=2)
        except subprocess.TimeoutExpired:
            pass
    shutil.rmtree(home, ignore_errors=True)


def _fetch_last_intro(client):
    """Read /api/debug/feed_log?label=intro_prompt → decoded text of latest event."""
    req = urllib.request.Request(
        f"{client['base']}/api/debug/feed_log?label=intro_prompt",
        headers={"Authorization": f"Bearer {client['token']}"},
    )
    data = json.loads(urllib.request.urlopen(req, timeout=5).read())
    if not data["events"]:
        return None
    return base64.b64decode(data["events"][-1]["bytes_b64"]).decode("utf-8", errors="replace")


def _open_claude(client, config_name):
    req = urllib.request.Request(
        f"{client['base']}/api/tabs/ai/claude",
        data=json.dumps({"config_name": config_name}).encode(),
        headers={"Authorization": f"Bearer {client['token']}",
                 "Content-Type": "application/json"},
        method="POST",
    )
    json.loads(urllib.request.urlopen(req, timeout=8).read())
    time.sleep(1)


def test_default_enabled_plugins_includes_plugin_context(isolated_bterminal):
    """enabled_plugins=None (klucz nieobecny w config) → wszystkie plugins
    included w intro prompt (backwards compat)."""
    _open_claude(isolated_bterminal, "with_default")
    intro = _fetch_last_intro(isolated_bterminal)
    assert intro is not None, "no intro_prompt feed event captured"
    assert "PLUGIN_CTX_MARKER_xyz" in intro, (
        f"default enabled_plugins powinno zawierać plugin context, "
        f"ale brak markera w intro:\n{intro[:300]}"
    )


def test_empty_enabled_plugins_excludes_plugin_context(isolated_bterminal):
    """enabled_plugins=[] (explicit empty) → plugin context NIE w intro.

    Regression dla bug 2026-05-04 — open_claude_tab ustawiało
    enabled_plugins PO spawn_claude, więc intro zawsze miało wszystkie."""
    _open_claude(isolated_bterminal, "with_empty")
    intro = _fetch_last_intro(isolated_bterminal)
    assert intro is not None, "no intro_prompt feed event captured"
    assert "PLUGIN_CTX_MARKER_xyz" not in intro, (
        f"enabled_plugins=[] should EXCLUDE plugin context, "
        f"ale marker JEST w intro:\n{intro[:500]}"
    )
