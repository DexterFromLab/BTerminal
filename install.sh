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

if command -v claude &>/dev/null; then
    CLAUDE_VER="$(claude --version 2>/dev/null || echo 'unknown')"
    echo "  Claude Code already installed: $CLAUDE_VER"
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
    CLAUDE_VER="$(claude --version 2>/dev/null || echo 'unknown')"
    echo "  Claude Code installed: $CLAUDE_VER"
fi

# ─── System dependencies ───────────────────────────────────────────────

echo "[2/6] Checking system dependencies..."

MISSING=()
command -v git &>/dev/null || MISSING+=("git")
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

# ─── Install files ─────────────────────────────────────────────────────

echo "[3/6] Installing BTerminal..."

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$CONFIG_DIR" "$CTX_DIR" "$ICON_DIR"

cp "$SCRIPT_DIR/bterminal.py" "$INSTALL_DIR/bterminal.py"
cp "$SCRIPT_DIR/ctx" "$INSTALL_DIR/ctx"
cp "$SCRIPT_DIR/consult" "$INSTALL_DIR/consult"
cp "$SCRIPT_DIR/tasks" "$INSTALL_DIR/tasks"
cp "$SCRIPT_DIR/bterminal.svg" "$ICON_DIR/bterminal.svg"
chmod +x "$INSTALL_DIR/bterminal.py" "$INSTALL_DIR/ctx" "$INSTALL_DIR/consult" "$INSTALL_DIR/tasks"

# Save repo path for auto-update
echo "$SCRIPT_DIR" > "$CONFIG_DIR/repo_path"
echo "  Repo path saved: $SCRIPT_DIR"

# ─── Symlinks ──────────────────────────────────────────────────────────

echo "[4/6] Creating symlinks in $BIN_DIR..."

ln -sf "$INSTALL_DIR/bterminal.py" "$BIN_DIR/bterminal"
ln -sf "$INSTALL_DIR/ctx" "$BIN_DIR/ctx"
ln -sf "$INSTALL_DIR/consult" "$BIN_DIR/consult"
ln -sf "$INSTALL_DIR/tasks" "$BIN_DIR/tasks"

echo "  bterminal -> $INSTALL_DIR/bterminal.py"
echo "  ctx       -> $INSTALL_DIR/ctx"
echo "  consult   -> $INSTALL_DIR/consult"
echo "  tasks     -> $INSTALL_DIR/tasks"

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
echo "Make sure these are in your PATH (add to ~/.bashrc):"
echo "  export PATH=\"\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH\""
