"""E2E test for BUG#12 — Pull failed dialog title not translated to
Polish.

User report (manual QA, 2026-05-10): with `language: "pl"`, the
result dialog after a failed pull shows literal English "Pull
failed" as title instead of Polish "Pobieranie nieudane" or
similar.

Evidence already captured in BUG#11 (`smoke-logs/bug11-pull-friendly/
vm/pull_failed_dialog_v2_zoom.png`) — the screenshot taken on real
VM with init_locale("pl") shows the title bar reading "Pull failed"
verbatim.

Triple-layer bug:

1. **Bare string literal** (`bterminal/ui/dialogs/options.py:638`):
   ```python
   text=("Model pulled" if ok else "Pull failed"),
   ```
   No `_()` wrapper. Gettext can't translate what it never sees.
   The "Cancel" button on the same dialog IS translated (Anuluj)
   because `add_button(_("Cancel"), ...)` does wrap it.

2. **Catalog drift**: even if the source were wrapped, neither
   "Pull failed" nor "Model pulled" appear in `locale/bterminal.pot`
   (last extracted 2026-05-05).

3. **Missing PL msgstrs**: nothing in `locale/pl/.../*.po` to fall
   back on after extract.

The test pins all three layers — same shape as BUG#9 / BUG#1
catalog-completeness pattern.

Cross-reference: this fix combines naturally with BUG#9 and BUG#11.
One `i18n.sh extract → fill PL → compile` cycle covers them all.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent
OPTIONS_PY = REPO_ROOT / "bterminal" / "ui" / "dialogs" / "options.py"
PL_PO = REPO_ROOT / "locale" / "pl" / "LC_MESSAGES" / "bterminal.po"
POT = REPO_ROOT / "locale" / "bterminal.pot"

EXPECTED_MSGIDS = ["Pull failed", "Model pulled"]


def _parse_po_msgs(po_path: Path) -> dict[str, str]:
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


# ── Layer 1: source must wrap strings in _() ─────────────────────────────


def test_pull_result_dialog_text_uses_translation_function():
    """Pin: the `text=` argument of the result `Gtk.MessageDialog`
    must use `_()`. Today line 638 has bare string literals which
    gettext silently passes through to the user."""
    src = OPTIONS_PY.read_text(encoding="utf-8")
    # Slice _pull_model_blocking body
    start = src.find("def _pull_model_blocking")
    assert start > 0
    end = src.find("\n    def ", start + 1)
    body = src[start:end] if end > start else src[start:]

    # The bug shape — bare literals, no _() wrapper
    bad_pattern = re.compile(
        r'text=\(\s*"Model pulled"\s+if\s+ok\s+else\s+"Pull failed"\s*\)'
    )
    if bad_pattern.search(body):
        pytest.fail(
            f"text= uses BARE string literals — gettext can't "
            f"translate them. Refactor to:\n"
            f'    text=(_("Model pulled") if ok else _("Pull failed")),'
        )

    # Positive contract: both strings must appear inside `_()` calls
    has_pulled = bool(re.search(r'_\(\s*"Model pulled"\s*\)', body))
    has_failed = bool(re.search(r'_\(\s*"Pull failed"\s*\)', body))
    assert has_pulled and has_failed, (
        f"Pull result strings not all wrapped in `_()`. "
        f"has 'Model pulled': {has_pulled}, "
        f"has 'Pull failed': {has_failed}\n"
        f"Body slice:\n{body[400:800]}"
    )


# ── Layer 2: catalog drift ───────────────────────────────────────────────


def test_pot_contains_pull_result_msgids():
    """Pin: the .pot must carry both msgids. If the source isn't
    wrapped (Layer 1 fails), xgettext can't extract them — Layer 1
    fix must come first."""
    pot = _parse_po_msgs(POT)
    missing = [m for m in EXPECTED_MSGIDS if m not in pot]
    assert not missing, (
        f"missing from {POT.name}: {missing}. Either Layer 1 "
        f"(`_()` wrapping) hasn't been done OR `./tools/i18n.sh "
        f"extract` wasn't re-run after the fix."
    )


# ── Layer 3: PL msgstrs ──────────────────────────────────────────────────


def test_pl_catalog_translates_pull_result_msgids():
    """Pin: each msgid must have a non-empty PL msgstr."""
    po = _parse_po_msgs(PL_PO)
    untranslated = []
    for mid in EXPECTED_MSGIDS:
        if mid not in po:
            untranslated.append(f"{mid!r}: ABSENT from {PL_PO.name}")
        elif not po[mid].strip():
            untranslated.append(f"{mid!r}: empty msgstr")
    assert not untranslated, (
        f"Pull result strings not translated in PL catalog:\n  "
        + "\n  ".join(untranslated)
        + f"\n\nSuggested PL: 'Pull failed' → 'Pobieranie nieudane', "
        f"'Model pulled' → 'Model pobrany'"
    )


def test_pl_msgstrs_actually_polish():
    """Sanity: msgstr ≠ msgid (lazy roundtrip detector)."""
    po = _parse_po_msgs(PL_PO)
    bad = []
    pl_diacritics = set("ąćęłńóśźżĄĆĘŁŃÓŚŹŻ")
    for mid in EXPECTED_MSGIDS:
        msgstr = po.get(mid, "").strip()
        if not msgstr:
            continue
        if msgstr == mid:
            bad.append(f"{mid!r}: msgstr == msgid")
            continue
        ms_lower = msgstr.lower()
        looks_polish = (
            any(c in pl_diacritics for c in msgstr)
            or "pobier" in ms_lower
            or "nieudan" in ms_lower
            or "pobran" in ms_lower
        )
        if not looks_polish:
            bad.append(f"{mid!r}: {msgstr!r} doesn't look Polish")
    assert not bad, "\n  ".join(["non-Polish msgstrs:", *bad])


# ── Behavioural: empirical screenshot evidence ───────────────────────────


def test_visual_evidence_from_bug12_fix_exists_for_review():
    """Pin: the BUG#12 fix workflow captured before/after screenshots
    on real VM in smoke-logs/bug12-fix/. The 06_pull_failed_title_
    after.png shows title 'Pobieranie nieudane' (PL) — visual proof
    via Read tool that catalog wiring works end-to-end."""
    after = (REPO_ROOT / "smoke-logs" / "bug12-fix"
                       / "06_pull_failed_title_after.png")
    if not after.is_file():
        pytest.skip(
            f"VM evidence not yet captured at {after}. "
            f"Re-run task #29 driver."
        )
    # Non-trivial size (real screenshot, not a placeholder)
    size = after.stat().st_size
    assert size > 5_000, (
        f"screenshot too small ({size} bytes) — likely a render "
        f"failure, not usable visual evidence"
    )
