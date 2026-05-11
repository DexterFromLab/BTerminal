#!/bin/bash
set -euo pipefail

# BTerminal installer / updater
# Reads defaults/dependencies.json and enforces version requirements.
# Exit code 1 = critical dependency error (shown as dialog in GUI auto-update).
#
# ─── DOWNLOAD POLICY ──────────────────────────────────────────────────────
# Every network download performed by the installer (or by any sub-script
# we invoke, like ollama.com/install.sh) MUST show live progress to the
# user — % completed, transfer speed, ETA. Reasoning:
#   - Wizard runs in a GUI dialog; without progress lines users assume
#     the installer is hung and SIGTERM it mid-download (bug 2026-05-08).
#   - "Silent" curl flags (`-s`, `--silent`, `--progress-bar` alone)
#     hide download progress and ARE BANNED in this script.
#
# How to comply:
#   1. Direct curl: use `curl -fL <url>` (NO -s flag). Default progress
#      meter shows: `% Total / Average Speed / Time Total / Time Spent /
#      Time Left`. Pipe through `stdbuf -oL` so GUI wizard line-buffers
#      progress updates in real time.
#   2. wget: use `wget -nv --show-progress <url>`. Plain `wget` (no
#      flags) is also fine — its default already shows progress.
#   3. Sub-scripts that run their own curl (ollama install.sh): hook
#      `curl` via a PATH-injected wrapper that strips `--progress-bar`
#      so curl's default meter takes over. See `_install_curl_hook` and
#      `OLLAMA_DO_INSTALL` block below.
#   4. Long-running phases that don't have an obvious progress source
#      (e.g. apt-get under a sudo prompt) MUST emit a heartbeat line at
#      least every 30 s so the user sees the wizard isn't frozen.
# ───────────────────────────────────────────────────────────────────────────

SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
INSTALL_DIR="$HOME/.local/share/bterminal"
BIN_DIR="$HOME/.local/bin"
CONFIG_DIR="$HOME/.config/bterminal"
CTX_DIR="$HOME/.claude-context"
ICON_DIR="$HOME/.local/share/icons/hicolor/scalable/apps"
DESKTOP_DIR="$HOME/.local/share/applications"
DEPS_JSON="$SCRIPT_DIR/defaults/dependencies.json"
ERRORS_FILE="$CONFIG_DIR/install_errors.json"

# Task #4 (#76 in audit doc): expanded CLI for GTK installer wizard
# orchestration. Behaviour matrix:
#   default                   — interactive, all 'auto'-tier deps installed
#   --no-sudo                 — skip apt-needed installs (CI fallback)
#   --headless                — marker for "GUI orchestrator runs me"
#                               (no shell prompts; reserved for future
#                               interactive checkpoints — currently
#                               install.sh has none, so this is a no-op
#                               beyond signalling intent in [SUMMARY])
#   --selected meld,llama     — CSV whitelist; 'auto'-tier deps NOT in
#                               the list are skipped even if missing.
#                               Empty / unset = legacy behaviour (try
#                               every 'auto' dep). Special tokens:
#                               'llama' → install ollama via curl
#                                         (audit § 5 Local LLM)
#   --status-json             — emit one-JSON-per-line on stdout for
#                               every phase boundary so the wizard can
#                               parse progress without ANSI cruft. Each
#                               line: {"phase": "claude", "status":
#                               "installing|ok|missing|failed",
#                               "progress": 0-100}.
NO_SUDO=false
HEADLESS=false
SELECTED_DEPS=""
STATUS_JSON=false
ACTION="install"   # install | uninstall | fix
PURGE=false        # only relevant for --uninstall (also drop user data)

while [[ $# -gt 0 ]]; do
    case "$1" in
        --no-sudo)     NO_SUDO=true; shift ;;
        --headless)    HEADLESS=true; shift ;;
        --selected)    SELECTED_DEPS="${2:-}"; shift 2 ;;
        --selected=*)  SELECTED_DEPS="${1#*=}"; shift ;;
        --status-json) STATUS_JSON=true; shift ;;
        --uninstall)   ACTION="uninstall"; shift ;;
        --fix)         ACTION="fix"; shift ;;
        --purge)       PURGE=true; shift ;;
        --help|-h)
            cat <<EOF
Usage: install.sh [options]

Actions (mutually exclusive):
  (default)            Install BTerminal + dependencies
  --uninstall          Remove BTerminal symlinks, files, desktop entry,
                       and CLI bins (claude, copilot). User configs in
                       ~/.config/bterminal and ~/.claude-context kept.
  --uninstall --purge  Same as --uninstall, plus delete configs +
                       sessions DB + ctx DB.
  --fix                Repair broken install: re-run validation on every
                       installed CLI, re-create symlinks, refresh
                       desktop entry, leave user data alone.

Options:
  --no-sudo            Skip apt installs (use existing system packages)
  --headless           Marker for non-interactive runs (GTK orchestrator)
  --selected csv       Whitelist for 'auto'-tier deps (e.g. meld,llama,latex).
                       Tokens NOT in this list are skipped even if missing.
                       Special token 'llama' triggers ollama install.
                       Use 'none' (literal) to skip ALL auto-tier deps.
  --status-json        Emit JSON status lines on stdout (one per phase).
EOF
            exit 0
            ;;
        *)
            echo "Unknown argument: $1 (use --help)" >&2
            exit 2
            ;;
    esac
done

# Helper: emit a single JSON status line when --status-json is set.
# Schema: {phase: <slug>, status: ok|missing|installing|failed,
#          progress: 0-100, label: human text}.
status_json() {  # status_json <phase> <status> <progress> <label>
    [[ "$STATUS_JSON" != true ]] && return 0
    python3 -c "
import json, sys
print(json.dumps({
    'phase':    '$1',
    'status':   '$2',
    'progress': int('$3'),
    'label':    '$4',
}))
"
}

# Helper: returns 0 if --selected is empty (= legacy behaviour, try all),
# OR if $1 is in the CSV. 'auto'-tier deps consult this before apt install.
# Special: --selected none (literal) skips ALL auto-tier deps.
selected_includes() {  # selected_includes <token>
    [[ -z "$SELECTED_DEPS" ]] && return 0
    [[ "$SELECTED_DEPS" == "none" ]] && return 1
    local IFS=','
    for sel in $SELECTED_DEPS; do
        [[ "$sel" == "$1" ]] && return 0
    done
    return 1
}

# ─── Helpers (must be defined BEFORE maybe_launch_gtk_wizard) ──────────────
#
# Bug 2026-05-08: ok/warn/fail/info were previously defined LATER
# (after the wizard auto-spawn block), so when wizard logic invoked
# `info "..."`, bash didn't find the function and fell back to
# /usr/bin/info (texinfo) which printed `Brak elementu menu '...'
# w węźle '(dir)Top'`. Fix: move helpers above maybe_launch_gtk_wizard
# so forward-references resolve correctly.

ERRORS=()
WARNINGS=()

# Components the installer couldn't put in place — collected as
# (name, fix-instruction) tuples flattened into a single array
# (alternating values: name, fix, name, fix, ...). Surfaced at the
# end as a "Manual install needed" block so the user knows exactly
# what to do; subsequent install.sh runs detect already-installed
# pieces and skip past them.
MANUAL_INSTALLS=()
add_manual_install() {  # add_manual_install <name> <command-to-run>
    MANUAL_INSTALLS+=("$1" "$2")
    log_line "MANUAL" "$1 → run manually: $2"
}

# Structured install log — every line timestamped, machine-readable
# tail-able by tests + GUI wizard. install_errors.json is the
# summary; this file is the running record.
INSTALL_LOG="$CONFIG_DIR/install.log"
mkdir -p "$CONFIG_DIR" 2>/dev/null || true
log_line() {  # log_line <level> <message>
    local ts
    ts="$(date -u +%FT%TZ)"
    printf '%s [%s] %s\n' "$ts" "$1" "$2" >> "$INSTALL_LOG"
}

ok()   { printf "  \033[32m✓\033[0m %s\n" "$*"; log_line "OK"   "$*"; }
warn() { printf "  \033[33m⚠\033[0m %s\n" "$*"; WARNINGS+=("$*"); log_line "WARN" "$*"; }
fail() { printf "  \033[31m✗\033[0m %s\n" "$*"; ERRORS+=("$*");  log_line "FAIL" "$*"; }
info() { printf "    %s\n" "$*";                                  log_line "INFO" "$*"; }

# ─── Diagnostic capture (for bug reports) ──────────────────────────────────
#
# At the start of every run, write a `[DIAG]` block to install.log
# with OS / hardware / runtime versions. Also save a per-run log
# under ~/.config/bterminal/install-runs/ so users can attach the
# raw subprocess output to bug reports without re-running the whole
# install.

# Per-run log: tee everything to a timestamped file. Sourced from
# stdin via process substitution would lose set -e safety, so we
# just pin the path — wizard's subprocess wrapper teex into here.
RUN_LOG_DIR="$CONFIG_DIR/install-runs"
mkdir -p "$RUN_LOG_DIR" 2>/dev/null || true
RUN_LOG_FILE="$RUN_LOG_DIR/run-$(date -u +%Y%m%d-%H%M%S).log"
log_line "RUN_START" "Run log: $RUN_LOG_FILE"

# Diagnostic block — captured ONCE at the start of every run.
# Used by `--diag-bundle` (next step) to include OS/hardware in
# bug-report tar.gz files.
{
    log_line "DIAG" "BTerminal version: $(cat "$SCRIPT_DIR/VERSION" 2>/dev/null || echo 'unknown')"
    log_line "DIAG" "Action: ${ACTION:-install}  (HEADLESS=$HEADLESS  NO_SUDO=$NO_SUDO  STATUS_JSON=$STATUS_JSON)"
    log_line "DIAG" "Selected: ${SELECTED_DEPS:-(empty)}"
    if [[ -r /etc/os-release ]]; then
        # PRETTY_NAME is the human-friendly distro string
        DIAG_PRETTY="$(grep -E '^PRETTY_NAME=' /etc/os-release | head -1 | sed 's/^PRETTY_NAME=//;s/^"//;s/"$//')"
        log_line "DIAG" "OS: ${DIAG_PRETTY:-unknown}"
        unset DIAG_PRETTY
    fi
    log_line "DIAG" "Kernel: $(uname -srm 2>/dev/null || echo 'unknown')"
    log_line "DIAG" "Architecture: $(uname -m 2>/dev/null || echo 'unknown')"
    log_line "DIAG" "Locale: ${LANG:-${LC_ALL:-unknown}}"
    log_line "DIAG" "Display: ${DISPLAY:-unset}  Wayland: ${WAYLAND_DISPLAY:-unset}"
    log_line "DIAG" "Shell: $0  PID: $$  PPID: $PPID"
    log_line "DIAG" "User: $(whoami)  Home: $HOME"
    # Force LANG=C so `free -h` / `df -h` output is in English —
    # otherwise pl_PL/de_DE/etc. localize "Mem:" → "pamięć:"/"Speicher:"
    # and our awk pattern misses it.
    DIAG_MEM="$(LANG=C free -h 2>/dev/null \
        | awk '/^Mem:/ {print $2 " total, " $7 " available"}' \
        || true)"
    log_line "DIAG" "Memory: ${DIAG_MEM:-(free -h failed)}"
    DIAG_DF="$(LANG=C df -h "$HOME" 2>/dev/null \
        | tail -1 | awk '{print $4 " available on " $1}' \
        || true)"
    log_line "DIAG" "Disk free: ${DIAG_DF:-(df -h failed)}"
    unset DIAG_MEM DIAG_DF
} 2>/dev/null || true

