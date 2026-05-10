#!/usr/bin/env bash
# vm_test.sh — sync working tree to VM and run the test suite there.
#
# Default: --quick (unit only, ~0.3s, no subprocess BT spawn).
# Args after `--` are forwarded to test_all.sh on the VM.
#
# Usage:
#   ./tools/vm_test.sh                     # --quick (unit only)
#   ./tools/vm_test.sh -- --slow           # full + slow on VM
#   ./tools/vm_test.sh -- --layer e2e      # only E2E tests on VM
#
# Requires: SSH alias `vm-test`, vm_sync.sh.

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="vm-test"
VM_PATH="/home/michal/BTerminal"

# Forward args after `--` to remote test_all.sh
TEST_ARGS=()
saw_dashdash=0
for arg in "$@"; do
    if [[ $saw_dashdash -eq 1 ]]; then
        TEST_ARGS+=("$arg")
    elif [[ "$arg" == "--" ]]; then
        saw_dashdash=1
    fi
done
if [[ ${#TEST_ARGS[@]} -eq 0 ]]; then
    TEST_ARGS=("--quick")
fi

# 1. Sync sources to VM
"$REPO_ROOT/tools/vm_sync.sh"

# 2. Run remote test suite
echo
echo "[vm_test] running on $VM_HOST: ./tools/test_all.sh ${TEST_ARGS[*]}"
echo
ssh "$VM_HOST" "cd '$VM_PATH' && ./tools/test_all.sh ${TEST_ARGS[*]}"
