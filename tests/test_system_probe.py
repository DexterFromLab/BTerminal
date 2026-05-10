"""Tests for bterminal.system_probe (task #1 / #73 in audit doc).

Pure-helper coverage:
  - probe_system() returns a stable dict shape regardless of host
  - sub-probes don't raise on missing tools
  - recommend_models() heuristic picks correct tiers per scenario
"""
from __future__ import annotations

import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bterminal import system_probe as sp


# ─── probe_system shape ─────────────────────────────────────────────────────


def test_probe_system_returns_all_expected_keys():
    """Every key the InstallerWizard / recommend_models reaches for
    must always be present."""
    out = sp.probe_system()
    for key in (
        "ram_gb", "cpu_cores", "cpu_avx2", "cpu_avx512",
        "gpu_nvidia", "gpu_amd", "disk_free_gb",
        "ollama_installed", "llamacpp_installed",
    ):
        assert key in out, f"probe_system missing key {key!r}"


def test_probe_system_types_stable():
    """Types must be predictable so consumers can index without isinstance
    checks (e.g. probe['gpu_nvidia'][0]['vram_gb'])."""
    out = sp.probe_system()
    assert isinstance(out["ram_gb"], float)
    assert isinstance(out["cpu_cores"], int)
    assert isinstance(out["cpu_avx2"], bool)
    assert isinstance(out["cpu_avx512"], bool)
    assert isinstance(out["gpu_nvidia"], list)
    assert isinstance(out["gpu_amd"], list)
    assert isinstance(out["disk_free_gb"], float)
    assert isinstance(out["ollama_installed"], bool)
    assert isinstance(out["llamacpp_installed"], bool)


def test_probe_system_does_not_raise_on_missing_tools(monkeypatch):
    """Force every external tool to look missing and verify still no
    crash + reasonable defaults."""
    monkeypatch.setattr(sp.shutil, "which", lambda _: None)

    def boom(*a, **kw):
        raise OSError("simulated missing")
    monkeypatch.setattr(sp.subprocess, "run", boom)

    out = sp.probe_system()
    assert out["gpu_nvidia"] == []
    assert out["gpu_amd"] == []
    assert out["ollama_installed"] is False
    assert out["llamacpp_installed"] is False


# ─── nvidia-smi probe ───────────────────────────────────────────────────────


def test_nvidia_probe_parses_csv_output(monkeypatch):
    """nvidia-smi --query-gpu output: 'NVIDIA GeForce RTX 3060, 12288'"""
    monkeypatch.setattr(sp.shutil, "which",
                        lambda c: "/usr/bin/nvidia-smi" if c == "nvidia-smi" else None)
    fake_result = MagicMock(returncode=0,
                            stdout="NVIDIA GeForce RTX 3060, 12288\n"
                                   "NVIDIA RTX A4000, 16384\n")
    monkeypatch.setattr(sp.subprocess, "run",
                        lambda *a, **kw: fake_result)

    gpus = sp._probe_nvidia_gpus()
    assert len(gpus) == 2
    assert gpus[0] == {"name": "NVIDIA GeForce RTX 3060", "vram_gb": 12.0}
    assert gpus[1] == {"name": "NVIDIA RTX A4000", "vram_gb": 16.0}


def test_nvidia_probe_returns_empty_when_smi_missing(monkeypatch):
    monkeypatch.setattr(sp.shutil, "which", lambda _: None)
    assert sp._probe_nvidia_gpus() == []


def test_nvidia_probe_handles_subprocess_failure(monkeypatch):
    monkeypatch.setattr(sp.shutil, "which",
                        lambda c: "/usr/bin/nvidia-smi" if c == "nvidia-smi" else None)
    fake_result = MagicMock(returncode=1, stdout="")
    monkeypatch.setattr(sp.subprocess, "run",
                        lambda *a, **kw: fake_result)
    assert sp._probe_nvidia_gpus() == []


def test_nvidia_probe_handles_timeout(monkeypatch):
    monkeypatch.setattr(sp.shutil, "which",
                        lambda c: "/usr/bin/nvidia-smi" if c == "nvidia-smi" else None)

    def raise_timeout(*a, **kw):
        raise sp.subprocess.TimeoutExpired(cmd="nvidia-smi", timeout=3)
    monkeypatch.setattr(sp.subprocess, "run", raise_timeout)
    assert sp._probe_nvidia_gpus() == []


# ─── disk probe ─────────────────────────────────────────────────────────────


def test_disk_free_returns_float():
    out = sp._probe_disk_free_gb()
    assert isinstance(out, float)
    assert out >= 0


def test_disk_free_falls_back_when_target_missing(tmp_path):
    out = sp._probe_disk_free_gb(str(tmp_path / "nonexistent"))
    # Falls back to $HOME, should still return a real number.
    assert isinstance(out, float)
    assert out >= 0


# ─── recommend_models heuristic ─────────────────────────────────────────────


