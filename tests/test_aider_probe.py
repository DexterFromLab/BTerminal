"""Unit tests for bterminal.providers.aider_probe (BUG#20 foundation).

These never touch real hardware: psutil + nvidia-smi + ollama are all
mocked. The probe module is the substrate the aider setup wizard will
trust to render the semaforowa table — wrong scoring here would mean
the wizard recommends a model that swaps the user to death.
"""
from __future__ import annotations

import json
import subprocess
from pathlib import Path
from unittest.mock import patch, MagicMock, mock_open

import pytest

from bterminal.providers import aider_probe


# ─── Bundled catalog sanity ───────────────────────────────────────────────


def test_bundled_catalog_loads_and_has_expected_shape():
    """Pin: aider_models.json must stay parseable as the wizard depends
    on it. Every entry needs the keys the wizard renders. Schema v2
    (BUG#25): `description` was replaced with `url` so the wizard
    never displays stale free-form copy — only a link to the canonical
    Ollama Library page."""
    data = aider_probe.load_catalog()
    assert isinstance(data, dict)
    assert "models" in data and isinstance(data["models"], list)
    assert len(data["models"]) >= 5, "wizard's status table needs ≥5 rows"
    required = {"tag", "label", "url", "ram_gb",
                "vram_gb", "download_mb"}
    for m in data["models"]:
        missing = required - set(m.keys())
        assert not missing, f"model {m.get('tag')} is missing {missing}"
        # Schema v2 guard — `description` must NOT be present (drifts).
        assert "description" not in m, (
            f"{m['tag']} carries a free-form 'description' field — "
            f"schema v2 forbids this (BUG#25). Move the copy to the "
            f"Ollama Library page and link via 'url'."
        )
        # URL sanity — must point at ollama.com/library and reference
        # the tag's base name (everything before the colon).
        assert m["url"].startswith("https://ollama.com/library/"), (
            f"{m['tag']} has non-canonical url {m['url']!r}"
        )


def test_bundled_catalog_contains_recommended_qwen_7b():
    """Pin: the 7b recommended tier is the wizard's default for 16 GB hosts.
    If it's renamed or removed the recommendation falls back to 3b silently
    — caught here instead of in the wizard."""
    data = aider_probe.load_catalog()
    tags = [m["tag"] for m in data["models"]]
    assert "qwen2.5-coder:7b" in tags


def test_load_catalog_returns_empty_dict_on_bad_path(tmp_path):
    """Pin: missing/corrupt catalog must NOT raise — wizard degrades to
    'free-text model entry' rather than crashing."""
    assert aider_probe.load_catalog(str(tmp_path / "nonexistent.json")) == {}
    bad = tmp_path / "bad.json"
    bad.write_text("{not valid json")
    assert aider_probe.load_catalog(str(bad)) == {}


# ─── Hardware probes ──────────────────────────────────────────────────────


def test_detect_ram_gb_via_psutil():
    """Pin: psutil path returns rounded GB. 32 GB host should not report
    32.000123 or 31.9 — wizard's RAM banner needs a clean number."""
    fake_mem = MagicMock()
    fake_mem.total = 32 * (1024 ** 3)  # 32 GB
    with patch("psutil.virtual_memory", return_value=fake_mem):
        assert aider_probe.detect_ram_gb() == 32.0


def test_detect_ram_gb_falls_back_to_proc_meminfo():
    """Pin: ImportError on psutil → /proc/meminfo parse. Guards against
    minimal Docker containers where psutil might not be installed."""
    meminfo_contents = "MemTotal:       16777216 kB\nSwapTotal: 0 kB\n"

    real_import = __import__

    def _fake_import(name, *args, **kwargs):
        if name == "psutil":
            raise ImportError("no psutil here")
        return real_import(name, *args, **kwargs)

    with patch("builtins.__import__", side_effect=_fake_import), \
         patch("builtins.open", mock_open(read_data=meminfo_contents)):
        got = aider_probe.detect_ram_gb()
    # 16777216 kB = 16 GB
    assert got == 16.0


def test_detect_vram_gb_no_nvidia_returns_zero():
    """Pin: no nvidia-smi on PATH → 0.0, never raise. Most user laptops
    fall in this bucket."""
    with patch("shutil.which", return_value=None):
        assert aider_probe.detect_vram_gb() == 0.0


def test_detect_vram_gb_picks_largest_card():
    """Pin: multi-GPU host returns the *largest* card. Ollama loads one
    model on one device; pretending we have 16 GB when each card is 8
    would mislead the recommender."""
    fake_out = "8192\n24576\n8192\n"  # 8 GB, 24 GB, 8 GB
    with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=fake_out, stderr="")
        assert aider_probe.detect_vram_gb() == 24.0


def test_detect_vram_gb_nvidia_smi_errors_return_zero():
    """Pin: driver-loaded-but-broken state (nvidia-smi exits 1) → 0.
    Otherwise a stale install would inject garbage into the wizard."""
    with patch("shutil.which", return_value="/usr/bin/nvidia-smi"), \
         patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=1, stdout="", stderr="N/A")
        assert aider_probe.detect_vram_gb() == 0.0


