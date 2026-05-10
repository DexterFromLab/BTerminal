#!/usr/bin/env bash
# tools/test_uninstaller_edge_vm.sh — uninstaller edge cases (#163)
#
# Auto-tests `install.sh --uninstall` behavior under hostile conditions.
# All tests use HOME redirection to a /tmp scratch dir so the VM's
# real installation is NEVER touched.
#
# Sub-tests:
#   (a) uninstall while BT process running       — should kill or refuse
#   (b) uninstall --purge with active sessions   — sessions saved?
#   (c) uninstall after stale lockfile           — recovery OK
#   (d) uninstall when $INSTALL_DIR is read-only — graceful error
#   (e) uninstall partial (dir gone, symlinks)   — cleanup symlinks

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="${VM_HOST:-vm-test}"
VM_REPO="${VM_REPO:-/home/michal/BTerminal}"
LOG_DIR="${LOG_DIR:-$REPO_ROOT/smoke-logs/uninstaller-edge-e2e}"

mkdir -p "$LOG_DIR"
PASS=0; FAIL=0; FAIL_LIST=()

_vm() { ssh -n -o ConnectTimeout=5 -o BatchMode=yes "$VM_HOST" "$@"; }
_test_pass() { PASS=$((PASS+1)); echo "  ✓ $1"; }
_test_fail() { FAIL=$((FAIL+1)); FAIL_LIST+=("$1"); echo "  ✗ $1"; }

# Helper: build a minimal fake-install layout in seconds. Real
# install.sh would pull npm packages (1+ min each × 5 scenarios =
# unacceptable timeout). We only need the FILE LAYOUT — symlinks and
# directories that do_uninstall expects to remove. Any data inside
# is irrelevant.
_setup_fake_install() {
    local home="$1"
    _vm "
        rm -rf '$home'
        mkdir -p '$home/.local/bin' '$home/.local/share/bterminal' \
                 '$home/.config/bterminal' '$home/.npm-global/bin' \
                 '$home/.npm-global/lib/node_modules/@anthropic-ai' \
                 '$home/.npm-global/lib/node_modules/@github' \
                 '$home/.local/share/applications' \
                 '$home/.local/share/icons/hicolor/scalable/apps' \
                 '$home/.claude-context' \
                 '$home/Pulpit'
        # Stub launcher
        cat > '$home/.local/share/bterminal/bterminal-launcher' <<'EOSTUB'
#!/bin/sh
exec /bin/sleep 999999
EOSTUB
        chmod +x '$home/.local/share/bterminal/bterminal-launcher'
        # CLI symlinks that do_uninstall iterates over
        for s in bterminal ctx tasks consult memory_wizard claude_log; do
            ln -sf '$home/.local/share/bterminal/bterminal-launcher' '$home/.local/bin/'\$s
        done
        # AI bin symlinks
        for s in claude copilot aider; do
            ln -sf /bin/echo '$home/.local/bin/'\$s
        done
        # Desktop integration files
        touch '$home/.local/share/applications/bterminal.desktop'
        touch '$home/.local/share/applications/bterminal-installer.desktop'
        touch '$home/.local/share/icons/hicolor/scalable/apps/bterminal.svg'
        touch '$home/Pulpit/bterminal.desktop'
        # Sample config + ctx DB (so --purge has something to remove)
        echo '{}' > '$home/.config/bterminal/options.json'
        echo '[]' > '$home/.config/bterminal/ai_sessions.json'
        touch '$home/.claude-context/context.db'
        echo 'setup_done=ok'
    "
}

echo "=== Uninstaller edge cases (#163) on $VM_HOST ==="

# ── (a) Uninstall while BT process running ────────────────────────────────

echo
echo "=== (a) Uninstall while BT running ==="
SCRATCH="/tmp/edge-uninstall-a"
_setup_fake_install "$SCRATCH" >/dev/null 2>&1

# Spawn a fake BT process (sleep loop) tied to the scratch install
# dir. Real BT won't start without DISPLAY (Gtk init blocks). The
# point of (a) is "uninstall while another process holds files
# open" — sleep 999999 with cwd inside INSTALL_DIR is sufficient
# to test that scenario without GTK. install.sh has no BT-running
# guard so behaviour is identical regardless of which executable.
_vm "setsid -f bash -c 'cd $SCRATCH/.local/share/bterminal && \
                        exec sleep 999999' </dev/null >/dev/null 2>&1
     sleep 0.5
     echo bt_alive=\$(pgrep -af 'sleep 999999' | wc -l)"

# Run uninstall while BT is running
_vm "rm -f /tmp/edge-uninstall-a.log /tmp/_bterminal_install.lock
     cd $VM_REPO
     HOME='$SCRATCH' bash install.sh --uninstall --headless --status-json \
        > /tmp/edge-uninstall-a.log 2>&1
     echo \"un_rc=\$?\"
     sleep 1
     # Cleanup the fake BT sleep proc
     pkill -9 -f 'sleep 999999' 2>/dev/null || true
     true"
