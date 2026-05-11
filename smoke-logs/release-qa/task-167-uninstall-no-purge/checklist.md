# Task #95 (#167) — Uninstall NO --purge via wizard

**Date:** 2026-05-08
**Tester:** Claude (Opus 4.7 1M)
**VM:** vm-test (Mint, michal@192.168.0.123)
**Methodology:** docs/release-qa-process.md (#164)

---

## Cel

Verify że Tools → Install dependencies → Uninstall (Purge UNCHECKED) usuwa
BT files ALE preserves `~/.config/bterminal/` z saved sessions.

## Pre-state (verified)

- [x] BT v1.3.0 (dev source) installed po `tools/vm_sync.sh` + reinstall
- [x] License zaakceptowana (license_accepted_hash w options.json)
- [x] `~/.config/bterminal/ai_sessions.json` zawiera "PreservedSession_167" (active session)
- [x] BT spawned z --debug-rest, REST :7780 healthy (`tag-221114-09-bt-dev-running.png` — sidebar pokazuje PreservedSession_167)

## Bug znaleziony przy setup

**v1.3.0 release `install.sh` NIE MA `--uninstall` flag, ani `do_uninstall` function. Tools menu w v1.3.0 ma TYLKO 2 itemy (Updates, Errata) — Diagnostics + Install dependencies dodane w mojej dev branch (jeszcze nie pushed).**

Workaround: `tools/vm_sync.sh` zsynchronizował dev source na VM, reinstall.
Po sync: install.sh ma `do_uninstall` + `--uninstall` flag, app.py ma 4
Tools menu items.

To jest motivation dla zadania **#110 (branch organization)** — uncommitted
changes muszą być pushed by VM mogła testować pełną funkcjonalność.

## Kroki + Screenshot evidence

| # | Step | Screenshot | Visual review |
|---|------|------------|---------------|
| 0 | Pre-state: BT v1.3.0 dev z PreservedSession_167 | `09-bt-dev-running` | ✓ pełny BT main window, sidebar BTerminal Sessions z entry "PreservedSession_167" |
| 1 | Tools → Install dependencies (F10 Right Right Return Down×3 Return) | `10-installer-wizard-welcome` | ✓ "BTerminal Installer" Step 1/3 Welcome, 3 radio buttons (Install/Fix/Uninstall), license terms widoczne, Cancel + "Repair →" |
| 2 | Click Uninstall radio (mouse 297,333) | `13-after-click-uninstall-radio` | ✓ "Uninstall BTerminal" radio selected (highlighted blue), button changed to "Next →" |
| 3 | Alt+I → license checkbox checked | `15-license-alt-i` | ✓ checkbox "I have read and accept the license terms" CHECKED (✓) |
| 4 | Click Next button → Step 2 Confirm | `16-step2-purge-confirm`, `18-step2-purge-unchecked-screenshot` | ✓ **Step 2 of 4: Confirm what to remove**: list "BTerminal package files / CLI symlinks / Desktop entry / npm Claude Code / npm Copilot CLI" + "By default these are kept: ~/.config/bterminal/, ~/.claude-context/" + checkbox "**Also delete my user data** — UNCHECKED" + Cancel/Back/Uninstall buttons |
| 5 | Click "Uninstall →" button | `19-uninstall-progress` | ✓ **Step 4 of 4: Summary — "Uninstall finished."** + [SUMMARY] block (git/ssh/latexmk/meld/pandoc/pdflatex/poppler-utils/git-lfs/xdg-open) + Save diagnostic / Open logs / Back / **Close** buttons |
| 6 | Click Close (1102, 695) | `20-after-close` | ✓ wizard zamknięty |

## Post-state verification (ssh probes)

| Check | Wynik |
|-------|-------|
| `~/.local/share/bterminal` | ✓ ABSENT (removed by uninstall) |
| `~/.local/bin/bterminal` | ✓ ABSENT |
| `~/.local/bin/claude`, `~/.local/bin/copilot` | ✓ ABSENT (npm symlinks removed) |
| `~/.config/bterminal/` | ✓ **PRESERVED** — 10 plików: ai_sessions.json, consult.json, debug_pid, debug_token, install_errors.json, install.log, install-runs, options.json, repo_path, sidecars |
| `~/.config/bterminal/ai_sessions.json` | ✓ **PRESERVED** — zawiera `[{"id":"preserved-167","name":"PreservedSession_167",...}]` |
| `~/.claude-context/` | ✓ PRESERVED |

## Acceptance checklist

- [x] Wszystkie screenshoty istnieją i są niepuste (>1 KB)
- [x] Każdy screenshot przejrzany (Read tool) przez testera
- [x] Pre-state matched (BT installed + active session)
- [x] Wizard otworzony Step 1 Welcome (3 radio buttons + license)
- [x] Uninstall radio selected (visually verified — niebieski highlight)
- [x] License checkbox checked (visually verified)
- [x] Step 2 Confirm widoczny + Purge checkbox UNCHECKED
- [x] Step 4 Summary "Uninstall finished" + Close button widoczny
- [x] Post-state: BT removed
- [x] Post-state: ~/.config/bterminal PRESERVED + ai_sessions.json zachowane
- [x] Post-state: ~/.claude-context PRESERVED

## Bugi non-blocking (dla future fix)

1. **F10+Right+Right wcześniej (przed dev sync) trafiał w gnome-terminal**
   bo BT v1.3.0 release nie ma 4 Tools menu items. Po sync dev source —
   działa.

2. **xdotool mouse click coordinates** wymagały DPI verification.
   `xdotool getdisplaygeometry` = 1167×900, screenshot dimensions też
   1167×900 → 1:1 mapping. Click X=297, Y=333 to centerl radio circle.

3. **Return key w wizardzie nie aktywował default Next button** —
   musiał być explicit mouse click. To może być GTK3 dialog focus
   issue gdy ostatnia akcja była checkbox toggle.

## Cleanup post-test

- BT proces zabity (uninstall removed bin)
- VM stan: clean (no BT installed) — gotowy dla #168 (Uninstall --purge)
- Test sessions w ~/.config/bterminal preserved (jako evidence dla post-state)

## Verdict

**PASS** — Tools → Install dependencies → Uninstall (no purge) zachowuje
się dokładnie jak spec wymagał:
1. Wizard wykrył installed state, default radio = Fix
2. User wybiera Uninstall radio + accepts license
3. Confirm page pokazuje co będzie removed vs preserved
4. Purge checkbox NIEZAZNACZONY (default)
5. Po Uninstall → Summary "Uninstall finished" + Close button
6. Post-state: BT files removed ALE ~/.config/bterminal preserved

Methodology #164 spełniona: 21 screenshotów (12 z pre-sync issues +
9 z properly working flow) + Read-tool review każdego + ssh probes +
checklist + live-monitor session.
