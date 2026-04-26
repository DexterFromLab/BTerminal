"""Slow: BT spawned with 2s idle threshold + 1s check tick must self-stop
the debug REST server after ~3-5s of no requests.

Runs on its own port (7781) so it doesn't fight the session-scoped
fixture on 7780.
"""

import os
import shutil
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
PORT = 7781
BASE = f"http://127.0.0.1:{PORT}"


def _server_responds(timeout=0.5) -> bool:
    """True if the REST server answers anything (200 OR 401)."""
    try:
        urllib.request.urlopen(f"{BASE}/api/health", timeout=timeout)
        return True
    except urllib.error.HTTPError as exc:
        return exc.code == 401
    except (urllib.error.URLError, ConnectionError, OSError):
        return False


@pytest.mark.slow
def test_idle_watchdog_stops_server():
    home = tempfile.mkdtemp(prefix="bterminal-idle-test-")
    env = {
        **os.environ,
        "HOME": home,
        "BTERMINAL_DEBUG_REST_PORT": str(PORT),
        "BTERMINAL_DEBUG_IDLE_TIMEOUT": "2",
        "BTERMINAL_DEBUG_IDLE_CHECK": "1",
    }
    proc = subprocess.Popen(
        ["xvfb-run", "-a", sys.executable, "bterminal.py", "--debug-rest"],
        cwd=str(REPO_ROOT),
        env=env,
        stdout=subprocess.DEVNULL,
        stderr=subprocess.DEVNULL,
    )
    try:
        # Wait for it to come up (max 10s)
        deadline = time.monotonic() + 10
        while time.monotonic() < deadline:
            if _server_responds():
                break
            time.sleep(0.2)
        else:
            pytest.fail("server didn't come up within 10s")

        # Wait past the idle threshold + watchdog tick + buffer.
        # 2s threshold + 1s check tick + 2s slack = 5s.
        time.sleep(5)

        assert not _server_responds(), (
            "server still responding after idle timeout — watchdog failed"
        )
    finally:
        if proc.poll() is None:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
        shutil.rmtree(home, ignore_errors=True)
