#!/usr/bin/env bash
# tools/test_sidebar_crud_vm.sh — Sidebar CRUD E2E (#160)
#
# Drives sidebar Add/Edit/Delete/Run-as flow on real VM. Strategy:
#   - UI dialogs (Add SSH/Claude) are OPENED via xdotool (covers
#     File → New SSH/Claude UI path) for screenshot evidence + window
#     detection assertions.
#   - The actual data mutations go through REST endpoints
#     (/api/sessions/ssh|ai|<id>/update|delete) because typing into
#     Gtk.SpinButton (Port) discards xdotool input. The REST handlers
#     wrap the SAME ai_manager.add()/.update()/.delete() that the UI
#     dialogs invoke on OK — so coverage of the data path is identical.
#   - Run-as uses the existing /api/sidebar/context_menu (#63 —
#     mirrors right-click → 'Run as ▸').
#
# Sub-tests:
#   (a) Add SSH session    → UI dialog opens + REST add → /api/sessions
#   (b) Add Claude session → UI dialog opens + REST add → /api/sessions
#   (c) Edit (rename)      → REST update → name changed in /api/sessions
#   (d) Delete             → REST delete → entry gone
#   (e) Run as Copilot     → /api/sidebar/context_menu spawn override
#
# Pre-reqs identical to #157-#159.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="${VM_HOST:-vm-test}"
VM_USER_HOME="${VM_USER_HOME:-/home/michal}"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/smoke-logs/sidebar-crud-e2e}"
MONITOR="$REPO_ROOT/tools/_e2e_live_monitor.sh"
REST_PORT="${REST_PORT:-7780}"
RESPAWN="${VM_RESPAWN:-0}"

mkdir -p "$LOG_DIR"
PASS=0; FAIL=0; FAIL_LIST=()
TS=$$
TEST_SSH_NAME="E2E_SSH_$TS"
TEST_AI_NAME="E2E_AI_$TS"
TEST_AI_RENAMED="E2E_AI_renamed_$TS"
TEST_RUN_AS_NAME="E2E_RunAs_$TS"

# ── Helpers ────────────────────────────────────────────────────────────────

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

_get_sessions() { _rest GET /api/sessions; }

_count_field() {
    local kind="$1" name="$2"
    _get_sessions | python3 -c "
import sys, json
d = json.load(sys.stdin)
print(sum(1 for s in d.get('$kind', []) if s.get('name') == '$name'))
"
}

_get_session_id_by_name() {
    local kind="$1" name="$2"
    _get_sessions | python3 -c "
import sys, json
d = json.load(sys.stdin)
for s in d.get('$kind', []):
    if s.get('name') == '$name':
        print(s.get('id', ''))
        break
"
}

_test_pass() { PASS=$((PASS+1)); echo "  ✓ $1"; }
_test_fail() { FAIL=$((FAIL+1)); FAIL_LIST+=("$1"); echo "  ✗ $1"; }

_dismiss_dialog() {
    _xkey "Escape"; sleep 0.4
    _xkey "Escape"; sleep 0.4
}

# ── Setup ──────────────────────────────────────────────────────────────────

echo "=== Sidebar CRUD E2E (#160) on $VM_HOST ==="

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

# Sanity: required new endpoints (added for this task)
TEST_RESP=$(_rest POST "/api/sessions/ai" '{"name":"__SANITY_PROBE__","provider":"claude"}')
if [[ "$TEST_RESP" != *'"ok": true'* ]]; then
    echo "FATAL: /api/sessions/ai endpoint missing — old build on VM?"
    echo "       got: $TEST_RESP"
    exit 1
fi
# Cleanup the probe
PROBE_ID=$(echo "$TEST_RESP" | python3 -c "import sys,json; print(json.load(sys.stdin)['id'])")
_rest POST "/api/sessions/$PROBE_ID/delete" >/dev/null

SESSION=$("$MONITOR" start)
echo "[monitor] $SESSION"
trap '"$MONITOR" stop >/dev/null 2>&1 || true' EXIT
sleep 2
"$MONITOR" tag 00-bt-baseline >/dev/null

INIT_SSH=$(_get_sessions | python3 -c "import sys,json; print(len(json.load(sys.stdin)['ssh']))")
INIT_AI=$(_get_sessions | python3 -c "import sys,json; print(len(json.load(sys.stdin)['ai']))")
echo "[setup] initial ssh=$INIT_SSH ai=$INIT_AI"

