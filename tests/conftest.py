"""Pytest fixtures for BTerminal debug-REST integration tests.

Requires `httpx` in the test environment (pip install httpx).

The bterminal_process fixture spawns BTerminal under Xvfb with --debug-rest,
waits for /api/health, yields a typed client, then tears it down via
POST /api/quit?confirm=true (fallback: SIGTERM, then SIGKILL).
"""

import os
import shutil
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

DEBUG_REST_BASE = "http://127.0.0.1:7780"
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


@pytest.fixture(scope="session")
def bterminal_process():
    """Session-scoped BTerminal process under Xvfb with debug-REST enabled.

    Yields BTerminalClient with a preconfigured httpx.Client (Bearer auth,
    base_url set). Tests just call `bt.http_client.get('/api/state')` etc.

    Cleanup order: graceful POST /api/quit?confirm=true (2s) → terminate
    (5s) → kill → close http client → rmtree isolated HOME.
    """
    home = tempfile.mkdtemp(prefix="bterminal-test-home-")
    env = {**os.environ, "HOME": home}
    proc = subprocess.Popen(
        ["xvfb-run", "-a", sys.executable, "bterminal.py", "--debug-rest"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
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
            proc.terminate()
            try:
                proc.wait(timeout=TERMINATE_TIMEOUT_SEC)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(home, ignore_errors=True)
