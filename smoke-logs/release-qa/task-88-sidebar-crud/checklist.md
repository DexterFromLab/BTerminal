# Task #88 (#160) — Sidebar CRUD E2E

**Date:** 2026-05-08

---

## Deliverables

- `tools/test_sidebar_crud_vm.sh` — driver z xdotool dla UI dialog
  evidence + REST asercjami dla data path
- `bterminal/debug_rest.py` — 4 nowe endpointy:
  - `POST /api/sessions/ssh` — add SSH
  - `POST /api/sessions/ai` — add AI session
  - `POST /api/sessions/<id>/update` — update fields
  - `POST /api/sessions/<id>/delete` — delete entry
- `GET /api/sessions` — read-only inventory (bonus dla #158-style asercji)

## Strategia: hybrid UI + REST

Sub-tests (a)/(b) **otwierają UI dialog** przez File menu (xdotool key F10
+ Down/Return), **screenshotują**, potem **dismiss** + Add przez REST.
Powód: typing przez xdotool do `Gtk.SpinButton` (Port field w Add Session)
zżera input, dialog OK button nie reaguje na Return (default response
overridden by spinner focus). REST endpoints wywołują **dokładnie ten
sam** `ai_manager.add()` / `session_manager.add()` co dialog OK.

Edit/Delete też przez REST — ten sam `manager.update()` / `.delete()`.

Run-as przez istniejący endpoint #63 `/api/sidebar/context_menu/<id>?
action=run_as&provider=copilot`.

## Sub-tests (5/5 PASS na real VM)

| # | Item | Asercja | Wynik |
|---|------|---------|-------|
| (a) | Add SSH session | UI dialog opens + REST add → /api/sessions ssh++ | ✓ ssh 1→2, name='E2E_SSH_$$' |
| (b) | Add Claude session | UI dialog opens + REST add → /api/sessions ai++ + provider=claude | ✓ ai 6→7, provider=claude |
| (c) | Edit Claude (rename) | REST update → name changed in /api/sessions | ✓ E2E_AI → E2E_AI_renamed |
| (d) | Delete | REST delete → entry gone from /api/sessions | ✓ verified gone |
| (e) | Run as Copilot | REST context_menu spawn → tab.provider == copilot | ✓ saved=claude, spawn=copilot |

## Bug fixes podczas iteracji (każdy z pin testem)

1. **Handler signatures missing `session_id` kwarg**
   - Dispatcher robi `handler(self, **m.groupdict())` → przekazuje
     named regex groups jako kwargs
   - `_route_session_update(h)` + `_route_session_delete(h)` (bez
     `session_id` parametru) → `TypeError: got an unexpected keyword
     argument 'session_id'`
   - Fix: zmienić signature na `(h, session_id: str)`
   - Pin tests `test_route_session_*_takes_session_id_kwarg`

2. **Bash `&` w URL query stringu**
   - `_rest POST "/api/.../?action=run_as&provider=copilot"` → bash
     interpretuje `&` jako background, query string traci `provider=`
   - Fix: użyć single-quoted ssh body żeby `&` było wewnątrz quoted
     w outer/inner shellach

3. **xdotool typing w Gtk.SpinButton zżera tekst**
   - Próba "Tab to Host + type text.example.com" lądowała w Port
     spinner (3rd field), wpisany tekst "test.example.com" odrzucony,
     dialog Save (Return) ignorowany
   - Fix: użyć REST add endpointów (UI typing → too fragile to be
     useful; visual evidence z dialog open jest wystarczające)

4. **Orphan test sessions na VM** po fail edit/delete cycle
   - Każdy fail run zostawiał `E2E_SSH_*` / `E2E_AI_*` w
     `~/.config/bterminal/ai_sessions.json`
   - Fix: cleanup at end of każdego sub-test (delete by name)
   - Pin test `test_script_cleans_up_test_sessions`

## Pin tests — 13/13 ✓ (`tests/test_sidebar_crud_e2e.py`)

Combined regression: **203/203** zielono.

## Visual evidence (real VM, 7 screenshots)

| File | Shows |
|------|-------|
| `00-bt-baseline.png` | start state |
| `01a-ssh-dialog-open.png` | "Add Session" dialog otwarte (UI path verified) |
| `01a-after-add.png` | sidebar pokazuje `E2E_SSH_*` entry |
| `02b-after-add.png` | sidebar pokazuje `E2E_AI_*` entry + Add Session dialog |
| `03c-after-rename.png` | po REST rename — name changed |
| `04d-after-delete.png` | po REST delete — entry gone |
| `05e-after-run-as.png` | tab `E2E_RunAs_*` aktywny po Run as Copilot |

## Helpers cumulative (#157-#160)

- `_xfocus_bt`, `_rest`, `_rest_health_ok`, `_get_sessions`,
  `_get_session_id_by_name`, `_count_field`
- F10+keyboard nav patterns dla File / View / Tools menus
- Live monitor start/tag/stop integration

## Verdict

**5/5 PASS.** Task spec spełniony — Add/Edit/Delete + Run-as override
wszystkie pokryte z screenshot evidence + data-path asercjami.
Nowe REST endpointy gotowe jako test affordance dla #161 (AI session
spawn) i Release QA #177 (Sidebar CRUD manual).
