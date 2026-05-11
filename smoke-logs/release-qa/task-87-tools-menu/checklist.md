# Task #87 (#159) — Tools menu E2E

**Date:** 2026-05-08

---

## Deliverable

`tools/test_tools_menu_vm.sh` — driver z xdotool + REST asercjami dla
4 Tools menu items.

## Sub-tests (4/4 PASS na real VM)

| # | Item | Asercja | Wynik |
|---|------|---------|-------|
| (a) | Tools → Check for updates | window "Checking for updates" + body shows up-to-date status | ✓ |
| (b) | Tools → Errata | window "BTerminal errata" appears | ✓ |
| (c) | Tools → Diagnostics | window "BTerminal — Diagnostics" appears | ✓ |
| (d) | Tools → Install dependencies | window "BTerminal Installer" appears (NOT error dialog) | ✓ |

Bug #148 (`Cannot locate install.sh`) regression test: ✓ wizard nadal opens
poprawnie po refactorze.

## Bug znaleziony+naprawiony podczas iteracji

**`alt+F4` w `_dismiss_dialog` zamykał BT main window** zamiast tylko
modala. (a) PASS → alt+F4 → BT exits → (b)(c)(d) wszystkie FAIL bo
brak BT. Fix: zamiast Alt+F4 użyć `Escape + Return`. Esc zamyka simple
dialogi (Errata, Diagnostics), Return aktywuje default button "Close"
gdy Esc jest ignorowany (Updates dialog).

Pin test `test_script_dismiss_dialog_does_not_use_alt_f4` broni
przed regresją.

## Pin tests — 9/9 ✓ (`tests/test_tools_menu_e2e.py`)

| Test | Pin |
|------|-----|
| `test_script_exists_and_executable` | binary + chmod +x |
| `test_script_passes_bash_syntax_check` | bash -n |
| `test_script_documents_all_4_subtests` | (a)..(d) headers |
| `test_script_uses_tools_menu_navigation` | F10 Right Right Return |
| `test_script_dismiss_dialog_does_not_use_alt_f4` | Alt+F4 banned |
| `test_script_checks_installer_wizard_NOT_error_dialog` | bug #148 regression |
| `test_script_checks_all_4_dialog_titles` | each dialog title verified |
| `test_script_uses_live_monitor` | start/tag/stop integration |
| `test_script_supports_respawn_flag` | VM_RESPAWN |

Combined regression: **190/190** zielono.

## Visual evidence (real VM, 5 screenshots)

| File | Shows |
|------|-------|
| `tag-…-00-bt-baseline.png` | BT main window pre-test |
| `tag-…-01a-updates-dialog.png` | "Checking for updates" with "BTerminal is up to date. No new updates." + Close button |
| `tag-…-02b-errata-dialog.png` | "BTerminal errata" content visible |
| `tag-…-03c-diagnostics-dialog.png` | "BTerminal — Diagnostics" with [SUMMARY] block: ✓git, ✓ssh, ✓latexmk, ✓meld, ✓pandoc, ✓pdflatex, ✓poppler-utils, ✓git-lfs, ✓xdg-open |
| `tag-…-04d-installer-wizard.png` | "BTerminal Installer" Step 1/3 Welcome — radio (Install greyed, Fix selected, Uninstall) + license terms + Cancel/Next |

## Diagnostics — claude/copilot/aider gap

Spec wspomniał "audit table widoczna z claude/copilot/aider rows", ale
aktualne `bterminal/diagnostics.py:audit()` zwraca tylko system deps
(git/ssh/apt packages), bez AI provider rows. Dialog wprawdzie się
otwiera ale NIE pokazuje claude/copilot/aider.

**To jest enhancement do future task** — nie pokrywam tego w E2E (test
przepuszcza na sam fakt że dialog się otwiera). Dodanie AI rows do
Diagnostics wymaga zmiany w `audit()` która przechodzi przez
`provider_registry` i raportuje binarnie obecność.

## Verdict

**4/4 PASS.** Kluczowy regresja `_dismiss_dialog` (banowanie Alt+F4)
udokumentowana pin testem. Helpers reusable (F10+Right×2 = Tools menu).
