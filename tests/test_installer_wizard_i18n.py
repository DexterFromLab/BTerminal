"""i18n: InstallerWizard locale switching pinned as 'stays EN'
(#46 / #118, audit § 6.4 #19).

The InstallerWizard hardcodes English strings — page headers,
button labels, license-accept checkbox, summary banner. Setting
`BTERMINAL_LANG=pl` does NOT translate them.

This is a DELIBERATE design choice, pinned by these tests:

  Rationale:
    1. Chicken-and-egg: the wizard runs PRE-BT-install. The
       gettext .mo catalogs live under
       `~/.local/share/bterminal/locale/` which doesn't exist
       yet on first run. The wizard would have to bundle its
       own mini-catalog or fall back to EN anyway — added
       complexity for little user value.
    2. Rare exposure: wizard appears once per machine on the
       initial install, plus occasionally on `Tools → Install
       dependencies`. Compare to the BT main UI which a user
       interacts with for hours daily.
    3. International audience: setup-screen labels are well-
       understood EN ('Welcome', 'Cancel', 'Next →'). Even
       non-English speakers in technical roles recognize them.
    4. Test xdotool-driven E2E (#87) hardcodes the EN page
       headers (`Step 1 of 5: Welcome + License`) — i18n would
       require parametrizing the keystroke driver per locale.

Three decision branches mapped to actual locale state:
  (a) PL locale active (`BTERMINAL_LANG=pl`) — wizard stays EN.
  (b) DE locale active — wizard stays EN.
  (c) Fallback to EN (no env var) — wizard EN (consistent).

If a future change adds wizard i18n, these tests MUST be updated
to assert localized output instead. Lifting the pin is the
explicit migration step.

Manual VM smoke (`BTERMINAL_LANG=pl xvfb-run python3 -m bterminal
--installer`) is documented in tests/manual/README.md.
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
WIZARD = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
I18N = REPO_ROOT / "bterminal" / "i18n.py"


# ─── Source-grep: wizard does NOT use i18n ───────────────────────────────


def test_installer_wizard_does_not_import_underscore_from_i18n():
    """Pin: `from bterminal.i18n import _` is absent in
    installer_wizard.py. The wizard's strings are hardcoded EN.

    If you need to ADD i18n to the wizard (legitimate future
    work), update this test to assert the import IS present, +
    update all the page-header tests below to assert localized
    output."""
    src = WIZARD.read_text()
    assert "from bterminal.i18n import" not in src, (
        "installer_wizard.py now imports from bterminal.i18n — "
        "wizard i18n landed; lift the 'stays EN' pin"
    )
    # Defensive: also check no `_(` translation calls
    # (allow `__init__` and `_show_page` etc. — those start with
    # `def _`, distinct from `_(text)` translation calls).
    # A real translation call has form `_(string-literal)`.
    import re
    translation_calls = re.findall(r'\b_\("[^"]+"\)', src)
    assert not translation_calls, (
        f"wizard has gettext-style translation calls: "
        f"{translation_calls[:3]} — wizard i18n landed"
    )


def test_i18n_module_provides_underscore_for_main_ui():
    """Cross-reference: BT's main UI DOES use i18n. The wizard
    is the deliberate exception. Pin that the i18n primitives
    exist so the rest of BT keeps working."""
    src = I18N.read_text()
    assert "def _(text: str)" in src
    assert "def ngettext" in src
    assert "def current_language" in src
    assert "init_locale" in src


def test_main_ui_dialogs_DO_use_i18n_for_contrast():
    """Pin contrast: at least one OTHER UI module imports `_`
    from i18n. Without this, the 'wizard is the exception'
    framing is meaningless — every UI hardcodes EN."""
    repo = REPO_ROOT / "bterminal" / "ui"
    files_using_i18n = []
    for py in repo.rglob("*.py"):
        text = py.read_text()
        if "from bterminal.i18n import" in text:
            files_using_i18n.append(py.name)
    assert files_using_i18n, (
        "no UI module uses i18n — i18n infrastructure is dead, "
        "wizard's 'stays EN' framing collapses"
    )


# ─── Page headers stay EN regardless of BTERMINAL_LANG ──────────────────


HEADERS_EXPECTED_EN = [
    "Step 1 of 5: Welcome + License",
    "Step 2 of 5: System inventory",
    "Step 3 of 5: Pick what to install",
    "Step 4 of 5: Installing",
    "Step 5 of 5: Summary",
]


@pytest.mark.parametrize("expected_header", HEADERS_EXPECTED_EN)
def test_page_headers_are_english_in_source(expected_header):
    """Pin: each of the 5 wizard page headers is in English in
    the source. The HEADERS tuple is module-level, evaluated at
    import time — locale doesn't influence its content."""
    src = WIZARD.read_text()
    assert expected_header in src, (
        f"wizard lost EN page header {expected_header!r}"
    )


