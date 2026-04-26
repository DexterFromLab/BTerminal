"""Manifest tests — unit (always run) + slow E2E (skipped without env)."""

import json
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest

from bterminal import SidecarManifest, SidecarRunner

EXAMPLES_DIR = Path(__file__).parent.parent / "examples" / "sidecars"


def test_btmsg_manifest_loads():
    raw = json.loads((EXAMPLES_DIR / "btmsg.json").read_text())
    m = SidecarManifest.from_dict(raw)
    assert m.name == "btmsg"
    assert m.plugin_address.startswith("http://127.0.0.1:8766")
    assert m.healthcheck_url.endswith("/api/health")
    assert m.run_command == "python3 -m plugins.btmsg.run"
    assert m.default_in_session is False
    assert m.auto_start is False
    assert "btmsg" in m.prompt.lower()


@pytest.mark.slow
def test_btmsg_starts_and_health():
    """Requires agent_controller/plugins available at manifest's cwd,
    python3-flask installed, and port 8766 free.
    """
    raw = json.loads((EXAMPLES_DIR / "btmsg.json").read_text())
    manifest = SidecarManifest.from_dict(raw)
    if not Path(manifest.cwd).exists():
        pytest.skip(f"manifest cwd missing: {manifest.cwd}")

    runner = SidecarRunner()
    try:
        runner.start("btmsg", manifest)
        deadline = time.monotonic() + 5
        ok = False
        while time.monotonic() < deadline:
            try:
                with urllib.request.urlopen(manifest.healthcheck_url, timeout=1) as r:
                    if r.status == 200:
                        ok = True
                        break
            except (urllib.error.URLError, OSError):
                pass
            time.sleep(0.2)
        assert ok, "btmsg /api/health never returned 200 within 5s"
    finally:
        runner.stop("btmsg")
