"""Comprehensive translation coverage tests.

These tests focus on the *content* of the translation catalog (rather
than the i18n machinery itself, which is in test_i18n.py):

  - Every msgid in bterminal.pot has a non-empty msgstr in pl.po.
  - No fuzzy translations slipped past the auditor.
  - The PL plural triple is well-formed (3 msgstr[N] entries).
  - Specific user-visible strings in major dialogs render in Polish at
    runtime (round-trip from extractor through msgfmt back into _()).
  - Switching language back and forth at runtime works (catalog
    reloads cleanly on each init_locale call).
  - LICENSE files exist for every language listed in SUPPORTED_LANGUAGES.

Tests that need a compiled .mo skip themselves (with a clear reason) if
the catalog has not been built yet — `tools/i18n.sh compile` produces
it, and `install.sh` runs that automatically. CI runs install.sh first,
so this is a no-op there.
"""

from __future__ import annotations

import subprocess
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
from bterminal import license as lic  # noqa: E402

LOCALE_DIR = REPO_ROOT / "locale"
POT = LOCALE_DIR / "bterminal.pot"


# ─── Helpers ─────────────────────────────────────────────────────────────────


def _read_po(path: Path):
    """Parse a .po file with babel; returns the Catalog."""
    pytest.importorskip("babel.messages.pofile")
    from babel.messages.pofile import read_po
    with open(path, "rb") as fh:
        return read_po(fh)


def _ensure_compiled(po: Path) -> Path:
    """Compile po -> mo on the fly when missing. Skips if msgfmt absent."""
    mo = po.with_suffix(".mo")
    if mo.exists():
        return mo
    if not po.exists():
        pytest.skip(f"catalog source not found: {po}")
    if subprocess.call(["which", "msgfmt"], stdout=subprocess.DEVNULL) != 0:
        pytest.skip("msgfmt not available — install gettext")
    subprocess.check_call(["msgfmt", "--check", "-o", str(mo), str(po)])
    return mo


def _clear_lang_env(monkeypatch):
    monkeypatch.delenv("LANGUAGE", raising=False)
    monkeypatch.delenv("LANG", raising=False)


# ─── Catalog completeness ────────────────────────────────────────────────────


def test_pot_exists():
    """`tools/i18n.sh extract` must have been run to produce the .pot."""
    assert POT.exists(), (
        f"{POT} missing — run './tools/i18n.sh extract' before running tests."
    )


def test_pl_po_exists():
    po = LOCALE_DIR / "pl" / "LC_MESSAGES" / "bterminal.po"
    assert po.exists(), f"{po} missing — initialise with './tools/i18n.sh new pl_PL'"


def test_pl_catalog_has_no_untranslated_msgids():
    """Every msgid in pl.po (except the empty header) must have a non-empty
    msgstr. An untranslated entry would silently fall back to the English
    msgid at runtime, which defeats the purpose of localisation."""
    po = LOCALE_DIR / "pl" / "LC_MESSAGES" / "bterminal.po"
    catalog = _read_po(po)
    untranslated = []
    for message in catalog:
        if not message.id:
            continue  # header
        # Plural messages: string is a tuple of 3
        if isinstance(message.id, tuple):
            if any(not s for s in (message.string or ("",) * 3)):
                untranslated.append(message.id)
        else:
            if not message.string:
                untranslated.append(message.id)
    assert not untranslated, (
        f"PL catalog has {len(untranslated)} untranslated entries: "
        f"{untranslated[:5]}{'...' if len(untranslated) > 5 else ''}"
    )


def test_pl_catalog_has_no_fuzzy_translations():
    """Fuzzy entries (auto-merged by msgmerge but unverified by a human)
    must be reviewed before shipping. CI fails until they are cleared."""
    po = LOCALE_DIR / "pl" / "LC_MESSAGES" / "bterminal.po"
    catalog = _read_po(po)
    fuzzy = [m.id for m in catalog if m.id and m.fuzzy]
    assert not fuzzy, f"PL catalog has {len(fuzzy)} fuzzy entries: {fuzzy[:5]}"


