"""E2E test for BUG#6 — 'Auto-add vision hint when pasting images
into Copilot sessions' checkbox label not translated to Polish.

User report (manual QA, 2026-05-10): visible in the same Options
dialog screenshot that triggered BUG#5 — the checkbox label appears
literally as English text even though the user picked Polish.

Same root cause class as BUG#1 (Tools menu items missing from
catalog): the source uses `_("Auto-add vision hint…")` correctly,
but the string was added to `bterminal/ui/dialogs/options.py:196`
AFTER the last `./tools/i18n.sh extract` run. The .pot file
mtime (2026-05-05) precedes the source mtime (2026-05-10), so this
msgid never reached `locale/pl/LC_MESSAGES/bterminal.po`. At runtime
gettext silently returns the English msgid.

The test asserts:
  1. Source still wraps the label in `_()` (regression guard against
     future un-marking)
  2. The msgid string appears verbatim in `locale/bterminal.pot`
  3. The msgid has a non-empty msgstr in `locale/pl/.../bterminal.po`
  4. The msgstr is actually Polish (contains diacritics or known
     Polish stems) — not a roundtrip of the English source

Run on VM is not strictly required (this is a catalog-completeness
test that runs anywhere) but the user's screenshot from VM is the
visual ground truth that motivates the fix.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OPTIONS_PY = REPO_ROOT / "bterminal" / "ui" / "dialogs" / "options.py"
PL_PO = REPO_ROOT / "locale" / "pl" / "LC_MESSAGES" / "bterminal.po"
POT = REPO_ROOT / "locale" / "bterminal.pot"

# The full msgid as it appears after string concatenation in source.
# Source:
#   _("Auto-add vision hint when pasting images "
#     "into Copilot sessions")
# Python concatenates these at compile time; xgettext sees the
# joined form.
EXPECTED_MSGID = (
    "Auto-add vision hint when pasting images into Copilot sessions"
)


def _parse_po_msgs(po_path: Path) -> dict[str, str]:
    """Return {msgid: msgstr} from a .po/.pot file. Multi-line
    msgids/msgstrs are joined."""
    if not po_path.is_file():
        return {}
    out = {}
    cur_id = cur_str = None
    in_id = in_str = False
    for raw in po_path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if line.startswith("msgid "):
            if cur_id is not None:
                out[cur_id] = cur_str or ""
            cur_id = _unquote(line[len("msgid "):])
            cur_str = ""
            in_id, in_str = True, False
        elif line.startswith("msgstr "):
            cur_str = _unquote(line[len("msgstr "):])
            in_id, in_str = False, True
        elif line.startswith('"') and line.endswith('"'):
            chunk = _unquote(line)
            if in_id:
                cur_id = (cur_id or "") + chunk
            elif in_str:
                cur_str = (cur_str or "") + chunk
        elif not line:
            if cur_id is not None:
                out[cur_id] = cur_str or ""
                cur_id = cur_str = None
                in_id = in_str = False
    if cur_id is not None:
        out[cur_id] = cur_str or ""
    return out


def _unquote(s: str) -> str:
    """Strip surrounding quotes + decode gettext escapes; UTF-8 stays as-is."""
    if s.startswith('"') and s.endswith('"'):
        s = s[1:-1]
    out = []
    i = 0
    while i < len(s):
        c = s[i]
        if c == "\\" and i + 1 < len(s):
            nxt = s[i + 1]
            mapping = {"n": "\n", "t": "\t", "r": "\r",
                       '"': '"', "\\": "\\"}
            if nxt in mapping:
                out.append(mapping[nxt])
                i += 2
                continue
        out.append(c)
        i += 1
    return "".join(out)


# ── Static checks ────────────────────────────────────────────────────────


def test_source_still_wraps_image_hint_label_in_translation_function():
    """Sanity: regression guard against someone un-marking the
    string. The whole bug is moot if the source label uses a bare
    string literal — gettext can't translate what it never sees."""
    src = OPTIONS_PY.read_text(encoding="utf-8")
    # Match either form: _("Auto-add vision hint…") or with line
    # continuation across two strings.
    pattern = re.compile(
        r'_\(\s*"Auto-add vision hint when pasting images\s*"\s*'
        r'"\s*into Copilot sessions"\s*\)'
    )
    pattern_single = re.compile(
        r'_\(\s*"Auto-add vision hint when pasting images '
        r'into Copilot sessions"\s*\)'
    )
    assert pattern.search(src) or pattern_single.search(src), (
        "expected `_('Auto-add vision hint when pasting images "
        "into Copilot sessions')` in options.py — either it was "
        "removed or the string was un-marked"
    )


def test_pot_contains_image_hint_msgid():
    """Catches the catalog-drift root cause: source uses _() but
    extract was not re-run. .pot is the master catalog xgettext
    builds; if the msgid is absent here, no language can have a
    msgstr for it."""
    pot = _parse_po_msgs(POT)
    assert EXPECTED_MSGID in pot, (
        f"{EXPECTED_MSGID!r} not in {POT.name}. Run "
        f"`./tools/i18n.sh extract` to refresh the master catalog. "
        f"Catalogs in pot: {len(pot)} entries."
    )


def test_pl_catalog_has_image_hint_msgstr():
    """Pin: PL .po must have a non-empty msgstr for this msgid.
    Empty string → gettext returns the msgid (English) at runtime,
    which is exactly the user-reported behaviour."""
    po = _parse_po_msgs(PL_PO)
    assert EXPECTED_MSGID in po, (
        f"{EXPECTED_MSGID!r} ABSENT from {PL_PO.name}. After fixing "
        f"the .pot drift (prev test), run `./tools/i18n.sh update` "
        f"to merge the new msgid into pl/.../*.po."
    )
    msgstr = po[EXPECTED_MSGID].strip()
    assert msgstr, (
        f"msgstr for {EXPECTED_MSGID!r} is empty in {PL_PO.name}. "
        f"Fill it in (e.g. 'Dodaj wskazówkę vision przy wklejaniu "
        f"obrazów do sesji Copilot') and `./tools/i18n.sh compile`."
    )


def test_pl_msgstr_is_actually_polish_not_lazy_roundtrip():
    """If msgstr equals the English msgid (or is just the English
    text), gettext returns English at runtime — defeats the point.
    Real translations contain Polish diacritics or characteristic
    stems."""
    po = _parse_po_msgs(PL_PO)
    if EXPECTED_MSGID not in po:
        pytest.skip("msgid not present yet — see prior test")
    msgstr = po[EXPECTED_MSGID].strip()
    if not msgstr:
        pytest.skip("msgstr empty — see prior test")
    assert msgstr != EXPECTED_MSGID, (
        f"msgstr ({msgstr!r}) equals the English msgid — that's a "
        "pass-through, not a translation. gettext will still hand "
        "back English at runtime."
    )
    pl_diacritics = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
    pl_stems = ("dodaj", "wkleja", "wskaz", "obraz", "wizji", "vision",
                "kopilot", "copilot")
    has_diacritic = any(c in pl_diacritics for c in msgstr)
    has_pl_stem = any(s in msgstr.lower() for s in pl_stems)
    assert has_diacritic or has_pl_stem, (
        f"msgstr {msgstr!r} doesn't look like Polish — no "
        f"diacritics and no expected stems. Translation may be "
        f"placeholder text or a different language was put in."
    )