# ─── Uninstall / Fix actions ───────────────────────────────────────────────
#
# Both run BEFORE the wizard auto-spawn check so they're usable
# headlessly without a GUI.

# Items the installer creates (single source of truth — uninstall +
# fix consult the same list).
INSTALL_DIR="${INSTALL_DIR:-$HOME/.local/share/bterminal}"
BIN_DIR="${BIN_DIR:-$HOME/.local/bin}"
DESKTOP_DIR="${DESKTOP_DIR:-$HOME/.local/share/applications}"
ICON_DIR="${ICON_DIR:-$HOME/.local/share/icons/hicolor/scalable/apps}"
CTX_DIR="${CTX_DIR:-$HOME/.claude-context}"
NPM_PREFIX="${NPM_PREFIX:-$HOME/.npm-global}"

# Symlinks/launchers in BIN_DIR created by install.sh
_BT_BIN_SYMLINKS=(bterminal ctx tasks consult memory_wizard claude_log claude copilot aider)

# Forward declarations of binary lookup helpers — needed by do_fix
# which runs before phase [2/7] (where the originals are defined).
# Duplicating definitions is harmless in bash (later definition wins
# — they're identical so it doesn't matter).
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
find_claude_bin_loose() {
    local candidates=(
        "$HOME/.npm-global/bin/claude"
        "/usr/local/bin/claude"
        "/usr/bin/claude"
        "/opt/homebrew/bin/claude"
    )
    for p in "${candidates[@]}"; do [[ -e "$p" ]] && { echo "$p"; return 0; }; done
    for p in "$HOME"/.nvm/versions/node/*/bin/claude; do [[ -e "$p" ]] && { echo "$p"; return 0; }; done
    [[ -e "$BIN_DIR/claude" ]] && { echo "$BIN_DIR/claude"; return 0; }
    return 0
}
find_copilot_bin() {
    local candidates=(
        "$HOME/.npm-global/bin/copilot"
        "/usr/local/bin/copilot"
        "/usr/bin/copilot"
        "/opt/homebrew/bin/copilot"
    )
    for p in "${candidates[@]}"; do [[ -x "$p" ]] && { echo "$p"; return; }; done
    for p in "$HOME"/.nvm/versions/node/*/bin/copilot; do [[ -x "$p" ]] && { echo "$p"; return; }; done
    [[ -x "$BIN_DIR/copilot" ]] && { echo "$BIN_DIR/copilot"; return; }
    command -v copilot 2>/dev/null || true
}
find_copilot_bin_loose() {
    local candidates=(
        "$HOME/.npm-global/bin/copilot"
        "/usr/local/bin/copilot"
        "/usr/bin/copilot"
        "/opt/homebrew/bin/copilot"
    )
    for p in "${candidates[@]}"; do [[ -e "$p" ]] && { echo "$p"; return 0; }; done
    for p in "$HOME"/.nvm/versions/node/*/bin/copilot; do [[ -e "$p" ]] && { echo "$p"; return 0; }; done
    [[ -e "$BIN_DIR/copilot" ]] && { echo "$BIN_DIR/copilot"; return 0; }
    return 0
}

# Forward declarations: validate_npm_cli + validate_explain — needed by
# do_fix() (line ~422). Original definition is later in the script
# (after [2/7] phase) but bash's late-binding only works for functions
# defined BEFORE the call site at execution time. do_fix runs early
# (case statement line ~551), so these forwards are required.
# Bug 2026-05-08: scenarios (b)/(c)/(d)/(e) of #143 were silently
# failing because do_fix hit `validate_npm_cli: command not found`
# at line 504 when fixing AI provider symlinks.
validate_npm_cli() {
    # #111: every external probe below (readlink, head, stat, the binary
    # itself) inherits this shell's cwd via fork(). When wizard spawns
    # install.sh --fix from cwd=$INSTALL_DIR and a later phase renames
    # INSTALL_DIR aside, the next subprocess fork prints "current working
    # directory was deleted" before --version even runs. cd is a bash
    # builtin so it works even when cwd is dangling.
    cd /tmp 2>/dev/null || cd /
    local bin_path="$1"
    local friendly="$2"
    if [[ -z "$bin_path" ]]; then
        log_line "VALIDATE" "$friendly: not found in any PATH location" 2>/dev/null || true
        return 1
    fi
    local real_path
    real_path="$(readlink -f "$bin_path" 2>/dev/null || echo "$bin_path")"
    if [[ ! -e "$real_path" ]]; then
        log_line "VALIDATE" "$friendly: symlink dangling — target $real_path missing" 2>/dev/null || true
        return 1
    fi
    if [[ ! -x "$real_path" ]]; then
        log_line "VALIDATE" "$friendly: file present but not executable" 2>/dev/null || true
        return 2
    fi
    if head -c 512 "$real_path" 2>/dev/null | grep -qE \
        'native binary not installed|postinstall did not run|optional dependency was not downloaded'; then
        log_line "VALIDATE" "$friendly: stub detected at $real_path" 2>/dev/null || true
        return 3
    fi
    local ver_out
    ver_out="$("$bin_path" --version 2>&1 </dev/null || true)"
    if ! "$bin_path" --version >/dev/null 2>&1 </dev/null; then
        log_line "VALIDATE" "$friendly: --version errored, output=$(echo "$ver_out" | head -c 200)" 2>/dev/null || true
        return 4
    fi
    if [[ -z "$ver_out" ]]; then
        log_line "VALIDATE" "$friendly: --version returned empty output" 2>/dev/null || true
        return 4
    fi
    log_line "VALIDATE" "$friendly: OK ($(echo "$ver_out" | head -1))" 2>/dev/null || true
    echo "$ver_out" | head -1
    return 0
}

validate_explain() {
    case "$1" in
        1) echo "binary not found in any expected location" ;;
        2) echo "binary present but not executable (chmod +x missing — broken npm install)" ;;
        3) echo "stub binary detected (npm postinstall didn't fetch native CLI)" ;;
        4) echo "binary exists but '--version' failed (corrupted install)" ;;
        *) echo "unknown validation failure" ;;
    esac
}

do_uninstall() {
    echo "=== BTerminal uninstall ==="
    echo ""
    status_json uninstall installing 5 "Starting uninstall"

    info "Removing CLI symlinks from $BIN_DIR..."
    status_json uninstall installing 15 "Removing CLI symlinks"
    local removed=0
    for s in "${_BT_BIN_SYMLINKS[@]}"; do
        if [[ -L "$BIN_DIR/$s" || -f "$BIN_DIR/$s" ]]; then
            rm -f "$BIN_DIR/$s" && removed=$((removed + 1))
        fi
    done
    ok "$removed CLI symlinks removed"

    info "Removing $INSTALL_DIR..."
    status_json uninstall installing 35 "Removing BTerminal files"
    if [[ -d "$INSTALL_DIR" ]]; then
        rm -rf "$INSTALL_DIR"
        ok "BTerminal files removed"
    else
        info "  (already absent)"
    fi

    info "Removing desktop entry + icon..."
    status_json uninstall installing 50 "Removing desktop integration"
    rm -f "$DESKTOP_DIR/bterminal.desktop" "$DESKTOP_DIR/bterminal-installer.desktop"
    rm -f "$ICON_DIR/bterminal.svg" "$ICON_DIR/bterminal.png"
    # Also remove the file from the user's actual Desktop folder
    # (where shortcuts live in file managers). XDG locale-aware
    # name varies: Desktop / Pulpit (pl) / Bureau (fr) /
    # Schreibtisch (de) / 桌面 (zh) etc.
    DESKTOP_USER_DIR=""
    if command -v xdg-user-dir >/dev/null 2>&1; then
        DESKTOP_USER_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
    fi
    # Fallback list (covers most locales and the case where
    # xdg-user-dir is missing).
    for d in "$DESKTOP_USER_DIR" \
             "$HOME/Desktop" "$HOME/Pulpit" "$HOME/Bureau" \
             "$HOME/Schreibtisch" "$HOME/Escritorio" "$HOME/桌面"; do
        if [[ -n "$d" && -d "$d" && -f "$d/bterminal.desktop" ]]; then
            rm -f "$d/bterminal.desktop"
            info "  Removed shortcut at $d/bterminal.desktop"
        fi
    done
    ok "Desktop integration removed"

    # Optionally remove npm-installed CLI (claude, copilot)
    status_json uninstall installing 65 "Removing AI CLIs (claude, copilot)"
    if [[ -d "$NPM_PREFIX/lib/node_modules/@anthropic-ai" ]]; then
        info "Removing npm-installed Claude Code..."
        rm -rf "$NPM_PREFIX/lib/node_modules/@anthropic-ai"
        rm -f "$NPM_PREFIX/bin/claude"
        ok "Claude Code (npm) removed"
    fi
    if [[ -d "$NPM_PREFIX/lib/node_modules/@github" ]]; then
        info "Removing npm-installed Copilot CLI..."
        rm -rf "$NPM_PREFIX/lib/node_modules/@github"
        rm -f "$NPM_PREFIX/bin/copilot"
        ok "Copilot CLI (npm) removed"
    fi

    # Uninstall Aider (pipx venv or pip --user). Best-effort.
    if command -v pipx >/dev/null 2>&1 \
            && pipx list 2>/dev/null | grep -q "aider-chat"; then
        info "Removing pipx-installed Aider..."
        pipx uninstall aider-chat 2>/dev/null || true
        ok "Aider (pipx) removed"
    elif command -v pip >/dev/null 2>&1 \
            && pip show aider-chat >/dev/null 2>&1; then
        info "Removing pip-installed Aider..."
        pip uninstall -y aider-chat 2>/dev/null || true
        ok "Aider (pip) removed"
    fi
    rm -f "$BIN_DIR/aider" 2>/dev/null  # remove our launcher symlink

    status_json uninstall installing 85 "Cleaning up user data"
    if [[ "$PURGE" == true ]]; then
        info "PURGE mode — removing user configs + databases..."
        # Once we purge $CONFIG_DIR, every subsequent log_line append
        # would hit a missing directory and trip `set -e`. Redirect
        # the install log to /dev/null AFTER capturing the final
        # "purge complete" line in /tmp so the bug-report bundle
        # still has a record.
        FINAL_LOG_TMP="$(mktemp -t bterminal-uninstall-final.XXXXXX.log)"
        # Append the current install.log to the tmp file before purge
        if [[ -f "$INSTALL_LOG" ]]; then
            cp -f "$INSTALL_LOG" "$FINAL_LOG_TMP" 2>/dev/null || true
        fi
        rm -rf "$CONFIG_DIR" "$CTX_DIR"
        # Switch log destination so the rest of do_uninstall doesn't
        # crash trying to append into a deleted directory.
        INSTALL_LOG="$FINAL_LOG_TMP"
        ok "Configs + ctx DB + sessions removed"
        info "Final uninstall log saved to: $FINAL_LOG_TMP"
    else
        info "User data preserved at:"
        info "  $CONFIG_DIR  (sessions, options, install logs)"
        info "  $CTX_DIR  (ctx + tasks DB)"
        info "  Use --uninstall --purge to also remove these."
    fi

    info "Note: Ollama (if installed) is NOT removed automatically;"
    info "      uninstall it with: sudo rm -rf /usr/local/lib/ollama /usr/local/bin/ollama"
    info "      and: sudo systemctl disable --now ollama (if systemd unit exists)"

    echo ""
    echo "=== BTerminal uninstall completed ==="
    # Final status_json marks the wizard's progress bar as 100%
    # AND triggers the "_final_status_seen" flag so summary page
    # shows "Uninstall finished." rather than "partial uninstall".
    status_json done ok 100 "Uninstall completed"
    # Also write the marker via log_line so automated tests can
    # grep `install.log` (or the /tmp fallback after --purge) for
    # success regardless of whether stdout was captured.
    log_line "OK" "Uninstall completed"
    exit 0
}

do_fix() {
    # #111: wizard may spawn install.sh --fix with cwd=$INSTALL_DIR,
    # which gets temporarily renamed during the repair flow (BACKUP_DIR
    # rotation if we fall through to full install). Move to a stable cwd
    # so every external probe below inherits a valid one via fork().
    cd /tmp 2>/dev/null || cd /
    echo "=== BTerminal fix (repair broken install) ==="
    echo ""
    status_json fix installing 5 "Starting repair"
    info "Strategy: detect every break scenario from #143, restore"
    info "  what's recoverable in-place, fall through to full install"
    info "  for anything that needs reinstallation. User data preserved."
    echo ""

    local need_full_install=false
    local fixed_count=0
    fix_log() {  # fix_log <message>
        log_line "FIX" "$1"
        info "  [FIX] $1"
        fixed_count=$((fixed_count + 1))
    }

    # Scenario (d): stale install.lock — wipe immediately so a
    # subsequent install path doesn't trip on it. install.sh's own
    # stale-lock recovery at startup handles this too, but doing it
    # here (with a [FIX] log entry) makes the action visible.
    status_json fix installing 10 "Checking install lockfile"
    if [[ -f "$CONFIG_DIR/install.lock" ]]; then
        local lock_pid
        lock_pid="$(cat "$CONFIG_DIR/install.lock" 2>/dev/null \
            | head -1 | tr -dc '0-9')"
        if [[ -n "$lock_pid" ]] && ! kill -0 "$lock_pid" 2>/dev/null; then
            rm -f "$CONFIG_DIR/install.lock"
            fix_log "Removed stale install.lock (dead PID $lock_pid)"
        fi
    fi

    # Scenario (b): bterminal/__init__.py missing → full reinstall
    status_json fix installing 20 "Checking BTerminal package files"
    if [[ ! -f "$INSTALL_DIR/bterminal/__init__.py" ]]; then
        fix_log "BTerminal package __init__.py missing — full reinstall scheduled"
        need_full_install=true
    else
        ok "BTerminal package present at $INSTALL_DIR/bterminal/"
    fi

    # Scenario (a): launcher missing → full reinstall (or relink if
    # source script still exists)
    status_json fix installing 35 "Checking launcher symlink"
    if [[ ! -L "$BIN_DIR/bterminal" && ! -e "$BIN_DIR/bterminal" ]]; then
        if [[ -f "$INSTALL_DIR/bterminal-launcher" ]]; then
            ln -sf "$INSTALL_DIR/bterminal-launcher" "$BIN_DIR/bterminal"
            fix_log "Restored launcher symlink: $BIN_DIR/bterminal → $INSTALL_DIR/bterminal-launcher"
        else
            fix_log "Launcher source missing — full reinstall scheduled"
            need_full_install=true
        fi
    else
        ok "Launcher symlink intact"
    fi

    # Scenario (e): companion CLI symlinks (ctx/tasks/consult/...)
    # — relink if source file exists, otherwise schedule reinstall.
    status_json fix installing 50 "Checking companion CLI symlinks"
    for tool in ctx tasks consult memory_wizard claude_log; do
        if [[ ! -L "$BIN_DIR/$tool" && ! -e "$BIN_DIR/$tool" ]]; then
            if [[ -f "$INSTALL_DIR/$tool" ]]; then
                ln -sf "$INSTALL_DIR/$tool" "$BIN_DIR/$tool"
                fix_log "Restored symlink: $BIN_DIR/$tool"
            else
                fix_log "$tool source missing — full reinstall scheduled"
                need_full_install=true
            fi
        fi
    done

    # Scenario (c): AI CLI binary is a stub (no +x or stub markers).
    # Re-validate; if broken, schedule install path to redo npm install.
    status_json fix installing 70 "Re-validating AI provider CLIs"
    EXISTING_CLAUDE="$(find_claude_bin)"
    if [[ -z "$EXISTING_CLAUDE" ]]; then
        EXISTING_CLAUDE="$(find_claude_bin_loose)"
    fi
    if [[ -n "$EXISTING_CLAUDE" ]]; then
        # Capture rc BEFORE the `if !` flip — `$?` after `if !` is
        # always 0 (1 inverted to 0 by !) so validate_explain
        # received empty rc and printed nothing.
        validate_npm_cli "$EXISTING_CLAUDE" "Claude Code" >/dev/null
        local cl_rc=$?
        if [[ $cl_rc -ne 0 ]]; then
            fix_log "Claude Code broken ($(validate_explain $cl_rc)) — full reinstall scheduled"
            CLAUDE_NEEDS_INSTALL=true
            need_full_install=true
        else
            ok "Claude Code post-fix validation: OK"
        fi
    fi
    EXISTING_COPILOT="$(find_copilot_bin)"
    if [[ -z "$EXISTING_COPILOT" ]]; then
        EXISTING_COPILOT="$(find_copilot_bin_loose)"
    fi
    if [[ -n "$EXISTING_COPILOT" ]]; then
        validate_npm_cli "$EXISTING_COPILOT" "Copilot CLI" >/dev/null
        local cp_rc=$?
        if [[ $cp_rc -ne 0 ]]; then
            fix_log "Copilot CLI broken ($(validate_explain $cp_rc)) — full reinstall scheduled"
            COPILOT_NEEDS_INSTALL=true
            need_full_install=true
        else
            ok "Copilot CLI post-fix validation: OK"
        fi
    fi

    if [[ "$need_full_install" == true ]]; then
        warn "$fixed_count in-place fix(es) applied; falling through to full"
        warn "  install path to restore missing/broken components."
        echo ""
        # Fall through to the normal install path. ACTION→install
        # makes the rest of install.sh run as if it was a normal
        # install (idempotent on already-installed pieces).
        ACTION="install"
        return 0
    fi

    echo ""
    echo "=== BTerminal repair completed ($fixed_count fix(es) applied) ==="
    status_json done ok 100 "Repair completed"
    log_line "OK" "Repair completed ($fixed_count fix(es))"
    exit 0
}

# Dispatch action — uninstall exits, fix falls through to install loop
case "$ACTION" in
    uninstall) do_uninstall ;;
    fix)       do_fix ;;
esac

# Task #6 (#78 in audit doc): GTK auto-spawn detection. When the
# user runs `./install.sh` from a graphical desktop session, prefer
# the InstallerWizard GUI (5 pages, log streaming, progress bar) over
# the bash flow. Skip auto-spawn when:
#   - --headless     explicit user opt-out (CI / scripted)
#   - --no-sudo      offline mode, GUI assumes apt available
#   - --status-json  caller is the wizard itself (recursion guard)
#   - $DISPLAY unset (SSH without X-forwarding, headless server)
#   - python3-gi     missing (no GTK bindings = wizard can't render)
#
# bterminal/ui/installer_wizard.py runs straight from the cloned tree
# (sys.path = SCRIPT_DIR), so the wizard works pre-install. The
# wizard then re-invokes THIS script with --headless --selected csv
# --status-json to do the actual work — avoids a chicken/egg
# bootstrap dance.
maybe_launch_gtk_wizard() {
    [[ "$HEADLESS" == true ]]    && return 0
    [[ "$STATUS_JSON" == true ]] && return 0  # wizard already running
    [[ "$NO_SUDO" == true ]]     && return 0  # bash mode is fine offline
    [[ -z "${DISPLAY:-}${WAYLAND_DISPLAY:-}" ]] && return 0
    command -v python3 &>/dev/null || return 0
    if ! python3 -c "import gi; gi.require_version('Gtk','3.0')" \
            >/dev/null 2>&1; then
        return 0
    fi

    info "GTK desktop session detected — launching graphical installer"
    info "(use --headless to force the bash flow)."
    BTERMINAL_REPO_DIR="$SCRIPT_DIR" \
        PYTHONPATH="$SCRIPT_DIR${PYTHONPATH:+:$PYTHONPATH}" \
        python3 -m bterminal --installer
    local rc=$?
    if [[ $rc -eq 0 ]]; then
        # Wizard finished successfully (it already ran install.sh
        # internally with the user's selections).
        echo ""
        echo "=== InstallerWizard finished — install completed ==="
        exit 0
    fi
    info "Wizard cancelled or unavailable — falling back to bash flow."
    return 0
}

maybe_launch_gtk_wizard

# #110 (audit § 6.2 #11): only one install.sh may run at a time per
# user. Two parallel runs collide on BACKUP_DIR / file copies / npm
# globals, so we acquire an advisory lock here. Acquired AFTER the
# wizard short-circuit so the wizard's internal `install.sh` spawn
# (in a different process group) sees no contention.
#
# The lock is per-user (HOME-scoped) — multiple users on the same
# host can install in parallel without interference. Released
# automatically when this process exits via the FD 9 close.
INSTALL_LOCKFILE="${INSTALL_LOCKFILE:-$HOME/.config/bterminal/install.lock}"
mkdir -p "$(dirname "$INSTALL_LOCKFILE")"

# Stale-lock auto-recovery (2026-05-08): when a previous install.sh
# died abnormally (SIGKILL, kernel panic, system shutdown mid-run),
# flock on the kernel-tracked FD is released — but the lockfile
# still contains a PID. flock -n itself is enough to gate concurrent
# runs, so we just verify the recorded PID is still alive and
# offer a clear diagnostic instead of an opaque "exit 7".
if [[ -f "$INSTALL_LOCKFILE" && -s "$INSTALL_LOCKFILE" ]]; then
    LOCK_PID="$(cat "$INSTALL_LOCKFILE" 2>/dev/null | head -1 | tr -dc '0-9')"
    if [[ -n "$LOCK_PID" ]] && ! kill -0 "$LOCK_PID" 2>/dev/null; then
        # PID dead — wipe stale lockfile so flock starts clean.
        rm -f "$INSTALL_LOCKFILE"
    fi
fi

exec 9>"$INSTALL_LOCKFILE"
if ! flock -n 9; then
    LOCK_HOLDER="$(cat "$INSTALL_LOCKFILE" 2>/dev/null | head -1 || true)"
    echo ""
    printf "  \033[31m✗\033[0m Another install.sh is already running.\n"
    printf "    Lock: %s\n" "$INSTALL_LOCKFILE"
    if [[ -n "$LOCK_HOLDER" ]] && kill -0 "$LOCK_HOLDER" 2>/dev/null; then
        printf "    Active install.sh PID: %s\n" "$LOCK_HOLDER"
        printf "    Wait for it to finish, or kill PID %s and re-run.\n" \
            "$LOCK_HOLDER"
    else
        printf "    The recorded holder process is gone but flock\n"
        printf "    is still held — try: rm %s && re-run installer.\n" \
            "$INSTALL_LOCKFILE"
    fi
    echo "BTERMINAL_INSTALL_LOCKED" >&2
    exit 7
fi
# Stash PID into the lockfile so a sysadmin can identify the holder
# AND so the next run can verify whether the holder is still alive.
echo "$$" > "$INSTALL_LOCKFILE"

# Remove the lockfile on clean exit so post-install state doesn't
# look "broken" to detect_install_state(). flock releases the lock
# automatically when FD 9 closes (process exit), but the file
# itself sticks around — and the file alone (with a now-dead PID
# on next run) is what detect_install_state classifies as broken.
# Trap EXIT covers normal exit AND signal-induced cleanup; the
# trap from #105 (`_on_interrupt` for SIGINT/SIGTERM) is preserved.
trap 'rm -f "$INSTALL_LOCKFILE" 2>/dev/null || true' EXIT

BTERMINAL_VERSION="$(cat "$SCRIPT_DIR/VERSION" 2>/dev/null || echo 'unknown')"

echo "=== BTerminal Installer v${BTERMINAL_VERSION} ==="
echo ""

# ─── Validation helpers (CLI binary sanity check) ──────────────────────────
# (ok/warn/fail/info/log_line/ERRORS/WARNINGS now defined earlier — before
# maybe_launch_gtk_wizard — to avoid forward-reference fall through to
# /usr/bin/info texinfo binary on systems where it's installed.)

# validate_npm_cli — sanity check after `npm install -g <pkg>`.
# Catches the npm postinstall failure modes that leave a non-functional
# binary on disk (the GitHub bug 2026-05-08: claude.exe stub bez +x bit).
#
# Args: $1 = bin path (full or empty), $2 = friendly name (e.g. "Claude Code")
# Echoes: validated version on stdout when OK; nothing on failure.
# Returns: 0 if usable, 1 if missing, 2 if not executable (chmod missing),
#          3 if stub (fake binary placeholder), 4 if --version errored.
validate_npm_cli() {
    # #111: see forward declaration above — neutralize a possibly-deleted
    # cwd before any subprocess fork.
    cd /tmp 2>/dev/null || cd /
    local bin_path="$1"
    local friendly="$2"
    if [[ -z "$bin_path" ]]; then
        log_line "VALIDATE" "$friendly: not found in any PATH location"
        return 1
    fi
    # Resolve symlink so the chmod / file-content checks operate on
    # the real file (npm uses .exe symlinks in some package layouts).
    local real_path
    real_path="$(readlink -f "$bin_path" 2>/dev/null || echo "$bin_path")"
    if [[ ! -e "$real_path" ]]; then
        log_line "VALIDATE" "$friendly: symlink dangling — target $real_path missing"
        return 1
    fi
    if [[ ! -x "$real_path" ]]; then
        log_line "VALIDATE" "$friendly: file present but not executable ($real_path mode=$(stat -c %a "$real_path" 2>/dev/null))"
        return 2
    fi
    # Stub detection: npm postinstall sometimes leaves a placeholder
    # script that just prints an error message instead of pulling the
    # native binary. Pattern observed: bin/claude.exe contains literal
    # `echo "Error: claude native binary not installed."` as line 1.
    if head -c 512 "$real_path" 2>/dev/null | grep -qE \
        'native binary not installed|postinstall did not run|optional dependency was not downloaded'; then
        log_line "VALIDATE" "$friendly: stub detected at $real_path (npm postinstall didn't fetch native binary)"
        return 3
    fi
    # Final functional check: --version must exit 0 with non-empty
    # output. Some CLIs print to stderr — capture both.
    local ver_out ver_rc
    ver_out="$("$bin_path" --version 2>&1 </dev/null || true)"
    ver_rc="${PIPESTATUS[0]}"
    # Re-run for actual exit code (bash quirk with command substitution)
    if ! "$bin_path" --version >/dev/null 2>&1 </dev/null; then
        log_line "VALIDATE" "$friendly: --version errored, output=$(echo "$ver_out" | head -c 200)"
        return 4
    fi
    if [[ -z "$ver_out" ]]; then
        log_line "VALIDATE" "$friendly: --version returned empty output"
        return 4
    fi
    log_line "VALIDATE" "$friendly: OK ($(echo "$ver_out" | head -1))"
    echo "$ver_out" | head -1
    return 0
}

# Helper: format validate_npm_cli return code into a user-message
# fragment, so install.sh can print actionable diagnostics rather
# than a generic "install failed".
validate_explain() {
    case "$1" in
        1) echo "binary not found in any expected location" ;;
        2) echo "binary present but not executable (chmod +x missing — broken npm install)" ;;
        3) echo "stub binary detected (npm postinstall didn't fetch native CLI — try: npm install -g --include=optional --foreground-scripts)" ;;
        4) echo "binary exists but '--version' failed (corrupted install)" ;;
        *) echo "unknown validation failure" ;;
    esac
}

# ─── Rollback ──────────────────────────────────────────────────────────────

BACKUP_DIR=""
# CLI tools (flat at INSTALL_DIR root); the bterminal/ Python package is
# copied separately as a directory tree (see install loop below).
BTERMINAL_FILES=(ctx consult tasks claude_log memory_wizard aider_setup_wizard)

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

# #105 (audit § 6.1 #6): SIGINT / SIGTERM during install.sh now invoke
# the same rollback flow as ERR trap. Without this, a user pressing
# Ctrl-C mid-install left a partial state with no recovery message.
# Exit code 130 (128 + SIGINT) signals to the GUI installer that the
# user (not an error) interrupted; same BACKUP_DIR restoration logic.
_on_interrupt() {
    echo ""
    if [[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]]; then
        printf "  \033[33m⚠\033[0m Interrupted — restoring previous version...\n"
        for f in "${BTERMINAL_FILES[@]}"; do
            [[ -f "$BACKUP_DIR/$f" ]] && cp -f "$BACKUP_DIR/$f" "$INSTALL_DIR/$f" 2>/dev/null || true
        done
        rm -rf "$BACKUP_DIR"
        printf "  \033[32m✓\033[0m Previous version restored.\n"
        echo "BTERMINAL_INTERRUPT_ROLLBACK_OK" >&2
        printf "    Run ./install.sh again to resume.\n"
    else
        printf "  \033[31m✗\033[0m Interrupted before backup created — partial state.\n"
        # #112: phase [1/7] runs `mkdir -p "$INSTALL_DIR"` up-front (line
        # ~940), so a SIGTERM anywhere between that and the file-copy in
        # phase [5/7] (~line 1678) leaves an empty INSTALL_DIR turd. Walk
        # any empty subdirs first (extensions/, locale/, …) then rmdir
        # the parent. rmdir refuses non-empty dirs so a real partial
        # install (some files copied) is never destroyed by this path.
        if [[ -n "$INSTALL_DIR" && -d "$INSTALL_DIR" ]]; then
            find "$INSTALL_DIR" -mindepth 1 -type d -empty -delete 2>/dev/null || true
            if rmdir "$INSTALL_DIR" 2>/dev/null; then
                echo "BTERMINAL_INTERRUPT_CLEANED_INSTALL_DIR" >&2
            fi
        fi
        printf "    Run ./install.sh again to retry from clean.\n"
        echo "BTERMINAL_INTERRUPT_NO_BACKUP" >&2
    fi
    exit 130
}

trap '_on_error' ERR
trap '_on_interrupt' INT TERM

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
    # 2026-05-08 fix: sudo 1.9+ uses `tty_tickets` by default — cache
    # is bound to the parent TTY/process group, so `sudo -n` from a
    # subprocess spawned by the wizard does NOT see the wizard's
    # `sudo -v` cache. SUDO_ASKPASS solves this: install.sh runs
    # `sudo -A`, sudo invokes the script in $SUDO_ASKPASS to fetch
    # the password (the wizard prepared this script with the user's
    # password held in mode-0700 file under /tmp).
    # DOWNLOAD POLICY: apt-get's `-qq` flag suppresses download
    # progress, making the GUI wizard look frozen for minutes
    # while large packages (texlive-latex-extra ≈ 150 MB) pull.
    # Drop `-qq` so apt streams "Get:1 http://... [12.3 MB]" +
    # download % lines that the wizard's log view + progress
    # filter (_RX_PROGRESS_PCT) pick up in real time.
    #
    # Keep `-y` (don't prompt for confirmations — those would
    # hang anyway in a no-TTY subprocess).
    #
    # Wait for any lingering dpkg lock from the previous apt-get
    # call to release. Without this, back-to-back apt installs
    # can race on /var/lib/dpkg/lock-frontend even when the
    # parent apt-get returned (dpkg post-install scripts run
    # briefly after).
    local lock_wait=0
    while sudo -n fuser /var/lib/dpkg/lock-frontend >/dev/null 2>&1 \
            || sudo -n fuser /var/lib/dpkg/lock >/dev/null 2>&1; do
        ((lock_wait++ >= 30)) && break  # 30 s max
        sleep 1
    done

    # Run apt-get in background so we have a PID to supervise
    # for the heartbeat (apt without -qq still has long silent
    # dpkg phases).
    if [[ -n "${SUDO_ASKPASS:-}" && -x "$SUDO_ASKPASS" ]]; then
        sudo -A apt-get install -y "$@" &
    elif [[ -t 0 && -t 1 ]]; then
        sudo apt-get install -y "$@" &
    else
        sudo -n apt-get install -y "$@" &
    fi
    local apt_pid=$!
    local pkg_label="$1"
    # Heartbeat: pass PID + label as positional args to the subshell
    # so they're captured at fork time (passing via env var would
    # be evaluated lazy-late and miss the PID).
    bash -c '
        sleep 10
        while kill -0 "$1" 2>/dev/null; do
            printf "    ...still installing %s...\n" "$2"
            sleep 10
        done
    ' _ "$apt_pid" "$pkg_label" &
    local hb=$!
    local rc=0
    wait "$apt_pid"; rc=$?
    kill "$hb" 2>/dev/null; wait "$hb" 2>/dev/null
    return $rc
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

status_json runtime installing 5 "Checking runtime"
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
            curl -fL https://deb.nodesource.com/setup_22.x | sudo -n bash -
        fi
        sudo -n apt-get install -y nodejs
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

status_json claude installing 15 "Checking Claude Code CLI"
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

# Loose variant: returns first EXISTING (not necessarily +x) candidate.
# Used to detect stub binaries that strict `find_claude_bin` skips —
# the bug 2026-05-08 where claude.exe stub without +x was invisible
# to install.sh's existing-detection, so it was never overwritten by
# the next npm install.
find_claude_bin_loose() {
    local candidates=(
        "$HOME/.npm-global/bin/claude"
        "/usr/local/bin/claude"
        "/usr/bin/claude"
        "/opt/homebrew/bin/claude"
    )
    for p in "${candidates[@]}"; do [[ -e "$p" ]] && { echo "$p"; return 0; }; done
    for p in "$HOME"/.nvm/versions/node/*/bin/claude; do [[ -e "$p" ]] && { echo "$p"; return 0; }; done
    [[ -e "$BIN_DIR/claude" ]] && { echo "$BIN_DIR/claude"; return 0; }
    return 0
}

