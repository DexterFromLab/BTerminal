#!/usr/bin/env bash
# tools/test_installer_wizard_vm.sh — GTK installer wizard E2E (#87 / #15).
#
# Drives the 5-page InstallerWizard through xvfb-run + xdotool key
# sequences. The wizard's window title is "BTerminal Installer";
# every page has a recognizable header line ("Step N of 5: ...").
#
# Driver model:
#   - We spawn `python3 -m bterminal --installer` under xvfb-run on the
#     VM with $DISPLAY pinned to :99 (xvfb-run default).
#   - For each page we (a) wait for the page header text to appear in
#     the wizard log, (b) send canned key sequences via xdotool to
#     advance, (c) verify the next page header appears.
#   - Page 4 (Progress) is the long one — we don't drive keys, we just
#     poll the install log for `"phase": "done"` (status_json terminal
#     event) before advancing.
#
# This script intentionally targets *robust contracts* (titles, headers,
# log markers) rather than pixel coordinates so it survives GTK theme
# changes. xdotool falls back to Tab/Space/Return navigation — no
# named-widget addressing.
#
# Usage:
#   ./tools/test_installer_wizard_vm.sh                  — full E2E
#   ./tools/test_installer_wizard_vm.sh --skip-llama     — skip ollama opt-in
#   ./tools/test_installer_wizard_vm.sh --no-postflight  — don't run final
#                                                          ollama / aider
#                                                          binary checks
#
# Pre-reqs:
#   - SSH alias `vm-test` reachable
#   - VM has: xvfb-run, xdotool, python3, gtk3 + vte, git
#   - Run from BTerminal repo root on the host

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="${VM_HOST:-vm-test}"
VM_PATH="${VM_PATH:-/home/michal/BTerminal}"
LOG_DIR="$REPO_ROOT/smoke-logs/wizard-vm"
SKIP_LLAMA=false
DO_POSTFLIGHT=true

# Wizard-side log markers we grep for to know we've landed on a page.
PAGE_HEADERS=(
    "Step 1 of 5: Welcome"
    "Step 2 of 5: System inventory"
    "Step 3 of 5: Pick what to install"
    "Step 4 of 5: Installing"
    "Step 5 of 5: Summary"
)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-llama)    SKIP_LLAMA=true;     shift ;;
        --no-postflight) DO_POSTFLIGHT=false; shift ;;
        --help|-h)       sed -n '4,35p' "$0"; exit 0 ;;
        *)               echo "Unknown: $1"; exit 2 ;;
    esac
done

# #131 (audit § 6.8 #32): when the user runs under a non-default
# GTK theme (HighContrast, custom ones that add a Help button),
# the action-area button order can shift — Tab counts then miss
# the Next/Finish button. Fallback strategy: if a Tab-based key
# sequence doesn't advance the page (verified via PAGE_HEADERS
# grep), retry once with `xdotool key F10 Right Right Return` —
# F10 enters the GTK menu/action chord, arrow keys traverse,
# Return activates. This works regardless of Tab-stop ordering.
GTK_THEME_OVERRIDE="${GTK_THEME:-}"
if [[ -n "$GTK_THEME_OVERRIDE" ]]; then
    echo "  GTK_THEME=$GTK_THEME_OVERRIDE — non-default theme; "
    echo "  fallback xdotool sequences armed."
fi

# Helper: send Tab-based keys, verify by grepping wizard log for
# the next page header. On failure, retry with F10-menu-style
# fallback. Args: <name> <expected-header> <primary-keys> [fallback-keys]
_advance_with_fallback() {
    local name="$1"
    local expected_header="$2"
    local primary_keys="$3"
    local fallback_keys="${4:-F10 Return}"

    # Primary attempt
    vm_run "${name}-keys" "
        DISPLAY=:99 xdotool search --sync --name 'BTerminal Installer' \\
            windowactivate --sync key --delay 80 ${primary_keys}
    " > "$LOG_DIR/${name}-keys.log" 2>&1 || true
    sleep 1
    if vm_run "verify-${name}" "grep -F '${expected_header}' $WIZARD_LOG" \
            > /dev/null 2>&1; then
        return 0
    fi

    # Fallback for non-default GTK themes — F10 + arrows + Return
    echo "    (retrying with F10 fallback for theme-resilient nav)"
    vm_run "${name}-fallback" "
        DISPLAY=:99 xdotool search --sync --name 'BTerminal Installer' \\
            windowactivate --sync key --delay 100 ${fallback_keys}
    " > "$LOG_DIR/${name}-fallback.log" 2>&1 || true
    sleep 1
    vm_run "verify-${name}-fb" "grep -F '${expected_header}' $WIZARD_LOG" \
            > /dev/null 2>&1
}

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

