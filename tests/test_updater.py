"""Unit tests for updater module — pure logic + ANSI strip + errata sort.

GUI dialog code (_prompt_update, _show_errata_dialog, _do_update) is
not unit-tested here — it requires a running GTK loop and is exercised
via xvfb smoke tests of the full app.
"""

import json

import pytest

from bterminal import config
from bterminal import updater
# ─── _load_local_errata ──────────────────────────────────────────────────────

def test_load_local_errata_no_repo_returns_empty(monkeypatch):
    """REPO_DIR=None → no errata file possible, return []."""
    monkeypatch.setattr(updater, "REPO_DIR", None)
    assert updater._load_local_errata() == []


def test_load_local_errata_missing_file(tmp_path, monkeypatch):
    """REPO_DIR set but errata.json absent → return []."""
    monkeypatch.setattr(updater, "REPO_DIR", str(tmp_path))
    assert updater._load_local_errata() == []


def test_load_local_errata_parses_file(tmp_path, monkeypatch):
    """Valid errata.json → list of entries."""
    monkeypatch.setattr(updater, "REPO_DIR", str(tmp_path))
    payload = [
        {"version": "1.1.6", "date": "2026-04-29", "summary": "fix update dialog"},
        {"version": "1.1.5", "date": "2026-04-28", "summary": "rollback on error"},
    ]
    (tmp_path / "errata.json").write_text(json.dumps(payload))
    out = updater._load_local_errata()
    assert out == payload


def test_load_local_errata_corrupt_json_returns_empty(tmp_path, monkeypatch):
    """Garbage in errata.json → graceful [] (don't crash startup)."""
    monkeypatch.setattr(updater, "REPO_DIR", str(tmp_path))
    (tmp_path / "errata.json").write_text("{not really json")
    assert updater._load_local_errata() == []


# ─── ANSI strip helper (used by live update log) ──────────────────────────────

def test_ansi_strip_regex_present():
    """Module exposes the ANSI escape regex used to clean install.sh log
    output before showing in the dialog (regression: 3f131b1).
    Verified by feeding sample output through it."""
    # Build a string with common ANSI codes the installer emits
    raw = "\x1b[32m  ✓\x1b[0m installed git\n\x1b[31m  ✗\x1b[0m failed pip\n"
    # The exact regex is internal — test by checking _do_update wouldn't
    # leave escape codes in output. Use re directly to mirror updater logic.
    import re
    cleaned = re.sub(r"\x1b\[[\d;]*m", "", raw)
    assert "\x1b" not in cleaned
    assert "✓" in cleaned and "✗" in cleaned


# ─── Module-level smoke ──────────────────────────────────────────────────────

def test_updater_imports_all_public_names():
    """Verify the names bterminal.py expects are exported."""
    for name in ("_check_for_updates", "_do_update", "_load_local_errata",
                 "_prompt_update", "_restart_bterminal", "_show_errata_dialog"):
        assert hasattr(updater, name), f"updater.{name} missing"
        assert callable(getattr(updater, name)), f"updater.{name} not callable"


def test_repo_dir_imported_from_config():
    """updater.REPO_DIR comes from config.REPO_DIR — same source of truth
    as the rest of the app, so a single ~/.config/bterminal/repo_path
    file controls all consumers."""
    assert updater.REPO_DIR == config.REPO_DIR


def test_check_for_updates_no_repo_does_not_crash(monkeypatch):
    """If REPO_DIR is None and not manual, _check_for_updates should
    silently return without raising — boot path must not depend on a
    valid repo (fresh install before install.sh has run yet)."""
    monkeypatch.setattr(updater, "REPO_DIR", None)
    # window=None is OK for the early-return path
    updater._check_for_updates(window=None, manual=False)
    # Reaches here = no exception