EXISTING_CLAUDE="$(find_claude_bin)"
# When strict find returns empty, look for a non-+x file in the same
# locations — could be a broken stub the validator should diagnose.
if [[ -z "$EXISTING_CLAUDE" ]]; then
    LOOSE_CLAUDE="$(find_claude_bin_loose)"
    if [[ -n "$LOOSE_CLAUDE" ]]; then
        log_line "DETECT" "Claude binary exists but isn't +x: $LOOSE_CLAUDE — running validator"
        EXISTING_CLAUDE="$LOOSE_CLAUDE"
    fi
fi
NPM_PREFIX="${HOME}/.npm-global"
mkdir -p "$NPM_PREFIX"

# Pre-validate any existing binary — find_claude_bin() uses [[ -x ]]
# but doesn't catch stub binaries (2026-05-08: claude.exe placeholder
# without +x AND with literal `Error: claude native binary not
# installed` content). validate_npm_cli does both checks.
CLAUDE_NEEDS_INSTALL=false
if [[ -n "$EXISTING_CLAUDE" ]]; then
    if CLAUDE_VER="$(validate_npm_cli "$EXISTING_CLAUDE" "Claude Code")"; then
        ok "claude $CLAUDE_VER ($EXISTING_CLAUDE)"
        info "Updating to latest..."
        npm config set prefix "$NPM_PREFIX" 2>/dev/null || true
        export PATH="$NPM_PREFIX/bin:$PATH"
        if npm install -g @anthropic-ai/claude-code --quiet 2>/dev/null; then
            EXISTING_CLAUDE="$(find_claude_bin)"
            if CLAUDE_VER_NEW="$(validate_npm_cli "$EXISTING_CLAUDE" "Claude Code")"; then
                [[ "$CLAUDE_VER_NEW" != "$CLAUDE_VER" ]] \
                    && info "Updated: $CLAUDE_VER → $CLAUDE_VER_NEW" \
                    || info "Already up to date"
            else
                rc=$?
                warn "Claude Code update validation failed — $(validate_explain $rc); kept previous $CLAUDE_VER"
            fi
        else
            warn "Claude Code update failed — continuing with $CLAUDE_VER"
        fi
    else
        rc=$?
        warn "Existing Claude binary at $EXISTING_CLAUDE is broken — $(validate_explain $rc); will reinstall"
        CLAUDE_NEEDS_INSTALL=true
    fi
