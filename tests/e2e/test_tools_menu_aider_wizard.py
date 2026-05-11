"""Pin tests for BUG#23 — Narzędzia → Konfiguruj lokalny model.

These verify three concerns:
  1. The English source string is present in app.py exactly as it
     was extracted into the .pot (so locale catalogs match the menu).
  2. The Polish translation is present and reads exactly as agreed
     on the smoke-logs/bug23-fix/02 screenshot.
  3. The menu callback hands off to App.open_aider_wizard_tab with
     a session-less config — i.e. the *manual* entry path that must
     NOT auto-relaunch a session afterwards (would surprise the user
     who just wanted to pre-pull a model from Tools menu).
"""
from __future__ import annotations

from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent


def test_english_source_string_present_in_app_py():
    """Pin: app.py must contain the exact N_('Configure local model
    (aider)…') literal — that's what i18n.sh extract picks up. If a
    rename here doesn't update the .pot/.po, gettext silently returns
    the raw msgid (English) on PL hosts, regressing BUG#23."""
    src = (REPO_ROOT / "bterminal" / "app.py").read_text(encoding="utf-8")
    assert 'N_("Configure local model (aider)…")' in src


def test_polish_translation_in_po_catalog():
    """Pin: locale/pl/LC_MESSAGES/bterminal.po must carry the agreed
    PL translation. Anything else (empty msgstr, typo, missing letter)
    falls back to English in BT and the menu item shows up untranslated."""
    po = (REPO_ROOT / "locale" / "pl" / "LC_MESSAGES"
          / "bterminal.po").read_text(encoding="utf-8")
    # Find the entry and assert the msgstr line that follows.
    idx = po.find('msgid "Configure local model (aider)…"')
    assert idx >= 0, "msgid not present — re-run tools/i18n.sh extract+update"
    after = po[idx:idx + 500]
    assert 'msgstr "Konfiguruj lokalny model (aider)…"' in after


def test_compiled_mo_contains_polish_translation():
    """Pin: the .mo binary must reflect the .po contents. Devs sometimes
    edit .po and forget `tools/i18n.sh compile` — without the .mo,
    runtime never sees the new translation."""
    import gettext
    mo_path = (REPO_ROOT / "locale" / "pl" / "LC_MESSAGES"
               / "bterminal.mo")
    assert mo_path.exists(), "run tools/i18n.sh compile"
    with open(mo_path, "rb") as fh:
        cat = gettext.GNUTranslations(fh)
    assert cat.gettext("Configure local model (aider)…") \
        == "Konfiguruj lokalny model (aider)…"


def test_on_open_aider_wizard_calls_open_aider_wizard_tab_without_session():
    """Pin: manual entry callback must invoke open_aider_wizard_tab
    with an *empty* session_config so the post-wizard sentinel doesn't
    accidentally trigger a session relaunch. (BUG#22 path uses a real
    session_id — that's what differentiates the two entry points.)"""
    # Import the unbound method without spinning up GTK/App.__init__.
    from bterminal.app import BTerminalApp
    fake_self = MagicMock(spec=BTerminalApp)
    BTerminalApp._on_open_aider_wizard(fake_self)
    fake_self.open_aider_wizard_tab.assert_called_once_with({})
