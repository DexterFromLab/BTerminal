"""BTerminal configuration: paths, options, theme palette, CSS, dialog helpers.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/` package together with __main__.py in a later
migration etap (when bterminal.py becomes a thin shim).
"""

import json
import os
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, Gtk

# ─── App identity ─────────────────────────────────────────────────────────────

APP_NAME = "BTerminal"


def _read_version() -> str:
    try:
        return (Path(__file__).parent.parent / "VERSION").read_text().strip()
    except Exception:
        return "unknown"


APP_VERSION = _read_version()


# ─── Filesystem paths ─────────────────────────────────────────────────────────

CONFIG_DIR = os.path.expanduser("~/.config/bterminal")
SESSIONS_FILE = os.path.join(CONFIG_DIR, "sessions.json")
# Legacy filename — read once for migration into AI_SESSIONS_FILE (T1.7),
# then renamed to .bak. Kept as a constant so the migrator can find it.
CLAUDE_SESSIONS_FILE = os.path.join(CONFIG_DIR, "claude_sessions.json")
# Canonical AI session store (T1.7) — provider-aware (`provider` field
# per session) so Claude / Copilot / future CLIs share one file.
AI_SESSIONS_FILE = os.path.join(CONFIG_DIR, "ai_sessions.json")
CONSULT_CONFIG_FILE = os.path.join(CONFIG_DIR, "consult.json")
PLUGINS_DIR = os.path.join(CONFIG_DIR, "plugins")
PLUGINS_CONFIG_FILE = os.path.join(CONFIG_DIR, "plugins.json")
OPTIONS_FILE = os.path.join(CONFIG_DIR, "options.json")

# Ctx subsystem (sqlite DB at ~/.claude-context/, image attachments).
# Bterminal CTX panels and the standalone `ctx` CLI both read/write here.
CTX_DB = os.path.join(os.path.expanduser("~"), ".claude-context", "context.db")
CTX_IMAGES_DIR = os.path.join(os.path.expanduser("~"), ".claude-context", "images")

SSH_PATH = "/usr/bin/ssh"

# Repo path written by install.sh into ~/.config/bterminal/repo_path.
# Updater + errata viewer use it to locate the source tree.
_repo_path_file = os.path.join(CONFIG_DIR, "repo_path")
REPO_DIR = open(_repo_path_file).read().strip() if os.path.isfile(_repo_path_file) else None


# ─── Terminal constants ───────────────────────────────────────────────────────

SCROLLBACK_LINES = 10000

KEY_MAP = {
    "Enter":  "\r",
    "Tab":    "\t",
    "Escape": "\x1b",
    "Ctrl+C": "\x03",
    "Ctrl+D": "\x04",
}


# ─── Options ─────────────────────────────────────────────────────────────────

_OPTIONS_DEFAULTS = {
    "theme": "dark",
    "font": "Monospace 11",
    "shell": "",
    "check_updates_on_start": True,
    # i18n — None / "auto" means: fall through to LANGUAGE / LANG env vars,
    # then default to 'en'. Set explicitly via Options dialog (F5).
    "language": None,
    # When True and the active language != 'en', the AI intro prompt
    # appends a one-liner telling the agent which language the user
    # prefers. User can disable in the Options dialog.
    "tell_ai_language": True,
    # Task #70 (2026-05-07): when True (default), pasting an image
    # into an AI tab whose provider declares an `image_paste_template`
    # wraps the path with that template (e.g. "User provided image:
    # {path} — Read it before responding." for Copilot). When False,
    # bare path always — provider templates ignored. Per-session
    # override via provider_options.image_paste_template (#71)
    # supersedes both this toggle and the provider default.
    "image_paste_hint_enabled": True,
    # Task #7 (#79 in audit doc): per-provider local model overrides.
    # Map provider name → ollama tag (e.g. "aider" → "qwen2.5-coder:1.5b").
    # Empty dict = use provider's capabilities.default_model. Edited
    # via OptionsDialog 'Set as default' button.
    "default_local_model_for_provider": {},
    # Task #11 (#83): names of bundled providers the user has hidden
    # via OptionsDialog → AI Providers. Disabled providers stay
    # registered (existing sessions keep working) but vanish from the
    # Add AI Session dropdown. Empty list = all enabled (default).
    "disabled_providers": [],
}

