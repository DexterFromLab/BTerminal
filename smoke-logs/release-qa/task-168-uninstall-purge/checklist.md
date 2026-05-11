# Task #96 (#168) — Uninstall --purge via wizard

**Date:** 2026-05-08
**Tester:** Claude (Opus 4.7 1M)
**VM:** vm-test (Mint, michal@192.168.0.123)
**Methodology:** docs/release-qa-process.md (#164)

---

## Cel

Verify że Tools → Install dependencies → Uninstall (Purge **CHECKED**)
usuwa BT files **AND** `~/.config/bterminal/` + `~/.claude-context/`.

## Pre-state (verified)

- [x] BT v1.3.0 (dev source) reinstalled po #167
- [x] License zaakceptowana
- [x] `~/.config/bterminal/ai_sessions.json` zawiera "PurgeTestSession_168"
- [x] `~/.claude-context/test_marker_purge168.txt` istnieje (test marker)
- [x] BT spawned, REST :7780 healthy

## Kroki + Screenshot evidence

| # | Step | Screenshot | Visual review |
|---|------|------------|---------------|
| 0 | Pre-state: BT running, sidebar Sessions widoczna | `00-pre-state-bt-running` | ✓ "BTerminal — Terminal [DEBUG-REST :7780]" |
| 1 | Tools → Install deps → Wizard Step 1 | `01-installer-wizard-step1` | ✓ Welcome page z 3 radio buttons + license |
| 2 | After Uninstall radio + license accept + Next → Step 2 | `02-step2-confirm-default` | ✓ **Step 2 of 4: Confirm what to remove** + checkbox UNCHECKED domyślnie |
| 3-7 | Multiple click attempts to check the purge checkbox | `03..08-*` | iteration — mouse coords nie matched checkbox bezpośrednio; finalnie click na label text (X=450) sfocusował → `Space` przełączył |
| 8 | **Checkbox CHECKED** | `09-purge-checkbox-CHECKED-FINAL` | ✓ "**☑ Also delete my user data (sessions, ctx + tasks DB)**" — checkbox NIEBIESKI ✓ |
| 9 | Click Uninstall → button | `10-uninstall-summary-purge` | ✓ **Step 4 of 4: Summary — "Uninstall finished."** + [SUMMARY] block + Save report / Open logs / Back / **Close** |
| 10 | Click Close | `11-after-close` | wizard zamknięty |

## Bug znaleziony przy iteration

**Mouse click na checkbox circle (X≈289-290) nie trafia w hit-target.**

GTK CheckButton ma hit-target ROZSZERZONY na cały label, NIE tylko circle.
Klikanie tylko circle (X≈289) NIE rejestruje. Solution:
1. Click na label text (X≈450, gdzieś w środku tekstu) → fokus na checkbox
2. Press Space → toggle

To jest **GTK widget design** — checkbox toggleable przez:
- Click anywhere on label
- Tab to focus + Space  

To wzorzec różny od radio button który ma click-on-circle behavior.

## Post-purge verification (ssh probes — wszystkie ✓ REMOVED)

| Check | Wynik |
|-------|-------|
| `~/.local/share/bterminal/` | ✓ ABSENT (BT files removed) |
| `~/.local/bin/bterminal` | ✓ ABSENT (CLI symlink removed) |
| **`~/.config/bterminal/`** | ✓ **ABSENT** (PURGE — sessions/options/install logs removed) |
| **`~/.claude-context/`** | ✓ **ABSENT** (PURGE — ctx + tasks SQLite DB removed) |
| `ai_sessions.json` | ✓ unreachable (parent dir gone) |
| `test_marker_purge168.txt` | ✓ unreachable (parent .claude-context gone) |

## Comparison vs #167 (NO --purge)

| Item | #167 (no purge) | #168 (purge) |
|------|-----------------|--------------|
| `~/.local/share/bterminal/` | ✓ removed | ✓ removed |
| `~/.local/bin/bterminal` | ✓ removed | ✓ removed |
| `~/.config/bterminal/` | **PRESERVED** | **REMOVED** |
| `~/.claude-context/` | **PRESERVED** | **REMOVED** |
| ai_sessions.json | preserved | gone |

Difference confirms purge checkbox semantics correct.

## Acceptance checklist

- [x] Wszystkie screenshoty istnieją i niepuste
- [x] Każdy screenshot przejrzany (Read tool)
- [x] Pre-state matched (BT installed + ai_sessions.json + .claude-context test marker)
- [x] Wizard Step 1 Welcome → Uninstall radio + license accept
- [x] Wizard Step 2 Confirm → checkbox **CHECKED** (visual ✓ niebieski)
- [x] Wizard Step 4 Summary "Uninstall finished" + Close
- [x] Post-state: `~/.local/share/bterminal/` REMOVED
- [x] Post-state: **`~/.config/bterminal/` REMOVED** (purge ✓)
- [x] Post-state: **`~/.claude-context/` REMOVED** (purge ✓)
- [x] No FATAL/Traceback markers w install logs

## Cleanup post-test

VM stan: BT completely removed including user data. Gotowe dla #169 (Fix flow per scenario) — wymaga reinstall.

## Verdict

**PASS** — Tools → Install dependencies → Uninstall + **Purge CHECKED**
zachowuje się dokładnie jak spec wymagał:
1. Wizard Step 1 → Step 2 → checkbox **"Also delete my user data" CHECKED**
2. Po Uninstall → Summary "Uninstall finished" + Close
3. Post-state: BT **AND** user data REMOVED (`~/.config/bterminal/` +
   `~/.claude-context/` oba gone)

Methodology #164 spełniona: 12 screenshotów + Read-tool review każdego
+ ssh probes weryfikujące każdy element acceptance + checklist.

GTK checkbox hit-target bug udokumentowany dla future testers (use Space
on focused checkbox, NIE click circle).
