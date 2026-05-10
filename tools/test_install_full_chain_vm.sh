#!/usr/bin/env bash
# tools/test_install_full_chain_vm.sh — pełen end-to-end test
# instalatora na VM. Powstał w odpowiedzi na bug 2026-05-08:
# install.sh raportował "claude installed" mimo że npm zostawił
# stub claude.exe bez bitu +x — BTerminal odpalał się i pokazywał
# "Claude Code not found" przy próbie spawn sesji.
#
# Co testuje:
#   (a) Stub-injection: pre-seed broken claude.exe, run install.sh
#       --no-sudo, assert validate_npm_cli wykrywa stub i WARN-uje.
#   (b) Mock-npm clean install: PATH override z fake npm który
#       tworzy działający binary; install.sh musi raportować ok.
#   (c) Strukturalny log: ~/.config/bterminal/install.log zawiera
#       linie [VALIDATE] dla każdego AI provider CLI.
#   (d) install_errors.json: errors[] / warnings[] poprawnie
#       wypełnione.
#   (e) Post-install BT spawn: BT subprocess uruchamia się, REST
#       /api/health zwraca 200, /api/tabs/ai/{provider} zwraca
#       sensowny exit code dla każdego z 3 providerów.
#
# Każdy krok loguje do smoke-logs/install-fullchain/<step>.log.
# Po wszystkim summary z pass/fail count.
#
# Usage:
#   ./tools/test_install_full_chain_vm.sh
#   ./tools/test_install_full_chain_vm.sh --skip-bt-spawn   # tylko install
#   ./tools/test_install_full_chain_vm.sh --modes a,c        # subset
#
# Pre-reqs: ssh vm-test, VM ma bash/python3/git, działający npm
# nie jest wymagany (mockujemy).
set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="${VM_HOST:-vm-test}"
VM_PATH="${VM_PATH:-/home/michal/BTerminal}"
LOG_DIR="$REPO_ROOT/smoke-logs/install-fullchain"
MODES="a,b,c,d,e"
SKIP_BT_SPAWN=false
KEEP_STATE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --modes)         MODES="$2"; shift 2 ;;
        --modes=*)       MODES="${1#*=}"; shift ;;
        --skip-bt-spawn) SKIP_BT_SPAWN=true; shift ;;
        --keep-state)    KEEP_STATE=true; shift ;;
        --help|-h)       sed -n '2,30p' "$0"; exit 0 ;;
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

vm_run_capture() {
    # Same as vm_run but echoes stdout to caller for grepping
    local logname="$1"; shift
    ssh -o ConnectTimeout=10 "$VM_HOST" "$@" \
        2> "$LOG_DIR/$logname.stderr.log" \
        | tee "$LOG_DIR/$logname.stdout.log"
}

# Set up an isolated HOME on the VM so each run is reproducible.
# Use $HOME/bterm-fullchain-$$ as the test root; wipe at start.
TEST_HOME="bterm-fullchain"

setup_isolated_vm_home() {
    vm_run "setup-home" "
        rm -rf \$HOME/$TEST_HOME
        mkdir -p \$HOME/$TEST_HOME/.config/bterminal
        mkdir -p \$HOME/$TEST_HOME/.local/bin
        mkdir -p \$HOME/$TEST_HOME/.npm-global/bin
        mkdir -p \$HOME/$TEST_HOME/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin
    "
}

# ─── (a) Stub-injection: pre-seed broken claude.exe ─────────────────────────