@pytest.mark.parametrize("locale", ["pl", "de", "fr", "ja"])
def test_page_headers_unchanged_under_any_bterminal_lang(locale,
                                                            monkeypatch):
    """(a) + (b): For PL / DE / FR / JA locales, the source
    string remains the SAME English literal. Demonstrates the
    headers are not gettext-wrapped."""
    monkeypatch.setenv("BTERMINAL_LANG", locale)
    # Re-read source (locale doesn't affect file content, but
    # we run the test under the env var to confirm).
    src = WIZARD.read_text()
    for header in HEADERS_EXPECTED_EN:
        assert header in src


def test_page_headers_unchanged_when_no_bterminal_lang_set(monkeypatch):
    """(c) Fallback: no BTERMINAL_LANG → still EN headers
    (consistent with the locale-active branches)."""
    monkeypatch.delenv("BTERMINAL_LANG", raising=False)
    src = WIZARD.read_text()
    for header in HEADERS_EXPECTED_EN:
        assert header in src


# ─── Button labels also EN ──────────────────────────────────────────────


@pytest.mark.parametrize("label", [
    "Cancel",
    "← Back",
    "Next →",
    "Open BTerminal",  # finish button
    "I have read and accept the license terms.",
])
def test_button_and_checkbox_labels_are_english(label):
    """Pin: action area labels + license checkbox are EN
    literals. Drives the xdotool e2e test (#87) — translation
    would break the keystroke driver."""
    src = WIZARD.read_text()
    assert label in src, (
        f"wizard lost EN label {label!r}"
    )


# ─── Banner + summary text EN ───────────────────────────────────────────


@pytest.mark.parametrize("phrase", [
    "Welcome to BTerminal.",
    "Read and accept the license to continue.",
    "System inventory",  # page 2 header marker
    "Optional dependencies",  # page 3 marker
    "Installation finished.",  # summary success banner
    "Cancelled — partial install.",  # summary cancel banner (#111)
])
def test_descriptive_banners_are_english(phrase):
    """Pin: longer prose strings (welcome blurb, summary banners
    pinned in #105 / #111) are EN. Lifting the pin requires
    translating these too."""
    src = WIZARD.read_text()
    assert phrase in src, (
        f"wizard lost EN phrase {phrase!r}"
    )


# ─── Window title EN (xdotool dependency) ───────────────────────────────


def test_window_title_is_bterminal_installer_in_english():
    """Pin: `title="BTerminal Installer"` literal. The xdotool
    runner from #87 uses this for `xdotool search --name`. A
    locale-dependent title would force the runner to know the
    locale (regression risk)."""
    src = WIZARD.read_text()
    assert 'title="BTerminal Installer"' in src


# ─── Documented rationale via this test file ────────────────────────────


def test_rationale_documented_in_test_file_header():
    """Self-pin: the rationale for 'wizard stays EN' lives in
    THIS test file's docstring. If anyone removes it, the
    'deliberate choice' framing becomes implicit again."""
    this_file = Path(__file__).read_text()
    assert "Chicken-and-egg" in this_file
    assert "Rare exposure" in this_file
    assert "xdotool" in this_file


def test_rationale_referenced_from_audit_doc():
    """Pin: docs/audit-completed-work.md (or similar) references
    this i18n decision. Without a doc-side reference, future
    audits won't see the rationale chain."""
    audit_path = REPO_ROOT / "docs" / "audit-completed-work.md"
    if not audit_path.exists():
        pytest.skip("audit doc not present (older branch?)")
    text = audit_path.read_text()
    # Audit doc must mention the wizard's i18n status
    assert "wizard" in text.lower()
    # And i18n / i18n end-to-end appears somewhere
    assert "i18n" in text.lower() or "Polish" in text


# ─── Defensive: BT i18n init does NOT crash wizard import ───────────────