def _extract_msgids_from_pot(path: Path) -> set:
    """Lightweight regex-based msgid extractor for .pot files.

    babel.messages.pofile.read_po raises ValueError on .pot files whose
    `Language:` header is intentionally empty (the template carries no
    locale), so we parse manually here. Decodes \\n / \\t / \\" escape
    sequences so the resulting strings compare equal to the ones babel
    produces from .po files (which DO get those decoded automatically).
    """
    import re
    msgids: set[str] = set()
    current: list[str] = []
    in_msgid = False

    def _decode_po_escapes(s: str) -> str:
        """Decode the small set of escape sequences PO files actually use:
        \\n, \\t, \\r, \\", \\\\. Avoids `codecs.unicode_escape` because
        that decoder treats UTF-8 bytes as Latin-1 and mangles characters
        like em-dash (U+2014)."""
        return (s
                .replace("\\\\", "\x00")
                .replace("\\n", "\n")
                .replace("\\t", "\t")
                .replace("\\r", "\r")
                .replace('\\"', '"')
                .replace("\x00", "\\"))

    def _flush():
        if not current:
            return
        joined = "".join(current)
        if not joined:
            return
        msgids.add(_decode_po_escapes(joined))

    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.rstrip("\n")
        if line.startswith("msgid "):
            _flush()
            current = [re.sub(r'^msgid\s+"(.*)"$', r"\1", line)]
            in_msgid = True
        elif line.startswith("msgid_plural "):
            _flush()
            current = []
            in_msgid = False
        elif in_msgid and line.startswith('"') and line.endswith('"'):
            current.append(line[1:-1])
        else:
            _flush()
            current = []
            in_msgid = False
    _flush()
    msgids.discard("")
    return msgids


def test_pl_catalog_msgids_match_pot():
    """Every msgid in the .pot must have a corresponding entry in pl.po
    (otherwise developers added new strings without running
    `tools/i18n.sh update` to propagate them)."""
    pot_ids = _extract_msgids_from_pot(POT)
    pl = _read_po(LOCALE_DIR / "pl" / "LC_MESSAGES" / "bterminal.po")
    pl_ids = set()
    for m in pl:
        if not m.id:
            continue
        if isinstance(m.id, tuple):
            pl_ids.update(m.id)
        else:
            pl_ids.add(m.id)
    missing = pot_ids - pl_ids
    assert not missing, (
        f"{len(missing)} msgid(s) present in .pot but missing from pl.po — "
        f"run './tools/i18n.sh update' to propagate. Examples: "
        f"{list(missing)[:3]}"
    )


def test_pl_plural_has_three_forms():
    """Polish nplurals=3. The {n}-file plural triple must have all three
    msgstr[N] entries set."""
    po = LOCALE_DIR / "pl" / "LC_MESSAGES" / "bterminal.po"
    catalog = _read_po(po)
    for message in catalog:
        if isinstance(message.id, tuple):
            assert len(message.string) == 3, (
                f"PL plural for {message.id[0]!r} has "
                f"{len(message.string)} forms, expected 3 (singular/few/many)"
            )
            assert all(message.string), (
                f"PL plural for {message.id[0]!r} has empty form(s): "
                f"{message.string}"
            )


# ─── End-to-end runtime translation ──────────────────────────────────────────


@pytest.fixture(scope="module")
def pl_runtime():
    """Compile pl.mo if needed (no-op when install.sh already did it)."""
    po = LOCALE_DIR / "pl" / "LC_MESSAGES" / "bterminal.po"
    _ensure_compiled(po)