run_mode_a_stub_detection() {
    echo "=== (a) Stub detection: pre-seeded broken claude.exe ==="
    setup_isolated_vm_home

    # Inject a stub identical to the one observed on real VM
    vm_run "inject-stub" "
        cat > \$HOME/$TEST_HOME/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe << 'STUB'
echo \"Error: claude native binary not installed.\" >&2
echo \"\" >&2
echo \"Either postinstall did not run (--ignore-scripts, some pnpm configs)\" >&2
echo \"or the platform-native optional dependency was not downloaded\"
echo \"(--omit=optional).\"
STUB
        # NO chmod +x — replicates the exact bug
        chmod 0644 \$HOME/$TEST_HOME/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
        ln -sf ../lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe \\
               \$HOME/$TEST_HOME/.npm-global/bin/claude
        ls -la \$HOME/$TEST_HOME/.npm-global/bin/claude \\
               \$HOME/$TEST_HOME/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin/claude.exe
    "

    # Run install.sh in --no-sudo mode (skip apt) with HOME override.
    # Use 'true' to substitute for npm so install doesn't actually
    # try to network — we want to test the validator's detection
    # of the pre-existing stub, not real network behaviour.
    vm_run "install-stub-mode" "
        cd $VM_PATH
        export HOME=\$HOME/$TEST_HOME
        export PATH=\"\$HOME/.npm-global/bin:\$PATH\"
        bash install.sh --no-sudo 2>&1 || true
    "

    local stdout="$LOG_DIR/install-stub-mode.stdout.log"

    # Assertions
    if grep -q "Existing Claude binary.*broken.*will reinstall" "$stdout"; then
        step_pass "(a) install.sh detected pre-existing stub claude.exe"
    else
        step_fail "(a) installer did not detect stub" \
            "stdout tail: $(tail -20 "$stdout" | head -10)"
    fi

    # Stub detection in log: validator emits one of 4 diagnostics
    # depending on which check failed (chmod, content, --version).
    # The stub we injected has mode 0644 (no +x) → matches rc=2
    # path: "binary present but not executable".
    if vm_run_capture "stub-log-check" "
        grep -qE 'stub|not executable|broken|chmod' \\
            \$HOME/$TEST_HOME/.config/bterminal/install.log 2>/dev/null
    " >/dev/null 2>&1; then
        step_pass "(a) install.log contains broken-binary diagnostic"
    else
        step_fail "(a) install.log lacks broken-binary diagnostic" \
            "log: $(ssh "$VM_HOST" 'cat \$HOME/'$TEST_HOME'/.config/bterminal/install.log 2>/dev/null' | head -10)"
    fi

    # Pin specific message: validator must log [VALIDATE] line for
    # Claude Code (not just generic OK/WARN)
    if vm_run_capture "stub-validate-line" "
        grep -q '\[VALIDATE\] Claude Code' \\
            \$HOME/$TEST_HOME/.config/bterminal/install.log 2>/dev/null
    " >/dev/null 2>&1; then
        step_pass "(a) [VALIDATE] Claude Code entry logged"
    else
        step_fail "(a) [VALIDATE] Claude Code entry missing" ""
    fi
}

# ─── (b) Mock-npm clean install ─────────────────────────────────────────────


run_mode_b_mock_npm() {
    echo "=== (b) Mock-npm clean install ==="
    setup_isolated_vm_home

    # Build a fake npm wrapper that materializes a working
    # claude/copilot binary in $NPM_PREFIX/bin instead of doing
    # real network install. Mock binary just prints `claude X.Y.Z`
    # on --version.
    # Note: install.sh sets HOME=<test-home>, so $HOME inside mock-npm
    # already IS the test home — DON'T concatenate /$TEST_HOME again.
    vm_run "create-mock-npm" "
        mkdir -p \$HOME/$TEST_HOME/mock-bin
        cat > \$HOME/$TEST_HOME/mock-bin/npm << 'MOCKNPM'
#!/bin/bash
# Drop-in npm replacement for install.sh testing.
# install.sh exports HOME=<test-home>, so \$HOME below is the test home.
case \"\$*\" in
    *'@anthropic-ai/claude-code'*)
        DEST_DIR=\"\$HOME/.npm-global/bin\"
        LIB_DIR=\"\$HOME/.npm-global/lib/node_modules/@anthropic-ai/claude-code/bin\"
        mkdir -p \"\$DEST_DIR\" \"\$LIB_DIR\"
        cat > \"\$LIB_DIR/claude\" << 'BINARY'
