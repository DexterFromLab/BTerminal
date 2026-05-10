#!/usr/bin/env bash
# tools/smoke_3rd_provider.sh — manual VM smoke for the Aider + Ollama
# 3rd-provider stack (audit § 8 / task #12 / #84).
#
# Validates that a freshly-installed BTerminal can:
#   1. Run install.sh in headless mode and pull Ollama + Qwen-0.5B
#   2. Spawn an Aider AI tab via debug-REST
#   3. Stream JSON status lines (#4 / #76)
#   4. Hit ollama's /api/tags so the local model is reachable
#   5. Honor image-paste vision hint (#69) for Aider sessions
#
# Each step prints PASS / FAIL and accumulates to a final summary.
# Logs land in /tmp/bterminal-smoke-<phase>.log on the VM and are
# rsynced back to ./smoke-logs/ at the end.
#
# Usage:
#   ./tools/smoke_3rd_provider.sh                    — full run on vm-test
#   ./tools/smoke_3rd_provider.sh --skip-llama       — skip ollama install
#   ./tools/smoke_3rd_provider.sh --no-wipe          — keep existing VM state
#
# Pre-reqs:
#   - SSH alias `vm-test` (~/.ssh/config) reaching a Linux VM
#   - VM has python3, npm, sudo, xvfb, gir1.2-gtk-3.0 installed
#   - Run from the BTerminal repo root on the host

set -uo pipefail   # NOT errexit — we want to keep going through failures

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="${VM_HOST:-vm-test}"
VM_PATH="${VM_PATH:-/home/michal/BTerminal}"
LOG_DIR="$REPO_ROOT/smoke-logs"
SKIP_LLAMA=false
NO_WIPE=false

while [[ $# -gt 0 ]]; do
    case "$1" in
        --skip-llama) SKIP_LLAMA=true; shift ;;
        --no-wipe)    NO_WIPE=true; shift ;;
        --help|-h)
            sed -n '4,29p' "$0"
            exit 0
            ;;
        *)
            echo "Unknown flag: $1 (use --help)" >&2
            exit 2
            ;;
    esac
done

mkdir -p "$LOG_DIR"

PASS_COUNT=0
FAIL_COUNT=0
declare -a FAIL_PHASES=()

step_pass() {
    PASS_COUNT=$((PASS_COUNT + 1))
    printf "  \033[32m✓ PASS\033[0m  %s\n" "$1"
}

step_fail() {
    FAIL_COUNT=$((FAIL_COUNT + 1))
    FAIL_PHASES+=("$1")
    printf "  \033[31m✗ FAIL\033[0m  %s\n" "$1"
    if [[ -n "${2:-}" ]]; then
        printf "          %s\n" "$2"
    fi
}

vm_run() {
    # Wrapper around ssh — captures stderr to local log, stdout returned.
    local phase="$1"; shift
    ssh -o ConnectTimeout=10 "$VM_HOST" "$@" \
        2> "$LOG_DIR/$phase.stderr.log"
}

echo "=== BTerminal 3rd-provider smoke ($(date -u +%FT%TZ)) ==="
echo "VM: $VM_HOST  Path: $VM_PATH  Logs: $LOG_DIR"
echo ""

# ─── Phase 0: prerequisites + connectivity ──────────────────────────────────

echo "[0/7] Preflight checks..."
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$VM_HOST" \
        "echo OK" > /dev/null 2>&1; then
    step_fail "ssh-to-vm" "Cannot reach $VM_HOST. Configure ~/.ssh/config alias."
    echo ""
    echo "=== HARD STOP — VM unreachable ==="
    exit 1
fi
step_pass "ssh-to-vm reachable"

if ! vm_run "preflight" "command -v xvfb-run && command -v python3 && command -v npm" \
        > /dev/null; then
    step_fail "vm-prerequisites" "VM missing one of: xvfb-run, python3, npm."
fi
step_pass "VM has xvfb-run / python3 / npm"

# ─── Phase 1: clean state on VM ─────────────────────────────────────────────

echo ""
echo "[1/7] Cleaning prior install state..."
if [[ "$NO_WIPE" == true ]]; then
    step_pass "wipe (skipped — --no-wipe)"
else
    vm_run "wipe" "
        rm -rf ~/.local/share/bterminal/bterminal \
                ~/.local/bin/bterminal-launcher \
                ~/.local/bin/{bterminal,ctx,tasks,consult,memory_wizard,claude_log} \
                ~/.config/bterminal \
                ~/.cache/bterminal 2>/dev/null
        # Don't wipe ~/.ollama (model pulls expensive), unless --skip-llama
        true
    "
    step_pass "wipe ~/.local/share/bterminal + ~/.config/bterminal"
fi

