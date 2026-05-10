#!/usr/bin/env bash
# tools/_e2e_live_monitor.sh — live screenshot+log monitor for VM E2E tests
# (#156)
#
# Spawns two background loops while a test driver runs interactively:
#   1. gnome-screenshot on VM every $INTERVAL sec → scp pull →
#      SESSION_DIR/frames/NNNN.png
#   2. tail -F install.log + bterminal.log on VM →
#      SESSION_DIR/log-stream.txt
#
# Lets the test runner observe what's happening WITHOUT predefined sleep
# checkpoints — bug screenshots are caught even when the test path is
# ambiguous. The runner can also call `tag NAME` to record a symlink to
# the latest frame at a known UI state (login screen, dialog open, etc.).
#
# Designed to compose with the per-menu E2E scripts (#157-#161): each
# of those wraps its xdotool flow with `start`, `tag`s key states, then
# `stop`s — leaving an evidence bundle in smoke-logs/live-monitor/<ts>/.
#
# Usage:
#   ./tools/_e2e_live_monitor.sh start         # spawn monitors → echo SESSION_DIR
#   ./tools/_e2e_live_monitor.sh tag NAME      # snapshot+symlink latest frame
#   ./tools/_e2e_live_monitor.sh stop          # kill monitors, finalize
#   ./tools/_e2e_live_monitor.sh status        # is a monitor running?
#   ./tools/_e2e_live_monitor.sh --help        # this help
#
# Env vars:
#   VM_HOST            — ssh host (default: vm-test)
#   VM_LOG_DIR         — where to tail logs from on VM
#                        (default: /home/michal/.local/share/bterminal/logs)
#   MONITOR_INTERVAL_SEC — frame grab interval (default: 2)
#   STATE_FILE         — PID/dir state (default: /tmp/_e2e_monitor.state)
#   SESSION_DIR_ROOT   — output root (default: smoke-logs/live-monitor)
#   MONITOR_NO_VM      — set to 1 to skip ssh (pin-test mode); only writes
#                        the session structure, doesn't grab frames.

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

        if [[ "$NO_VM" != "1" ]]; then
            # Background screenshot loop. Each iteration:
            #   1. ssh into VM, gnome-screenshot to /tmp on VM
            #   2. cat the bytes back over stdout → local frame file
            # Errors swallowed so transient ssh hiccups don't kill the loop.
            # Crucial: `setsid -f` forks then detaches into its own
            # session, severing all inherited fds. Without -f a
            # $(start) substitution would wait on the bg process's
            # stdout (still tied to the substitution pipe).
            # We write PIDs to a small marker file so we can read
            # them back without command substitution timing.
            FRAMES_MARKER="$SESSION_DIR/.frames.pid"
            LOGS_MARKER="$SESSION_DIR/.logs.pid"

            setsid -f bash -c '
                STATE_FILE="$1" SESSION_DIR="$2" VM_HOST="$3" INTERVAL="$4"
                MARKER="$5"
                echo "$$" > "$MARKER"
                i=0
                while [[ -f "$STATE_FILE" ]]; do
                    i=$((i+1))
                    fname=$(printf "%04d.png" "$i")
                    ssh -n -o ConnectTimeout=3 -o BatchMode=yes "$VM_HOST" \
                        "DISPLAY=:0 gnome-screenshot --display=:0 -f /tmp/_mon.png 2>/dev/null && cat /tmp/_mon.png" \
                        > "$SESSION_DIR/frames/$fname" 2>>"$SESSION_DIR/monitor.log" \
                        || rm -f "$SESSION_DIR/frames/$fname"
                    sleep "$INTERVAL"
                done
            ' _ "$STATE_FILE" "$SESSION_DIR" "$VM_HOST" "$INTERVAL" "$FRAMES_MARKER" \
                </dev/null >/dev/null 2>>"$SESSION_DIR/monitor.log"

            setsid -f bash -c '
                VM_HOST="$1" VM_LOG_DIR="$2" SESSION_DIR="$3" MARKER="$4"
                echo "$$" > "$MARKER"
                ssh -n -o ConnectTimeout=3 -o BatchMode=yes "$VM_HOST" \
                    "tail -F -n 0 $VM_LOG_DIR/*.log 2>/dev/null" \
                    >> "$SESSION_DIR/log-stream.txt" 2>>"$SESSION_DIR/monitor.log"
            ' _ "$VM_HOST" "$VM_LOG_DIR" "$SESSION_DIR" "$LOGS_MARKER" \
                </dev/null >/dev/null 2>>"$SESSION_DIR/monitor.log"

            # Wait briefly for markers (forked child writes its PID).
            for _ in 1 2 3 4 5; do
                [[ -f "$FRAMES_MARKER" && -f "$LOGS_MARKER" ]] && break
                sleep 0.1
            done
            FRAMES_PID="$(cat "$FRAMES_MARKER" 2>/dev/null || echo 0)"
            LOGS_PID="$(cat "$LOGS_MARKER" 2>/dev/null || echo 0)"
            echo "FRAMES_PID=$FRAMES_PID" >> "$STATE_FILE"
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
        # Find latest NON-EMPTY frame (skip in-progress writes from
        # the bg ssh redirect — those appear with size 0 until ssh
        # finishes streaming).
        latest=""
        # shellcheck disable=SC2012
        for f in $(ls -1t "$SESSION_DIR/frames/"*.png 2>/dev/null); do
            if [[ -s "$f" ]]; then
                latest="$f"
                break
            fi
        done
        out="$SESSION_DIR/tag-${ts}-${name}.png"
        if [[ -n "$latest" ]]; then
            cp "$latest" "$out"
        else
            : > "$out"
            echo "[$(date '+%H:%M:%S')] tag $name: no non-empty frame yet" \
                >> "$SESSION_DIR/monitor.log"
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
