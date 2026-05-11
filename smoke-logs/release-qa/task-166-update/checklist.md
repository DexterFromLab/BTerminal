# Task #94 (#166) — Update flow

**Date:** 2026-05-08
**Tester:** Claude (Opus 4.7 1M)
**VM:** vm-test (Mint, michal@192.168.0.123)
**Methodology:** docs/release-qa-process.md (#164)

---

## Cel

Verify że update flow `Tools → Check for updates` na BT v1.2.0 doprowadza
do BT v1.3.0 running, install_errors clean, license re-prompt fires po
update (R52 hash drift).

## Pre-state setup (verified)

- [x] VM purged → install fresh from `git checkout master` reset to v1.2.0 commit (`55afd82`)
- [x] BT installed: `~/.local/share/bterminal/VERSION` = `1.2.0`
- [x] Git state: master = `55afd82` (v1.2.0), origin/master = `5188de6` (v1.3.0) — divergence confirmed via `git log master..origin/master` showing 3 commits
- [x] BT spawned with --debug-rest, REST `version: 1.2.0`

## Kroki + Screenshot evidence

| # | Step | Screenshot | Visual review |
|---|------|------------|---------------|
| 0 | Pre-test desktop | `tag-193101-01-bt-v1.2.0-running.png` | ✓ pierwsza próba (master nie zresetowane → "up to date" — false negative; problem fixed: git reset master → v1.2.0) |
| 1 | "Sprawdzanie aktualizacji — BTerminal jest aktualny" (FALSE NEGATIVE — pre-fix) | `tag-193203-02-checking-for-updates.png` | ✓ widoczne — NIE update available bo master=origin/master oba na v1.3.0 |
| 2 | BT v1.2.0 spawned (post git reset) | `tag-193408-03-bt-v1.2.0-spawned.png` | ✓ "BTerminal — Terminal [DEBUG-REST :7780]", REST: `version: 1.2.0` |
| 3 | **"Aktualizacja BTerminal — Aktualizacja w toku..."** + progress bar + "✓ claude 2.1.133" | `tag-193512-04-update-check-result.png` | ✓ **kluczowa evidence**: update flow ACTIVE, install.sh re-running |
| 4 | Update progress (BT closed mid-update) | `tag-193548-05-update-progress-cont.png` | ✓ Mint desktop only — BT zamknięte mid-update (expected — install.sh restarting BT process) |
| 5 | Post-update state (intermediate) | `tag-193733-06-bt-v1.3.0-running.png`, `tag-193852-07-current-state-after-update.png` | ✓ License Agreement (First run) dialog — R52 license re-prompt po hash drift v1.2.0 → v1.3.0 |
| 6 | BT v1.3.0 main window (license re-accepted) | `tag-194140-09-bt-v1.3.0-main-final.png` | ✓ pełny BT main window, title "BTerminal — Terminal [DEBUG-REST :7780]", File/View/Tools menu, Sessions sidebar, terminal tab z prompt |

## Phase markers (REST + git probes)

```
PRE:    REST.version = 1.2.0      (BT v1.2.0 confirmed)
        git rev-parse master = 55afd82  (v1.2.0 commit)
        git rev-parse origin/master = 5188de6  (v1.3.0 commit)
        UPDATE AVAILABLE: 3 commits ahead

ACTION: Tools → Check for updates → "Update available" auto-accepted
        (drugi Return po menu nav trafił w default Accept button)

DURING: "Aktualizacja BTerminal" dialog z progress bar + claude 2.1.133 ✓ row
        install.sh re-runs (bash) → git pull origin/master → master=5188de6

POST:   ~/.local/share/bterminal/VERSION = 1.3.0   ✓
        git rev-parse HEAD = 5188de65c4860cc86d4e0b07cbf0e38355b7469e   ✓
        License re-prompt: R52 fires (license hash 04e36c08… now matches v1.3.0)
        After license accept: REST.version = 1.3.0   ✓
```

## Acceptance checklist

- [x] Wszystkie screenshoty istnieją i są niepuste (>1 KB każdy) — 9 PNGów
- [x] Każdy screenshot przejrzany (Read tool) przez testera
- [x] Pre-state matched (BT v1.2.0 running, git divergence confirmed)
- [x] Post-state matches expected (VERSION=1.3.0, git HEAD=origin/master)
- [x] No `FATAL` / `Traceback` markers w install logs
- [x] Test acceptance criteria met:
   - [x] Tools → Check for updates trigger ✓
   - [x] Update progress dialog widoczny ✓ (kluczowy screenshot 04)
   - [x] Update completed (VERSION → 1.3.0) ✓
   - [x] BT v1.3.0 running po finalize ✓
- [x] Live monitor session zachowany w `live-monitor/`

## Bugi + observations

### Bug 1 (test setup): `git checkout <commit>` daje detached HEAD → updater nie wykrywa update

**Root cause:** updater porównuje `git rev-parse master` vs `git rev-parse origin/master`. Detached HEAD zostawia `master` na origin/master, więc oba zwracają v1.3.0 — false negative "up to date".

**Fix (test):** `git checkout master && git reset --hard 55afd82` — modyfikuje gałąź master, tym razem updater wykrywa divergence.

**Pin test idea:** dodać helper `_setup_vm_baseline_v1_2_0()` w QA scripts który zawsze używa `git reset` (nie `checkout`).

### Observation 1: "Update available" dialog auto-skipped

Kolejność xdotool keys: `F10 Right Right Return Return` (open Tools menu, Check for updates, then 2nd Return = default Accept button na "Update available" dialog). To skutkowało **brakiem screenshot dialog "Update available"** — od razu progress dialog. Manual tester musi dać delay między klikami żeby uchwycić każdy stan.

### Observation 2: Restart prompt nie wystąpił

Po update install.sh re-spawned BT automatically (BACKUP_DIR restoration + spawn launcher). Brak modal "BT restart required — click OK". Może to być design choice (auto-restart) lub prompt jest skipped w `--headless` mode.

### Observation 3: License re-prompt po update (R52)

VERSION=1.3.0 ma inne LICENSE.en.md niż 1.2.0; license_accepted_hash w `options.json` nie matches → R52 license dialog re-shown. To **expected behavior**.

## Post-state (dla kolejnych sub-tasków)

VM po teście:
- Git: master = origin/master (5188de6 = v1.3.0) — przywrócone do clean state
- BT v1.3.0 installed
- License zaakceptowana (hash matches LICENSE.en.md v1.3.0)
- BT process running (REST :7780)

## Verdict

**PASS** — Update flow z v1.2.0 do v1.3.0 zakończony sukcesem:
- Tools → Check for updates wykrył divergence
- "Aktualizacja BTerminal" dialog widoczny + progress bar
- install.sh re-run zaktualizował git + VERSION
- BT v1.3.0 spawned post-update (REST verify)
- License re-prompt fired (R52 hash drift)

Methodology #164 spełniona: 9 screenshotów + Read-tool review + git/REST probes + checklist + live-monitor session zachowane.

Bug w test setup (detached HEAD) udokumentowany dla przyszłego testera.
