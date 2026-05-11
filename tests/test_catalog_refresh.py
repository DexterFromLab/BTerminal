"""Unit tests for BUG#24 — aider model catalog auto-refresh.

Strategy: never hit GitHub. urllib.request.urlopen is mocked to return
synthesized JSON or to raise URLError/HTTPError. CACHE_PATH is redirected
via monkeypatching so test runs don't touch ~/.config/bterminal.
"""
from __future__ import annotations

import io
import json
import os
import time
from pathlib import Path
from unittest.mock import patch, MagicMock

import pytest
import urllib.error

from bterminal.providers import aider_probe


# ─── Fresh-cache TTL probe ────────────────────────────────────────────────


def test_cache_fresh_within_7_days(tmp_path):
    """Pin: 6-day-old cache must still be considered fresh — TTL is 7d
    so this exercises the boundary on the 'still trusted' side."""
    cache = tmp_path / "cat.json"
    cache.write_text("{}")
    six_days = time.time() - 6 * 86400
    os.utime(cache, (six_days, six_days))
    assert aider_probe._cache_is_fresh(str(cache))


def test_cache_stale_after_7_days(tmp_path):
    """Pin: 8-day-old cache is stale → load_catalog must skip it and
    use bundled instead. Without this, removed models stay in the wizard
    forever once the user has any old cache."""
    cache = tmp_path / "cat.json"
    cache.write_text("{}")
    eight_days = time.time() - 8 * 86400
    os.utime(cache, (eight_days, eight_days))
    assert not aider_probe._cache_is_fresh(str(cache))


def test_cache_fresh_returns_false_when_file_missing(tmp_path):
    """Pin: no cache file at all → False, never raise OSError."""
    assert not aider_probe._cache_is_fresh(str(tmp_path / "no-such.json"))


# ─── load_catalog chain ───────────────────────────────────────────────────


def _bundled_models() -> set:
    return {m["tag"] for m in aider_probe.load_catalog().get("models", [])}


def test_load_catalog_uses_fresh_cache_over_bundled(tmp_path, monkeypatch):
    """Pin: when a fresh cache exists, load_catalog must read THAT, not
    the bundled JSON. The cache is what refresh_catalog_background just
    wrote from the upstream raw URL — bundled is the offline fallback."""
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({
        "_meta": {"source": "cache"},
        "models": [{"tag": "fake-cache:1b", "ram_gb": 1, "vram_gb": 0,
                    "download_mb": 100, "label": "X", "description": "Y"}],
    }))
    monkeypatch.setattr(aider_probe, "CACHE_PATH", str(cache))
    catalog = aider_probe.load_catalog()
    tags = {m["tag"] for m in catalog["models"]}
    assert "fake-cache:1b" in tags


def test_load_catalog_falls_back_to_bundled_when_cache_stale(
        tmp_path, monkeypatch):
    """Pin: stale cache is ignored — bundled wins. Otherwise old caches
    pin the wizard to outdated model lists across BT updates."""
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({
        "_meta": {"source": "cache"},
        "models": [{"tag": "stale:1b", "ram_gb": 1, "vram_gb": 0,
                    "download_mb": 100, "label": "X", "description": "Y"}],
    }))
    eight_days = time.time() - 8 * 86400
    os.utime(cache, (eight_days, eight_days))
    monkeypatch.setattr(aider_probe, "CACHE_PATH", str(cache))
    tags = {m["tag"] for m in aider_probe.load_catalog()["models"]}
    assert "stale:1b" not in tags  # bundled used instead
    # And the bundled qwen tier is intact:
    assert "qwen2.5-coder:7b" in tags


def test_load_catalog_falls_back_to_bundled_when_cache_missing(
        tmp_path, monkeypatch):
    """Pin: never-refreshed environment (no cache file at all) — bundled
    is loaded silently. This is the state on every fresh install."""
    monkeypatch.setattr(aider_probe, "CACHE_PATH",
                        str(tmp_path / "absent.json"))
    tags = {m["tag"] for m in aider_probe.load_catalog()["models"]}
    assert "qwen2.5-coder:7b" in tags


def test_load_catalog_explicit_path_bypasses_cache(tmp_path, monkeypatch):
    """Pin: backwards-compat — old `load_catalog(path)` call (used by
    pin tests in BUG#20) skips cache logic entirely. Otherwise the
    test catalog fixture would be silently shadowed by ~/.config cache
    if one exists on the test machine."""
    explicit = tmp_path / "explicit.json"
    explicit.write_text(json.dumps({
        "models": [{"tag": "explicit:1b", "ram_gb": 1, "vram_gb": 0,
                    "download_mb": 100, "label": "X", "description": "Y"}],
    }))
    # Put a fresh cache somewhere to prove it's not used:
    cache = tmp_path / "cache.json"
    cache.write_text(json.dumps({
        "models": [{"tag": "cache:1b", "ram_gb": 1, "vram_gb": 0}],
    }))
    monkeypatch.setattr(aider_probe, "CACHE_PATH", str(cache))
    got = aider_probe.load_catalog(str(explicit))
    tags = {m["tag"] for m in got["models"]}
    assert tags == {"explicit:1b"}


