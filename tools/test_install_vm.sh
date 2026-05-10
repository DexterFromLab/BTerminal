#!/usr/bin/env bash
# tools/test_install_vm.sh — installer end-to-end on VM (#85 / #13).
#
# Runs install.sh in 3 modes against a freshly-wiped VM and asserts
# canonical post-install state. Plus a rollback test that corrupts
# one file mid-install and verifies the script restores from backup.
#
# Usage:
#   ./tools/test_install_vm.sh                      — all 3 modes + rollback
#   ./tools/test_install_vm.sh --modes a,b          — only modes a + b
#   ./tools/test_install_vm.sh --skip-rollback      — skip the rollback test
#
# Modes:
#   (a) bash --no-sudo                              — skip apt installs
#   (b) bash --headless --selected meld --status-json
#   (c) bash --headless --selected llama            — ollama via curl|sh
#
# Pre-reqs:
#   - SSH alias `vm-test` reachable
#   - VM has bash, python3, npm, sudo (for mode b/c apt installs)
#   - Run from BTerminal repo root

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="${VM_HOST:-vm-test}"
VM_PATH="${VM_PATH:-/home/michal/BTerminal}"
LOG_DIR="$REPO_ROOT/smoke-logs/install-vm"
MODES="a,b,c"
SKIP_ROLLBACK=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --modes)         MODES="$2"; shift 2 ;;
        --modes=*)       MODES="${1#*=}"; shift ;;
        --skip-rollback) SKIP_ROLLBACK=true; shift ;;
        --help|-h)       sed -n '4,22p' "$0"; exit 0 ;;
        *)               echo "Unknown: $1"; exit 2 ;;
    esac
done

mkdir -p "$LOG_DIR"
PASS=0; FAIL=0
declare -a FAIL_LIST=()

step_pass() {
    PASS=$((PASS + 1))
    printf "  \033[32m✓\033[0m %s\n" "$1"
}

step_fail() {
    FAIL=$((FAIL + 1))
    FAIL_LIST+=("$1")
    printf "  \033[31m✗\033[0m %s\n" "$1"
    [[ -n "${2:-}" ]] && printf "      %s\n" "$2"
}

vm_run() {
    # Run a one-liner on the VM. Captures stderr to the named log
    # file so per-step failures can be debugged after the fact.
    local logname="$1"; shift
    ssh -o ConnectTimeout=10 "$VM_HOST" "$@" \
        2> "$LOG_DIR/$logname.stderr.log"
}

wipe_vm_state() {
    vm_run "wipe" "
        rm -rf ~/.local/share/bterminal/bterminal \
                ~/.local/bin/{bterminal,ctx,tasks,consult,memory_wizard,claude_log,bterminal-launcher} \
                ~/.config/bterminal/install_errors.json
        true
    "
}

assert_layout() {
    # Returns 0 when canonical post-install state is present, else 1.
    vm_run "layout-$1" "
        test -f ~/.local/share/bterminal/bterminal/__init__.py &&
        test -L ~/.local/bin/bterminal &&
        test -x ~/.local/bin/ctx &&
        test -x ~/.local/bin/tasks &&
        test -x ~/.local/bin/consult &&
        test -x ~/.local/bin/memory_wizard
    "
}

assert_summary_block() {
    # The final 'echo \"=== ... installed successfully ===\"' line is
    # the canonical pass marker. plus [SUMMARY] block from #62 must
    # appear above it.
    local logfile="$1"
    grep -q '\[SUMMARY\]' "$logfile" \
        && grep -q 'installed successfully' "$logfile"
}

# ─── Preflight ─────────────────────────────────────────────────────────────

echo "=== install.sh VM smoke ($(date -u +%FT%TZ)) ==="
echo "VM: $VM_HOST  Path: $VM_PATH  Modes: $MODES  Logs: $LOG_DIR"
echo ""

if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$VM_HOST" \
        "echo OK" > /dev/null 2>&1; then
    echo "Cannot reach $VM_HOST" >&2
    exit 1
fi

