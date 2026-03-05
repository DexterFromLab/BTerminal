#!/bin/bash
set -euo pipefail

# BTerminal installer
# Installs BTerminal + ctx (Claude Code context manager)

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.local/share/bterminal"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/bterminal"
CTX_DIR="$HOME/.claude-context"
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

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$CONFIG_DIR" "$CTX_DIR"

cp "$SCRIPT_DIR/bterminal.py" "$INSTALL_DIR/bterminal.py"
cp "$SCRIPT_DIR/ctx" "$INSTALL_DIR/ctx"
chmod +x "$INSTALL_DIR/bterminal.py" "$INSTALL_DIR/ctx"

# ─── Symlinks ──────────────────────────────────────────────────────────

echo "[3/5] Creating symlinks in $BIN_DIR..."

ln -sf "$INSTALL_DIR/bterminal.py" "$BIN_DIR/bterminal"
ln -sf "$INSTALL_DIR/ctx" "$BIN_DIR/ctx"

echo "  bterminal -> $INSTALL_DIR/bterminal.py"
echo "  ctx       -> $INSTALL_DIR/ctx"

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
Icon=utilities-terminal
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
echo "Make sure $BIN_DIR is in your PATH."
echo "If not, add to ~/.bashrc:"
echo "  export PATH=\"\$HOME/.local/bin:\$PATH\""
