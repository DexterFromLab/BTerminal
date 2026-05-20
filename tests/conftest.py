"""Pytest fixtures for BTerminal debug-REST integration tests.

Requires `httpx` in the test environment (pip install httpx).

The bterminal_process fixture spawns BTerminal under Xvfb with --debug-rest,
waits for /api/health, yields a typed client, then tears it down via
POST /api/quit?confirm=true (fallback: SIGTERM, then SIGKILL).
"""

import os
import shutil
import signal
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

import httpx
import pytest

REPO_ROOT = Path(__file__).parent.parent
# Make `import bterminal` work in tests that need internal classes
# (SidecarManifest/Discovery etc) without going through subprocess.
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_TEST_PORT = int(os.environ.get("BTERMINAL_TEST_REST_PORT", "7790"))
DEBUG_REST_BASE = f"http://127.0.0.1:{_TEST_PORT}"
HEALTH_TIMEOUT_SEC = 10.0
HEALTH_POLL_INTERVAL = 0.3
QUIT_TIMEOUT_SEC = 2.0
TERMINATE_TIMEOUT_SEC = 5.0


@dataclass
class BTerminalClient:
    """Test-scope handle to a running BTerminal --debug-rest process."""

    process: subprocess.Popen
    base_url: str
    token: str
    http_client: httpx.Client
    home: str
    stderr_log: str = ""    # path to captured stderr log (set by fixture)


def _wait_for_server(deadline_ts: float) -> bool:
    """Poll /api/health until any HTTP response (401 counts — server is up)
    or until deadline_ts is reached. Returns True on success.
    """
    while time.monotonic() < deadline_ts:
        try:
            urllib.request.urlopen(
                f"{DEBUG_REST_BASE}/api/health", timeout=1.0
            )
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return True  # auth wall = process is alive and routing
        except (urllib.error.URLError, ConnectionError, OSError):
            pass
        time.sleep(HEALTH_POLL_INTERVAL)
    return False


class StderrWatcher:
    """Cursor-based stderr watcher dla BTerminal subprocess.

    Każdy `check()` weryfikuje że BRAK NOWYCH error patterns w stderr od
    ostatniego check'u. Złapie NameError / AttributeError / TypeError /
    Traceback które inaczej giną w GTK signal callbackach.

    Ignoruje znane benigne komunikaty (Gtk-WARNING, color, gdk-pixbuf).
    """

    BAD_PATTERNS = (
        "Traceback (most recent call last):",
        "NameError:", "AttributeError:", "TypeError:", "ImportError:",
        "ModuleNotFoundError:", "KeyError:", "ValueError:",
    )

    def __init__(self, log_path: str):
        self.path = log_path
        # Start from END of file — ignore everything that happened during
        # BT boot (we want to catch errors per-action, not pre-existing).
        self._cursor = self._size()

    def _size(self) -> int:
        try:
            return os.path.getsize(self.path)
        except OSError:
            return 0

    def check(self, action: str = ""):
        """Assert no NEW error patterns since last check (or fixture start).

        `action` is included in failure message dla łatwego pinpointu
        który REST call wywołał problem.
        """
        size = self._size()
        if size <= self._cursor:
            return
        try:
            with open(self.path, "rb") as f:
                f.seek(self._cursor)
                new = f.read().decode("utf-8", errors="replace")
            self._cursor = size
        except OSError as exc:
            pytest.fail(f"cannot read stderr log {self.path}: {exc}")
            return

        bad_lines = []
        for line in new.splitlines():
            if any(p in line for p in self.BAD_PATTERNS):
                bad_lines.append(line)

        if bad_lines:
            pytest.fail(
                f"BTerminal stderr errors after action '{action}':\n"
                + "\n".join(f"  {l}" for l in bad_lines[:10])
                + (f"\n  ... +{len(bad_lines)-10} more" if len(bad_lines) > 10 else "")
            )


@pytest.fixture
def bt_stderr_watcher(bterminal_process):
    """Function-scoped watcher na BTerminal subprocess stderr.

    Usage:
        def test_open_claude_tab(bterminal_process, bt_stderr_watcher):
            resp = bterminal_process.http_client.post("/api/tabs/claude", ...)
            assert resp.status_code == 200
            bt_stderr_watcher.check("open_claude_tab")  # asercja clean stderr
    """
    return StderrWatcher(bterminal_process.stderr_log)


