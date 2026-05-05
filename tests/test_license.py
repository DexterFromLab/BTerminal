"""Unit tests for the license acceptance subsystem (no GTK dialog).

The dialog itself needs xvfb + a real Gtk loop and is exercised by
the e2e/component layer; here we only cover the persistence + hash
logic that decides *whether* to prompt.
"""

import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import gi
gi.require_version("Gtk", "3.0")  # noqa: E402

from bterminal import config as cfg  # noqa: E402
from bterminal import license as lic  # noqa: E402


# ─── Fixtures ────────────────────────────────────────────────────────────────


def _isolated_options(tmp_path, monkeypatch):
    """Point _OPTIONS / OPTIONS_FILE at a temp file and return a clean dict."""
    opts_file = tmp_path / "options.json"
    monkeypatch.setattr(cfg, "OPTIONS_FILE", str(opts_file))
    monkeypatch.setattr(cfg, "CONFIG_DIR", str(tmp_path))
    cfg._OPTIONS.clear()
    cfg._OPTIONS.update({"theme": "dark"})  # minimal
    monkeypatch.setattr(lic, "_OPTIONS", cfg._OPTIONS)
    return cfg._OPTIONS, opts_file


# ─── _hash_text ──────────────────────────────────────────────────────────────


def test_hash_text_is_deterministic():
    h1 = lic._hash_text("hello")
    h2 = lic._hash_text("hello")
    assert h1 == h2
    assert len(h1) == 64  # sha256 hex digest


def test_hash_text_differs_on_whitespace():
    """Trailing newline / spaces produce a different hash — important
    because `git show` and on-disk file may diverge by a trailing
    newline. We accept that as a valid mismatch."""
    assert lic._hash_text("hello") != lic._hash_text("hello\n")


# ─── _is_accepted_for ────────────────────────────────────────────────────────


def test_not_accepted_when_options_empty(tmp_path, monkeypatch):
    _isolated_options(tmp_path, monkeypatch)
    assert lic._is_accepted_for("any text") is False


def test_not_accepted_when_hash_mismatches(tmp_path, monkeypatch):
    opts, _ = _isolated_options(tmp_path, monkeypatch)
    opts["license_accepted_hash"] = lic._hash_text("OLD LICENSE")
    assert lic._is_accepted_for("NEW LICENSE") is False


def test_accepted_when_hash_matches(tmp_path, monkeypatch):
    opts, _ = _isolated_options(tmp_path, monkeypatch)
    text = "LICENSE TEXT v1.0"
    opts["license_accepted_hash"] = lic._hash_text(text)
    assert lic._is_accepted_for(text) is True


# ─── _record_acceptance ──────────────────────────────────────────────────────


def test_record_acceptance_persists_hash_and_timestamp(tmp_path, monkeypatch):
    opts, opts_file = _isolated_options(tmp_path, monkeypatch)
    text = "LICENSE TEXT"
    lic._record_acceptance(text)

    # Persisted to disk
    on_disk = json.loads(opts_file.read_text())
    assert on_disk["license_accepted_hash"] == lic._hash_text(text)
    assert "license_accepted_at" in on_disk
    # ISO-8601 -> at least YYYY-MM-DDTHH:MM:SS
    assert len(on_disk["license_accepted_at"]) >= 19

    # And in the runtime dict
    assert opts["license_accepted_hash"] == lic._hash_text(text)


# ─── _read_license_text ──────────────────────────────────────────────────────


def test_read_license_returns_none_for_missing_path(tmp_path, monkeypatch):
    """Empty license dir -> resolver finds neither <lang>.md nor en.md
    fallback -> _read_license_text returns None."""
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    assert lic._read_license_text() is None


def test_read_license_returns_text_for_existing_path(tmp_path, monkeypatch):
    en = tmp_path / "LICENSE.en.md"
    en.write_text("BTerminal License v1\nCopyright Bartosz Czarnota\n")
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr("bterminal.license.current_language", lambda: "en")
    text = lic._read_license_text()
    assert text is not None
    assert "Bartosz Czarnota" in text


def test_real_license_md_exists_and_has_required_attribution():
    """The shipped LICENSE.en.md must mention author and email — an
    essential part of the license itself per spec ('user musi
    powielić informacje o autorstwie'). Repo root LICENSE.md is a
    symlink to defaults/license/LICENSE.en.md (for GitHub display)."""
    repo_root = Path(__file__).resolve().parent.parent
    en = repo_root / "defaults" / "license" / "LICENSE.en.md"
    assert en.exists(), f"{en} is missing from the repo"
    text = en.read_text(encoding="utf-8")
    assert "Bartosz Czarnota" in text
    assert "bartoszczarnota1@gmail.com" in text
    # repo root LICENSE.md must exist (symlink) for GitHub to detect it
    root_link = repo_root / "LICENSE.md"
    assert root_link.exists(), "repo root LICENSE.md (symlink) missing"


# ─── _require_license_acceptance: fail-open when LICENSE missing ─────────────