else
    # npm install -g uses ~/.npm-global (user-level prefix) — no sudo
    # required. Old logic gated this behind NO_SUDO=false but that
    # was wrong: `--no-sudo` only means "no apt installs", AI CLIs
    # via npm still work fine.
    CLAUDE_NEEDS_INSTALL=true
fi

if [[ "$CLAUDE_NEEDS_INSTALL" == true ]]; then
    info "Installing Claude Code via npm..."
    info "  Anthropic's terminal-based AI coding agent. Powers the"
    info "  'Claude' provider in BTerminal — opens an interactive Claude"
    info "  session with your repo context. Requires Anthropic account."
    npm config set prefix "$NPM_PREFIX"
    export PATH="$NPM_PREFIX/bin:$PATH"
    # Clean broken stub before retry (npm reinstall doesn't always
    # overwrite a non-+x file from a previous failed postinstall).
    if [[ -e "$NPM_PREFIX/bin/claude" ]]; then
        rm -f "$NPM_PREFIX/bin/claude" 2>/dev/null || true
    fi
    if [[ -d "$NPM_PREFIX/lib/node_modules/@anthropic-ai" ]]; then
        rm -rf "$NPM_PREFIX/lib/node_modules/@anthropic-ai" 2>/dev/null || true
    fi
    # First attempt: standard install. If postinstall fails to pull
    # the native binary (the 2026-05-08 stub bug), retry with
    # --include=optional --foreground-scripts which forces the
    # platform-specific dep to be downloaded synchronously.
    if npm install -g @anthropic-ai/claude-code --quiet \
        || npm install -g @anthropic-ai/claude-code \
            --include=optional --foreground-scripts; then
        EXISTING_CLAUDE="$(find_claude_bin)"
        if CLAUDE_VER="$(validate_npm_cli "$EXISTING_CLAUDE" "Claude Code")"; then
            ok "claude $CLAUDE_VER (installed)"
        else
            rc=$?
            warn "Claude Code installation failed — $(validate_explain $rc)"
            add_manual_install "Claude Code" \
                "npm install -g @anthropic-ai/claude-code"
            EXISTING_CLAUDE=""
        fi
    else
        warn "Claude Code installation failed — see manual install below"
        add_manual_install "Claude Code" \
            "npm install -g @anthropic-ai/claude-code"
    fi