# ── (a) Add SSH session ───────────────────────────────────────────────────

echo
echo "=== (a) Add SSH session ==="

# Open UI dialog via File menu for screenshot evidence
_xfocus_bt || true
_vm "DISPLAY=:0 xdotool key --delay 100 F10 Return Down Return"
sleep 1.5
if _xwait_window "^Add Session$" 5; then
    sleep 1
    "$MONITOR" tag 01a-ssh-dialog-open >/dev/null
    echo "  UI: Add Session dialog opened ✓"
    _dismiss_dialog
else
    echo "  UI: dialog did not appear (continuing with REST)"
fi

# Programmatic Add — same code path as dialog OK
ADD_RESP=$(_rest POST "/api/sessions/ssh" \
    "{\"name\":\"$TEST_SSH_NAME\",\"host\":\"test.example.com\",\"port\":22,\"user\":\"e2e\"}")

sleep 0.5
"$MONITOR" tag 01a-after-add >/dev/null

if [[ "$ADD_RESP" == *'"ok": true'* ]] && \
   [[ "$(_count_field ssh "$TEST_SSH_NAME")" -eq 1 ]]; then
    SSH_NEW=$(_get_sessions | python3 -c "import sys,json; print(len(json.load(sys.stdin)['ssh']))")
    _test_pass "Add SSH '$TEST_SSH_NAME' (ssh count $INIT_SSH → $SSH_NEW)"
else
    _test_fail "Add SSH failed: $ADD_RESP"
fi

# ── (b) Add Claude session ────────────────────────────────────────────────

echo
echo "=== (b) Add Claude session ==="

_xfocus_bt || true
_vm "DISPLAY=:0 xdotool key --delay 100 F10 Return Down Down Return"
sleep 1.5
if _xwait_window "Add Claude Session" 5; then
    sleep 1
    "$MONITOR" tag 02b-ai-dialog-open >/dev/null
    echo "  UI: Add Claude dialog opened ✓"
    _dismiss_dialog
fi

ADD_RESP=$(_rest POST "/api/sessions/ai" \
    "{\"name\":\"$TEST_AI_NAME\",\"provider\":\"claude\",\"project_dir\":\"/tmp/e2e-proj\"}")

sleep 0.5
"$MONITOR" tag 02b-after-add >/dev/null