# ─── Phase 2: rsync working tree to VM ──────────────────────────────────────

echo ""
echo "[2/7] Syncing working tree..."
"$REPO_ROOT/tools/vm_sync.sh" > "$LOG_DIR/vm_sync.log" 2>&1
if [[ $? -eq 0 ]]; then
    step_pass "rsync working tree → $VM_HOST:$VM_PATH"
else
    step_fail "vm_sync" "see $LOG_DIR/vm_sync.log"
fi

# ─── Phase 3: install.sh --headless ─────────────────────────────────────────

echo ""
echo "[3/7] Running install.sh --headless..."
SELECTED="meld"
if [[ "$SKIP_LLAMA" != true ]]; then
    SELECTED="$SELECTED,llama"
fi
INSTALL_OUT="$LOG_DIR/install.log"
if vm_run "install" "
    cd $VM_PATH
    bash install.sh --headless --selected $SELECTED --status-json --no-sudo
" > "$INSTALL_OUT" 2>&1; then
    step_pass "install.sh --headless --selected $SELECTED --no-sudo"
else
    rc=$?
    step_fail "install.sh exit=$rc" "see $INSTALL_OUT"
fi

# Parse the JSON status stream for the terminal 'done' event
if grep -q '"phase": "done"' "$INSTALL_OUT" \
        && grep -q '"progress": 100' "$INSTALL_OUT"; then
    step_pass "JSON status stream emitted phase=done progress=100"
else
    step_fail "install JSON status incomplete" \
              "missing terminal {phase: done, progress: 100}"
fi

# ─── Phase 4: post-install file system layout ───────────────────────────────

echo ""
echo "[4/7] Verifying install layout..."
if vm_run "layout" "
    test -f ~/.local/share/bterminal/bterminal/__init__.py &&
    test -L ~/.local/bin/bterminal &&
    test -f ~/.local/bin/ctx &&
    test -f ~/.local/share/bterminal/defaults/icons/aider.svg
" > /dev/null 2>&1; then
    step_pass "~/.local/share/bterminal/ + bin symlink + aider icon present"
else
    step_fail "post-install layout" "see VM:~/install.log or run vm-test ls"
fi

# ─── Phase 5: ollama daemon + qwen-0.5b ─────────────────────────────────────

echo ""
echo "[5/7] Verifying Ollama (only if --selected llama)..."
if [[ "$SKIP_LLAMA" == true ]]; then
    step_pass "Ollama (skipped — --skip-llama)"
else
    if vm_run "ollama-version" "command -v ollama && ollama --version" \
            > "$LOG_DIR/ollama-version.log" 2>&1; then
        step_pass "ollama binary installed"
    else
        step_fail "ollama-binary" "see $LOG_DIR/ollama-version.log"
    fi

    # Start daemon if not running, in background, capture pid
    vm_run "ollama-start" "
        if ! curl -fsS http://localhost:11434/api/tags > /dev/null 2>&1; then
            nohup ollama serve > /tmp/ollama-serve.log 2>&1 &
            sleep 3
        fi
        curl -fsS http://localhost:11434/api/tags > /dev/null 2>&1
    "
    if [[ $? -eq 0 ]]; then
        step_pass "ollama daemon responding on :11434"
    else
        step_fail "ollama-daemon" "see VM:/tmp/ollama-serve.log"
    fi

    # Pull qwen-0.5b if missing
    if vm_run "ollama-pull" "
        if ! ollama list | grep -q 'qwen2.5-coder:0.5b'; then
            ollama pull qwen2.5-coder:0.5b
        fi
        ollama list | grep -q 'qwen2.5-coder:0.5b'
    " > "$LOG_DIR/ollama-pull.log" 2>&1; then
        step_pass "qwen2.5-coder:0.5b model pulled"
    else
        step_fail "ollama-pull" "see $LOG_DIR/ollama-pull.log"
    fi
fi

# ─── Phase 6: BT spawn + Aider session via debug-REST ───────────────────────

echo ""
echo "[6/7] BTerminal + Aider session smoke..."
PORT=$(shuf -i 17000-17999 -n 1)
BT_HOME="/tmp/bt-smoke-$$"

