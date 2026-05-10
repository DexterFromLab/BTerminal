#!/usr/bin/env bash
# tools/_e2e_live_monitor.sh — event-driven live screenshot+log monitor
# for VM E2E tests (#156, action-driven snapshots since task #1).
#
# Spawns one background loop (log tail) while a test driver runs.
# Screenshots are NEVER polled — every snapshot comes from an explicit
# `tag NAME` call, which executes ssh+gnome-screenshot on demand.
#
# Why action-driven: the previous polling implementation produced
# thousands of duplicate frames (5279 PNGs / 990MB in smoke-logs/) even
# during idle stretches of a test. With per-action tags the evidence
# bundle has exactly N PNGs where N = number of meaningful state
# changes — every screenshot maps to a documented step.
#
# Designed to compose with the per-menu E2E scripts (#157-#161): each
# of those wraps its xdotool flow with `start`, `tag`s key states, then
# `stop`s — leaving an evidence bundle in smoke-logs/live-monitor/<ts>/.
#
# Usage:
#   ./tools/_e2e_live_monitor.sh start         # spawn log-tail loop → echo SESSION_DIR
#   ./tools/_e2e_live_monitor.sh tag NAME      # ssh+gnome-screenshot NOW → tag-HHMMSS-NAME.png
#   ./tools/_e2e_live_monitor.sh stop          # kill log loop, finalize
#   ./tools/_e2e_live_monitor.sh status        # is a monitor running?
#   ./tools/_e2e_live_monitor.sh --help        # this help
#
# Env vars:
#   VM_HOST            — ssh host (default: vm-test)
#   VM_LOG_DIR         — where to tail logs from on VM
#                        (default: /home/michal/.local/share/bterminal/logs)
#   MONITOR_INTERVAL_SEC — DEPRECATED, kept only for back-compat with
#                          older callers. Action-driven mode ignores it.
#   STATE_FILE         — PID/dir state (default: /tmp/_e2e_monitor.state)
#   SESSION_DIR_ROOT   — output root (default: smoke-logs/live-monitor)
#   MONITOR_NO_VM      — set to 1 to skip ssh (pin-test mode); `tag`
#                        writes empty-placeholder PNGs so test fixtures
#                        can assert file presence without ssh.

set -uo pipefail

VM_HOST="${VM_HOST:-vm-test}"
VM_LOG_DIR="${VM_LOG_DIR:-/home/michal/.local/share/bterminal/logs}"
INTERVAL="${MONITOR_INTERVAL_SEC:-2}"
STATE_FILE="${STATE_FILE:-/tmp/_e2e_monitor.state}"
SESSION_DIR_ROOT="${SESSION_DIR_ROOT:-$(pwd)/smoke-logs/live-monitor}"
NO_VM="${MONITOR_NO_VM:-0}"

cmd="${1:---help}"
shift || true

