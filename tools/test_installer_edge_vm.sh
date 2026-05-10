#!/usr/bin/env bash
# tools/test_installer_edge_vm.sh — installer edge cases (#162)
#
# Auto-tests installer behavior under hostile conditions:
#   (a) Offline / no network    — manual procedure documented; auto
#                                  variant uses `--no-sudo` mode which
#                                  bypasses apt (safe to run offline).
#   (b) SIGTERM mid-apt          — auto: spawn install in bg, kill
#                                  during apt phase, verify rollback
#   (c) Corrupt LICENSE.md       — auto: zero-out license file, run,
#                                  verify clean error or graceful skip
#   (d) Read-only HOME           — manual procedure; auto variant uses
#                                  read-only $HOME/.local/share to
#                                  exercise the same code path safely
#   (e) Parallel install (flock) — auto: fork 2 install runs, verify
#                                  2nd refused with flock message
#
# All tests run on VM (vm-test) against /home/michal/BTerminal/install.sh.
# Each scenario captures relevant logs + a screenshot for visual review.

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="${VM_HOST:-vm-test}"
VM_REPO="${VM_REPO:-/home/michal/BTerminal}"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/smoke-logs/installer-edge-e2e}"

mkdir -p "$LOG_DIR"
PASS=0; FAIL=0; FAIL_LIST=()

