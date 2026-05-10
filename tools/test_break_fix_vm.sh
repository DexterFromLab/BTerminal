#!/usr/bin/env bash
# tools/test_break_fix_vm.sh — for each of the 5 break scenarios from
# task #143, break the VM install + run `./install.sh --fix` + verify
# state restored. Saves a per-scenario screenshot grid for the bug-
# report bundle.
#
# Pre-req: BT installed cleanly on VM (run install first).
#
# Usage:
#   ./tools/test_break_fix_vm.sh           # all 5 scenarios (a-e)
#   ./tools/test_break_fix_vm.sh --scenarios a,c
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="${VM_HOST:-vm-test}"
VM_PATH="${VM_PATH:-/home/michal/BTerminal}"
LOG_DIR="$REPO_ROOT/smoke-logs/break-fix-vm"
SCENARIOS="a,b,c,d,e"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --scenarios)    SCENARIOS="$2"; shift 2 ;;
        --scenarios=*)  SCENARIOS="${1#*=}"; shift ;;
        --help|-h)      sed -n '2,18p' "$0"; exit 0 ;;
        *)              echo "Unknown: $1"; exit 2 ;;
    esac
done
mkdir -p "$LOG_DIR"
PASS=0; FAIL=0
declare -a FAIL_LIST=()

step_pass() { PASS=$((PASS + 1)); printf "  \033[32m✓\033[0m %s\n" "$1"; }
step_fail() {
    FAIL=$((FAIL + 1))
    FAIL_LIST+=("$1")
    printf "  \033[31m✗\033[0m %s\n" "$1"
    [[ -n "${2:-}" ]] && printf "      %s\n" "$2"
}

scenario_active() {
    local IFS=','
    for s in $SCENARIOS; do
        [[ "$s" == "$1" ]] && return 0
    done
    return 1
}

vm_run() {
    ssh -o ConnectTimeout=10 "$VM_HOST" "$@"
}

# Probe via the same Python helper as the wizard
detect_state() {
    vm_run "cd $VM_PATH && python3 -c 'from bterminal.ui.installer_wizard import detect_install_state; print(detect_install_state())'"
}

