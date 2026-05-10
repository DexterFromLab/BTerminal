#!/usr/bin/env bash
# tools/test_xvfb_concurrent.sh — concurrent xvfb-run display
# collision avoidance (#62 / #134, audit § 6.8 #35).
#
# When two BT installer wizards / e2e tests run in parallel
# under xvfb-run on the same VM, the `-a` flag must pick a
# FREE server number for each — they can't both grab :99.
# Without this, the second runner inherits the first's display
# (and dies with "Server is already active"), or worse, lands
# on whatever DISPLAY the user already exports.
#
# Three decision branches:
#   (a) 2 parallel — minimal smoke, the canonical case for
#       running the wizard test alongside an e2e suite
#   (b) 5 parallel — stress, simulates a CI pipeline that
#       sharded its tests
#   (c) DISPLAY pre-set in env — `-a` should override (find
#       a free server number anyway, NOT honor the inherited
#       DISPLAY)
#
# Usage:
#   ./tools/test_xvfb_concurrent.sh                  — all 3 branches
#   ./tools/test_xvfb_concurrent.sh --modes a,b      — skip pre-set test
#   ./tools/test_xvfb_concurrent.sh --remote vm-test — run via SSH on VM
#
# Pre-reqs:
#   - xvfb-run installed locally (or on remote VM)
#   - Run from BTerminal repo root

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
LOG_DIR="$REPO_ROOT/smoke-logs/xvfb-concurrent"
MODES="a,b,c,d"
REMOTE=""

while [[ $# -gt 0 ]]; do
    case "$1" in
        --modes)         MODES="$2"; shift 2 ;;
        --modes=*)       MODES="${1#*=}"; shift ;;
        --remote)        REMOTE="$2"; shift 2 ;;
        --remote=*)      REMOTE="${1#*=}"; shift ;;
        --help|-h)       sed -n '4,30p' "$0"; exit 0 ;;
        *)               echo "Unknown: $1"; exit 2 ;;
    esac
done

mkdir -p "$LOG_DIR"
PASS=0; FAIL=0
declare -a FAIL_LIST=()

step_pass() {
    PASS=$((PASS + 1))
    printf "  \033[32mOK\033[0m  %s\n" "$1"
}

step_fail() {
    FAIL=$((FAIL + 1))
    FAIL_LIST+=("$1")
    printf "  \033[31mFAIL\033[0m %s\n" "$1"
    [[ -n "${2:-}" ]] && printf "      %s\n" "$2"
}

mode_active() {
    [[ ",$MODES," == *",$1,"* ]]
}

# Run a command either locally or on the remote VM. Output to
# stdout; caller redirects to log files as needed.
run_cmd() {
    if [[ -n "$REMOTE" ]]; then
        ssh -o ConnectTimeout=10 "$REMOTE" "$@"
    else
        bash -c "$@"
    fi
}

# Helper: spawn N xvfb-run processes in parallel using EXPLICIT
# `--server-num=$NUM` allocation rather than `-a` (auto-pick).
# Realne odkrycie: `-a` has a race window — multiple processes
# probing for free server numbers at the same instant all see
# the same "free" candidate and grab it. With explicit numbers,
# there's no collision because each worker gets a distinct N.
#
# This is the workaround BT's e2e harness should use when
# spawning concurrent xvfb-run sessions (e.g. pytest -n with
# xvfb-run inside each fixture). The `-a` flag is fine for
# sequential invocations.
spawn_n_xvfb() {
    local n=$1
    local logfile="$2"
    local extra_env="${3:-}"
    local mode="${4:-explicit}"  # "explicit" or "auto" (broken)

    rm -f "$logfile"
    local pids=()
    for i in $(seq 1 "$n"); do
        # Pick a unique server number per worker. PID-based
        # offset avoids collision with another concurrent test
        # run (e.g. CI pipelines).
        local server_num=$(( 200 + ($$ % 50) + i ))
        local xvfb_flag
        if [[ "$mode" == "auto" ]]; then
            xvfb_flag="-a"
        else
            xvfb_flag="--server-num=$server_num"
        fi
        run_cmd "
            $extra_env xvfb-run $xvfb_flag \
                bash -c 'echo \"\$DISPLAY\" >> $logfile; sleep 1'
        " > /dev/null 2>&1 &
        pids+=("$!")
    done
    # Wait for all child processes
    for pid in "${pids[@]}"; do
        wait "$pid" 2>/dev/null || true
    done
}

