"""BTerminal sidecar plugin runtime — manifest, discovery, runner, health.

Sidecar plugins are external HTTP services that BTerminal launches as
subprocess.Popen children, identified by a JSON manifest in
~/.config/bterminal/sidecars/<name>.json. This is the second plugin
contract (alongside in-process GTK plugins in plugin_runtime.py).

Lifecycle:
  Discovery.load_all() → {name: SidecarManifest}
  Runner.start(name, manifest) → spawn subprocess, track PID
  HealthChecker.ping(url) → liveness probe for `/health`
  Runner.stop(name) → SIGTERM → SIGKILL escalation
  Runner.stop_all() → atexit cleanup hook

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/plugins/sidecar/` package in a later migration etap.
"""

import json
import os
import shlex
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass, field, fields

from bterminal.config import CONFIG_DIR


SIDECARS_DIR = os.path.join(CONFIG_DIR, "sidecars")
os.makedirs(SIDECARS_DIR, exist_ok=True)


@dataclass
class SidecarManifest:
    """Schema of a sidecar plugin manifest stored in
    ~/.config/bterminal/sidecars/<name>.json.

    Required: name. Everything else has a sensible default so partial
    manifests still load.
    """

    name: str
    plugin_address: str = ""
    plugin_dashboard: str = ""
    healthcheck_url: str = ""
    run_command: str = ""
    reset_command: str = ""
    cwd: str = ""
    env: dict = field(default_factory=dict)
    prompt: str = ""
    title: str = ""
    description: str = ""
    default_in_session: bool = True
    auto_start: bool = False

    @classmethod
    def from_dict(cls, data: dict) -> "SidecarManifest":
        """Build from a JSON dict, silently dropping unknown keys so a manifest
        with extra fields is still loadable."""
        known = {f.name for f in fields(cls)}
        kwargs = {k: v for k, v in data.items() if k in known}
        return cls(**kwargs)


class SidecarDiscovery:
    """Loads SidecarManifest objects from ~/.config/bterminal/sidecars/*.json.

    Skips non-JSON files, malformed JSON, and manifests missing 'name'.
    """

    def __init__(self, sidecars_dir: str = SIDECARS_DIR) -> None:
        self._dir = sidecars_dir

    def load_all(self) -> dict[str, "SidecarManifest"]:
        out: dict[str, SidecarManifest] = {}
        if not os.path.isdir(self._dir):
            return out
        for entry in sorted(os.listdir(self._dir)):
            if not entry.endswith(".json"):
                continue
            path = os.path.join(self._dir, entry)
            try:
                with open(path) as fh:
                    data = json.load(fh)
            except (OSError, json.JSONDecodeError):
                continue
            if not isinstance(data, dict) or not data.get("name"):
                continue
            manifest = SidecarManifest.from_dict(data)
            out[manifest.name] = manifest
        return out


class SidecarRunner:
    """Owns subprocess.Popen handles for live sidecars, keyed by manifest name.

    start() is idempotent (re-start while running is a no-op). Subprocesses
    use start_new_session=True so an unexpected BTerminal crash does not
    immediately reap them — atexit cleanup is the canonical shutdown path.
    """

    def __init__(self) -> None:
        self._procs: dict[str, subprocess.Popen] = {}

    def is_running(self, name: str) -> bool:
        proc = self._procs.get(name)
        return proc is not None and proc.poll() is None

    def start(self, name: str, manifest: SidecarManifest) -> dict:
        """Spawn the sidecar if not already running. Returns
        {already_running: bool, pid: int}.
        """
        if self.is_running(name):
            return {"already_running": True, "pid": self._procs[name].pid}
        if not manifest.run_command:
            raise RuntimeError(f"sidecar '{name}' has empty run_command")
        argv = shlex.split(manifest.run_command)
        env = {**os.environ, **(manifest.env or {})}
        cwd = manifest.cwd or None
        proc = subprocess.Popen(
            argv,
            cwd=cwd,
            env=env,
            start_new_session=True,
            stdout=subprocess.DEVNULL,
            stderr=subprocess.PIPE,
        )
        self._procs[name] = proc
        return {"already_running": False, "pid": proc.pid}

    def stop(self, name: str) -> dict:
        """Terminate the named sidecar. Idempotent — safe to call when not
        running. Escalates SIGTERM → SIGKILL after 5s.
        """
        proc = self._procs.get(name)
        if proc is None:
            return {"was_running": False}
        was_running = proc.poll() is None
        if was_running:
            proc.terminate()
            try:
                proc.wait(timeout=5)
            except subprocess.TimeoutExpired:
                proc.kill()
                try:
                    proc.wait(timeout=2)
                except subprocess.TimeoutExpired:
                    pass
        del self._procs[name]
        return {"was_running": was_running}

    def stop_all(self) -> None:
        """Best-effort shutdown of every tracked sidecar. Used by atexit."""
        for name in list(self._procs):
            try:
                self.stop(name)
            except Exception:  # noqa: BLE001 — never raise during shutdown
                pass


class HealthChecker:
    """Liveness probe for sidecar HTTP endpoints. Stateless — staticmethod."""

    @staticmethod
    def ping(url: str, timeout: float = 2.0) -> bool:
        """True if the URL responds with status < 500 within timeout.

        4xx counts as 'up' (the server is answering, it just doesn't like
        the request). Connection refused / timeout / DNS failure → False.
        """
        if not url:
            return False
        try:
            with urllib.request.urlopen(url, timeout=timeout) as resp:
                return resp.status < 500
        except urllib.error.HTTPError as exc:
            return exc.code < 500
        except (urllib.error.URLError, OSError):
            return False
