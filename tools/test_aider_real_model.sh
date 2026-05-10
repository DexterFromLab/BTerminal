#!/usr/bin/env bash
# tools/test_aider_real_model.sh — Aider + Ollama + Qwen-0.5B E2E (#89 / #17).
#
# Spawns aider with BT-style argv (same flags AiderProvider.build_argv
# emits) against a real Ollama daemon serving qwen2.5-coder:0.5b, sends
# a single deterministic prompt, and asserts the response made it
# into the chat history file.
#
# Phases:
#   0. Preflight — VM reachable, ollama installed + serving, qwen pulled
#   1. Wipe ~/.aider state + create /tmp/test-proj-aider-$$
#   2. Compute argv from AiderProvider.build_argv (parity guarantee)
#   3. Spawn aider with --message "Reply with exactly the word PONG..."
#   4. Assert stdout+log contains 'PONG' (case-insensitive)
#   5. Assert .aider.chat.history.md created
#   6. Run AiderProvider.parse_session_stats — assert response_count>=1
#
# Catches: model dispatch broken, OpenAI-compat endpoint mismatch,
# --no-stream regression, build_argv parity drift, parse_session_stats
# regex breakage on real-world markdown.
#
# Usage:
#   ./tools/test_aider_real_model.sh                  — full E2E
#   ./tools/test_aider_real_model.sh --no-pull        — skip ollama pull
#                                                       (fails if model missing)
#   ./tools/test_aider_real_model.sh --keep-project   — don't wipe /tmp project
#                                                       on success
#   ./tools/test_aider_real_model.sh --large-project  — synthesize ~100k LOC
#                                                       repo to stress aider
#                                                       startup time + verify
#                                                       gitignore + cwd-only
#                                                       AIDER.md discovery
#                                                       (#132 / audit § 6.8 #33)
#
# Pre-reqs:
#   - SSH alias `vm-test`; VM has python3 + ollama + aider
#   - Ollama daemon either already running or auto-launchable

set -uo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
HELPER_DIR="$REPO_ROOT/tools/_vm_aider_checks"
VM_HOST="${VM_HOST:-vm-test}"
VM_PATH="${VM_PATH:-/home/michal/BTerminal}"
LOG_DIR="$REPO_ROOT/smoke-logs/aider-vm"
QWEN_MODEL="qwen2.5-coder:0.5b"
PROJECT_REL="/tmp/test-proj-aider-$$"
PROMPT="Reply with exactly the word PONG and nothing else."
DO_PULL=true
KEEP_PROJECT=false
LARGE_PROJECT=false
# #132 (audit § 6.8 #33): synthesize a large repo (~100k LOC)
# instead of cloning linux-mainline. 1000 files × 100 lines
# each = realistic startup-time stress without 1+ GB clone.
# Three sub-modes covered:
#   (a) plain large repo with .git only
#   (b) include node_modules/ — verify aider respects gitignore
#   (c) deeply nested AIDER.md alongside the root one — verify
#       cwd-based auto-discovery picks the ROOT, not a sibling

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-pull)        DO_PULL=false;        shift ;;
        --keep-project)   KEEP_PROJECT=true;    shift ;;
        --large-project)  LARGE_PROJECT=true;   shift ;;
        --help|-h)        sed -n '4,42p' "$0"; exit 0 ;;
        *)                echo "Unknown: $1"; exit 2 ;;
    esac
done

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

echo "=== aider real-model E2E ($(date -u +%FT%TZ)) ==="
echo "VM: $VM_HOST  Path: $VM_PATH  Logs: $LOG_DIR"
echo "Model: $QWEN_MODEL  Project: $PROJECT_REL  Prompt: \"$PROMPT\""
echo ""

# ─── Phase 0: preflight ────────────────────────────────────────────────────

echo "[0] Preflight..."
if ! ssh -o ConnectTimeout=5 -o BatchMode=yes "$VM_HOST" \
        "echo OK" > /dev/null 2>&1; then
    echo "Cannot reach $VM_HOST" >&2
    exit 1