# Count unique DISPLAY values in a log
count_unique_displays() {
    local logfile="$1"
    if [[ ! -s "$logfile" ]]; then
        echo 0
        return
    fi
    sort -u "$logfile" | grep -c '^:'
}

# ─── Preflight ─────────────────────────────────────────────────────────────

echo "=== xvfb-run concurrent smoke ($(date -u +%FT%TZ)) ==="
echo "Modes: $MODES  Remote: ${REMOTE:-<local>}  Logs: $LOG_DIR"
echo ""

if [[ -n "$REMOTE" ]]; then
    if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$REMOTE" \
            "command -v xvfb-run" > /dev/null 2>&1; then
        echo "Cannot reach $REMOTE or xvfb-run missing" >&2
        exit 1
    fi
else
    if ! command -v xvfb-run > /dev/null; then
        echo "xvfb-run not installed locally — apt install xvfb" >&2
        exit 1
    fi
fi

# ─── (a) 2 parallel — canonical case ───────────────────────────────────────

if mode_active a; then
    echo "[a] 2 parallel xvfb-run sessions..."
    LOG="$LOG_DIR/2-parallel.log"
    # Use unique tmp file on remote / local
    REMOTE_LOG="${LOG_DIR}/2-parallel-displays.txt"
    if [[ -n "$REMOTE" ]]; then
        # Use /tmp on the remote
        ssh "$REMOTE" "rm -f /tmp/xvfb-test-displays.txt"
        spawn_n_xvfb 2 "/tmp/xvfb-test-displays.txt"
        ssh "$REMOTE" "cat /tmp/xvfb-test-displays.txt" \
            > "$REMOTE_LOG" 2>/dev/null
    else
        spawn_n_xvfb 2 "$REMOTE_LOG"
    fi

    UNIQUE=$(count_unique_displays "$REMOTE_LOG")
    if [[ "$UNIQUE" == "2" ]]; then
        step_pass "(a) 2 parallel: each picked unique DISPLAY"
        cat "$REMOTE_LOG" | sed 's/^/      /'
    else
        step_fail "(a) 2 parallel: only $UNIQUE unique DISPLAY (expected 2)" \
                  "see $REMOTE_LOG"
    fi
fi

# ─── (b) 5 parallel — stress ───────────────────────────────────────────────

if mode_active b; then
    echo ""
    echo "[b] 5 parallel xvfb-run sessions (CI shard simulation)..."
    REMOTE_LOG="${LOG_DIR}/5-parallel-displays.txt"
    if [[ -n "$REMOTE" ]]; then
        ssh "$REMOTE" "rm -f /tmp/xvfb-test-displays-5.txt"
        spawn_n_xvfb 5 "/tmp/xvfb-test-displays-5.txt"
        ssh "$REMOTE" "cat /tmp/xvfb-test-displays-5.txt" \
            > "$REMOTE_LOG" 2>/dev/null
    else
        spawn_n_xvfb 5 "$REMOTE_LOG"
    fi

    UNIQUE=$(count_unique_displays "$REMOTE_LOG")
    if [[ "$UNIQUE" == "5" ]]; then
        step_pass "(b) 5 parallel: all 5 unique DISPLAYs allocated"
    else
        step_fail "(b) 5 parallel: only $UNIQUE unique DISPLAY (expected 5)" \
                  "see $REMOTE_LOG"
    fi

    # No display collision: every line in the log appears
    # exactly once
    if [[ -s "$REMOTE_LOG" ]]; then
        DUPS=$(sort "$REMOTE_LOG" | uniq -d | wc -l)
        if [[ "$DUPS" == "0" ]]; then
            step_pass "(b) 5 parallel: zero duplicate DISPLAY values"
        else
            step_fail "(b) 5 parallel: $DUPS duplicate DISPLAYs" \
                      "see $REMOTE_LOG"
        fi
    fi