vm_run "bt-prep" "
    mkdir -p $BT_HOME/.config/bterminal /tmp/aider-test-proj
    # Pre-accept license (avoid GTK modal blocking debug-REST)
    cat > $BT_HOME/.config/bterminal/options.json <<EOF
{\"license_accepted_hash\": \"x\", \"license_accepted_at\": \"2026-05-07T00:00:00\"}
EOF
    cat > $BT_HOME/.config/bterminal/ai_sessions.json <<EOF
[{
    \"id\": \"aider-smoke\",
    \"name\": \"AiderSmoke\",
    \"provider\": \"aider\",
    \"project_dir\": \"/tmp/aider-test-proj\",
    \"color\": \"#fab387\"
}]
EOF
"

# Note: license seed via simple JSON above won't pass hash check;
# use the canonical helper from tests/_subprocess_helpers.py
vm_run "bt-license" "
    cd $VM_PATH
    HOME=$BT_HOME PYTHONPATH=$VM_PATH python3 -c '
from tests._subprocess_helpers import seed_license
import os
seed_license(os.environ[\"HOME\"])
'
"

# Spawn BT subprocess; redirect stdout/stderr to logs
ssh "$VM_HOST" "
    cd $VM_PATH
    HOME=$BT_HOME BTERMINAL_DEBUG_REST_PORT=$PORT \
        nohup xvfb-run -a python3 -m bterminal --debug-rest \
        > /tmp/bt-smoke-stdout.log 2> /tmp/bt-smoke-stderr.log &
    echo \$! > /tmp/bt-smoke.pid
    sleep 8
"

# Wait for /api/health
HEALTH_OK=$(ssh "$VM_HOST" "
    for i in 1 2 3 4 5 6 7 8; do
        code=\$(curl -s -o /dev/null -w '%{http_code}' http://localhost:$PORT/api/health 2>/dev/null)
        if [[ \"\$code\" == \"200\" || \"\$code\" == \"401\" ]]; then echo OK; exit 0; fi
        sleep 2
    done
    echo NOPE
")

if [[ "$HEALTH_OK" == "OK" ]]; then
    step_pass "BT subprocess spawned + debug-REST :$PORT responding"
else
    step_fail "bt-spawn" "see VM:/tmp/bt-smoke-stderr.log"
fi

# Open Aider tab via REST
TOKEN=$(ssh "$VM_HOST" "cat $BT_HOME/.config/bterminal/debug_token 2>/dev/null")
if [[ -z "$TOKEN" ]]; then
    step_fail "debug-token" "no $BT_HOME/.config/bterminal/debug_token"
else
    OPEN_RESP=$(ssh "$VM_HOST" "curl -s -X POST \
        -H 'Authorization: Bearer $TOKEN' \
        -H 'Content-Type: application/json' \
        -d '{\"config_name\":\"AiderSmoke\"}' \
        http://localhost:$PORT/api/tabs/ai/aider")
    if echo "$OPEN_RESP" | grep -q '"ok": true' 2>/dev/null; then
        step_pass "POST /api/tabs/ai/aider → 200 (tab opened)"
    else
        step_fail "open-aider-tab" "response: $OPEN_RESP"
    fi

    # Verify tab listed in /api/tabs with provider=aider
    TABS_RESP=$(ssh "$VM_HOST" "curl -s \
        -H 'Authorization: Bearer $TOKEN' \
        http://localhost:$PORT/api/tabs")
    if echo "$TABS_RESP" | grep -q '"provider": "aider"'; then
        step_pass "/api/tabs lists tab with provider=aider"
    else
        step_fail "tabs-list" "response: $TABS_RESP"
    fi
fi

# Cleanup BT subprocess
ssh "$VM_HOST" "
    if [[ -f /tmp/bt-smoke.pid ]]; then
        kill \$(cat /tmp/bt-smoke.pid) 2>/dev/null || true
        rm -f /tmp/bt-smoke.pid
    fi
    rm -rf $BT_HOME
"

# ─── Phase 7: image paste template plumbing ────────────────────────────────

echo ""
echo "[7/7] Image paste template config check (Aider provider)..."
if vm_run "img-template" "
    cd $VM_PATH
    PYTHONPATH=$VM_PATH python3 -c '
from bterminal.providers import load_providers_config
cfg = load_providers_config()
tpl = cfg[\"providers\"][\"aider\"][\"argv\"].get(\"image_paste_template\")
assert tpl and \"{path}\" in tpl, f\"missing or no {{path}} in tpl={tpl!r}\"
print(\"OK\", tpl)
'
" > "$LOG_DIR/img-template.log" 2>&1; then
    step_pass "Aider's argv.image_paste_template wraps {path}"
else
    step_fail "img-template" "see $LOG_DIR/img-template.log"
fi

# ─── Final summary ──────────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo "Total: $PASS_COUNT passed, $FAIL_COUNT failed"
if [[ $FAIL_COUNT -eq 0 ]]; then
    printf "\033[32m=== SMOKE OK — 3rd provider stack reachable on %s ===\033[0m\n" \
        "$VM_HOST"
    exit 0
else
    printf "\033[31m=== SMOKE FAILED ===\033[0m\n"
    echo "Failed phases:"
    for p in "${FAIL_PHASES[@]}"; do
        echo "  • $p"
    done
    echo "Logs in $LOG_DIR/"
    exit 1
fi
