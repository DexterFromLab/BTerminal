# Task #85 (#157) — File menu E2E

**Date:** 2026-05-08

---

## Deliverable

`tools/test_file_menu_vm.sh` — driver-and-asserter for all 5 File menu items
on real VM (vm-test). Uses xdotool for navigation + REST asserts +
live monitor #156 for screenshot evidence.

## Sub-tests (5/5 PASS na real VM)

| # | Item | Assertion | Result |
|---|------|-----------|--------|
| (a) | File → New local tab | `/api/tabs` count incremented | ✓ tabs went 1 → 2 |
| (b) | File → New SSH session… | window "Add Session" appears | ✓ |
| (c) | File → New Claude Code session… | window "Add Claude Session" appears | ✓ |
| (d) | File → Options… | window "Opcje BTerminal" appears | ✓ |
| (e) | File → Quit | REST :7780 unreachable | ✓ |

## Bugs found + fixed during E2E iteration

1. **Alt+F goes to VTE bash readline 'forward-word'** — used `F10` to enter
   menubar instead. Pin test guards regression.

2. **xdotool search "BTerminal" matched gnome-terminal** (cwd=~/BTerminal).
   Used precise pattern `"BTerminal — Terminal"` to focus.

3. **Down+Return as separate ssh calls lost menu focus** between hops.
   Chained as single `xdotool key --delay 100 Down Return`.

4. **F10+Return on "File" auto-highlights 1st submenu item** — Down counts
   are N-1, not N. (a) `Return` only, (b) `Down Return`, (c) `Down Down
   Return`, (d) `Down×3 Return`, (e) `Down×4 Return`.

5. **Live monitor tag captures previous frame** — added `sleep 2` between
   dialog-open and tag so frame grabber catches the dialog.

6. **REST health pattern `"ok":true` failed** — JSON output uses
   `"ok": true` (with space). Accept both forms.

## Pin tests — 10/10 ✓ (`tests/test_file_menu_e2e.py`)

| Test | Pin |
|------|-----|
| `test_script_exists_and_executable` | binary + chmod +x |
| `test_script_passes_bash_syntax_check` | bash -n |
| `test_script_documents_all_five_subtests` | (a)..(e) headers |
| `test_script_uses_f10_not_alt_f` | NEVER revert to alt+f |
| `test_script_uses_chained_xdotool_keys` | one ssh per chord |
| `test_script_integrates_with_live_monitor` | start/tag/stop calls |
| `test_script_uses_rest_assertions` | _rest_health_ok + /api/tabs |
| `test_script_supports_quick_and_respawn_flags` | VM_QUICK / VM_RESPAWN |
| `test_script_focuses_precise_bt_window_pattern` | "BTerminal — Terminal" |
| `test_script_cleanup_traps_monitor_stop` | trap EXIT |

Combined regression suite: **171/171** zielono (installer pin + options E2E +
monitor pin + file-menu pin).

## Real VM screenshot evidence (`screenshots/`)

| File | Shows |
|------|-------|
| `tag-…-00-bt-baseline.png` | BT main window pre-test |
| `tag-…-01a-file-menu-open.png` | File menu open with all 5 items visible |
| `tag-…-01a-after-new-local.png` | Tab strip after New local — 2 tabs |
| `tag-…-02b-ssh-dialog-open.png` | "Add Session" SSH dialog (Name/Host/Port/etc) |
| `tag-…-03c-ai-dialog-open.png` | "Add AI Session" Claude dialog (provider radio + flags + name + folder + project_dir + plugins) |
| `tag-…-04d-options-open.png` | "Opcje BTerminal" with Theme/Font/Shell/Language + AI Providers + Local Models expanders |
| `tag-…-05e-after-quit.png` | Desktop after BT exited |

## Run modes

```
./tools/test_file_menu_vm.sh                   # full run, all 5 items
VM_QUICK=1 ./tools/test_file_menu_vm.sh        # skip Quit (BT stays alive)
VM_RESPAWN=1 ./tools/test_file_menu_vm.sh      # force pkill+respawn even if running
```

## Verdict

**5/5 PASS na real VM**, 10/10 pin tests. Bugs znalezione przy iteracji udokumentowane
w skrypcie + pin-test'ach żeby się nie powtarzały. Helper pattern (F10 + chained
xdotool + auto-highlight account + live monitor) gotowy do reuse w #158-#161.
