# Task #NNN — <task name>

**Date:** YYYY-MM-DD
**Tester:** Claude / <name>
**VM:** vm-test
**Pre-state:** <verified state before test>

> Ten plik jest TEMPLATE. Skopiuj do `smoke-logs/release-qa/task-NNN-<name>/checklist.md`
> i wypełnij konkretnie. Wszystkie sekcje muszą być uzupełnione przed `tasks done`.

---

## Cel

<one sentence — np. "Verify że `install.sh` na czystym VM doprowadza
do działającego BT z claude/copilot CLI">

## Pre-state

- [ ] VM purged (no `~/.local/share/bterminal`)
- [ ] No claude/copilot/aider in `~/.local/bin`
- [ ] No `~/.config/bterminal/`
- [ ] (innym warunki specyficzne dla tego sub-tasku)

## Kroki

| # | Action | xdotool / shell command | Screenshot evidence | Visual review (Read tool) |
|---|--------|-------------------------|---------------------|---------------------------|
| 1 | ssh + run install.sh | `ssh vm "cd BTerminal && ./install.sh"` | `screenshots/HHMMSS-01-pre.png` | ✓ desktop empty, no BT installed |
| 2 | wizard welcome page | `xdotool search 'BTerminal Installer' …` | `screenshots/HHMMSS-02-welcome.png` | ✓ "Welcome" + 3 radio buttons |
| 3 | accept license | `xdotool key alt+i` (or click checkbox) | `screenshots/HHMMSS-03-license.png` | ✓ "I have read…" checked |
| 4 | next page (picks) | `xdotool key Return` | `screenshots/HHMMSS-04-picks.png` | ✓ checkboxes for deps visible |
| 5 | … | … | … | … |
| N | summary | observe summary page | `screenshots/HHMMSS-NN-summary.png` | ✓ "Installed successfully" + Open BTerminal |

## Acceptance checklist

- [ ] Wszystkie screenshoty istnieją i są niepuste (>1 KB każdy)
- [ ] Każdy screenshot przejrzany (Read tool) przez testera
- [ ] Pre-state matched (VM was in expected state przed startem)
- [ ] Post-state matches expected (artifacts created/removed)
- [ ] No `FATAL` / `Traceback` markers w install.log
- [ ] Test acceptance criteria met (per task spec — see #164)
- [ ] `install.log` skopiowany do task folder
- [ ] `install_errors.json` skopiowany do task folder (jeśli istnieje)
- [ ] Live monitor session zachowany (jeśli używany)

## Post-state

- [ ] BT installed at `~/.local/share/bterminal/`
- [ ] `~/.local/bin/bterminal` symlink valid
- [ ] (inne warunki post-test)

## Bugi znalezione (jeśli były)

- (any unexpected behavior — task open dla każdego)

## Verdict

**PASS** / **FAIL** — <one sentence summary z linki do najważniejszego
screenshota>

---

## Notes for next tester

(Anything quirky observed that wasn't a bug but is worth knowing —
np. "wizard takes 2-3s to appear after install.sh start", "REST :7780
needs ~5s after BT spawn before /api/health responds")