# Always sync first so VM has the latest install.sh under test
"$REPO_ROOT/tools/vm_sync.sh" > "$LOG_DIR/vm_sync.log" 2>&1
if [[ $? -ne 0 ]]; then
    step_fail "vm_sync" "see $LOG_DIR/vm_sync.log"
fi

# ─── Mode A: --no-sudo ─────────────────────────────────────────────────────

if [[ ",$MODES," == *",a,"* ]]; then
    echo "[A] install.sh --no-sudo (skip apt)..."
    wipe_vm_state
    LOG="$LOG_DIR/mode-a.log"
    if vm_run "mode-a" "
        cd $VM_PATH
        bash install.sh --no-sudo
    " > "$LOG" 2>&1; then
        step_pass "mode A: install.sh --no-sudo exit 0"
    else
        rc=$?
        step_fail "mode A exit=$rc" "see $LOG"
    fi

    if assert_summary_block "$LOG"; then
        step_pass "mode A: [SUMMARY] block + 'installed successfully' marker"
    else
        step_fail "mode A: missing [SUMMARY] / success marker" "see $LOG"
    fi

    if assert_layout "a"; then
        step_pass "mode A: post-install layout (bterminal/ + bin symlinks)"
    else
        step_fail "mode A: layout missing" "see $LOG_DIR/layout-a.stderr.log"
    fi
fi

# ─── Mode B: --headless --selected meld --status-json ──────────────────────

if [[ ",$MODES," == *",b,"* ]]; then
    echo ""
    echo "[B] install.sh --headless --selected meld --status-json --no-sudo..."
    wipe_vm_state
    LOG="$LOG_DIR/mode-b.log"
    if vm_run "mode-b" "
        cd $VM_PATH
        bash install.sh --headless --selected meld --status-json --no-sudo
    " > "$LOG" 2>&1; then
        step_pass "mode B: install.sh --headless exit 0"
    else
        rc=$?
        step_fail "mode B exit=$rc" "see $LOG"
    fi

    # JSON stream: must contain a phase=done progress=100 line
    if grep -q '"phase": "done"' "$LOG" \
            && grep -q '"progress": 100' "$LOG"; then
        step_pass "mode B: JSON stream emitted phase=done progress=100"
    else
        step_fail "mode B: JSON stream incomplete" "see $LOG"
    fi

    # Selected whitelist: latex/pandoc must be 'skipped (not in --selected list)'
    if grep -q 'pdflatex.*not in --selected list\|pandoc.*not in --selected list' "$LOG"; then
        step_pass "mode B: --selected whitelist gates auto-tier (pandoc/latex skipped)"
    else
        step_fail "mode B: --selected whitelist not honored" "see $LOG"
    fi

    if assert_layout "b"; then
        step_pass "mode B: post-install layout"
    else
        step_fail "mode B: layout missing" "see $LOG_DIR/layout-b.stderr.log"
    fi
fi

# ─── Mode C: --headless --selected llama ────────────────────────────────────

if [[ ",$MODES," == *",c,"* ]]; then
    echo ""
    echo "[C] install.sh --headless --selected llama --status-json..."
    # Don't wipe ~/.ollama (keeps qwen if pulled). Only BT files.
    wipe_vm_state
    LOG="$LOG_DIR/mode-c.log"
    # Note: --selected llama needs sudo for ollama's systemd unit;
    # skip --no-sudo here. If running on a VM without sudo, set
    # SKIP_MODE_C=1 in env.
    if [[ "${SKIP_MODE_C:-0}" == "1" ]]; then
        step_pass "mode C: skipped (SKIP_MODE_C=1)"
    elif vm_run "mode-c" "
        cd $VM_PATH
        bash install.sh --headless --selected llama --status-json
    " > "$LOG" 2>&1; then
        step_pass "mode C: install.sh --selected llama exit 0"
        # ollama daemon should be installed afterwards
        if vm_run "ollama-check" "command -v ollama" > /dev/null 2>&1; then
            step_pass "mode C: ollama binary on \$PATH after install"
        else
            step_fail "mode C: ollama not installed" "see $LOG"
        fi
    else
        rc=$?
        step_fail "mode C exit=$rc" "see $LOG"
    fi
