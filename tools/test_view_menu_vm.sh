#!/usr/bin/env bash
# tools/test_view_menu_vm.sh — View menu E2E (#158)
#
# Drives the BTerminal View menu on real VM (vm-test) via xdotool +
# REST debug API. Uses the new /api/window/state endpoint added for
# this task to query sidebar/git/theme/active-panel without GUI
# scraping.
#
# Sub-tests:
#   (a) Ctrl+B toggle sidebar       → /api/window/state.sidebar_visible flips
#   (b) Ctrl+G toggle Git panel     → state.git_visible flips
#   (c) View → Toggle theme         → state.theme dark↔light
#   (d) View → Sessions/Ctx/Consult/Tasks/Plugins panel switchers →
#                                      state.sidebar_active_panel matches
#
# Pre-reqs identical to #157 (xdotool, gnome-screenshot, --debug-rest).
#
# Usage:
#   ./tools/test_view_menu_vm.sh                    # full
#   VM_RESPAWN=1 ./tools/test_view_menu_vm.sh       # force fresh BT

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="${VM_HOST:-vm-test}"
VM_USER_HOME="${VM_USER_HOME:-/home/michal}"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/smoke-logs/view-menu-e2e}"
MONITOR="$REPO_ROOT/tools/_e2e_live_monitor.sh"
REST_PORT="${REST_PORT:-7780}"
RESPAWN="${VM_RESPAWN:-0}"

mkdir -p "$LOG_DIR"
PASS=0; FAIL=0; FAIL_LIST=()

# ── Helpers (shared with #157) ────────────────────────────────────────────