# ─── fetch_remote_catalog ────────────────────────────────────────────────


def _mock_urlopen_returning(body: bytes):
    """Helper: build a mock urlopen that returns `body` as the response."""
    fake_resp = MagicMock()
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: False
    fake_resp.read.return_value = body
    return MagicMock(return_value=fake_resp)


def test_fetch_remote_catalog_happy_path():
    """Pin: 200 OK with valid catalog JSON → fetch returns the parsed
    dict. This is the only path that produces a cache write."""
    payload = {"_meta": {"source": "remote"},
               "models": [{"tag": "remote:1b"}]}
    body = json.dumps(payload).encode("utf-8")
    with patch("urllib.request.urlopen", _mock_urlopen_returning(body)):
        got = aider_probe.fetch_remote_catalog()
    assert got == payload


def test_fetch_remote_catalog_url_error_returns_none():
    """Pin: offline / DNS fail / GitHub down → None (NOT raise). The
    refresh thread runs at BT startup; an exception here would log to
    stderr on every launch with no internet — unacceptable noise."""
    with patch("urllib.request.urlopen",
               side_effect=urllib.error.URLError("offline")):
        assert aider_probe.fetch_remote_catalog() is None


def test_fetch_remote_catalog_invalid_json_returns_none():
    """Pin: GitHub serving an HTML 404 (or any non-JSON) → None. Without
    this guard a redirect page would get cached as 'the catalog'."""
    with patch("urllib.request.urlopen",
               _mock_urlopen_returning(b"<html>404 not found</html>")):
        assert aider_probe.fetch_remote_catalog() is None


def test_fetch_remote_catalog_missing_models_key_returns_none():
    """Pin: validate the response shape. JSON like {"hello":1} is parseable
    but useless to the wizard — must be rejected rather than cached."""
    with patch("urllib.request.urlopen",
               _mock_urlopen_returning(b'{"hello":1}')):
        assert aider_probe.fetch_remote_catalog() is None


# ─── refresh_catalog_background (end-to-end) ─────────────────────────────


def test_refresh_writes_cache_on_success(tmp_path, monkeypatch):
    """Pin: happy path through the background refresh — thread starts,
    HTTP returns valid JSON, _write_cache atomically replaces the
    cache file. Done callback fires with success=True."""
    monkeypatch.setattr(aider_probe, "CACHE_PATH",
                        str(tmp_path / "cat.json"))
    payload = {"_meta": {"source": "remote"},
               "models": [{"tag": "freshly:1b"}]}
    body = json.dumps(payload).encode("utf-8")
    flag = {"ok": None}

    def _done(ok):
        flag["ok"] = ok

    with patch("urllib.request.urlopen", _mock_urlopen_returning(body)):
        t = aider_probe.refresh_catalog_background(on_done=_done)
        t.join(timeout=5)
    assert flag["ok"] is True
    cached = aider_probe.load_catalog(str(tmp_path / "cat.json"))
    assert cached["_meta"]["source"] == "remote"
    assert cached["models"][0]["tag"] == "freshly:1b"


def test_refresh_does_not_write_cache_on_network_failure(
        tmp_path, monkeypatch):
    """Pin: URLError → cache file is NOT touched (no truncation, no
    write of half-baked JSON). Existing cache survives. Callback
    receives success=False so callers can log / surface a banner."""
    cache_path = tmp_path / "cat.json"
    cache_path.write_text(json.dumps({"models": [{"tag": "old:1b"}]}))
    pre_mtime = cache_path.stat().st_mtime
    monkeypatch.setattr(aider_probe, "CACHE_PATH", str(cache_path))

    flag = {"ok": None}

    def _done(ok):
        flag["ok"] = ok

    with patch("urllib.request.urlopen",
               side_effect=urllib.error.URLError("nope")):
        t = aider_probe.refresh_catalog_background(on_done=_done)
        t.join(timeout=5)
    assert flag["ok"] is False
    # Old cache intact:
    assert cache_path.stat().st_mtime == pre_mtime
    survived = json.loads(cache_path.read_text())
    assert survived["models"][0]["tag"] == "old:1b"


def test_refresh_thread_is_daemon():
    """Pin: must NOT keep the GTK main loop alive after BT close. If
    refresh hangs on a slow GitHub request, daemon=True ensures the
    process can still exit cleanly when the user quits."""
    with patch("urllib.request.urlopen",
               side_effect=urllib.error.URLError("x")):
        t = aider_probe.refresh_catalog_background()
        t.join(timeout=5)
    assert t.daemon is True
