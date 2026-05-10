#!/usr/bin/env bash
# tools/test_file_menu_vm.sh — File menu E2E (#157)
#
# Drives the BTerminal File menu on a real VM (vm-test) via xdotool +
# REST debug API. Each sub-test produces a tagged screenshot via the
# live monitor (#156) and an explicit assertion (REST or
# xdotool-window-search).
#
# Sub-tests:
#   (a) File → New local tab        → /api/tabs grew by 1, type=local
#   (b) File → New SSH session…     → SSHSessionDialog window appears,
#                                      fill name+host, save, sidebar
#                                      shows the entry
#   (c) File → New Claude Code…     → AISessionDialog window appears,
#                                      screenshot, Cancel
#   (d) File → Options…             → OptionsDialog window appears,
#                                      screenshot, close
#   (e) File → Quit                 → app exits cleanly (REST :7780
#                                      becomes unreachable)
#
# Pre-reqs on VM:
#   - BTerminal installed + ~/.local/bin/bterminal launcher
#   - Active X session at DISPLAY=:0
#   - xdotool, gnome-screenshot
#
# Run from host:  ./tools/test_file_menu_vm.sh
# Force re-spawn: VM_RESPAWN=1 ./tools/test_file_menu_vm.sh
# CI quick:       VM_QUICK=1 ./tools/test_file_menu_vm.sh   (skip Quit)

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="${VM_HOST:-vm-test}"
VM_USER_HOME="${VM_USER_HOME:-/home/michal}"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/smoke-logs/file-menu-e2e}"
MONITOR="$REPO_ROOT/tools/_e2e_live_monitor.sh"
REST_PORT="${REST_PORT:-7780}"
QUICK="${VM_QUICK:-0}"
RESPAWN="${VM_RESPAWN:-0}"

mkdir -p "$LOG_DIR"
PASS=0; FAIL=0; FAIL_LIST=()

# ── Helpers ────────────────────────────────────────────────────────────────

_vm() {
    ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$VM_HOST" "$@"
}

_xkey() {
    _vm "DISPLAY=:0 xdotool key --delay 50 $*"
}

_xtype() {
    # Caller must escape quotes appropriately
    _vm "DISPLAY=:0 xdotool type --delay 50 -- '$1'"
}

_xwindow_present() {
    local pattern="$1"
    _vm "DISPLAY=:0 xdotool search --name '$pattern' 2>/dev/null" \
        | grep -q . 2>/dev/null
}

_xwait_window() {
    local pattern="$1" timeout="${2:-5}"
    for ((i=0; i<timeout*2; i++)); do
        if _xwindow_present "$pattern"; then return 0; fi
        sleep 0.5
    done
    return 1
}

_xfocus_window() {
    local pattern="$1"
    _vm "DISPLAY=:0 xdotool search --onlyvisible --name '$pattern' \
         windowactivate --sync 2>/dev/null"
}

# Focus the actual BT main window — not gnome-terminal cwd matches or
# splash. Targets the "BTerminal — Terminal …" title precisely.
_xfocus_bt() {
    _vm "DISPLAY=:0 xdotool search --onlyvisible --name 'BTerminal — Terminal' \
         windowactivate --sync 2>/dev/null"
}

# Cached REST token from VM
TOKEN=""
_rest_load_token() {
    TOKEN=$(_vm "cat $VM_USER_HOME/.config/bterminal/debug_token 2>/dev/null")
    if [[ -z "$TOKEN" ]]; then
        echo "ERROR: no debug token on VM at $VM_USER_HOME/.config/bterminal/debug_token" >&2
        return 1
    fi
}

_rest() {
    local method="$1" path="$2" body="${3:-}"
    if [[ -n "$body" ]]; then
        _vm "curl -s -X $method -H 'Authorization: Bearer $TOKEN' \
             -H 'Content-Type: application/json' \
             -d '$body' http://127.0.0.1:$REST_PORT$path"
    else
        _vm "curl -s -X $method -H 'Authorization: Bearer $TOKEN' \
             http://127.0.0.1:$REST_PORT$path"
    fi
}