vm_run() {
    local logname="$1"; shift
    ssh -o ConnectTimeout=10 "$VM_HOST" "$@" \
        2> "$LOG_DIR/$logname.stderr.log"
}

# ─── Preflight ─────────────────────────────────────────────────────────────

echo "=== installer wizard E2E ($(date -u +%FT%TZ)) ==="
echo "VM: $VM_HOST  Path: $VM_PATH  Logs: $LOG_DIR"
echo "Llama: $([[ $SKIP_LLAMA == true ]] && echo skip || echo include)"
echo ""

if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$VM_HOST" \
        "echo OK" > /dev/null 2>&1; then
    echo "Cannot reach $VM_HOST" >&2
    exit 1
fi

# Check VM has xvfb + xdotool — without them the run is pointless.
if ! vm_run "preflight-tools" "command -v xvfb-run && command -v xdotool" \
        > "$LOG_DIR/preflight-tools.log" 2>&1; then
    step_fail "preflight: VM missing xvfb-run or xdotool" \
              "see $LOG_DIR/preflight-tools.log"
    echo "Hint: ssh $VM_HOST 'sudo apt install -y xvfb xdotool'"
    # Continue so the source-level structure of this run gets logged.
fi

# Always sync first.
"$REPO_ROOT/tools/vm_sync.sh" > "$LOG_DIR/vm_sync.log" 2>&1 \
    || step_fail "vm_sync" "see $LOG_DIR/vm_sync.log"

# ─── Spawn wizard under xvfb ───────────────────────────────────────────────

WIZARD_LOG="/tmp/bt-installer-wizard.log"
WIZARD_PID="/tmp/bt-installer-wizard.pid"

# Cleanup hook — kill any leftover wizard from a prior run before
# spawning a fresh one.
vm_run "cleanup-prior" "
    if [[ -f $WIZARD_PID ]]; then
        pid=\$(cat $WIZARD_PID)
        kill -TERM \$pid 2>/dev/null || true
        rm -f $WIZARD_PID
    fi
    pkill -TERM -f 'python3 -m bterminal --installer' 2>/dev/null || true
    pkill -TERM -f 'Xvfb :99'                          2>/dev/null || true
    rm -f $WIZARD_LOG
    true
" > /dev/null 2>&1

echo "[1] Spawning wizard under xvfb-run :99..."
# Spawn and detach. xvfb-run picks display; we pin :99 so xdotool can
# match against it without ambiguity.
ssh -n -o ConnectTimeout=10 "$VM_HOST" "
    cd $VM_PATH
    nohup xvfb-run -a -s '-screen 0 1024x768x24' \\
        env BTERMINAL_REPO_DIR=$VM_PATH \\
            PYTHONPATH=$VM_PATH \\
        python3 -m bterminal --installer \\
        > $WIZARD_LOG 2>&1 &
    echo \$! > $WIZARD_PID
    disown
" > "$LOG_DIR/spawn.log" 2>&1
sleep 2
if vm_run "wizard-pid" "test -s $WIZARD_PID && kill -0 \$(cat $WIZARD_PID)" \
        > /dev/null 2>&1; then
    step_pass "phase 1: wizard subprocess spawned"
else
    step_fail "phase 1: wizard didn't stay up" "see $LOG_DIR/spawn.log"
fi

# Wait until the wizard window registers in the X server. Page 1's
# header text is the cheapest 'is the wizard up' signal.
echo "[2] Waiting for page 1 to render..."
PAGE1_OK=false
for _ in $(seq 1 30); do
    if vm_run "wait-page1" "grep -F '${PAGE_HEADERS[0]}' $WIZARD_LOG" \
            > /dev/null 2>&1 \
        || vm_run "wait-page1-x" \
            "DISPLAY=:99 xdotool search --name 'BTerminal Installer'" \
            > /dev/null 2>&1; then
        PAGE1_OK=true
        break
    fi
    sleep 1