def test_is_model_installed_strips_litellm_prefix():
    """Pin: aider passes tags as `openai/qwen2.5-coder:0.5b` (litellm
    routing convention). Ollama lists them as bare `qwen2.5-coder:0.5b`.
    Without stripping, every installed model would appear missing."""
    listing = (
        "NAME                  ID            SIZE     MODIFIED\n"
        "qwen2.5-coder:0.5b    abc12345      400 MB   1 hour ago\n"
    )
    with patch("subprocess.run") as mock_run:
        mock_run.return_value = subprocess.CompletedProcess(
            args=[], returncode=0, stdout=listing, stderr="")
        assert aider_probe.is_model_installed("openai/qwen2.5-coder:0.5b")
        assert aider_probe.is_model_installed("qwen2.5-coder:0.5b")
        assert not aider_probe.is_model_installed("openai/qwen2.5-coder:7b")


def test_is_model_installed_returns_false_when_ollama_missing():
    """Pin: FileNotFoundError → False, never propagate. Fresh hosts
    without Ollama are exactly the audience for the install prompt."""
    with patch("subprocess.run", side_effect=FileNotFoundError):
        assert aider_probe.is_model_installed("openai/anything") is False


# ─── Recommendation logic ─────────────────────────────────────────────────


@pytest.fixture
def fake_catalog():
    """Compact catalog covering the relevant size bands."""
    return {
        "models": [
            {"tag": "tiny",   "ram_gb": 1.0,  "vram_gb": 0.0},
            {"tag": "small",  "ram_gb": 2.5,  "vram_gb": 0.0},
            {"tag": "mid",    "ram_gb": 5.0,  "vram_gb": 0.0},
            {"tag": "large",  "ram_gb": 10.0, "vram_gb": 0.0},
            {"tag": "xl",     "ram_gb": 20.0, "vram_gb": 12.0},
            {"tag": "xxl",    "ram_gb": 40.0, "vram_gb": 24.0},
        ]
    }


def test_recommend_model_8gb_host_picks_mid(fake_catalog):
    """Pin: 8 GB host has 5.6 GB budget (70%). 'large' (10 GB) too big,
    'mid' (5 GB) is the biggest that fits."""
    got = aider_probe.recommend_model(fake_catalog, ram_gb=8.0)
    assert got["tag"] == "mid"


def test_recommend_model_16gb_host_picks_large(fake_catalog):
    """Pin: 16 GB host with 11.2 GB budget. 'large' (10 GB) fits, 'xl'
    (20 GB) doesn't — recommended for the user's host (the one in BUG#19
    reproduction was 16 GB)."""
    got = aider_probe.recommend_model(fake_catalog, ram_gb=16.0)
    assert got["tag"] == "large"


def test_recommend_model_32gb_host_picks_xl(fake_catalog):
    """Pin: 32 GB host budget = 22.4 GB. 'xl' (20 GB) fits, 'xxl' (40)
    does not. Conservative — leaves browser/IDE headroom."""
    got = aider_probe.recommend_model(fake_catalog, ram_gb=32.0)
    assert got["tag"] == "xl"


def test_recommend_model_steps_down_when_vram_smaller_than_ram(fake_catalog):
    """Pin: 32 GB RAM + 8 GB GPU → CPU-fit says 'xl' but GPU can't load
    it. Spec requires stepping down one tier when VRAM < RAM."""
    got = aider_probe.recommend_model(fake_catalog, ram_gb=32.0, vram_gb=8.0)
    assert got["tag"] == "large"  # one tier below xl


def test_recommend_model_no_stepdown_when_vram_equals_or_exceeds_ram(
        fake_catalog):
    """Pin: workstation with 32 GB RAM + 32 GB VRAM (A6000) → no stepdown,
    GPU large enough for the natural pick."""
    got = aider_probe.recommend_model(fake_catalog, ram_gb=32.0, vram_gb=32.0)
    assert got["tag"] == "xl"


def test_recommend_model_returns_none_when_nothing_fits(fake_catalog):
    """Pin: 1 GB host (a Raspberry Pi class) — even 'tiny' needs 1 GB,
    and 70% of 1 GB is 0.7 — nothing fits. Return None so the wizard can
    show 'Twój sprzęt nie obsłuży żadnego modelu' instead of crashing."""
    got = aider_probe.recommend_model(fake_catalog, ram_gb=1.0)
    assert got is None


def test_recommend_model_empty_catalog_returns_none():
    """Pin: empty / malformed catalog → None. Used as the wizard's
    fall-through path when network fetch fails AND bundled is corrupted."""
    assert aider_probe.recommend_model({}, ram_gb=16.0) is None
    assert aider_probe.recommend_model({"models": []}, ram_gb=16.0) is None


# ─── Score (semafor) ──────────────────────────────────────────────────────


def test_score_model_three_buckets(fake_catalog):
    """Pin: wizard semafor depends on this mapping. 'large' (10 GB):
      - on 8 GB host  → too_big (10 > 8)
      - on 16 GB host → tight (10 > 11.2-budget? 10 ≤ 11.2 → fits)
      - on 14 GB host → tight (10 > 9.8-budget, 10 ≤ 14)
    """
    large = next(m for m in fake_catalog["models"] if m["tag"] == "large")
    assert aider_probe.score_model(large, ram_gb=8.0) == "too_big"
    assert aider_probe.score_model(large, ram_gb=16.0) == "fits"
    assert aider_probe.score_model(large, ram_gb=14.0) == "tight"
