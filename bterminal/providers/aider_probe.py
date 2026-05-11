"""bterminal.providers.aider_probe — hardware probe + model recommendation.

Foundation under the aider setup wizard (BUG#20/21). Two responsibilities:

  1. Read host RAM + GPU VRAM in a defensive way (every probe falls back
     to 0.0 rather than raising). On Linux desktops we have psutil for RAM
     and nvidia-smi for VRAM — anything exotic returns 0.

  2. Score a curated catalog (bterminal/providers/aider_models.json)
     against probed hardware and recommend a model. The semaforowa
     table in the wizard reads {✓, ⚠, ✗} directly from this scoring.

Deliberately kept side-effect-free and synchronous: easy to mock from
tests, no UI imports, no Gtk. The wizard CLI (tools/aider_setup_wizard)
and the GTK fallback dialogs share these helpers.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
import threading
import time
import urllib.error
import urllib.request
from typing import Optional

CATALOG_PATH = os.path.join(os.path.dirname(__file__), "aider_models.json")
CACHE_PATH = os.path.join(
    os.path.expanduser("~"), ".config", "bterminal",
    "aider_models_catalog.json",
)
RAW_CATALOG_URL = (
    "https://raw.githubusercontent.com/DexterFromLab/BTerminal/master/"
    "bterminal/providers/aider_models.json"
)
CACHE_TTL_SECONDS = 7 * 24 * 3600  # 7 days


# ─── Hardware probes (defensive — never raise) ─────────────────────────────


def detect_ram_gb() -> float:
    """Total system RAM in GB (rounded to 1 decimal). 0.0 on hard failure."""
    try:
        import psutil
        return round(psutil.virtual_memory().total / (1024 ** 3), 1)
    except ImportError:
        pass
    try:
        with open("/proc/meminfo", encoding="utf-8") as fh:
            for line in fh:
                if line.startswith("MemTotal:"):
                    kb = int(line.split()[1])
                    return round(kb / (1024 ** 2), 1)
    except OSError:
        pass
    return 0.0


def detect_vram_gb() -> float:
    """Largest single GPU's VRAM in GB. 0.0 when no NVIDIA GPU detected.

    Multi-GPU systems return the *largest* card — Ollama loads one model
    onto one device, so two 8 GB cards don't behave like a 16 GB card.
    AMD/Intel GPUs aren't probed (rocm-smi works, but practical Ollama
    GPU offload outside CUDA is unreliable as of 2026).
    """
    if not shutil.which("nvidia-smi"):
        return 0.0
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
    except (OSError, subprocess.SubprocessError):
        return 0.0
    if result.returncode != 0:
        return 0.0
    best = 0.0
    for line in result.stdout.splitlines():
        try:
            vram_mb = int(line.strip())
            best = max(best, round(vram_mb / 1024, 1))
        except ValueError:
            continue
    return best


# ─── Ollama state — what's already pulled ──────────────────────────────────


def is_model_installed(tag: str) -> bool:
    """Bare tag (sans `openai/` / `ollama/` litellm prefix) in `ollama list`."""
    bare = tag.split("/", 1)[1] if "/" in tag else tag
    try:
        result = subprocess.run(
            ["ollama", "list"],
            capture_output=True, text=True, timeout=5,
        )
    except (FileNotFoundError, subprocess.SubprocessError, OSError):
        return False
    if result.returncode != 0:
        return False
    for line in result.stdout.splitlines()[1:]:  # skip header
        parts = line.split()
        if parts and parts[0] == bare:
            return True
    return False


# ─── Catalog loader ────────────────────────────────────────────────────────


def _cache_is_fresh(cache_path: Optional[str] = None,
                    ttl: int = CACHE_TTL_SECONDS) -> bool:
    """True when the cache file exists and was modified ≤ttl seconds ago.

    cache_path resolves at call time (not at module load) so tests can
    monkeypatch aider_probe.CACHE_PATH after import.
    """
    if cache_path is None:
        cache_path = CACHE_PATH
    try:
        return (time.time() - os.path.getmtime(cache_path)) <= ttl
    except OSError:
        return False


def _read_json_catalog(path: str) -> dict:
    """Load a single JSON file as a catalog dict. {} on any error."""
    try:
        with open(path, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError):
        return {}
    if not isinstance(data, dict) or "models" not in data:
        return {}
    return data


def load_catalog(path: Optional[str] = None) -> dict:
    """Resolve and load the model catalog (BUG#24).

    With no `path`:
      - prefer the on-disk cache (~/.config/bterminal/aider_models_catalog.json)
        when it's fresh (mtime ≤ 7d) — this is what refresh_catalog_background
        produces from the upstream GitHub raw URL
      - fall back to the bundled CATALOG_PATH (always present in the repo)
      - return {} only when both sources fail to parse

    With an explicit `path` (used by tests + fixtures), skip the cache
    logic entirely and read that file.
    """
    if path is not None:
        return _read_json_catalog(path)
    if _cache_is_fresh():
        cached = _read_json_catalog(CACHE_PATH)
        if cached:
            return cached
    return _read_json_catalog(CATALOG_PATH)


def _write_cache(payload: dict, cache_path: Optional[str] = None) -> bool:
    """Atomically write `payload` to the cache path. False on IO error.

    cache_path resolves at call time (see _cache_is_fresh docstring).
    """
    if cache_path is None:
        cache_path = CACHE_PATH
    try:
        os.makedirs(os.path.dirname(cache_path), exist_ok=True)
        tmp = cache_path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as fh:
            json.dump(payload, fh, indent=2, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, cache_path)
        return True
    except OSError:
        return False


def fetch_remote_catalog(
    url: str = RAW_CATALOG_URL,
    timeout: float = 5.0,
) -> Optional[dict]:
    """Synchronously GET the raw catalog JSON. None on any error.

    Validates `models` key presence — guards against accidental HTML
    responses (e.g. GitHub serving a 404 page) ending up cached as
    a "valid" catalog.
    """
    try:
        with urllib.request.urlopen(url, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8")
    except (urllib.error.URLError, OSError, ValueError):
        return None
    try:
        data = json.loads(raw)
    except json.JSONDecodeError:
        return None
    if not isinstance(data, dict) or "models" not in data:
        return None
    return data


def refresh_catalog_background(
    url: str = RAW_CATALOG_URL,
    timeout: float = 5.0,
    on_done=None,
) -> threading.Thread:
    """Spawn a daemon thread that fetches the remote catalog and writes
    it to the local cache. Returns the Thread (started, not joined).

    Callers (BTerminalApp.__init__ today, future wizard CLI on-demand
    refresh) don't need to join — failures are silent by design (no
    network → keep bundled, user never sees a popup at startup).
    on_done(success: bool) is invoked on completion when provided.
    """
    def _worker():
        payload = fetch_remote_catalog(url=url, timeout=timeout)
        ok = bool(payload) and _write_cache(payload)
        if on_done:
            try:
                on_done(ok)
            except Exception:
                pass  # never crash a background daemon on callback errors

    t = threading.Thread(
        target=_worker, name="aider-catalog-refresh", daemon=True,
    )
    t.start()
    return t


# ─── Recommendation algorithm ──────────────────────────────────────────────


def _ram_budget(ram_gb: float) -> float:
    """Headroom for OS + Ollama overhead. 70% of total RAM is the cap that
    keeps a browser + IDE responsive while a model is resident."""
    return ram_gb * 0.70


def recommend_model(
    catalog: dict,
    ram_gb: float,
    vram_gb: float = 0.0,
) -> Optional[dict]:
    """Pick the best-fitting entry from `catalog["models"]`.

    Rules (matches BUG#20 spec):
      - filter to entries whose ram_gb ≤ 70% of host RAM
      - of those, take the largest (highest ram_gb)
      - if VRAM > 0 AND VRAM < RAM (smaller GPU than RAM), step down
        one tier — running fully on GPU is safer than partial CPU
        offload for whole-edit format
      - return the full model dict (None when nothing fits)
    """
    models = (catalog.get("models") or []) if isinstance(catalog, dict) else []
    budget = _ram_budget(ram_gb)
    fitting = sorted(
        (m for m in models if m.get("ram_gb", 0) <= budget),
        key=lambda m: m.get("ram_gb", 0),
    )
    if not fitting:
        return None
    if vram_gb > 0 and vram_gb < ram_gb and len(fitting) >= 2:
        return fitting[-2]
    return fitting[-1]


def score_model(model: dict, ram_gb: float, vram_gb: float = 0.0) -> str:
    """Return one of {'fits', 'tight', 'too_big'}.

    - 'fits'   : model.ram_gb ≤ 70% of host RAM (semafor ✓)
    - 'tight'  : model.ram_gb > 70% but ≤ 100% — runs, swaps under load (⚠)
    - 'too_big': model.ram_gb > host RAM (✗)
    """
    req = model.get("ram_gb", 0)
    if req <= _ram_budget(ram_gb):
        return "fits"
    if req <= ram_gb:
        return "tight"
    return "too_big"


# ─── Wizard handoff: sentinel parsing + relaunch config ────────────────────


SENTINEL_PATH = os.path.join(
    os.path.expanduser("~"), ".config", "bterminal",
    ".aider_wizard_done.json",
)


def read_sentinel(path: Optional[str] = None) -> Optional[dict]:
    """Read the wizard's done-sentinel. None when missing/unreadable."""
    target = path or SENTINEL_PATH
    if not os.path.isfile(target):
        return None
    try:
        with open(target, encoding="utf-8") as fh:
            data = json.load(fh)
    except (OSError, json.JSONDecodeError, ValueError):
        return None
    if not isinstance(data, dict):
        return None
    return data


def compute_relaunch_config(
    sentinel_payload: Optional[dict],
    original_config: dict,
) -> Optional[dict]:
    """Decide whether the wizard's sentinel should drive a session relaunch.

    Returns:
      - a new config dict (deep-ish copy of `original_config`, with
        provider_options.model = sentinel.model) when the sentinel
        belongs to this session
      - None when the sentinel is missing, malformed, points at a
        different session_id, or doesn't carry a model

    The caller (App._on_aider_wizard_done) owns the GTK side — spawning
    the new tab, removing the sentinel file, etc. — so this function
    stays pure for tests.
    """
    if not sentinel_payload or not isinstance(sentinel_payload, dict):
        return None
    model = sentinel_payload.get("model")
    if not model:
        return None
    target_sid = sentinel_payload.get("session_id")
    if target_sid != original_config.get("id"):
        return None
    new_config = dict(original_config)
    opts = dict(new_config.get("provider_options") or {})
    opts["model"] = model
    new_config["provider_options"] = opts
    return new_config


__all__ = [
    "CATALOG_PATH",
    "CACHE_PATH",
    "CACHE_TTL_SECONDS",
    "RAW_CATALOG_URL",
    "SENTINEL_PATH",
    "detect_ram_gb",
    "detect_vram_gb",
    "is_model_installed",
    "load_catalog",
    "recommend_model",
    "score_model",
    "read_sentinel",
    "compute_relaunch_config",
    "fetch_remote_catalog",
    "refresh_catalog_background",
]
