# Task #82 (#154) — Options dialog E2E regression suite

**Date:** 2026-05-08
**Coverage:** #151 (Ollama daemon UI) + #152 (ScrolledWindow) + #153 (collapse-shrink floor)

---

## Approach

Pytest-based E2E uruchamiany **w tym samym repo** pod `xvfb-run`, bez ssh do VM,
bez xdotool. Driver script (inline w teście) buduje OptionsDialog, mutuje stan
expanderów, robi screenshot przez cairo widget.draw() i serializuje wyniki do JSON.
Każda mutacja state'u to osobny "step" — assertions kontrolują geometrię + hierarchy
w każdym z nich.

5 steps cyclu:
1. `baseline_collapsed` — start state
2. `expand_providers` — expand AI Providers
3. `expand_local_models` — expand both
4. `collapse_providers` — collapse AI Providers (Local Models still up)
5. `collapse_both` — collapse both

## Pin tests (`tests/test_options_dialog.py`) — 9/9 ✓

| Test | Asserts |
|------|---------|
| `test_e2e_dialog_built_with_two_expanders_and_buttons` | hierarchy: 2× Expander + Save+Cancel + ≥1 ScrolledWindow |
| `test_e2e_dialog_fits_within_80pct_screen_cap` | h ≤ 80% workarea w każdym step (#152) |
| `test_e2e_dialog_respects_min_height_floor` | h ≥ 480px floor w każdym step (#153) |
| `test_e2e_save_cancel_visible_in_every_step` | Save+Cancel widoczne we wszystkich 5 stepach (#153) |
| `test_e2e_both_expanders_present_after_full_collapse_cycle` | po collapse cycle, oba expandery w drzewie (#153) |
| `test_e2e_local_models_visible_after_collapsing_providers` | collapse Providers ≠ hide Local Models (#153) |
| `test_e2e_scrolled_window_has_correct_policy` | propagate_natural_height=False + min ≥ 400 (#152) |
| `test_e2e_scrollbar_appears_when_both_expanded` | vadj.upper > page_size przy obu expanded (#152) |
| `test_e2e_screenshots_persisted_to_smoke_logs` | każdy step → PNG > 1KB w `smoke-logs/options-e2e/` |

Combined suite (installer pin + options E2E): **147/147** zielono.

## Screenshot evidence (`smoke-logs/options-e2e/`)

- `01-baseline-collapsed.png` — initial state
- `02-providers-expanded.png` — AI Providers expanded, providers list visible
- `03-both-expanded.png` — both sections, content overflows → scrollable
- `04-providers-collapsed-localmodels-still-up.png` — **kluczowy screen**: pokazuje
  jednocześnie wszystkie 3 fixes:
  - **#151**: Start daemon / Stop daemon / Refresh buttons + "running on :11434"
  - **#152**: dialog 600×722 mieści się w cap
  - **#153**: po collapse providers, Local Models + Save+Cancel nadal widoczne
- `05-both-collapsed.png` — final collapse state, dialog respektuje 480px floor,
  buttons + ekspandery na miejscu

## Test discoverability

Tests are skipped automatycznie kiedy `xvfb-run` brak i nie ma DISPLAY — CI bez X
pomija je gracefully zamiast crashować.

---

## Verdict

**Coverage komplet.** Każdy z 3 fixes (#151/#152/#153) ma assertions w E2E i przetrwa
regression. Screenshot bundle daje visual review przy każdym uruchomieniu testów.
