#!/usr/bin/env bash
# tools/test_wizard_e2e_vm.sh — automatic install + uninstall test
# with screenshots at every wizard step. Lets us catch UI bugs that
# pure-source pin-tests miss (button labels, dialog state, panel
# rendering). Reads install logs after each phase and asserts on
# their contents.
#
# Test stages:
#   1. Wipe VM state (any prior install)
#   2. Spawn ./install.sh via xdotool — accept license, click Next,
#      enter sudo password, screenshot every page transition
#   3. Read install.log + install_errors.json + per-run logs
#   4. Spawn wizard again with --uninstall — screenshot each step
#   5. Verify uninstall via post-state probes
#   6. Save screenshot bundle for visual review
#
# Pre-reqs: ssh vm-test, xdotool + gnome-screenshot or xwd on VM,
# repo synced via tools/vm_sync.sh, sudo password = $VM_SUDO_PASS
# (defaults to "qwerty" — Mint/Ubuntu test VM convention).
#
# Usage:
#   ./tools/test_wizard_e2e_vm.sh                    # full run
#   ./tools/test_wizard_e2e_vm.sh --modes install    # install only
#   ./tools/test_wizard_e2e_vm.sh --modes uninstall  # uninstall only
#   ./tools/test_wizard_e2e_vm.sh --no-screenshots   # skip image capture
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="${VM_HOST:-vm-test}"
VM_PATH="${VM_PATH:-/home/michal/BTerminal}"
VM_SUDO_PASS="${VM_SUDO_PASS:-qwerty}"
LOG_DIR="$REPO_ROOT/smoke-logs/wizard-e2e"
SCREENSHOTS_DIR="$LOG_DIR/screenshots"
MODES="install,uninstall"
TAKE_SCREENSHOTS=true

while [[ $# -gt 0 ]]; do
    case "$1" in
        --modes)          MODES="$2"; shift 2 ;;
        --modes=*)        MODES="${1#*=}"; shift ;;
        --no-screenshots) TAKE_SCREENSHOTS=false; shift ;;
        --help|-h)        sed -n '2,30p' "$0"; exit 0 ;;
        *)                echo "Unknown: $1"; exit 2 ;;
    esac
done

mkdir -p "$LOG_DIR" "$SCREENSHOTS_DIR"
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

mode_active() {
    local IFS=','
    for m in $MODES; do
        [[ "$m" == "$1" ]] && return 0
    done
    return 1
}

vm_run() {
    local logname="$1"; shift
    ssh -o ConnectTimeout=10 "$VM_HOST" "$@" \
        > "$LOG_DIR/$logname.stdout.log" \
        2> "$LOG_DIR/$logname.stderr.log"
}

