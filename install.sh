#!/bin/bash
set -euo pipefail

# BTerminal installer / updater
# Reads defaults/dependencies.json and enforces version requirements.
# Exit code 1 = critical dependency error (shown as dialog in GUI auto-update).

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.local/share/bterminal"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/bterminal"
CTX_DIR="$HOME/.claude-context"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
DESKTOP_DIR="$HOME/.local/share/applications"
DEPS_JSON="$SCRIPT_DIR/defaults/dependencies.json"
ERRORS_FILE="$CONFIG_DIR/install_errors.json"

NO_SUDO=false
[[ "${1:-}" == "--no-sudo" ]] && NO_SUDO=true

BTERMINAL_VERSION="$(cat "$SCRIPT_DIR/VERSION" 2>/dev/null || echo 'unknown')"

echo "=== BTerminal Installer v${BTERMINAL_VERSION} ==="
echo ""

# ─── Helpers ───────────────────────────────────────────────────────────────

ERRORS=()
WARNINGS=()

ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; }
warn() { printf "  \033[33m⚠\033[0m %s\n" "$*"; WARNINGS+=("$*"); }
fail() { printf "  \033[31m✗\033[0m %s\n" "$*"; ERRORS+=("$*"); }
info() { printf "    %s\n" "$*"; }

# ─── Rollback ──────────────────────────────────────────────────────────────

BACKUP_DIR=""
BTERMINAL_FILES=(bterminal.py ctx consult tasks claude_log memory_wizard)

_on_error() {
    local code=$?
    echo ""
    if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
        printf "  \033[33m⚠\033[0m Installation failed — restoring previous version...\n"
        for f in "${BTERMINAL_FILES[@]}"; do
            [[ -f "$BACKUP_DIR/$f" ]] && cp -f "$BACKUP_DIR/$f" "$INSTALL_DIR/$f" 2>/dev/null || true
        done
        rm -rf "$BACKUP_DIR"
        printf "  \033[32m✓\033[0m Previous version restored.\n"
        # Marker for BTerminal GUI — rollback succeeded, show user-friendly dialog
        echo "BTERMINAL_ROLLBACK_OK" >&2
    else
        printf "  \033[31m✗\033[0m Installation failed (fresh install, nothing to restore).\n"
        printf "    Fix the error above and run ./install.sh again.\n"
        echo "BTERMINAL_FRESH_INSTALL_FAILED" >&2
    fi
    exit "$code"
}

trap '_on_error' ERR

# Returns 0 if version $1 >= $2 (dot-separated)
ver_ge() {
    python3 -c "
a = tuple(int(x) for x in '$1'.split('.')[:3] if x.isdigit())
b = tuple(int(x) for x in '$2'.split('.')[:3] if x.isdigit())
exit(0 if a >= b else 1)
" 2>/dev/null
}

apt_install() {
    if [[ "$NO_SUDO" == true ]]; then
        warn "Cannot install $* without sudo — run install.sh without --no-sudo"
        return 1
    fi
    sudo apt-get install -y "$@" -qq
}

# Read a value from dependencies.json using python3
dep_get() {  # dep_get <category> <name> <field>
    python3 -c "
import json, sys
d = json.load(open('$DEPS_JSON'))
val = d.get('$1', {}).get('$2', {}).get('$3', '')
print(val)
" 2>/dev/null || true
}

mkdir -p "$CONFIG_DIR" "$BIN_DIR" "$INSTALL_DIR"

# ─── [1/7] Runtime: Python, Node, npm ─────────────────────────────────────

echo "[1/7] Checking runtime..."

# Python 3
PY_VER="$(python3 -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}.{sys.version_info.micro}')" 2>/dev/null || echo '0.0.0')"
PY_MIN="$(dep_get runtime python3 version | tr -d '>=')"
if ver_ge "$PY_VER" "$PY_MIN"; then
    ok "python3 $PY_VER (>= $PY_MIN required)"