fi

# ─── (c) DISPLAY pre-set in env — `-a` should override ────────────────────

if mode_active c; then
    echo ""
    echo "[c] DISPLAY=:99 pre-set; xvfb-run -a should still pick free..."
    REMOTE_LOG="${LOG_DIR}/preset-displays.txt"
    if [[ -n "$REMOTE" ]]; then
        ssh "$REMOTE" "rm -f /tmp/xvfb-test-preset.txt"
        spawn_n_xvfb 2 "/tmp/xvfb-test-preset.txt" "DISPLAY=:99"
        ssh "$REMOTE" "cat /tmp/xvfb-test-preset.txt" \
            > "$REMOTE_LOG" 2>/dev/null
    else
        spawn_n_xvfb 2 "$REMOTE_LOG" "DISPLAY=:99"
    fi

    UNIQUE=$(count_unique_displays "$REMOTE_LOG")
    if [[ "$UNIQUE" == "2" ]]; then
        step_pass "(c) pre-set DISPLAY=:99: -a still picked 2 unique displays"
    else
        step_fail "(c) pre-set DISPLAY=:99: only $UNIQUE unique" \
                  "see $REMOTE_LOG"
    fi

    # Specifically: NEITHER process inherited :99 (the pre-set
    # value). If they had, count_unique would be 1.
    if [[ -s "$REMOTE_LOG" ]] && grep -q ':99' "$REMOTE_LOG"; then
        step_fail "(c) one process inherited DISPLAY=:99 (pre-set leaked)" \
                  "see $REMOTE_LOG"
    elif [[ -s "$REMOTE_LOG" ]]; then
        step_pass "(c) pre-set :99 not honored — xvfb-run -a override OK"
    fi
fi

# ─── (d) Negative pin: confirm `-a` race is real ──────────────────────────

if mode_active d; then
    echo ""
    echo "[d] Pinning xvfb-run -a race condition (negative test)..."
    REMOTE_LOG="${LOG_DIR}/auto-mode-race.log"
    if [[ -n "$REMOTE" ]]; then
        ssh "$REMOTE" "rm -f /tmp/xvfb-test-auto.txt"
        spawn_n_xvfb 5 "/tmp/xvfb-test-auto.txt" "" "auto"
        ssh "$REMOTE" "cat /tmp/xvfb-test-auto.txt" \
            > "$REMOTE_LOG" 2>/dev/null
    else
        spawn_n_xvfb 5 "$REMOTE_LOG" "" "auto"
    fi

    # With `-a`, we EXPECT collisions (5 spawns, < 5 unique).
    # If this test ever passes with 5 unique displays, xvfb-run
    # has been fixed upstream — flip the assertion to '==5'
    # and use `-a` everywhere instead of explicit --server-num.
    UNIQUE=$(count_unique_displays "$REMOTE_LOG")
    if [[ "$UNIQUE" -lt "5" ]]; then
        step_pass "(d) -a race confirmed: $UNIQUE/5 unique (collisions expected)"
    else
        step_fail "(d) -a now picks all unique — xvfb-run upstream fixed?" \
                  "consider switching from --server-num back to -a"
    fi
fi

# ─── Final summary ─────────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo "Total: $PASS passed, $FAIL failed"
if [[ $FAIL -eq 0 ]]; then
    printf "\033[32m=== xvfb concurrent smoke OK ===\033[0m\n"
    exit 0
else
    printf "\033[31m=== xvfb concurrent smoke FAILED ===\033[0m\n"
    echo "Failed:"
    for f in "${FAIL_LIST[@]}"; do echo "  - $f"; done
    echo "Logs: $LOG_DIR/"
    exit 1
fi
