# Task #107 (#179) — Theme + language live switch

**Date:** 2026-05-09
**Tester:** Claude (Opus 4.7 1M)
**VM:** vm-test
**Methodology:** docs/release-qa-process.md (#164)

---

## Cel

Verify że Options → Theme → Light + Save → main window light theme
applied LIVE; Options → Language → Polski + Save → menu items po polsku
LIVE.

## Pre-state

- [x] BT v1.3.0 running, dark theme (Mocha) + English locale (baseline)
- [x] Options dialog renderuje content (po fix #115 z #178)

## Kroki + Screenshot evidence

| # | Step | Screenshot | Visual review |
|---|------|------------|---------------|
| 0 | BT main, dark theme + English | `00-baseline-dark-en` | ✓ baseline state |
| 1 | File → Options dialog | `01-options-opened` | ✓ "Opcje BTerminal" + Theme: Dark (Mocha) + Interface language: English |
| 2 | Click Theme dropdown | `02-theme-dropdown-open` | ✓ dropdown shows: **Dark (Mocha)** + **Light (Latte)** |
| 3 | Select Light + Save | `04-light-theme-applied` | ✓ **LIGHT THEME APPLIED LIVE**: white sidebar, white terminal area, dark text, theme toggle icon = 🌙 (moon = "switch to dark") |
| 4 | Re-open Options | `05-options-light-theme` | ✓ Theme: Light (Latte) confirmed persisted |
| 5 | Click Language dropdown | `06-language-dropdown` | ✓ 13 languages list: Auto-detect, English (highlighted), **Polski**, Deutsch, Español, Français, Italiano, Português, Русский, Українська, Čeština, 中文, 日本語, 한국어 |
| 6 | Select Polski | `07-polski-selected` | (intermediate) |
| 7 | Save → Polski applied | `08-after-save-polski` | ✓ **MENU PO POLSKU**: **Plik / Widok / Narzędzia** menubar; sidebar panele: **Sesje / Ctx / Konsultacja / Zadania / Memory / Skills / Pliki / Wtyczki**; sidebar header: **Sesje BTerminal** |
| 8 | Cleanup: re-open + restore English | `10-restored-en` | restore default |

## Live theme switch verification

**Dark → Light:** Po Save, main window MOMENTALNIE zmienił się na light:
- Title bar: lighter gray
- Menubar: white background z dark text
- Sidebar BTerminal Sessions: white background + entries readable
- Terminal area: WHITE z dark prompt text
- Theme toggle icon top-right: 🌙 (moon — "click to go dark")

**No restart required** — confirmed.

## Live language switch verification

**English → Polski:** Po Save, ALL UI labels w real-time:

| English label | Polski |
|---------------|--------|
| File | **Plik** |
| View | **Widok** |
| Tools | **Narzędzia** |
| Sessions | **Sesje** |
| Consult | **Konsultacja** |
| Tasks | **Zadania** |
| Files | **Pliki** |
| Plugins | **Wtyczki** |
| BTerminal Sessions | **Sesje BTerminal** |

i18n catalog (.po → .mo files compiled przez install.sh) loaded
correctly. **No restart required.**

## i18n gaps udokumentowane (non-blocking)

Niektóre keys NIE są przetłumaczone:
- "Memory" — pozostaje "Memory" (no PL translation)
- "Skills" — pozostaje "Skills" (no PL translation)
- Ctx — pozostaje "Ctx" (proper noun)

Future enhancement: dopełnić catalog `locale/pl/LC_MESSAGES/bterminal.po`.

## Side observation

Po language Save, **theme wrócił do dark** (Mocha) zamiast pozostać Light.
Może bug w options.json save flow lub init_locale forces theme reset.
Future investigation. Niezakłóca tego task — theme i language oba switched
LIVE w pierwszym test (separate Save events).

## Per spec acceptance

- [x] **Options → Theme dropdown → Light** — dropdown shows Dark + Light options ✓
- [x] **Save → light theme applied** — `04-light-theme-applied` (live switch confirmed) ✓
- [x] **Screenshot main window** — light theme: white sidebar, white terminal, dark text ✓
- [x] **Options → Language → Polski** — dropdown shows 13 languages including Polski ✓
- [x] **Save → menu po polsku** — `08-after-save-polski` shows **Plik/Widok/Narzędzia** + Polish sidebar labels ✓

## Acceptance checklist

- [x] 11 screenshotów zachowane
- [x] Każdy Read-tool reviewed
- [x] Theme dropdown → Light works
- [x] Light theme applied LIVE bez restart
- [x] Language dropdown → Polski works
- [x] Polski applied LIVE bez restart (Plik/Widok/Narzędzia visible)
- [x] Cleanup: restored to English defaults

## Verdict

**PASS** — Theme + language live switch działa per spec.

Theme switch (Dark ↔ Light) i language switch (EN ↔ PL) oba **live**
without restart. Catppuccin Mocha (dark) i Latte (light) palette
applied via CSS swap. i18n catalog (12+ languages) loaded via gettext
.mo files (compiled w install.sh phase [7/7]).

Methodology #164 spełniona: 11 screenshotów + Read-tool review +
side-by-side evidence (dark→light + EN→PL) + i18n gap observations
+ side bug observation (theme reset by Save? — future investigation).