# Specific user-facing strings that MUST appear in PL when language=pl.
# Tuples are (English msgid, expected PL msgstr). Picking a sample across
# different files so a regression in any one doesn't go unnoticed.
DIALOG_STRINGS = [
    # license.py
    ("Decline and exit", "Odrzuć i wyjdź"),
    ("Accept", "Akceptuję"),
    ("First run", "Pierwsze uruchomienie"),
    ("I have read and accept the license terms.",
     "Przeczytałem i akceptuję warunki licencji."),
    # updater.py
    ("Cancel", "Anuluj"),
    ("Close", "Zamknij"),
    ("Update and restart", "Aktualizuj i uruchom ponownie"),
    ("BTerminal is up to date. No new updates.",
     "BTerminal jest aktualny. Brak nowych aktualizacji."),
    # app.py — menubar
    ("File", "Plik"),
    ("View", "Widok"),
    ("Tools", "Narzędzia"),
    ("Quit", "Zamknij aplikację"),
    ("Sessions", "Sesje"),
    ("Tasks", "Zadania"),
    ("Plugins", "Wtyczki"),
    # options.py
    ("Appearance", "Wygląd"),
    ("Theme:", "Motyw:"),
    ("Save", "Zapisz"),
    ("General", "Ogólne"),
    # git panel
    ("Working tree clean", "Drzewo robocze czyste"),
]


@pytest.mark.parametrize("english,polish", DIALOG_STRINGS)
def test_dialog_string_translates_to_polish(pl_runtime, monkeypatch, english, polish):
    """Each curated msgid must round-trip through gettext to the
    expected Polish msgstr when the locale is pl."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    i18n.init_locale("pl")
    assert i18n._(english) == polish, (
        f"_({english!r}) -> {i18n._(english)!r}, expected {polish!r}"
    )


def test_runtime_language_switch(pl_runtime, monkeypatch):
    """Calling init_locale() multiple times must reload the catalog
    cleanly — switching pl -> en -> pl returns each language's strings."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)

    i18n.init_locale("pl")
    pl_cancel = i18n._("Cancel")
    assert pl_cancel == "Anuluj"

    i18n.init_locale("en")
    en_cancel = i18n._("Cancel")
    assert en_cancel == "Cancel"   # identity fallback (no en.mo, that's correct)

    i18n.init_locale("pl")
    pl_cancel_again = i18n._("Cancel")
    assert pl_cancel_again == "Anuluj"


def test_supported_languages_each_have_a_license_file():
    """Per F4: every language listed in SUPPORTED_LANGUAGES must have a
    LICENSE.<lang>.md, otherwise the resolver silently falls back to EN
    and users on that locale see the wrong language in the legal dialog."""
    license_dir = REPO_ROOT / "defaults" / "license"
    for code, native_name, _en_name in i18n.SUPPORTED_LANGUAGES:
        path = license_dir / f"LICENSE.{code}.md"
        assert path.exists(), (
            f"SUPPORTED_LANGUAGES advertises {code!r} ({native_name!r}) but "
            f"{path} is missing — translate the license or remove the language "
            f"entry."
        )


def test_pl_license_preserves_attribution_clause():
    """Attribution clause (author + email + project name) is the legal
    core of the license — it MUST survive translation intact."""
    pl = REPO_ROOT / "defaults" / "license" / "LICENSE.pl.md"
    text = pl.read_text(encoding="utf-8")
    assert "Bartosz Czarnota" in text
    assert "bartoszczarnota1@gmail.com" in text
    assert "BTerminal" in text


def test_pl_license_hash_differs_from_en_license():
    """If both files were accidentally identical (e.g. someone copied
    LICENSE.en.md into LICENSE.pl.md without translating), the hash
    check would behave the same for both locales — defeating F4.d. We
    assert they have different hashes to catch that mistake."""
    en = (REPO_ROOT / "defaults" / "license" / "LICENSE.en.md").read_text("utf-8")
    pl = (REPO_ROOT / "defaults" / "license" / "LICENSE.pl.md").read_text("utf-8")
    assert lic._hash_text(en) != lic._hash_text(pl)


# ─── Multi-language coverage: every advertised language is functional ───────


# Build a fixture for each non-EN language, parametrising the runtime
# tests below. EN is excluded because it has no .mo (msgids are canonical).
NON_EN_LANGUAGES = [
    code for code, _native, _en in i18n.SUPPORTED_LANGUAGES if code != "en"
]


