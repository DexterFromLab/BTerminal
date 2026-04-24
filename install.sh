#!/bin/bash
set -euo pipefail

# BTerminal installer
# Installs BTerminal + ctx (Claude Code context manager)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.local/share/bterminal"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/bterminal"
CTX_DIR="$HOME/.claude-context"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
DESKTOP_DIR="$HOME/.local/share/applications"

NO_SUDO=false
if [[ "${1:-}" == "--no-sudo" ]]; then
    NO_SUDO=true
fi

echo "=== BTerminal Installer ==="
echo ""

# ─── Claude Code ──────────────────────────────────────────────────────

echo "[1/6] Checking Claude Code..."

# Ensure ~/.local/bin exists early — we'll symlink claude here for a stable path
mkdir -p "$BIN_DIR"

find_claude_bin() {
    # Prefer real install locations over our own symlink so we can repoint it.
    local candidates=(
        "$HOME/.npm-global/bin/claude"
        "/usr/local/bin/claude"
        "/usr/bin/claude"
        "/opt/homebrew/bin/claude"
    )
    for p in "${candidates[@]}"; do
        [[ -x "$p" ]] && { echo "$p"; return; }
    done
    for p in "$HOME"/.nvm/versions/node/*/bin/claude; do
        [[ -x "$p" ]] && { echo "$p"; return; }
    done
    # Last resort: our own symlink (or whatever is on PATH).
    if [[ -x "$HOME/.local/bin/claude" ]]; then
        echo "$HOME/.local/bin/claude"
        return
    fi
    command -v claude 2>/dev/null || true
}

EXISTING_CLAUDE="$(find_claude_bin)"

if [[ -n "$EXISTING_CLAUDE" ]]; then
    CLAUDE_VER="$("$EXISTING_CLAUDE" --version 2>/dev/null || echo 'unknown')"
    echo "  Claude Code already installed: $CLAUDE_VER ($EXISTING_CLAUDE)"
elif [[ "$NO_SUDO" == true ]]; then
    echo "  Claude Code not found (skipped — no-sudo mode)."
else
    echo "  Claude Code not found. Installing via npm..."
    if ! command -v npm &>/dev/null; then
        echo "  npm not found. Installing Node.js..."
        if command -v curl &>/dev/null; then
            curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -
            sudo apt install -y nodejs
        else
            sudo apt install -y nodejs npm
        fi
    fi
    # Set up npm prefix for non-root installs
    NPM_PREFIX="${HOME}/.npm-global"
    mkdir -p "$NPM_PREFIX"
    npm config set prefix "$NPM_PREFIX"
    export PATH="$NPM_PREFIX/bin:$PATH"
    npm install -g @anthropic-ai/claude-code
    EXISTING_CLAUDE="$(find_claude_bin)"
    CLAUDE_VER="$("$EXISTING_CLAUDE" --version 2>/dev/null || echo 'unknown')"
    echo "  Claude Code installed: $CLAUDE_VER ($EXISTING_CLAUDE)"
fi

# Symlink claude to ~/.local/bin for a PATH-independent stable location.
# Without this, GUI launches (desktop entry) often fail to find claude
# because ~/.npm-global/bin lives only in ~/.bashrc.
if [[ -n "$EXISTING_CLAUDE" && "$EXISTING_CLAUDE" != "$BIN_DIR/claude" ]]; then
    ln -sf "$EXISTING_CLAUDE" "$BIN_DIR/claude"
    echo "  Linked $BIN_DIR/claude -> $EXISTING_CLAUDE"
fi

# ─── System dependencies ───────────────────────────────────────────────

echo "[2/6] Checking system dependencies..."

MISSING=()
command -v git &>/dev/null || MISSING+=("git")
command -v git-lfs &>/dev/null || MISSING+=("git-lfs")
python3 -c "import gi" 2>/dev/null || MISSING+=("python3-gi")
python3 -c "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk" 2>/dev/null || MISSING+=("gir1.2-gtk-3.0")
python3 -c "import gi; gi.require_version('Vte', '2.91'); from gi.repository import Vte" 2>/dev/null || MISSING+=("gir1.2-vte-2.91")

if [ ${#MISSING[@]} -gt 0 ]; then
    if [[ "$NO_SUDO" == true ]]; then
        echo "  Missing: ${MISSING[*]} (skipped — no-sudo mode)"
    else
        echo "  Missing: ${MISSING[*]}"
        echo "  Installing..."
        sudo apt-get update -qq
        sudo apt-get install -y "${MISSING[@]}"
    fi
else
    echo "  All dependencies OK."
fi

# Initialize git-lfs if available
if command -v git-lfs &>/dev/null; then
    git lfs install --skip-repo >/dev/null 2>&1
    echo "  git-lfs initialized."
fi

# ─── Install files ─────────────────────────────────────────────────────

echo "[3/6] Installing BTerminal..."

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$CONFIG_DIR" "$CTX_DIR" "$ICON_DIR"

cp "$SCRIPT_DIR/bterminal.py" "$INSTALL_DIR/bterminal.py"
cp "$SCRIPT_DIR/ctx" "$INSTALL_DIR/ctx"
cp "$SCRIPT_DIR/consult" "$INSTALL_DIR/consult"
cp "$SCRIPT_DIR/tasks" "$INSTALL_DIR/tasks"
cp "$SCRIPT_DIR/claude_log" "$INSTALL_DIR/claude_log"
cp "$SCRIPT_DIR/memory_wizard" "$INSTALL_DIR/memory_wizard"
cp "$SCRIPT_DIR/bterminal.svg" "$ICON_DIR/bterminal.svg"
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor/" 2>/dev/null || true
chmod +x "$INSTALL_DIR/bterminal.py" "$INSTALL_DIR/ctx" "$INSTALL_DIR/consult" "$INSTALL_DIR/tasks" "$INSTALL_DIR/claude_log" "$INSTALL_DIR/memory_wizard"

# Save repo path for auto-update
echo "$SCRIPT_DIR" > "$CONFIG_DIR/repo_path"
echo "  Repo path saved: $SCRIPT_DIR"

# ─── Symlinks ──────────────────────────────────────────────────────────

echo "[4/6] Creating symlinks in $BIN_DIR..."

ln -sf "$INSTALL_DIR/bterminal.py" "$BIN_DIR/bterminal"
ln -sf "$INSTALL_DIR/ctx" "$BIN_DIR/ctx"
ln -sf "$INSTALL_DIR/consult" "$BIN_DIR/consult"
ln -sf "$INSTALL_DIR/tasks" "$BIN_DIR/tasks"
ln -sf "$INSTALL_DIR/claude_log" "$BIN_DIR/claude_log"
ln -sf "$INSTALL_DIR/memory_wizard" "$BIN_DIR/memory_wizard"

echo "  bterminal      -> $INSTALL_DIR/bterminal.py"
echo "  ctx            -> $INSTALL_DIR/ctx"
echo "  consult        -> $INSTALL_DIR/consult"
echo "  tasks          -> $INSTALL_DIR/tasks"
echo "  claude_log     -> $INSTALL_DIR/claude_log"
echo "  memory_wizard  -> $INSTALL_DIR/memory_wizard"

# ─── Init ctx database ────────────────────────────────────────────────

echo "[5/6] Initializing context database..."

if [ -f "$CTX_DIR/context.db" ]; then
    echo "  Database already exists, skipping init."
else
    "$BIN_DIR/ctx" list >/dev/null 2>&1
    echo "  Created $CTX_DIR/context.db"
fi

# Set default shared context
"$BIN_DIR/ctx" shared set user_preferences "At the start of each session, tell the user which model you are before beginning work."
echo "  Shared context: user_preferences set"

# ─── Desktop file ──────────────────────────────────────────────────────

echo "[6/6] Creating desktop entry..."

mkdir -p "$DESKTOP_DIR"
cat > "$DESKTOP_DIR/bterminal.desktop" << EOF
[Desktop Entry]
Name=BTerminal
Comment=Terminal with SSH & Claude Code session management
Exec=$BIN_DIR/bterminal
Icon=bterminal
Type=Application
Categories=System;TerminalEmulator;
Terminal=false
StartupNotify=true
EOF
update-desktop-database "$DESKTOP_DIR" 2>/dev/null || true

echo ""
echo "=== Installation complete ==="
echo ""
echo "Run BTerminal:"
echo "  bterminal"
echo ""
echo "Context manager:"
echo "  ctx --help"
echo ""
echo "Consult external AI models:"
echo "  consult --help"
echo ""
echo "Task manager:"
echo "  tasks --help"
echo ""
echo "Session log collector:"
echo "  claude_log --help"
echo ""
echo "Memory wizard:"
echo "  memory_wizard <project>"
echo ""
echo "Make sure these are in your PATH (add to ~/.bashrc):"
echo "  export PATH=\"\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH\""