# R1.f2: gdy load wykryje uszkodzony JSON, zapisuje tu wyjątek żeby
# BTerminalApp.__init__ mógł pokazać dialog po stworzeniu okna (na
# poziomie tego modułu GTK jeszcze nie żyje, więc dialog odroczony).
# Self-heal nadpisuje plik defaultami od razu, niezależnie od dialog'u.
_options_load_error: Exception | None = None


def _load_options():
    """Load options.json. Trzy przypadki:
    1. Brak pliku → defaults (R1.f1, silent).
    2. Plik valid → merge z defaultami (forward-compat).
    3. Plik istnieje ale invalid JSON / unreadable → self-heal:
       defaults używane w runtime + plik nadpisany defaultami +
       error zapisany w `_options_load_error` do późniejszego dialog'u.
       (R1.f2)
    """
    global _options_load_error
    if not os.path.exists(OPTIONS_FILE):
        return dict(_OPTIONS_DEFAULTS)
    try:
        with open(OPTIONS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return {**_OPTIONS_DEFAULTS, **data}
    except Exception as exc:  # JSONDecodeError, OSError, ...
        _options_load_error = exc
        # Self-heal: nadpisz defaultami żeby kolejny start był czysty.
        # Cicha porażka jeśli nie da się zapisać (np. permissions) —
        # i tak runtime będzie miał defaults z return'a.
        try:
            os.makedirs(CONFIG_DIR, exist_ok=True)
            with open(OPTIONS_FILE, "w", encoding="utf-8") as fh:
                json.dump(_OPTIONS_DEFAULTS, fh, indent=2)
        except OSError:
            pass
        return dict(_OPTIONS_DEFAULTS)


def _save_options(opts):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(OPTIONS_FILE, "w", encoding="utf-8") as fh:
        json.dump(opts, fh, indent=2)


_OPTIONS = _load_options()
FONT = _OPTIONS["font"]


# ─── Theme palettes ───────────────────────────────────────────────────────────

CATPPUCCIN_MOCHA = {
    "rosewater": "#f5e0dc",
    "flamingo":  "#f2cdcd",
    "pink":      "#f5c2e7",
    "mauve":     "#cba6f7",
    "red":       "#f38ba8",
    "maroon":    "#eba0ac",
    "peach":     "#fab387",
    "yellow":    "#f9e2af",
    "green":     "#a6e3a1",
    "teal":      "#94e2d5",
    "sky":       "#89dceb",
    "sapphire":  "#74c7ec",
    "blue":      "#89b4fa",
    "lavender":  "#b4befe",
    "text":      "#cdd6f4",
    "subtext1":  "#bac2de",
    "subtext0":  "#a6adc8",
    "overlay2":  "#9399b2",
    "overlay1":  "#7f849c",
    "overlay0":  "#6c7086",
    "surface2":  "#585b70",
    "surface1":  "#45475a",
    "surface0":  "#313244",
    "base":      "#1e1e2e",
    "mantle":    "#181825",
    "crust":     "#11111b",
}

CATPPUCCIN_LATTE = {
    "rosewater": "#dc8a78",
    "flamingo":  "#dd7878",
    "pink":      "#ea76cb",
    "mauve":     "#8839ef",
    "red":       "#d20f39",
    "maroon":    "#e64553",
    "peach":     "#fe640b",
    "yellow":    "#df8e1d",
    "green":     "#40a02b",
    "teal":      "#179299",
    "sky":       "#04a5e5",
    "sapphire":  "#209fb5",
    "blue":      "#1e66f5",
    "lavender":  "#7287fd",
    "text":      "#3c3f58",
    "subtext1":  "#4c4f67",
    "subtext0":  "#5c5f75",
    "overlay2":  "#7c7f93",
    "overlay1":  "#8c8fa1",
    "overlay0":  "#9ca0b0",
    "surface2":  "#acb0be",
    "surface1":  "#bcc0cc",
    "surface0":  "#ccd0da",
    "base":      "#eff1f5",
    "mantle":    "#e6e9ef",
    "crust":     "#dce0e8",
}

# Active theme — mutable, switched at runtime via _toggle_theme in bterminal.py
# (which mutates CATPPUCCIN.update(...) and TERMINAL_PALETTE[:] = ...).
CATPPUCCIN = dict(CATPPUCCIN_LATTE if _OPTIONS.get("theme") == "light" else CATPPUCCIN_MOCHA)

TERMINAL_PALETTE_MOCHA = [
    "#45475a", "#f38ba8", "#a6e3a1", "#f9e2af",
    "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
    "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af",
    "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8",
]

TERMINAL_PALETTE_LATTE = [
    "#e6e9ef", "#d20f39", "#40a02b", "#df8e1d",
    "#1e66f5", "#ea76cb", "#179299", "#5c5f77",
    "#dce0e8", "#d20f39", "#40a02b", "#df8e1d",
    "#1e66f5", "#ea76cb", "#179299", "#6c6f85",
]

TERMINAL_PALETTE = list(TERMINAL_PALETTE_LATTE if _OPTIONS.get("theme") == "light" else TERMINAL_PALETTE_MOCHA)

_THEME_COLORS = {
    "dark":  {"ssh": "#89b4fa", "claude": "#89b4fa"},
    "light": {"ssh": "#5c5f77", "claude": "#6c6f85"},
}


def _session_color(session_type="ssh"):
    """Return the fixed color for a session type in current theme.

    Reads _OPTIONS['theme'] (kept in sync with bterminal.py's
    _current_theme by _toggle_theme) — equivalent to reading the old
    module-level _current_theme.
    """
    theme = _OPTIONS.get("theme", "dark")
    return _THEME_COLORS[theme].get(session_type, CATPPUCCIN["text"])


# ─── CSS ─────────────────────────────────────────────────────────────────────

def _build_css(t):
    """Generate CSS from a Catppuccin theme dict."""
    return f"""
.debug-rest-bar {{ background-color: #e74c3c; min-height: 2px; }}
window {{ background-color: {t['base']}; color: {t['text']}; }}
.sidebar {{ background-color: {t['mantle']}; border-right: 1px solid {t['surface0']}; color: {t['text']}; }}
.sidebar label {{ color: {t['text']}; }}
.git-panel {{ border-right: none; border-left: 1px solid {t['surface0']}; }}
.git-header {{ background-color: {t['crust']}; padding: 6px 10px; border-bottom: 1px solid {t['surface0']}; }}
.git-header label {{ font-weight: bold; font-size: 13px; color: {t['blue']}; }}
.git-header button {{ min-width: 28px; min-height: 28px; padding: 2px; border-radius: 4px; font-size: 14px; color: {t['text']}; }}
.git-section-title {{ background-color: {t['surface0']}; padding: 5px 10px; font-weight: bold; font-size: 11px; color: {t['subtext1']}; border-top: 1px solid {t['surface1']}; }}
.git-section-body {{ padding: 6px 10px; color: {t['text']}; }}
.git-section-body label {{ color: {t['text']}; }}
.git-branch-name {{ font-weight: bold; font-size: 14px; color: {t['green']}; }}
.sidebar * {{ min-width: 0; }}
.sidebar button, .sidebar combobox, .sidebar combobox button, .sidebar entry {{ min-width: 0; padding: 2px 2px; border: none; }}
.sidebar button label {{ min-width: 0; }}
.sidebar-header {{ background-color: {t['crust']}; padding: 8px 12px; font-weight: bold; font-size: 13px; color: {t['blue']}; border-bottom: 1px solid {t['surface0']}; }}
.sidebar-btn {{ background: {t['surface0']}; border: none; border-radius: 4px; color: {t['text']}; padding: 4px 4px; min-height: 24px; min-width: 0; }}
.sidebar-tab {{ padding: 4px 2px; min-width: 0; min-height: 0; border-radius: 0; border: none; background: {t['mantle']}; color: {t['subtext0']}; font-size: 13px; border-bottom: 2px solid transparent; }}
.sidebar-tab:hover {{ background: {t['surface0']}; }}
.sidebar-tab-active {{ color: {t['blue']}; border-bottom: 2px solid {t['blue']}; }}
.sidebar-btn:hover {{ background: {t['surface1']}; }}
.sidebar-btn:active {{ background: {t['surface2']}; }}
notebook header tab {{ background: {t['mantle']}; color: {t['subtext0']}; border: none; padding: 4px 12px; border-radius: 6px 6px 0 0; margin: 0 1px; }}
notebook header tab:checked {{ background: {t['surface0']}; color: {t['text']}; }}
notebook header {{ background: {t['crust']}; }}
notebook {{ background: {t['base']}; }}
treeview {{ background-color: {t['mantle']}; color: {t['text']}; }}
treeview:selected {{ background-color: {t['surface1']}; color: {t['text']}; }}
treeview:hover {{ background-color: {t['surface0']}; }}
textview text {{ background-color: {t['base']}; color: {t['text']}; }}
.tab-close-btn {{ background: transparent; border: none; border-radius: 4px; padding: 0; min-width: 20px; min-height: 20px; color: {t['overlay1']}; }}
.tab-close-btn:hover {{ background: {t['surface2']}; color: {t['red']}; }}
stackswitcher {{ background: {t['crust']}; border-bottom: 1px solid {t['surface0']}; }}
stackswitcher button {{ background: {t['crust']}; color: {t['subtext0']}; border: none; border-radius: 0; padding: 6px 16px; border-bottom: 2px solid transparent; font-weight: bold; font-size: 12px; }}
stackswitcher button:checked {{ background: {t['mantle']}; color: {t['blue']}; border-bottom: 2px solid {t['blue']}; }}
stackswitcher button:hover {{ background: {t['surface0']}; }}
textview.ctx-detail {{ font-family: monospace; font-size: 10pt; }}
textview.ctx-detail text {{ background-color: {t['crust']}; color: {t['subtext1']}; }}
.stats-bar {{ background-color: {t['mantle']}; border-top: 1px solid {t['surface0']}; }}
.stats-bar label {{ color: {t['subtext1']}; font-size: 13px; }}
.theme-toggle {{ background: {t['surface0']}; border: none; border-radius: 12px; padding: 2px 10px; min-height: 26px; min-width: 26px; color: {t['yellow']}; font-size: 16px; }}
.theme-toggle:hover {{ background: {t['surface1']}; }}
dialog {{ background-color: {t['base']}; color: {t['text']}; }}
dialog box, dialog grid, dialog label {{ color: {t['text']}; }}
entry {{ background-color: {t['mantle']}; color: {t['text']}; border: 1px solid {t['surface1']}; border-radius: 4px; }}
combobox window menu {{ background-color: {t['mantle']}; color: {t['text']}; }}
combobox button {{ background-color: {t['mantle']}; color: {t['text']}; }}
menu {{ background-color: {t['mantle']}; color: {t['text']}; border: 1px solid {t['surface1']}; }}
menu menuitem {{ color: {t['text']}; }}
menu menuitem:hover {{ background-color: {t['surface1']}; }}
checkbutton {{ color: {t['text']}; }}
button {{ background-color: {t['surface0']}; color: {t['text']}; border: 1px solid {t['surface1']}; }}
button:hover {{ background-color: {t['surface1']}; }}
dialog button {{ background-color: {t['surface0']}; color: {t['text']}; border: 1px solid {t['surface1']}; border-radius: 4px; padding: 6px 16px; }}
dialog button:hover {{ background-color: {t['surface1']}; }}
headerbar {{ background-color: {t['crust']}; color: {t['text']}; }}
headerbar button {{ color: {t['text']}; }}
messagedialog {{ background-color: {t['base']}; color: {t['text']}; }}
messagedialog label {{ color: {t['text']}; }}
dialog headerbar {{ background-color: {t['crust']}; color: {t['text']}; }}
dialog decoration {{ background-color: {t['crust']}; }}
spinbutton {{ background-color: {t['mantle']}; color: {t['text']}; }}
scrollbar {{ background-color: {t['mantle']}; }}
scrollbar slider {{ background-color: {t['surface1']}; border-radius: 4px; }}
scrollbar slider:hover {{ background-color: {t['surface2']}; }}
"""


def _parse_color(hex_str):
    """Parse hex color string to Gdk.RGBA."""
    c = Gdk.RGBA()
    c.parse(hex_str)
    return c


# ─── Modal dialogs ────────────────────────────────────────────────────────────

def show_error_dialog(parent, msg):
    """Show a modal error dialog."""
    dlg = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.ERROR,
        buttons=Gtk.ButtonsType.OK,
        text=msg,
    )
    dlg.run()
    dlg.destroy()


def show_info_dialog(parent, title, msg):
    """Show a modal info dialog."""
    dlg = Gtk.MessageDialog(
        transient_for=parent,
        modal=True,
        message_type=Gtk.MessageType.INFO,
        buttons=Gtk.ButtonsType.OK,
        text=title,
    )
    dlg.format_secondary_text(msg)
    dlg.run()
    dlg.destroy()