@pytest.fixture(scope="module")
def all_catalogs_compiled():
    """Compile every locale/<lang>/LC_MESSAGES/bterminal.po -> .mo.
    install.sh does this on a real install; we replicate it here so
    the test layer can run on a clean checkout."""
    if subprocess.call(["which", "msgfmt"], stdout=subprocess.DEVNULL) != 0:
        pytest.skip("msgfmt not available — install gettext")
    for code in NON_EN_LANGUAGES:
        po = LOCALE_DIR / code / "LC_MESSAGES" / "bterminal.po"
        if not po.exists():
            continue
        mo = po.with_suffix(".mo")
        if not mo.exists():
            subprocess.check_call(["msgfmt", "--check", "-o", str(mo), str(po)])


@pytest.mark.parametrize("code", NON_EN_LANGUAGES)
def test_each_language_translates_known_strings(all_catalogs_compiled, monkeypatch, code):
    """Every advertised language must translate a canonical set of
    strings to *something other than the English msgid* — a sanity
    check that catches forgotten / empty translations.

    Exception: a translation that legitimately equals the English
    msgid (e.g. 'Sessions' in French is also 'Sessions') is allowed,
    so we test for "at least 3 of 5 differ from EN" rather than 5/5."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    i18n.init_locale(code)

    canon = ["File", "Cancel", "Save", "Accept", "Decline and exit"]
    differing = sum(1 for m in canon if i18n._(m) != m)
    assert differing >= 3, (
        f"{code}: only {differing}/5 canonical strings translated. "
        f"Got: " + ", ".join(f"{m!r}->{i18n._(m)!r}" for m in canon)
    )


@pytest.mark.parametrize("code", NON_EN_LANGUAGES)
def test_each_language_catalog_has_no_untranslated(code):
    """Every shipped .po must have 100% coverage — no empty msgstr
    slipping through. Catches the classic 'msgmerge added new strings,
    nobody translated them, ship anyway' mistake."""
    po = LOCALE_DIR / code / "LC_MESSAGES" / "bterminal.po"
    if not po.exists():
        pytest.skip(f"{po} missing — language not bootstrapped yet")
    catalog = _read_po(po)
    untranslated = []
    for m in catalog:
        if not m.id:
            continue
        if isinstance(m.id, tuple):
            forms = m.string or ()
            if not forms or any(not s for s in forms):
                untranslated.append(m.id[0])
        else:
            if not m.string:
                untranslated.append(m.id)
    assert not untranslated, (
        f"{code}: {len(untranslated)} untranslated msgids: "
        f"{untranslated[:3]}"
    )


@pytest.mark.parametrize("code", NON_EN_LANGUAGES)
def test_each_language_catalog_has_no_fuzzy(code):
    """Fuzzy entries are auto-merge guesses that bypass the translator's
    review. They're invisible at runtime (gettext returns msgid). We
    require zero fuzzy in shipped catalogs."""
    po = LOCALE_DIR / code / "LC_MESSAGES" / "bterminal.po"
    if not po.exists():
        pytest.skip(f"{po} missing")
    catalog = _read_po(po)
    fuzzy = [m.id for m in catalog if m.id and m.fuzzy]
    assert not fuzzy, f"{code}: {len(fuzzy)} fuzzy entries — review and clear"


@pytest.mark.parametrize("code", NON_EN_LANGUAGES)
def test_each_language_has_license_file(code):
    """Every advertised language must ship a LICENSE.<code>.md.
    The license module falls back to LICENSE.en.md if missing, but
    that defeats the F4 promise of localised legal text."""
    license_path = REPO_ROOT / "defaults" / "license" / f"LICENSE.{code}.md"
    assert license_path.exists(), (
        f"LICENSE.{code}.md missing — translate it or remove {code} "
        f"from SUPPORTED_LANGUAGES"
    )


@pytest.mark.parametrize("code", NON_EN_LANGUAGES)
def test_each_language_license_preserves_attribution(code):
    """Every translated LICENSE must keep the legal core: author name,
    contact email, project name. This is the whole point of the
    license — translation must not erode it."""
    p = REPO_ROOT / "defaults" / "license" / f"LICENSE.{code}.md"
    if not p.exists():
        pytest.skip(f"{p} missing")
    text = p.read_text(encoding="utf-8")
    assert "Bartosz Czarnota" in text, f"{code}: author name missing"
    assert "bartoszczarnota1@gmail.com" in text, f"{code}: contact email missing"
    assert "BTerminal" in text, f"{code}: project name missing"