else
    fail "python3 $PY_VER found, need >= $PY_MIN — upgrade Python manually (pyenv or system package)"
fi

# Node.js
NODE_RAW="$(node --version 2>/dev/null | tr -d 'v' || echo '0.0.0')"
NODE_MIN="$(dep_get runtime nodejs version | tr -d '>=')"
if ver_ge "$NODE_RAW" "$NODE_MIN"; then
    ok "node v$NODE_RAW (>= $NODE_MIN required)"
else
    info "node v$NODE_RAW found, need >= $NODE_MIN — upgrading via NodeSource..."
    if [[ "$NO_SUDO" == true ]]; then
        fail "Node.js $NODE_RAW < $NODE_MIN — re-run installer without --no-sudo to upgrade"
    else
        if command -v curl &>/dev/null; then
            curl -fsSL https://deb.nodesource.com/setup_22.x | sudo bash -
        fi
        sudo apt-get install -y nodejs
        NODE_RAW="$(node --version 2>/dev/null | tr -d 'v' || echo '0.0.0')"
        if ver_ge "$NODE_RAW" "$NODE_MIN"; then
            ok "node v$NODE_RAW (upgraded)"
        else
            fail "Node.js upgrade failed — version $NODE_RAW, need >= $NODE_MIN"
        fi
    fi
fi

# npm
NPM_VER="$(npm --version 2>/dev/null || echo '0.0.0')"
NPM_MIN="$(dep_get runtime npm version | tr -d '>=')"
if ver_ge "$NPM_VER" "$NPM_MIN"; then
    ok "npm $NPM_VER (>= $NPM_MIN required)"
else
    info "npm $NPM_VER found, need >= $NPM_MIN — upgrading..."
    if npm install -g npm@latest --quiet 2>/dev/null; then
        NPM_VER="$(npm --version 2>/dev/null || echo '0.0.0')"
        ok "npm $NPM_VER (upgraded)"
    else
        warn "npm upgrade failed — continuing with $NPM_VER"
    fi
fi

# ─── [2/7] Claude Code ─────────────────────────────────────────────────────

echo "[2/7] Checking Claude Code..."