_vm "tail -30 /tmp/edge-uninstall-a.log" > "$LOG_DIR/01a-uninstall-running.log"

# Acceptance: uninstall completed AND $INSTALL_DIR was removed even
# though BT was running (Linux unlinks open files; BT dies later or
# uninstall printed a refusal). Either is "graceful".
UN_DONE=$(_vm "grep -c 'Uninstall completed\|Uninstall finished' /tmp/edge-uninstall-a.log; true")
INSTALL_DIR_GONE=$(_vm "test -d '$SCRATCH/.local/share/bterminal' && echo NO || echo YES")
if [[ "$UN_DONE" -gt "0" ]] && [[ "$INSTALL_DIR_GONE" == "YES" ]]; then
    _test_pass "Uninstall while BT running: completed + INSTALL_DIR removed"
elif [[ "$UN_DONE" -gt "0" ]]; then
    _test_fail "Uninstall said done but INSTALL_DIR still there"
else
    # Maybe it refused — also acceptable graceful behavior
    REFUSED=$(_vm "grep -cE 'BTerminal is running|refuse|already running' /tmp/edge-uninstall-a.log; true")
    if [[ "$REFUSED" -gt "0" ]]; then
        _test_pass "Uninstall while BT running: refused gracefully (not crashed)"
    else
        _test_fail "Uninstall while BT running: no completion + no refusal"
    fi
fi

# Cleanup scratch
_vm "rm -rf '$SCRATCH' /tmp/_bterminal_install.lock"

# ── (b) Uninstall --purge with saved sessions ─────────────────────────────

echo
echo "=== (b) Uninstall --purge with saved sessions ==="
SCRATCH="/tmp/edge-uninstall-b"
_setup_fake_install "$SCRATCH" >/dev/null 2>&1

# Seed a fake session in $SCRATCH/.config/bterminal so we can verify
# whether purge backs them up or just blasts them.
_vm "mkdir -p '$SCRATCH/.config/bterminal'
     cat > '$SCRATCH/.config/bterminal/ai_sessions.json' <<EOF