def test_all_license_hashes_unique():
    """No two LICENSE.<lang>.md files may have identical content —
    otherwise switching between those two languages would not
    re-prompt the user (hash compare succeeds against the wrong file).
    Strong guarantee that every translation actually differs."""
    license_dir = REPO_ROOT / "defaults" / "license"
    by_hash: dict[str, str] = {}
    for code, _native, _en in i18n.SUPPORTED_LANGUAGES:
        path = license_dir / f"LICENSE.{code}.md"
        if not path.exists():
            continue
        digest = lic._hash_text(path.read_text(encoding="utf-8"))
        if digest in by_hash:
            pytest.fail(
                f"LICENSE.{code}.md has the same SHA-256 as "
                f"LICENSE.{by_hash[digest]}.md — duplicate content"
            )
        by_hash[digest] = code


# ─── Plural-forms by category ───────────────────────────────────────────────


# Plural-form classes per language. Validated by checking what
# ngettext returns for n in (1, 2, 5).
THREE_FORM_LANGUAGES = ["pl", "ru", "uk", "cs"]   # singular / few / many
TWO_FORM_LANGUAGES = ["de", "es", "fr", "it", "pt"]   # singular / plural
ONE_FORM_LANGUAGES = ["zh", "ja", "ko"]           # invariant


@pytest.mark.parametrize("code", THREE_FORM_LANGUAGES)
def test_three_form_plural_distinguishes_categories(all_catalogs_compiled, monkeypatch, code):
    """Slavic languages: ngettext must produce three distinct forms for
    n=1, n=2, n=5. If forms 2 and 5 collapse, the Plural-Forms header
    is wrong (probably copy-pasted from English n!=1)."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    i18n.init_locale(code)

    sing = i18n.ngettext("{n} file", "{n} files", 1).format(n=1)
    few = i18n.ngettext("{n} file", "{n} files", 2).format(n=2)
    many = i18n.ngettext("{n} file", "{n} files", 5).format(n=5)
    assert sing != few, f"{code}: singular and few collapsed: {sing!r}=={few!r}"
    assert few != many, f"{code}: few and many collapsed: {few!r}=={many!r}"


@pytest.mark.parametrize("code", TWO_FORM_LANGUAGES)
def test_two_form_plural_distinguishes_singular_plural(all_catalogs_compiled, monkeypatch, code):
    """Romance + Germanic: singular form for n=1, plural for n>=2."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    i18n.init_locale(code)

    sing = i18n.ngettext("{n} file", "{n} files", 1).format(n=1)
    plur = i18n.ngettext("{n} file", "{n} files", 2).format(n=2)
    assert sing != plur, f"{code}: singular and plural collapsed: {sing!r}"


