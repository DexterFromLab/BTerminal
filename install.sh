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

echo "=== BTerminal Installer ==="
echo ""

# ─── System dependencies ───────────────────────────────────────────────

echo "[1/5] Checking system dependencies..."

MISSING=()
python3 -c "import gi" 2>/dev/null || MISSING+=("python3-gi")
python3 -c "import gi; gi.require_version('Gtk', '3.0'); from gi.repository import Gtk" 2>/dev/null || MISSING+=("gir1.2-gtk-3.0")
python3 -c "import gi; gi.require_version('Vte', '2.91'); from gi.repository import Vte" 2>/dev/null || MISSING+=("gir1.2-vte-2.91")

if [ ${#MISSING[@]} -gt 0 ]; then
    echo "  Missing: ${MISSING[*]}"
    echo "  Installing..."
    sudo apt install -y "${MISSING[@]}"
else
    echo "  All dependencies OK."
fi

# ─── Install files ─────────────────────────────────────────────────────

echo "[2/5] Installing BTerminal..."

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$CONFIG_DIR" "$CTX_DIR" "$ICON_DIR"

cp "$SCRIPT_DIR/bterminal.py" "$INSTALL_DIR/bterminal.py"
cp "$SCRIPT_DIR/ctx" "$INSTALL_DIR/ctx"
cp "$SCRIPT_DIR/consult" "$INSTALL_DIR/consult"
cp "$SCRIPT_DIR/tasks" "$INSTALL_DIR/tasks"
cp "$SCRIPT_DIR/bterminal.svg" "$ICON_DIR/bterminal.svg"
chmod +x "$INSTALL_DIR/bterminal.py" "$INSTALL_DIR/ctx" "$INSTALL_DIR/consult" "$INSTALL_DIR/tasks"

# ─── Symlinks ──────────────────────────────────────────────────────────

echo "[3/5] Creating symlinks in $BIN_DIR..."

ln -sf "$INSTALL_DIR/bterminal.py" "$BIN_DIR/bterminal"
ln -sf "$INSTALL_DIR/ctx" "$BIN_DIR/ctx"
ln -sf "$INSTALL_DIR/consult" "$BIN_DIR/consult"
ln -sf "$INSTALL_DIR/tasks" "$BIN_DIR/tasks"

echo "  bterminal -> $INSTALL_DIR/bterminal.py"
echo "  ctx       -> $INSTALL_DIR/ctx"
echo "  consult   -> $INSTALL_DIR/consult"
echo "  tasks     -> $INSTALL_DIR/tasks"

# ─── Init ctx database ────────────────────────────────────────────────

echo "[4/5] Initializing context database..."

if [ -f "$CTX_DIR/context.db" ]; then
    echo "  Database already exists, skipping."
else
    "$BIN_DIR/ctx" list >/dev/null 2>&1
    echo "  Created $CTX_DIR/context.db"
fi

# ─── Desktop file ──────────────────────────────────────────────────────

echo "[5/5] Creating desktop entry..."

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
echo "Make sure $BIN_DIR is in your PATH."
echo "If not, add to ~/.bashrc:"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