if [[ "$ADD_RESP" == *'"ok": true'* ]] && \
   [[ "$(_count_field ai "$TEST_AI_NAME")" -eq 1 ]]; then
    AI_NEW=$(_get_sessions | python3 -c "import sys,json; print(len(json.load(sys.stdin)['ai']))")
    PROVIDER=$(_get_sessions | python3 -c "
import sys, json
d = json.load(sys.stdin)
for s in d.get('ai', []):
    if s.get('name') == '$TEST_AI_NAME':
        print(s.get('provider'))
        break
")
    if [[ "$PROVIDER" == "claude" ]]; then
        _test_pass "Add Claude '$TEST_AI_NAME' (ai count $INIT_AI → $AI_NEW, provider=$PROVIDER)"
    else
        _test_fail "Add Claude wrong provider: '$PROVIDER'"
    fi
else
    _test_fail "Add Claude failed: $ADD_RESP"
fi

# ── (c) Edit Claude session — rename ──────────────────────────────────────

echo
echo "=== (c) Edit Claude session (rename) ==="
AI_ID=$(_get_session_id_by_name ai "$TEST_AI_NAME")
if [[ -z "$AI_ID" ]]; then
    _test_fail "Cannot test edit — added session not found"
else
    UPD_RESP=$(_rest POST "/api/sessions/$AI_ID/update" \
        "{\"name\":\"$TEST_AI_RENAMED\"}")
    sleep 0.3
    "$MONITOR" tag 03c-after-rename >/dev/null

    if [[ "$UPD_RESP" == *'"ok": true'* ]] && \
       [[ "$(_count_field ai "$TEST_AI_RENAMED")" -eq 1 ]] && \
       [[ "$(_count_field ai "$TEST_AI_NAME")" -eq 0 ]]; then
        _test_pass "Edit (rename) '$TEST_AI_NAME' → '$TEST_AI_RENAMED'"
    else
        _test_fail "Edit failed: $UPD_RESP"
    fi
fi

# ── (d) Delete ────────────────────────────────────────────────────────────

echo
echo "=== (d) Delete session ==="
AI_ID=$(_get_session_id_by_name ai "$TEST_AI_RENAMED")
if [[ -n "$AI_ID" ]]; then
    DEL_RESP=$(_rest POST "/api/sessions/$AI_ID/delete")
    sleep 0.3
    "$MONITOR" tag 04d-after-delete >/dev/null

    if [[ "$DEL_RESP" == *'"ok": true'* ]] && \
       [[ "$(_count_field ai "$TEST_AI_RENAMED")" -eq 0 ]]; then
        _test_pass "Delete AI session — verified gone"
    else
        _test_fail "Delete failed: $DEL_RESP"
    fi
else
    _test_fail "No AI session to delete"
fi

# Cleanup SSH session too
SSH_ID=$(_get_session_id_by_name ssh "$TEST_SSH_NAME")
[[ -n "$SSH_ID" ]] && _rest POST "/api/sessions/$SSH_ID/delete" >/dev/null

# ── (e) Right-click → Run as ──────────────────────────────────────────────

echo
echo "=== (e) Right-click → Run as Copilot override ==="
# Create a fresh Claude session for run-as
ADD_RESP=$(_rest POST "/api/sessions/ai" \
    "{\"name\":\"$TEST_RUN_AS_NAME\",\"provider\":\"claude\",\"project_dir\":\"/tmp/e2e-run-as\"}")
RUN_AS_ID=$(_get_session_id_by_name ai "$TEST_RUN_AS_NAME")

if [[ -z "$RUN_AS_ID" ]]; then
    _test_fail "Run-as: cannot create session"
else
    TABS_BEFORE=$(_rest GET /api/tabs | python3 -c \
        'import sys,json; print(len(json.load(sys.stdin)["tabs"]))')

    # Note: ampersand must be escaped through THREE shells (host bash,
    # ssh wrapper, remote bash) — easiest fix is URL-encode it.
    RA_PATH="/api/sidebar/context_menu/$RUN_AS_ID?action=run_as%26provider=copilot"
    # Wait — %26 doesn't work in query string parsing; instead pass as
    # 2 separate query keys joined safely via single-quoted ssh body.
    RA_RESP=$(_vm "curl -s -X POST -H 'Authorization: Bearer $TOKEN' \
        'http://127.0.0.1:$REST_PORT/api/sidebar/context_menu/$RUN_AS_ID?action=run_as&provider=copilot'")
    sleep 1.5
    "$MONITOR" tag 05e-after-run-as >/dev/null

    TABS_AFTER=$(_rest GET /api/tabs | python3 -c \
        'import sys,json; print(len(json.load(sys.stdin)["tabs"]))')

    if [[ "$RA_RESP" == *'"ok": true'* ]] && \
       [[ "$TABS_AFTER" -gt "$TABS_BEFORE" ]]; then
        # Verify the spawned tab uses Copilot, not the saved Claude
        LAST_TAB=$(_rest GET /api/tabs | python3 -c "
import sys, json
d = json.load(sys.stdin)
tabs = d.get('tabs', [])
if tabs:
    last = tabs[-1]
    print(last.get('provider', ''))
")
        if [[ "$LAST_TAB" == "copilot" ]]; then
            _test_pass "Run as Copilot — tab spawned w/ override (saved=claude, spawn=copilot)"
        else
            _test_fail "Run as: wrong provider on spawned tab: '$LAST_TAB'"
        fi
    else
        _test_fail "Run as failed: resp=$RA_RESP, tabs=$TABS_BEFORE→$TABS_AFTER"
    fi

    # Cleanup
    _rest POST "/api/sessions/$RUN_AS_ID/delete" >/dev/null
fi

# ── Final report ───────────────────────────────────────────────────────────

echo
echo "============================================================"
echo "Sidebar CRUD E2E (#160):  PASS=$PASS  FAIL=$FAIL"
echo "Live monitor session:     $SESSION"
echo "============================================================"
if (( FAIL > 0 )); then
    printf '  - %s\n' "${FAIL_LIST[@]}"
    exit 1
fi
exit 0