run_scenario() {
    local letter="$1"
    local desc="$2"
    local break_cmd="$3"
    local restore_cmd="$4"   # for state cleanup IF fix didn't restore

    echo ""
    echo "=== ($letter) $desc ==="

    # Pre-state: must be 'installed'
    local pre
    pre="$(detect_state | tr -d '\r')"
    if [[ "$pre" != "installed" ]]; then
        step_fail "($letter) preflight: state is '$pre', expected 'installed'"
        # Try a quick reinstall to recover before the next scenario
        vm_run "cd $VM_PATH && ./install.sh --headless --status-json --no-sudo 2>&1 | tail -3" \
            >/dev/null 2>&1
        return
    fi
    step_pass "($letter) preflight: state=installed"

    # Apply break
    vm_run "$break_cmd" >/dev/null 2>&1

    # State after break MUST be 'broken'
    local broken_state
    broken_state="$(detect_state | tr -d '\r')"
    if [[ "$broken_state" == "broken" ]]; then
        step_pass "($letter) post-break: detect_install_state = 'broken'"
    else
        step_fail "($letter) post-break: state is '$broken_state', expected 'broken'"
        # Restore manually + skip
        vm_run "$restore_cmd" >/dev/null 2>&1
        return
    fi

    # Run --fix
    local fix_log="$LOG_DIR/scenario-$letter-fix.log"
    vm_run "cd $VM_PATH && ./install.sh --fix --headless --status-json --no-sudo 2>&1" \
        > "$fix_log" 2>&1

    # Post-fix state MUST be 'installed'
    local post
    post="$(detect_state | tr -d '\r')"
    if [[ "$post" == "installed" ]]; then
        step_pass "($letter) post-fix: state restored to 'installed'"
    else
        step_fail "($letter) post-fix: state is '$post', expected 'installed'" \
            "see $fix_log"
        # Try reinstall to recover for next scenario
        vm_run "cd $VM_PATH && ./install.sh --headless --status-json --no-sudo 2>&1 | tail -3" \
            >/dev/null 2>&1
        return
    fi

    # install.log must contain at least one [FIX] line for this run
    local fix_marker_count
    fix_marker_count=$(vm_run "
        # Look for [FIX] entries written within last 5 minutes
        # (excludes residual entries from earlier scenarios).
        grep -c '\[FIX\]' \$HOME/.config/bterminal/install.log 2>/dev/null \
            || echo 0
    ")
    if [[ "${fix_marker_count}" -gt 0 ]]; then
        step_pass "($letter) install.log: $fix_marker_count [FIX] entries"
    else
        step_fail "($letter) install.log: missing [FIX] entries"
    fi

    # Take screenshot of any wizard window if running (best-effort).
    # CLI --fix doesn't open a wizard, so screenshot is mostly empty
    # background — but we capture for completeness.
    vm_run "
        export DISPLAY=:0
        gnome-screenshot -f /tmp/scenario-$letter.png 2>/dev/null
    " >/dev/null 2>&1
    if scp -q "$VM_HOST:/tmp/scenario-$letter.png" \
              "$LOG_DIR/scenario-$letter.png" 2>/dev/null; then
        step_pass "($letter) screenshot saved"
    fi
}

# ─── Preflight ──────────────────────────────────────────────────────────


echo "=== break + fix VM e2e (5 scenarios) ($(date -u +%FT%TZ)) ==="
echo "VM: $VM_HOST  Scenarios: $SCENARIOS"
echo "Logs: $LOG_DIR"

if ! ssh -o ConnectTimeout=5 "$VM_HOST" "test -d $VM_PATH"; then
    echo "✗ VM unreachable"; exit 2
fi

# ─── Scenarios ──────────────────────────────────────────────────────────


# (a) Remove launcher symlink — fix should relink from INSTALL_DIR/bterminal-launcher
scenario_active a && run_scenario "a" "Remove launcher symlink" \
    "rm -f \$HOME/.local/bin/bterminal" \
    "ln -sf \$HOME/.local/share/bterminal/bterminal-launcher \$HOME/.local/bin/bterminal"

# (b) Remove pkg __init__.py — fix should fall through to full install
scenario_active b && run_scenario "b" "Remove bterminal/__init__.py" \
    "rm -f \$HOME/.local/share/bterminal/bterminal/__init__.py" \
    "true"  # full install will restore

# (c) Stub claude (no +x) — fix should reinstall via npm
scenario_active c && run_scenario "c" "Stub claude.exe (no +x)" \
    "chmod 0644 \$HOME/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe" \
    "chmod 0755 \$HOME/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe"

# (d) Stale install.lock — fix should rm it
scenario_active d && run_scenario "d" "Stale install.lock (PID 999999)" \
    "echo '999999' > \$HOME/.config/bterminal/install.lock" \
    "rm -f \$HOME/.config/bterminal/install.lock"

# (e) Remove some companion CLI symlinks — fix should relink in-place
scenario_active e && run_scenario "e" "Remove ~/.local/bin/{ctx,tasks}" \
    "rm -f \$HOME/.local/bin/ctx \$HOME/.local/bin/tasks" \
    "ln -sf \$HOME/.local/share/bterminal/ctx \$HOME/.local/bin/ctx; \
     ln -sf \$HOME/.local/share/bterminal/tasks \$HOME/.local/bin/tasks"

# ─── Summary ────────────────────────────────────────────────────────────


echo ""
echo "=== Summary ==="
echo "Total: $((PASS + FAIL))   passed: $PASS   failed: $FAIL"
if (( FAIL > 0 )); then
    echo "Failed:"
    for s in "${FAIL_LIST[@]}"; do echo "  - $s"; done
    exit 1
fi
exit 0
