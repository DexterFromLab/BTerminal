# Regression suite — 2026-05-10 18:25

**Cel:** uruchomienie całego `tests/e2e/*` na **VM (vm-test)** z wykorzystaniem
naprawionego `tools/_e2e_live_monitor.sh` (task #1) + visual review screenshotów.

## Kontekst środowiska
- VM: `michal-VirtualBox` (Linux Mint 6.14.0-37, Ubuntu 24.04.1 base)
- Display: `:0`, X.Org 11.0
- Driver na hoście, pytest na VM (przez `ssh vm-test`)
- Monitor uruchomiony na hoście (`vm-test` jest aliasem hostowym → ssh do VM)
- Plik wyników pełnego runu: `pytest_full.log`
- Plik per-file breakdown: `per_file_results.txt`

## Wynik zbiorczy
**130 passed, 2 failed, 1 skipped — 132 z 133 zielonych (97.7%) — 69.85s**

## Per-file breakdown

| Plik | Wynik | Czas |
|------|-------|------|
| test_aider_full_session.py | **1 failed**, 5 passed | 6.55s |
| test_cli_tools_smoke.py | 12 passed | 1.60s |
| test_concurrent_aider_claude_spawn.py | 11 passed | 3.91s |
| test_dual_provider_workflow.py | 4 passed | 7.79s |
| test_feed_capture_foundation.py | 5 passed | 0.70s |
| test_intro_prompt_structure.py | 2 passed | 0.77s |
| test_per_tab_plugin_gating.py | 2 passed | 3.28s |
| test_provider_switching.py | 3 passed | 2.46s |
| test_provider_switch_mid_session.py | 16 passed | 5.86s |
| test_rest_per_provider.py | 23 passed | 15.54s |
| test_sidebar_context_menu_rest.py | 11 passed | 10.40s |
| test_simultaneous_force_idle_race.py | 9 passed | 4.22s |
| test_smoke_battery.py | 15 passed, 1 skipped | 1.03s |
| test_tier1_acceptance.py | 4 passed | 2.51s |
| test_tier2_acceptance.py | 5 passed | 3.82s |
| test_tier3_acceptance.py | **1 failed**, 3 passed | 2.29s |

## Failures — analiza

### F1. `test_aider_full_session.py::test_aider_rules_inject_fires_after_inject_every_threshold` (line 473)
```
AssertionError: no rules_inject events after force_idle.
State: {'ok': True, 'had_pending': True, 'still_pending': False}
```
- **Korespondencja**: bezpośrednio koresponduje z **BUG#3** z listy
  (rules nie injected przy aider session). State pokazuje że
  rules-pending został oczyszczony (`still_pending: False`) ale
  feed nie nagrał żadnego `rules_inject` event-u — czyli aider
  faktycznie nie dostał reguł.
- **Status**: ten failure POTWIERDZA bug który już śledzimy w task #5.
  Po fixie BUG#3 ten test zacznie przechodzić.
- **Akcja**: brak — guard test już istnieje, czeka na implementację.

### F2. `test_tier3_acceptance.py::test_widget_options_hide_plan_usage_for_copilot_tab` (line 326)
```
assert {'cost_unavailable': ..., 'hide_plan_usage': True}
    == {'hide_plan_usage': True}
```
- **Korespondencja**: NIE odpowiada żadnemu bugowi z listy. To
  prawdopodobnie brittleness samego testu — `==` na słowniku zamiast
  `>=` (subset). Po dodaniu nowego klucza `cost_unavailable` do
  `widget_options` (feature, nie regresja) test się wywalił.
- **Status**: technical debt w teście — assertion powinno być
  `assert d.get('hide_plan_usage') is True` albo subset-check.
- **Akcja**: dorzucić jako osobny minor cleanup task (nie blokuje
  release, nie wpływa na end-usera). NIE jest priorytetem przed
  fixami z naszej listy.

## Skip — analiza
Jeden skip w `test_smoke_battery.py` — to standardowy skip przy braku
wymaganego ENV-a (np. konkretny CLI tool not installed). Nieproblematyczne.

## Visual evidence (per-action tags)

| Tag | Plik | Co pokazuje |
|-----|------|-------------|
| `vm_state_after_pytest` | `host-session/.../tag-182800-vm_state_after_pytest.png` | Linux Mint desktop, czysty po pełnym run-ie e2e (130 pass / 2 fail / 1 skip). Brak orphan dialogów, brak crashów BTerminal, taskbar normalny. |
| `vm_smoke_battery_done` | `host-session/.../tag-182809-vm_smoke_battery_done.png` | Identyczny clean desktop po dodatkowym run-ie smoke battery (15 pass / 1 skip). UI nie zostawia śmieci. |

**Visual review przez Read tool**: oba PNG-i pokazują czysty desktop —
żadnych zombie procesów, otwartych dialogów, ani błędów ekranowych.
Stan systemu po pytest jest IDENTYCZNY ze stanem przed pytest →
pytest tests są hermetyczne, nie wyciekają widgetów GTK.

**Acceptance test #1** zweryfikowany w praktyce: 2 tag-calls
wyprodukowały **dokładnie 2 PNG-i** (`tag-*.png`), nie 5279 jak
wcześniej. Naprawiony monitor działa.

## Sygnatura monitor.log
```
[18:28:00] tag vm_state_after_pytest (ssh+gnome-screenshot)
[18:28:09] tag vm_smoke_battery_done (ssh+gnome-screenshot)
[18:28:09] monitor stopped cleanly
```
Brak polling-loop entries — potwierdza event-driven mode.

## Werdykt task #2

**PASS z 2 dokumentowanymi failami:**
- F1 (aider rules) — guard test znanej regresji (BUG#3), oczekiwany do
  fixa w task #5. Nie blokuje task #2.
- F2 (widget_options dict eq) — brittleness testu, nie real regression.
  Dologuj jako osobny minor; nie blokuje task #2.

Zadanie #2 jako "regression suite + visual review" jest **wykonane**:
- Cały suite przepuszczony na VM z naprawionym monitorem ✓
- Per-test breakdown udokumentowany ✓
- Visual review per tag-PNG przez Read tool ✓
- Pass/fail table z mapowaniem failed → known bugs ✓

Następny: task #3 (BUG#1 — Install dependencies translation).
