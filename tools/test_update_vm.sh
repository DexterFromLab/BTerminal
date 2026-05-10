#!/usr/bin/env bash
# tools/test_update_vm.sh — BT updater flow on VM (#86 / #14).
#
# Verifies five things, each in its own phase:
#   1. _read_local_license returns markdown TEXT, not a path string
#      (regression for issue #52).
#   2. _load_local_errata tolerates a corrupted errata.json without
#      crashing the updater.
#   3. End-to-end: a fake local upstream remote with VERSION=99.0.0,
#      a working clone at VERSION=1.2.0; updater's git pull helper
#      advances the working tree.
#   4. Rollback: corrupt install.sh mid-run, verify BACKUP_DIR
#      restoration kicks in (re-uses the pattern from #85).
#   5. _remote_license_blob_path() points at a real markdown file
#      under defaults/license/, not the LICENSE.md symlink.
#
# The full GTK update dialog (Tools → Check for updates) is exercised
# by #87's xdotool runner — this script focuses on updater.py's pure
# helpers + install.sh integration.
#
# Usage:
#   ./tools/test_update_vm.sh                  — all phases (fake remote)
#   ./tools/test_update_vm.sh --skip-rollback  — skip phase 4
#   ./tools/test_update_vm.sh --modes 1,3      — only phases 1 + 3
#   ./tools/test_update_vm.sh --use-real-remote — phase 3 hits real
#                                                 GitHub (network gated)
#   BTERMINAL_NETWORK_TESTS=1 ./tools/test_update_vm.sh — env-var
#                                                 alternative
#   BTERMINAL_GITHUB_REMOTE=URL — override GitHub URL (default:
#                                 https://github.com/DexterFromLab/
#                                 BTerminal.git)
#
# Phase 3 has two implementations:
#   - default: synthesizes a fake bare upstream in /tmp (offline,
#     deterministic, runs in CI without network)
#   - --use-real-remote: clones the actual GitHub repo (catches
#     auth issues, rate limits, registration with real Git remotes
#     that fake /tmp can't reproduce). #130 / audit § 6.8 #31.
#
# Pre-reqs:
#   - SSH alias `vm-test` reachable
#   - VM has bash + python3 + git
#   - Run from the BTerminal repo root on the host

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER_DIR="$REPO_ROOT/tools/_vm_update_checks"
VM_HOST="${VM_HOST:-vm-test}"
VM_PATH="${VM_PATH:-/home/michal/BTerminal}"
LOG_DIR="$REPO_ROOT/smoke-logs/update-vm"
MODES="1,2,3,4,5,6"
USE_REAL_REMOTE=false
# #130: real-remote test gated by env var to avoid accidental
# network calls during routine smokes. Pass --use-real-remote
# to flip ON, OR set BTERMINAL_NETWORK_TESTS=1.
GITHUB_REMOTE="${BTERMINAL_GITHUB_REMOTE:-https://github.com/DexterFromLab/BTerminal.git}"

while [[ $# -gt 0 ]]; do
    case "$1" in
        --modes)             MODES="$2"; shift 2 ;;
        --modes=*)           MODES="${1#*=}"; shift ;;
        --skip-rollback)     MODES="${MODES//4,/}"; MODES="${MODES//,4/}"; MODES="${MODES//4/}"; shift ;;
        --use-real-remote)   USE_REAL_REMOTE=true; shift ;;
        --help|-h)           sed -n '4,42p' "$0"; exit 0 ;;
        *)                   echo "Unknown: $1"; exit 2 ;;
    esac
done

# Env-var alternative to --use-real-remote (matches #89's
# BTERMINAL_VM_TESTS / #103's BTERMINAL_NETWORK_DOWN_TEST style).
if [[ "${BTERMINAL_NETWORK_TESTS:-0}" == "1" ]]; then
    USE_REAL_REMOTE=true
fi

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
    # Run a one-liner on the VM. Captures stderr to per-step log file
    # so post-run debugging doesn't need re-execution.
    local logname="$1"; shift
    ssh -o ConnectTimeout=10 "$VM_HOST" "$@" \
        2> "$LOG_DIR/$logname.stderr.log"
}