def test_recommend_models_low_ram_only_returns_smallest():
    """1GB RAM host → only the 0.5B tier fits."""
    probe = {"ram_gb": 1.0, "gpu_nvidia": [], "gpu_amd": []}
    out = sp.recommend_models(probe)
    tags = [m["ollama_tag"] for m in out]
    assert tags == ["qwen2.5-coder:0.5b"]


def test_recommend_models_medium_ram_returns_up_to_3b():
    """5GB RAM CPU-only → up to 3B tier."""
    probe = {"ram_gb": 5.0, "gpu_nvidia": [], "gpu_amd": []}
    out = sp.recommend_models(probe)
    tags = [m["ollama_tag"] for m in out]
    assert "qwen2.5-coder:0.5b" in tags
    assert "qwen2.5-coder:3b" in tags
    # No 7B (needs 10GB), no GPU-only tiers
    assert "qwen2.5-coder:7b" not in tags
    assert "qwen2.5-coder:14b" not in tags


def test_recommend_models_high_ram_no_gpu_caps_at_8b():
    """16GB CPU-only — 7B/8B fit but 14B+ require VRAM and are dropped."""
    probe = {"ram_gb": 16.0, "gpu_nvidia": [], "gpu_amd": []}
    out = sp.recommend_models(probe)
    tags = [m["ollama_tag"] for m in out]
    assert "llama3.1:8b" in tags
    assert "qwen2.5-coder:7b" in tags
    assert "qwen2.5-coder:14b" not in tags  # needs 12GB VRAM
    assert "qwen2.5-coder:32b" not in tags


def test_recommend_models_high_ram_with_gpu_includes_14b():
    """24GB RAM + 16GB VRAM — 14B fits both ways."""
    probe = {
        "ram_gb": 24.0,
        "gpu_nvidia": [{"name": "RTX A4000", "vram_gb": 16.0}],
        "gpu_amd": [],
    }
    out = sp.recommend_models(probe)
    tags = [m["ollama_tag"] for m in out]
    assert "qwen2.5-coder:14b" in tags
    assert "qwen2.5-coder:32b" not in tags  # needs 24GB VRAM


def test_recommend_models_workstation_includes_32b():
    """64GB RAM + RTX A6000 (48GB) — top tier fits."""
    probe = {
        "ram_gb": 64.0,
        "gpu_nvidia": [{"name": "RTX A6000", "vram_gb": 48.0}],
        "gpu_amd": [],
    }
    out = sp.recommend_models(probe)
    tags = [m["ollama_tag"] for m in out]
    assert "qwen2.5-coder:32b" in tags


def test_recommend_models_handles_missing_keys():
    """Defensive: empty / partial probe dict shouldn't crash."""
    assert sp.recommend_models({}) == []
    assert sp.recommend_models({"ram_gb": 0}) == []


def test_recommend_models_returns_dict_with_documented_fields():
    """Each entry must have the contract fields used by InstallerWizard
    page 3 + OptionsDialog #79."""
    probe = {"ram_gb": 8.0, "gpu_nvidia": [], "gpu_amd": []}
    out = sp.recommend_models(probe)
    assert out, "8GB RAM should fit at least 0.5B/1B/3B"
    for entry in out:
        assert set(entry.keys()) >= {
            "ollama_tag", "friendly_name", "ram_gb_min",
            "vram_gb_helpful", "fits_in_ram", "fits_in_vram",
        }


def test_recommend_models_amd_gpu_also_counts_for_vram():
    """ROCm-equipped AMD card should also unlock GPU-helpful tiers
    when rocm-smi reported a real vram_gb (not 0.0 fallback). 14B
    needs 12GB VRAM + 20GB RAM, both satisfied here. 32B is gated
    by the RAM tier (40GB) too, not just VRAM — see _MODEL_TIERS."""
    probe = {
        "ram_gb": 32.0,
        "gpu_nvidia": [],
        "gpu_amd": [{"name": "AMD RX 7900 XTX", "vram_gb": 24.0}],
    }
    out = sp.recommend_models(probe)
    tags = [m["ollama_tag"] for m in out]
    assert "qwen2.5-coder:14b" in tags  # 12GB VRAM + 20GB RAM ok
    # 32GB RAM < 40GB tier requirement for 32B → dropped
    assert "qwen2.5-coder:32b" not in tags


# ─── CPU flags probe ────────────────────────────────────────────────────────


def test_cpu_flags_returns_dict_with_keys():
    flags = sp._probe_cpu_flags()
    assert "avx2" in flags
    assert "avx512" in flags
    assert isinstance(flags["avx2"], bool)
    assert isinstance(flags["avx512"], bool)


def test_cpu_flags_defensive_on_missing_proc(monkeypatch):
    """Non-Linux: /proc/cpuinfo missing → both flags False, no crash."""
    real_open = open

    def fake_open(path, *a, **kw):
        if path == "/proc/cpuinfo":
            raise OSError("simulated non-Linux")
        return real_open(path, *a, **kw)
    monkeypatch.setattr("builtins.open", fake_open)

    flags = sp._probe_cpu_flags()
    assert flags == {"avx2": False, "avx512": False}