class FeedCapture:
    """Wrapper na `GET /api/debug/feed_log` — łatwy interfejs dla testów.

    Usage:
        def test_intro(bterminal_process, vte_capture):
            # ... open Claude tab ...
            events = vte_capture.events_for("intro_prompt")
            assert any("## Rules" in e.text for e in events)
    """

    def __init__(self, http_client):
        self._http = http_client
        self._since = 0.0  # timestamp pivot — only events after this

    def reset(self):
        """Mark current time as new pivot — subsequent events_for() returns
        only events recorded AFTER this call."""
        import time as _time
        self._since = _time.time()

    def events_for(self, label=None, since=None):
        """Fetch captured feed events. Optionally filter by label.

        Returns list of FeedEvent (dataclass-like dict): {ts, label, tab_idx,
        text}. `text` is bytes_b64 decoded as UTF-8 (replace errors).
        """
        import base64
        params = {"since": since if since is not None else self._since}
        if label:
            params["label"] = label
        resp = self._http.get("/api/debug/feed_log", params=params)
        resp.raise_for_status()
        events = resp.json()["events"]
        # Decode bytes for convenience
        for e in events:
            e["text"] = base64.b64decode(e["bytes_b64"]).decode("utf-8", errors="replace")
        return events

    def wait_for(self, label, timeout=10.0, poll=0.2):
        """Block until ≥1 event with given label appears, or timeout.
        Returns the first matching event."""
        import time as _time
        deadline = _time.monotonic() + timeout
        while _time.monotonic() < deadline:
            events = self.events_for(label=label)
            if events:
                return events[0]
            _time.sleep(poll)
        raise TimeoutError(
            f"No '{label}' feed event captured within {timeout}s"
        )


@pytest.fixture
def vte_capture(bterminal_process):
    """Function-scoped fixture for capturing bytes BTerminal sends to AI CLI.

    Each test starts with a fresh `since` pivot so previous test's events
    don't leak. Uses cooperative capture via `record_feed()` calls inside
    BTerminal at sites of interest:
      - intro_prompt — fed to spawn'd subprocess
      - auto_trigger — task auto-trigger [AUTO-TRIGGER] message
      - rules_inject — periodic rules re-injection block
      - ctx_refresh — ctx refresh follow-up (after rules)

    Foundation for E2E tests asserting WHAT BTerminal said to the CLI
    (provider-agnostic).
    """
    capture = FeedCapture(bterminal_process.http_client)
    capture.reset()
    return capture


