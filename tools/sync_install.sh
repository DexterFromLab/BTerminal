#!/usr/bin/env bash
# sync_install.sh — single-file rsync deploy from working tree to the
# already-installed BTerminal location (V2 / 2026-05-07).
#
# What it does:
#   * rsync working-tree `bterminal/` → `~/.local/share/bterminal/bterminal/`
#     (incremental, --delete, --exclude=__pycache__/*.pyc)
#   * rsync working-tree `tools/{ctx,consult,tasks,claude_log,memory_wizard}`
#     → `~/.local/share/bterminal/` (CLI binaries — flat layout)
#   * Refresh `~/.local/share/bterminal/bterminal-launcher` only when
#     INSTALL_DIR moved (script is otherwise stable across versions).
#   * Optionally recompile `locale/*/LC_MESSAGES/*.po` → `.mo` if any
#     `.po` is newer than its `.mo` (gettext catalogs).
#
# What it does NOT do (vs install.sh):
#   * apt / npm package checks
#   * Claude Code / Copilot CLI install
#   * sudo
#   * License acceptance (unchanged)
#   * desktop entry / icon cache
#   * symlinks (defaults/, README.md, VERSION, locale/) — those stay
#     live-symlinked into the repo by the original install.sh; sync
#     to them is automatic since the symlink target IS the working tree.
#
# Use this when iterating on BTerminal's own code so the running BT
# instance picks up changes without resetting config / restarting
# system services. install.sh is still required for fresh installs
# and after system-package changes.
#
# Usage:
#   ./tools/sync_install.sh              # sync to default ~/.local/share/bterminal/
#   ./tools/sync_install.sh --target X   # sync to alternate INSTALL_DIR
#   ./tools/sync_install.sh --check      # dry-run, list what would change

set -euo pipefail

REPO_ROOT="$(cd "$(dirname "$0")/.." && pwd)"
INSTALL_DIR="${HOME}/.local/share/bterminal"
DRY_RUN=0

while [[ $# -gt 0 ]]; do
    case "$1" in
        --target) INSTALL_DIR="$2"; shift 2 ;;
        --check|--dry-run) DRY_RUN=1; shift ;;
        -h|--help)
            sed -n '2,/^$/p' "$0" | sed 's/^# \?//'
            exit 0
            ;;
        *) echo "Unknown arg: $1" >&2; exit 2 ;;
    esac
done

if [[ ! -d "$INSTALL_DIR" ]]; then
    echo "[sync_install] $INSTALL_DIR does not exist." >&2
    echo "[sync_install] Run ./install.sh first for a fresh install." >&2
    exit 1
fi

# CLI tool names that install.sh deploys flat at $INSTALL_DIR.
CLI_TOOLS=(ctx consult tasks claude_log memory_wizard mock_ai_cli)

RSYNC_OPTS=(-a --delete
            --exclude=__pycache__/
            --exclude=*.pyc
            --exclude=.pytest_cache/)
if [[ $DRY_RUN -eq 1 ]]; then
    RSYNC_OPTS+=(-vn)
    echo "[sync_install] dry-run mode — no files written"
else
    RSYNC_OPTS+=(-v)
fi

# 1. bterminal/ Python package
echo "[sync_install] $REPO_ROOT/bterminal/  →  $INSTALL_DIR/bterminal/"
rsync "${RSYNC_OPTS[@]}" "$REPO_ROOT/bterminal/" "$INSTALL_DIR/bterminal/"

# 2. CLI tools (flat at $INSTALL_DIR root)
for tool in "${CLI_TOOLS[@]}"; do
    src="$REPO_ROOT/tools/$tool"
    dst="$INSTALL_DIR/$tool"
    if [[ -f "$src" ]]; then
        if [[ $DRY_RUN -eq 1 ]]; then
            cmp -s "$src" "$dst" 2>/dev/null \
                && echo "  [unchanged] $tool" \
                || echo "  [would copy] $tool"
        else
            cp -f "$src" "$dst"
            chmod +x "$dst"
        fi
    fi
done

# 3. Recompile .po -> .mo when any .po is newer than its .mo.
if compgen -G "$REPO_ROOT/locale/*/LC_MESSAGES/*.po" > /dev/null; then
    if command -v msgfmt &>/dev/null; then
        rebuilt=0
        for po in "$REPO_ROOT"/locale/*/LC_MESSAGES/*.po; do
            mo="${po%.po}.mo"
            if [[ ! -f "$mo" ]] || [[ "$po" -nt "$mo" ]]; then
                if [[ $DRY_RUN -eq 1 ]]; then
                    echo "  [would compile] $(basename "$po")"
                else
                    msgfmt --check --output-file="$mo" "$po" 2>/dev/null \
                        && rebuilt=$((rebuilt + 1)) || true
                fi
            fi
        done
        [[ $DRY_RUN -eq 0 && $rebuilt -gt 0 ]] \
            && echo "[sync_install] recompiled $rebuilt translation catalog(s)"
    fi
fi

if [[ $DRY_RUN -eq 1 ]]; then
    echo "[sync_install] dry-run complete"
else
    echo "[sync_install] OK — restart BTerminal to pick up changes"
fi