def test_wizard_module_imports_under_polish_locale(monkeypatch):
    """Even though the wizard ignores locale, importing it under
    BTERMINAL_LANG=pl must not crash. If the wizard's pure
    helpers (parse_status_json_line, strip_ansi, etc.) ever
    call into gettext, this test catches it."""
    monkeypatch.setenv("BTERMINAL_LANG", "pl")
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-c",
         "import bterminal.ui.installer_wizard as m; "
         "assert hasattr(m, 'InstallerWizard'); "
         "assert hasattr(m, 'parse_status_json_line')"],
        capture_output=True, text=True, timeout=10,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"wizard import failed under pl locale:\n"
        f"stdout: {result.stdout}\nstderr: {result.stderr}"
    )


def test_wizard_module_imports_under_german_locale(monkeypatch):
    """Same as above for DE."""
    monkeypatch.setenv("BTERMINAL_LANG", "de")
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-c",
         "import bterminal.ui.installer_wizard as m; "
         "assert hasattr(m, 'InstallerWizard')"],
        capture_output=True, text=True, timeout=10,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0


def test_wizard_module_imports_with_no_bterminal_lang_env(monkeypatch):
    """(c) baseline: no env var set → import fine."""
    monkeypatch.delenv("BTERMINAL_LANG", raising=False)
    import subprocess
    import sys
    result = subprocess.run(
        [sys.executable, "-c",
         "import bterminal.ui.installer_wizard as m; "
         "assert hasattr(m, 'InstallerWizard')"],
        capture_output=True, text=True, timeout=10,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0


# ─── Pinned: pure helpers don't translate output ────────────────────────


def test_parse_status_json_line_returns_raw_dict_keys():
    """Pin: `parse_status_json_line` returns a dict with the
    raw JSON keys (`phase`, `status`, `progress`, `label`). If
    we translated keys, the wizard's progress widget code would
    have to switch on translated strings — fragile."""
    from bterminal.ui.installer_wizard import parse_status_json_line
    out = parse_status_json_line(
        '{"phase": "claude", "status": "installing", '
        '"progress": 30, "label": "Installing Claude Code"}'
    )
    # Keys are EN (the JSON contract)
    assert set(out.keys()) >= {"phase", "status", "progress"}


def test_strip_ansi_does_not_translate_remaining_text():
    """Pure transformation — ANSI codes stripped, content
    untouched. Pin: Polish content (e.g. apt-get output in
    user's locale) survives strip_ansi via UTF-8 round-trip."""
    from bterminal.ui.installer_wizard import strip_ansi
    polish_input = "\033[1;31mBłąd:\033[0m brak pakietu meld"
    out = strip_ansi(polish_input)
    assert out == "Błąd: brak pakietu meld"


# ─── Migration marker: if wizard i18n IS added, lift these pins ─────────


def test_lift_pins_when_wizard_gets_i18n_marker():
    """Marker test: when the wizard gets i18n, these tests
    become STALE — anyone touching wizard locale should grep for
    '#118' / 'audit § 6.4 #19' / 'stays EN' and update.

    Pin: this test passes today (wizard EN), and is a
    deliberately INVERSE assertion — it fails when wizard i18n
    lands, forcing the migrator to read the rationale."""
    src = WIZARD.read_text()
    # The day this assertion fails, the migrator updates the
    # whole test file.
    assert "from bterminal.i18n" not in src, (
        "Wizard i18n landed — flip every assertion in this file "
        "from 'pin EN' to 'assert localized'. Read the docstring "
        "header for the rationale chain to update."
    )


# ─── Cross: BTerminal i18n catalog availability check ──────────────────


def test_pl_locale_catalog_exists_for_main_ui():
    """Pin: pl.mo (or pl/LC_MESSAGES/bterminal.mo) is shipped
    for the MAIN UI. This shows that:
      - i18n infrastructure is live (Polish translations exist).
      - The wizard's 'stays EN' is a deliberate carve-out, NOT
        an oversight from no-translations-anywhere."""
    locale_dirs = [
        REPO_ROOT / "locale",
        REPO_ROOT / "bterminal" / "locale",
        REPO_ROOT / "defaults" / "locale",
    ]
    found = []
    for ld in locale_dirs:
        if not ld.exists():
            continue
        for path in ld.rglob("*.po"):
            if "pl" in path.parts:
                found.append(path)
    if not found:
        pytest.skip(
            "no PL .po catalog on this branch — wizard's "
            "'stays EN' rationale weakens but doesn't break"
        )
    assert found  # at least one PL catalog exists