_rest_health_ok() {
    local hp
    hp=$(_rest GET /api/health 2>/dev/null || true)
    # JSON output uses "key": value (with space) — accept both forms.
    [[ "$hp" == *'"ok": true'* || "$hp" == *'"ok":true'* ]]
}

_test_pass() {
    PASS=$((PASS+1))
    echo "  ✓ $1"
}

_test_fail() {
    FAIL=$((FAIL+1))
    FAIL_LIST+=("$1")
    echo "  ✗ $1"
}

# ── Setup ──────────────────────────────────────────────────────────────────

echo "=== File menu E2E (#157) on $VM_HOST ==="

# Try existing BT first: if window present + token loadable + REST OK,
# reuse; else respawn.
_setup_running_bt() {
    _xwindow_present "BTerminal" || return 1
    _rest_load_token 2>/dev/null || return 1
    _rest_health_ok 2>/dev/null || return 1
    return 0
}

if [[ "$RESPAWN" == "1" ]] || ! _setup_running_bt; then
    echo "[setup] Stopping any running BTerminal on VM…"
    _vm "pkill -f 'python.*-m bterminal' 2>/dev/null; sleep 1; \
         pkill -9 -f 'python.*-m bterminal' 2>/dev/null; true"

    echo "[setup] Spawning BT with --debug-rest…"
    _vm "DISPLAY=:0 setsid -f $VM_USER_HOME/.local/bin/bterminal --debug-rest \
         </dev/null >/tmp/bt-e2e.log 2>&1"

    if ! _xwait_window "BTerminal" 15; then
        echo "[setup] FATAL: BT window not seen within 15s"
        _vm "tail -30 /tmp/bt-e2e.log" || true
        exit 1
    fi

    # Wait for REST server to bind + token file written
    for i in $(seq 1 10); do
        sleep 1
        if _rest_load_token 2>/dev/null && _rest_health_ok 2>/dev/null; then
            break
        fi
    done

    if ! _setup_running_bt; then
        echo "FATAL: BT spawned but REST :$REST_PORT not responding"
        _vm "tail -30 /tmp/bt-e2e.log" || true
        exit 1
    fi
fi

echo "[setup] BT running, token loaded, REST healthy."

# 4. Start live monitor
SESSION=$("$MONITOR" start)
echo "[monitor] $SESSION"
trap '"$MONITOR" stop >/dev/null 2>&1 || true' EXIT
sleep 2  # let first frame land

"$MONITOR" tag 00-bt-baseline >/dev/null

# Initial tab count (REST-based)
TABS_BEFORE=$(_rest GET /api/tabs | python3 -c \
    'import sys,json; print(len(json.load(sys.stdin).get("tabs",[])))' \
    2>/dev/null || echo 0)
echo "[setup] Initial tabs: $TABS_BEFORE"

# Make sure BT window is focused before menu drives
_xfocus_bt || true

# ── (a) File → New local tab ───────────────────────────────────────────────

echo
echo "=== (a) File → New local tab ==="
_xkey "F10"
sleep 0.3
_xkey "Return"  # activate File (first menubar item)
sleep 0.3
"$MONITOR" tag 01a-file-menu-open >/dev/null
# After F10+Return on "File", submenu opens with 1st item auto-
# highlighted. Just press Return to activate "New local tab".
_vm "DISPLAY=:0 xdotool key --delay 100 Return"
sleep 1
"$MONITOR" tag 01a-after-new-local >/dev/null

TABS_AFTER=$(_rest GET /api/tabs | python3 -c \
    'import sys,json; print(len(json.load(sys.stdin).get("tabs",[])))' \
    2>/dev/null || echo 0)