fi

# Stable symlink for GUI launches (desktop entry bypasses ~/.bashrc PATH)
if [[ -n "${EXISTING_CLAUDE:-}" && "$EXISTING_CLAUDE" != "$BIN_DIR/claude" ]]; then
    ln -sf "$EXISTING_CLAUDE" "$BIN_DIR/claude"
    info "Linked $BIN_DIR/claude -> $EXISTING_CLAUDE"
fi

# ─── [2.5/7] GitHub Copilot CLI ────────────────────────────────────────────
#
# Task #64 (2026-05-07): symmetric auto-install with Claude — install
# the binary unconditionally, let the CLI itself enforce /login on
# first launch (device-flow against github.com/login/device, same UX
# as Claude's first-run auth). Pre-task install was 'detection only',
# leaving users stuck mocking or installing manually.
#
# Active GitHub Copilot subscription (Pro / Pro+ / Business / Enterprise)
# is still required to USE the CLI — without it, the device-flow login
# will refuse to issue a token. The binary itself installs cleanly.

status_json copilot installing 30 "Checking GitHub Copilot CLI"
echo "[2.5/7] Checking GitHub Copilot CLI..."

find_copilot_bin() {
    local candidates=(
        "$HOME/.npm-global/bin/copilot"
        "/usr/local/bin/copilot"
        "/usr/bin/copilot"
        "/opt/homebrew/bin/copilot"
    )
    for p in "${candidates[@]}"; do [[ -x "$p" ]] && { echo "$p"; return; }; done
    for p in "$HOME"/.nvm/versions/node/*/bin/copilot; do [[ -x "$p" ]] && { echo "$p"; return; }; done
    [[ -x "$BIN_DIR/copilot" ]] && { echo "$BIN_DIR/copilot"; return; }
    command -v copilot 2>/dev/null || true
}

find_copilot_bin_loose() {
    local candidates=(
        "$HOME/.npm-global/bin/copilot"
        "/usr/local/bin/copilot"
        "/usr/bin/copilot"
        "/opt/homebrew/bin/copilot"
    )
    for p in "${candidates[@]}"; do [[ -e "$p" ]] && { echo "$p"; return 0; }; done
    for p in "$HOME"/.nvm/versions/node/*/bin/copilot; do [[ -e "$p" ]] && { echo "$p"; return 0; }; done
    [[ -e "$BIN_DIR/copilot" ]] && { echo "$BIN_DIR/copilot"; return 0; }
    return 0
}

EXISTING_COPILOT="$(find_copilot_bin)"
if [[ -z "$EXISTING_COPILOT" ]]; then
    LOOSE_COPILOT="$(find_copilot_bin_loose)"
    if [[ -n "$LOOSE_COPILOT" ]]; then
        log_line "DETECT" "Copilot binary exists but isn't +x: $LOOSE_COPILOT — running validator"
        EXISTING_COPILOT="$LOOSE_COPILOT"
    fi
fi
COPILOT_NEEDS_INSTALL=false

if [[ -n "$EXISTING_COPILOT" ]]; then
    if COPILOT_VER="$(validate_npm_cli "$EXISTING_COPILOT" "Copilot CLI")"; then
        ok "copilot $COPILOT_VER ($EXISTING_COPILOT)"
        info "Updating to latest..."
        npm config set prefix "$NPM_PREFIX" 2>/dev/null || true
        export PATH="$NPM_PREFIX/bin:$PATH"
        if npm install -g @github/copilot --quiet 2>/dev/null; then
            EXISTING_COPILOT="$(find_copilot_bin)"
            if COPILOT_VER_NEW="$(validate_npm_cli "$EXISTING_COPILOT" "Copilot CLI")"; then
                [[ "$COPILOT_VER_NEW" != "$COPILOT_VER" ]] \
                    && info "Updated: $COPILOT_VER → $COPILOT_VER_NEW" \
                    || info "Already up to date"
            else
                rc=$?
                warn "Copilot CLI update validation failed — $(validate_explain $rc); kept previous $COPILOT_VER"
            fi
        else
            warn "Copilot CLI update failed — continuing with $COPILOT_VER"
        fi
    else
        rc=$?
        warn "Existing Copilot binary at $EXISTING_COPILOT is broken — $(validate_explain $rc); will reinstall"
        COPILOT_NEEDS_INSTALL=true
    fi