find_claude_bin() {
    local candidates=(
        "$HOME/.npm-global/bin/claude"
        "/usr/local/bin/claude"
        "/usr/bin/claude"
        "/opt/homebrew/bin/claude"
    )
    for p in "${candidates[@]}"; do [[ -x "$p" ]] && { echo "$p"; return; }; done
    for p in "$HOME"/.nvm/versions/node/*/bin/claude; do [[ -x "$p" ]] && { echo "$p"; return; }; done
    [[ -x "$BIN_DIR/claude" ]] && { echo "$BIN_DIR/claude"; return; }
    command -v claude 2>/dev/null || true
}

EXISTING_CLAUDE="$(find_claude_bin)"
NPM_PREFIX="${HOME}/.npm-global"
mkdir -p "$NPM_PREFIX"

if [[ -n "$EXISTING_CLAUDE" ]]; then
    CLAUDE_VER="$("$EXISTING_CLAUDE" --version 2>/dev/null || echo 'unknown')"
    ok "claude $CLAUDE_VER ($EXISTING_CLAUDE)"
    info "Updating to latest..."
    npm config set prefix "$NPM_PREFIX" 2>/dev/null || true
    export PATH="$NPM_PREFIX/bin:$PATH"
    if npm install -g @anthropic-ai/claude-code --quiet 2>/dev/null; then
        EXISTING_CLAUDE="$(find_claude_bin)"
        CLAUDE_VER_NEW="$("$EXISTING_CLAUDE" --version 2>/dev/null || echo 'unknown')"
        [[ "$CLAUDE_VER_NEW" != "$CLAUDE_VER" ]] && info "Updated: $CLAUDE_VER → $CLAUDE_VER_NEW" || info "Already up to date"
    else
        warn "Claude Code update failed — continuing with $CLAUDE_VER"
    fi
elif [[ "$NO_SUDO" == true ]]; then
    warn "Claude Code not found (skipped — no-sudo mode)"
else
    info "Claude Code not found — installing..."
    npm config set prefix "$NPM_PREFIX"
    export PATH="$NPM_PREFIX/bin:$PATH"
    if npm install -g @anthropic-ai/claude-code --quiet; then
        EXISTING_CLAUDE="$(find_claude_bin)"
        CLAUDE_VER="$("$EXISTING_CLAUDE" --version 2>/dev/null || echo 'unknown')"
        ok "claude $CLAUDE_VER (installed)"
    else
        fail "Claude Code installation failed — install manually: npm install -g @anthropic-ai/claude-code"
    fi
fi

# Stable symlink for GUI launches (desktop entry bypasses ~/.bashrc PATH)
if [[ -n "${EXISTING_CLAUDE:-}" && "$EXISTING_CLAUDE" != "$BIN_DIR/claude" ]]; then
    ln -sf "$EXISTING_CLAUDE" "$BIN_DIR/claude"
    info "Linked $BIN_DIR/claude -> $EXISTING_CLAUDE"
fi

# ─── [3/7] System tools ────────────────────────────────────────────────────

echo "[3/7] Checking system tools..."

check_tool() {  # check_tool <cmd> <apt_pkg> <required: true|auto|false> <label>
    local cmd="$1" pkg="$2" required="$3" label="$4"
    if command -v "$cmd" &>/dev/null; then
        ok "$label"
    elif [[ "$required" == "true" ]]; then
        info "$label not found — installing $pkg..."
        if apt_install "$pkg"; then
            ok "$label (installed)"
        else
            fail "$label required but could not be installed — apt install $pkg"
        fi
    elif [[ "$required" == "auto" ]]; then
        info "$label not found — installing $pkg..."
        if apt_install "$pkg"; then
            ok "$label (installed)"
        else
            warn "$label could not be installed — apt install $pkg"
        fi
    else
        warn "$label not found (optional) — apt install $pkg"
    fi
}

check_tool git        git             true  "git"
check_tool git-lfs    git-lfs         false "git-lfs"
check_tool ssh        openssh-client  true  "ssh"
check_tool xdg-open   xdg-utils       false "xdg-open"
check_tool meld       meld            auto  "meld"
check_tool pdflatex   texlive-latex-extra auto "pdflatex (LaTeX)"
check_tool latexmk    latexmk         auto  "latexmk"
check_tool pdftoppm   poppler-utils   auto  "poppler-utils (PDF preview)"
check_tool pandoc     pandoc          auto  "pandoc"

if command -v git-lfs &>/dev/null; then
    git lfs install --skip-repo >/dev/null 2>&1 || true
fi

# ─── [4/7] GTK bindings ────────────────────────────────────────────────────

echo "[4/7] Checking GTK bindings..."

check_gtk() {  # check_gtk <label> <apt_pkg> <python_check>
    local label="$1" pkg="$2" pycheck="$3"
    if python3 -c "$pycheck" 2>/dev/null; then
        ok "$label"
    else
        info "$label not found — installing $pkg..."
        if apt_install "$pkg"; then
            if python3 -c "$pycheck" 2>/dev/null; then
                ok "$label (installed)"
            else
                fail "$label installed but import failed — try: apt install $pkg"
            fi
        else
            fail "$label required but could not be installed — apt install $pkg"
        fi
    fi
}

check_gtk "python3-gi"   "python3-gi"        "import gi"
check_gtk "GTK 3.0"      "gir1.2-gtk-3.0"   "import gi; gi.require_version('Gtk','3.0'); from gi.repository import Gtk"
check_gtk "VTE 2.91"     "gir1.2-vte-2.91"  "import gi; gi.require_version('Vte','2.91'); from gi.repository import Vte"

# ─── [5/7] Install BTerminal files ─────────────────────────────────────────

echo "[5/7] Installing BTerminal files..."

# Backup current installation so ERR trap can restore it on failure
if [[ -f "$INSTALL_DIR/bterminal.py" ]]; then
    BACKUP_DIR="$(mktemp -d /tmp/bterminal-backup-XXXXXX)"
    for f in "${BTERMINAL_FILES[@]}"; do
        [[ -f "$INSTALL_DIR/$f" ]] && cp -f "$INSTALL_DIR/$f" "$BACKUP_DIR/$f" 2>/dev/null || true
    done
    info "Backup: $BACKUP_DIR"
fi

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$CONFIG_DIR" "$CTX_DIR" "$ICON_DIR"

for f in bterminal.py ctx consult tasks claude_log memory_wizard; do
    cp "$SCRIPT_DIR/$f" "$INSTALL_DIR/$f"
done
cp "$SCRIPT_DIR/bterminal.svg" "$ICON_DIR/bterminal.svg"
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor/" 2>/dev/null || true
chmod +x "$INSTALL_DIR/bterminal.py" "$INSTALL_DIR/ctx" "$INSTALL_DIR/consult" \
         "$INSTALL_DIR/tasks" "$INSTALL_DIR/claude_log" "$INSTALL_DIR/memory_wizard"

# Live symlinks from repo (git pull → immediate effect, no reinstall needed)
ln -sfn "$SCRIPT_DIR/defaults" "$INSTALL_DIR/defaults"
ln -sf  "$SCRIPT_DIR/README.md" "$INSTALL_DIR/README.md"
ln -sf  "$SCRIPT_DIR/VERSION"   "$INSTALL_DIR/VERSION"
info "Live symlinks: defaults/ README.md VERSION"

echo "$SCRIPT_DIR" > "$CONFIG_DIR/repo_path"
info "Repo path: $SCRIPT_DIR"

# Bundled skills — install new ones, never overwrite user edits
SKILLS_SRC="$SCRIPT_DIR/defaults/skills"
SKILLS_DST="$HOME/.claude/commands"
mkdir -p "$SKILLS_DST"
SKILLS_NEW=0; SKILLS_SKIP=0
if [[ -d "$SKILLS_SRC" ]]; then
    for skill_file in "$SKILLS_SRC"/*.md; do
        [[ -f "$skill_file" ]] || continue
        skill_name="$(basename "$skill_file")"
        if [[ -f "$SKILLS_DST/$skill_name" ]]; then
            SKILLS_SKIP=$((SKILLS_SKIP + 1))
        else
            cp "$skill_file" "$SKILLS_DST/$skill_name"
            SKILLS_NEW=$((SKILLS_NEW + 1))
            info "Skill installed: $skill_name"
        fi
    done
    ok "Skills: $SKILLS_NEW installed, $SKILLS_SKIP already present"
fi

# latex-document-skill extension — pin to commit from dependencies.json
LATEX_EXT_DIR="$INSTALL_DIR/extensions/latex-document-skill"
LATEX_SKILL_LINK="$SKILLS_DST/latex.md"
LATEX_REPO="$(dep_get extensions latex-document-skill repo)"
LATEX_PIN="$(dep_get extensions latex-document-skill version)"
mkdir -p "$INSTALL_DIR/extensions"

if [[ -n "$LATEX_REPO" ]] && command -v git &>/dev/null; then
    if [[ ! -d "$LATEX_EXT_DIR/.git" ]]; then
        info "latex-document-skill: cloning..."
        if git clone --quiet "$LATEX_REPO" "$LATEX_EXT_DIR" 2>/dev/null; then
            ok "latex-document-skill (cloned)"
        else
            warn "latex-document-skill clone failed — check internet connection"
        fi
    fi

    if [[ -d "$LATEX_EXT_DIR/.git" && -n "$LATEX_PIN" ]]; then
        CURRENT="$(git -C "$LATEX_EXT_DIR" rev-parse --short HEAD 2>/dev/null || echo '')"
        if [[ "$CURRENT" != "$LATEX_PIN" ]]; then
            info "latex-document-skill: switching $CURRENT → $LATEX_PIN"
            git -C "$LATEX_EXT_DIR" fetch --quiet origin 2>/dev/null
            if git -C "$LATEX_EXT_DIR" checkout --quiet "$LATEX_PIN" 2>/dev/null; then
                ok "latex-document-skill @ $LATEX_PIN"
            else
                warn "latex-document-skill checkout $LATEX_PIN failed — staying at $CURRENT"
            fi
        else
            ok "latex-document-skill @ $LATEX_PIN (up to date)"
        fi
    fi
fi

if [[ -f "$LATEX_EXT_DIR/SKILL.md" ]]; then
    if [[ ! -f "$LATEX_SKILL_LINK" ]]; then
        ln -sf "$LATEX_EXT_DIR/SKILL.md" "$LATEX_SKILL_LINK"
        info "Skill linked: latex.md"
    fi
    ok "/latex skill available"
fi

# ─── [6/7] Symlinks ────────────────────────────────────────────────────────

echo "[6/7] Creating symlinks..."

for tool in bterminal ctx consult tasks claude_log memory_wizard; do
    src="$INSTALL_DIR/$tool"
    [[ "$tool" == "bterminal" ]] && src="$INSTALL_DIR/bterminal.py"
    ln -sf "$src" "$BIN_DIR/$tool"
done
ok "Symlinks in $BIN_DIR"

# ─── [7/7] Init ctx + desktop entry ────────────────────────────────────────

echo "[7/7] Finalizing..."

if [[ ! -f "$CTX_DIR/context.db" ]]; then
    "$BIN_DIR/ctx" list >/dev/null 2>&1 || true
    info "Created $CTX_DIR/context.db"
fi
"$BIN_DIR/ctx" shared set user_preferences \
    "At the start of each session, tell the user which model you are before beginning work." \
    2>/dev/null || true

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
ok "Desktop entry created"

# ─── Summary ───────────────────────────────────────────────────────────────

echo ""

# Write errors/warnings to JSON for bterminal startup check
ERRORS_JSON="[]"
WARNINGS_JSON="[]"
if [[ ${#ERRORS[@]} -gt 0 ]]; then
    ERRORS_JSON="$(python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" -- "${ERRORS[@]}")"
fi
if [[ ${#WARNINGS[@]} -gt 0 ]]; then
    WARNINGS_JSON="$(python3 -c "import json,sys; print(json.dumps(sys.argv[1:]))" -- "${WARNINGS[@]}")"
fi
python3 -c "
import json
data = {'bterminal_version': '$BTERMINAL_VERSION', 'errors': $ERRORS_JSON, 'warnings': $WARNINGS_JSON}
with open('$ERRORS_FILE', 'w') as f:
    json.dump(data, f, indent=2)
"

if [[ ${#WARNINGS[@]} -gt 0 ]]; then
    echo "Warnings (non-critical):"
    for w in "${WARNINGS[@]}"; do printf "  \033[33m⚠\033[0m %s\n" "$w"; done
    echo ""
fi

if [[ ${#ERRORS[@]} -gt 0 ]]; then
    echo "ERRORS — action required:"
    for e in "${ERRORS[@]}"; do printf "  \033[31m✗\033[0m %s\n" "$e"; done
    echo ""
    # Write clean error summary to stderr for GUI dialog
    {
        echo "BTerminal v${BTERMINAL_VERSION} — dependency errors:"
        for e in "${ERRORS[@]}"; do echo "  • $e"; done
    } >&2
    exit 1
fi

# Clean up backup — install succeeded
[[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] && rm -rf "$BACKUP_DIR"

echo "=== BTerminal v${BTERMINAL_VERSION} installed successfully ==="
echo ""
echo "Run:  bterminal"
echo "PATH: export PATH=\"\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH\""