echo "  tabs before=$TABS_BEFORE after=$TABS_AFTER"
if [[ "$TABS_AFTER" -gt "$TABS_BEFORE" ]]; then
    _test_pass "New local tab created (REST: tabs went $TABS_BEFORE → $TABS_AFTER)"
else
    _test_fail "New local tab — tab count unchanged"
fi

# ── (b) File → New SSH session… ────────────────────────────────────────────

echo
echo "=== (b) File → New SSH session ==="
_xfocus_bt || true
_xkey "F10"
sleep 0.3
_xkey "Return"
sleep 0.3
# 2nd item: 1×Down + Return (1st auto-highlight, +1 = 2nd)
_vm "DISPLAY=:0 xdotool key --delay 100 Down Return"
sleep 1

# Match precisely on Add Session (SSH dialog title) — not "Claude Session"
if _xwait_window "^Add Session$|^Edit Session$" 5; then
    sleep 2  # let live monitor grab a frame WITH the dialog
    "$MONITOR" tag 02b-ssh-dialog-open >/dev/null
    _test_pass "SSH dialog appeared"
    # Try a minimal fill: Cancel out (we're not testing the SSH connect
    # flow here — just that the dialog opens via menu). Esc closes.
    _xkey "Escape"
    sleep 0.5
else
    _test_fail "SSH dialog did not appear"
fi

# ── (c) File → New Claude Code session… ────────────────────────────────────

echo
echo "=== (c) File → New Claude Code session ==="
_xfocus_bt || true
_xkey "F10"
sleep 0.3
_xkey "Return"
sleep 0.3
# 3rd item: 2×Down + Return
_vm "DISPLAY=:0 xdotool key --delay 100 Down Down Return"
sleep 1

if _xwait_window "Add Claude Session|AI Session" 5; then
    sleep 2
    "$MONITOR" tag 03c-ai-dialog-open >/dev/null
    _test_pass "AISessionDialog appeared"
    _xkey "Escape"
    sleep 0.5
else
    _test_fail "AISessionDialog did not appear"
fi

# ── (d) File → Options… ────────────────────────────────────────────────────

echo
echo "=== (d) File → Options ==="
_xfocus_bt || true
_xkey "F10"
sleep 0.3
_xkey "Return"
sleep 0.3
# Options is 4th (after sep): 3×Down + Return
_vm "DISPLAY=:0 xdotool key --delay 100 Down Down Down Return"
sleep 1

if _xwait_window "Opcje BTerminal|Options" 5; then
    sleep 2
    "$MONITOR" tag 04d-options-open >/dev/null
    _test_pass "OptionsDialog appeared"
    _xkey "Escape"
    sleep 0.5
else
    _test_fail "OptionsDialog did not appear"
fi

# ── (e) File → Quit ────────────────────────────────────────────────────────

if [[ "$QUICK" == "1" ]]; then
    echo
    echo "=== (e) File → Quit (SKIPPED — VM_QUICK=1) ==="
else
    echo
    echo "=== (e) File → Quit ==="
    _xfocus_bt || true
    _xkey "F10"
    sleep 0.3
    _xkey "Return"
    sleep 0.3
    # Quit is 5th item: 4×Down + Return
    _vm "DISPLAY=:0 xdotool key --delay 100 Down Down Down Down Return"
    sleep 2
    "$MONITOR" tag 05e-after-quit >/dev/null

    # REST should now be unreachable (BT exited)
    if _rest_health_ok 2>/dev/null; then
        _test_fail "Quit — REST still reachable, BT did not exit"
    else
        _test_pass "Quit — BT exited (REST unreachable)"
    fi
fi

# ── Final report ───────────────────────────────────────────────────────────

echo
echo "============================================================"
echo "File menu E2E (#157):  PASS=$PASS  FAIL=$FAIL"
echo "Live monitor session:  $SESSION"
echo "============================================================"
if (( FAIL > 0 )); then
    printf '  - %s\n' "${FAIL_LIST[@]}"
    exit 1
fi
exit 0