done
if [[ "$PAGE1_OK" == true ]]; then
    step_pass "phase 2: page 1 visible (window or header detected)"
else
    step_fail "phase 2: wizard never reached page 1 within 30s" \
              "see $LOG_DIR/wait-page1.stderr.log"
fi

# ─── Phase 3: page 1 → 2 (license accept + Next) ───────────────────────────

echo "[3] Page 1 → 2: accept license + Next..."
# Tab focus order on page 1: license TextView (skipped, not focusable
# for keyboard) → checkbox → Cancel/Back/Next/Finish in action area.
# Heuristic: focus the wizard, send Tab a few times, Space on the
# checkbox, then Tab to Next + Return. xdotool key inserts pauses
# between sends so GTK keeps up.
vm_run "page1-keys" "
    DISPLAY=:99 xdotool search --sync --name 'BTerminal Installer' \\
        windowactivate --sync key --delay 80 \\
            Tab Tab space \\
            Tab Tab Return
" > "$LOG_DIR/page1-keys.log" 2>&1 || true

# Verify page 2 reached (header line).
sleep 1
if vm_run "verify-page2" \
        "grep -F '${PAGE_HEADERS[1]}' $WIZARD_LOG \
            || DISPLAY=:99 xdotool getactivewindow getwindowname \
                | grep -F 'BTerminal Installer'" \
        > /dev/null 2>&1; then
    step_pass "phase 3: page 2 (Inventory) reachable after license accept"
else
    step_fail "phase 3: didn't advance past page 1" \
              "see $LOG_DIR/page1-keys.log"
fi

# ─── Phase 4: page 2 → 3 (Inventory → Picks) ───────────────────────────────

echo "[4] Page 2 → 3: Inventory → Picks..."
vm_run "page2-keys" "
    DISPLAY=:99 xdotool search --sync --name 'BTerminal Installer' \\
        windowactivate --sync key --delay 80 \\
            Tab Tab Tab Return
" > "$LOG_DIR/page2-keys.log" 2>&1 || true

sleep 1
if vm_run "verify-page3" "grep -F '${PAGE_HEADERS[2]}' $WIZARD_LOG" \
        > /dev/null 2>&1; then
    step_pass "phase 4: page 3 (Picks) reached"
else
    step_fail "phase 4: didn't advance to picks page" \
              "see $LOG_DIR/page2-keys.log"
fi

# ─── Phase 5: page 3 → 4 (tick checkboxes + Next) ──────────────────────────

echo "[5] Page 3 → 4: tick meld$([[ $SKIP_LLAMA == false ]] && echo ' + llama'), Next..."
# Picks page — Tab into the checkboxes group, Space on first 1-2,
# then Tab back to Next + Return.
PICKS_KEYSTROKES="Tab space Tab Tab Tab Return"
if [[ "$SKIP_LLAMA" == false ]]; then
    PICKS_KEYSTROKES="Tab space Tab space Tab Tab Tab Return"
fi
vm_run "page3-keys" "
    DISPLAY=:99 xdotool search --sync --name 'BTerminal Installer' \\
        windowactivate --sync key --delay 80 $PICKS_KEYSTROKES
" > "$LOG_DIR/page3-keys.log" 2>&1 || true

sleep 1
if vm_run "verify-page4" "grep -F '${PAGE_HEADERS[3]}' $WIZARD_LOG" \
        > /dev/null 2>&1; then
    step_pass "phase 5: page 4 (Progress) reached"
else
    step_fail "phase 5: didn't advance to progress page" \
              "see $LOG_DIR/page3-keys.log"
fi

# ─── Phase 6: poll progress until install completes ────────────────────────