@pytest.fixture(scope="session")
def bterminal_process():
    """Session-scoped BTerminal process under Xvfb with debug-REST enabled.

    Yields BTerminalClient with a preconfigured httpx.Client (Bearer auth,
    base_url set). Tests just call `bt.http_client.get('/api/state')` etc.

    Cleanup order: graceful POST /api/quit?confirm=true (2s) → terminate
    (5s) → kill → close http client → rmtree isolated HOME.
    """
    home = tempfile.mkdtemp(prefix="bterminal-test-home-")
    # Seed a lightweight sidecar manifest so refcount + per-tab tests can
    # exercise the full lifecycle without depending on agent_controller.
    sidecars_dir = Path(home) / ".config" / "bterminal" / "sidecars"
    sidecars_dir.mkdir(parents=True, exist_ok=True)
    import json as _json
    (sidecars_dir / "test_sleeper.json").write_text(_json.dumps({
        "name": "test_sleeper",
        "title": "TestSleeper",
        "run_command": "sleep 9999",
        # Fake URL — sleep doesn't serve HTTP, but having a non-empty
        # healthcheck_url lets the health endpoint short-circuit on
        # is_running rather than complaining about a missing field.
        "healthcheck_url": "http://127.0.0.1:65500/never",
        "default_in_session": False,
        "auto_start": False,
    }))
    # Seed a minimal GTK plugin so hot toggle tests can verify the
    # importlib + activate/deactivate paths end-to-end.
    plugins_dir = Path(home) / ".config" / "bterminal" / "plugins"
    plugins_dir.mkdir(parents=True, exist_ok=True)
    (plugins_dir / "test_panel.py").write_text(
        "import gi\n"
        "gi.require_version('Gtk', '3.0')\n"
        "from gi.repository import Gtk\n\n"
        "class BTerminalPlugin:\n"
        "    name=''; title=''; version=''; description=''; author=''\n"
        "    default_in_session=True\n"
        "    def activate(self, app): return None\n"
        "    def deactivate(self): pass\n"
        "    def get_keyboard_shortcuts(self): return []\n"
        "    def on_sidebar_shown(self): pass\n"
        "    def get_session_context(self): return None\n\n"
        "class TestPanel(BTerminalPlugin):\n"
        "    name='test_panel'; title='TestPanel'; version='0.0.1'\n"
        "    def activate(self, app):\n"
        "        b = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)\n"
        "        b.pack_start(Gtk.Label(label='test_panel active'), False, False, 0)\n"
        "        return b\n\n"
        "def create_plugin(app): return TestPanel()\n"
    )
    # Pre-accept license so the subprocess doesn't open a modal GTK
    # dialog and block debug-REST startup (see _subprocess_helpers).
    from tests._subprocess_helpers import seed_license
    seed_license(home)
    env = {**os.environ, "HOME": home,
           "BTERMINAL_DEBUG_REST_PORT": str(_TEST_PORT),
           # BUG#31g: component fixture spawns BT with the sudo cache in
           # fake mode so /api/debug/sudo_submit can populate it without
           # requiring real root. Production paths are unaffected — the
           # var only short-circuits SudoAskpassCache.ensure().
           "BTERMINAL_TEST_FAKE_SUDO": "1"}
    # Capture stderr to file — runtime errors w GTK callbacks (NameError z
    # refactoringów, AttributeError z brakujących imports, etc.) idą do
    # stderr i bez tego ginęłyby w DEVNULL. bt_stderr_watcher fixture +
    # smoke battery testują clean log po każdej akcji.
    stderr_log_path = Path(home) / "bterminal-stderr.log"
    stderr_handle = open(stderr_log_path, "w")
    proc = subprocess.Popen(
        ["xvfb-run", "-a", sys.executable, "-m", "bterminal", "--debug-rest"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=stderr_handle,
        # xvfb-run wraps Xvfb + Python — bez nowej grupy procesów
        # SIGTERM nie propaguje się na child Pythona, leaving zombies.
        start_new_session=True,
    )
    client: httpx.Client | None = None
    try:
        deadline = time.monotonic() + HEALTH_TIMEOUT_SEC
        if not _wait_for_server(deadline):
            raise RuntimeError(
                f"BTerminal debug-REST did not respond within "
                f"{HEALTH_TIMEOUT_SEC}s on {DEBUG_REST_BASE}/api/health"
            )
        token_path = Path(home) / ".config" / "bterminal" / "debug_token"
        if not token_path.exists():
            raise RuntimeError(f"debug_token not created at {token_path}")
        token = token_path.read_text().strip()
        if not token:
            raise RuntimeError(f"debug_token is empty: {token_path}")
        client = httpx.Client(
            base_url=DEBUG_REST_BASE,
            headers={"Authorization": f"Bearer {token}"},
            timeout=5.0,
        )
        yield BTerminalClient(
            process=proc,
            base_url=DEBUG_REST_BASE,
            token=token,
            http_client=client,
            home=home,
            stderr_log=str(stderr_log_path),
        )
    finally:
        if client is not None:
            try:
                client.post(
                    "/api/quit", params={"confirm": "true"},
                    timeout=QUIT_TIMEOUT_SEC,
                )
            except (httpx.HTTPError, httpx.TimeoutException):
                pass
            try:
                client.close()
            except Exception:  # noqa: BLE001
                pass
        if proc.poll() is None:
            # Kill całą grupę procesów (xvfb-run + Xvfb + python -m bterminal),
            # nie tylko wrapper. proc.terminate() wysyła SIGTERM tylko do
            # xvfb-run shellscript który nie zawsze łapie sygnał i nie
            # propaguje go do Python child.
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
            except (ProcessLookupError, PermissionError):
                proc.terminate()
            try:
                proc.wait(timeout=TERMINATE_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except (ProcessLookupError, PermissionError):
                    proc.kill()
                try:
                    proc.wait(timeout=2.0)
                except subprocess.TimeoutExpired:
                    pass
        try:
            stderr_handle.close()
        except Exception:  # noqa: BLE001
            pass
        shutil.rmtree(home, ignore_errors=True)