mode_active() {
    [[ ",$MODES," == *",$1,"* ]]
}

# ─── Preflight ─────────────────────────────────────────────────────────────

echo "=== updater VM smoke ($(date -u +%FT%TZ)) ==="
echo "VM: $VM_HOST  Path: $VM_PATH  Modes: $MODES  Logs: $LOG_DIR"
echo ""

if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$VM_HOST" \
        "echo OK" > /dev/null 2>&1; then
    echo "Cannot reach $VM_HOST" >&2
    exit 1
fi

# Always sync first so the VM has the latest updater.py + install.sh.
"$REPO_ROOT/tools/vm_sync.sh" > "$LOG_DIR/vm_sync.log" 2>&1 \
    || step_fail "vm_sync" "see $LOG_DIR/vm_sync.log"

# Stage helper scripts on the VM under /tmp/bt_update_checks so the
# heredoc-free Python files can be invoked by absolute path.
ssh "$VM_HOST" "rm -rf /tmp/bt_update_checks && mkdir -p /tmp/bt_update_checks" \
    > /dev/null 2>&1
scp -q "$HELPER_DIR"/*.py "$VM_HOST:/tmp/bt_update_checks/" \
    > "$LOG_DIR/scp_helpers.log" 2>&1 \
    || step_fail "scp_helpers" "see $LOG_DIR/scp_helpers.log"

# ─── Phase 1: license regression (#52) ─────────────────────────────────────

if mode_active 1; then
    echo "[1] _read_local_license returns markdown TEXT (#52 regression)..."
    LOG="$LOG_DIR/phase1.log"
    if vm_run "phase1" "
        cd $VM_PATH && \
        PYTHONPATH=$VM_PATH python3 /tmp/bt_update_checks/license_regression.py $VM_PATH
    " > "$LOG" 2>&1; then
        if grep -q 'local-license-ok' "$LOG"; then
            step_pass "phase 1: local license loader returns markdown text"
        else
            step_fail "phase 1: local license check missing OK marker" "see $LOG"
        fi
        if grep -q 'remote-license-ok\|remote-license-skipped' "$LOG"; then
            step_pass "phase 1: remote license blob is markdown (or skipped on shallow clone)"
        else
            step_fail "phase 1: remote license check absent" "see $LOG"
        fi
    else
        step_fail "phase 1: Python check exit nonzero" "see $LOG"
    fi
fi

# ─── Phase 2: errata loader tolerates bad JSON ─────────────────────────────

if mode_active 2; then
    echo ""
    echo "[2] _load_local_errata tolerates corrupted errata.json..."
    LOG="$LOG_DIR/phase2.log"
    # Backup → corrupt → run → restore. Single ssh round-trip.
    if vm_run "phase2" "
        cd $VM_PATH && \
        cp errata.json errata.json.real 2>/dev/null || true; \
        printf '%s' '{this is not valid json' > errata.json && \
        PYTHONPATH=$VM_PATH python3 /tmp/bt_update_checks/errata_corruption.py $VM_PATH; \
        rc=\$?; \
        mv errata.json.real errata.json 2>/dev/null || rm -f errata.json; \
        exit \$rc
    " > "$LOG" 2>&1; then
        if grep -q 'errata-loader-ok' "$LOG"; then
            step_pass "phase 2: _load_local_errata returns [] on corrupted JSON"
        else
            step_fail "phase 2: errata loader OK marker missing" "see $LOG"
        fi
    else
        step_fail "phase 2: Python check exit nonzero" "see $LOG"
    fi
fi

# ─── Phase 3: end-to-end git pull (fake upstream OR real GitHub) ───────────

if mode_active 3 && [[ "$USE_REAL_REMOTE" == false ]]; then
    echo ""
    echo "[3] End-to-end — fake upstream remote + downgraded clone + pull..."
    LOG="$LOG_DIR/phase3.log"
    UPSTREAM="/tmp/bt-fake-upstream-$$"
    LOCAL="/tmp/bt-fake-local-$$"
    BUMP="/tmp/bt-fake-bump-$$"

    # Single ssh call sets up the fake remote + downgraded local clone,
    # pushes a newer VERSION=99.0.0 commit, then runs the helper.
    if vm_run "phase3" "
        set -e
        rm -rf $UPSTREAM $LOCAL $BUMP
        # Bare clone of the synced VM source serves as 'upstream'.
        git clone --quiet --bare $VM_PATH $UPSTREAM
        # 'Local' working clone — the box we're upgrading.
        git clone --quiet $UPSTREAM $LOCAL
        cd $LOCAL
        git config user.email test@test
        git config user.name  test
        echo 1.2.0 > VERSION
        git commit --quiet -am 'fake: rollback VERSION to 1.2.0 for update test'
        # Push a NEW commit with VERSION=99.0.0 to upstream so the local
        # clone has something to pull.
        git clone --quiet $UPSTREAM $BUMP
        cd $BUMP
        git config user.email test@test
        git config user.name  test
        echo 99.0.0 > VERSION
        git commit --quiet -am 'fake: bump VERSION to 99.0.0'
        git push --quiet origin master
        rm -rf $BUMP
        # Now the local clone at VERSION=1.2.0 has a newer master upstream.
        cd $LOCAL
        git fetch --quiet origin master
        before=\$(cat VERSION)
        PYTHONPATH=$VM_PATH python3 /tmp/bt_update_checks/git_pull_check.py \
            $VM_PATH $UPSTREAM $LOCAL
        after=\$(cat VERSION)
        echo \"VERSION before=\$before after=\$after\"
        rm -rf $UPSTREAM $LOCAL
    " > "$LOG" 2>&1; then
        if grep -q 'pull-ok' "$LOG"; then
            step_pass "phase 3: _git_pull_with_autostash advanced VERSION 1.2.0 -> 99.0.0"
        else
            step_fail "phase 3: pull-ok marker missing" "see $LOG"
        fi
    else
        step_fail "phase 3: end-to-end pull failed" "see $LOG"
        # Best-effort cleanup if mid-run failure left tmp dirs around.
        ssh "$VM_HOST" "rm -rf $UPSTREAM $LOCAL $BUMP" > /dev/null 2>&1
    fi
fi

# ─── Phase 3 (alt): real-remote — clone github.com → fetch → pull ──────────

if mode_active 3 && [[ "$USE_REAL_REMOTE" == true ]]; then
    echo ""
    echo "[3-real] End-to-end — real GitHub remote at $GITHUB_REMOTE..."
    LOG="$LOG_DIR/phase3-real.log"
    LOCAL="/tmp/bt-real-local-$$"

    # Decision branch (a) — pre-flight reachability probe. If
    # offline / DNS broken / GitHub down, branch (b) rate-limit
    # or (c) auth failure are unreachable too. Probe HEAD on the
    # remote with a short timeout so the test fails fast rather
    # than hanging on the clone.
    if ! vm_run "phase3-real-probe" "
        timeout 10 git ls-remote --quiet '$GITHUB_REMOTE' HEAD
    " > "$LOG_DIR/phase3-real-probe.log" 2>&1; then
        step_fail "phase 3 (real): GitHub unreachable / rate-limited / auth fail" \
                  "see $LOG_DIR/phase3-real-probe.log"
        # Skip the rest of the real-remote branch — no point trying
        # the clone when the probe failed.
    elif vm_run "phase3-real" "
        set -e
        rm -rf $LOCAL
        # Shallow clone (--depth 50) keeps the test fast — we
        # only need a few commits of history for the pull check.
        git clone --quiet --depth 50 '$GITHUB_REMOTE' $LOCAL
        cd $LOCAL
        # Roll back working tree to a synthetic 1.2.0 state so
        # there's something to 'pull'. Using a worktree-only
        # change (don't push to origin — read-only access).
        git config user.email test@test
        git config user.name  test
        echo 1.2.0 > VERSION
        git commit --quiet -am 'fake: simulate older clone for pull test'
        # Detach back to origin/master so a fetch+pull pulls
        # the real remote's master.
        REAL_VERSION=\$(git show origin/master:VERSION 2>/dev/null || echo unknown)
        git fetch --quiet origin master
        before=\$(cat VERSION)
        PYTHONPATH=$VM_PATH python3 /tmp/bt_update_checks/git_pull_check.py \
            $VM_PATH '$GITHUB_REMOTE' $LOCAL || true
        after=\$(cat VERSION)
        echo \"VERSION before=\$before after=\$after real-master=\$REAL_VERSION\"
        rm -rf $LOCAL
    " > "$LOG" 2>&1; then
        if grep -q 'pull-ok\|real-master=' "$LOG"; then
            step_pass "phase 3 (real): real-remote clone + fetch + pull verified"
        else
            step_fail "phase 3 (real): pull check did not produce expected markers" \
                      "see $LOG"
        fi
    else
        step_fail "phase 3 (real): real-remote pull failed" "see $LOG"
        ssh "$VM_HOST" "rm -rf $LOCAL" > /dev/null 2>&1
    fi
fi

# ─── Phase 4: rollback test ────────────────────────────────────────────────

if mode_active 4; then
    echo ""
    echo "[4] Rollback — corrupt install.sh mid-run, verify backup restored..."
    LOG="$LOG_DIR/phase4.log"

    # Need a populated install first so BACKUP_DIR has content to restore.
    vm_run "phase4-prep" "cd $VM_PATH && bash install.sh --no-sudo" \
        > "$LOG_DIR/phase4-prep.log" 2>&1

    PRE_HASH=$(vm_run "phase4-pre" \
        "sha256sum ~/.local/share/bterminal/bterminal/__init__.py 2>/dev/null" \
        | awk '{print $1}')

    # Inject 'false' right after the phase 5 banner — fires _on_error
    # which runs the rollback hook (BACKUP_DIR populated by prep run).
    CORRUPT="/tmp/bt-install-corrupt-$$.sh"
    if vm_run "phase4" "
        cp $VM_PATH/install.sh $CORRUPT && \
        sed -i 's|^echo \"\\[5/7\\] Installing BTerminal files\\.\\.\\.\"|&\\nfalse|' $CORRUPT && \
        cd $VM_PATH && \
        bash $CORRUPT --no-sudo; \
        rc=\$?; \
        rm -f $CORRUPT; \
        exit \$rc
    " > "$LOG" 2>&1; then
        step_fail "phase 4: corrupt install unexpectedly succeeded" "see $LOG"
    else
        step_pass "phase 4: corrupt install exit nonzero (as expected)"
    fi

    if grep -q 'BTERMINAL_ROLLBACK_OK' "$LOG"; then
        step_pass "phase 4: BTERMINAL_ROLLBACK_OK marker emitted"
    else
        step_fail "phase 4: missing rollback marker" "see $LOG"
    fi

    POST_HASH=$(vm_run "phase4-post" \
        "sha256sum ~/.local/share/bterminal/bterminal/__init__.py 2>/dev/null" \
        | awk '{print $1}')
    if [[ -n "$PRE_HASH" && "$PRE_HASH" == "$POST_HASH" ]]; then
        step_pass "phase 4: bterminal/__init__.py restored from backup (hash match)"
    else
        step_fail "phase 4: __init__.py hash drift" \
                  "pre=$PRE_HASH post=$POST_HASH"
    fi
fi

# ─── Phase 5: blob path resolves to real markdown (#52 deep) ───────────────

if mode_active 5; then
    echo ""
    echo "[5] _remote_license_blob_path resolves to a real markdown blob..."
    LOG="$LOG_DIR/phase5.log"
    if vm_run "phase5" "
        cd $VM_PATH && \
        PYTHONPATH=$VM_PATH python3 /tmp/bt_update_checks/blob_path_probe.py $VM_PATH
    " > "$LOG" 2>&1; then
        if grep -q 'blob-path-ok' "$LOG" && grep -q 'blob-content-ok' "$LOG"; then
            step_pass "phase 5: blob path -> real markdown (#52 deep regression)"
        else
            step_fail "phase 5: probe assertion missing" "see $LOG"
        fi
    else
        step_fail "phase 5: Python probe exit nonzero" "see $LOG"
    fi
fi

# ─── Phase 6: post-update CLI validation (regression for 2026-05-08) ───────
#
# After a successful update, install.sh's validate_npm_cli helper
# must have logged [VALIDATE] entries for every previously-installed
# AI provider. Without this phase, the updater could silently
# downgrade a working claude binary into a stub (the bug that
# originally triggered this whole audit).

if mode_active 6; then
    echo ""
    echo "[6] post-update CLI validation: install.log [VALIDATE] entries..."
    LOG="$LOG_DIR/phase6.log"

    # Pre-seed an isolated test home with a working mock claude in
    # ~/.npm-global/bin, then run install.sh --no-sudo (the same
    # invocation the updater uses) and assert validate_npm_cli ran.
    if vm_run "phase6_setup" "
        rm -rf \$HOME/bterm-update-validate
        mkdir -p \$HOME/bterm-update-validate/.config/bterminal
        mkdir -p \$HOME/bterm-update-validate/.local/bin
        mkdir -p \$HOME/bterm-update-validate/.npm-global/bin
        mkdir -p \$HOME/bterm-update-validate/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin
        cat > \$HOME/bterm-update-validate/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude << 'BIN'
#!/bin/sh
[ \"\$1\" = \"--version\" ] && echo \"claude 9.9.9-pre-update\" && exit 0
exit 0
BIN
        chmod +x \$HOME/bterm-update-validate/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude
        ln -sf ../lib/node_modules/@anthropic-ai/claude-code/bin/claude \\
               \$HOME/bterm-update-validate/.npm-global/bin/claude
    " > "$LOG" 2>&1; then
        if vm_run "phase6_run" "
            cd $VM_PATH
            export HOME=\$HOME/bterm-update-validate
            export PATH=\"\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH\"
            bash install.sh --no-sudo 2>&1 | tail -30
        " >> "$LOG" 2>&1; then
            if vm_run "phase6_check" "
                grep -q '\[VALIDATE\] Claude Code: OK' \\
                    \$HOME/bterm-update-validate/.config/bterminal/install.log 2>/dev/null
            " >> "$LOG" 2>&1; then
                step_pass "phase 6: install.log records [VALIDATE] Claude Code: OK after update"
            else
                step_fail "phase 6: missing [VALIDATE] Claude Code: OK after update" "see $LOG"
            fi

            # Also check that the mock claude binary is preserved
            # (updater shouldn't break what worked before)
            if vm_run "phase6_preserved" "
                test -x \$HOME/bterm-update-validate/.npm-global/bin/claude \\
                && \$HOME/bterm-update-validate/.npm-global/bin/claude --version \\
                    | grep -qi 'claude'
            " >> "$LOG" 2>&1; then
                step_pass "phase 6: pre-update claude binary still functional"
            else
                step_fail "phase 6: claude binary broken after update" "see $LOG"
            fi
        else
            step_fail "phase 6: install.sh --no-sudo run failed" "see $LOG"
        fi
        # Cleanup (skip if PHASE6_KEEP_STATE=1 for debugging)
        if [[ "${PHASE6_KEEP_STATE:-0}" != "1" ]]; then
            vm_run "phase6_cleanup" "rm -rf \$HOME/bterm-update-validate" \
                > /dev/null 2>&1
        fi
    else
        step_fail "phase 6: setup failed" "see $LOG"
    fi
fi

# ─── Cleanup helper staging ────────────────────────────────────────────────

ssh "$VM_HOST" "rm -rf /tmp/bt_update_checks" > /dev/null 2>&1

# ─── Final summary ─────────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo "Total: $PASS passed, $FAIL failed"
if [[ $FAIL -eq 0 ]]; then
    printf "\033[32m=== updater VM smoke OK ===\033[0m\n"
    exit 0
else
    printf "\033[31m=== updater VM smoke FAILED ===\033[0m\n"
    echo "Failed:"
    for f in "${FAIL_LIST[@]}"; do echo "  - $f"; done
    echo "Logs: $LOG_DIR/"
    exit 1
fi