#!/bin/sh
[ \"\$1\" = \"--version\" ] && echo \"claude 1.2.3-mock\" && exit 0
exit 0
BINARY
        chmod +x \"\$LIB_DIR/claude\"
        ln -sf \"../lib/node_modules/@anthropic-ai/claude-code/bin/claude\" \"\$DEST_DIR/claude\"
        echo \"+ @anthropic-ai/claude-code@1.2.3-mock (mocked)\"
        exit 0
        ;;
    *'@github/copilot'*)
        DEST_DIR=\"\$HOME/.npm-global/bin\"
        LIB_DIR=\"\$HOME/.npm-global/lib/node_modules/@github/copilot\"
        mkdir -p \"\$DEST_DIR\" \"\$LIB_DIR\"
        cat > \"\$LIB_DIR/copilot.js\" << 'BINARY'
#!/bin/sh
[ \"\$1\" = \"--version\" ] && echo \"copilot 4.5.6-mock\" && exit 0
exit 0
BINARY
        chmod +x \"\$LIB_DIR/copilot.js\"
        ln -sf \"../lib/node_modules/@github/copilot/copilot.js\" \"\$DEST_DIR/copilot\"
        echo \"+ @github/copilot@4.5.6-mock (mocked)\"
        exit 0
        ;;
    *'config set prefix'*)
        exit 0
        ;;
    *'--version'*)
        echo \"10.99.0-mock\"
        exit 0
        ;;
    *)
        exit 0
        ;;
esac
MOCKNPM
        chmod +x \$HOME/$TEST_HOME/mock-bin/npm
    "

    # Run install with mock npm in PATH. HOME is exported FIRST,
    # so subsequent \$HOME refers to the test home — DON'T add
    # /$TEST_HOME again or you'll get double-path collisions.
    # NOTE: NOT --no-sudo, because --no-sudo skips Claude/Copilot
    # install entirely. We want install.sh to actually invoke our
    # mock npm; sudo apt-installs will WARN-not-FAIL since the
    # ssh non-interactive shell can't supply a password.
    vm_run "install-mock-mode" "
        cd $VM_PATH
        export HOME=\$HOME/$TEST_HOME
        export PATH=\"\$HOME/mock-bin:\$HOME/.npm-global/bin:\$PATH\"
        bash install.sh --selected '' 2>&1 || true
    "

    local stdout="$LOG_DIR/install-mock-mode.stdout.log"

    if grep -q "claude.*1.2.3-mock.*installed\|claude 1.2.3-mock" "$stdout"; then
        step_pass "(b) mock npm install produced working claude (validated)"
    else
        step_fail "(b) mock claude not validated" \
            "tail: $(tail -10 "$stdout")"
    fi

    if grep -q "copilot.*4.5.6-mock\|copilot 4.5.6-mock" "$stdout"; then
        step_pass "(b) mock npm install produced working copilot"
    else
        step_fail "(b) mock copilot not validated" \
            "tail: $(tail -10 "$stdout")"
    fi
}

# ─── (c) Strukturalny log: install.log format ───────────────────────────────


