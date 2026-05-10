"""bterminal.system_probe — hardware introspection for local-LLM model picker.

Audit doc § 5: there's no hardware probe in the existing codebase, so this
module is built from scratch. Two public entry points:

    probe_system() -> dict
        Snapshot of available CPU/RAM/GPU/disk + presence of ollama/llama.cpp.
        All sub-probes are defensive: missing tools / non-Linux / no GPU
        return empty values rather than raising.

    recommend_models(probe) -> list[str]
        Heuristic mapping from the snapshot to a list of model names that
        the user can realistically run locally. Largest first.

Used by:
  - InstallerWizard page 3 to show "your machine fits these models"
  - OptionsDialog → Local Models section to recommend pulls
  - tests via mocked subprocess + psutil
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
from typing import Optional


# ─── Sub-probes (defensive — never raise) ──────────────────────────────────


def _probe_ram_gb() -> float:
    """Total RAM in GB. Uses psutil when available, /proc/meminfo fallback."""
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


def _probe_cpu_cores() -> int:
    return os.cpu_count() or 0


def _probe_cpu_flags() -> dict:
    """Look for AVX2 / AVX512 in /proc/cpuinfo. Defaults False on non-Linux."""
    flags = {"avx2": False, "avx512": False}
    try:
        with open("/proc/cpuinfo", encoding="utf-8") as fh:
            line_buf = fh.read(8192)  # first CPU stanza is enough
        flags["avx2"] = " avx2 " in f" {line_buf} "
        # AVX512 reports as avx512f / avx512vl / etc.
        flags["avx512"] = "avx512" in line_buf
    except OSError:
        pass
    return flags


def _probe_nvidia_gpus() -> list[dict]:
    """Returns [{name, vram_gb}, ...] via nvidia-smi. Empty list when
    nvidia-smi missing or returns no GPUs."""
    if not shutil.which("nvidia-smi"):
        return []
    try:
        result = subprocess.run(
            ["nvidia-smi", "--query-gpu=name,memory.total",
             "--format=csv,noheader,nounits"],
            capture_output=True, text=True, timeout=3,
        )
        if result.returncode != 0:
            return []
    except (OSError, subprocess.SubprocessError):
        return []
    out = []
    for line in result.stdout.splitlines():
        parts = [p.strip() for p in line.split(",")]
        if len(parts) >= 2:
            try:
                vram_mb = int(parts[1])
                out.append({"name": parts[0],
                            "vram_gb": round(vram_mb / 1024, 1)})
            except ValueError:
                continue
    return out


def _probe_amd_gpus() -> list[dict]:
    """Best-effort AMD GPU detection — rocm-smi when present, otherwise
    parse /sys/class/drm/card*/device/uevent for amdgpu driver. Returns
    empty when nothing detected; we don't try to size VRAM without
    rocm-smi (parsing sysfs reliably is too distro-dependent)."""
    if shutil.which("rocm-smi"):
        try:
            result = subprocess.run(
                ["rocm-smi", "--showproductname", "--csv"],
                capture_output=True, text=True, timeout=3,
            )
            if result.returncode == 0:
                lines = [l for l in result.stdout.splitlines()
                         if l and not l.lower().startswith(("device", "card"))]
                return [{"name": l.split(",")[-1].strip(), "vram_gb": 0.0}
                        for l in lines]
        except (OSError, subprocess.SubprocessError):
            pass

    # Fallback: count amdgpu devices via sysfs (no VRAM size).
    try:
        gpus = []
        for entry in sorted(os.listdir("/sys/class/drm")):
            if not entry.startswith("card") or "-" in entry:
                continue
            uevent = f"/sys/class/drm/{entry}/device/uevent"
            if not os.path.isfile(uevent):
                continue
            with open(uevent, encoding="utf-8") as fh:
                if "DRIVER=amdgpu" in fh.read():
                    gpus.append({"name": f"AMD {entry}", "vram_gb": 0.0})
        return gpus
    except OSError:
        return []


def _probe_disk_free_gb(path: str = None) -> float:
    """Free disk in GB at the path where ollama models would live
    (~/.ollama/models by default — fall back to home dir)."""
    target = path or os.path.expanduser("~/.ollama") \
                  or os.path.expanduser("~")
    if not os.path.exists(target):
        target = os.path.expanduser("~")
    try:
        st = os.statvfs(target)
        return round(st.f_bavail * st.f_frsize / (1024 ** 3), 1)
    except OSError:
        return 0.0


def _probe_ollama_installed() -> bool:
    return shutil.which("ollama") is not None


def _probe_llamacpp_installed() -> bool:
    """llama.cpp ships several binaries — `llama-server` is the standard
    one for v0.4+. Older builds use `server`. Either is fine."""
    return any(shutil.which(c) for c in ("llama-server", "llama", "main"))


# ─── Public API ────────────────────────────────────────────────────────────


def probe_system() -> dict:
    """Single-call snapshot of host capabilities. Safe to call anytime —
    every sub-probe is defensive. Returns a flat dict with stable keys
    so consumers (InstallerWizard, recommend_models) can index without
    None-checking."""
    flags = _probe_cpu_flags()
    return {
        "ram_gb": _probe_ram_gb(),
        "cpu_cores": _probe_cpu_cores(),
        "cpu_avx2": flags["avx2"],
        "cpu_avx512": flags["avx512"],
        "gpu_nvidia": _probe_nvidia_gpus(),
        "gpu_amd": _probe_amd_gpus(),
        "disk_free_gb": _probe_disk_free_gb(),
        "ollama_installed": _probe_ollama_installed(),
        "llamacpp_installed": _probe_llamacpp_installed(),
    }


# ─── Recommendation engine ────────────────────────────────────────────────


# Tiered model catalog — keyed by minimum RAM needed for Q4 quant. Each
# entry: (ollama_tag, friendly_name, ram_gb_min, vram_gb_helpful).
# Order matters (smallest → largest); recommend_models() walks from the
# top until it hits a tier the host can't run, returning everything up
# to that point. vram_gb_helpful=0 means CPU-only is fine.
_MODEL_TIERS: tuple[tuple[str, str, float, float], ...] = (
    ("qwen2.5-coder:0.5b", "Qwen2.5-Coder 0.5B (code-tuned tiny, 400MB)", 1.0, 0.0),
    ("tinyllama:1.1b",     "TinyLlama 1.1B (generic chat, 700MB)",       2.0, 0.0),
    ("qwen2.5-coder:1.5b", "Qwen2.5-Coder 1.5B (code, ~1GB)",            2.5, 0.0),
    ("phi3:mini",          "Phi-3-mini 4k (~2.4GB, balanced)",           4.0, 0.0),
    ("qwen2.5-coder:3b",   "Qwen2.5-Coder 3B (code, ~2GB)",              5.0, 0.0),
    ("llama3.2:3b",        "Llama 3.2 3B (general, ~2GB)",               5.0, 0.0),
    ("qwen2.5-coder:7b",   "Qwen2.5-Coder 7B (code, ~4.5GB)",            10.0, 0.0),
    ("llama3.1:8b",        "Llama 3.1 8B (general, ~5GB)",               12.0, 0.0),
    ("qwen2.5-coder:14b",  "Qwen2.5-Coder 14B (code, ~9GB)",             20.0, 12.0),
    ("qwen2.5-coder:32b",  "Qwen2.5-Coder 32B (code, ~20GB)",            40.0, 24.0),
)


def recommend_models(probe: dict) -> list[dict]:
    """Pick models the host can realistically run. Returns a list ordered
    smallest → largest (so callers can prepend "we recommend the smallest
    available" as a sane default).

    Each item: {ollama_tag, friendly_name, ram_gb_min, vram_gb_helpful,
                fits_in_ram: bool, fits_in_vram: bool}.
    `fits_in_vram=True` means the host has a GPU with enough VRAM to
    accelerate this model; CPU-only hosts get fits_in_vram=False but
    still get fits_in_ram=True for reasonable sizes.

    Filters: anything where ram_gb_min > host's ram_gb is dropped
    entirely (won't fit even with swap). Larger sizes that need GPU
    are kept only when host has matching VRAM.
    """
    host_ram = float(probe.get("ram_gb") or 0)
    nvidia = probe.get("gpu_nvidia") or []
    amd = probe.get("gpu_amd") or []
    max_vram = 0.0
    for g in nvidia + amd:
        max_vram = max(max_vram, float(g.get("vram_gb") or 0))

    out = []
    for tag, name, ram_min, vram_helpful in _MODEL_TIERS:
        if ram_min > host_ram:
            continue
        # GPU-only tier (>=14B Q4) requires VRAM, drop on CPU-only hosts.
        if vram_helpful > 0 and max_vram < vram_helpful:
            continue
        out.append({
            "ollama_tag": tag,
            "friendly_name": name,
            "ram_gb_min": ram_min,
            "vram_gb_helpful": vram_helpful,
            "fits_in_ram": ram_min <= host_ram,
            "fits_in_vram": (vram_helpful == 0
                             or max_vram >= vram_helpful),
        })
    return out


__all__ = [
    "probe_system",
    "recommend_models",
    "_MODEL_TIERS",  # exported for tests
]