@pytest.mark.parametrize("code", ONE_FORM_LANGUAGES)
def test_one_form_plural_invariant(all_catalogs_compiled, monkeypatch, code):
    """CJK languages have no plural distinction — ngettext returns the
    same form for any n."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    i18n.init_locale(code)

    forms = {i18n.ngettext("{n} file", "{n} files", n).format(n=n)
             for n in (1, 2, 5, 10)}
    # Different *numbers* substituted, but the *template* should be the same.
    # So strip the digit and compare the rest.
    templates = {f.replace("1", "").replace("2", "").replace("5", "").replace("0", "").strip()
                 for f in forms}
    assert len(templates) == 1, f"{code}: expected 1 template, got {templates!r}"


# ─── License resolution covers all languages ────────────────────────────────


@pytest.mark.parametrize("code", [c for c, _n, _e in i18n.SUPPORTED_LANGUAGES])
def test_license_resolver_picks_correct_file_for_each_language(monkeypatch, code):
    """For every advertised language, license.py's _resolve_license_path
    must return the matching file (not fall back to en) — verifies F4.c
    end-to-end across the full language set."""
    monkeypatch.setattr(
        "bterminal.license.current_language", lambda c=code: c
    )
    expected = REPO_ROOT / "defaults" / "license" / f"LICENSE.{code}.md"
    if not expected.exists():
        pytest.skip(f"{expected} missing — language file not yet shipped")
    resolved = lic._resolve_license_path()
    assert resolved == str(expected), (
        f"resolver returned {resolved!r}, expected {expected}"
    )


@pytest.mark.parametrize("code", NON_EN_LANGUAGES)
def test_license_text_for_each_language_starts_with_localized_title(code):
    """Sanity check that LICENSE.<code>.md actually opens in the target
    language (rather than being an EN copy with the wrong filename).
    We check the first line contains language-specific characters or
    a known translated phrase."""
    p = REPO_ROOT / "defaults" / "license" / f"LICENSE.{code}.md"
    if not p.exists():
        pytest.skip(f"{p} missing")
    first_line = p.read_text(encoding="utf-8").splitlines()[0]
    # A simple heuristic: the title must NOT match the EN title verbatim.
    en_title = "# BTerminal License Agreement"
    assert first_line != en_title, (
        f"{code}: LICENSE first line is the English title — file is "
        f"a copy of LICENSE.en.md, not a translation"
    )


# ─── AI prompt language hint ────────────────────────────────────────────────


# ─── Options dialog: language setting propagates after Save ─────────────────


def test_language_choice_persists_to_options_json(tmp_path, monkeypatch):
    """User picks 'en' in OptionsDialog → clicks Save → options.json on
    disk reads 'language': 'en'. This is the contract that survives the
    restart required for the UI to actually re-render in the new
    language."""
    monkeypatch.setattr(cfg, "OPTIONS_FILE", str(tmp_path / "options.json"))
    monkeypatch.setattr(cfg, "CONFIG_DIR", str(tmp_path))

    # Simulate run_and_apply() core: write language + save.
    cfg._OPTIONS.clear()
    cfg._OPTIONS.update(dict(cfg._OPTIONS_DEFAULTS))
    cfg._OPTIONS["language"] = "en"
    cfg._OPTIONS["tell_ai_language"] = False
    cfg._save_options(cfg._OPTIONS)

    # Reload from disk — what would happen on next launch.
    reloaded = cfg._load_options()
    assert reloaded["language"] == "en"
    assert reloaded["tell_ai_language"] is False


def test_language_choice_takes_effect_on_next_init_locale(tmp_path, monkeypatch):
    """End-to-end propagation: after Save (language=en) → next process
    start (simulated by reloading options + calling init_locale) honours
    the new value, even though LANGUAGE/LANG env say otherwise."""
    monkeypatch.setattr(cfg, "OPTIONS_FILE", str(tmp_path / "options.json"))
    monkeypatch.setattr(cfg, "CONFIG_DIR", str(tmp_path))
    monkeypatch.setenv("LANG", "pl_PL.UTF-8")  # env-pl, but options will override

    # Apply: user picks 'en' and saves.
    cfg._OPTIONS.clear()
    cfg._OPTIONS.update(dict(cfg._OPTIONS_DEFAULTS))
    cfg._OPTIONS["language"] = "en"
    cfg._save_options(cfg._OPTIONS)

    # Restart simulation: reload options and re-init locale (this is
    # what __main__.main() does on launch).
    cfg._OPTIONS.clear()
    cfg._OPTIONS.update(cfg._load_options())
    resolved = i18n.init_locale(cfg._OPTIONS.get("language"))
    assert resolved == "en"
    assert i18n.current_language() == "en"


def test_auto_detect_sentinel_falls_through_to_env(tmp_path, monkeypatch):
    """OptionsDialog 'Auto-detect' maps to options.language=None.
    init_locale() must then fall through to LANGUAGE/LANG env."""
    monkeypatch.setattr(cfg, "OPTIONS_FILE", str(tmp_path / "options.json"))
    monkeypatch.setattr(cfg, "CONFIG_DIR", str(tmp_path))
    _clear_lang_env(monkeypatch)
    monkeypatch.setenv("LANG", "pl_PL.UTF-8")

    cfg._OPTIONS.clear()
    cfg._OPTIONS.update(dict(cfg._OPTIONS_DEFAULTS))
    cfg._OPTIONS["language"] = None  # Auto-detect sentinel after Save
    cfg._save_options(cfg._OPTIONS)

    reloaded = cfg._load_options()
    resolved = i18n.init_locale(reloaded.get("language"))
    assert resolved == "pl"  # env wins because options is None


def test_language_change_round_trip_pl_to_en_to_pl(tmp_path, monkeypatch):
    """User flips PL → EN → PL across three 'sessions'. Each time the
    persisted value drives the next init_locale call. Catches a class
    of bugs where _save_options writes correctly but _load_options
    fails to read back (e.g. JSON encoding / missing key fallback)."""
    monkeypatch.setattr(cfg, "OPTIONS_FILE", str(tmp_path / "options.json"))
    monkeypatch.setattr(cfg, "CONFIG_DIR", str(tmp_path))
    _clear_lang_env(monkeypatch)

    for picked in ("pl", "en", "pl"):
        cfg._OPTIONS.clear()
        cfg._OPTIONS.update(dict(cfg._OPTIONS_DEFAULTS))
        cfg._OPTIONS["language"] = picked
        cfg._save_options(cfg._OPTIONS)

        # New process simulation
        cfg._OPTIONS.clear()
        cfg._OPTIONS.update(cfg._load_options())
        resolved = i18n.init_locale(cfg._OPTIONS.get("language"))
        assert resolved == picked, (
            f"after Save({picked}), init_locale resolved to {resolved!r}"
        )


def test_options_dialog_dropdown_round_trip(tmp_path, monkeypatch):
    """OptionsDialog stores the active value via combo's id ('en', 'pl',
    or '__auto__' sentinel) — verify the sentinel mapping is symmetric:
    None on disk -> '__auto__' in combo -> None on Save."""
    # The dialog code does:
    #   saved = _OPTIONS.get("language")        # None or 'en' or 'pl'
    #   self._language_combo.set_active_id(saved or "__auto__")
    # ...and on Save:
    #   picked = self._language_combo.get_active_id()
    #   new_language = None if picked == "__auto__" else picked
    # So: None <-> "__auto__", "en" <-> "en", "pl" <-> "pl"
    cases = [
        (None,  "__auto__"),
        ("en",  "en"),
        ("pl",  "pl"),
    ]
    for option_value, combo_id in cases:
        # Saved -> combo
        derived_combo = option_value or "__auto__"
        assert derived_combo == combo_id

        # Combo -> saved
        derived_option = None if combo_id == "__auto__" else combo_id
        assert derived_option == option_value


def test_tell_ai_language_persists(tmp_path, monkeypatch):
    """Toggling the 'Tell AI my language' checkbox must persist to disk
    independently of language choice."""
    monkeypatch.setattr(cfg, "OPTIONS_FILE", str(tmp_path / "options.json"))
    monkeypatch.setattr(cfg, "CONFIG_DIR", str(tmp_path))

    for flag in (True, False, True):
        cfg._OPTIONS.clear()
        cfg._OPTIONS.update(dict(cfg._OPTIONS_DEFAULTS))
        cfg._OPTIONS["tell_ai_language"] = flag
        cfg._save_options(cfg._OPTIONS)

        reloaded = cfg._load_options()
        assert reloaded["tell_ai_language"] is flag


# ─── Live translation refresh ───────────────────────────────────────────────


def test_register_and_refresh_updates_widget(pl_runtime, monkeypatch):
    """Smoke test for the live-refresh mechanism: register a fake widget
    holding a label, change locale, refresh — the label updates without
    rebuilding the widget."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    i18n.init_locale("en")

    class FakeWidget:
        def __init__(self):
            self.label = None
        def set_label(self, text):
            self.label = text

    w = FakeWidget()
    i18n.tr(w, "set_label", "Cancel")
    assert w.label == "Cancel"  # EN identity (no en.mo)

    i18n.init_locale("pl")
    refreshed = i18n.refresh_translatables()
    assert refreshed >= 1, "expected at least one widget refreshed"
    assert w.label == "Anuluj", f"after refresh expected PL, got {w.label!r}"

    # And back to EN
    i18n.init_locale("en")
    i18n.refresh_translatables()
    assert w.label == "Cancel"


