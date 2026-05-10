#!/usr/bin/env bash
# vm_install.sh — sync working tree to VM and run install.sh there.
#
# Replaces "run install.sh on host" (which resets the user's running
# BTerminal) with an isolated VM install — only michal's installation
# is affected, host stays untouched.
#
# Args after `--` are forwarded to install.sh on the VM.
#
# Usage:
#   ./tools/vm_install.sh                  # default install
#   ./tools/vm_install.sh -- --no-sudo     # skip apt steps
#
# Requires: SSH alias `vm-test`, vm_sync.sh, sudo on VM (or --no-sudo).

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="vm-test"
VM_PATH="/home/michal/BTerminal"

INSTALL_ARGS=()
saw_dashdash=0
for arg in "$@"; do
    if [[ $saw_dashdash -eq 1 ]]; then
        INSTALL_ARGS+=("$arg")
    elif [[ "$arg" == "--" ]]; then
        saw_dashdash=1
    fi
done

# 1. Sync sources
"$REPO_ROOT/tools/vm_sync.sh"

# 2. Run remote install.sh
echo
echo "[vm_install] running on $VM_HOST: ./install.sh ${INSTALL_ARGS[*]:-}"
echo
ssh "$VM_HOST" "cd '$VM_PATH' && ./install.sh ${INSTALL_ARGS[*]:-}"
