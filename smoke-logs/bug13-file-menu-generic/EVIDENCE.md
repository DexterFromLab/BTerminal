# BUG#13 evidence — File menu has Claude-specific item label

**Captured:** 2026-05-10 19:38-19:42 (real VM run)
**Test:** `tests/e2e/test_file_menu_generic_ai_session.py`

## Visual evidence (real VM, manual driver)

`vm/file_menu_pl_v2.png` — captured via:
1. `nohup python3 -m bterminal` on VM with `language: pl`
2. Wait for window via `xdotool search --name BTerminal`
3. `xdotool key F10` to open menubar (first menu = Plik)
4. `gnome-screenshot -f` on VM
5. PIL crop to focus on menu region
6. scp pull to host
7. Read tool visual review

The screenshot shows the open File menu (Plik) in PL locale with:
- Nowa karta lokalna
- Nowa sesja SSH...
- **"Nowa sesja Claude Code..."** ← THE BUG — Claude-specific label
- Opcje...
- Zamknij aplikację

## Source-level proof

`bterminal/app.py:452`:
```python
file_menu.append(_item(N_("New Claude Code session…"),
                       lambda: self.sidebar._on_add_claude()))
```

Both the LABEL and the CALLBACK hardcode Claude. Source artifact
from when Claude was the only AI provider, not updated after the
T2.1 provider abstraction (Copilot, Aider, Ollama added).

## Catalog state

`locale/pl/LC_MESSAGES/bterminal.po`:
```
msgid "New Claude Code session…"
msgstr "Nowa sesja Claude Code…"
```

The PL translation IS present and valid (this isn't an i18n bug —
it's a UX bug). The msgstr will become orphan after the source
refactor; `i18n.sh update` will mark it `#~` (commented out).

## Fix recipe (BUG#13 implementation)

### Step 1 — generic label + provider-picker callback

`bterminal/app.py:452`:
```python
# Before:
file_menu.append(_item(N_("New Claude Code session…"),
                       lambda: self.sidebar._on_add_claude()))

# After: open the same Add ▼ provider picker the sidebar has
file_menu.append(_item(N_("New AI session…"),
                       lambda: self.sidebar._on_add_ai_picker()))
```

If `_on_add_ai_picker` doesn't exist yet, expose the existing
sidebar Add ▼ flow as a public method:
```python
# bterminal/ui/sidebar.py
def _on_add_ai_picker(self):
    """Open the provider chooser used by the Add ▼ button."""
    self.add_dropdown.popup()
```

### Step 2 — refresh catalogs
```bash
./tools/i18n.sh extract       # picks up "New AI session…"
./tools/i18n.sh update        # comments out orphan "New Claude Code session…"
```

### Step 3 — fill PL msgstr
```
msgid "New AI session…"
msgstr "Nowa sesja AI…"
```

### Step 4 — compile
```bash
./tools/i18n.sh compile
```

After fix, File menu in PL shows:
- Nowa karta lokalna
- Nowa sesja SSH…
- **Nowa sesja AI…** (opens provider picker)
- Opcje…
- Zamknij aplikację

## Pin tests (regression guard)

`tests/e2e/test_file_menu_generic_ai_session.py` — 5 tests:

| Test | Status today | Role |
|------|--------------|------|
| `test_file_menu_does_not_hardcode_claude_in_label` | **FAIL** | catches Claude in label |
| `test_file_menu_has_generic_new_ai_session_label` | **FAIL** | requires generic label |
| `test_file_menu_callback_does_not_hardcode_on_add_claude` | **FAIL** | requires provider-picker callback |
| `test_pl_catalog_translates_new_ai_session_msgid` | SKIP | activates after Layer 1 |
| `test_existing_claude_specific_msgstr_can_be_removed_after_fix` | SKIP | bookkeeping for orphan |

After fix lands: 3 FAILs flip PASS, 2 SKIPs activate → 5/5 green.

## Cross-reference

This is a UX bug, not a catalog bug — distinct class from
BUG#1/#6/#9/#12. Single-line source change + sidebar method
extraction + 2 catalog entries (1 add, 1 mark-orphan).
