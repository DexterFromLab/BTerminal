"""Unit tests for the i18n subsystem (F1).

Covers locale resolution chain (init_locale), identity fallback when no
catalog is present, current_language() reporting, and the noop semantics
of N_(). Catalog-loaded translation lookup + ngettext PL plural rules
are covered by F6.c (test scaffolding extended once .mo files exist).
"""

import os
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

import gi
gi.require_version("Gtk", "3.0")  # noqa: E402

from bterminal import config as cfg  # noqa: E402
from bterminal import i18n  # noqa: E402


# ─── _normalize ──────────────────────────────────────────────────────────────


def test_normalize_strips_encoding_and_territory():
    assert i18n._normalize("pl_PL.UTF-8") == "pl"
    assert i18n._normalize("en_US") == "en"
    assert i18n._normalize("de") == "de"


def test_normalize_returns_none_for_falsy_or_pseudo():
    assert i18n._normalize("") is None
    assert i18n._normalize(None) is None
    assert i18n._normalize("  ") is None
    assert i18n._normalize("auto") is None
    assert i18n._normalize("AUTO") is None
    assert i18n._normalize("C") is None
    assert i18n._normalize("POSIX") is None


# ─── init_locale resolution chain ────────────────────────────────────────────


def _clear_lang_env(monkeypatch):
    monkeypatch.delenv("LANGUAGE", raising=False)
    monkeypatch.delenv("LANG", raising=False)


def test_init_locale_explicit_arg_wins(monkeypatch):
    """Explicit `language=` overrides options.json and env."""
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    cfg._OPTIONS["language"] = "de"
    try:
        assert i18n.init_locale("pl") == "pl"
        assert i18n.current_language() == "pl"
    finally:
        cfg._OPTIONS.pop("language", None)


def test_init_locale_options_json_used(monkeypatch):
    """When no explicit arg, options.json:language wins over env."""
    _clear_lang_env(monkeypatch)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    cfg._OPTIONS["language"] = "pl"
    try:
        assert i18n.init_locale() == "pl"
    finally:
        cfg._OPTIONS.pop("language", None)


def test_init_locale_options_auto_falls_through(monkeypatch):
    """options.language='auto' is treated as 'no preference' -> next layer."""
    _clear_lang_env(monkeypatch)
    monkeypatch.setenv("LANG", "de_DE.UTF-8")
    cfg._OPTIONS["language"] = "auto"
    try:
        assert i18n.init_locale() == "de"
    finally:
        cfg._OPTIONS.pop("language", None)


def test_init_locale_options_none_falls_through(monkeypatch):
    """options.language=None -> next layer."""
    _clear_lang_env(monkeypatch)
    monkeypatch.setenv("LANGUAGE", "pl_PL")
    cfg._OPTIONS["language"] = None
    try:
        assert i18n.init_locale() == "pl"
    finally:
        cfg._OPTIONS.pop("language", None)


def test_init_locale_LANGUAGE_env_used(monkeypatch):
    """LANGUAGE env wins over LANG."""
    cfg._OPTIONS.pop("language", None)
    monkeypatch.setenv("LANGUAGE", "pl_PL")
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert i18n.init_locale() == "pl"


def test_init_locale_LANG_env_used(monkeypatch):
    """LANG used when LANGUAGE absent."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    monkeypatch.setenv("LANG", "fr_FR.UTF-8")
    assert i18n.init_locale() == "fr"


def test_init_locale_default_en_when_nothing_set(monkeypatch):
    """No options, no env -> 'en'."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    assert i18n.init_locale() == "en"


def test_init_locale_C_locale_falls_through_to_en(monkeypatch):
    """LANG=C is a 'no preference' marker, fall through to default."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    monkeypatch.setenv("LANG", "C")
    assert i18n.init_locale() == "en"


# ─── _() / ngettext fallback when no catalog ────────────────────────────────


def test_underscore_returns_msgid_when_catalog_missing(monkeypatch):
    """No .mo for the active language -> _() returns msgid (identity)."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    i18n.init_locale("xx")  # nonsense language, no catalog
    assert i18n._("Cancel") == "Cancel"
    assert i18n._("New version available") == "New version available"