fi

# ─── Rollback test ─────────────────────────────────────────────────────────

if [[ "$SKIP_ROLLBACK" != true ]]; then
    echo ""
    echo "[R] Rollback test — corrupt mid-install + verify backup restored..."
    LOG="$LOG_DIR/rollback.log"

    # First: a clean install so we have something to restore.
    wipe_vm_state
    vm_run "rollback-prep" "cd $VM_PATH && bash install.sh --no-sudo" \
        > /dev/null 2>&1

    # Capture VERSION + a checksum of bterminal/__init__.py — the
    # rollback should preserve both after corruption.
    PRE_HASH=$(vm_run "rollback-prehash" \
        "sha256sum ~/.local/share/bterminal/bterminal/__init__.py" \
        2>/dev/null | awk '{print $1}')

    # ─── (c) Fail just-after-backup-created — phase 5 (Files install) ──────
    #
    # Existing rollback test pattern: inject `false` right after
    # the phase 5 banner. install.sh has trap '_on_error' ERR
    # which restores from BACKUP_DIR. The success marker is
    # BTERMINAL_ROLLBACK_OK.
    vm_run "rollback-corrupt" "
        cp $VM_PATH/install.sh /tmp/install_corrupt.sh
        sed -i 's|^echo \"\\[5/7\\] Installing BTerminal files\\.\\.\\.\"|&\\nfalse|' /tmp/install_corrupt.sh
    "

    # Run corrupted install — expect non-zero exit + rollback marker
    if vm_run "rollback-run" "
        cd $VM_PATH
        bash /tmp/install_corrupt.sh --no-sudo
    " > "$LOG" 2>&1; then
        step_fail "rollback (c): corrupted install unexpectedly succeeded" "see $LOG"
    else
        step_pass "rollback (c) phase-5: corrupted install exit non-zero"
    fi

    # Rollback marker
    if grep -q 'BTERMINAL_ROLLBACK_OK' "$LOG"; then
        step_pass "rollback (c): BTERMINAL_ROLLBACK_OK marker emitted"
    else
        step_fail "rollback (c): missing BTERMINAL_ROLLBACK_OK marker" "see $LOG"
    fi

    # Verify __init__.py still intact (same hash as before)
    POST_HASH=$(vm_run "rollback-posthash" \
        "sha256sum ~/.local/share/bterminal/bterminal/__init__.py" \
        2>/dev/null | awk '{print $1}')
    if [[ -n "$PRE_HASH" && "$PRE_HASH" == "$POST_HASH" ]]; then
        step_pass "rollback (c): bterminal/__init__.py restored from backup"
    else
        step_fail "rollback (c): __init__.py hash drift" \
                  "pre=$PRE_HASH post=$POST_HASH"
    fi

    # Cleanup phase-5 corrupt script
    vm_run "rollback-cleanup" "rm -f /tmp/install_corrupt.sh" >/dev/null

    # ─── (a) Fail PHASE 1 — BEFORE BACKUP_DIR populated (#133) ────────────
    #
    # Inject `false` right after the [1/7] Runtime banner — long
    # before BACKUP_DIR is mktemp'd at line ~632. install.sh's
    # _on_error trap runs but BACKUP_DIR is empty string ("");
    # the no-backup branch fires + emits
    # BTERMINAL_FRESH_INSTALL_FAILED instead of BTERMINAL_ROLLBACK_OK.
    LOG_A="$LOG_DIR/rollback-phase1.log"
    vm_run "rollback-corrupt-1" "
        cp $VM_PATH/install.sh /tmp/install_corrupt_1.sh
        sed -i 's|^echo \"\\[1/7\\] Checking runtime\\.\\.\\.\"|&\\nfalse|' /tmp/install_corrupt_1.sh
    "
    if vm_run "rollback-run-1" "
        cd $VM_PATH
        bash /tmp/install_corrupt_1.sh --no-sudo
    " > "$LOG_A" 2>&1; then
        step_fail "rollback (a) phase-1: install unexpectedly succeeded" \
                  "see $LOG_A"
    else
        step_pass "rollback (a) phase-1: install exit non-zero"
    fi

    # FRESH_INSTALL_FAILED marker (no backup branch of _on_error)
    if grep -q 'BTERMINAL_FRESH_INSTALL_FAILED' "$LOG_A"; then
        step_pass "rollback (a): BTERMINAL_FRESH_INSTALL_FAILED marker emitted"
    else
        step_fail "rollback (a): missing FRESH_INSTALL_FAILED marker" \
                  "see $LOG_A"
    fi

    # ROLLBACK_OK MUST NOT fire when there's no backup
    if grep -q 'BTERMINAL_ROLLBACK_OK' "$LOG_A"; then
        step_fail "rollback (a): unexpected ROLLBACK_OK in phase-1 fail" \
                  "see $LOG_A"
    else
        step_pass "rollback (a): no ROLLBACK_OK marker (no backup to restore)"
    fi

    # User-friendly message — install.sh's no-backup branch
    # mentions 'fresh install' or 'Fix the error above'
    if grep -qE 'fresh install|Fix the error above' "$LOG_A"; then
        step_pass "rollback (a): user-facing 'no backup' message present"
    else
        step_fail "rollback (a): no actionable message in output" \
                  "see $LOG_A"
    fi

    vm_run "rollback-cleanup-1" "rm -f /tmp/install_corrupt_1.sh" >/dev/null

    # ─── (b) Fail PHASE 2.5 — also pre-backup ────────────────────────────
    #
    # Phase 2.5 (GitHub Copilot CLI check) runs before BACKUP_DIR
    # too. Same expected outcome as (a): FRESH_INSTALL_FAILED.
    # Pin to catch a refactor that moves BACKUP_DIR earlier in
    # the script — test would then detect both branches taking
    # the rollback path inappropriately.
    LOG_B="$LOG_DIR/rollback-phase25.log"
    vm_run "rollback-corrupt-25" "
        cp $VM_PATH/install.sh /tmp/install_corrupt_25.sh
        sed -i 's|^echo \"\\[2.5/7\\] Checking GitHub Copilot CLI\\.\\.\\.\"|&\\nfalse|' /tmp/install_corrupt_25.sh
    "
    if vm_run "rollback-run-25" "
        cd $VM_PATH
        bash /tmp/install_corrupt_25.sh --no-sudo
    " > "$LOG_B" 2>&1; then
        step_fail "rollback (b) phase-2.5: install unexpectedly succeeded" \
                  "see $LOG_B"
    else
        step_pass "rollback (b) phase-2.5: install exit non-zero"
    fi

    if grep -q 'BTERMINAL_FRESH_INSTALL_FAILED' "$LOG_B"; then
        step_pass "rollback (b): BTERMINAL_FRESH_INSTALL_FAILED marker emitted"
    else
        step_fail "rollback (b): missing FRESH_INSTALL_FAILED marker" \
                  "see $LOG_B"
    fi

    if ! grep -q 'BTERMINAL_ROLLBACK_OK' "$LOG_B"; then
        step_pass "rollback (b): no ROLLBACK_OK in phase-2.5 fail (correct)"
    else
        step_fail "rollback (b): unexpected ROLLBACK_OK marker" \
                  "see $LOG_B"
    fi

    vm_run "rollback-cleanup-25" "rm -f /tmp/install_corrupt_25.sh" >/dev/null
fi

# ─── Final summary ─────────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo "Total: $PASS passed, $FAIL failed"
if [[ $FAIL -eq 0 ]]; then
    printf "\033[32m=== install.sh VM smoke OK ===\033[0m\n"
    exit 0
else
    printf "\033[31m=== install.sh VM smoke FAILED ===\033[0m\n"
    echo "Failed:"
    for f in "${FAIL_LIST[@]}"; do echo "  • $f"; done
    echo "Logs: $LOG_DIR/"
    exit 1
fi