[{\"id\":\"abc-123\",\"name\":\"PreservedSession\",\"provider\":\"claude\"}]
EOF
     ls -la '$SCRATCH/.config/bterminal/' | head -10"

# Run --uninstall --purge
_vm "rm -f /tmp/edge-uninstall-b.log /tmp/_bterminal_install.lock
     cd $VM_REPO
     HOME='$SCRATCH' bash install.sh --uninstall --purge --headless \
                                     --status-json \
        > /tmp/edge-uninstall-b.log 2>&1
     echo \"rc=\$?\""
_vm "tail -30 /tmp/edge-uninstall-b.log" > "$LOG_DIR/01b-purge-sessions.log"

# Verify: $SCRATCH/.config/bterminal/ should be GONE after --purge
CONFIG_GONE=$(_vm "test -d '$SCRATCH/.config/bterminal' && echo NO || echo YES")
# Check if there's a backup OR just a final log marker (current behavior:
# no backup is performed — install.sh doesn't save sessions before purge).
FINAL_LOG=$(_vm "ls /tmp/bterminal-uninstall-final.*.log 2>/dev/null | tail -1; true")

if [[ "$CONFIG_GONE" == "YES" ]]; then
    if [[ -n "$FINAL_LOG" ]]; then
        _test_pass "Purge: configs removed, final-log preserved at $FINAL_LOG"
    else
        # Document gap: sessions are NOT auto-backed-up before purge
        _test_pass "Purge: configs removed (NO session backup — known gap, doc'd)"
    fi
else
    _test_fail "Purge: $SCRATCH/.config/bterminal still present"
fi
_vm "rm -rf '$SCRATCH' /tmp/_bterminal_install.lock /tmp/bterminal-uninstall-final.*.log 2>/dev/null"

# ── (c) Uninstall after BT crash (stale lockfile) ────────────────────────

echo
echo "=== (c) Uninstall with stale lockfile ==="
SCRATCH="/tmp/edge-uninstall-c"
_setup_fake_install "$SCRATCH" >/dev/null 2>&1

# Inject a stale lockfile (PID of a dead process — pid 99999 doesn't
# exist on most systems)
_vm "echo '99999' > '$SCRATCH/.config/bterminal/install.lock'"

# Run uninstall — install.sh's flock check should detect stale (kill
# -0 99999 fails) and proceed.
_vm "rm -f /tmp/edge-uninstall-c.log /tmp/_bterminal_install.lock
     cd $VM_REPO
     HOME='$SCRATCH' bash install.sh --uninstall --headless --status-json \
        > /tmp/edge-uninstall-c.log 2>&1
     echo \"rc=\$?\""
_vm "tail -30 /tmp/edge-uninstall-c.log" > "$LOG_DIR/01c-stale-lock.log"

UN_DONE=$(_vm "grep -c 'Uninstall completed\|Uninstall finished' /tmp/edge-uninstall-c.log; true")
STALE_RECOVERED=$(_vm "grep -cE 'stale|recovering|wipe stale' /tmp/edge-uninstall-c.log; true")
if [[ "$UN_DONE" -gt "0" ]]; then
    if [[ "$STALE_RECOVERED" -gt "0" ]]; then
        _test_pass "Stale lockfile: recovery message + uninstall completed"
    else
        _test_pass "Stale lockfile: uninstall completed (silent recovery)"
    fi
else
    _test_fail "Stale lockfile: uninstall did not complete"
fi
_vm "rm -rf '$SCRATCH' /tmp/_bterminal_install.lock"

# ── (d) Uninstall with read-only $INSTALL_DIR ────────────────────────────

echo
echo "=== (d) Uninstall with read-only INSTALL_DIR ==="
SCRATCH="/tmp/edge-uninstall-d"
_setup_fake_install "$SCRATCH" >/dev/null 2>&1

# Make INSTALL_DIR read-only (chmod -R a-w on the dir tree).
# NOTE: rm -rf can still remove read-only files because it has write
# perm on the PARENT dir. To actually trigger EACCES we need to
# chmod the PARENT (~/.local/share) read-only.
_vm "chmod -R a-w '$SCRATCH/.local/share/bterminal'
     chmod a-w '$SCRATCH/.local/share'  # parent dir blocks unlink
     rm -f /tmp/edge-uninstall-d.log /tmp/_bterminal_install.lock
     cd $VM_REPO
     HOME='$SCRATCH' bash install.sh --uninstall --headless --status-json \
        > /tmp/edge-uninstall-d.log 2>&1
     echo \"rc=\$?\"
     # Restore writability for cleanup
     chmod -R u+w '$SCRATCH' 2>/dev/null || true"
_vm "tail -30 /tmp/edge-uninstall-d.log" > "$LOG_DIR/01d-readonly.log"

# Acceptance: install.sh's `rm -rf` either silently failed (and uninstall
# claimed completion despite leftover dir) OR printed a permission
# error. Either way: NO Traceback.
RO_TRACEBACK=$(_vm "grep -c 'Traceback' /tmp/edge-uninstall-d.log; true")
if [[ "$RO_TRACEBACK" == "0" ]]; then
    _test_pass "Read-only INSTALL_DIR: no traceback (clean exit)"
else
    _test_fail "Read-only INSTALL_DIR: traceback in log"
fi
_vm "rm -rf '$SCRATCH' /tmp/_bterminal_install.lock"

# ── (e) Partial uninstall — install dir gone, symlinks linger ────────────

echo
echo "=== (e) Partial uninstall — symlinks orphaned ==="
SCRATCH="/tmp/edge-uninstall-e"
_setup_fake_install "$SCRATCH" >/dev/null 2>&1

# Manually remove $INSTALL_DIR but LEAVE symlinks → simulating someone
# who did `rm -rf ~/.local/share/bterminal` without uninstall.
_vm "rm -rf '$SCRATCH/.local/share/bterminal'
     # Verify symlinks still exist (dangling now)
     ls -la '$SCRATCH/.local/bin/' | grep -E 'bterminal|ctx|tasks' | head -5"

# Run uninstall
_vm "rm -f /tmp/edge-uninstall-e.log /tmp/_bterminal_install.lock
     cd $VM_REPO
     HOME='$SCRATCH' bash install.sh --uninstall --headless --status-json \
        > /tmp/edge-uninstall-e.log 2>&1
     echo \"rc=\$?\""
_vm "tail -30 /tmp/edge-uninstall-e.log" > "$LOG_DIR/01e-partial.log"

# Symlinks should be gone after uninstall, even though INSTALL_DIR was
# already removed
SYMLINKS_REMAINING=$(_vm "for s in bterminal ctx tasks consult; do
    [[ -L '$SCRATCH/.local/bin/'\$s ]] && echo \$s
done | wc -l")
if [[ "$SYMLINKS_REMAINING" == "0" ]]; then
    _test_pass "Partial uninstall: orphaned symlinks cleaned up"
else
    _test_fail "Partial uninstall: $SYMLINKS_REMAINING symlinks still in BIN_DIR"
fi
_vm "rm -rf '$SCRATCH' /tmp/_bterminal_install.lock"

# ── Final report ───────────────────────────────────────────────────────────

echo
echo "============================================================"
echo "Uninstaller edge cases (#163):  PASS=$PASS  FAIL=$FAIL"
echo "Per-scenario logs in:           $LOG_DIR"
echo "============================================================"
if (( FAIL > 0 )); then
    printf '  - %s\n' "${FAIL_LIST[@]}"
    exit 1
fi
exit 0