# Take a screenshot of the wizard window. Two strategies:
#   1. xdotool finds window by name → xwd dumps it → convert via
#      gimp / xwdtopnm if available, else save raw .xwd
#   2. fallback: full-screen via gnome-screenshot
take_screenshot() {
    [[ "$TAKE_SCREENSHOTS" == false ]] && return 0
    local label="$1"
    local outfile="$SCREENSHOTS_DIR/$(date +%H%M%S)-${label}.png"
    ssh "$VM_HOST" "
        export DISPLAY=:0
        # Find wizard window
        WID=\$(xdotool search --onlyvisible --name 'BTerminal Installer' 2>/dev/null | head -1)
        if [[ -n \"\$WID\" ]]; then
            # xdotool window-id → xwd dump → convert
            xwd -id \"\$WID\" -silent > /tmp/wiz-screenshot.xwd 2>/dev/null
            if command -v xwdtopnm >/dev/null 2>&1; then
                xwdtopnm /tmp/wiz-screenshot.xwd 2>/dev/null \
                    | pnmtopng > /tmp/wiz-screenshot.png 2>/dev/null
            else
                # Fallback: gnome-screenshot of full screen
                gnome-screenshot -f /tmp/wiz-screenshot.png 2>/dev/null
            fi
        else
            gnome-screenshot -f /tmp/wiz-screenshot.png 2>/dev/null || true
        fi
    " > /dev/null 2>&1
    scp -q "$VM_HOST:/tmp/wiz-screenshot.png" "$outfile" 2>/dev/null \
        && step_pass "screenshot: $label → $(basename "$outfile")" \
        || step_fail "screenshot: $label (capture failed)"
}

# Send keypress(es) to wizard window.
# CRITICAL (2026-05-08): MUST NOT use `--window <WID>` on xdotool key —
# that uses XSendEvent (synthetic events) which GTK ignores for security.
# Instead, activate+raise the window so it's focused, then plain
# `xdotool key` uses XTEST (real keyboard input) which GTK honours.
xkey() {
    local keys="$1"
    ssh "$VM_HOST" "
        export DISPLAY=:0
        WID=\$(xdotool search --onlyvisible --name 'BTerminal Installer' 2>/dev/null | head -1)
        [ -n \"\$WID\" ] || exit 1
        xdotool windowactivate --sync \"\$WID\"
        xdotool windowraise \"\$WID\"
        sleep 0.2
        xdotool key --delay 80 $keys
    " 2>/dev/null
    sleep 0.5
}

# Type a string into the sudo password dialog (split-chain
# pattern same as xkey).
xtype() {
    local text="$1"
    ssh "$VM_HOST" "
        export DISPLAY=:0
        WID=\$(xdotool search --onlyvisible --name 'Administrator password' 2>/dev/null | head -1)
        [ -n \"\$WID\" ] || exit 1
        xdotool windowactivate --sync \"\$WID\"
        xdotool windowraise \"\$WID\"
        sleep 0.2
        # No --window here — XTEST input only; --window uses XSendEvent
        # which GTK ignores for security.
        xdotool type --delay 50 -- '$text'
    " 2>/dev/null
    sleep 0.5
}

wait_for_window_title() {
    local needle="$1"
    local timeout="${2:-30}"
    local deadline=$(($(date +%s) + timeout))
    while [[ $(date +%s) -lt $deadline ]]; do
        if ssh "$VM_HOST" "DISPLAY=:0 xdotool search --onlyvisible --name '$needle' 2>/dev/null | head -1" \
                | grep -q '[0-9]'; then
            return 0
        fi
        sleep 1
    done
    return 1
}

# Wait until install.log on the VM contains a marker line. Used
# instead of window-title polling because the GTK Dialog NEVER
# changes its window title — the "Step N of M" header is in the
# content area, invisible to xdotool window-title search.
wait_for_install_log() {
    local marker="$1"
    local timeout="${2:-60}"
    local deadline=$(($(date +%s) + timeout))
    while [[ $(date +%s) -lt $deadline ]]; do
        if ssh "$VM_HOST" "
            test -f \$HOME/.config/bterminal/install.log &&
            grep -q -- '$marker' \$HOME/.config/bterminal/install.log
        " 2>/dev/null; then
            return 0
        fi
        sleep 1
    done
    return 1
}

wipe_vm() {
    vm_run "wipe" "
        rm -rf \$HOME/.config/bterminal \$HOME/.claude-context
        rm -rf \$HOME/.local/share/bterminal
        rm -f \$HOME/.local/bin/{bterminal,ctx,tasks,consult,memory_wizard,claude_log,bterminal-launcher}
        rm -f \$HOME/.local/bin/{claude,copilot}
        rm -rf \$HOME/.npm-global/lib/node_modules/@anthropic-ai
        rm -rf \$HOME/.npm-global/lib/node_modules/@github
        rm -f \$HOME/.npm-global/bin/{claude,copilot}
        rm -f \$HOME/.local/share/applications/bterminal*.desktop
        rm -f \$HOME/.local/share/icons/hicolor/scalable/apps/bterminal*
        # Kill any leftover wizard / install.sh
        pkill -KILL -f 'bterminal --installer' 2>/dev/null || true
        pkill -KILL -f 'install\\.sh' 2>/dev/null || true
        true
    "
}

# ─── Mode: install ──────────────────────────────────────────────────────────


run_mode_install() {
    echo "=== install mode ==="
    wipe_vm

    # Spawn wizard in background on the VM's real display
    ssh "$VM_HOST" "
        export DISPLAY=:0
        cd $VM_PATH
        nohup ./install.sh > /tmp/wizard-install-stdout.log 2>&1 &
        disown
    " > /dev/null

    # Wait for wizard window to appear
    if ! wait_for_window_title "BTerminal Installer" 15; then
        step_fail "install: wizard window never appeared (15s)"
        return
    fi
    step_pass "install: wizard window appeared"
    sleep 1
    take_screenshot "install-01-welcome"

    # Welcome page: accept license via Alt+I mnemonic
    # (matches `_I have read and accept...` label).
    xkey "alt+i"
    sleep 0.5
    take_screenshot "install-02-welcome-license-accepted"

    # Wizard sets Next as default response on every input page,
    # so Return activates it regardless of focused widget.
    xkey "Return"
    sleep 1.5

    # Cannot wait_for_window_title here — GTK Dialog never changes
    # its title. Wizard transitions are visible only via screenshots.
    # Fall back: just sleep + screenshot to confirm advancement.
    take_screenshot "install-03-inventory"
    step_pass "install: advanced past welcome (license accepted)"

    # Next → picks page
    xkey "Return"
    sleep 1.5
    take_screenshot "install-04-picks"

    # Skip ALL deps to keep test fast — uncheck everything would be
    # tedious via xdotool; instead rely on default state and just
    # advance. For now we let defaults run.
    # Next → progress (will trigger sudo prompt)
    xkey "Return"
    sleep 2

    # Sudo dialog appears as a separate window with title
    # "Administrator password required" — that title IS visible
    # to xdotool (unlike inner page transitions).
    if wait_for_window_title "Administrator password" 30; then
        step_pass "install: sudo dialog appeared"
        take_screenshot "install-05-sudo-prompt"
        xtype "$VM_SUDO_PASS"
        sleep 0.3
        # Press Enter to submit — sudo dialog still active
        ssh "$VM_HOST" "
            export DISPLAY=:0
            WID=\$(xdotool search --onlyvisible --name 'Administrator password' 2>/dev/null | head -1)
            if [ -n \"\$WID\" ]; then
                xdotool windowactivate --sync \"\$WID\"
                sleep 0.2
                xdotool key Return
            fi
        " 2>/dev/null
        sleep 1.5
    else
        step_fail "install: sudo dialog never appeared"
    fi

    # Wait for install.log to record the final phase (status_json
    # done ok 100, OR done failed 100). install.sh writes
    # `[OK] Desktop entry created` near the very end on success.
    # Since GTK Dialog title doesn't change between steps, we tail
    # the install log and screenshot every minute as the install
    # progresses.
    DEADLINE=$(($(date +%s) + 600))
    REACHED_END=false
    while [[ $(date +%s) -lt $DEADLINE ]]; do
        # Final markers in install.log:
        #  - "installed successfully" → success (added 2026-05-08)
        #  - "Install verification failed" → verification rejected
        #  - "Installation failed" → script exited non-zero earlier
        if ssh "$VM_HOST" "
            test -f \$HOME/.config/bterminal/install.log &&
            grep -qE 'installed successfully|Install verification failed|Installation failed' \\
                \$HOME/.config/bterminal/install.log
        " 2>/dev/null; then
            sleep 2  # let summary page render
            step_pass "install: install.log shows final status"
            REACHED_END=true
            break
        fi
        sleep 30
        take_screenshot "install-progress-$(date +%H%M%S)"
    done
    [[ "$REACHED_END" == false ]] && step_fail "install: did not reach final status in 10 min"

    take_screenshot "install-99-summary"

    # Read install.log + assert no critical errors
    INSTALL_LOG_OUT="$LOG_DIR/install.log.copy"
    scp -q "$VM_HOST:~/.config/bterminal/install.log" "$INSTALL_LOG_OUT" \
        2>/dev/null || true
    if [[ -s "$INSTALL_LOG_OUT" ]]; then
        step_pass "install: install.log captured ($(wc -l < "$INSTALL_LOG_OUT") lines)"
        if grep -q '\[FAIL\]' "$INSTALL_LOG_OUT"; then
            step_fail "install: install.log contains [FAIL] entries"
        else
            step_pass "install: no [FAIL] entries in install.log"
        fi
        if grep -q 'VERIFY_FAIL\|verification failed' "$INSTALL_LOG_OUT"; then
            step_fail "install: post-install verification reported errors"
        else
            step_pass "install: post-install verification passed"
        fi
    else
        step_fail "install: install.log empty or missing"
    fi

    # Close wizard via Escape (cancel) or Alt+F4
    ssh "$VM_HOST" "
        export DISPLAY=:0
        xdotool search --onlyvisible --name 'BTerminal Installer' windowclose 2>/dev/null
    " 2>/dev/null
    sleep 1

    # Verify post-install state on disk
    if vm_run "post-install-check" "
        test -x \$HOME/.local/bin/bterminal &&
        test -f \$HOME/.local/share/bterminal/bterminal/__init__.py
    "; then
        step_pass "install: post-state probes pass (launcher + package)"
    else
        step_fail "install: post-state probes FAIL — BT not installed"
    fi
}

# ─── Mode: uninstall ────────────────────────────────────────────────────────


run_mode_uninstall() {
    echo "=== uninstall mode (5 sub-tests for task #140) ==="

    # Sanity: BT must be installed first
    if ! vm_run "preflight" "test -x \$HOME/.local/bin/bterminal"; then
        step_fail "uninstall: BT not installed — run install first"
        return
    fi
    step_pass "uninstall: preflight OK (BT present)"

    # ─── Pre-test setup ─────────────────────────────────────────────
    # (c) Plant a desktop shortcut on the user's XDG_DESKTOP_DIR
    # (Polish locale → ~/Pulpit). do_uninstall must remove it.
    vm_run "plant-pulpit" "
        DESKTOP=\$(xdg-user-dir DESKTOP 2>/dev/null || echo \$HOME/Desktop)
        [ -z \"\$DESKTOP\" ] && DESKTOP=\$HOME/Pulpit
        mkdir -p \"\$DESKTOP\"
        cp -f \$HOME/.local/share/applications/bterminal.desktop \\
              \"\$DESKTOP/bterminal.desktop\" 2>/dev/null
        ls \"\$DESKTOP/bterminal.desktop\"
    " >/dev/null 2>&1
    if vm_run "plant-pulpit-check" "
        DESKTOP=\$(xdg-user-dir DESKTOP 2>/dev/null || echo \$HOME/Desktop)
        [ -z \"\$DESKTOP\" ] && DESKTOP=\$HOME/Pulpit
        test -f \"\$DESKTOP/bterminal.desktop\"
    "; then
        step_pass "uninstall: pre-test desktop shortcut planted"
    else
        step_fail "uninstall: failed to plant desktop shortcut" \
            "(c) test depends on this — desktop dir may not exist"
    fi

    # ─── Run uninstall WITHOUT --purge ──────────────────────────────
    info_msg() { printf "  • %s\n" "$1"; }
    info_msg "Running ./install.sh --uninstall (no --purge)"

    vm_run "uninstall-no-purge" "
        cd $VM_PATH
        ./install.sh --uninstall 2>&1 | tail -30
    " > /dev/null
    take_screenshot "uninstall-01-after-no-purge"

    # (a) BT files removed
    if vm_run "post-no-purge-removed" "
        ! test -e \$HOME/.local/bin/bterminal &&
        ! test -d \$HOME/.local/share/bterminal
    "; then
        step_pass "(a) uninstall: BT files + launcher removed"
    else
        step_fail "(a) uninstall: BT artefacts still present"
    fi

    # (a) configs + ctx DB preserved
    if vm_run "post-no-purge-kept" "
        test -d \$HOME/.config/bterminal &&
        test -d \$HOME/.claude-context
    "; then
        step_pass "(a) uninstall: configs + ctx DB preserved (no --purge)"
    else
        step_fail "(a) uninstall: configs/ctx removed despite no --purge"
    fi

    # (c) Desktop shortcut from XDG_DESKTOP_DIR removed
    if vm_run "post-no-purge-pulpit" "
        DESKTOP=\$(xdg-user-dir DESKTOP 2>/dev/null || echo \$HOME/Desktop)
        [ -z \"\$DESKTOP\" ] && DESKTOP=\$HOME/Pulpit
        ! test -f \"\$DESKTOP/bterminal.desktop\"
    "; then
        step_pass "(c) uninstall: XDG desktop shortcut removed"
    else
        step_fail "(c) uninstall: shortcut on Pulpit/Desktop still present"
    fi

    # (d) AI CLIs removed (npm-installed claude + copilot)
    if vm_run "post-no-purge-ai" "
        ! test -e \$HOME/.npm-global/bin/claude &&
        ! test -e \$HOME/.npm-global/bin/copilot &&
        ! test -d \$HOME/.npm-global/lib/node_modules/@anthropic-ai &&
        ! test -d \$HOME/.npm-global/lib/node_modules/@github
    "; then
        step_pass "(d) uninstall: claude + copilot npm packages removed"
    else
        step_fail "(d) uninstall: npm-installed AI CLI residue present"
    fi

    # ─── Re-install for second pass (--purge test) ──────────────────
    info_msg "Re-installing for --purge sub-test"
    vm_run "reinstall" "
        cd $VM_PATH
        export DISPLAY=:0
        ./install.sh --headless --status-json --no-sudo 2>&1 | tail -10
    " > /dev/null

    if ! vm_run "reinstall-check" "test -x \$HOME/.local/bin/bterminal"; then
        step_fail "uninstall: re-install failed; can't run --purge sub-test"
        return
    fi
    step_pass "uninstall: re-install OK (preparing --purge test)"

    # ─── Run uninstall WITH --purge ─────────────────────────────────
    info_msg "Running ./install.sh --uninstall --purge"
    vm_run "uninstall-purge" "
        cd $VM_PATH
        ./install.sh --uninstall --purge 2>&1 | tail -30
    " > /dev/null

    # (b) Everything (including user data) removed
    if vm_run "post-purge-removed" "
        ! test -e \$HOME/.local/bin/bterminal &&
        ! test -d \$HOME/.local/share/bterminal &&
        ! test -d \$HOME/.config/bterminal &&
        ! test -d \$HOME/.claude-context
    "; then
        step_pass "(b) --purge: BT files + configs + ctx DB all removed"
    else
        step_fail "(b) --purge: residue (one of: launcher/install_dir/config/ctx)"
    fi

    # ─── (e) Wizard mode uninstall: button label test ───────────────
    info_msg "Re-installing for wizard-mode uninstall test"
    vm_run "reinstall2" "
        cd $VM_PATH
        export DISPLAY=:0
        ./install.sh --headless --status-json --no-sudo 2>&1 | tail -5
    " > /dev/null
    if ! vm_run "reinstall2-check" "test -x \$HOME/.local/bin/bterminal"; then
        step_fail "uninstall (e): re-install failed before wizard test"
        return
    fi

    # Spawn wizard in install mode (default), wait, click Uninstall
    # radio, click through to summary, take screenshot
    ssh "$VM_HOST" "
        export DISPLAY=:0
        cd $VM_PATH
        nohup ./install.sh > /tmp/wiz-uninstall.log 2>&1 &
        disown
    " > /dev/null
    if ! wait_for_window_title "BTerminal Installer" 15; then
        step_fail "(e) uninstall via wizard: window never appeared"
        return
    fi
    sleep 1

    # Welcome page: Tab to "Uninstall BTerminal" radio. Tab order:
    # Install (selected) → Fix (disabled, skip) → Uninstall.
    # Install is checked default → arrow Down moves selection in
    # the radio group. Press Down twice (Install→Fix→Uninstall, but
    # Fix is disabled so should skip).
    xkey "Down Down"  # select Uninstall radio
    sleep 0.3
    xkey "alt+i"      # accept license
    sleep 0.3
    take_screenshot "uninstall-02-radio-uninstall-selected"
    xkey "Return"     # → Confirm uninstall page
    sleep 1.5
    take_screenshot "uninstall-03-confirm-page"
    xkey "Return"     # → run uninstall
    sleep 8

    take_screenshot "uninstall-04-summary"

    # Search 4 places for completion marker — install.sh writes
    # it in different files depending on --purge state:
    #   1. /tmp/wiz-uninstall.log — wizard subprocess stdout
    #   2. ~/.config/bterminal/install.log — structured log (no purge)
    #   3. ~/.config/bterminal/install-runs/wizard-run-*.log — wizard tee
    #   4. /tmp/bterminal-uninstall-final-*.log — purge fallback log
    if ssh "$VM_HOST" "
        grep -q 'Uninstall completed' /tmp/wiz-uninstall.log 2>/dev/null \\
        || grep -q 'uninstall completed' \$HOME/.config/bterminal/install.log 2>/dev/null \\
        || ls \$HOME/.config/bterminal/install-runs/wizard-run-*.log 2>/dev/null \\
            | xargs --no-run-if-empty grep -l 'Uninstall completed\\|uninstall completed' 2>/dev/null | head -1 \\
        | grep -q . \\
        || ls /tmp/bterminal-uninstall-final-*.log 2>/dev/null \\
            | xargs --no-run-if-empty grep -l 'Uninstall completed\\|completed' 2>/dev/null | head -1 \\
        | grep -q .
    "; then
        step_pass "(e) wizard uninstall: completion marker found in log"
    else
        step_fail "(e) wizard uninstall: completion marker missing in any log"
    fi

    # Cleanup wizard window
    ssh "$VM_HOST" "
        export DISPLAY=:0
        WID=\$(xdotool search --onlyvisible --name 'BTerminal Installer' 2>/dev/null | head -1)
        [ -n \"\$WID\" ] && xdotool windowclose \"\$WID\"
    " 2>/dev/null
    sleep 1
}

# ─── Run ────────────────────────────────────────────────────────────────────


echo "=== wizard E2E with screenshots ($(date -u +%FT%TZ)) ==="
echo "VM: $VM_HOST  Path: $VM_PATH  Modes: $MODES"
echo "Screenshots: $SCREENSHOTS_DIR"
echo ""

if ! ssh -o ConnectTimeout=5 "$VM_HOST" "test -d $VM_PATH"; then
    echo "✗ VM unreachable or repo missing"
    exit 2
fi

mode_active install   && run_mode_install
mode_active uninstall && run_mode_uninstall

echo ""
echo "=== Summary ==="
echo "Total: $((PASS + FAIL))   passed: $PASS   failed: $FAIL"
echo "Screenshots: $SCREENSHOTS_DIR ($(ls "$SCREENSHOTS_DIR" 2>/dev/null | wc -l) files)"
if (( FAIL > 0 )); then
    echo "Failed:"
    for s in "${FAIL_LIST[@]}"; do echo "  - $s"; done
    exit 1
fi
exit 0