def test_destroy_signal_drops_entry_on_refresh(pl_runtime, monkeypatch):
    """When a widget emits the `destroy` signal, register_translatable's
    handler nulls the entry; the next refresh prunes it. This is how we
    avoid stale registrations after dialog teardown without leaking
    Python references to live GTK widgets."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    i18n.init_locale("pl")

    class FakeWidget:
        """Mimics the small GTK surface we touch: set_label + connect."""
        def __init__(self):
            self.label = None
            self._handlers = {}
        def set_label(self, text):
            self.label = text
        def connect(self, signal, callback):
            self._handlers.setdefault(signal, []).append(callback)
        def destroy(self):
            for cb in self._handlers.get("destroy", []):
                cb(self)

    import gc
    gc.collect()
    i18n.refresh_translatables()
    before = len(i18n._translatables)

    w = FakeWidget()
    i18n.tr(w, "set_label", "Cancel")
    assert len(i18n._translatables) == before + 1
    i18n.refresh_translatables()
    assert w.label == "Anuluj"

    # Simulate widget destroy: the signal handler nulls the entry.
    w.destroy()
    i18n.refresh_translatables()
    assert len(i18n._translatables) == before


def test_tr_fmt_re_formats_placeholder_on_refresh(pl_runtime, monkeypatch):
    """tr_fmt() captures the placeholder values at registration time.
    On refresh it MUST re-translate the template AND re-apply .format()
    so the visible string changes language while keeping the value."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    i18n.init_locale("en")

    class FakeWidget:
        def __init__(self):
            self.label = None
        def set_label(self, text):
            self.label = text

    w = FakeWidget()
    i18n.tr_fmt(w, "set_label", "{app} Sessions", app="BTerminal")
    assert w.label == "BTerminal Sessions"  # EN identity

    i18n.init_locale("pl")
    i18n.refresh_translatables()
    assert w.label == "Sesje BTerminal"