def test_ngettext_default_plural_when_catalog_missing(monkeypatch):
    """English plural rule (n != 1 -> plural form) when no catalog loaded."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    i18n.init_locale("xx")
    assert i18n.ngettext("{n} task", "{n} tasks", 1) == "{n} task"
    assert i18n.ngettext("{n} task", "{n} tasks", 2) == "{n} tasks"
    assert i18n.ngettext("{n} task", "{n} tasks", 0) == "{n} tasks"


# ─── N_ marker (noop) ───────────────────────────────────────────────────────


def test_N_returns_input_unchanged():
    assert i18n.N_("Cannot connect") == "Cannot connect"


# ─── config integration ─────────────────────────────────────────────────────


def test_OPTIONS_DEFAULTS_has_language_key():
    """1.b acceptance: 'language' is a documented default option."""
    assert "language" in cfg._OPTIONS_DEFAULTS
    assert cfg._OPTIONS_DEFAULTS["language"] is None


def test_OPTIONS_DEFAULTS_has_tell_ai_language():
    """5.b acceptance: 'tell_ai_language' default is True."""
    assert "tell_ai_language" in cfg._OPTIONS_DEFAULTS
    assert cfg._OPTIONS_DEFAULTS["tell_ai_language"] is True


def test_supported_languages_has_pl_and_en():
    """5.a acceptance: dropdown lists English + Polski."""
    codes = [c for c, _native, _en in i18n.SUPPORTED_LANGUAGES]
    assert "en" in codes
    assert "pl" in codes


def test_language_english_name():
    """5.c uses english_name to feed AI prompt — must return EN names."""
    assert i18n.language_english_name("pl") == "Polish"
    assert i18n.language_english_name("en") == "English"
    # Unknown code -> code itself (graceful fallback).
    assert i18n.language_english_name("xx") == "xx"


# ─── F5.c: AI intro prompt language hint ────────────────────────────────────


def test_intro_prompt_appends_language_hint_when_enabled(monkeypatch):
    """When tell_ai_language=True and lang != 'en', the intro prompt must
    include a hint telling the AI which language the user speaks."""
    cfg._OPTIONS["tell_ai_language"] = True
    cfg._OPTIONS.pop("language", None)
    monkeypatch.setenv("LANGUAGE", "pl_PL")
    monkeypatch.delenv("LANG", raising=False)
    i18n.init_locale()
    assert i18n.current_language() == "pl"

    from bterminal.ui.dialogs.claude_code import _build_intro_prompt
    # Stub helpers to return empty strings so we test only the hint logic.
    import bterminal.ui.dialogs.claude_code as cc
    monkeypatch.setattr(cc, "_fetch_ctx_output", lambda _p: "")
    monkeypatch.setattr(cc, "_tools_help", lambda _p: "")
    monkeypatch.setattr(cc, "_fetch_rules_block", lambda _p: "")
    monkeypatch.setattr(cc, "_read_global_rules", lambda: [])

    prompt = _build_intro_prompt("test_project")
    assert "User language" in prompt
    assert "Polish" in prompt
    assert "Respond in that language" in prompt


def test_intro_prompt_no_hint_when_disabled(monkeypatch):
    """tell_ai_language=False -> no hint appended even when lang != 'en'."""
    cfg._OPTIONS["tell_ai_language"] = False
    cfg._OPTIONS.pop("language", None)
    monkeypatch.setenv("LANGUAGE", "pl_PL")
    monkeypatch.delenv("LANG", raising=False)
    i18n.init_locale()

    from bterminal.ui.dialogs.claude_code import _build_intro_prompt
    import bterminal.ui.dialogs.claude_code as cc
    monkeypatch.setattr(cc, "_fetch_ctx_output", lambda _p: "")
    monkeypatch.setattr(cc, "_tools_help", lambda _p: "")
    monkeypatch.setattr(cc, "_fetch_rules_block", lambda _p: "")
    monkeypatch.setattr(cc, "_read_global_rules", lambda: [])

    prompt = _build_intro_prompt("test_project")
    assert "User language" not in prompt
    # Restore default for other tests
    cfg._OPTIONS.pop("tell_ai_language", None)


def test_intro_prompt_no_hint_when_lang_is_en(monkeypatch):
    """lang == 'en' -> no hint (AI speaks EN by default; redundant)."""
    cfg._OPTIONS["tell_ai_language"] = True
    cfg._OPTIONS.pop("language", None)
    monkeypatch.delenv("LANGUAGE", raising=False)
    monkeypatch.setenv("LANG", "en_US.UTF-8")
    i18n.init_locale()
    assert i18n.current_language() == "en"

    from bterminal.ui.dialogs.claude_code import _build_intro_prompt
    import bterminal.ui.dialogs.claude_code as cc
    monkeypatch.setattr(cc, "_fetch_ctx_output", lambda _p: "")
    monkeypatch.setattr(cc, "_tools_help", lambda _p: "")
    monkeypatch.setattr(cc, "_fetch_rules_block", lambda _p: "")
    monkeypatch.setattr(cc, "_read_global_rules", lambda: [])

    prompt = _build_intro_prompt("test_project")
    assert "User language" not in prompt
    cfg._OPTIONS.pop("tell_ai_language", None)


# ─── 6.c: catalog-loaded translation lookup + plural forms ─────────────────


import subprocess  # noqa: E402


@pytest.fixture(scope="module")
def pl_catalog_compiled():
    """Ensure locale/pl/LC_MESSAGES/bterminal.mo exists. Compiles on the
    fly from the .po if msgfmt is available; skips the test session when
    neither the .mo nor msgfmt are available."""
    repo_root = Path(__file__).resolve().parent.parent
    po = repo_root / "locale" / "pl" / "LC_MESSAGES" / "bterminal.po"
    mo = po.with_suffix(".mo")
    if mo.exists():
        return mo
    if not po.exists():
        pytest.skip(f"PL catalog source not found at {po}")
    if subprocess.call(["which", "msgfmt"], stdout=subprocess.DEVNULL) != 0:
        pytest.skip("msgfmt not available — install gettext")
    subprocess.check_call(["msgfmt", "--check", "-o", str(mo), str(po)])
    return mo


def test_underscore_lookup_with_real_pl_catalog(pl_catalog_compiled, monkeypatch):
    """6.c acceptance: _() returns Polish translation when pl.mo loaded."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    i18n.init_locale("pl")
    assert i18n.current_language() == "pl"
    # Spot-check a handful of msgids known to be in F3's pl.po.
    assert i18n._("Cancel") == "Anuluj"
    assert i18n._("Decline and exit") == "Odrzuć i wyjdź"
    assert i18n._("Save") == "Zapisz"
    assert i18n._("Working tree clean") == "Drzewo robocze czyste"


def test_ngettext_polish_plural_rules(pl_catalog_compiled, monkeypatch):
    """6.c acceptance: ngettext correctly applies Polish 3-form rule.
    Polish: 1 -> singular; 2-4 (except teens) -> few; rest -> many.
    Teens (12-14) -> many; 22-24 -> few again."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    i18n.init_locale("pl")
    cases = [
        (1, "1 plik"),       # singular
        (2, "2 pliki"),      # few
        (3, "3 pliki"),      # few
        (4, "4 pliki"),      # few
        (5, "5 plików"),     # many
        (12, "12 plików"),   # teen exception (many)
        (22, "22 pliki"),    # back to few
    ]
    for n, expected in cases:
        got = i18n.ngettext("{n} file", "{n} files", n).format(n=n)
        assert got == expected, f"n={n}: got {got!r}, want {expected!r}"


def test_underscore_falls_back_to_msgid_when_key_missing(
    pl_catalog_compiled, monkeypatch
):
    """6.c acceptance: a msgid not in the catalog falls back to the
    English msgid (identity), regardless of active language."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    i18n.init_locale("pl")
    # Some random string never seen by the extractor.
    assert i18n._("This string is not in any .po") == "This string is not in any .po"