else
    # npm install -g works without sudo (uses ~/.npm-global).
    # See parallel comment in [2/7] Claude block.
    COPILOT_NEEDS_INSTALL=true
fi

if [[ "$COPILOT_NEEDS_INSTALL" == true ]]; then
    info "Installing Copilot CLI via npm..."
    info "  GitHub's AI coding agent (terminal version). Powers the"
    info "  'Copilot' provider — agentic mode with task management,"
    info "  plan review. Requires active GitHub Copilot subscription."
    npm config set prefix "$NPM_PREFIX"
    export PATH="$NPM_PREFIX/bin:$PATH"
    if [[ -e "$NPM_PREFIX/bin/copilot" ]]; then
        rm -f "$NPM_PREFIX/bin/copilot" 2>/dev/null || true
    fi
    if [[ -d "$NPM_PREFIX/lib/node_modules/@github" ]]; then
        rm -rf "$NPM_PREFIX/lib/node_modules/@github" 2>/dev/null || true
    fi
    if npm install -g @github/copilot --quiet \
        || npm install -g @github/copilot \
            --include=optional --foreground-scripts; then
        EXISTING_COPILOT="$(find_copilot_bin)"
        if COPILOT_VER="$(validate_npm_cli "$EXISTING_COPILOT" "Copilot CLI")"; then
            ok "copilot $COPILOT_VER (installed)"
            info "First Copilot session will prompt for /login (device-flow,"
            info "  requires active GitHub Copilot subscription)"
        else
            rc=$?
            warn "Copilot CLI install validation failed — $(validate_explain $rc)"
            add_manual_install "Copilot CLI" \
                "npm install -g @github/copilot"
            EXISTING_COPILOT=""
        fi
    else
        warn "Copilot CLI install failed — see manual install below"
        add_manual_install "Copilot CLI" \
            "npm install -g @github/copilot"
    fi
fi

# Stable symlink for GUI launches (desktop entry bypasses ~/.bashrc PATH)
if [[ -n "${EXISTING_COPILOT:-}" && "$EXISTING_COPILOT" != "$BIN_DIR/copilot" ]]; then
    ln -sf "$EXISTING_COPILOT" "$BIN_DIR/copilot"
    info "Linked $BIN_DIR/copilot -> $EXISTING_COPILOT"
fi

# Record Copilot status in the [SUMMARY] block (task #62) — alongside
# claude/git/etc., so users see at a glance whether both AI CLIs are
# installed.
if [[ -n "${EXISTING_COPILOT:-}" ]]; then
    TOOL_REPORT+=("ok|copilot|auto|GitHub Copilot CLI")
else
    TOOL_REPORT+=("missing|copilot|auto|GitHub Copilot CLI")
fi
if [[ -n "${EXISTING_CLAUDE:-}" ]]; then
    TOOL_REPORT+=("ok|claude|auto|Claude Code CLI")
else
    TOOL_REPORT+=("missing|claude|auto|Claude Code CLI")
fi

# ─── [2.7/7] Local LLM backend (Ollama, opt-in via --selected llama) ──────
#
# Task #4 (#76 in audit doc): orchestrates ollama install for the
# Aider provider's local-LLM backend (audit § 5). Only triggers when
# --selected contains "llama" — keeps default install free of
# external curl|sh side effects. Existing ollama install is detected
# (any path) and reported but not re-installed.

status_json llama installing 40 "Checking local LLM backend (Ollama)"
echo "[2.7/7] Checking local LLM backend (Ollama)..."

EXISTING_OLLAMA="$(command -v ollama 2>/dev/null || true)"
if [[ -n "$EXISTING_OLLAMA" ]]; then
    OLLAMA_VER="$($EXISTING_OLLAMA --version 2>/dev/null | head -1 || echo 'unknown')"
    ok "ollama $OLLAMA_VER ($EXISTING_OLLAMA)"
    TOOL_REPORT+=("ok|ollama|auto|Ollama (local LLM backend)")
elif selected_includes "llama" || selected_includes "ollama"; then
    if [[ "$NO_SUDO" == true ]]; then
        warn "Ollama install needs root for systemd unit — skipped (--no-sudo)"
        TOOL_REPORT+=("missing|ollama|auto|Ollama (local LLM backend)")
    else
        info "Installing Ollama via ollama.com/install.sh (~1.5 GB download)..."
        info "  Includes ROCm/CUDA libraries; 5-15 min on slow links."
        info "  Ollama installer needs sudo (writes to /usr/local)."

        # Pre-cache sudo password so ollama install.sh's internal
        # `sudo install`, `sudo tar` etc. don't hang waiting for a
        # password (which they can't read in a non-interactive
        # `--headless` shell — was causing the 2026-05-08 hang on
        # `>>> Installing ollama to /usr/local`).
        #
        # In headless mode (GUI wizard) interactive `sudo -v` would
        # print the prompt to a non-existent TTY and hang. Try in
        # this order:
        #   1. sudo -n true            (already cached?)
        #   2. pkexec true             (GUI password prompt)
        #   3. interactive sudo -v     (only when run from terminal)
        OLLAMA_DO_INSTALL=true
        if sudo -n true 2>/dev/null; then
            : # already cached, no prompt needed
        elif [[ "$HEADLESS" == true ]] && command -v pkexec >/dev/null; then
            info "  Requesting graphical sudo prompt (pkexec)..."
            if pkexec --disable-internal-agent /bin/true 2>/dev/null; then
                # pkexec elevated once; refresh sudo cache so subsequent
                # `sudo` calls in ollama install.sh use cached creds.
                # Still need a real `sudo -v` for that — but with
                # cached pkexec auth it should pass without prompt.
                if ! sudo -n true 2>/dev/null; then
                    # pkexec doesn't refresh sudo's cache (different
                    # auth backend). Run install.sh's sudo bits via
                    # pkexec wrapper instead.
                    warn "Ollama install needs sudo cache refresh; skipping for now"
                    warn "  To install manually later (with terminal sudo):"
                    warn "    curl -fsSL https://ollama.com/install.sh | sh"
                    OLLAMA_DO_INSTALL=false
                fi
            else
                warn "Ollama install needs root — pkexec dialog cancelled/unavailable"
                warn "  To install manually later: curl -fsSL https://ollama.com/install.sh | sh"
                OLLAMA_DO_INSTALL=false
            fi
        elif [[ -t 0 && -t 1 ]]; then
            info "  Asking for sudo password (cached for next 15 min)..."
            if ! sudo -v 2>/dev/null; then
                warn "Ollama install needs sudo — couldn't acquire it; skipping"
                warn "  To install manually later: curl -fsSL https://ollama.com/install.sh | sh"
                OLLAMA_DO_INSTALL=false
            fi
        else
            warn "Ollama install needs sudo but wizard has no TTY for password input."
            warn "  Re-run from a terminal:  ./install.sh --selected llama"
            warn "  Or install manually: curl -fsSL https://ollama.com/install.sh | sh"
            OLLAMA_DO_INSTALL=false
        fi
        if [[ "$OLLAMA_DO_INSTALL" == false ]]; then
            TOOL_REPORT+=("missing|ollama|auto|Ollama (local LLM backend)")
            add_manual_install "Ollama" \
                "curl -fsSL https://ollama.com/install.sh | sh"
        fi

        if [[ "$OLLAMA_DO_INSTALL" == true ]]; then
            # Heartbeat: every 30 s emit `...still installing` so the
            # GUI wizard sees progress even when ollama's `\r` progress
            # updates are eaten by line-buffering.
            (
                sleep 30
                while kill -0 "$$" 2>/dev/null; do
                    printf '    ...ollama install still in progress (%s)\n' \
                        "$(du -sh /usr/local/lib/ollama 2>/dev/null \
                            | awk '{print $1}' || echo 'fetching')"
                    sleep 30
                done
            ) &
            HEARTBEAT_PID=$!

            info "  Progress shown below as % / speed / ETA:"
            # DOWNLOAD POLICY (see top of script): ollama install.sh
            # internally calls `curl --progress-bar -o ollama-linux-…tar.zst`
            # which only shows a `####` bar (no %, no speed, no ETA).
            # We hook `curl` via a PATH-injected wrapper that strips
            # `--progress-bar` so curl's default multi-column progress
            # meter takes over for ALL downloads inside their script.
            CURL_HOOK_DIR="$(mktemp -d -t bterm-curl-hook.XXXXXX)"
            REAL_CURL="$(command -v curl)"
            cat > "$CURL_HOOK_DIR/curl" <<HOOK_EOF
#!/bin/sh
# Wrapper auto-generated by install.sh to satisfy DOWNLOAD POLICY:
# every download must show progress (% / speed / ETA). Drops the
# terse \`--progress-bar\` flag so curl's default meter prints.
args=
for a in "\$@"; do
    case "\$a" in
        --progress-bar|-#) ;;          # drop terse progress flags
        --silent|-s)        ;;          # drop silent flags
        *) args="\$args \$(printf %q "\$a")" ;;
    esac
done
eval exec "$REAL_CURL" "\$args"
HOOK_EOF
            chmod +x "$CURL_HOOK_DIR/curl"

            # `stdbuf -oL` line-buffers so GUI wizard sees real-time updates.
            # `timeout` cap prevents indefinite hang on stuck network.
            OLLAMA_TIMEOUT="${OLLAMA_INSTALL_TIMEOUT:-1800}"
            if PATH="$CURL_HOOK_DIR:$PATH" timeout "$OLLAMA_TIMEOUT" bash -c \
                'stdbuf -oL curl -fL https://ollama.com/install.sh | stdbuf -oL sh'; then
                kill "$HEARTBEAT_PID" 2>/dev/null
                wait "$HEARTBEAT_PID" 2>/dev/null
                rm -rf "$CURL_HOOK_DIR" 2>/dev/null
                EXISTING_OLLAMA="$(command -v ollama 2>/dev/null || echo '/usr/local/bin/ollama')"
                ok "ollama (installed → $EXISTING_OLLAMA)"
                info "First Aider session will spawn 'ollama serve' if not running"
                info "Pull a starter model: ollama pull qwen2.5-coder:0.5b"
                TOOL_REPORT+=("ok|ollama|auto|Ollama (local LLM backend)")
            else
                kill "$HEARTBEAT_PID" 2>/dev/null
                wait "$HEARTBEAT_PID" 2>/dev/null
                rm -rf "$CURL_HOOK_DIR" 2>/dev/null
                warn "Ollama install failed — see manual install below"
                add_manual_install "Ollama" \
                    "curl -fsSL https://ollama.com/install.sh | sh"
                TOOL_REPORT+=("missing|ollama|auto|Ollama (local LLM backend)")
            fi
        fi
    fi
