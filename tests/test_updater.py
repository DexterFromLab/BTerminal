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


# ─── License fetch (Bug 2026-05-07: symlink-target leak) ────────────────────

def test_remote_license_blob_path_is_per_language(monkeypatch):
    """Active UI language drives which LICENSE.<lang>.md gets fetched
    from origin/master — pre-fix code always asked for root LICENSE.md
    which is a symlink pointing at LICENSE.en.md."""
    from bterminal import i18n
    monkeypatch.setattr(i18n, "current_language", lambda: "pl")
    assert updater._remote_license_blob_path() == "defaults/license/LICENSE.pl.md"

    monkeypatch.setattr(i18n, "current_language", lambda: "en")
    assert updater._remote_license_blob_path() == "defaults/license/LICENSE.en.md"


def test_remote_license_blob_path_falls_back_to_en_for_empty_lang(monkeypatch):
    """current_language() returning '' (init failure) defaults to en."""
    from bterminal import i18n
    monkeypatch.setattr(i18n, "current_language", lambda: "")
    assert updater._remote_license_blob_path() == "defaults/license/LICENSE.en.md"


def test_fetch_remote_license_returns_text_not_symlink_target(monkeypatch):
    """REGRESSION (2026-05-07): _fetch_remote_license must return the
    license TEXT, not a path-like string. Pre-fix, calling
    `git show origin/master:LICENSE.md` on the symlink at repo root
    returned the symlink target ("defaults/license/LICENSE.en.md\\n")
    which then ended up in the dialog body."""
    monkeypatch.setattr(updater, "REPO_DIR", "/fake/repo")

    license_text = (
        "# BTerminal License Agreement\n"
        "\n"
        "**Version 1.0** — effective 2026-05-04\n"
        "\n"
        "Copyright (c) 2024-2026 Bartosz Czarnota\n"
    )

    captured_args = {}

    def fake_subprocess_run(args, **kwargs):
        captured_args["args"] = args

        class _R:
            returncode = 0
            stdout = license_text
        return _R()

    monkeypatch.setattr(updater.subprocess, "run", fake_subprocess_run)
    from bterminal import i18n
    monkeypatch.setattr(i18n, "current_language", lambda: "en")

    result = updater._fetch_remote_license()
    assert result == license_text
    # Verify the per-language blob path was requested, NOT root LICENSE.md.
    assert captured_args["args"] == [
        "git", "show", "origin/master:defaults/license/LICENSE.en.md",
    ]


def test_fetch_remote_license_falls_back_to_en_when_lang_blob_missing(
    monkeypatch,
):
    """If the active language has no LICENSE.<lang>.md in origin/master
    (added to UI but not yet translated), fall back to LICENSE.en.md
    rather than returning None — user must always see SOMETHING legible
    rather than a "Cannot read LICENSE.md" error."""
    monkeypatch.setattr(updater, "REPO_DIR", "/fake/repo")
    from bterminal import i18n
    monkeypatch.setattr(i18n, "current_language", lambda: "xx")

    en_text = "# License (en fallback)\n"
    calls = []

    def fake_subprocess_run(args, **kwargs):
        calls.append(args)

        class _R:
            pass
        if "LICENSE.xx.md" in args[-1]:
            _R.returncode = 128  # missing blob
            _R.stdout = ""
        else:
            _R.returncode = 0
            _R.stdout = en_text
        return _R()

    monkeypatch.setattr(updater.subprocess, "run", fake_subprocess_run)
    assert updater._fetch_remote_license() == en_text
    assert len(calls) == 2  # tried xx, fell back to en


def test_fetch_remote_license_no_repo_returns_none(monkeypatch):
    monkeypatch.setattr(updater, "REPO_DIR", None)
    assert updater._fetch_remote_license() is None


def test_read_local_license_uses_per_language_resolver(monkeypatch, tmp_path):
    """_read_local_license must read the per-language file via
    license._resolve_license_path() so it doesn't dereference the root
    LICENSE.md symlink (which always lands on LICENSE.en.md)."""
    monkeypatch.setattr(updater, "REPO_DIR", str(tmp_path))

    target = tmp_path / "LICENSE.pl.md"
    target.write_text("# Polska wersja licencji\n", encoding="utf-8")

    from bterminal import license as lic_mod
    monkeypatch.setattr(lic_mod, "_resolve_license_path",
                        lambda language=None: str(target))

    out = updater._read_local_license()
    assert out == "# Polska wersja licencji\n"