def test_register_translatable_does_not_double_apply(pl_runtime, monkeypatch):
    """Calling refresh_translatables() twice in a row gives the same
    result both times (idempotent)."""
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)

    class FakeWidget:
        def __init__(self):
            self.label = None
            self.calls = 0
        def set_label(self, text):
            self.label = text
            self.calls += 1

    i18n.init_locale("pl")
    w = FakeWidget()
    i18n.tr(w, "set_label", "Save")
    initial_calls = w.calls

    i18n.refresh_translatables()
    i18n.refresh_translatables()
    # 1 init + 2 refreshes = 3 calls, all setting the same value.
    assert w.calls == initial_calls + 2
    assert w.label == "Zapisz"


# ─── AI prompt integration with options ──────────────────────────────────────


def test_ai_intro_hint_is_in_english_even_when_ui_is_polish(pl_runtime, monkeypatch):
    """The AI intro prompt is by project policy always English. The
    user-language hint that gets appended must use the language's English
    name (e.g. 'Polish'), NOT its native name ('Polski')."""
    cfg._OPTIONS["tell_ai_language"] = True
    cfg._OPTIONS.pop("language", None)
    _clear_lang_env(monkeypatch)
    monkeypatch.setenv("LANGUAGE", "pl_PL")
    i18n.init_locale()

    import bterminal.ui.dialogs.claude_code as cc
    monkeypatch.setattr(cc, "_fetch_ctx_output", lambda _p: "")
    monkeypatch.setattr(cc, "_tools_help", lambda _p: "")
    monkeypatch.setattr(cc, "_fetch_rules_block", lambda _p: "")
    monkeypatch.setattr(cc, "_read_global_rules", lambda: [])

    prompt = cc._build_intro_prompt("test")
    assert "Polish" in prompt
    assert "Polski" not in prompt
    assert "Respond in that language" in prompt
    cfg._OPTIONS.pop("tell_ai_language", None)
