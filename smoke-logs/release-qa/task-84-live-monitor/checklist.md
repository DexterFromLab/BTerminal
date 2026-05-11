# Task #84 (#156) — Live monitoring framework for VM E2E

**Date:** 2026-05-08

---

## Deliverable

`tools/_e2e_live_monitor.sh` — bash helper used by per-menu E2E scripts (#157-#162)
to capture continuous evidence (screenshots + log tail) WITHOUT requiring the
test runner to predict ahead of time when interesting state will occur.

## API

```
./tools/_e2e_live_monitor.sh start    → echo SESSION_DIR (returns immediately)
./tools/_e2e_live_monitor.sh status   → "running"|"stopped" + PIDs (rc 0|1)
./tools/_e2e_live_monitor.sh tag NAME → echo path to tag-HHMMSS-NAME.png
./tools/_e2e_live_monitor.sh stop     → kill bg, remove state, finalize
./tools/_e2e_live_monitor.sh --help   → usage doc
```

Env vars:
- `VM_HOST` — ssh host (default: vm-test)
- `VM_LOG_DIR` — log dir on VM (default: ~/.local/share/bterminal/logs)
- `MONITOR_INTERVAL_SEC` — frame grab interval (default: 2s)
- `STATE_FILE` — PID/dir state (default: /tmp/_e2e_monitor.state)
- `SESSION_DIR_ROOT` — output root (default: smoke-logs/live-monitor)
- `MONITOR_NO_VM=1` — skip ssh (pin-test mode)

## Architecture

`start` spawns 2 detached background loops via `setsid -f`:
1. **Frame grabber**: `ssh vm-test gnome-screenshot → cat → local NNNN.png`,
   loop bounded by state-file existence (gracefully exits on `stop`).
2. **Log tail**: `ssh vm-test tail -F install.log bterminal.log → log-stream.txt`.

PIDs are written by the forked children to marker files under SESSION_DIR;
parent reads them back into the state file. Both loops have stdin/stdout
fully detached so a `$(./monitor start)` substitution returns immediately
instead of hanging on inherited fds.

`stop` reads PIDs, kills the **process group** (`kill -- -$PID`) so ssh
children die together with the wrapper. Falls back to direct PID kill +
SIGKILL escalation if any holdouts.

`tag NAME` copies the latest frame to `SESSION_DIR/tag-HHMMSS-NAME.png`,
giving runners a way to mark "what was on screen at this exact moment".

## Pin tests — 14/14 ✓ (`tests/test_e2e_live_monitor.py`)

| Test | What it verifies |
|------|------------------|
| `test_script_exists_and_executable` | binary present + chmod +x |
| `test_script_passes_bash_syntax_check` | bash -n parse |
| `test_help_command_renders` | --help has start/stop/tag |
| `test_unknown_command_exits_2` | bogus subcommand → rc 2 |
| `test_status_when_not_running_exits_1` | status without state → "stopped" rc 1 |
| `test_stop_when_not_running_exits_2` | stop without state → rc 2 |
| `test_tag_when_not_running_exits_2` | tag without state → rc 2 |
| `test_start_creates_session_dir_and_state` | start → frames/, log-stream.txt, monitor.log + state file |
| `test_status_after_start_reports_running` | post-start status → "running" + PIDs |
| `test_double_start_refused` | second start when already running → rc 2 |
| `test_tag_creates_marker_file` | tag NAME → tag-HHMMSS-NAME.png |
| `test_stop_removes_state_file` | stop → state file gone |
| `test_full_lifecycle_smoke` | start → status → tag×2 → stop → status=stopped |
| `test_script_documents_required_helpers_for_e2e_runners` | env vars + cmds documented in source |

Combined suite: **161/161** zielono (installer pin + options E2E + monitor pin).

## Real VM smoke (vm-test reachable)

```
$ SESSION=$(VM_HOST=vm-test ./tools/_e2e_live_monitor.sh start)
$ ls $SESSION/frames after 4s
0001.png  0002.png  0003.png  0004.png  0005.png   ← all 464KB real screenshots
$ ./tools/_e2e_live_monitor.sh tag verify
…/tag-171038-verify.png   ← 464KB (skips in-progress 0-byte frames)
$ ./tools/_e2e_live_monitor.sh stop
$ pgrep -f "ssh.*vm-test" | wc -l
0   ← clean teardown after PGID kill
```

Real screenshot evidence: `vm-real-screenshot.png` — pokazuje BTerminal okno
na VM (File/View/Tools menu, BTerminal Sessions sidebar z entry "test",
terminal area, Mint Cinnamon desktop background).

**Bug znaleziony+naprawiony podczas smoke:** tag łapał frame mid-write (ssh
stdout-redirect w trakcie streamingu daje 0-byte file widoczny dla
`ls -1t`). Fix: pętla po frames od najnowszej i bierze pierwszą
**non-empty** (`-s` test).

## Manual smoke (host, MONITOR_NO_VM=1)

```
$ SESSION=$(MONITOR_NO_VM=1 ./tools/_e2e_live_monitor.sh start)
$ ls $SESSION
frames  log-stream.txt  monitor.log
$ ./tools/_e2e_live_monitor.sh status
running
session_dir=…/sessions/20260508-170657
frames_pid=… alive=…
logs_pid=… alive=…
$ ./tools/_e2e_live_monitor.sh tag dialog_visible
…/tag-170659-dialog_visible.png
$ ./tools/_e2e_live_monitor.sh stop
…
$ ./tools/_e2e_live_monitor.sh status
stopped  (rc 1)
```

Real VM run pattern (used by #157-#162 going forward):
```bash
SESSION=$(VM_HOST=vm-test ./tools/_e2e_live_monitor.sh start)
trap "./tools/_e2e_live_monitor.sh stop" EXIT
ssh vm-test 'xdotool key Return'
./tools/_e2e_live_monitor.sh tag after_license_accept
ssh vm-test 'xdotool type --delay 50 "qwerty"'
./tools/_e2e_live_monitor.sh tag sudo_typed
# … etc
./tools/_e2e_live_monitor.sh stop
```

## Verdict

Framework jest gotowy. `#157-#162` mogą source'ować `_e2e_live_monitor.sh` lub
spawnować przez subprocess; każdy etap testu otrzymuje screenshot bez fixed
sleep, a monitor.log łapie ssh errors gracefully. State file + marker pattern
zapewnia że `start` zwraca natychmiast bez blokowania substitution.
