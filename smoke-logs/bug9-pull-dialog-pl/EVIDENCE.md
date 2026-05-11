# BUG#9 evidence — Pull Ollama dialog strings not translated to PL

**Captured:** 2026-05-10 19:23
**Test:** `tests/e2e/test_pull_ollama_dialog_pl_translation.py`
**Method:** Catalog parsing + xvfb behavioral capture in PL locale

## Empirical capture (xvfb + intercept Gtk.Dialog.run)

```json
{
  "captured": true,
  "title": "Pull Ollama model",
  "button_labels": ["Anuluj", "Pull"],
  "label_texts": ["Model name (e.g. qwen2.5-coder:0.5b, llama3.1:8b):"]
}
```

In PL locale (`init_locale("pl")` applied):
- ✓ "Cancel" → "Anuluj" (catalog has this msgstr)
- ✗ Title "Pull Ollama model" → not translated
- ✗ "Pull" button → not translated
- ✗ Label "Model name (e.g. ...)" → English literal (untranslatable
  f-string)

## Three-layer bug analysis

### Layer 1: Catalog drift (.pot stale)
`bterminal/ui/dialogs/options.py` lines 577, 582 wrap with `_()`:
```python
title=_("Pull Ollama model"),
dlg.add_button(_("Pull"), Gtk.ResponseType.OK)
```
But neither msgid is in `locale/bterminal.pot` (last extracted
2026-05-05; source touched 2026-05-10).

### Layer 2: PL msgstrs missing
Even if extract refreshed .pot, PL `.po` would have empty msgstrs
until manual translation.

### Layer 3: Bare f-string at line 591-593
```python
lbl.set_markup(
    f"Model name (e.g. <tt>{rec_hint}</tt>, "
    f"<tt>llama3.1:8b</tt>):")
```
Python f-string — gettext/xgettext can't extract this. Even with
catalog refresh + manual translation, this label is never translated.

## Visual evidence

`user_reported_screenshot.png` — user manually documented this
(`copied_images/d9050a3d490b.png`). Title English, button "Pull"
English, label English. Only "Anuluj" is Polish.

## Fix recipe (BUG#9 implementation)

### Step 1: refactor f-string into translatable template
```python
# bterminal/ui/dialogs/options.py:591
lbl.set_markup(
    _("Model name (e.g. <tt>{primary}</tt>, "
      "<tt>{fallback}</tt>):").format(
        primary=rec_hint, fallback="llama3.1:8b"
    )
)
```

### Step 2: refresh catalogs
```bash
./tools/i18n.sh extract     # picks up Pull/Pull Ollama model/Model name template
./tools/i18n.sh update      # merges into pl/.../*.po
```

### Step 3: fill PL msgstrs in `locale/pl/LC_MESSAGES/bterminal.po`
```
msgid "Pull Ollama model"
msgstr "Pobierz model Ollama"

msgid "Pull"
msgstr "Pobierz"

msgid "Model name (e.g. <tt>{primary}</tt>, <tt>{fallback}</tt>):"
msgstr "Nazwa modelu (np. <tt>{primary}</tt>, <tt>{fallback}</tt>):"
```

### Step 4: compile
```bash
./tools/i18n.sh compile
```

After all four steps, behavioural test should report:
- title: "Pobierz model Ollama"
- buttons: ["Anuluj", "Pobierz"]
- label: "Nazwa modelu (np. ...)"

## Pin tests (regression guard)

`tests/e2e/test_pull_ollama_dialog_pl_translation.py` — 7 tests:

| Test | Status today | Role |
|------|--------------|------|
| `test_pull_msgids_present_in_pot` | **FAIL** | catalog drift detector |
| `test_pull_msgstrs_translated_in_pl_po` | **FAIL** | empty msgstr detector |
| `test_pull_pl_msgstrs_are_actually_polish` | PASS (vacuously — nothing to check) | activates after fix |
| `test_label_uses_translation_function_not_bare_f_string` | **FAIL** | bare f-string detector |
| `test_behavioural_dialog_title_is_polish` | **FAIL** | runtime title check |
| `test_behavioural_pull_button_label_is_polish` | **FAIL** | runtime button check |
| `test_behavioural_label_does_not_contain_english_model_name` | **FAIL** | runtime label check |

After fix lands: 6 FAILs flip to PASS, all 7 green.

## Cross-reference

- BUG#1 (Tools menu PL gaps) — same catalog drift class
- BUG#6 (Auto-add vision hint PL gap) — same drift
- BUG#8 (Pull dialog dropdown) — same dialog needs ComboBox+LinkButton
  refactor; can land in one PR with this fix
- BUG#12 (Pull failed PL gap) — sibling string in same dialog
  flow; covered by next task
