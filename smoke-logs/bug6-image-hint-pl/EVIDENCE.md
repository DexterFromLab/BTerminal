# BUG#6 evidence — "Auto-add vision hint…" checkbox not translated to PL

**Captured:** 2026-05-10 19:10
**Test:** `tests/e2e/test_options_image_hint_pl_translation.py`
**Class:** catalog drift (same root cause as BUG#1 Tools menu)

## Visual evidence

`user_reported_screenshot.png` (copied from
`copied_images/c2d86a5d1e45.png`, captured by user during manual QA
on 2026-05-10): Options dialog with `language: "pl"` selected.
Polish translations applied to most labels — but the checkbox at
the bottom-right shows literally:

> "Auto-add vision hint when pasting images into Copilot sessions"

While neighboring labels are PL ("Powiedz agentowi AI, w jakim
języku mówię", "Język interfejsu:", "Polski").

## Source-level proof

`bterminal/ui/dialogs/options.py:195-198`:
```python
self._image_hint_check = Gtk.CheckButton(
    label=_("Auto-add vision hint when pasting images "
            "into Copilot sessions"),
)
```

Source correctly wraps the label in `_(...)`. Python concatenates
the two adjacent string literals at compile-time, so the actual
msgid xgettext should pick up is the joined form:

```
Auto-add vision hint when pasting images into Copilot sessions
```

But:
- `locale/bterminal.pot` mtime: **2026-05-05** (75 msgids, NONE
  matching this string)
- `bterminal/ui/dialogs/options.py` mtime: **2026-05-10**

The string was added between the last `extract` run and now. Same
drift pattern as BUG#1 (Diagnostics… / Install dependencies…).

## Fix recipe (BUG#6 implementation)

Combined with BUG#1 fix:
```bash
./tools/i18n.sh extract        # adds the missing msgid to .pot
./tools/i18n.sh update         # merges into all locale/*/.po (empty msgstr)
# Edit locale/pl/LC_MESSAGES/bterminal.po:
#   msgid "Auto-add vision hint when pasting images into Copilot sessions"
#   msgstr "Dodaj wskazówkę vision przy wklejaniu obrazów do sesji Copilot"
./tools/i18n.sh compile        # builds .mo files
```

Suggested PL translation: "Dodaj wskazówkę vision przy wklejaniu
obrazów do sesji Copilot" (≤ 60 chars to avoid layout issues from
BUG#5).

## Pin tests (regression guard)

`tests/e2e/test_options_image_hint_pl_translation.py` — 4 tests:

| Test | Status today | Role |
|------|--------------|------|
| `test_source_still_wraps_image_hint_label_in_translation_function` | PASS | sanity (source still uses `_()`) |
| `test_pot_contains_image_hint_msgid` | **FAIL** | catches catalog drift |
| `test_pl_catalog_has_image_hint_msgstr` | **FAIL** | catches missing PL translation |
| `test_pl_msgstr_is_actually_polish_not_lazy_roundtrip` | SKIP | activates after fix lands |

After fix lands: 2 FAILs flip PASS, SKIP activates and asserts the
msgstr looks Polish (diacritics or known stems).

## Cross-reference

This is a sub-case of the broader catalog-completeness issue:
between 2026-05-05 (last extract) and 2026-05-10 (current source),
multiple `_()` and `N_()` strings were added without re-running
i18n.sh. A single extract+update+fill+compile cycle fixes BUG#1 and
BUG#6 together.