else
    info "Ollama not detected (optional — needed for Aider provider with local LLM)."
    info "  To install:  ./install.sh --selected llama"
    info "  Or:          curl -fsSL https://ollama.com/install.sh | sh"
    TOOL_REPORT+=("missing|ollama|auto|Ollama (local LLM backend)")
fi

# ─── [2.8/7] Aider AI coding assistant (Python, via pipx) ──────────────────
#
# Aider is the 3rd bundled AI provider (audit § 9). Unlike Claude/Copilot
# (npm), Aider is a Python package — install via pipx for venv isolation.
# Pipx handles its own PATH-shim so binary lands in ~/.local/bin/aider.
# Falls back to `pip install --user aider-chat` if pipx is missing
# (binary still ends up in ~/.local/bin/ via Python user-base).

status_json aider installing 45 "Checking Aider AI coding assistant"
echo "[2.8/7] Checking Aider..."

find_aider_bin() {
    local candidates=(
        "$HOME/.local/bin/aider"
        "$HOME/.local/pipx/venvs/aider-chat/bin/aider"
        "/usr/local/bin/aider"
        "/usr/bin/aider"
        "/opt/homebrew/bin/aider"
    )
    for p in "${candidates[@]}"; do [[ -x "$p" ]] && { echo "$p"; return; }; done
    command -v aider 2>/dev/null || true
}

EXISTING_AIDER="$(find_aider_bin)"
AIDER_NEEDS_INSTALL=false

if [[ -n "$EXISTING_AIDER" ]]; then
    AIDER_VER="$("$EXISTING_AIDER" --version 2>/dev/null | head -1 || echo 'unknown')"
    ok "aider $AIDER_VER ($EXISTING_AIDER)"
elif [[ "$NO_SUDO" == true && ! -x "$(command -v pipx 2>/dev/null)" \
        && ! -x "$(command -v pip 2>/dev/null)" ]]; then
    warn "Aider not found and no pipx/pip available without sudo — skipping"
    add_manual_install "Aider" \
        "pipx install aider-chat   # or: pip install --user aider-chat"
    TOOL_REPORT+=("missing|aider|auto|Aider AI coding assistant")
else
    AIDER_NEEDS_INSTALL=true
fi

if [[ "$AIDER_NEEDS_INSTALL" == true ]]; then
    info "Installing Aider via pipx..."
    info "  Open-source AI coding agent with local-LLM support."
    info "  Powers the 'Aider' provider in BTerminal — pairs with"
    info "  Ollama / OpenAI-compatible endpoints. Python package via"
    info "  pipx (venv-isolated, no system-Python interference)."

    # Strategy:
    #   1. Try pipx install — preferred (isolated venv).
    #   2. If pipx missing → apt install pipx (with SUDO_ASKPASS),
    #      then retry pipx install.
    #   3. Fallback: pip install --user aider-chat.
    AIDER_INSTALLED=false
    if command -v pipx >/dev/null 2>&1; then
        if pipx install aider-chat --force 2>&1 | tail -5; then
            AIDER_INSTALLED=true
        fi
    fi

    if [[ "$AIDER_INSTALLED" == false && "$NO_SUDO" == false ]]; then
        # Try to install pipx via apt, then retry
        info "  pipx not found — installing via apt..."
        if apt_install pipx; then
            # pipx installs to ~/.local/bin; ensure it's on PATH for this run
            export PATH="$HOME/.local/bin:$PATH"
            pipx ensurepath 2>/dev/null || true
            if pipx install aider-chat --force 2>&1 | tail -5; then
                AIDER_INSTALLED=true
            fi
        fi
    fi

    if [[ "$AIDER_INSTALLED" == false ]]; then
        info "  Falling back to pip install --user aider-chat..."
        if pip install --user --break-system-packages aider-chat 2>&1 | tail -5 \
                || pip install --user aider-chat 2>&1 | tail -5; then
            AIDER_INSTALLED=true
        fi
    fi

    if [[ "$AIDER_INSTALLED" == true ]]; then
        # Re-discover binary (may be in pipx venv or ~/.local/bin)
        EXISTING_AIDER="$(find_aider_bin)"
        if [[ -n "$EXISTING_AIDER" ]]; then
            AIDER_VER="$("$EXISTING_AIDER" --version 2>/dev/null | head -1 || echo 'unknown')"
            ok "aider $AIDER_VER (installed)"
            log_line "VALIDATE" "Aider: OK ($AIDER_VER)"
            TOOL_REPORT+=("ok|aider|auto|Aider AI coding assistant")
        else
            warn "Aider install reported success but binary not found"
            add_manual_install "Aider" \
                "pipx install aider-chat   # or: pip install --user aider-chat"
            TOOL_REPORT+=("missing|aider|auto|Aider AI coding assistant")
        fi
    else
        warn "Aider install failed — see manual install below"
        add_manual_install "Aider" \
            "pipx install aider-chat   # or: pip install --user aider-chat"
        TOOL_REPORT+=("missing|aider|auto|Aider AI coding assistant")
    fi
fi

# Stable symlink for GUI launches (desktop entry bypasses ~/.bashrc PATH)
if [[ -n "${EXISTING_AIDER:-}" && "$EXISTING_AIDER" != "$BIN_DIR/aider" ]]; then
    ln -sf "$EXISTING_AIDER" "$BIN_DIR/aider"
    info "Linked $BIN_DIR/aider -> $EXISTING_AIDER"
fi

# ─── [3/7] System tools ────────────────────────────────────────────────────

status_json system_tools installing 50 "Checking system tools"
echo "[3/7] Checking system tools..."

# Task #62 (2026-05-07): per-tool report — accumulated across check_tool
# calls and dumped at the end as the [SUMMARY] block. Each entry:
#   "<status>|<cmd>|<tier>|<label>"
# where status ∈ {ok, missing} and tier mirrors the third arg below.
TOOL_REPORT=()

check_tool() {  # check_tool <cmd> <apt_pkg> <required: true|auto|false> <label>
    local cmd="$1" pkg="$2" required="$3" label="$4"
    local tier
    case "$required" in
        true)  tier="required" ;;
        auto)  tier="auto"     ;;
        *)     tier="optional" ;;
    esac

    if command -v "$cmd" &>/dev/null; then
        ok "$label"
        TOOL_REPORT+=("ok|$cmd|$tier|$label")
        return
    fi

    # Pull purpose from defaults/dependencies.json so install logs
    # explain *what* each tool is for (no need to update install.sh
    # when descriptions change — single source of truth).
    local purpose
    purpose="$(dep_get system_tools "$cmd" description)"

    if [[ "$tier" == "required" ]]; then
        info "$label not found — installing $pkg..."
        [[ -n "$purpose" ]] && info "  ($purpose)"
        if apt_install "$pkg"; then
            ok "$label (installed)"
            TOOL_REPORT+=("ok|$cmd|$tier|$label")
        else
            fail "$label required but could not be installed — apt install $pkg"
            TOOL_REPORT+=("missing|$cmd|$tier|$label")
            add_manual_install "$label (required)" "sudo apt install $pkg"
        fi
    elif [[ "$tier" == "auto" ]]; then
        # Task #4: --selected whitelist gates 'auto' tier installs.
        # Empty list → all 'auto' deps attempted (legacy behaviour);
        # non-empty list → only deps in the list considered.
        if ! selected_includes "$cmd"; then
            warn "$label not found — skipped (not in --selected list)"
            TOOL_REPORT+=("missing|$cmd|$tier|$label")
            return
        fi
        info "$label not found — installing $pkg..."
        [[ -n "$purpose" ]] && info "  ($purpose)"
        if apt_install "$pkg"; then
            ok "$label (installed)"
            TOOL_REPORT+=("ok|$cmd|$tier|$label")
        else
            warn "$label could not be installed — apt install $pkg"
            TOOL_REPORT+=("missing|$cmd|$tier|$label")
            add_manual_install "$label" "sudo apt install $pkg"
        fi
    else
        warn "$label not found (optional) — apt install $pkg"
        TOOL_REPORT+=("missing|$cmd|$tier|$label")
        add_manual_install "$label (optional)" "sudo apt install $pkg"
    fi
}

# Emits the [SUMMARY] block grouped by tier. Same layout as
# bterminal.diagnostics.format_summary_text() — tools/test_install_summary.sh
# verifies parity. Called at end of install regardless of warnings/errors
# so users can see what's actually present after the run.
emit_tool_summary() {
    echo ""
    echo "[SUMMARY]"
    local tier title status entry cmd label
    for tier in required auto optional; do
        case "$tier" in
            required) title="Required" ;;
            auto)     title="Auto-install (apt)" ;;
            optional) title="Optional (manual)" ;;
        esac
        local printed_header=0
        for entry in "${TOOL_REPORT[@]}"; do
            IFS='|' read -r status cmd ent_tier label <<< "$entry"
            [[ "$ent_tier" == "$tier" ]] || continue
            if [[ $printed_header -eq 0 ]]; then
                echo "$title:"
                printed_header=1
            fi
            if [[ "$status" == "ok" ]]; then
                printf "  \033[32m✓\033[0m %s\n" "$label"
            else
                printf "  \033[31m✗\033[0m %s\n" "$label"
            fi
        done
    done
    echo ""
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

status_json gtk installing 65 "Checking GTK bindings"
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

status_json files installing 75 "Installing BTerminal files"
echo "[5/7] Installing BTerminal files..."

# Backup current installation so ERR trap can restore it on failure
if [[ -d "$INSTALL_DIR/bterminal" || -f "$INSTALL_DIR/bterminal.py" ]]; then
    BACKUP_DIR="$(mktemp -d /tmp/bterminal-backup-XXXXXX)"
    for f in "${BTERMINAL_FILES[@]}"; do
        [[ -f "$INSTALL_DIR/$f" ]] && cp -f "$INSTALL_DIR/$f" "$BACKUP_DIR/$f" 2>/dev/null || true
    done
    [[ -d "$INSTALL_DIR/bterminal" ]] && cp -r "$INSTALL_DIR/bterminal" "$BACKUP_DIR/bterminal" 2>/dev/null || true
    [[ -f "$INSTALL_DIR/bterminal.py" ]] && cp -f "$INSTALL_DIR/bterminal.py" "$BACKUP_DIR/" 2>/dev/null || true
    info "Backup: $BACKUP_DIR"
fi

mkdir -p "$INSTALL_DIR" "$BIN_DIR" "$CONFIG_DIR" "$CTX_DIR" "$ICON_DIR"

# Drop the legacy flat shim if present from a previous install
rm -f "$INSTALL_DIR/bterminal.py"