_vm() { ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$VM_HOST" "$@"; }
_xkey() { _vm "DISPLAY=:0 xdotool key --delay 80 $*"; }

_xwindow_present() {
    _vm "DISPLAY=:0 xdotool search --name '$1' 2>/dev/null" | grep -q .
}

_xwait_window() {
    local pattern="$1" timeout="${2:-5}"
    for ((i=0; i<timeout*2; i++)); do
        _xwindow_present "$pattern" && return 0
        sleep 0.5
    done
    return 1
}

_xfocus_bt() {
    _vm "DISPLAY=:0 xdotool search --onlyvisible --name 'BTerminal — Terminal' \
         windowactivate --sync 2>/dev/null"
}

TOKEN=""
_rest_load_token() {
    TOKEN=$(_vm "cat $VM_USER_HOME/.config/bterminal/debug_token 2>/dev/null")
    [[ -n "$TOKEN" ]]
}

_rest() {
    local method="$1" path="$2" body="${3:-}"
    if [[ -n "$body" ]]; then
        _vm "curl -s -X $method -H 'Authorization: Bearer $TOKEN' \
             -H 'Content-Type: application/json' -d '$body' \
             http://127.0.0.1:$REST_PORT$path"
    else
        _vm "curl -s -X $method -H 'Authorization: Bearer $TOKEN' \
             http://127.0.0.1:$REST_PORT$path"
    fi
}

_rest_health_ok() {
    local hp; hp=$(_rest GET /api/health 2>/dev/null || true)
    [[ "$hp" == *'"ok": true'* ]]
}

_get_window_state() {
    _rest GET /api/window/state 2>/dev/null
}

_state_field() {
    local field="$1" json="$2"
    echo "$json" | python3 -c "
import sys, json
try:
    d = json.load(sys.stdin)
    val = d.get('$field')
    if isinstance(val, bool):
        print('true' if val else 'false')
    else:
        print(val if val is not None else '')
except: print('')
"
}

_test_pass() { PASS=$((PASS+1)); echo "  ✓ $1"; }
_test_fail() { FAIL=$((FAIL+1)); FAIL_LIST+=("$1"); echo "  ✗ $1"; }

# ── Setup ──────────────────────────────────────────────────────────────────

echo "=== View menu E2E (#158) on $VM_HOST ==="

_setup_running_bt() {
    _xwindow_present "BTerminal" || return 1
    _rest_load_token 2>/dev/null || return 1
    _rest_health_ok 2>/dev/null || return 1
}

if [[ "$RESPAWN" == "1" ]] || ! _setup_running_bt; then
    echo "[setup] Stopping any running BTerminal on VM…"
    _vm "pkill -f 'python.*-m bterminal' 2>/dev/null; sleep 1; \
         pkill -9 -f 'python.*-m bterminal' 2>/dev/null; true"
    echo "[setup] Spawning BT with --debug-rest…"
    _vm "DISPLAY=:0 setsid -f $VM_USER_HOME/.local/bin/bterminal --debug-rest \
         </dev/null >/tmp/bt-e2e.log 2>&1"
    if ! _xwait_window "BTerminal" 15; then
        echo "FATAL: BT window not seen"
        _vm "tail -30 /tmp/bt-e2e.log" || true
        exit 1
    fi
    for i in $(seq 1 10); do
        sleep 1
        _rest_load_token && _rest_health_ok && break
    done
    _setup_running_bt || { echo "FATAL: REST not responding"; exit 1; }
fi

echo "[setup] BT running, REST healthy."

# Sanity: must have new endpoint
WSTATE=$(_get_window_state)
if [[ "$WSTATE" != *sidebar_visible* ]]; then
    echo "FATAL: /api/window/state missing — old build on VM?"
    echo "       got: $WSTATE"
    exit 1
fi

SESSION=$("$MONITOR" start)
echo "[monitor] $SESSION"
trap '"$MONITOR" stop >/dev/null 2>&1 || true' EXIT
sleep 2
"$MONITOR" tag 00-bt-baseline >/dev/null

# Make sure BT sidebar starts visible + sessions panel
_rest POST /api/window/sidebar/show '{"name":"sessions"}' >/dev/null
sleep 0.5

# ── (a) Ctrl+B toggle sidebar ──────────────────────────────────────────────

echo
echo "=== (a) Ctrl+B toggle sidebar ==="
_xfocus_bt || true

WSTATE=$(_get_window_state)
SIDEBAR_BEFORE=$(_state_field sidebar_visible "$WSTATE")
echo "  before: sidebar_visible=$SIDEBAR_BEFORE"
"$MONITOR" tag 01a-sidebar-before >/dev/null

_xkey "ctrl+b"
sleep 1
"$MONITOR" tag 01a-sidebar-after-1 >/dev/null

WSTATE=$(_get_window_state)
SIDEBAR_AFTER=$(_state_field sidebar_visible "$WSTATE")
echo "  after Ctrl+B: sidebar_visible=$SIDEBAR_AFTER"

if [[ "$SIDEBAR_BEFORE" != "$SIDEBAR_AFTER" ]]; then
    _test_pass "Ctrl+B toggled sidebar ($SIDEBAR_BEFORE → $SIDEBAR_AFTER)"
else
    _test_fail "Ctrl+B did NOT toggle sidebar (still $SIDEBAR_BEFORE)"
fi

# Toggle back to baseline so subsequent tests are deterministic
_xkey "ctrl+b"
sleep 0.5

# ── (b) Ctrl+G toggle Git panel ────────────────────────────────────────────

echo
echo "=== (b) Ctrl+G toggle Git panel ==="
_xfocus_bt || true

WSTATE=$(_get_window_state)
GIT_BEFORE=$(_state_field git_visible "$WSTATE")
echo "  before: git_visible=$GIT_BEFORE"

_xkey "ctrl+g"
sleep 1
"$MONITOR" tag 02b-git-after >/dev/null

WSTATE=$(_get_window_state)
GIT_AFTER=$(_state_field git_visible "$WSTATE")
echo "  after Ctrl+G: git_visible=$GIT_AFTER"

if [[ "$GIT_BEFORE" != "$GIT_AFTER" ]]; then
    _test_pass "Ctrl+G toggled Git panel ($GIT_BEFORE → $GIT_AFTER)"
else
    _test_fail "Ctrl+G did NOT toggle Git panel"
fi

# Toggle back
_xkey "ctrl+g"
sleep 0.5

# ── (c) View → Toggle theme ────────────────────────────────────────────────

echo
echo "=== (c) View → Toggle theme ==="
_xfocus_bt || true

WSTATE=$(_get_window_state)
THEME_BEFORE=$(_state_field theme "$WSTATE")
echo "  before: theme=$THEME_BEFORE"

# View menu = 2nd menubar item. F10 → Right (move focus to View) → Return
# (open submenu) → Down × 2 → Return (3rd item = "Toggle theme ☀/🌙")
# View menu items: Toggle sidebar(1), Toggle Git(2), Toggle theme(3),
#                  [sep], Sessions(4), Ctx(5), Consult(6), Tasks(7), Plugins(8)
# After Return on View, 1st item highlighted → 2×Down moves to Toggle theme
_vm "DISPLAY=:0 xdotool key --delay 100 F10 Right Return"
sleep 0.5
"$MONITOR" tag 03c-view-menu-open >/dev/null
_vm "DISPLAY=:0 xdotool key --delay 100 Down Down Return"
sleep 1
"$MONITOR" tag 03c-after-toggle-theme >/dev/null

WSTATE=$(_get_window_state)
THEME_AFTER=$(_state_field theme "$WSTATE")
echo "  after toggle: theme=$THEME_AFTER"

if [[ "$THEME_BEFORE" != "$THEME_AFTER" ]] && \
   [[ -n "$THEME_AFTER" ]] && [[ -n "$THEME_BEFORE" ]]; then
    _test_pass "Theme toggled ($THEME_BEFORE → $THEME_AFTER)"
else
    _test_fail "Theme did NOT toggle (still $THEME_AFTER)"
fi

# Switch back to dark for subsequent tests visual consistency
if [[ "$THEME_AFTER" != "dark" ]]; then
    _vm "DISPLAY=:0 xdotool key --delay 100 F10 Right Return Down Down Return"
    sleep 1
fi

# ── (d) View → 5x panel switchers ──────────────────────────────────────────

echo
echo "=== (d) Panel switchers (Sessions/Ctx/Consult/Tasks/Plugins) ==="

# Each panel: View → Down×N+3 (3 toggles + 1 separator + 1..5) + Return
# Index from menu top (after Return on View, 1st = Toggle sidebar):
#   sessions: 4th → Down 3
#   ctx:      5th → Down 4
#   consult:  6th → Down 5
#   tasks:    7th → Down 6
#   plugins:  8th → Down 7

_panel_test() {
    local name="$1" downs="$2"
    _xfocus_bt || true
    # Build "Down Down ..." string
    local key_chain="F10 Right Return"
    for ((i=0; i<downs; i++)); do key_chain+=" Down"; done
    key_chain+=" Return"
    _vm "DISPLAY=:0 xdotool key --delay 100 $key_chain"
    sleep 1
    "$MONITOR" tag "04d-panel-$name" >/dev/null
    local wstate active
    wstate=$(_get_window_state)
    active=$(_state_field sidebar_active_panel "$wstate")
    if [[ "$active" == "$name" ]]; then
        _test_pass "Panel '$name' active (Down×$downs)"
    else
        _test_fail "Panel '$name' — active='$active' expected='$name'"
    fi
}

# Make sure sidebar visible + start from a known panel (consult)
# so each switch is visibly different
_rest POST /api/window/sidebar/show '{"name":"consult"}' >/dev/null
sleep 0.5

_panel_test "sessions" 3
_panel_test "ctx"      4
_panel_test "consult"  5
_panel_test "tasks"    6
_panel_test "plugins"  7

# ── Final report ───────────────────────────────────────────────────────────

echo
echo "============================================================"
echo "View menu E2E (#158):  PASS=$PASS  FAIL=$FAIL"
echo "Live monitor session:  $SESSION"
echo "============================================================"
if (( FAIL > 0 )); then
    printf '  - %s\n' "${FAIL_LIST[@]}"
    exit 1
fi
exit 0
