# Task #91 (#163) — Uninstaller edge cases

**Date:** 2026-05-08

---

## Deliverable

`tools/test_uninstaller_edge_vm.sh` — auto-driver dla 5 hostile-condition
scenariuszy uninstall flow. **Zero ryzyka mutacji prawdziwego VM home**:
fast layout-only setup w `/tmp` scratch dirs, `HOME=$SCRATCH` redirection.

## Sub-tests (5/5 PASS na real VM)

| # | Scenariusz | Mechanizm | Wynik |
|---|------------|-----------|-------|
| (a) | Uninstall while concurrent process running | install.sh nie ma BT-running guard; rm-rf usuwa pliki, Linux unlink open files | ✓ uninstall completed + INSTALL_DIR removed |
| (b) | Uninstall --purge with seeded sessions | `rm -rf $CONFIG_DIR $CTX_DIR` + log redirect to `$FINAL_LOG_TMP` | ✓ configs removed, final-log preserved at `/tmp/bterminal-uninstall-final.W16REn.log` |
| (c) | Uninstall with stale lockfile (PID=99999 dead) | flock guard `kill -0 PID` check → wipe stale lockfile | ✓ uninstall completed (silent recovery) |
| (d) | Uninstall with read-only INSTALL_DIR's parent | `rm -rf` cannot unlink subdir when parent is read-only | ✓ no traceback (clean exit, rc=1) |
| (e) | Partial uninstall (dir gone, symlinks linger) | `[[ -d $INSTALL_DIR ]]` guard + per-symlink loop | ✓ orphaned symlinks cleaned up |

## Bugi znalezione+naprawione podczas iteracji

1. **Real `install.sh` w setup zżera 1+ min × 5 scenarios = timeout 360s**
   - Pierwsza iteracja: `_setup_fake_install` wywoływał `bash install.sh
     --no-sudo` aby utworzyć install layout. install.sh w phase [2/7]
     uruchamia `npm install @anthropic-ai/claude-code` (1+ min) +
     `npm install @github/copilot` (1 min) → 2-5 min × 5 scenarios.
   - Test `bc94zx93r` killed po 360s timeout (tylko (a) dokończyło).
   - **Fix**: napisać `_setup_fake_install` który buduje minimalny layout
     bezpośrednio przez `mkdir -p / touch / ln -sf` (~0.5s). Zawiera tylko
     katalogi i symlinki które `do_uninstall` iteruje (CLI symlinks, npm
     dirs, desktop files, configs). Real install.sh wywoływany w 0
     scenariuszach.
   - Pin test `test_setup_helper_does_not_run_real_install_sh`.

2. **BT spawn bez DISPLAY zawiesza Gtk init**
   - `setsid -f bterminal --debug-rest` próbuje Gtk.Application bez X
     server → blocks indefinitely.
   - **Fix**: spawn `sleep 999999` z cwd=$INSTALL_DIR jako "fake BT"
     concurrent process. install.sh nie sprawdza CZY proces to BT,
     zachowanie identyczne.
   - Pin test `test_script_uses_fake_bt_process_not_real_bt`.

3. **Read-only INSTALL_DIR samo nie blokuje rm-rf**
   - `chmod -R a-w $INSTALL_DIR` pozwala `rm -rf` usunąć (bo write perm
     jest na PARENT dir).
   - **Fix**: `chmod a-w $SCRATCH/.local/share` (parent dir).
   - Pin test `test_script_chmods_parent_for_readonly_test`.

## Pin tests — 15/15 ✓ (`tests/test_uninstaller_edge_e2e.py`)

| Test category | Tests |
|---------------|-------|
| Script structure | 4 (exists, syntax, headers, layout) |
| Setup helper | 2 (no install.sh call, paths required) |
| Test isolation | 1 (scratch dirs only) |
| Scenario specifics | 4 (fake BT, session seed, stale lock seed, parent chmod, partial install simulation) |
| install.sh guard pins | 4 (do_uninstall handles missing dir, iterates symlinks, purge log redirect, completion marker) |

Combined regression: **242/242** zielono (10 modules accumulated z #156-#163).

## Evidence (`*-tail.log`)

- `01a-uninstall-running.log` — full uninstall log z [SUMMARY] markers
- `01b-purge-sessions.log` — purge log + final-log preserved at `/tmp/bterminal-uninstall-final.*.log`
- `01c-stale-lock.log` — uninstall completed mimo stale lockfile (PID 99999 dead)
- `01d-readonly.log` — uninstall partial fail (rc=1) bez traceback
- `01e-partial.log` — partial uninstall: dir already gone, symlinks cleaned

## install.sh guards potwierdzone

- `do_uninstall` sprawdza `[[ -d "$INSTALL_DIR" ]]` przed rm
- `_BT_BIN_SYMLINKS=(bterminal ctx tasks consult memory_wizard claude_log claude copilot aider)` iterowane
- `--purge` redirectuje log do `$FINAL_LOG_TMP` (mktemp) zanim usunie `$CONFIG_DIR`
- `=== BTerminal uninstall completed ===` marker emitted

## Verdict

**5/5 PASS na real VM**, 15/15 pin tests, 242/242 combined regression.
Wszystkie 5 hostile uninstall scenarios udowodnione gracefully.

Helpers cumulative (#156-#163): live monitor, F10 menu nav, REST endpoints
(`/api/sessions/*`, `/api/window/state`, `/api/tabs/ai/<prov>`), edge
case test patterns, fast scratch-home setup. Wszystkie 8 E2E tasks
(#156-#163) zamknięte z 100% pass rate.

**Następny etap:** #164 (Release QA Master) i #165-#179 (manual QA per
methodology).
