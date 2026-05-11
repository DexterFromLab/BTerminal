# Task #92 (#164) — Release QA Process & Methodology

**Date:** 2026-05-08

---

## Deliverables

| Plik | Cel |
|------|-----|
| `docs/release-qa-process.md` | Canonical methodology — 7 NON-NEGOTIABLE rules, struktura sub-testu, framework, hard requirements, evidence layout, sub-task list (#165-#179) |
| `smoke-logs/release-qa/CHECKLIST_TEMPLATE.md` | Reusable template — tester kopiuje do task folder i wypełnia |
| `tools/release_qa_sanity.sh` | Pre-release gate — failuje gdy brak evidence |
| `tests/test_release_qa_methodology.py` | 17 pin testów walidujących doc + template + sanity |
| `ctx rules add bterminal #8` | VM-only test execution rule (auto-injected co 50 promptów do AI sessions) |

## Rule #7 + #8: VM-only test execution

User explicit demanded: "Typie, testuj w VM" + "BTerminal ma rules który wkleja co 50 promptów". Rezultat:

1. **Rule #7 w `docs/release-qa-process.md`** — canonical spec dla testera (manual/scripted): "WSZYSTKIE TESTY WYKONUJESZ NA VM (vm-test). NIGDY na hoście. xdotool/gnome-screenshot zawsze przez ssh vm-test."
2. **BTerminal rule #8** dodana via `ctx rules add bterminal` — wstrzykuje się automatycznie co 50 promptów do każdej AI session w projekcie bterminal.

**Dwa komplementarne mechanizmy:**
- Rule #7 (doc) = spec dla testera czytającego methodology
- Rule #8 (BTerminal rules) = przypomnienie w trakcie pracy AI session

## Pin tests (17/17 ✓)

`tests/test_release_qa_methodology.py` — 17 testów po 3 kategorie:

**Methodology document (7):**
- doc istnieje + ma actual content
- 7 NON-NEGOTIABLE rules (`1.` … `7.`)
- Rule #7 explicit VM-only mandate (vm-test, NIGDY na hoście, PIN tests exception)
- Wszystkie 15 sub-tasks wymienione (#165-#179)
- Evidence folder structure (`smoke-logs/release-qa/`, `screenshots/`, `checklist.md`, `install.log`)
- Hard requirements (rollback, flock, 3 providery, context files, menu, sidebar)
- Pitfalls z #157-#163 (F10, alt+F4, force=true, sleep 999999, Brak dostępu)

**Checklist template (3):**
- TEMPLATE.md exists
- ≥5 unticked `[ ]` dla testera do filling
- Reminder o NIE robieniu `tasks done` bez wszystkich evidence

**Sanity script (5):**
- exists + executable + bash -n
- Iteruje wszystkie 15 sub-tasks (#165-#179)
- Blokuje release na unchecked items (`grep -cE '^- \[ \]'` + `RELEASE BLOCKED`)
- Blokuje na missing screenshots (find + size +1k)
- Wywołuje pytest tests/ jako final gate

Combined regression: **259/259** zielono.

## Sub-task spec — #165-#179

Każdy ma własny acceptance + screenshot lista (zobacz tabelę w
`docs/release-qa-process.md` § "Sub-tasks"). Streszczenie:

| Range | Categoria | Typowy evidence |
|-------|-----------|-----------------|
| #165-#170 | Installer flows (fresh/update/uninstall/fix/interrupt) | wizard pages + install.log copy |
| #171-#175 | AI providers (3× spawn+prompt + context files + rules inject) | tab banner + UI prompt + visible response |
| #176-#179 | UI features (Tools menu, Sidebar CRUD, Options, Theme/lang) | per dialog + per CRUD step |

## Sanity script behavior (demonstrated on VM)

```
$ ssh vm-test "cd ~/BTerminal && bash tools/release_qa_sanity.sh"
=== Release QA sanity check ===
…
=== Per-task results ===
⚠ #165 — no evidence folder (yet)
⚠ #166 — no evidence folder (yet)
…
⚠ #179 — no evidence folder (yet)

=== Pin suite regression ===
259 passed in 3.0s

============================================================
Release QA sanity:  PASS=0  WARN=15  FAIL=0
============================================================
```

WARN dla #165-#179 = oczekiwane (te sub-tasks nie były jeszcze wykonane).
W `STRICT=1` mode warn → fail; bez strict — doc'uje gap, pin suite
musi być zielona.

## Helpers cumulative dla #165-#179

Wszystkie z #156-#163 plus:
- `tools/release_qa_sanity.sh` — pre-release gate
- `smoke-logs/release-qa/CHECKLIST_TEMPLATE.md` — reusable
- `docs/release-qa-process.md` — canonical methodology

## Verdict

**Methodology document + tooling complete.** 17/17 pin tests; sanity
script run-and-tested na VM (rule #7+#8 enforce); wszystkie 15
sub-tasks (#165-#179) udokumentowane z acceptance criteria.

User feedback przyjęte:
- "testuj w VM" → Rule #7 + BTerminal rule #8 added
- "BTerminal ma rules system" → użyto `ctx rules add` zamiast tylko
  pin testu

Następny etap: kolejne auto-trigger task (#165 — Install fresh) w
ramach metodologii dostarczonej w tym task.