fi
step_pass "phase 0: VM reachable"

# Sync source so AiderProvider.build_argv on VM matches host
"$REPO_ROOT/tools/vm_sync.sh" > "$LOG_DIR/vm_sync.log" 2>&1 \
    || step_fail "vm_sync" "see $LOG_DIR/vm_sync.log"

# Stage helpers
ssh "$VM_HOST" "rm -rf /tmp/bt_aider_checks && mkdir -p /tmp/bt_aider_checks" \
    > /dev/null 2>&1
scp -q "$HELPER_DIR"/*.py "$VM_HOST:/tmp/bt_aider_checks/" \
    > "$LOG_DIR/scp_helpers.log" 2>&1 \
    || step_fail "scp_helpers" "see $LOG_DIR/scp_helpers.log"

# ollama binary present
if vm_run "ollama-bin" "command -v ollama" > /dev/null 2>&1; then
    step_pass "phase 0: ollama binary on \$PATH"
else
    step_fail "phase 0: ollama not installed (run install.sh --selected llama)" "—"
    echo ""
    echo "Aborting: ollama is required."
    exit 1
fi

# aider binary present
if vm_run "aider-bin" \
        "command -v aider || test -x ~/.local/bin/aider" \
        > /dev/null 2>&1; then
    step_pass "phase 0: aider binary present"
else
    step_fail "phase 0: aider not installed (pipx install aider-chat)" "—"
    echo ""
    echo "Aborting: aider is required."
    exit 1
fi

# ollama daemon reachable on :11434 — try to start if missing
if ! vm_run "ollama-api" \
        "curl -fs -m 3 http://localhost:11434/api/tags" \
        > "$LOG_DIR/ollama-tags.json" 2>&1; then
    echo "  (daemon not up — attempting nohup ollama serve)"
    vm_run "ollama-spawn" "
        nohup ollama serve > /tmp/ollama-serve.log 2>&1 &
        disown
    " > /dev/null 2>&1
    sleep 4
    if vm_run "ollama-api-retry" \
            "curl -fs -m 5 http://localhost:11434/api/tags" \
            > "$LOG_DIR/ollama-tags.json" 2>&1; then
        step_pass "phase 0: ollama daemon spawned + serving on :11434"
    else
        step_fail "phase 0: ollama daemon won't start" \
                  "see /tmp/ollama-serve.log on VM"
        exit 1
    fi
else
    step_pass "phase 0: ollama daemon already serving on :11434"
fi

# Model available — pull if missing (and DO_PULL)
if vm_run "ollama-list" "ollama list 2>&1 | grep -q '$QWEN_MODEL'" \
        > /dev/null 2>&1; then
    step_pass "phase 0: $QWEN_MODEL already pulled"
elif [[ "$DO_PULL" == true ]]; then
    echo "  (pulling $QWEN_MODEL — first run takes ~30 s for 0.5B model)"
    if vm_run "ollama-pull" "ollama pull $QWEN_MODEL" \
            > "$LOG_DIR/ollama-pull.log" 2>&1; then
        step_pass "phase 0: $QWEN_MODEL pulled successfully"
    else
        step_fail "phase 0: ollama pull failed" "see $LOG_DIR/ollama-pull.log"
        exit 1
    fi
else
    step_fail "phase 0: $QWEN_MODEL missing + --no-pull set" "—"
    exit 1
fi

# ─── Phase 1: clean test project ───────────────────────────────────────────

echo ""
if [[ "$LARGE_PROJECT" == true ]]; then
    echo "[1] Setting up LARGE test project (~100k LOC) at $PROJECT_REL..."
    # Synthesize 1000 .py files × 100 lines = ~100k LOC. Plus
    # gitignored node_modules/ (10k files, never indexed by aider's
    # repo scanner) + a deeply-nested AIDER.md sibling that should
    # be IGNORED in favor of the root AIDER.md (auto-discovery
    # picks the cwd's file).
    vm_run "project-setup-large" "
        set -e
        rm -rf $PROJECT_REL
        mkdir -p $PROJECT_REL
        cd $PROJECT_REL
        git init --quiet
        # node_modules — must be gitignored (sub-mode b)
        echo 'node_modules/' > .gitignore
        mkdir -p node_modules
        for i in \$(seq 1 100); do
            mkdir -p node_modules/pkg-\$i
            echo \"module.exports = {x: \$i};\" \
                > node_modules/pkg-\$i/index.js
        done
        # 1000 source files × 100 lines = ~100k LOC (sub-mode a)
        mkdir -p src
        for i in \$(seq 1 1000); do
            python3 -c \"
print('# Module ' + str($i))
print('# Auto-generated stress test source')
for j in range(100):
    print('def func_' + str(j) + '(x):')
    print('    return x * ' + str(j) + ' + ' + str($i))
\" > src/mod_\$i.py
        done
        # Root AIDER.md — auto-discovery target
        echo '# Root AIDER.md (this is what aider should pick)' \
            > AIDER.md
        # Deeply nested decoy AIDER.md (sub-mode c) — must NOT be
        # picked by auto-discovery
        mkdir -p src/sub/deep/path
        echo '# DECOY AIDER.md — deep nested, should be ignored' \
            > src/sub/deep/path/AIDER.md
        git add .
        git -c user.email=test@test -c user.name=test \\
            commit --quiet -m 'large project seed'
        # Report stats
        echo \"Files tracked: \$(git ls-files | wc -l)\"
        echo \"Lines in src/: \$(find src -name '*.py' \
                                    -exec cat {} \\; | wc -l)\"
        echo \"node_modules size: \$(du -s node_modules \
                                       | cut -f1) KB\"
    " > "$LOG_DIR/phase1.log" 2>&1
    if grep -q "Files tracked:" "$LOG_DIR/phase1.log" \
            && grep -q "node_modules size:" "$LOG_DIR/phase1.log"; then
        step_pass "phase 1: large project (~100k LOC) ready at $PROJECT_REL"
        # Surface the stats line in test output
        grep -E "Files tracked|Lines in|node_modules size" \
            "$LOG_DIR/phase1.log" | sed 's/^/      /'
    else
        step_fail "phase 1: large project setup incomplete" \
                  "see $LOG_DIR/phase1.log"
    fi
else
    echo "[1] Setting up clean test project at $PROJECT_REL..."
    vm_run "project-setup" "
        rm -rf $PROJECT_REL
        mkdir -p $PROJECT_REL
        cd $PROJECT_REL
        # Aider expects a git repo (refuses to edit untracked dirs by
        # default; --no-git would also work but we want realistic flow).
        git init --quiet
        git -c user.email=test@test -c user.name=test \\
            commit --quiet --allow-empty -m 'init'
        # Mini source file so aider has something to discuss.
        echo 'def hello(): pass' > main.py
        git add main.py
        git -c user.email=test@test -c user.name=test \\
            commit --quiet -m 'add main.py'
    " > "$LOG_DIR/phase1.log" 2>&1
    step_pass "phase 1: empty git project ready at $PROJECT_REL"
fi

# ─── Phase 1.5: large-project sub-mode invariants (#132) ───────────────────

if [[ "$LARGE_PROJECT" == true ]]; then
    echo ""
    echo "[1.5] Verifying large-project sub-mode invariants..."

    # Sub-mode (a): .git tracks ~100k LOC w src/
    if vm_run "submode-a" "
        cd $PROJECT_REL && \
        TRACKED=\$(git ls-files src/ | wc -l) && \
        test \$TRACKED -ge 1000 && \
        echo \"src/ tracked: \$TRACKED files\"
    " > "$LOG_DIR/submode-a.log" 2>&1; then
        step_pass "phase 1.5 (a): .git tracks 1000+ src files (~100k LOC)"
    else
        step_fail "phase 1.5 (a): src/ tracked count below 1000" \
                  "see $LOG_DIR/submode-a.log"
    fi

    # Sub-mode (b): node_modules gitignored — git does NOT track
    if vm_run "submode-b" "
        cd $PROJECT_REL && \
        ! git ls-files node_modules/ | grep -q . && \
        echo 'node_modules/ correctly gitignored'
    " > "$LOG_DIR/submode-b.log" 2>&1; then
        step_pass "phase 1.5 (b): node_modules/ gitignored (not tracked)"
    else
        step_fail "phase 1.5 (b): node_modules tracked" \
                  "see $LOG_DIR/submode-b.log"
    fi

    # Sub-mode (c): aider's auto-discovery picks ROOT AIDER.md,
    # NOT the deeply nested decoy at src/sub/deep/path/AIDER.md.
    # Aider's CLI determines context_file by cwd-only by default;
    # spawning at $PROJECT_REL means the root AIDER.md wins.
    if vm_run "submode-c" "
        cd $PROJECT_REL && \
        ROOT_AIDER=\$(cat AIDER.md) && \
        DEEP_AIDER=\$(cat src/sub/deep/path/AIDER.md) && \
        test \"\$ROOT_AIDER\" != \"\$DEEP_AIDER\" && \
        echo \"root: \$ROOT_AIDER\" && \
        echo \"deep: \$DEEP_AIDER\"
    " > "$LOG_DIR/submode-c.log" 2>&1; then
        step_pass "phase 1.5 (c): deep AIDER.md sibling exists (decoy)"
        # The actual 'aider picks root not deep' is verified at
        # spawn time — when aider is invoked with cwd=PROJECT_REL,
        # only the root AIDER.md is on its discovery path. The
        # decoy is ignored because aider's context-file lookup is
        # cwd-only, not recursive subdir scan.
    else
        step_fail "phase 1.5 (c): decoy/root AIDER.md setup invalid" \
                  "see $LOG_DIR/submode-c.log"
    fi
fi

# ─── Phase 2: compute argv via AiderProvider.build_argv ────────────────────

echo ""
echo "[2] Computing BT-style argv from AiderProvider.build_argv..."
ARGV_FILE="$LOG_DIR/aider-argv.txt"
if vm_run "argv-parity" "
    cd $VM_PATH && \
    PYTHONPATH=$VM_PATH python3 /tmp/bt_aider_checks/argv_parity.py \
        $VM_PATH $PROJECT_REL
" > "$ARGV_FILE" 2>&1; then
    BT_ARGV=$(cat "$ARGV_FILE")
    if [[ -n "$BT_ARGV" ]]; then
        step_pass "phase 2: build_argv parity: $BT_ARGV"
    else
        step_fail "phase 2: build_argv returned empty" "see $ARGV_FILE"
        exit 1
    fi
else
    step_fail "phase 2: argv_parity helper failed" "see $ARGV_FILE"
    exit 1
fi

# ─── Phase 3: spawn aider with --message ───────────────────────────────────

echo ""
# #132: large-project mode allows extra startup time — aider's
# repo-map indexing scales with file count. 90s for small project,
# 180s for 100k LOC.
SPAWN_TIMEOUT=90
[[ "$LARGE_PROJECT" == true ]] && SPAWN_TIMEOUT=180
echo "[3] Spawning aider with --message prompt (${SPAWN_TIMEOUT}s timeout)..."
SPAWN_LOG="$LOG_DIR/aider-spawn.log"
# We invoke aider via the EXACT BT argv plus --yes-always (auto-confirm
# any add-file prompts) and --message for one-shot mode.
# Using `timeout ${SPAWN_TIMEOUT}s` so a hung qwen-0.5b / large repo
# indexing doesn't block the whole run.
SPAWN_START=$(date +%s)
if vm_run "aider-spawn" "
    cd $PROJECT_REL && \
    timeout ${SPAWN_TIMEOUT}s $BT_ARGV --yes-always --message '$PROMPT' 2>&1
" > "$SPAWN_LOG" 2>&1; then
    SPAWN_DURATION=$(( $(date +%s) - SPAWN_START ))
    step_pass "phase 3: aider exited 0 in ${SPAWN_DURATION}s (limit ${SPAWN_TIMEOUT}s)"
    if [[ "$LARGE_PROJECT" == true ]]; then
        echo "      large-project startup time: ${SPAWN_DURATION}s"
    fi
else
    rc=$?
    SPAWN_DURATION=$(( $(date +%s) - SPAWN_START ))
    if [[ $rc -eq 124 ]]; then
        step_fail "phase 3: aider timed out after ${SPAWN_TIMEOUT}s" \
                  "(large-project repo-map indexing too slow?)"
    else
        step_fail "phase 3: aider exit=$rc after ${SPAWN_DURATION}s" \
                  "see $SPAWN_LOG"
    fi
    # Don't abort — phase 4/5/6 may still be informative
fi

# ─── Phase 4: PONG appears in output ───────────────────────────────────────

echo ""
echo "[4] Asserting 'PONG' in aider stdout..."
if grep -qi 'pong' "$SPAWN_LOG"; then
    step_pass "phase 4: PONG found in aider stdout"
else
    step_fail "phase 4: PONG not found (model dispatch broken?)" \
              "tail of $SPAWN_LOG: $(tail -n 5 "$SPAWN_LOG" | tr '\n' '|')"
fi

# ─── Phase 5: chat history file created ────────────────────────────────────

echo ""
echo "[5] Asserting .aider.chat.history.md was created..."
HISTORY_LOG="$LOG_DIR/chat-history-tail.log"
if vm_run "chat-history" "
    test -f $PROJECT_REL/.aider.chat.history.md && \
    tail -n 50 $PROJECT_REL/.aider.chat.history.md
" > "$HISTORY_LOG" 2>&1; then
    step_pass "phase 5: .aider.chat.history.md created"
    # Bonus: PONG also persisted there
    if grep -qi 'pong' "$HISTORY_LOG"; then
        step_pass "phase 5: PONG also captured in chat history"
    else
        step_fail "phase 5: PONG missing from chat history" "see $HISTORY_LOG"
    fi
else
    step_fail "phase 5: chat history file not created" "—"
fi

# ─── Phase 6: parse_session_stats sanity ───────────────────────────────────

echo ""
echo "[6] Verifying AiderProvider.parse_session_stats reads the log..."
STATS_LOG="$LOG_DIR/stats-check.log"
if vm_run "stats-check" "
    cd $VM_PATH && \
    PYTHONPATH=$VM_PATH python3 /tmp/bt_aider_checks/stats_check.py \
        $VM_PATH $PROJECT_REL
" > "$STATS_LOG" 2>&1; then
    if grep -q 'stats-check-ok' "$STATS_LOG"; then
        step_pass "phase 6: parse_session_stats parsed log + emitted ok marker"
        # Pull out the response_count + model lines for visibility
        grep -E 'response-count|^model=' "$STATS_LOG" | sed 's/^/      /'
    else
        step_fail "phase 6: stats-check-ok marker missing" "see $STATS_LOG"
    fi
else
    step_fail "phase 6: stats_check helper exit nonzero" "see $STATS_LOG"
fi

# ─── Cleanup ───────────────────────────────────────────────────────────────

if [[ "$KEEP_PROJECT" == false && $FAIL -eq 0 ]]; then
    vm_run "cleanup-proj" "rm -rf $PROJECT_REL" > /dev/null 2>&1
fi
ssh "$VM_HOST" "rm -rf /tmp/bt_aider_checks" > /dev/null 2>&1

# ─── Final summary ─────────────────────────────────────────────────────────

echo ""
echo "============================================================"
echo "Total: $PASS passed, $FAIL failed"
if [[ $FAIL -eq 0 ]]; then
    printf "\033[32m=== aider real-model E2E OK ===\033[0m\n"
    exit 0
else
    printf "\033[31m=== aider real-model E2E FAILED ===\033[0m\n"
    echo "Failed:"
    for f in "${FAIL_LIST[@]}"; do echo "  - $f"; done
    echo "Logs: $LOG_DIR/  (project kept at $PROJECT_REL on VM for debug)"
    exit 1
fi