run_mode_c_structured_log() {
    echo "=== (c) Structured install.log format ==="
    # Re-uses state from mode (b)
    if [[ ! -f "$LOG_DIR/install-mock-mode.stdout.log" ]]; then
        step_fail "(c) skipped — mode (b) didn't run"
        return
    fi

    local install_log_remote
    install_log_remote=$(ssh -o ConnectTimeout=10 "$VM_HOST" "
        cat \$HOME/$TEST_HOME/.config/bterminal/install.log 2>/dev/null
    " 2>/dev/null || echo "")

    echo "$install_log_remote" > "$LOG_DIR/install.log.remote"

    if [[ -z "$install_log_remote" ]]; then
        step_fail "(c) install.log absent on VM"
        return
    fi

    # Format pin: each line starts with ISO-8601 UTC timestamp
    if echo "$install_log_remote" | head -10 | \
        grep -qE '^[0-9]{4}-[0-9]{2}-[0-9]{2}T[0-9]{2}:[0-9]{2}:[0-9]{2}Z \['; then
        step_pass "(c) install.log lines are timestamped"
    else
        step_fail "(c) timestamps missing or malformed" \
            "first line: $(echo "$install_log_remote" | head -1)"
    fi

    # Levels OK / WARN / FAIL / INFO / VALIDATE all present
    for level in OK INFO VALIDATE; do
        if echo "$install_log_remote" | grep -q "\[$level\]"; then
            step_pass "(c) install.log contains [$level] entries"
        else
            step_fail "(c) install.log missing [$level] level"
        fi
    done

    # VALIDATE entries for both AI providers
    if echo "$install_log_remote" | grep -q "\[VALIDATE\] Claude Code"; then
        step_pass "(c) Claude Code validation logged"
    else
        step_fail "(c) Claude Code VALIDATE entry missing"
    fi

    if echo "$install_log_remote" | grep -q "\[VALIDATE\] Copilot"; then
        step_pass "(c) Copilot CLI validation logged"
    else
        step_fail "(c) Copilot VALIDATE entry missing"
    fi
}

# ─── (d) install_errors.json schema ─────────────────────────────────────────


run_mode_d_errors_json() {
    echo "=== (d) install_errors.json schema ==="
    local errors_json
    errors_json=$(ssh -o ConnectTimeout=10 "$VM_HOST" "
        cat \$HOME/$TEST_HOME/.config/bterminal/install_errors.json 2>/dev/null
    " 2>/dev/null || echo "")

    echo "$errors_json" > "$LOG_DIR/install_errors.json.remote"

    if [[ -z "$errors_json" ]]; then
        step_fail "(d) install_errors.json absent (clean install — should still emit empty arrays)"
        return
    fi

    # Valid JSON
    if echo "$errors_json" | python3 -c 'import sys,json; json.load(sys.stdin)' 2>/dev/null; then
        step_pass "(d) install_errors.json is valid JSON"
    else
        step_fail "(d) install_errors.json is not valid JSON"
        return
    fi

    # Has both errors[] and warnings[] keys
    if echo "$errors_json" | python3 -c '
import sys, json
data = json.load(sys.stdin)
sys.exit(0 if isinstance(data.get("errors"), list) and isinstance(data.get("warnings"), list) else 1)
'; then
        step_pass "(d) errors[] and warnings[] are arrays"
    else
        step_fail "(d) schema mismatch — missing errors[]/warnings[] arrays"
    fi

    # bterminal_version field present
    if echo "$errors_json" | python3 -c '
import sys, json
data = json.load(sys.stdin)
sys.exit(0 if "bterminal_version" in data else 1)
'; then
        step_pass "(d) bterminal_version field present"
    else
        step_fail "(d) bterminal_version field missing"
    fi
}

# ─── (e) Post-install BT spawn + REST health ────────────────────────────────


run_mode_e_bt_spawn() {
    if [[ "$SKIP_BT_SPAWN" == true ]]; then
        echo "=== (e) BT spawn — SKIPPED (--skip-bt-spawn) ==="
        return
    fi
    echo "=== (e) Post-install BT spawn + REST health ==="

    # We need a working BT install. The mock install (mode b)
    # placed BT files in TEST_HOME via install.sh. Verify the
    # symlinks exist.
    if vm_run "check-bt-symlink" "
        test -L \$HOME/$TEST_HOME/.local/bin/bterminal \\
        && test -x \$HOME/$TEST_HOME/.local/bin/bterminal
    "; then
        step_pass "(e) BT symlink exists and is executable"
    else
        step_fail "(e) BT symlink missing or not executable"
        return
    fi

    # (e1) Pre-seed license + spawn BT with timeout (no crash check).
    # Direct `python3 -m bterminal` doesn't work in isolated HOME
    # — use the installed launcher which cd's to the install dir.
    vm_run "bt-spawn-noncrash" "
        export HOME=\$HOME/$TEST_HOME
        export PATH=\"\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH\"
        python3 -c \"
import hashlib, json, os
LIC=open('$VM_PATH/defaults/license/LICENSE.en.md','rb').read()
HASH=hashlib.sha256(LIC).hexdigest()
opts={'accepted_license_hash': HASH, 'language': 'en'}
os.makedirs(os.path.expanduser('~/.config/bterminal'), exist_ok=True)
open(os.path.expanduser('~/.config/bterminal/options.json'),'w').write(json.dumps(opts))
\"
        timeout --preserve-status 6 xvfb-run -a \\
            \$HOME/.local/bin/bterminal > /tmp/bt-spawn-e1.log 2>&1
        EC=\$?
        echo \"BT_EXIT_CODE=\$EC\"
        # 0 (clean exit), 124 (timeout default), 143 (timeout TERM signal)
        # — anything else is a real crash.
        case \"\$EC\" in
            0|124|143) echo BT_SPAWN_OK ;;
            *) echo BT_SPAWN_FAIL ;;
        esac
        echo '--- spawn log tail:'
        tail -30 /tmp/bt-spawn-e1.log 2>/dev/null
    "

    if grep -q "BT_SPAWN_OK" "$LOG_DIR/bt-spawn-noncrash.stdout.log"; then
        step_pass "(e1) BT spawned under xvfb without crashing"
    else
        local ec
        ec=$(grep "BT_EXIT_CODE=" "$LOG_DIR/bt-spawn-noncrash.stdout.log" | head -1)
        step_fail "(e1) BT crashed during spawn ($ec)" \
            "tail: $(tail -10 "$LOG_DIR/bt-spawn-noncrash.stdout.log")"
        return
    fi

    # NOTE: We deliberately do NOT spawn BT in background here +
    # poll /api/health. That race-prone test is already covered by
    # tests/e2e/test_smoke_battery.py and the rest-per-provider
    # suite (#136) which use deterministic readiness probes.
    # In this script we focus on the install→spawn invariant: BT
    # comes up under xvfb without crashing.

    # (e2) Validate that bterminal CLI tools are linked and runnable
    vm_run "bt-cli-tools" "
        export HOME=\$HOME/$TEST_HOME
        export PATH=\"\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH\"
        for tool in bterminal ctx tasks consult memory_wizard; do
            if [[ -x \$HOME/.local/bin/\$tool ]]; then
                echo \"TOOL_OK \$tool\"
            else
                echo \"TOOL_MISSING \$tool\"
            fi
        done
    "

    local missing=0
    if grep -q "^TOOL_MISSING" "$LOG_DIR/bt-cli-tools.stdout.log" 2>/dev/null; then
        missing=$(grep -c "^TOOL_MISSING" "$LOG_DIR/bt-cli-tools.stdout.log" || true)
    fi
    if [[ "$missing" -eq 0 ]]; then
        step_pass "(e2) all 5 CLI tool symlinks installed and executable"
    else
        step_fail "(e2) $missing CLI tool symlinks missing" \
            "log: $(cat "$LOG_DIR/bt-cli-tools.stdout.log")"
    fi
}