# Copy CLI tools (flat at INSTALL_DIR)
for f in "${BTERMINAL_FILES[@]}"; do
    cp "$SCRIPT_DIR/tools/$f" "$INSTALL_DIR/$f"
done

# Copy the bterminal/ Python package (recursively, refresh on reinstall).
# rsync rather than cp -r: --exclude skips __pycache__ from the dev
# machine so stale bytecode doesn't hold references after a refactor.
rm -rf "$INSTALL_DIR/bterminal"
if command -v rsync &>/dev/null; then
    rsync -a --exclude='__pycache__' --exclude='*.pyc' \
        "$SCRIPT_DIR/bterminal/" "$INSTALL_DIR/bterminal/"
else
    cp -r "$SCRIPT_DIR/bterminal" "$INSTALL_DIR/bterminal"
    find "$INSTALL_DIR/bterminal" -type d -name __pycache__ -exec rm -rf {} + 2>/dev/null || true
fi

# Launcher script — `~/.local/bin/bterminal` runs `python3 -m bterminal`
# from INSTALL_DIR so the package is on sys.path.
cat > "$INSTALL_DIR/bterminal-launcher" << EOF
#!/bin/bash
cd "$INSTALL_DIR"
exec python3 -m bterminal "\$@"
EOF
chmod +x "$INSTALL_DIR/bterminal-launcher"

cp "$SCRIPT_DIR/bterminal.svg" "$ICON_DIR/bterminal.svg"
gtk-update-icon-cache -f -t "$HOME/.local/share/icons/hicolor/" 2>/dev/null || true
chmod +x "$INSTALL_DIR/ctx" "$INSTALL_DIR/consult" \
         "$INSTALL_DIR/tasks" "$INSTALL_DIR/claude_log" "$INSTALL_DIR/memory_wizard" \
         "$INSTALL_DIR/aider_setup_wizard"

# Live symlinks from repo (git pull → immediate effect, no reinstall needed).
# License files live under defaults/license/ (LICENSE.en.md, LICENSE.pl.md...);
# the LICENSE.md at repo root is a symlink to LICENSE.en.md for GitHub display.
ln -sfn "$SCRIPT_DIR/defaults"   "$INSTALL_DIR/defaults"
ln -sf  "$SCRIPT_DIR/README.md"  "$INSTALL_DIR/README.md"
ln -sf  "$SCRIPT_DIR/VERSION"    "$INSTALL_DIR/VERSION"
info "Live symlinks: defaults/ README.md VERSION"

# i18n: compile .po -> .mo for every locale, then expose via symlink.
# .mo are not committed (see .gitignore), so we always build them here.
# Missing gettext is non-fatal — runtime falls back to identity (English).
if compgen -G "$SCRIPT_DIR/locale/*/LC_MESSAGES/*.po" > /dev/null; then
    if command -v msgfmt &>/dev/null; then
        compiled=0
        for po in "$SCRIPT_DIR"/locale/*/LC_MESSAGES/*.po; do
            mo="${po%.po}.mo"
            if msgfmt --check --output-file="$mo" "$po" 2>/dev/null; then
                compiled=$((compiled + 1))
            else
                warn "msgfmt failed for $po"
            fi
        done
        info "Compiled $compiled translation catalog(s)"
    else
        warn "gettext (msgfmt) not installed — UI will run in English only. Install with: apt install gettext"
    fi
fi
ln -sfn "$SCRIPT_DIR/locale" "$INSTALL_DIR/locale"

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

status_json symlinks installing 90 "Creating symlinks"
echo "[6/7] Creating symlinks..."

for tool in bterminal ctx consult tasks claude_log memory_wizard; do
    src="$INSTALL_DIR/$tool"
    [[ "$tool" == "bterminal" ]] && src="$INSTALL_DIR/bterminal-launcher"
    ln -sf "$src" "$BIN_DIR/$tool"
done
ok "Symlinks in $BIN_DIR"

# ─── [7/7] Init ctx + desktop entry ────────────────────────────────────────

status_json finalize installing 95 "Finalizing"
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
ok "Desktop entry created (apps menu)"

# Also place a launcher icon on the user's actual Desktop folder
# (~/Pulpit on pl_PL, ~/Desktop on en_US, etc.) — what users
# actually expect when they hear "ikonka na pulpicie".
# 2026-05-08 fix: install previously created the apps-menu entry
# only; user reported missing desktop shortcut.
DESKTOP_USER_DIR=""
if command -v xdg-user-dir >/dev/null 2>&1; then
    DESKTOP_USER_DIR="$(xdg-user-dir DESKTOP 2>/dev/null || true)"
fi
# Locale fallback list (covers Polish/French/German/Spanish/Chinese)
for d in "$DESKTOP_USER_DIR" \
         "$HOME/Desktop" "$HOME/Pulpit" "$HOME/Bureau" \
         "$HOME/Schreibtisch" "$HOME/Escritorio" "$HOME/桌面"; do
    if [[ -n "$d" && -d "$d" && "$d" != "$HOME" ]]; then
        cp -f "$DESKTOP_DIR/bterminal.desktop" "$d/bterminal.desktop"
        # Linux Mint 22.x + GNOME require +x on .desktop to launch
        # by double-click (otherwise icon shows "Unknown application").
        chmod +x "$d/bterminal.desktop"
        # Mark as trusted so the file manager doesn't show the
        # "Untrusted application launcher" warning. This is a
        # GNOME-specific extended attribute; harmless on other DEs.
        if command -v gio >/dev/null 2>&1; then
            gio set "$d/bterminal.desktop" \
                metadata::trusted true 2>/dev/null || true
        fi
        info "Desktop shortcut placed at $d/bterminal.desktop"
        ok "Desktop icon created on $(basename "$d")"
        break
    fi
done

# ─── Summary ───────────────────────────────────────────────────────────────

# Task #62: per-tool [SUMMARY] block — surfaces every dep's actual
# post-install state, separately from ERRORS/WARNINGS aggregation
# below. Helps users (and Help → Diagnostics) see at a glance
# whether 'auto' tier deps (meld, pandoc, …) actually landed.
emit_tool_summary

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
    status_json done failed 100 "Installation failed"
    exit 1
fi

# ─── Manual installs block — what user needs to do himself ────────────────
#
# Components that couldn't be installed automatically. The user
# can run these commands and re-run install.sh — the next pass
# will detect each component and skip past it.
if (( ${#MANUAL_INSTALLS[@]} > 0 )); then
    echo ""
    echo "──────────────────────────────────────────────────────────────"
    printf "  \033[33m⚠\033[0m %d component(s) need manual installation:\n" \
        $(( ${#MANUAL_INSTALLS[@]} / 2 ))
    echo ""
    # MANUAL_INSTALLS is alternating (name, cmd) — iterate pairs
    for ((i=0; i < ${#MANUAL_INSTALLS[@]}; i+=2)); do
        printf "  • \033[1m%s\033[0m\n" "${MANUAL_INSTALLS[$i]}"
        printf "      \033[36m\$\033[0m %s\n" "${MANUAL_INSTALLS[$((i+1))]}"
    done
    echo ""
    echo "  After installing manually, re-run ./install.sh —"
    echo "  it will detect each component and skip past it."
    echo "──────────────────────────────────────────────────────────────"
    echo ""
fi

# ─── Final verification — catches silent failures ──────────────────────────
#
# Without this block, install.sh could exit 0 with a "success"
# message even when nothing got installed (e.g., user dropped sudo
# AND BT files copy silently failed — possible if INSTALL_DIR was
# read-only). Enforces the "no trust, only verify" rule before
# claiming success.

VERIFY_ERRORS=()

# (1) BT package landed in INSTALL_DIR
if [[ ! -f "$INSTALL_DIR/bterminal/__init__.py" ]]; then
    VERIFY_ERRORS+=("BTerminal package not at $INSTALL_DIR/bterminal/")
fi

# (2) Launcher symlink + executable
if [[ ! -L "$BIN_DIR/bterminal" && ! -f "$BIN_DIR/bterminal" ]]; then
    VERIFY_ERRORS+=("Launcher missing at $BIN_DIR/bterminal")
elif [[ ! -x "$BIN_DIR/bterminal" ]]; then
    VERIFY_ERRORS+=("Launcher at $BIN_DIR/bterminal is not executable")
fi

# (3) Companion CLI symlinks
for tool in ctx tasks consult memory_wizard claude_log; do
    if [[ ! -e "$BIN_DIR/$tool" ]]; then
        VERIFY_ERRORS+=("CLI tool missing: $BIN_DIR/$tool")
    fi
done

# (4) Re-validate AI CLIs that we claimed to install. Only checks
# binaries we actually attempted to install (EXISTING_CLAUDE/
# EXISTING_COPILOT non-empty after the install branches).
if [[ -n "${EXISTING_CLAUDE:-}" ]]; then
    if ! validate_npm_cli "$EXISTING_CLAUDE" "Claude Code (post-install)" \
            >/dev/null; then
        rc=$?
        VERIFY_ERRORS+=("Claude Code post-install check failed: $(validate_explain $rc)")
    fi
fi
if [[ -n "${EXISTING_COPILOT:-}" ]]; then
    if ! validate_npm_cli "$EXISTING_COPILOT" "Copilot CLI (post-install)" \
            >/dev/null; then
        rc=$?
        VERIFY_ERRORS+=("Copilot CLI post-install check failed: $(validate_explain $rc)")
    fi
fi

if (( ${#VERIFY_ERRORS[@]} > 0 )); then
    echo ""
    echo "ERRORS — install verification failed:"
    for e in "${VERIFY_ERRORS[@]}"; do
        printf "  \033[31m✗\033[0m %s\n" "$e"
        log_line "VERIFY_FAIL" "$e"
    done
    {
        echo "BTerminal v${BTERMINAL_VERSION} — install verification failed:"
        for e in "${VERIFY_ERRORS[@]}"; do echo "  • $e"; done
        echo ""
        echo "Re-run ./install.sh after fixing the issues above."
        echo "Diagnostic log: $INSTALL_LOG"
    } >&2
    status_json done failed 100 "Install verification failed"
    exit 1
fi

# Clean up backup — install succeeded AND verified
[[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] && rm -rf "$BACKUP_DIR"

# Task #4: terminal status_json line so the GTK orchestrator knows
# the install loop completed cleanly (parses last line + flips to
# Summary page).
status_json done ok 100 "Installation completed"

echo "=== BTerminal v${BTERMINAL_VERSION} installed successfully ==="
echo ""
echo "Run:  bterminal"
echo "PATH: export PATH=\"\$HOME/.local/bin:\$HOME/.npm-global/bin:\$PATH\""
# Final log marker — automated tests (tools/test_wizard_e2e_vm.sh)
# poll install.log for this exact line to confirm success.
log_line "OK" "BTerminal v${BTERMINAL_VERSION} installed successfully"
