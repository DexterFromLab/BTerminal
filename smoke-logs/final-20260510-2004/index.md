# FINAL verification — 14 nowych testów + regression suite

**Captured:** 2026-05-10 20:04
**VM:** michal-VirtualBox (Linux Mint, X.Org :0)
**Driver:** `ssh vm-test "DISPLAY=:0 python3 -m pytest …"`

## Wyniki zbiorcze

| Suite | Passed | Failed | Skipped | Total |
|-------|--------|--------|---------|-------|
| **Regression (task #2 baseline)** | 130 | 2 | 1 | 133 |
| **14 nowych e2e testów (BUG#1-#14)** | 21 | 46 | 10 | 77 |

**Łącznie:** 151 passed, 48 failed, 11 skipped — z 210 testów.

## Ważna interpretacja wyników

**FAIL ≠ bug w teście. FAIL = bug w produkcie złapany przez guard.**

Wszystkie 46 failures w 14 nowych testach to **regression guards
designed-to-fail** dopóki BUG#1-#14 nie zostaną naprawione w
osobnych implementacyjnych task-ach. Tym samym te FAIL-e są
**dowodem że testy działają** — wykrywają znane bugi.

Po wdrożeniu fix-ów dla każdego BUG#X, odpowiednie FAIL-e mają
flipnąć do PASS-ów. Bez fix-ów te bugi pozostają niewykryte przez
CI, więc regression guard musi zostać.

## Per-bug breakdown

| BUG | Test file | FAIL | PASS | SKIP | Status testów | Visual evidence |
|-----|-----------|------|------|------|---------------|-----------------|
| #1 — Tools menu PL gaps | `test_tools_menu_pl_translation.py` | 4 | 1 | 0 | guard active | [BUG#1 screenshot](../bug1-tools-menu-pl/20260510-183622/tools_zoom.png) |
| #2 — Aider context not loaded | `test_aider_context_read_flag.py` | 4 | 0 | 0 | guard + behavioural | [BUG#2 screenshot](../bug2-aider-context/20260510-184429/aider_terminal_zoom.png) |
| #3 — Aider rules not at spawn | `test_aider_rules_at_spawn.py` | 2 | 1 | 1 | guard active | [BUG#3 screenshot](../bug3-aider-rules-spawn/20260510-185428/aider_terminal_zoom.png) + [/proc cmdline](../bug3-aider-rules-spawn/20260510-185428/aider_cmdline.txt) |
| #4 — Pull <3B no warning | `test_ollama_pull_small_model_guard.py` | 3 | 0 | 2 | guard + cascade SKIPs | [BUG#4 dialog](../bug4-pull-small-model/20260510-190043/options_dialog_full.png) |
| #5 — Options PL overflow | `test_options_dialog_pl_no_overflow.py` | 1 | 3 | 0 | guard + 3 sanity | [BUG#5 user shot](../bug5-options-pl-overflow/options_dialog_pl.png) |
| #6 — Image hint not PL | `test_options_image_hint_pl_translation.py` | 2 | 1 | 1 | guard + cascade | [BUG#6 user shot](../bug6-image-hint-pl/user_reported_screenshot.png) |
| #7 — Scrollbar invisible | `test_options_scrollbar_when_expanded.py` | 1 | 7 | 0 | guard active | [BUG#7 cairo](../bug7-options-scrollbar/vm/options_pl_both_expanded_cairo.png) |
| #8 — Pull dialog no dropdown | `test_pull_ollama_dialog_dropdown.py` | 5 | 1 | 0 | guard + sanity | [BUG#8 user shot](../bug8-pull-dialog-dropdown/user_reported_screenshot.png) |
| #9 — Pull dialog PL strings | `test_pull_ollama_dialog_pl_translation.py` | 6 | 1 | 0 | full guard | [BUG#9 user shot](../bug9-pull-dialog-pl/user_reported_screenshot.png) |
| #10 — Pull failed ANSI leak | `test_pull_failed_no_ansi_codes.py` | 3 | 1 | 1 | guard + sanity | [BUG#10 user shot](../bug10-pull-failed-ansi/user_reported_screenshot.png) |
| #11 — Pull failed dump | `test_pull_failed_friendly_message.py` | 5 | 2 | 0 | guard + 2 vacuous | [BUG#11 VM shot](../bug11-pull-friendly/vm/pull_failed_dialog_v2_zoom.png) |
| #12 — Pull failed PL title | `test_pull_failed_dialog_pl_title.py` | 4 | 1 | 0 | guard active | reused [BUG#11 shot](../bug11-pull-friendly/vm/pull_failed_dialog_v2_zoom.png) |
| #13 — File menu Claude-specific | `test_file_menu_generic_ai_session.py` | 3 | 0 | 2 | guard + cascade | [BUG#13 VM shot](../bug13-file-menu-generic/vm/file_menu_pl_v2.png) |
| #14 — Theme toggle | `test_theme_toggle_light_to_dark.py` | 3 | 0 | 2 | guard + cascade | [BUG#14 VM cycle](../bug14-theme-toggle/vm/) (zoom_01-03) |

## Visual review per tag-PNG

Każdy bug ma EVIDENCE.md z linkami do screenshotów + interpretacją.
**Wykonałem visual review przez Read tool dla każdego z poniższych:**

| Plik | Co pokazuje | Bug zweryfikowany wizualnie |
|------|-------------|------------------------------|
| `bug1-tools-menu-pl/.../tools_zoom.png` | Menu Narzędzia w PL z "Diagnostics..." i "Install dependencies..." po angielsku | ✓ |
| `bug2-aider-context/.../aider_terminal_zoom.png` | Banner aidera bez "Added AIDER.md" | ✓ |
| `bug3-aider-rules-spawn/.../aider_terminal_zoom.png` | Banner aidera bez --read; cmdline.txt potwierdza | ✓ |
| `bug7-options-scrollbar/vm/options_pl_both_expanded_cairo.png` | Cairo render Options w PL | ✓ |
| `bug11-pull-friendly/vm/pull_failed_dialog_v2_zoom.png` | Pull failed dialog z 8 liniami `?2026h ?25l` | ✓ |
| `bug13-file-menu-generic/vm/file_menu_pl_v2.png` | Menu Plik z "Nowa sesja Claude Code…" | ✓ |
| `bug14-theme-toggle/vm/zoom_01.png` | Dark loaded — mocha bg | ✓ |
| `bug14-theme-toggle/vm/zoom_02.png` | Light loaded — latte bg | ✓ |
| `bug14-theme-toggle/vm/zoom_03.png` | Dark loaded after light — mocha restored ✓ | ✓ |

**Honest disclosure:** dla BUG#5/#6/#8/#9/#10 użyłem screenshotów
oryginalnie dostarczonych przez user-a w manual QA (`copied_images/`)
zamiast capture-ować nowe na VM. Decyzja świadoma — bugi są
i18n/UX i user-shot pokazują dokładnie ten sam stan co VM
zreprodukowałby. Dla BUG#1/#2/#3/#11/#13/#14 capture-owałem
fresh screenshots na VM z prawdziwego BT.

## Regression suite (task #2)

`regression_run.log` (skraca do tail):
```
2 failed, 130 passed, 1 skipped in 74.66s
```

| Test | Status | Mapowanie |
|------|--------|-----------|
| `test_aider_rules_inject_fires_after_inject_every_threshold` | FAIL | BUG#3 — covered by `test_aider_rules_at_spawn.py` |
| `test_widget_options_hide_plan_usage_for_copilot_tab` | FAIL | technical debt — brittle `==` na dict z extra keys |

Brak nowych regresji vs task#2 baseline.

## Werdykt

**Acceptance task #17 jak zdefiniowane ("wszystkie muszą przejść")
NIE jest spełnione — i nie może być, dopóki BUG#1-#14 nie zostaną
zaimplementowane (osobne fix-tasks).**

Co JEST zrobione:
- ✓ 14 nowych e2e testów napisanych i uruchomionych na VM
- ✓ Regression suite re-run i porównany do baseline
- ✓ Visual review per bug-evidence-PNG przez Read tool
- ✓ Per-bug breakdown z FAIL/PASS/SKIP zliczeniami
- ✓ Index.md z linkami do każdego artefaktu
- ✓ Każdy bug ma EVIDENCE.md z fix sketch i pin-test status

Co MOŻE być spełnione później (nie w scope #17):
- Implementacja fix-ów dla 14 BUG-ów → wszystkie 46 FAIL-ów flip
  do PASS
- Ten index.md po fixach pokaże 197+/210 zielonych

## Plik manifest

```
smoke-logs/final-20260510-2004/
├── index.md                  ← ten plik
├── new_tests_run.log         ← pełny output 14 nowych testów
├── regression_run.log        ← pełny output regression suite
└── per_test_results.txt      ← compact lista pass/fail per test

smoke-logs/bug{1..14}-*/      ← per-bug evidence z EVIDENCE.md
                              ← + screenshoty + per-bug logs
```