case "$cmd" in
    start)
        if [[ -f "$STATE_FILE" ]]; then
            echo "ERROR: monitor already running (state file: $STATE_FILE)" >&2
            echo "       run 'stop' first or remove the state file." >&2
            exit 2
        fi
        ts="$(date +%Y%m%d-%H%M%S)"
        SESSION_DIR="$SESSION_DIR_ROOT/$ts"
        mkdir -p "$SESSION_DIR/frames"
        : > "$SESSION_DIR/log-stream.txt"
        : > "$SESSION_DIR/monitor.log"

        echo "SESSION_DIR=$SESSION_DIR" > "$STATE_FILE"

        # FRAMES_PID is always 0 in action-driven mode — there is no
        # background screenshot loop to track. Kept in state for
        # back-compat with `status` consumers and existing pin tests.
        if [[ "$NO_VM" != "1" ]]; then
            # Only the log-tail loop runs in the background; screenshots
            # are taken on-demand by `tag` (no polling).
            LOGS_MARKER="$SESSION_DIR/.logs.pid"

            setsid -f bash -c '
                VM_HOST="$1" VM_LOG_DIR="$2" SESSION_DIR="$3" MARKER="$4"
                echo "$$" > "$MARKER"
                ssh -n -o ConnectTimeout=3 -o BatchMode=yes "$VM_HOST" \
                    "tail -F -n 0 $VM_LOG_DIR/*.log 2>/dev/null" \
                    >> "$SESSION_DIR/log-stream.txt" 2>>"$SESSION_DIR/monitor.log"
            ' _ "$VM_HOST" "$VM_LOG_DIR" "$SESSION_DIR" "$LOGS_MARKER" \
                </dev/null >/dev/null 2>>"$SESSION_DIR/monitor.log"

            for _ in 1 2 3 4 5; do
                [[ -f "$LOGS_MARKER" ]] && break
                sleep 0.1
            done
            LOGS_PID="$(cat "$LOGS_MARKER" 2>/dev/null || echo 0)"
            echo "FRAMES_PID=0" >> "$STATE_FILE"
            echo "LOGS_PID=$LOGS_PID" >> "$STATE_FILE"
        else
            echo "FRAMES_PID=0" >> "$STATE_FILE"
            echo "LOGS_PID=0" >> "$STATE_FILE"
            echo "[$(date '+%H:%M:%S')] MONITOR_NO_VM=1 — skipping ssh" \
                >> "$SESSION_DIR/monitor.log"
        fi

        echo "$SESSION_DIR"
        ;;

    tag)
        name="${1:-tag}"
        if [[ ! -f "$STATE_FILE" ]]; then
            echo "ERROR: no monitor running (no state file)" >&2
            exit 2
        fi
        # shellcheck disable=SC1090
        source "$STATE_FILE"
        ts="$(date +%H%M%S)"
        out="$SESSION_DIR/tag-${ts}-${name}.png"
        if [[ "$NO_VM" == "1" ]]; then
            # Pin-test mode: write empty placeholder so callers can
            # assert is_file() without ssh dependencies.
            : > "$out"
            echo "[$(date '+%H:%M:%S')] tag $name (NO_VM placeholder)" \
                >> "$SESSION_DIR/monitor.log"
        else
            # Action-driven snapshot — ssh into VM, grab one fresh
            # frame, stream bytes back. This is the ONLY place a
            # screenshot is taken; no polling buffer involved.
            if ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$VM_HOST" \
                "DISPLAY=:0 gnome-screenshot --display=:0 -f /tmp/_tag.png 2>/dev/null && cat /tmp/_tag.png" \
                > "$out" 2>>"$SESSION_DIR/monitor.log" && [[ -s "$out" ]]; then
                : # success
            else
                rm -f "$out"
                echo "[$(date '+%H:%M:%S')] tag $name: ssh+gnome-screenshot failed" \
                    >> "$SESSION_DIR/monitor.log"
                # Still emit the path so callers can detect the failure
                # via missing file rather than parsing stderr.
                echo "$out" >&2
                exit 1
            fi
        fi
        echo "$out"
        ;;

    stop)
        if [[ ! -f "$STATE_FILE" ]]; then
            echo "ERROR: no monitor running (no state file)" >&2
            exit 2
        fi
        # shellcheck disable=SC1090
        source "$STATE_FILE"
        # Wipe state FIRST so the screenshot loop's `[[ -f $STATE_FILE ]]`
        # guard exits cleanly — kill is a fallback for the tail.
        rm -f "$STATE_FILE"
        # Kill the entire process group (setsid -f makes each bg
        # process its own session leader, so PGID == PID). Negative
        # arg to kill targets the group → catches the wrapper bash
        # AND its ssh child(ren).
        if [[ "${FRAMES_PID:-0}" != "0" ]]; then
            kill -TERM -- "-$FRAMES_PID" 2>/dev/null || \
                kill -TERM "$FRAMES_PID" 2>/dev/null || true
        fi
        if [[ "${LOGS_PID:-0}" != "0" ]]; then
            kill -TERM -- "-$LOGS_PID" 2>/dev/null || \
                kill -TERM "$LOGS_PID" 2>/dev/null || true
        fi
        # Wait briefly + SIGKILL holdouts.
        sleep 0.3
        if [[ "${FRAMES_PID:-0}" != "0" ]] && kill -0 "$FRAMES_PID" 2>/dev/null; then
            kill -KILL -- "-$FRAMES_PID" 2>/dev/null || \
                kill -KILL "$FRAMES_PID" 2>/dev/null || true
        fi
        if [[ "${LOGS_PID:-0}" != "0" ]] && kill -0 "$LOGS_PID" 2>/dev/null; then
            kill -KILL -- "-$LOGS_PID" 2>/dev/null || \
                kill -KILL "$LOGS_PID" 2>/dev/null || true
        fi
        # Drop a final marker so consumers know it stopped cleanly.
        echo "[$(date '+%H:%M:%S')] monitor stopped cleanly" \
            >> "$SESSION_DIR/monitor.log" 2>/dev/null || true
        echo "$SESSION_DIR"
        ;;

    status)
        if [[ -f "$STATE_FILE" ]]; then
            # shellcheck disable=SC1090
            source "$STATE_FILE"
            running_frames="no"
            running_logs="no"
            if [[ "${FRAMES_PID:-0}" != "0" ]] \
               && kill -0 "$FRAMES_PID" 2>/dev/null; then
                running_frames="yes"
            fi
            if [[ "${LOGS_PID:-0}" != "0" ]] \
               && kill -0 "$LOGS_PID" 2>/dev/null; then
                running_logs="yes"
            fi
            echo "running"
            echo "session_dir=$SESSION_DIR"
            echo "frames_pid=${FRAMES_PID:-0} alive=$running_frames"
            echo "logs_pid=${LOGS_PID:-0} alive=$running_logs"
        else
            echo "stopped"
            exit 1
        fi
        ;;

    --help|-h|help)
        sed -n '1,40p' "$0"
        ;;

    *)
        echo "Unknown command: $cmd" >&2
        echo "Run '$0 --help' for usage." >&2
        exit 2
        ;;
esac