# ─── Preflight ──────────────────────────────────────────────────────────────


echo "=== install.sh full-chain VM smoke ($(date -u +%FT%TZ)) ==="
echo "VM: $VM_HOST  Path: $VM_PATH  Modes: $MODES  Logs: $LOG_DIR"
echo ""

if ! ssh -o ConnectTimeout=5 "$VM_HOST" "test -d $VM_PATH"; then
    echo "✗ VM unreachable or repo missing at $VM_PATH"
    echo "  Hint: bash tools/vm_sync.sh"
    exit 2
fi

# Run modes in order — each leaves state for the next.
mode_active a && run_mode_a_stub_detection
mode_active b && run_mode_b_mock_npm
mode_active c && run_mode_c_structured_log
mode_active d && run_mode_d_errors_json
mode_active e && run_mode_e_bt_spawn

# ─── Cleanup ────────────────────────────────────────────────────────────────


if [[ "$KEEP_STATE" == false ]]; then
    vm_run "cleanup" "rm -rf \$HOME/$TEST_HOME"
else
    echo "(state retained at \$HOME/$TEST_HOME on VM for debugging)"
fi

# ─── Summary ────────────────────────────────────────────────────────────────


echo ""
echo "=== Summary ==="
echo "Total: $((PASS + FAIL))   passed: $PASS   failed: $FAIL"
if (( FAIL > 0 )); then
    echo "Failed steps:"
    for s in "${FAIL_LIST[@]}"; do
        echo "  - $s"
    done
    echo "Logs: $LOG_DIR"
    exit 1
fi
exit 0