def test_require_license_fail_open_when_unreadable(tmp_path, monkeypatch, capsys):
    """If no LICENSE.<lang>.md and no LICENSE.en.md fallback, fail open
    with stderr warning. We don't want a misconfigured install to lock
    the user out completely."""
    _isolated_options(tmp_path, monkeypatch)
    empty = tmp_path / "no_licenses_here"
    empty.mkdir()
    monkeypatch.setattr(lic, "LICENSE_DIR", empty)
    assert lic._require_license_acceptance(window=None) is True
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "LICENSE" in captured.err


def test_require_license_short_circuits_when_already_accepted(
    tmp_path, monkeypatch
):
    """If the stored hash matches the resolved LICENSE file, the function
    returns True without ever calling the dialog (we'd see a Gtk error
    in headless)."""
    opts, _ = _isolated_options(tmp_path, monkeypatch)
    en = tmp_path / "LICENSE.en.md"
    en.write_text("LICENSE v1\n")
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr("bterminal.license.current_language", lambda: "en")
    opts["license_accepted_hash"] = lic._hash_text("LICENSE v1\n")

    # If the dialog were called we'd hit Gtk.Dialog() and fail in unit env.
    called = {"dialog": False}

    def _no_dialog(*args, **kwargs):
        called["dialog"] = True
        return True

    monkeypatch.setattr(lic, "_show_license_dialog", _no_dialog)
    assert lic._require_license_acceptance(window=None) is True
    assert called["dialog"] is False


# ─── F4: per-language resolution + cross-language hash mismatch ─────────────


def test_resolve_picks_language_specific_file(tmp_path, monkeypatch):
    """current_language='pl' -> resolver returns LICENSE.pl.md."""
    (tmp_path / "LICENSE.en.md").write_text("EN content\n")
    (tmp_path / "LICENSE.pl.md").write_text("PL content\n")
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr("bterminal.license.current_language", lambda: "pl")
    assert lic._resolve_license_path() == str(tmp_path / "LICENSE.pl.md")
    assert lic._read_license_text() == "PL content\n"


def test_resolve_falls_back_to_en_when_lang_missing(tmp_path, monkeypatch):
    """current_language='de' but only LICENSE.en.md exists -> en fallback."""
    (tmp_path / "LICENSE.en.md").write_text("EN content\n")
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr("bterminal.license.current_language", lambda: "de")
    assert lic._resolve_license_path() == str(tmp_path / "LICENSE.en.md")
    assert lic._read_license_text() == "EN content\n"


def test_resolve_explicit_language_argument(tmp_path, monkeypatch):
    """Caller can pass explicit language to override current_language()."""
    (tmp_path / "LICENSE.en.md").write_text("EN content\n")
    (tmp_path / "LICENSE.pl.md").write_text("PL content\n")
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)
    monkeypatch.setattr("bterminal.license.current_language", lambda: "en")
    assert lic._resolve_license_path("pl") == str(tmp_path / "LICENSE.pl.md")
    assert lic._read_license_text("pl") == "PL content\n"


def test_language_switch_triggers_reprompt(tmp_path, monkeypatch):
    """4.d acceptance: a user accepts EN -> switches language to PL ->
    next first-run check sees a different file (different hash) and
    re-prompts. The acceptance state is hash-bound, not language-bound,
    so language is implicitly tracked via the hash mapping."""
    opts, _ = _isolated_options(tmp_path, monkeypatch)
    en = tmp_path / "LICENSE.en.md"
    pl = tmp_path / "LICENSE.pl.md"
    en.write_text("EN content\n")
    pl.write_text("PL content\n")
    monkeypatch.setattr(lic, "LICENSE_DIR", tmp_path)

    # User on EN locale accepts.
    monkeypatch.setattr("bterminal.license.current_language", lambda: "en")
    en_text = lic._read_license_text()
    lic._record_acceptance(en_text)
    assert lic._is_accepted_for(en_text) is True

    # User switches to PL — stored hash now mismatches the PL file.
    monkeypatch.setattr("bterminal.license.current_language", lambda: "pl")
    pl_text = lic._read_license_text()
    assert pl_text != en_text
    assert lic._is_accepted_for(pl_text) is False

    # _require_license_acceptance would now re-prompt (we stub the dialog
    # to skip GTK and return False; the function returns False on decline).
    monkeypatch.setattr(lic, "_show_license_dialog", lambda *a, **k: False)
    assert lic._require_license_acceptance(window=None) is False


def test_real_pl_license_exists_and_has_attribution():
    """defaults/license/LICENSE.pl.md must mirror the EN attribution
    (author name + email present, structure intact)."""
    repo_root = Path(__file__).resolve().parent.parent
    pl = repo_root / "defaults" / "license" / "LICENSE.pl.md"
    assert pl.exists(), f"{pl} is missing"
    text = pl.read_text(encoding="utf-8")
    assert "Bartosz Czarnota" in text
    assert "bartoszczarnota1@gmail.com" in text
