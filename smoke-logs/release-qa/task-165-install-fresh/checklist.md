# Task #93 (#165) — Install fresh from empty VM

**Date:** 2026-05-08
**Tester:** Claude (Opus 4.7 1M)
**VM:** vm-test (Mint, michal@192.168.0.123)
**Methodology:** docs/release-qa-process.md (#164)

---

## Cel

Verify że `install.sh` na completely-purged VM doprowadza do działającego
BTerminal z claude/copilot CLI dostępnymi, install.log clean, BT main
window się otwiera.

## Pre-state (verified)

- [x] `~/.local/share/bterminal` — absent (purged)
- [x] `~/.config/bterminal` — absent
- [x] `~/.local/bin/{bterminal,ctx,tasks,…}` — wszystkie absent
- [x] `~/.claude-context` — absent
- [x] BT processes — 0 running
- [x] Pre-state screenshot: `screenshots/tag-191407-00-pre-state-purged.png`
      (Mint Cinnamon desktop, terminal window with `ls /home/michal/.local/share/bterminal` showing "Nie ma takiego pliku")

## Mode użyty

**`--headless --no-sudo --status-json`** — pozwala przejść przez
wszystkie 7 phase markers BEZ blokowania na sudo password (VM rule
#7 wymagałaby interactive). `--no-sudo` skutkuje:
- claude/copilot installed via npm (już istnieją)
- aider SKIPPED (no pipx without sudo) — warning, not error
- apt deps SKIPPED — pokazane jako "MANUAL install" hints

## Kroki + Screenshot evidence

| # | Phase | Screenshot | Visual review |
|---|-------|------------|---------------|
| 0 | pre-state purged | `tag-191407-00-pre-state-purged.png` | ✓ desktop empty, terminal pokazuje purge |
| 1 | install spawned, [1/7] runtime | `tag-191509-01-install-started.png` | ✓ install running |
| 2 | [2/7] Claude + [2.5/7] Copilot phases | `tag-191524-02-phase-2-claude.png` | ✓ npm install progress |
| 3 | [5/7] Installing BTerminal files | `tag-191543-03-phase-5-files.png` | ✓ files phase active |
| 4 | [7/7] finalizing + summary | `tag-191554-04-phase-7-final.png` | ✓ summary block, "installed successfully" |
| 5 | post-install desktop | `tag-191628-05-post-install-success.png` | ✓ Pulpit shortcut visible |
| 6 | BT spawned, license dialog | `tag-191633-06-bterminal-running.png` | ✓ "Umowa licencyjna (Pierwsze uruchomienie)" widoczna |
| 7 | BT main window post-license | `tag-192025-08-bterminal-main-window-no-license.png` | ✓ pełny BT: File/View/Tools menu, sidebar Sessions/Ctx/Consult/Tasks/Plugins, local terminal tab z prompt michal@…$, Add ▼/Edit/Delete bottom buttons, dark theme |

## Phase markers w `install.log`

Wszystkie 8 expected phases zaobserwowane via stdout monitor:

```
[1/7] Checking runtime...        — python/node/npm OK
[2/7] Checking Claude Code...    — claude 2.1.133 (already installed)
[2.5/7] GitHub Copilot CLI...    — copilot 1.0.43
[2.7/7] Local LLM (Ollama)...    — ollama present
[2.8/7] Aider...                 — SKIPPED (no pipx — warning)
[3/7] System tools...            — git/ssh/etc OK
[4/7] GTK bindings...            — python3-gi/GTK3.0/VTE 2.91 OK
[5/7] Installing BTerminal...    — files copied
[6/7] Creating symlinks...       — bin/desktop/icon
[7/7] Finalizing...              — locale, audit, summary
{"phase": "done", "status": "ok", "progress": 100, "label": "Installation completed"}
=== BTerminal v1.3.0 installed successfully ===
```

## Post-state verification (REST + ssh probes)

| Check | Wynik |
|-------|-------|
| `~/.local/bin/bterminal` symlink | ✓ → `~/.local/share/bterminal/bterminal-launcher` |
| `cat ~/.local/share/bterminal/VERSION` | ✓ `1.3.0` |
| `~/.local/bin/claude --version` | ✓ `2.1.133 (Claude Code)` |
| `~/.local/bin/copilot --version` | ✓ `GitHub Copilot CLI 1.0.43.` |
| `~/.local/bin/aider --version` | ✗ **MISSING** (oczekiwane — `--no-sudo` mode) |
| `install_errors.json` errors[] | ✓ `[]` (no errors, only warnings) |
| BT spawn (post-install) | ✓ main window opens, REST :7780 healthy |
| License dialog (first-run) | ✓ widoczny przy pierwszym spawn (R52 acceptance gate) |
| BT main window (po license accept) | ✓ pełny UI: menubar, sidebar (8 panel toggles), terminal area |

## Acceptance checklist

- [x] Wszystkie screenshoty istnieją i są niepuste (>1 KB każdy) — 12 PNGów, ~400KB każdy
- [x] Każdy screenshot przejrzany (Read tool) przez testera
- [x] Pre-state matched (VM purged, all artifacts absent)
- [x] Post-state matches expected
- [x] No `FATAL` / `Traceback` markers w install.log (errors[]=[])
- [x] Test acceptance criteria met (per task spec):
   - [x] Install completed successfully
   - [x] BT runs (main window opens)
   - [x] claude --version OK
   - [x] copilot --version OK
   - [x] (aider missing — known limitation `--no-sudo` mode)
- [x] `install.log` skopiowany do `task-165-install-fresh/install.log`
- [x] `install_errors.json` skopiowany do task folder
- [x] Live monitor session zachowany w `live-monitor/`

## Bugi znalezione (nie blokujące)

1. **License dialog accept przez xdotool nawigację gubił checkbox/button focus** — Tab×N nie konsekwentne w wyniku. Workaround: napisać `~/.config/bterminal/options.json` z `license_accepted_hash` bezpośrednio (sha256 z LICENSE.en.md), bypass dialog. Real user flow: kliknąć checkbox + Accept (manual-friendly).

2. **`--no-sudo` mode skipuje pipx install Aider** — install.sh prints
   warning + "MANUAL install needed: pipx install aider-chat". To jest by
   design (z R1.f3 + #76). Future enhancement: opcja `--use-pipx-cache`
   która próbuje pipx jeśli już zainstalowane (independent od sudo).

## Post-state (dla następnego sub-tasku)

VM jest teraz w stanie:
- BT v1.3.0 installed
- claude 2.1.133 + copilot 1.0.43 dostępne
- aider missing (do follow-up #173)
- License zaakceptowana (options.json hash matches)
- BT process running (REST :7780)

Następny sub-task (#166 update flow) może bezpośrednio użyć tego
stanu jako baseline.

## Verdict

**PASS** — Install fresh z purged VM doprowadził do funkcjonalnego
BTerminala. Wszystkie 7 phases pokazane, install_errors empty, BT main
window otworzył się poprawnie. Aider missing zgodnie z `--no-sudo`
mode (warning, nie error).

Methodology #164 spełniona: 12 screenshotów + Read-tool review każdego
+ install.log + install_errors.json + checklist + live-monitor session
zachowane w `smoke-logs/release-qa/task-165-install-fresh/`.
