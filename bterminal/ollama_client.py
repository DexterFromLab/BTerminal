"""bterminal.ollama_client — thin wrapper around the Ollama CLI/API.

Audit doc § 5: Ollama is the recommended local LLM backend for the
Aider provider (#3). This module isolates ollama interaction so the
OptionsDialog "Local Models" section + future model-manager UI don't
have to spawn subprocesses ad-hoc.

Two surfaces:
  - CLI mode  — runs `ollama list / pull / rm` (works without daemon
                running in some commands; pull/list need daemon up).
  - HTTP mode — hits `http://localhost:11434/api/tags` for inventory
                without spawning a subprocess (useful in tight UI
                refresh loops).

Pure parsers exposed for unit tests (no subprocess, no network).
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import urllib.error
import urllib.request
from dataclasses import dataclass
from typing import Optional


DEFAULT_API_URL = "http://localhost:11434"


@dataclass
class OllamaModel:
    """One row in the local Ollama model registry."""
    name: str           # e.g. "qwen2.5-coder:0.5b"
    size_gb: float      # rounded to 1 decimal
    modified: str       # human "2 hours ago" / ISO ts (whichever ollama gives)
    digest: str = ""    # optional sha256 prefix


# ─── Pure parsers ──────────────────────────────────────────────────────────


def parse_ollama_list_output(stdout: str) -> list[OllamaModel]:
    """Parse `ollama list` tabular output. Format (v0.4+):

        NAME                       ID              SIZE     MODIFIED
        qwen2.5-coder:0.5b         5a2c...         397 MB   2 hours ago
        llama3.1:8b                a1b2...         4.7 GB   3 days ago

    Returns [] when stdout is empty or only contains the header.
    Sizes 'X MB' / 'X GB' / 'X kB' normalized to GB float.
    """
    out: list[OllamaModel] = []
    lines = [l.rstrip() for l in stdout.splitlines() if l.strip()]
    if not lines:
        return out
    # First line is header (NAME / ID / SIZE / MODIFIED). Skip.
    for line in lines[1:]:
        # Whitespace-separated; modified is everything after column 4.
        # Use rsplit-style: 4 fields then the rest is "modified" text.
        # Layout uses multiple spaces, regex-friendly.
        import re
        m = re.match(
            r"^(\S+)\s+(\S+)\s+([\d.]+)\s*(GB|MB|kB|KB|B)\s+(.+?)$",
            line,
        )
        if not m:
            continue
        name, digest, num, unit, modified = m.groups()
        size_gb = _normalize_to_gb(float(num), unit)
        out.append(OllamaModel(
            name=name, size_gb=size_gb,
            modified=modified.strip(), digest=digest,
        ))
    return out


def parse_ollama_api_tags(payload: dict) -> list[OllamaModel]:
    """Parse JSON payload from `:11434/api/tags` (more reliable than
    parsing CLI output across ollama versions). Schema (v0.4+):

        {"models": [
          {"name": "qwen2.5-coder:0.5b",
           "modified_at": "2026-05-07T...",
           "size": 397441024,
           "digest": "5a2c...",
           "details": {...}},
          ...
        ]}
    """
    out: list[OllamaModel] = []
    for m in payload.get("models") or []:
        if not isinstance(m, dict):
            continue
        name = m.get("name") or m.get("model") or ""
        if not name:
            continue
        size_bytes = m.get("size") or 0
        out.append(OllamaModel(
            name=name,
            size_gb=round(size_bytes / (1024 ** 3), 1),
            modified=str(m.get("modified_at", ""))[:19],
            digest=str(m.get("digest", ""))[:12],
        ))
    return out


def _normalize_to_gb(num: float, unit: str) -> float:
    u = unit.upper()
    if u == "GB":
        return round(num, 1)
    if u == "MB":
        return round(num / 1024, 2)
    if u in ("KB",):
        return round(num / (1024 ** 2), 4)
    if u == "B":
        return round(num / (1024 ** 3), 6)
    return round(num, 1)


# ─── Daemon probes ─────────────────────────────────────────────────────────


def is_daemon_running(api_url: str = DEFAULT_API_URL,
                      timeout: float = 1.0) -> bool:
    """Quick TCP check — `:11434/api/tags` responds 200. False on
    timeout / connection refused / non-200."""
    try:
        urllib.request.urlopen(
            f"{api_url.rstrip('/')}/api/tags", timeout=timeout)
        return True
    except (urllib.error.URLError, OSError):
        return False


def is_cli_installed() -> bool:
    return shutil.which("ollama") is not None


# ─── Daemon lifecycle (task #151) ───────────────────────────────────────────


def start_daemon() -> tuple[bool, str]:
    """Spawn `ollama serve` in the background, detached from the
    parent process so it outlives BTerminal.

    Returns (ok, message). ok=True iff a daemon is running within
    5 s of spawn (poll the API). ok=False when:
      - ollama CLI not installed
      - spawn raised OSError
      - timeout polling /api/tags after 5 s

    The daemon stdout/stderr go to /dev/null. If the user wants
    logs, they can launch `journalctl --user-unit ollama` (when
    systemd unit is installed) or run `ollama serve` from a terminal."""
    if not is_cli_installed():
        return (False, "ollama CLI not installed")
    if is_daemon_running():
        return (True, "Daemon already running")
    import subprocess as _sp
    import time as _time
    try:
        _sp.Popen(
            ["ollama", "serve"],
            stdin=_sp.DEVNULL,
            stdout=_sp.DEVNULL,
            stderr=_sp.DEVNULL,
            start_new_session=True,
        )
    except OSError as exc:
        return (False, f"Failed to spawn: {exc}")
    # Poll for up to 5 s — daemon takes a moment to bind :11434.
    deadline = _time.monotonic() + 5.0
    while _time.monotonic() < deadline:
        if is_daemon_running():
            return (True, "Daemon started")
        _time.sleep(0.3)
    return (False, "Daemon spawned but did not respond on :11434 within 5 s")


def stop_daemon() -> tuple[bool, str]:
    """SIGTERM any `ollama serve` process owned by current user.

    Doesn't touch system-managed daemons (systemd `ollama.service`)
    — those should be managed via `systemctl`. Returns (ok, message)."""
    if not is_daemon_running():
        return (True, "Daemon not running")
    import subprocess as _sp
    try:
        # pgrep returns matching PIDs; -u limits to current user
        # (avoids killing root-owned systemd unit).
        result = _sp.run(
            ["pgrep", "-u", str(os.getuid()), "-f", "ollama serve"],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, _sp.TimeoutExpired) as exc:
        return (False, f"pgrep failed: {exc}")
    pids = [int(p) for p in result.stdout.split() if p.isdigit()]
    if not pids:
        # Daemon running but not owned by us (probably systemd).
        return (False, "Daemon owned by another user (systemd?). "
                       "Use: sudo systemctl stop ollama")
    import signal as _signal
    for pid in pids:
        try:
            os.kill(pid, _signal.SIGTERM)
        except (OSError, ProcessLookupError):
            pass
    # Give it 3 s to exit gracefully, then verify
    import time as _time
    deadline = _time.monotonic() + 3.0
    while _time.monotonic() < deadline:
        if not is_daemon_running():
            return (True, f"Daemon stopped (PID(s): {pids})")
        _time.sleep(0.3)
    return (False, f"SIGTERM sent to {pids} but daemon still responding")


# ─── List models ───────────────────────────────────────────────────────────


def list_models(api_url: str = DEFAULT_API_URL,
                timeout: float = 3.0) -> list[OllamaModel]:
    """Live snapshot of installed models. Prefers HTTP API (no
    subprocess overhead, no version-dependent CLI parsing); falls
    back to CLI when the daemon isn't reachable; returns [] when
    neither path works (no daemon + no CLI installed)."""
    # HTTP first
    try:
        with urllib.request.urlopen(
                f"{api_url.rstrip('/')}/api/tags", timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return parse_ollama_api_tags(data)
    except (urllib.error.URLError, OSError, json.JSONDecodeError):
        pass

    # CLI fallback (only if installed)
    if not is_cli_installed():
        return []
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=timeout,
        )
        if result.returncode != 0:
            return []
        return parse_ollama_list_output(result.stdout)
    except (OSError, subprocess.SubprocessError):
        return []


# ─── Mutations (pull / delete) ─────────────────────────────────────────────


def delete_model(name: str, timeout: float = 10.0) -> tuple[bool, str]:
    """Run `ollama rm <name>`. Returns (success, message). Caller
    confirms with the user BEFORE invoking — no UI here."""
    if not is_cli_installed():
        return False, "ollama CLI not installed"
    try:
        result = subprocess.run(
            ["ollama", "rm", name],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"subprocess failed: {exc}"
    if result.returncode == 0:
        return True, result.stdout.strip() or f"deleted {name}"
    return False, (result.stderr or result.stdout).strip()


def pull_model(name: str, timeout: float = 600.0) -> tuple[bool, str]:
    """Synchronous `ollama pull <name>`. Blocks until done — UI should
    use Gio.Subprocess for streaming progress. Returns (success, msg)."""
    if not is_cli_installed():
        return False, "ollama CLI not installed"
    try:
        result = subprocess.run(
            ["ollama", "pull", name],
            capture_output=True, text=True, timeout=timeout,
        )
    except (OSError, subprocess.SubprocessError) as exc:
        return False, f"subprocess failed: {exc}"
    if result.returncode == 0:
        return True, "pulled successfully"
    return False, (result.stderr or result.stdout).strip()


__all__ = [
    "OllamaModel",
    "parse_ollama_list_output",
    "parse_ollama_api_tags",
    "is_daemon_running",
    "is_cli_installed",
    "start_daemon",
    "stop_daemon",
    "list_models",
    "delete_model",
    "pull_model",
    "DEFAULT_API_URL",
]