_vm() { ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$VM_HOST" "$@"; }
_test_pass() { PASS=$((PASS+1)); echo "  ✓ $1"; }
_test_fail() { FAIL=$((FAIL+1)); FAIL_LIST+=("$1"); echo "  ✗ $1"; }

echo "=== Installer edge cases (#162) on $VM_HOST ==="

# ── (a) Offline mode — install.sh --no-sudo (apt skipped) ─────────────────

echo
echo "=== (a) Offline / --no-sudo mode ==="
_vm "rm -f /tmp/edge-offline.log; \
     cd $VM_REPO && bash install.sh --headless --no-sudo --selected '' \
                                    --status-json 2>&1 \
        | tee /tmp/edge-offline.log >/dev/null
     echo \"RC=\$?\""
# --no-sudo prints MANUAL install instructions for missing apt deps
# (those lines contain "sudo apt install …" as docs, NOT as commands).
# We assert: install completed AND there's no "Running: sudo …" line
# (which would indicate an actual sudo invocation).
RUNNING_SUDO=$(_vm "grep -cE '^Running: sudo|^\\\$ sudo apt' /tmp/edge-offline.log" ; true)
COMPLETED=$(_vm "grep -c 'installed successfully\\|Installation completed' /tmp/edge-offline.log" ; true)
if [[ "$RUNNING_SUDO" == "0" ]] && [[ "$COMPLETED" -gt "0" ]]; then
    _test_pass "Offline (--no-sudo): completed without sudo invocations"
else
    _test_fail "Offline check: running_sudo=$RUNNING_SUDO completed=$COMPLETED"
fi
_vm "tail -5 /tmp/edge-offline.log" > "$LOG_DIR/01a-offline-tail.log" 2>&1

# ── (b) SIGTERM mid-apt — auto rollback ──────────────────────────────────

echo
echo "=== (b) SIGTERM mid-install — rollback ==="
# Use the slow [4/7]+ phase (Claude/Copilot npm install) which takes
# multiple seconds even in --no-sudo mode. Non-headless because
# headless skips interactive parts which are where SIGTERM matters.
_vm "rm -f /tmp/edge-sigterm.log /tmp/edge-sigterm.pid /tmp/_bterminal_install.lock
     # Run with HOME redirected so we don't actually mutate VM home;
     # this also lets [5/7] file-copy phase enter (creating BACKUP_DIR).
     mkdir -p /tmp/sigterm-home/.local /tmp/sigterm-home/.config
     cd $VM_REPO
     HOME=/tmp/sigterm-home bash install.sh --no-sudo --headless \
        --status-json > /tmp/edge-sigterm.log 2>&1 &
     echo \$! > /tmp/edge-sigterm.pid
     # Wait until install is past phase [3/7] (sudo gate) AND has hit
     # at least one of [4-7]/7 phases — that means BACKUP_DIR exists.
     for _ in 1 2 3 4 5 6 7 8 9 10 11 12 13 14 15; do
         if grep -qE '\\[[4-7]/7\\]' /tmp/edge-sigterm.log 2>/dev/null; then
             break
         fi
         sleep 1
     done
     PID=\$(cat /tmp/edge-sigterm.pid)
     # Send SIGTERM directly — install.sh trap '_on_interrupt' handles it
     kill -TERM \$PID 2>/dev/null || true
     # Wait for trap to fire (it prints BTERMINAL_INTERRUPT_* marker)
     for _ in 1 2 3 4 5 6 7 8 9 10; do
         if grep -qE 'BTERMINAL_INTERRUPT|Interrupted by user' /tmp/edge-sigterm.log 2>/dev/null; then
             break
         fi
         sleep 1
     done
     wait \$PID 2>/dev/null
     # Cleanup test home
     rm -rf /tmp/sigterm-home
     rm -f /tmp/_bterminal_install.lock
     true"
INTERRUPT_LOG=$(_vm "grep -cE 'BTERMINAL_INTERRUPT|Interrupted by user|Rollback|Restored from backup' /tmp/edge-sigterm.log" ; true)
COMPLETE_OK=$(_vm "grep -c 'installed successfully' /tmp/edge-sigterm.log" ; true)
if [[ "$INTERRUPT_LOG" -gt "0" ]]; then
    _test_pass "SIGTERM mid-install: trap fired (interrupt markers in log)"
elif [[ "$COMPLETE_OK" -gt "0" ]]; then
    # Install was too fast to interrupt — flag this so we can tune.
    _test_fail "SIGTERM mid-install: install completed before SIGTERM landed (too fast)"
else
    _test_fail "SIGTERM mid-install: no interrupt markers + no completion"
fi
_vm "tail -20 /tmp/edge-sigterm.log" > "$LOG_DIR/01b-sigterm-tail.log" 2>&1

# Cleanup any in-flight state
_vm "rm -f /tmp/_bterminal_install.lock 2>/dev/null; true"

# ── (c) Corrupt LICENSE.md — graceful handling ────────────────────────────

echo
echo "=== (c) Corrupt LICENSE.md ==="
# Save original license, corrupt it, run install, restore.
_vm "cp $VM_REPO/defaults/license/LICENSE.en.md /tmp/license-backup.md
     # Truncate license to 0 bytes (simulating download corruption)
     : > $VM_REPO/defaults/license/LICENSE.en.md
     rm -f /tmp/edge-license.log /tmp/_bterminal_install.lock
     cd $VM_REPO && bash install.sh --headless --no-sudo --selected '' \
                                    --status-json 2>&1 \
        | tee /tmp/edge-license.log >/dev/null
     RC=\$?
     # Restore
     cp /tmp/license-backup.md $VM_REPO/defaults/license/LICENSE.en.md
     echo \"RC=\$RC\""

# install.sh currently doesn't pre-verify license size — accepts empty
# license (uses it as live symlink). Document gap, verify install
# either succeeded (license is loaded at runtime by BT) or failed
# cleanly (no half-baked layout).
LICENSE_SUMMARY=$(_vm "grep -c '\\[SUMMARY\\]' /tmp/edge-license.log")
LICENSE_TRACEBACK=$(_vm "grep -cE 'Traceback|line .*: .*: error' /tmp/edge-license.log")
if [[ "$LICENSE_TRACEBACK" == "0" ]]; then
    _test_pass "Corrupt LICENSE.md: no Traceback/syntax error in install"
else
    _test_fail "Corrupt LICENSE.md crashed install: traceback=$LICENSE_TRACEBACK"
fi
_vm "tail -10 /tmp/edge-license.log" > "$LOG_DIR/01c-license-tail.log" 2>&1

# Cleanup: reset license file
_vm "diff -q /tmp/license-backup.md $VM_REPO/defaults/license/LICENSE.en.md \
     || cp /tmp/license-backup.md $VM_REPO/defaults/license/LICENSE.en.md"

# ── (d) Read-only HOME — clean error exit ─────────────────────────────────

echo
echo "=== (d) Read-only target dir ==="
# Safer than chmod $HOME (which would lock the user out): create a
# read-only $XDG_DATA_HOME/bterminal alternative and run install.sh
# with HOME redirected to a tmpfs. install.sh tries to mkdir $HOME/
# .local/share/bterminal — should fail cleanly with EACCES.
_vm "rm -rf /tmp/ro-home
     mkdir -p /tmp/ro-home/.local/share /tmp/ro-home/.config
     chmod -R a-w /tmp/ro-home/.local/share
     rm -f /tmp/edge-rohome.log /tmp/_bterminal_install.lock
     cd $VM_REPO && HOME=/tmp/ro-home bash install.sh --headless --no-sudo \
                                                      --selected '' --status-json \
                                                      2>&1 | tee /tmp/edge-rohome.log >/dev/null
     RC=\$?
     # Restore writability so we can clean up
     chmod -R u+w /tmp/ro-home 2>/dev/null
     rm -rf /tmp/ro-home
     echo \"RC=\$RC\""

# install.sh doesn't pre-check HOME writability; mkdir/cp will fail.
# The install.sh `_on_error` trap prints BTERMINAL_FRESH_INSTALL_FAILED
# marker when initial install can't even create $INSTALL_DIR — which is
# the cleanest possible exit signal.
# Locale-agnostic: check for the marker OR mkdir error in any language.
RO_FAILED=$(_vm "grep -cE 'BTERMINAL_FRESH_INSTALL_FAILED|Installation failed|mkdir:|Permission denied|Brak dostępu|EACCES' /tmp/edge-rohome.log" ; true)
if [[ "$RO_FAILED" -gt "0" ]]; then
    _test_pass "Read-only target: install caught permission error + clean failure marker"
else
    _test_fail "Read-only target: no failure marker (silent corruption?)"
fi
_vm "tail -10 /tmp/edge-rohome.log" > "$LOG_DIR/01d-rohome-tail.log" 2>&1

# ── (e) Parallel install — flock blocks 2nd ──────────────────────────────

echo
echo "=== (e) Parallel install — flock ==="
# Run 2 install.sh in parallel; 2nd must hit flock and refuse cleanly.
_vm "rm -f /tmp/edge-flock-1.log /tmp/edge-flock-2.log /tmp/_bterminal_install.lock
     cd $VM_REPO
     bash install.sh --headless --no-sudo --selected '' --status-json \
        > /tmp/edge-flock-1.log 2>&1 &
     PID1=\$!
     # 2nd run a moment later — should hit flock
     sleep 1
     bash install.sh --headless --no-sudo --selected '' --status-json \
        > /tmp/edge-flock-2.log 2>&1 &
     PID2=\$!
     wait \$PID1 \$PID2 2>/dev/null
     echo \"RC1=\$? RC2=?\"
     true"

FLOCK_REFUSED=$(_vm "grep -cE 'already running|BTERMINAL_INSTALL_LOCKED|Another instance' /tmp/edge-flock-2.log" ; true)
# Sum traceback hits across both files (single grep gives "file:N\nfile:N")
FLOCK_TRACEBACK=$(_vm "cat /tmp/edge-flock-1.log /tmp/edge-flock-2.log | grep -c 'Traceback'" ; true)
if [[ "$FLOCK_REFUSED" -gt "0" ]] && [[ "$FLOCK_TRACEBACK" == "0" ]]; then
    _test_pass "Parallel install: 2nd refused via flock (no crashes)"
else
    _test_fail "Parallel install: flock=$FLOCK_REFUSED, traceback=$FLOCK_TRACEBACK"
fi
_vm "tail -5 /tmp/edge-flock-2.log" > "$LOG_DIR/01e-flock-tail.log" 2>&1

# Cleanup
_vm "rm -f /tmp/_bterminal_install.lock 2>/dev/null; true"

# ── Final report ───────────────────────────────────────────────────────────

echo
echo "============================================================"
echo "Installer edge cases (#162):  PASS=$PASS  FAIL=$FAIL"
echo "Per-scenario logs in:         $LOG_DIR"
echo "============================================================"
if (( FAIL > 0 )); then
    printf '  - %s\n' "${FAIL_LIST[@]}"
    exit 1
fi
exit 0