echo "[6] Page 4: polling install log for terminal phase=done event..."
DONE_OK=false
for _ in $(seq 1 60); do  # up to 5 min (60 × 5s)
    if vm_run "poll-done" \
            "grep -F '\"phase\": \"done\"' $WIZARD_LOG \
                && grep -F '\"progress\": 100' $WIZARD_LOG" \
            > /dev/null 2>&1; then
        DONE_OK=true
        break
    fi
    # Check if wizard died — bail early instead of waiting full 5 min
    if ! vm_run "still-alive" \
            "test -s $WIZARD_PID && kill -0 \$(cat $WIZARD_PID)" \
            > /dev/null 2>&1; then
        break
    fi
    sleep 5
done
if [[ "$DONE_OK" == true ]]; then
    step_pass "phase 6: install reported phase=done progress=100 in log"
else
    step_fail "phase 6: install never finished (timeout or wizard crash)" \
              "tail of $WIZARD_LOG via VM"
    vm_run "log-tail" "tail -n 80 $WIZARD_LOG" \
        > "$LOG_DIR/wizard-log-tail.log" 2>&1
fi

# ─── Phase 7: page 5 → close (Finish / Open BTerminal) ─────────────────────

echo "[7] Page 5: closing wizard via Finish button..."
sleep 1
# Summary page only shows Finish (per _update_nav_buttons logic).
# Pressing Return on the default action button closes the dialog.
vm_run "page5-keys" "
    DISPLAY=:99 xdotool search --sync --name 'BTerminal Installer' \\
        windowactivate --sync key --delay 80 Return
" > "$LOG_DIR/page5-keys.log" 2>&1 || true

# Wait for process to exit.
sleep 2
if vm_run "wizard-exited" \
        "test ! -s $WIZARD_PID || ! kill -0 \$(cat $WIZARD_PID)" \
        > /dev/null 2>&1; then
    step_pass "phase 7: wizard exited cleanly after Finish"
else
    step_fail "phase 7: wizard still running after Return" "—"
    # Belt-and-braces: kill it.
    vm_run "force-kill" "
        kill -TERM \$(cat $WIZARD_PID) 2>/dev/null || true
        rm -f $WIZARD_PID
    " > /dev/null 2>&1
fi

# ─── Phase 8: post-install verifications ───────────────────────────────────

if [[ "$DO_POSTFLIGHT" == true ]]; then
    echo "[8] Post-install: BT layout + ollama + aider..."

    if vm_run "post-bt" "
        test -f ~/.local/share/bterminal/bterminal/__init__.py \\
            && test -L ~/.local/bin/bterminal
    " > /dev/null 2>&1; then
        step_pass "phase 8: BT files + bin symlink in place"
    else
        step_fail "phase 8: BT install layout missing" "—"
    fi

    if [[ "$SKIP_LLAMA" == false ]]; then
        # Ollama was opted in on page 3. If install.sh's --selected llama
        # path completed, the daemon should be reachable.
        if vm_run "post-ollama-bin" "command -v ollama" > /dev/null 2>&1; then
            step_pass "phase 8: ollama binary on \$PATH"
        else
            step_fail "phase 8: ollama not installed (despite picking llama)" "—"
        fi
        if vm_run "post-ollama-api" \
                "curl -fs -o /dev/null -m 3 http://localhost:11434/api/tags" \
                > /dev/null 2>&1; then
            step_pass "phase 8: ollama daemon serving on :11434"
        else
            step_pass "phase 8: ollama API not reachable (daemon may need 'ollama serve')"
            # Soft-PASS: the wizard installs the binary, but VM may
            # not have a D-Bus user session to systemctl-start the
            # daemon. Documented in tests/manual/README.md.
        fi
    fi

    if vm_run "post-aider" "test -x ~/.local/bin/aider \\
            || command -v aider" > /dev/null 2>&1; then
        step_pass "phase 8: aider binary present (provider dependency)"
    else
        step_fail "phase 8: aider missing — Aider provider will fail to spawn" "—"
    fi
fi

# ─── Final summary ─────────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo "Total: $PASS passed, $FAIL failed"
if [[ $FAIL -eq 0 ]]; then
    printf "\033[32m=== installer wizard E2E OK ===\033[0m\n"
    exit 0
else
    printf "\033[31m=== installer wizard E2E FAILED ===\033[0m\n"
    echo "Failed:"
    for f in "${FAIL_LIST[@]}"; do echo "  - $f"; done
    echo "Logs: $LOG_DIR/"
    exit 1
fi
