# BUG#1 evidence — Tools menu items not translated to Polish

**Captured:** 2026-05-10 18:36-18:40
**VM:** michal-VirtualBox (Linux Mint, X.Org :0)
**BT:** v1.3.0, language=pl

## Visual evidence

`tools_zoom.png` — cropped screenshot of opened Tools (Narzędzia) menu.

What the menu shows:
| Pozycja | Tekst | Status |
|---------|-------|--------|
| 1 | "Sprawdź aktualizacje" | ✓ PL |
| 2 | "Errata..." | ✓ proper noun (PL=EN) |
| 3 | **"Diagnostics..."** | ✗ angielski, oczekiwany PL |
| 4 | **"Install dependencies..."** | ✗ angielski, oczekiwany PL |

Other menus (Plik, Widok) are fully translated — bug is isolated to
last 2 items of Tools menu.

## Root cause (discovered while writing test)

`bterminal/app.py:_build_menubar` marks all 4 items with `N_("...")`:
```python
tools_menu.append(_item(N_("Check for updates"), ...))     # line 483
tools_menu.append(_item(N_("Errata…"), ...))               # line 484
tools_menu.append(_item(N_("Diagnostics…"), ...))          # line 487
tools_menu.append(_item(N_("Install dependencies…"), ...)) # line 493
```

`tools/i18n.sh extract` correctly has `--keyword=N_` so the strings
SHOULD make it to `locale/bterminal.pot`. But:

- `locale/bterminal.pot` mtime: **2026-05-05 13:51**
- `bterminal/app.py` mtime: **2026-05-10 17:40**

Diagnostics + Install dependencies were added to the source AFTER the
last `extract` run. The catalog never saw those msgids → PL `.po`
never got msgstrs → at runtime `gettext` falls through to msgid
(English).

`grep` confirms: `locale/pl/LC_MESSAGES/bterminal.po` has neither
"Diagnostics…" nor "Install dependencies…" anywhere in the file.

## Fix recipe (for next session, BUG#1 implementation)

```bash
./tools/i18n.sh extract           # pot now contains both
./tools/i18n.sh update            # merges into pl/.../*.po (empty msgstrs)
# Edit pl/.../*.po:
#   msgid "Diagnostics…"          → msgstr "Diagnostyka…"
#   msgid "Install dependencies…" → msgstr "Zainstaluj zależności…"
./tools/i18n.sh compile           # builds .mo
```

## Pin tests (regression guard)

`tests/e2e/test_tools_menu_pl_translation.py` — 5 tests, 4 currently fail:

- `test_menu_msgids_extracted_from_source` — sanity, PASS
- `test_pot_contains_every_menu_msgid` — FAIL (catches catalog drift)
- `test_pl_catalog_translates_every_menu_msgid` — FAIL (catches missing msgstr)
- `test_install_dependencies_msgstr_is_polish_not_english` — FAIL (specific guard)
- `test_diagnostics_msgstr_is_polish_not_english` — FAIL (specific guard)

After fix, all 5 must pass.
