# BUG#12 evidence — Pull failed dialog title not translated to Polish

**Captured:** 2026-05-10 19:33 (visual evidence reused from BUG#11)
**Test:** `tests/e2e/test_pull_failed_dialog_pl_title.py`

## Visual evidence (real VM)

`../bug11-pull-friendly/vm/pull_failed_dialog_v2_zoom.png` — same
screenshot as BUG#11 since the same dialog is the artifact for both
bugs. With `init_locale("pl")` applied to the BTerminal session
(visible: sidebar "Sesje BTerminal", buttons "Add ▼"/"Edit"/"Delete",
menu "Plik | Widok | Narzędzia"), the result dialog shows literally
"Pull failed" as the bold title.

In a properly translated UI the title would be "Pobieranie nieudane".

## Source-level proof — TRIPLE bug

### Layer 1: bare string literals (no `_()` wrapper)
`bterminal/ui/dialogs/options.py:638`:
```python
text=("Model pulled" if ok else "Pull failed"),
```
This is the `text=` argument of `Gtk.MessageDialog`. Both branches
pass plain Python string literals — gettext never sees them, so
catalog translations are not even consulted at runtime.

Compare with the same dialog's Cancel button: `_("Cancel")` IS
wrapped → renders as "Anuluj" correctly.

### Layer 2: catalog drift (.pot doesn't carry the msgids)
- `locale/bterminal.pot` — neither "Pull failed" nor "Model pulled"
  appears anywhere
- Same root cause as BUG#1, BUG#6, BUG#9 (extract last run
  2026-05-05; source touched 2026-05-10)

### Layer 3: missing PL msgstrs
- `locale/pl/LC_MESSAGES/bterminal.po` — both msgids absent
- Even after Layer 1+2 fix, manual translation entries needed

## Fix recipe (BUG#12 implementation)

**Step 1** — wrap source strings (`bterminal/ui/dialogs/options.py:638`):
```python
# Before:
text=("Model pulled" if ok else "Pull failed"),
# After:
text=(_("Model pulled") if ok else _("Pull failed")),
```

**Step 2** — refresh catalogs:
```bash
./tools/i18n.sh extract && ./tools/i18n.sh update
```

**Step 3** — fill PL msgstrs in `locale/pl/LC_MESSAGES/bterminal.po`:
```
msgid "Pull failed"
msgstr "Pobieranie nieudane"

msgid "Model pulled"
msgstr "Model pobrany"
```

**Step 4** — `./tools/i18n.sh compile`

## Pin tests (regression guard)

`tests/e2e/test_pull_failed_dialog_pl_title.py` — 5 tests:

| Test | Status today | Role |
|------|--------------|------|
| `test_pull_result_dialog_text_uses_translation_function` | **FAIL** | Layer 1 — bare literal detector |
| `test_pot_contains_pull_result_msgids` | **FAIL** | Layer 2 — catalog drift detector |
| `test_pl_catalog_translates_pull_result_msgids` | **FAIL** | Layer 3 — missing msgstr detector |
| `test_pl_msgstrs_actually_polish` | PASS (vacuous) | activates after fix; lazy-roundtrip detector |
| `test_visual_evidence_from_bug11_capture_shows_english_title` | PASS | references the live-VM screenshot from BUG#11 |

After fix lands: 3 FAILs flip PASS, vacuous PASS becomes meaningful → 5/5 green.

## Cross-reference

This is the LAST i18n catalog-completeness bug in the queue. Combined
with BUG#1 (Tools menu), BUG#6 (vision hint), BUG#9 (Pull dialog
strings) — ALL FOUR can be resolved by a single PR:

```bash
# 1. Wrap any remaining bare literals in _()
#    (BUG#9: f-string in lbl.set_markup; BUG#12: bare in text=)
# 2. Refresh catalogs
./tools/i18n.sh extract
./tools/i18n.sh update
# 3. Fill PL msgstrs (15+ new entries)
$EDITOR locale/pl/LC_MESSAGES/bterminal.po
# 4. Compile
./tools/i18n.sh compile
# 5. Verify pin tests for #1, #6, #9, #12 all flip to PASS
python3 -m pytest tests/e2e/test_*pl_translation.py \
                  tests/e2e/test_options_image_hint_pl_translation.py \
                  tests/e2e/test_pull_failed_dialog_pl_title.py
```
