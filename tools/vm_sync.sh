#!/usr/bin/env bash
# vm_sync.sh — rsync working tree to the test VM (T-V3 / V2 workflow).
#
# Replaces git-pull-on-VM (which fails on uncommitted local changes,
# see V1 bug) with an idempotent file-level rsync. The VM's git repo
# stays at whatever release it was at — we just overwrite source files.
#
# Usage:   ./tools/vm_sync.sh
# Requires: SSH alias `vm-test` (see ~/.ssh/config)

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
VM_HOST="vm-test"
VM_PATH="/home/michal/BTerminal"

# rsync options:
#   -a     archive (perms, times, symlinks)
#   -v     verbose (one line per changed file)
#   -z     compress over SSH
#   --delete           drop files removed on host
#   --exclude='.git'   keep VM's git history intact
#   --exclude='__pycache__'  / .pytest_cache (regenerated)
#   --exclude='*.bak'  / *.tmp / log files
echo "[vm_sync] $REPO_ROOT/  →  $VM_HOST:$VM_PATH/"
rsync -avz --delete \
    --exclude='.git/' \
    --exclude='__pycache__/' \
    --exclude='.pytest_cache/' \
    --exclude='copied_images/' \
    --exclude='node_modules/' \
    --exclude='*.bak' \
    --exclude='*.tmp' \
    --exclude='*.log' \
    "$REPO_ROOT/" "$VM_HOST:$VM_PATH/"

echo "[vm_sync] OK"
