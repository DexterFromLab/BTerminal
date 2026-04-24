#!/usr/bin/env python3
"""BTerminal — Terminal SSH z panelem sesji, w stylu MobaXterm."""
# v2025.03.31

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
gi.require_version("Gdk", "3.0")

import json
import os
import random
import sys
import re
import shutil
import sqlite3
import subprocess
import tempfile
import threading
from pathlib import Path
import urllib.error
import urllib.request
import uuid
import importlib

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, Vte

# ─── Stałe i konfiguracja ────────────────────────────────────────────────────

APP_NAME = "BTerminal"

def _read_version() -> str:
    try:
        return (Path(__file__).parent / "VERSION").read_text().strip()
    except Exception:
        return "unknown"

APP_VERSION = _read_version()
CONFIG_DIR = os.path.expanduser("~/.config/bterminal")
SESSIONS_FILE = os.path.join(CONFIG_DIR, "sessions.json")
CLAUDE_SESSIONS_FILE = os.path.join(CONFIG_DIR, "claude_sessions.json")
CONSULT_CONFIG_FILE = os.path.join(CONFIG_DIR, "consult.json")
PLUGINS_DIR = os.path.join(CONFIG_DIR, "plugins")
PLUGINS_CONFIG_FILE = os.path.join(CONFIG_DIR, "plugins.json")
OPTIONS_FILE = os.path.join(CONFIG_DIR, "options.json")
SSH_PATH = "/usr/bin/ssh"

_OPTIONS_DEFAULTS = {
    "theme": "dark",
    "font": "Monospace 11",
    "shell": "",
    "check_updates_on_start": True,
}

def _load_options():
    try:
        with open(OPTIONS_FILE, encoding="utf-8") as fh:
            data = json.load(fh)
        return {**_OPTIONS_DEFAULTS, **data}
    except Exception:
        return dict(_OPTIONS_DEFAULTS)

def _save_options(opts):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(OPTIONS_FILE, "w", encoding="utf-8") as fh:
        json.dump(opts, fh, indent=2)

_OPTIONS = _load_options()
_repo_path_file = os.path.join(os.path.expanduser("~/.config/bterminal"), "repo_path")
REPO_DIR = open(_repo_path_file).read().strip() if os.path.isfile(_repo_path_file) else None

def _find_claude_path():
    """Locate Claude Code binary across common install locations.

    Returns absolute path if found, otherwise None. Handles npm-global
    (default prefix used by our installer), nvm, system paths, and macOS
    homebrew. Falls back to PATH lookup with an extended search so that
    GUI launches (which often miss ~/.npm-global/bin from ~/.bashrc)
    still resolve the binary.
    """
    import glob
    candidates = [
        os.path.expanduser("~/.local/bin/claude"),
        os.path.expanduser("~/.npm-global/bin/claude"),
        "/usr/local/bin/claude",
        "/usr/bin/claude",
        "/opt/homebrew/bin/claude",
    ]
    candidates += sorted(
        glob.glob(os.path.expanduser("~/.nvm/versions/node/*/bin/claude")),
        reverse=True,
    )
    for p in candidates:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    extra = os.pathsep.join([
        os.path.expanduser("~/.npm-global/bin"),
        os.path.expanduser("~/.local/bin"),
    ])
    env_path = os.environ.get("PATH", "") + os.pathsep + extra
    return shutil.which("claude", path=env_path)

CLAUDE_PATH = _find_claude_path()

FONT = _OPTIONS["font"]
SCROLLBACK_LINES = 10000

# Catppuccin Mocha
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

# Active theme — mutable, switched at runtime
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

# Fixed session type colors per theme (SSH, Claude Code)
_THEME_COLORS = {
    "dark":  {"ssh": "#89b4fa", "claude": "#89b4fa"},
    "light": {"ssh": "#5c5f77", "claude": "#6c6f85"},
}

_current_theme = _OPTIONS.get("theme", "dark")


def _session_color(session_type="ssh"):
    """Return the fixed color for a session type in current theme."""
    return _THEME_COLORS[_current_theme].get(session_type, CATPPUCCIN["text"])

KEY_MAP = {
    "Enter": "\r",
    "Tab": "\t",
    "Escape": "\x1b",
    "Ctrl+C": "\x03",
    "Ctrl+D": "\x04",
}

def _build_css(t):
    """Generate CSS from a Catppuccin theme dict."""
    return f"""
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

CSS = _build_css(CATPPUCCIN)


def _parse_color(hex_str):
    """Parse hex color string to Gdk.RGBA."""
    c = Gdk.RGBA()
    c.parse(hex_str)
    return c


def _create_color_combo():
    """Create a ComboBox with color swatches for SESSION_COLORS."""
    store = Gtk.ListStore(str, GdkPixbuf.Pixbuf)
    for hex_color in SESSION_COLORS:
        r = int(hex_color[1:3], 16)
        g = int(hex_color[3:5], 16)
        b = int(hex_color[5:7], 16)
        pixbuf = GdkPixbuf.Pixbuf.new(GdkPixbuf.Colorspace.RGB, False, 8, 20, 14)
        pixbuf.fill((r << 24) | (g << 16) | (b << 8) | 0xFF)
        store.append([hex_color, pixbuf])
    combo = Gtk.ComboBox(model=store, hexpand=True)
    renderer_pixbuf = Gtk.CellRendererPixbuf()
    combo.pack_start(renderer_pixbuf, False)
    combo.add_attribute(renderer_pixbuf, "pixbuf", 1)
    renderer_text = Gtk.CellRendererText()
    combo.pack_start(renderer_text, True)
    combo.add_attribute(renderer_text, "text", 0)
    combo.set_active(0)
    return combo


def _save_expanded(tree, store, id_col):
    """Save set of expanded node IDs from a TreeView."""
    expanded = set()
    store.foreach(lambda m, path, it: (
        expanded.add(m.get_value(it, id_col))
        if tree.row_expanded(path) else None
    ))
    return expanded


def _restore_expanded(tree, store, id_col, expanded):
    """Restore expansion state from saved IDs."""
    def _check(model, path, it):
        if model.get_value(it, id_col) in expanded:
            tree.expand_row(path, False)
    store.foreach(_check)


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


# ─── SessionManager ──────────────────────────────────────────────────────────


class JsonListManager:
    """Generic CRUD manager for a list of dicts stored in a JSON file."""

    def __init__(self, filepath):
        self._filepath = filepath
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.sessions = []
        self.load()

    def validate_entry(self, entry):
        """Override in subclasses to validate before add/update."""
        pass

    def load(self):
        if os.path.exists(self._filepath):
            try:
                with open(self._filepath, "r") as f:
                    self.sessions = json.load(f)
            except (json.JSONDecodeError, IOError):
                self.sessions = []
        else:
            self.sessions = []

    def save(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.sessions, f, indent=2)
            os.replace(tmp, self._filepath)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def add(self, session):
        self.validate_entry(session)
        session["id"] = str(uuid.uuid4())
        self.sessions.append(session)
        self.save()
        return session

    def update(self, session_id, data):
        for i, s in enumerate(self.sessions):
            if s["id"] == session_id:
                self.sessions[i].update(data)
                self.validate_entry(self.sessions[i])
                self.save()
                return self.sessions[i]
        return None

    def delete(self, session_id):
        self.sessions = [s for s in self.sessions if s["id"] != session_id]
        self.save()

    def get(self, session_id):
        for s in self.sessions:
            if s["id"] == session_id:
                return s
        return None

    def all(self):
        return list(self.sessions)


class SessionManager(JsonListManager):
    """Zarządzanie zapisanymi sesjami SSH."""

    def __init__(self):
        super().__init__(SESSIONS_FILE)

    def validate_entry(self, entry):
        if not entry.get("host"):
            raise ValueError("SSH session requires 'host'")


class ClaudeSessionManager(JsonListManager):
    """Zarządzanie zapisanymi konfiguracjami Claude Code."""

    def __init__(self):
        super().__init__(CLAUDE_SESSIONS_FILE)


# ─── ConsultManager ──────────────────────────────────────────────────────────


class ConsultManager:
    """Manage consult configuration (API key, models from OpenRouter & Claude Code)."""

    CLAUDE_CODE_MODELS = {
        "claude-code/opus": {"name": "Claude Opus 4.6", "enabled": True, "source": "claude-code"},
        "claude-code/sonnet": {"name": "Claude Sonnet 4.6", "enabled": True, "source": "claude-code"},
        "claude-code/haiku": {"name": "Claude Haiku 4.5", "enabled": True, "source": "claude-code"},
    }

    DEFAULT_CONFIG = {
        "api_key": "",
        "default_model": "google/gemini-2.5-pro",
        "models": {
            "google/gemini-2.5-pro": {"enabled": True, "name": "Gemini 2.5 Pro", "source": "openrouter"},
            "openai/gpt-4o": {"enabled": True, "name": "GPT-4o", "source": "openrouter"},
            "openai/o3-mini": {"enabled": True, "name": "o3-mini", "source": "openrouter"},
            "deepseek/deepseek-r1": {"enabled": True, "name": "DeepSeek R1", "source": "openrouter"},
            "anthropic/claude-sonnet-4": {"enabled": False, "name": "Claude Sonnet 4", "source": "openrouter"},
            "meta-llama/llama-4-maverick": {
                "enabled": False,
                "name": "Llama 4 Maverick",
                "source": "openrouter",
            },
            "claude-code/opus": {"enabled": True, "name": "Claude Opus 4.6", "source": "claude-code"},
            "claude-code/sonnet": {"enabled": True, "name": "Claude Sonnet 4.6", "source": "claude-code"},
            "claude-code/haiku": {"enabled": True, "name": "Claude Haiku 4.5", "source": "claude-code"},
        },
    }

    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.config = {}
        self.load()

    def load(self):
        if os.path.isfile(CONSULT_CONFIG_FILE):
            try:
                with open(CONSULT_CONFIG_FILE) as f:
                    self.config = json.load(f)
                self._ensure_claude_code_models()
                return
            except (json.JSONDecodeError, IOError):
                pass
        self.config = json.loads(json.dumps(self.DEFAULT_CONFIG))
        self.save()

    def _ensure_claude_code_models(self):
        """Ensure Claude Code models exist and are enabled in config."""
        models = self.config.setdefault("models", {})
        changed = False
        for mid, info in self.CLAUDE_CODE_MODELS.items():
            if mid not in models:
                models[mid] = dict(info)
                changed = True
            elif not models[mid].get("enabled", False):
                models[mid]["enabled"] = True
                changed = True
        if changed:
            self.save()

    def save(self):
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(self.config, f, indent=2)
            os.replace(tmp, CONSULT_CONFIG_FILE)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def get_api_key(self):
        return self.config.get("api_key", "")

    def set_api_key(self, key):
        self.config["api_key"] = key
        self.save()

    def get_default_model(self):
        return self.config.get("default_model", "")

    def set_default_model(self, model_id):
        self.config["default_model"] = model_id
        models = self.config.setdefault("models", {})
        if model_id in models:
            models[model_id]["enabled"] = True
        self.save()

    def get_models(self):
        return self.config.get("models", {})

    def set_model_enabled(self, model_id, enabled):
        models = self.config.setdefault("models", {})
        if model_id in models:
            models[model_id]["enabled"] = enabled
            self.save()

    def add_model(self, model_id, name="", enabled=True, source="openrouter"):
        models = self.config.setdefault("models", {})
        models[model_id] = {"name": name or model_id, "enabled": enabled, "source": source}
        self.save()

    def remove_model(self, model_id):
        models = self.config.get("models", {})
        models.pop(model_id, None)
        if self.config.get("default_model") == model_id:
            self.config["default_model"] = ""
        self.save()

    def get_project_preset(self, project_dir):
        """Return tribunal preset for a project dir, or None."""
        presets = self.config.get("tribunal_projects", {})
        return presets.get(project_dir)

    def save_project_preset(self, project_dir, preset):
        """Save tribunal preset for a project dir."""
        if "tribunal_projects" not in self.config:
            self.config["tribunal_projects"] = {}
        self.config["tribunal_projects"][project_dir] = preset
        self.save()

    def delete_project_preset(self, project_dir):
        """Remove tribunal preset for a project dir."""
        presets = self.config.get("tribunal_projects", {})
        presets.pop(project_dir, None)
        self.save()


# ─── SessionDialog ────────────────────────────────────────────────────────────


class SessionDialog(Gtk.Dialog):
    """Dialog dodawania/edycji sesji SSH."""

    def __init__(self, parent, session=None):
        title = "Edit Session" if session else "Add Session"
        super().__init__(
            title=title,
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        self.set_default_size(420, -1)
        self.set_default_response(Gtk.ResponseType.OK)

        box = self.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        box.pack_start(grid, True, True, 0)

        labels = ["Name:", "Host:", "Port:", "Username:", "SSH Key:", "Folder:"]
        for i, text in enumerate(labels):
            lbl = Gtk.Label(label=text, halign=Gtk.Align.END)
            grid.attach(lbl, 0, i, 1, 1)

        self.entry_name = Gtk.Entry(hexpand=True)
        self.entry_host = Gtk.Entry(hexpand=True)
        self.entry_port = Gtk.SpinButton.new_with_range(1, 65535, 1)
        self.entry_port.set_value(22)
        self.entry_username = Gtk.Entry(hexpand=True)
        self.entry_key = Gtk.Entry(hexpand=True)
        self.entry_key.set_placeholder_text("(optional) path to private key")
        self.folder_combo = Gtk.ComboBoxText.new_with_entry()
        self.folder_combo.set_hexpand(True)
        for f in sorted({
            s.get("folder", "").strip()
            for s in parent.session_manager.all()
            if s.get("folder", "").strip()
        }):
            self.folder_combo.append_text(f)
        self.folder_combo.get_child().set_placeholder_text("(optional) folder for grouping")

        grid.attach(self.entry_name, 1, 0, 1, 1)
        grid.attach(self.entry_host, 1, 1, 1, 1)
        grid.attach(self.entry_port, 1, 2, 1, 1)
        grid.attach(self.entry_username, 1, 3, 1, 1)
        grid.attach(self.entry_key, 1, 4, 1, 1)
        grid.attach(self.folder_combo, 1, 5, 1, 1)

        # Edit mode: fill fields
        if session:
            self.entry_name.set_text(session.get("name", ""))
            self.entry_host.set_text(session.get("host", ""))
            self.entry_port.set_value(int(session.get("port", 22)))
            self.entry_username.set_text(session.get("username", ""))
            self.entry_key.set_text(session.get("key_file", ""))
            self.folder_combo.get_child().set_text(session.get("folder", ""))

        self.show_all()

    def get_data(self):
        return {
            "name": self.entry_name.get_text().strip(),
            "host": self.entry_host.get_text().strip(),
            "port": int(self.entry_port.get_value()),
            "username": self.entry_username.get_text().strip(),
            "key_file": self.entry_key.get_text().strip(),
            "folder": self.folder_combo.get_child().get_text().strip(),
        }

    def validate(self):
        data = self.get_data()
        if not data["name"]:
            self._show_error("Name is required.")
            return False
        if not data["host"]:
            self._show_error("Host is required.")
            return False
        if not data["username"]:
            self._show_error("Username is required.")
            return False
        return True

    def _show_error(self, msg):
        show_error_dialog(self, msg)


# ─── MacroDialog ─────────────────────────────────────────────────────────────


class MacroStepRow(Gtk.ListBoxRow):
    """Single step row in the macro editor."""

    def __init__(self, step=None):
        super().__init__()
        box = Gtk.Box(spacing=6)
        box.set_border_width(4)

        self.type_combo = Gtk.ComboBoxText()
        for t in ("text", "key", "delay"):
            self.type_combo.append(t, t)
        self.type_combo.set_active_id("text")
        box.pack_start(self.type_combo, False, False, 0)

        self.stack = Gtk.Stack()

        # text entry
        self.text_entry = Gtk.Entry(hexpand=True)
        self.text_entry.set_placeholder_text("Text to send")
        self.stack.add_named(self.text_entry, "text")

        # key combo
        self.key_combo = Gtk.ComboBoxText()
        for k in ("Enter", "Tab", "Escape", "Ctrl+C", "Ctrl+D"):
            self.key_combo.append(k, k)
        self.key_combo.set_active(0)
        self.stack.add_named(self.key_combo, "key")

        # delay spin
        self.delay_spin = Gtk.SpinButton.new_with_range(100, 10000, 100)
        self.delay_spin.set_value(1000)
        self.stack.add_named(self.delay_spin, "delay")

        box.pack_start(self.stack, True, True, 0)
        self.add(box)

        self.type_combo.connect("changed", self._on_type_changed)

        if step:
            self.type_combo.set_active_id(step["type"])
            if step["type"] == "text":
                self.text_entry.set_text(step["value"])
            elif step["type"] == "key":
                self.key_combo.set_active_id(step["value"])
            elif step["type"] == "delay":
                self.delay_spin.set_value(int(step["value"]))

        self._on_type_changed(self.type_combo)
        self.show_all()

    def _on_type_changed(self, combo):
        active = combo.get_active_id()
        if active:
            self.stack.set_visible_child_name(active)

    def get_step(self):
        t = self.type_combo.get_active_id()
        if t == "text":
            return {"type": "text", "value": self.text_entry.get_text()}
        elif t == "key":
            return {"type": "key", "value": self.key_combo.get_active_id()}
        elif t == "delay":
            return {"type": "delay", "value": int(self.delay_spin.get_value())}
        return {"type": "text", "value": ""}


class MacroDialog(Gtk.Dialog):
    """Dialog do dodawania/edycji makra SSH."""

    def __init__(self, parent, macro=None):
        title = "Edit Macro" if macro else "Add Macro"
        super().__init__(
            title=title,
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        self.set_default_size(500, 400)
        self.set_default_response(Gtk.ResponseType.OK)

        box = self.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)

        # Name
        name_box = Gtk.Box(spacing=8)
        name_box.pack_start(Gtk.Label(label="Name:"), False, False, 0)
        self.entry_name = Gtk.Entry(hexpand=True)
        name_box.pack_start(self.entry_name, True, True, 0)
        box.pack_start(name_box, False, False, 0)

        # Steps label
        box.pack_start(Gtk.Label(label="Steps:", halign=Gtk.Align.START), False, False, 0)

        # Steps listbox in scrolled window
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(200)
        self.listbox = Gtk.ListBox()
        self.listbox.set_selection_mode(Gtk.SelectionMode.SINGLE)
        scrolled.add(self.listbox)
        box.pack_start(scrolled, True, True, 0)

        # Buttons
        btn_box = Gtk.Box(spacing=4)
        for label_text, cb in [
            ("Add Step", self._on_add),
            ("Remove", self._on_remove),
            ("Move Up", self._on_move_up),
            ("Move Down", self._on_move_down),
        ]:
            btn = Gtk.Button(label=label_text)
            btn.connect("clicked", cb)
            btn_box.pack_start(btn, True, True, 0)
        box.pack_start(btn_box, False, False, 0)

        # Quick-add shortcuts
        box.pack_start(Gtk.Separator(), False, False, 2)
        quick_label = Gtk.Label(label="Quick add:", halign=Gtk.Align.START)
        quick_label.set_opacity(0.6)
        box.pack_start(quick_label, False, False, 0)

        quick_box = Gtk.Box(spacing=4)
        for key_name in ("Enter", "Tab", "Escape", "Ctrl+C", "Ctrl+D"):
            btn = Gtk.Button(label=key_name)
            btn.connect("clicked", self._on_quick_key, key_name)
            quick_box.pack_start(btn, True, True, 0)
        box.pack_start(quick_box, False, False, 0)

        delay_box = Gtk.Box(spacing=6)
        btn_delay = Gtk.Button(label="+ Delay")
        self.delay_spin = Gtk.SpinButton.new_with_range(100, 10000, 100)
        self.delay_spin.set_value(500)
        lbl_ms = Gtk.Label(label="ms")
        btn_delay.connect("clicked", self._on_quick_delay)
        delay_box.pack_start(btn_delay, False, False, 0)
        delay_box.pack_start(self.delay_spin, False, False, 0)
        delay_box.pack_start(lbl_ms, False, False, 0)
        box.pack_start(delay_box, False, False, 0)

        # Fill if editing
        if macro:
            self.entry_name.set_text(macro.get("name", ""))
            for step in macro.get("steps", []):
                self.listbox.add(MacroStepRow(step))

        self.show_all()

    def _on_quick_key(self, btn, key_name):
        row = MacroStepRow({"type": "key", "value": key_name})
        self.listbox.add(row)

    def _on_quick_delay(self, btn):
        ms = int(self.delay_spin.get_value())
        row = MacroStepRow({"type": "delay", "value": ms})
        self.listbox.add(row)

    def _on_add(self, btn):
        row = MacroStepRow()
        self.listbox.add(row)

    def _on_remove(self, btn):
        row = self.listbox.get_selected_row()
        if row:
            self.listbox.remove(row)

    def _on_move_up(self, btn):
        row = self.listbox.get_selected_row()
        if row:
            idx = row.get_index()
            if idx > 0:
                step = row.get_step()
                self.listbox.remove(row)
                new_row = MacroStepRow(step)
                self.listbox.insert(new_row, idx - 1)
                self.listbox.select_row(new_row)

    def _on_move_down(self, btn):
        row = self.listbox.get_selected_row()
        if row:
            idx = row.get_index()
            n = len(self.listbox.get_children())
            if idx < n - 1:
                step = row.get_step()
                self.listbox.remove(row)
                new_row = MacroStepRow(step)
                self.listbox.insert(new_row, idx + 1)
                self.listbox.select_row(new_row)

    def get_data(self):
        steps = []
        for row in self.listbox.get_children():
            steps.append(row.get_step())
        return {
            "name": self.entry_name.get_text().strip(),
            "steps": steps,
        }

    def validate(self):
        data = self.get_data()
        if not data["name"]:
            self._show_error("Macro name is required.")
            return False
        if not data["steps"]:
            self._show_error("At least one step is required.")
            return False
        return True

    def _show_error(self, msg):
        show_error_dialog(self, msg)


# ─── ClaudeCodeDialog ─────────────────────────────────────────────────────────


def _fetch_ctx_output(project_name):
    """Run 'ctx get <project>' and return its stdout, or empty string on failure."""
    try:
        result = subprocess.run(
            ["ctx", "get", project_name, "--shared"],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


_GLOBAL_RULES_FILE = Path(__file__).parent / "defaults" / "global_rules.txt"
_BUNDLED_SKILLS_DIR = Path(__file__).parent / "defaults" / "skills"


def _read_global_rules() -> list:
    """Read enabled rules from defaults/global_rules.txt (lines not starting with #)."""
    try:
        text = _GLOBAL_RULES_FILE.read_text(errors="replace")
        return [l.strip() for l in text.splitlines()
                if l.strip() and not l.strip().startswith("#")]
    except Exception:
        return []


def _fetch_rules_block(project_name):
    """Return formatted rules block for project, or empty string if none."""
    try:
        result = subprocess.run(
            ["ctx", "rules", "inject", project_name],
            capture_output=True, text=True, timeout=5,
        )
        if result.returncode == 0 and result.stdout.strip():
            return result.stdout.strip()
    except (subprocess.TimeoutExpired, FileNotFoundError):
        pass
    return ""


def _tools_help(project_name):
    """Return detailed tool instructions string for a given project.

    Covers ctx (context management), consult (external AI queries / Tribunal),
    and tasks (CLI task manager with auto-trigger).
    """
    return (
        f"Kontekst zarządzasz przez: ctx --help\n"
        f"Ważne odkrycia zapisuj: ctx set {project_name} <key> <value>\n"
        f"Dołączanie do istniejącego: ctx append {project_name} <key> <value>\n"
        f'Przed zakończeniem sesji: ctx summary {project_name} "<co zrobiono>"\n'
        f"\n"
        f"Konsultacje z zewnętrznymi modelami AI: consult \"pytanie\"\n"
        f"Konkretny model: consult -m <model_id> \"pytanie\" — ZAWSZE najpierw sprawdź dostępne modele: consult models\n"
        f"Nazwy modeli to PEŁNE ID z prefixem providera, np. 'google/gemini-2.5-pro', 'openai/gpt-5-codex', 'deepseek/deepseek-r1' — NIE skracaj.\n"
        f"Dołączanie pliku jako kontekst: consult -f plik.py \"pytanie\"\n"
        f"Tribunal — debata wielu modeli AI: consult debate \"problem\"\n"
        f"  Domyślne role: --analyst claude-code/opus --arbiter claude-code/opus\n"
        f"  Advocate i Critic dobieraj wg potrzeb spośród: openai/gpt-5-codex, deepseek/deepseek-r1, google/gemini-2.5-pro\n"
        f'  Przykład: consult debate "problem" --analyst claude-code/opus --advocate openai/gpt-5-codex --critic deepseek/deepseek-r1 --arbiter claude-code/opus\n'
        f"\n"
        f"Dostępne narzędzie 'tasks' — ZEWNĘTRZNY CLI tool uruchamiany w Bash (NIE wbudowany TaskCreate/TaskList).\n"
        f"NIE pobieraj ani nie wykonuj zadań z listy samodzielnie.\n"
        f"Jeśli system auto-trigger wyśle Ci polecenie z listą zadań — wtedy wykonuj.\n"
        f"Po każdym wykonanym zadaniu MUSISZ oznaczyć je jako done: tasks done {project_name} <task_id>\n"
        f"Pomoc: tasks --help\n"
        f"\n"
        f"Memory Wizard — konfiguracja reguł na podstawie logów sesji:\n"
        f"  Dry-run (przejrzyj propozycje, zastosuj ręcznie wybrane):\n"
        f"    memory_wizard {project_name} --project-dir <dir> --dry-run\n"
        f"  Interaktywny (potwierdź każdą propozycję):\n"
        f"    memory_wizard {project_name} --project-dir <dir>\n"
        f"  Uruchom gdy użytkownik poprawia Cię wielokrotnie w ten sam sposób,\n"
        f"  lub po dłuższej sesji aby utrwalić wzorce w regułach."
    )


def _build_intro_prompt(project_name):
    """Build the standard intro prompt for a Claude Code session.

    Embeds ctx context directly + tool instructions for ctx, consult and tasks.
    """
    ctx_output = _fetch_ctx_output(project_name)
    tools = _tools_help(project_name)
    rules_block = _fetch_rules_block(project_name)
    global_rules = _read_global_rules()

    readme_path = Path(__file__).parent / "README.md"
    readme_hint = f" README: {readme_path}" if readme_path.exists() else ""
    header = f"Pracujesz w środowisku BTerminal — terminal SSH/Claude z wbudowanymi narzędziami (ctx, consult, tasks, memory_wizard, skills).{readme_hint}"

    if ctx_output:
        base = f"{header}\n\nKontekst projektu ({project_name}):\n{ctx_output}\n\n--- Narzędzia ---\n\n{tools}"
    else:
        base = f"{header}\n\nNazwa projektu w ctx/tasks: {project_name}\n\n--- Narzędzia ---\n\n{tools}"

    if global_rules:
        base += "\n\n--- Reguły globalne (BTerminal defaults) ---\n" + \
                "\n".join(f"- {r}" for r in global_rules)

    if rules_block:
        base += f"\n\n{rules_block}"
    return base


class ClaudeCodeDialog(Gtk.Dialog):
    """Dialog konfiguracji sesji Claude Code."""

    def __init__(self, parent, session=None):
        title = "Edit Claude Session" if session else "Add Claude Session"
        super().__init__(
            title=title,
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        self.set_default_size(460, -1)
        self.set_default_response(Gtk.ResponseType.OK)

        box = self.get_content_area()
        box.set_border_width(12)
        box.set_spacing(10)

        # Name, Folder, Color grid
        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        box.pack_start(grid, False, False, 0)

        for i, text in enumerate(["Name:", "Folder:", "Project dir:"]):
            lbl = Gtk.Label(label=text, halign=Gtk.Align.END)
            grid.attach(lbl, 0, i, 1, 1)

        self.entry_name = Gtk.Entry(hexpand=True)
        grid.attach(self.entry_name, 1, 0, 1, 1)

        self.folder_combo = Gtk.ComboBoxText.new_with_entry()
        self.folder_combo.set_hexpand(True)
        for f in sorted({
            s.get("folder", "").strip()
            for s in parent.claude_manager.all()
            if s.get("folder", "").strip()
        }):
            self.folder_combo.append_text(f)
        self.folder_combo.get_child().set_placeholder_text("(optional) folder for grouping")
        grid.attach(self.folder_combo, 1, 1, 1, 1)

        dir_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.entry_project_dir = Gtk.Entry(hexpand=True)
        self.entry_project_dir.set_placeholder_text("path to project directory (required)")
        dir_box.pack_start(self.entry_project_dir, True, True, 0)
        btn_browse = Gtk.Button(label="Browse…")
        btn_browse.connect("clicked", self._on_browse_dir)
        dir_box.pack_start(btn_browse, False, False, 0)
        grid.attach(dir_box, 1, 2, 1, 1)

        self.lbl_ctx_status = Gtk.Label(xalign=0)
        grid.attach(self.lbl_ctx_status, 1, 3, 1, 1)

        # Separator
        box.pack_start(Gtk.Separator(), False, False, 2)

        # Sudo checkbox
        self.chk_sudo = Gtk.CheckButton(label="Run with sudo (asks for password)")
        self.chk_sudo.set_active(True)
        box.pack_start(self.chk_sudo, False, False, 0)

        # Resume session checkbox
        self.chk_resume = Gtk.CheckButton(label="Resume last session (--resume)")
        self.chk_resume.set_active(False)
        box.pack_start(self.chk_resume, False, False, 0)

        # Skip permissions checkbox
        self.chk_skip_perms = Gtk.CheckButton(label="Skip permissions (--dangerously-skip-permissions)")
        self.chk_skip_perms.set_active(True)
        box.pack_start(self.chk_skip_perms, False, False, 0)

        # Custom prompt (appended after standard intro)
        lbl = Gtk.Label(label="Custom prompt (optional, appended after standard intro):", halign=Gtk.Align.START)
        box.pack_start(lbl, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(80)
        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        scrolled.add(self.textview)
        box.pack_start(scrolled, True, True, 0)

        # Edit mode: fill fields
        if session:
            self.entry_name.set_text(session.get("name", ""))
            self.folder_combo.get_child().set_text(session.get("folder", ""))
            self.chk_sudo.set_active(session.get("sudo", True))
            self.chk_resume.set_active(session.get("resume", True))
            self.chk_skip_perms.set_active(session.get("skip_permissions", True))
            self.entry_project_dir.set_text(session.get("project_dir", ""))
            prompt = session.get("prompt", "")
            if prompt:
                self.textview.get_buffer().set_text(prompt)

        self.show_all()
        self._update_ctx_status()

    def get_data(self):
        buf = self.textview.get_buffer()
        start, end = buf.get_bounds()
        prompt = buf.get_text(start, end, False).strip()
        return {
            "name": self.entry_name.get_text().strip(),
            "folder": self.folder_combo.get_child().get_text().strip(),
            "sudo": self.chk_sudo.get_active(),
            "resume": self.chk_resume.get_active(),
            "skip_permissions": self.chk_skip_perms.get_active(),
            "prompt": prompt,
            "project_dir": self.entry_project_dir.get_text().strip(),
        }

    def validate(self):
        data = self.get_data()
        if not data["name"]:
            self._show_error("Name is required.")
            return False
        if not data["project_dir"]:
            self._show_error("Project directory is required.")
            return False
        if not os.path.isdir(data["project_dir"]):
            self._show_error(f"Directory does not exist:\n{data['project_dir']}")
            return False
        return True

    def _show_error(self, msg):
        show_error_dialog(self, msg)

    def _on_browse_dir(self, button):
        dlg = Gtk.FileChooserDialog(
            title="Select project directory",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        if dlg.run() == Gtk.ResponseType.OK:
            path = dlg.get_filename()
            self.entry_project_dir.set_text(path)
            basename = os.path.basename(path.rstrip("/"))
            if not self.entry_name.get_text().strip():
                self.entry_name.set_text(basename)
            self._update_ctx_status()
        dlg.destroy()

    def _update_ctx_status(self):
        project_dir = self.entry_project_dir.get_text().strip()
        if not project_dir:
            self.lbl_ctx_status.set_text("")
            return
        name = os.path.basename(project_dir.rstrip("/"))
        if _is_ctx_project_registered(name):
            self.lbl_ctx_status.set_markup(
                '<small>\u2713 Ctx project "<b>'
                + GLib.markup_escape_text(name)
                + '</b>" is registered</small>'
            )
        else:
            self.lbl_ctx_status.set_markup(
                "<small>\u2139 New project \u2014 ctx wizard will guide you after save</small>"
            )


# ─── CtxEditDialog ────────────────────────────────────────────────────────────


CTX_DB = os.path.join(os.path.expanduser("~"), ".claude-context", "context.db")
CTX_IMAGES_DIR = os.path.join(os.path.expanduser("~"), ".claude-context", "images")
_IMAGE_EXTENSIONS = {".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp", ".svg", ".tiff", ".ico"}


def _clipboard_has_image_or_path():
    """Check if clipboard has an image bitmap or a text path to an image file."""
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    if clipboard.wait_is_image_available():
        return True
    text = clipboard.wait_for_text()
    if text:
        path = text.strip().strip("'\"")
        if os.path.isfile(path) and os.path.splitext(path)[1].lower() in _IMAGE_EXTENSIONS:
            return True
    return False


def _clipboard_get_image_or_path():
    """Return (pixbuf, None) or (None, file_path) or (None, None)."""
    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
    pixbuf = clipboard.wait_for_image()
    if pixbuf:
        return pixbuf, None
    text = clipboard.wait_for_text()
    if text:
        path = text.strip().strip("'\"")
        if os.path.isfile(path) and os.path.splitext(path)[1].lower() in _IMAGE_EXTENSIONS:
            return None, path
    return None, None


def _ensure_images_table():
    """Create images table in ctx database if it doesn't exist."""
    if not os.path.exists(CTX_DB):
        return
    db = sqlite3.connect(CTX_DB)
    db.execute(
        "CREATE TABLE IF NOT EXISTS images ("
        "  id INTEGER PRIMARY KEY AUTOINCREMENT,"
        "  project TEXT NOT NULL,"
        "  filename TEXT NOT NULL,"
        "  original_name TEXT,"
        "  added_at TEXT DEFAULT (datetime('now')),"
        "  UNIQUE(project, filename)"
        ")"
    )
    db.commit()
    db.close()


def _save_ctx_image(project, source, original_name=None):
    """Save image to ctx. source: file path (str) or GdkPixbuf.Pixbuf."""
    import shutil
    _ensure_images_table()
    proj_dir = os.path.join(CTX_IMAGES_DIR, project)
    os.makedirs(proj_dir, exist_ok=True)

    if isinstance(source, GdkPixbuf.Pixbuf):
        ext = ".png"
        if not original_name:
            original_name = "clipboard.png"
        filename = f"{uuid.uuid4().hex[:12]}{ext}"
        dest = os.path.join(proj_dir, filename)
        source.savev(dest, "png", [], [])
    else:
        if not original_name:
            original_name = os.path.basename(source)
        ext = os.path.splitext(original_name)[1] or ".png"
        filename = f"{uuid.uuid4().hex[:12]}{ext}"
        dest = os.path.join(proj_dir, filename)
        shutil.copy2(source, dest)

    db = sqlite3.connect(CTX_DB)
    db.execute(
        "INSERT OR REPLACE INTO images (project, filename, original_name, added_at) "
        "VALUES (?, ?, ?, datetime('now'))",
        (project, filename, original_name),
    )
    db.commit()
    db.close()
    return filename


def _delete_ctx_image(project, filename):
    """Delete an image file and its database entry."""
    path = os.path.join(CTX_IMAGES_DIR, project, filename)
    if os.path.exists(path):
        os.remove(path)
    if os.path.exists(CTX_DB):
        db = sqlite3.connect(CTX_DB)
        db.execute(
            "DELETE FROM images WHERE project = ? AND filename = ?",
            (project, filename),
        )
        db.commit()
        db.close()


def _detect_project_description(project_dir):
    """Detect project description from README or directory name."""
    for name in ["README.md", "README.rst", "README.txt", "README"]:
        readme_path = os.path.join(project_dir, name)
        if os.path.isfile(readme_path):
            try:
                with open(readme_path, "r") as f:
                    for line in f:
                        line = line.strip().lstrip("#").strip()
                        if line:
                            return line[:100]
            except (IOError, UnicodeDecodeError):
                pass
    return os.path.basename(project_dir.rstrip("/"))


_GENERIC_SUBDIRS = frozenset({
    "docs", "doc", "src", "source", "lib", "libs", "app", "apps",
    "frontend", "backend", "web", "api", "test", "tests", "spec",
    "scripts", "script", "code", "project", "workspace", "core",
})


def _smart_project_name(project_dir):
    """Return a meaningful project name for a directory.

    If the directory's basename looks like a generic subfolder (docs, src, …),
    walk up to the nearest git root and use that name instead.
    Falls back to the directory's own basename.
    """
    if not project_dir:
        return ""
    normalized = project_dir.rstrip("/")
    basename = os.path.basename(normalized)
    if basename.lower() not in _GENERIC_SUBDIRS:
        return basename
    # Walk up looking for .git to find the project root
    path = normalized
    while True:
        parent = os.path.dirname(path)
        if parent == path:
            break
        if os.path.isdir(os.path.join(path, ".git")):
            return os.path.basename(path)
        path = parent
    # No git root found — use the immediate parent name if available
    parent_name = os.path.basename(os.path.dirname(normalized))
    return parent_name if parent_name else basename


def _resolve_ctx_project_name(project_dir):
    """Resolve ctx project name from a project directory path.

    First looks up the sessions table by work_dir (exact match, then parent
    directories). Falls back to _smart_project_name.
    """
    if not project_dir or not os.path.exists(CTX_DB):
        return _smart_project_name(project_dir) if project_dir else None
    normalized = project_dir.rstrip("/")
    try:
        db = sqlite3.connect(CTX_DB)
        # Walk up from project_dir: first exact match, then parent dirs
        path = normalized
        while True:
            row = db.execute(
                "SELECT name FROM sessions WHERE RTRIM(work_dir, '/') = ?",
                (path,),
            ).fetchone()
            if row:
                db.close()
                return row[0]
            parent = os.path.dirname(path)
            if parent == path:
                break
            path = parent
        db.close()
    except sqlite3.Error:
        pass
    return _smart_project_name(normalized)


def _is_ctx_project_registered(project_name):
    """Check if a ctx project is already registered in the database."""
    if not os.path.exists(CTX_DB):
        return False
    try:
        db = sqlite3.connect(CTX_DB)
        row = db.execute(
            "SELECT 1 FROM sessions WHERE name = ?", (project_name,)
        ).fetchone()
        db.close()
        return row is not None
    except sqlite3.Error:
        return False


def _collect_claude_log(tab):
    """Collect the Claude Code session JSONL into project's claude_log/ directory on tab close."""
    claude_config = getattr(tab, "claude_config", None)
    if not claude_config:
        return
    project_dir = claude_config.get("project_dir", "")
    if not project_dir or not os.path.isdir(project_dir):
        return
    stats_bar = getattr(tab, "_stats_bar", None)
    jsonl_path = None
    if stats_bar and getattr(stats_bar, "_reader", None):
        jsonl_path = stats_bar._reader._cached
    cmd = ["claude_log", "collect", project_dir]
    if jsonl_path:
        cmd.append(jsonl_path)
    try:
        subprocess.Popen(cmd, stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
    except FileNotFoundError:
        pass


def _is_ctx_available():
    """Check if ctx command is available."""
    try:
        subprocess.run(["ctx", "--version"], capture_output=True, timeout=5)
        return True
    except (FileNotFoundError, subprocess.TimeoutExpired):
        return False


def _run_ctx_wizard_if_needed(parent, data):
    """Launch ctx wizard if project_dir is set but ctx not registered. Returns updated data."""
    project_dir = data.get("project_dir", "")
    if not project_dir or not _is_ctx_available():
        return data
    project_name = _smart_project_name(project_dir)
    if _is_ctx_project_registered(project_name):
        return data
    wizard = CtxSetupWizard(parent, project_dir)
    wizard.run_wizard()
    return data


_WIZARD_BACK = 1
_WIZARD_NEXT = 2


class CtxSetupWizard(Gtk.Dialog):
    """Step-by-step wizard for initial ctx project setup."""

    def __init__(self, parent, project_dir):
        super().__init__(
            title="Ctx — New Project Setup",
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.set_default_size(540, -1)
        self.set_resizable(False)
        self.project_dir = project_dir
        self.project_name = _smart_project_name(project_dir)
        self.success = False
        self.result_prompt = ""
        self._current_page = 0

        box = self.get_content_area()
        box.set_border_width(16)
        box.set_spacing(12)

        # Page header
        self.lbl_header = Gtk.Label(xalign=0)
        box.pack_start(self.lbl_header, False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 0)

        # Stack for pages
        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        box.pack_start(self.stack, True, True, 0)

        # Status bar (for errors)
        self.lbl_status = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
        box.pack_start(self.lbl_status, False, False, 0)

        self._build_page_project()
        self._build_page_entry()
        self._build_page_confirm()

        # Navigation buttons
        self.btn_cancel = self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.btn_back = self.add_button("\u2190 Back", _WIZARD_BACK)
        self.btn_next = self.add_button("Next \u2192", _WIZARD_NEXT)
        self.btn_finish = self.add_button("\u2713 Create", Gtk.ResponseType.OK)

        self._show_page(0)
        self.show_all()

    def _build_page_project(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        info = Gtk.Label(wrap=True, xalign=0, max_width_chars=58)
        info.set_markup(
            "Register the project in the ctx database.\n"
            "The <b>project name</b> is used in all ctx commands "
            "(e.g. <tt>ctx get MyProject</tt>).\n"
            "<b>Description</b> helps Claude understand the project purpose."
        )
        page.pack_start(info, False, False, 0)

        warn = Gtk.Label(wrap=True, xalign=0, max_width_chars=58)
        warn.set_markup(
            '<small>\u26a0 Case matters! "<tt>MyProject</tt>" \u2260 '
            '"<tt>myproject</tt>". The name must match exactly in all commands.</small>'
        )
        page.pack_start(warn, False, False, 4)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)

        grid.attach(Gtk.Label(label="Directory:", halign=Gtk.Align.END), 0, 0, 1, 1)
        lbl_dir = Gtk.Label(
            label=self.project_dir, halign=Gtk.Align.START,
            selectable=True, ellipsize=Pango.EllipsizeMode.MIDDLE,
        )
        grid.attach(lbl_dir, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Project name:", halign=Gtk.Align.END), 0, 1, 1, 1)
        self.w_name = Gtk.Entry(hexpand=True)
        self.w_name.set_text(self.project_name)
        grid.attach(self.w_name, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="Description:", halign=Gtk.Align.END), 0, 2, 1, 1)
        self.w_desc = Gtk.Entry(hexpand=True)
        self.w_desc.set_text(_detect_project_description(self.project_dir))
        grid.attach(self.w_desc, 1, 2, 1, 1)

        page.pack_start(grid, False, False, 0)
        self.stack.add_named(page, "project")

    def _build_page_entry(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        info = Gtk.Label(wrap=True, xalign=0, max_width_chars=58)
        info.set_markup(
            "Add the <b>first context entry</b>. Claude reads these at the start "
            "of each session to understand the project.\n\n"
            "Examples:\n"
            '  Key: <tt>repo</tt>  Value: <tt>GitHub: .../MyRepo, branch: main</tt>\n'
            '  Key: <tt>stack</tt>  Value: <tt>Python 3.12, Flask, PostgreSQL</tt>'
        )
        page.pack_start(info, False, False, 0)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)

        grid.attach(Gtk.Label(label="Key:", halign=Gtk.Align.END), 0, 0, 1, 1)
        self.w_key = Gtk.Entry(hexpand=True)
        self.w_key.set_text("init")
        grid.attach(self.w_key, 1, 0, 1, 1)

        grid.attach(
            Gtk.Label(label="Value:", halign=Gtk.Align.END, valign=Gtk.Align.START),
            0, 1, 1, 1,
        )
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(90)
        self.w_value = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
        self.w_value.get_buffer().set_text(
            "Kontekst projektu nie został jeszcze zebrany. "
            "Zbierz kontekst w trakcie pracy i zapisuj ważne odkrycia: "
            "ctx set <project> <key> <value>"
        )
        scrolled.add(self.w_value)
        grid.attach(scrolled, 1, 1, 1, 1)

        page.pack_start(grid, True, True, 0)
        self.stack.add_named(page, "entry")

    def _build_page_confirm(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        info = Gtk.Label(wrap=True, xalign=0, max_width_chars=58)
        info.set_text("Review and confirm. The following actions will be performed:")
        page.pack_start(info, False, False, 0)

        self.lbl_summary = Gtk.Label(wrap=True, xalign=0, max_width_chars=58)
        page.pack_start(self.lbl_summary, False, False, 0)

        page.pack_start(Gtk.Separator(), False, False, 4)
        self.stack.add_named(page, "confirm")

    def _show_page(self, idx):
        self._current_page = idx
        pages = ["project", "entry", "confirm"]
        self.stack.set_visible_child_name(pages[idx])
        self.lbl_status.set_text("")

        headers = [
            "Step 1 of 3: Project registration",
            "Step 2 of 3: First context entry",
            "Step 3 of 3: Confirm and create",
        ]
        self.lbl_header.set_markup(f"<b>{headers[idx]}</b>")

        if idx == 2:
            self._update_summary()

    def _update_buttons(self):
        idx = self._current_page
        self.btn_back.set_visible(idx > 0)
        self.btn_next.set_visible(idx < 2)
        self.btn_finish.set_visible(idx == 2)

    def _update_summary(self):
        name = self.w_name.get_text().strip()
        desc = self.w_desc.get_text().strip()
        key = self.w_key.get_text().strip()
        buf = self.w_value.get_buffer()
        s, e = buf.get_bounds()
        value = buf.get_text(s, e, False).strip()
        val_preview = value[:150] + ("\u2026" if len(value) > 150 else "")

        self.lbl_summary.set_markup(
            f"<tt>1.</tt> <tt>ctx init</tt> \u2014 register project "
            f"<b>{GLib.markup_escape_text(name)}</b>\n"
            f"     {GLib.markup_escape_text(desc)}\n\n"
            f"<tt>2.</tt> <tt>ctx set</tt> \u2014 add entry "
            f"<b>{GLib.markup_escape_text(key)}</b>\n"
            f"     {GLib.markup_escape_text(val_preview)}\n\n"
            f"<tt>3.</tt> Create <tt>CLAUDE.md</tt> in project directory\n"
            f"     (will be skipped if file already exists)"
        )

    def _validate_page(self, idx):
        if idx == 0:
            name = self.w_name.get_text().strip()
            desc = self.w_desc.get_text().strip()
            if not name:
                self.lbl_status.set_markup(
                    '<span foreground="red">Project name is required.</span>'
                )
                self.w_name.grab_focus()
                return False
            if not desc:
                self.lbl_status.set_markup(
                    '<span foreground="red">Description is required.</span>'
                )
                self.w_desc.grab_focus()
                return False
        elif idx == 1:
            key = self.w_key.get_text().strip()
            buf = self.w_value.get_buffer()
            s, e = buf.get_bounds()
            value = buf.get_text(s, e, False).strip()
            if not key:
                self.lbl_status.set_markup(
                    '<span foreground="red">Key is required. '
                    'E.g. "repo", "stack", "notes".</span>'
                )
                self.w_key.grab_focus()
                return False
            if not value:
                self.lbl_status.set_markup(
                    '<span foreground="red">Value is required. '
                    "Describe something about the project.</span>"
                )
                self.w_value.grab_focus()
                return False
        return True

    def _execute(self):
        """Run ctx init, ctx set, and create CLAUDE.md."""
        name = self.w_name.get_text().strip()
        desc = self.w_desc.get_text().strip()
        key = self.w_key.get_text().strip()
        buf = self.w_value.get_buffer()
        s, e = buf.get_bounds()
        value = buf.get_text(s, e, False).strip()

        # 1. ctx init
        try:
            r = subprocess.run(
                ["ctx", "init", name, desc, self.project_dir],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                self.lbl_status.set_markup(
                    f'<span foreground="red">ctx init failed: '
                    f"{GLib.markup_escape_text(r.stderr.strip())}</span>"
                )
                return False
        except FileNotFoundError:
            self.lbl_status.set_markup(
                '<span foreground="red">ctx command not found.</span>'
            )
            return False

        # 2. ctx set
        try:
            r = subprocess.run(
                ["ctx", "set", name, key, value],
                capture_output=True, text=True,
            )
            if r.returncode != 0:
                self.lbl_status.set_markup(
                    f'<span foreground="red">ctx set failed: '
                    f"{GLib.markup_escape_text(r.stderr.strip())}</span>"
                )
                return False
        except FileNotFoundError:
            return False

        # 3. CLAUDE.md
        claude_md = os.path.join(self.project_dir, "CLAUDE.md")
        if not os.path.exists(claude_md):
            try:
                with open(claude_md, "w") as f:
                    f.write(
                        f"# {name}\n\n"
                        f"Context is loaded automatically via intro prompt. No need to run `ctx get` manually.\n\n"
                        f"During work:\n"
                        f"- Save important discoveries: `ctx set {name} <key> <value>`\n"
                        f"- Append to existing: `ctx append {name} <key> <value>`\n"
                        f'- Before ending session: `ctx summary {name} "<what was done>"`\n'
                        f"\n"
                        f"## Consult & Tribunal (CLI tools)\n\n"
                        f"Konsultacje z zewnętrznymi modelami AI: `consult \"pytanie\"`\n"
                        f"Konkretny model: `consult -m <model_id> \"pytanie\"` — ZAWSZE najpierw sprawdź dostępne modele: `consult models`\n"
                        f"Nazwy modeli to PEŁNE ID z prefixem providera, np. `google/gemini-2.5-pro`, `openai/gpt-5-codex`, `deepseek/deepseek-r1` — NIE skracaj.\n"
                        f"Dołączanie pliku jako kontekst: `consult -f plik.py \"pytanie\"`\n"
                        f"Tribunal — debata wielu modeli AI: `consult debate \"problem\"`\n"
                        f"  Kontekst pliku: `consult debate -f plik.py \"problem\"`\n"
                        f"  Domyślne role: `--analyst claude-code/opus --arbiter claude-code/opus`\n"
                        f"  Advocate i Critic dobieraj wg potrzeb spośród: `openai/gpt-5-codex`, `deepseek/deepseek-r1`, `google/gemini-2.5-pro`\n"
                        f'  Przykład: `consult debate "problem" --analyst claude-code/opus --advocate openai/gpt-5-codex --critic deepseek/deepseek-r1 --arbiter claude-code/opus`\n'
                        f"\n"
                        f"## Task management (CLI tool)\n\n"
                        f"IMPORTANT: Use the `tasks` CLI tool via Bash — NOT the built-in TaskCreate/TaskUpdate/TaskList tools.\n"
                        f"The built-in task tools are a different system. Always use `tasks` in Bash.\n\n"
                        f"```bash\n"
                        f"tasks list {name}                           # show all tasks\n"
                        f"tasks context {name}                        # show tasks + next task instructions\n"
                        f'tasks add {name} "description"              # add a task\n'
                        f"tasks done {name} <task_id>                 # mark task as done\n"
                        f"tasks --help                                # full help\n"
                        f"```\n\n"
                        f"Do NOT pick up tasks on your own. Only execute tasks when the auto-trigger system sends you a command.\n"
                    )
            except IOError as e:
                self.lbl_status.set_markup(
                    f'<span foreground="red">CLAUDE.md: {GLib.markup_escape_text(str(e))}</span>'
                )
                return False

        self.project_name = name
        self.result_prompt = _build_intro_prompt(name)
        self.success = True
        return True

    def run_wizard(self):
        """Run the wizard. Returns True if completed successfully."""
        while True:
            self._update_buttons()
            resp = self.run()
            if resp == _WIZARD_NEXT:
                if self._validate_page(self._current_page):
                    self._show_page(self._current_page + 1)
            elif resp == _WIZARD_BACK:
                self._show_page(self._current_page - 1)
            elif resp == Gtk.ResponseType.OK:
                if self._execute():
                    self.destroy()
                    return True
            else:
                self.destroy()
                return False


class _CtxEntryDialog(Gtk.Dialog):
    """Small dialog for adding/editing a ctx key-value entry."""

    def __init__(self, parent, title, key="", value=""):
        super().__init__(
            title=title,
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        self.set_default_size(400, -1)
        self.set_default_response(Gtk.ResponseType.OK)

        box = self.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        box.pack_start(grid, True, True, 0)

        grid.attach(Gtk.Label(label="Key:", halign=Gtk.Align.END), 0, 0, 1, 1)
        self.entry_key = Gtk.Entry(hexpand=True)
        self.entry_key.set_text(key)
        grid.attach(self.entry_key, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Value:", halign=Gtk.Align.END, valign=Gtk.Align.START), 0, 1, 1, 1)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(100)
        self.textview = Gtk.TextView()
        self.textview.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        if value:
            self.textview.get_buffer().set_text(value)
        scrolled.add(self.textview)
        grid.attach(scrolled, 1, 1, 1, 1)

        self.show_all()

    def get_data(self):
        key = self.entry_key.get_text().strip()
        buf = self.textview.get_buffer()
        start, end = buf.get_bounds()
        value = buf.get_text(start, end, False).strip()
        return key, value


class _CtxProjectDialog(Gtk.Dialog):
    """Dialog for adding/editing a ctx project."""

    def __init__(self, parent, title="New Project", name="", description="", work_dir=""):
        super().__init__(
            title=title,
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        self.set_default_size(450, -1)
        self.set_default_response(Gtk.ResponseType.OK)

        box = self.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)

        grid = Gtk.Grid(column_spacing=12, row_spacing=8)
        box.pack_start(grid, True, True, 0)

        grid.attach(Gtk.Label(label="Name:", halign=Gtk.Align.END), 0, 0, 1, 1)
        self.entry_name = Gtk.Entry(hexpand=True)
        self.entry_name.set_text(name)
        grid.attach(self.entry_name, 1, 0, 1, 1)

        grid.attach(Gtk.Label(label="Description:", halign=Gtk.Align.END), 0, 1, 1, 1)
        self.entry_desc = Gtk.Entry(hexpand=True)
        self.entry_desc.set_text(description)
        grid.attach(self.entry_desc, 1, 1, 1, 1)

        grid.attach(Gtk.Label(label="Directory:", halign=Gtk.Align.END), 0, 2, 1, 1)
        dir_box = Gtk.Box(spacing=4)
        self.entry_dir = Gtk.Entry(hexpand=True)
        self.entry_dir.set_text(work_dir)
        self.entry_dir.set_placeholder_text("(optional) path to project directory")
        dir_box.pack_start(self.entry_dir, True, True, 0)
        btn_browse = Gtk.Button(label="Browse\u2026")
        btn_browse.connect("clicked", self._on_browse)
        dir_box.pack_start(btn_browse, False, False, 0)
        grid.attach(dir_box, 1, 2, 1, 1)

        self.show_all()

    def _on_browse(self, button):
        dlg = Gtk.FileChooserDialog(
            title="Select directory",
            parent=self,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        if dlg.run() == Gtk.ResponseType.OK:
            path = dlg.get_filename()
            self.entry_dir.set_text(path)
            if not self.entry_name.get_text().strip():
                self.entry_name.set_text(os.path.basename(path.rstrip("/")))
        dlg.destroy()

    def get_data(self):
        return (
            self.entry_name.get_text().strip(),
            self.entry_desc.get_text().strip(),
            self.entry_dir.get_text().strip(),
        )


class CtxEditDialog(Gtk.Dialog):
    """Dialog to view and edit ctx project entries."""

    def __init__(self, parent, ctx_project, project_dir=""):
        super().__init__(
            title=f"Ctx: {ctx_project}",
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.add_buttons(Gtk.STOCK_CLOSE, Gtk.ResponseType.CLOSE)
        self.set_default_size(550, 400)
        self.ctx_project = ctx_project
        self.project_dir = project_dir

        box = self.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)

        # Description
        desc_box = Gtk.Box(spacing=8)
        desc_box.pack_start(Gtk.Label(label="Description:"), False, False, 0)
        self.entry_desc = Gtk.Entry(hexpand=True)
        desc_box.pack_start(self.entry_desc, True, True, 0)
        btn_save_desc = Gtk.Button(label="Save")
        btn_save_desc.connect("clicked", self._on_save_desc)
        desc_box.pack_start(btn_save_desc, False, False, 0)
        box.pack_start(desc_box, False, False, 0)

        box.pack_start(Gtk.Separator(), False, False, 2)

        # Entries list
        self.store = Gtk.ListStore(str, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)

        renderer_key = Gtk.CellRendererText()
        col_key = Gtk.TreeViewColumn("Key", renderer_key, text=0)
        col_key.set_min_width(120)
        self.tree.append_column(col_key)

        renderer_val = Gtk.CellRendererText()
        renderer_val.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_val = Gtk.TreeViewColumn("Value", renderer_val, text=1)
        col_val.set_expand(True)
        self.tree.append_column(col_val)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.tree)
        box.pack_start(scrolled, True, True, 0)

        # Buttons
        btn_box = Gtk.Box(spacing=4)
        for label_text, cb in [("Add", self._on_add), ("Edit", self._on_edit), ("Delete", self._on_delete)]:
            btn = Gtk.Button(label=label_text)
            btn.connect("clicked", cb)
            btn_box.pack_start(btn, True, True, 0)
        box.pack_start(btn_box, False, False, 0)

        self._load_data()
        self.show_all()

    def _load_data(self):
        self.store.clear()
        if not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row
        session = db.execute(
            "SELECT description FROM sessions WHERE name = ?", (self.ctx_project,)
        ).fetchone()
        if session:
            self.entry_desc.set_text(session["description"] or "")
        entries = db.execute(
            "SELECT key, value FROM contexts WHERE project = ? ORDER BY key",
            (self.ctx_project,),
        ).fetchall()
        for row in entries:
            self.store.append([row["key"], row["value"]])
        db.close()

    def _on_save_desc(self, button):
        desc = self.entry_desc.get_text().strip()
        if desc:
            subprocess.run(
                ["ctx", "init", self.ctx_project, desc, self.project_dir],
                capture_output=True, text=True,
            )

    def _on_add(self, button):
        dlg = _CtxEntryDialog(self, "Add entry")
        if dlg.run() == Gtk.ResponseType.OK:
            key, value = dlg.get_data()
            if key:
                subprocess.run(
                    ["ctx", "set", self.ctx_project, key, value],
                    capture_output=True, text=True,
                )
                self._load_data()
        dlg.destroy()

    def _on_edit(self, button):
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        if it is None:
            return
        old_key = model.get_value(it, 0)
        old_value = model.get_value(it, 1)
        dlg = _CtxEntryDialog(self, "Edit entry", old_key, old_value)
        if dlg.run() == Gtk.ResponseType.OK:
            key, value = dlg.get_data()
            if key:
                if key != old_key:
                    subprocess.run(
                        ["ctx", "delete", self.ctx_project, old_key],
                        capture_output=True, text=True,
                    )
                subprocess.run(
                    ["ctx", "set", self.ctx_project, key, value],
                    capture_output=True, text=True,
                )
                self._load_data()
        dlg.destroy()

    def _on_delete(self, button):
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        if it is None:
            return
        key = model.get_value(it, 0)
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f'Delete entry "{key}"?',
        )
        if dlg.run() == Gtk.ResponseType.YES:
            subprocess.run(
                ["ctx", "delete", self.ctx_project, key],
                capture_output=True, text=True,
            )
            self._load_data()
        dlg.destroy()


# ─── Claude plan usage cache (shared across all tabs) ────────────────────────

import glob
import time as _time_mod
from datetime import datetime, timezone

_CLAUDE_CREDENTIALS_FILE = os.path.expanduser("~/.claude/.credentials.json")
_CLAUDE_USAGE_API = "https://api.anthropic.com/api/oauth/usage"
_CLAUDE_OAUTH_BETA = "oauth-2025-04-20"

# Module-level cache: {data: dict|None, fetched_at: float}
_usage_cache = {"data": None, "fetched_at": 0.0, "fetching": False}
_USAGE_TTL = 60.0  # seconds


def _get_claude_oauth_token():
    """Read OAuth access token from Claude credentials file."""
    try:
        with open(_CLAUDE_CREDENTIALS_FILE, encoding="utf-8") as fh:
            creds = json.load(fh)
        oauth = creds.get("claudeAiOauth", {})
        token = oauth.get("accessToken")
        expires = oauth.get("expiresAt", 0)
        if token and expires > _time_mod.time() * 1000:
            return token
    except (OSError, json.JSONDecodeError, KeyError):
        pass
    return None


def _fetch_claude_usage():
    """Fetch usage data from Claude API. Returns dict or None on failure."""
    token = _get_claude_oauth_token()
    if not token:
        return None
    req = urllib.request.Request(
        _CLAUDE_USAGE_API,
        headers={
            "Content-Type": "application/json",
            "User-Agent": "claude-code/2.1.90",
            "Authorization": f"Bearer {token}",
            "anthropic-beta": _CLAUDE_OAUTH_BETA,
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode())
            if "error" not in data:
                return data
    except Exception:
        pass
    return None


def _refresh_usage_cache():
    """Fetch usage in background thread and update cache."""
    if _usage_cache["fetching"]:
        return
    _usage_cache["fetching"] = True

    def _bg():
        data = _fetch_claude_usage()
        _usage_cache["fetching"] = False
        if data is not None:
            _usage_cache["data"] = data
            _usage_cache["fetched_at"] = _time_mod.time()

    threading.Thread(target=_bg, daemon=True).start()


def _get_usage_cache():
    """Return cached usage data, triggering background refresh if stale."""
    age = _time_mod.time() - _usage_cache["fetched_at"]
    if age > _USAGE_TTL:
        _refresh_usage_cache()
    return _usage_cache["data"]


def _fmt_reset_time(resets_at):
    """Format reset time as human-readable relative string.

    *resets_at* can be a Unix epoch (int/float) or an ISO-8601 string.
    """
    if isinstance(resets_at, str):
        try:
            dt = datetime.fromisoformat(resets_at)
            epoch = dt.timestamp()
        except (ValueError, TypeError):
            return "?"
    else:
        epoch = float(resets_at)
    diff = epoch - _time_mod.time()
    if diff <= 0:
        return "now"
    if diff < 3600:
        return f"{int(diff / 60)}min"
    hours = diff / 3600
    if hours < 24:
        return f"{hours:.1f}h"
    return f"{hours / 24:.1f}d"


# ─── SessionStatsBar (Claude Code session metrics) ───────────────────────────

_STATS_PRICING = {
    "claude-opus-4-6":   {"input": 15.0,  "output": 75.0,  "cache_read": 1.50,  "cache_write": 18.75},
    "claude-sonnet-4-6": {"input":  3.0,  "output": 15.0,  "cache_read": 0.30,  "cache_write":  3.75},
    "claude-haiku-4-5":  {"input":  0.80, "output":  4.0,  "cache_read": 0.08,  "cache_write":  1.00},
    "claude-opus-4-5":   {"input": 15.0,  "output": 75.0,  "cache_read": 1.50,  "cache_write": 18.75},
    "claude-sonnet-4-5": {"input":  3.0,  "output": 15.0,  "cache_read": 0.30,  "cache_write":  3.75},
}
_STATS_DEFAULT_PRICE = {"input": 3.0, "output": 15.0, "cache_read": 0.30, "cache_write": 3.75}
_CLAUDE_PROJECTS_DIR = os.path.expanduser("~/.claude/projects")


def _fmt_tok(n):
    if n >= 1_000_000: return f"{n / 1_000_000:.1f}M"
    if n >= 1_000: return f"{n / 1_000:.1f}K"
    return str(n)


def _fmt_dur(seconds):
    s = int(seconds)
    h, rem = divmod(s, 3600)
    m, sec = divmod(rem, 60)
    return f"{h}h {m:02d}m" if h else f"{m}m {sec:02d}s"


class _SessionStatsReader:
    """Reads Claude Code JSONL session file for live token/cost stats."""

    def __init__(self, project_dir):
        self._project_dir = project_dir.rstrip("/")
        self._start = datetime.now(timezone.utc)
        self._cached = None

    def _find_file(self):
        if self._cached and os.path.isfile(self._cached):
            return self._cached
        key = re.sub(r'[^a-zA-Z0-9-]', '-', self._project_dir)
        files = glob.glob(os.path.join(_CLAUDE_PROJECTS_DIR, key, "*.jsonl"))
        if not files:
            return None
        start_epoch = self._start.timestamp()
        recent = [f for f in files if os.path.getmtime(f) >= start_epoch]
        if recent:
            self._cached = max(recent, key=os.path.getmtime)
            return self._cached
        # Current session's JSONL may not exist yet — return newest but don't cache
        return max(files, key=os.path.getmtime)

    def read(self):
        result = {"model": "", "input": 0, "output": 0, "cache_read": 0,
                  "cache_write": 0, "responses": 0, "first_ts": None, "last_ts": None}
        path = self._find_file()
        if not path:
            return result
        try:
            with open(path, encoding="utf-8", errors="replace") as fh:
                for line in fh:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        e = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    ts_str = e.get("timestamp", "")
                    if ts_str:
                        try:
                            ts = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                            if result["first_ts"] is None or ts < result["first_ts"]:
                                result["first_ts"] = ts
                            if result["last_ts"] is None or ts > result["last_ts"]:
                                result["last_ts"] = ts
                        except ValueError:
                            pass
                    msg = e.get("message", {})
                    if not isinstance(msg, dict):
                        continue
                    if msg.get("role") == "assistant":
                        result["responses"] += 1
                        if msg.get("model"):
                            result["model"] = msg["model"]
                    usage = msg.get("usage", {})
                    if usage:
                        result["input"] += usage.get("input_tokens", 0)
                        result["output"] += usage.get("output_tokens", 0)
                        result["cache_read"] += usage.get("cache_read_input_tokens", 0)
                        result["cache_write"] += usage.get("cache_creation_input_tokens", 0)
        except OSError:
            pass
        return result


class SessionStatsBar(Gtk.Box):
    """Thin status bar showing Claude Code session metrics."""

    def __init__(self, project_dir):
        super().__init__(orientation=Gtk.Orientation.HORIZONTAL, spacing=0)
        self._reader = _SessionStatsReader(project_dir)
        self._prompt_count = 0
        self._timer = 0

        self.set_size_request(-1, 44)

        style = self.get_style_context()
        style.add_class("stats-bar")

        self._labels = {}
        fields = [
            ("dur",      "⏱ --:--",     "Session duration"),
            ("s1",       " │ ",          None),
            ("prompts",  "💬 0",         "Prompts sent"),
            ("s2",       " │ ",          None),
            ("resp",     "🤖 0",         "Responses received"),
            ("s3",       " │ ",          None),
            ("tok_in",   "↑ 0",          "Input tokens (incl. cache writes)"),
            ("s3b",      " ",            None),
            ("tok_out",  "↓ 0",          "Output tokens"),
            ("s4",       " │ ",          None),
            ("cache",    "📦 0%",        "Cache hit rate"),
            ("s5",       " │ ",          None),
            ("cost",     "💰 $0.00",     "Estimated cost"),
            ("s6",       " │ ",          None),
            ("tok_h",    "⚡ 0 tok/h",   "Tokens per hour (throughput)"),
            ("s7",       " │ ",          None),
            ("model",    "",             "Model used"),
            ("s8",       " │ ",          None),
            ("usage_5h", "🔋 5h –",      "Plan usage: current session (5h window)"),
            ("s9",       " ",            None),
            ("usage_7d", "7d –",         "Plan usage: weekly (7d window)"),
        ]
        for key, text, tooltip in fields:
            lbl = Gtk.Label(label=text)
            lbl.set_margin_start(4)
            lbl.set_margin_end(2)
            lbl.set_margin_top(4)
            lbl.set_margin_bottom(4)
            if tooltip:
                lbl.set_tooltip_text(tooltip)
                lbl.set_has_tooltip(True)
            self._labels[key] = lbl
            self.pack_start(lbl, False, False, 0)

        self.show_all()
        self._timer = GLib.timeout_add(5000, self._update)

    def increment_prompt(self):
        self._prompt_count += 1

    def stop(self):
        if self._timer:
            GLib.source_remove(self._timer)
            self._timer = 0

    def _update(self):
        s = self._reader.read()
        M = 1_000_000
        price = _STATS_PRICING.get(s["model"], _STATS_DEFAULT_PRICE)
        cost = (s["input"] * price["input"] / M + s["output"] * price["output"] / M
                + s["cache_read"] * price["cache_read"] / M + s["cache_write"] * price["cache_write"] / M)
        dur = 0.0
        if s["first_ts"]:
            end = s["last_ts"] or datetime.now(timezone.utc)
            dur = (end - s["first_ts"]).total_seconds()
        total_tok = s["input"] + s["cache_write"] + s["cache_read"] + s["output"]
        tok_h = total_tok / (dur / 3600) if dur > 1 else 0
        total_in = s["input"] + s["cache_write"]
        cache_pct = int(s["cache_read"] / (total_in + s["cache_read"]) * 100) if (total_in + s["cache_read"]) else 0

        self._labels["dur"].set_text(f"⏱ {_fmt_dur(dur)}")
        self._labels["prompts"].set_text(f"💬 {self._prompt_count}")
        self._labels["resp"].set_text(f"🤖 {s['responses']}")
        self._labels["tok_in"].set_text(f"↑ {_fmt_tok(total_in)}")
        self._labels["tok_out"].set_text(f"↓ {_fmt_tok(s['output'])}")
        self._labels["cache"].set_text(f"📦 {cache_pct}%")
        self._labels["cost"].set_text(f"💰 ${cost:.4f}")
        self._labels["tok_h"].set_text(f"⚡ {_fmt_tok(int(tok_h))} tok/h")
        if s["model"]:
            self._labels["model"].set_text(s["model"].replace("claude-", "").replace("-2024", ""))

        usage = _get_usage_cache()
        for key, lbl_key in [("five_hour", "usage_5h"), ("seven_day", "usage_7d")]:
            prefix = "5h" if key == "five_hour" else "7d"
            info = usage.get(key) if usage else None
            icon = "🔋 " if key == "five_hour" else ""
            if not info:
                self._labels[lbl_key].set_text(f"{icon}{prefix} –")
                self._labels[lbl_key].set_tooltip_text(
                    "Plan usage: current session (5h window)" if key == "five_hour"
                    else "Plan usage: weekly (7d window)"
                )
            else:
                util = info.get("utilization", 0)
                # API returns percentage directly (e.g. 36.0 = 36%)
                pct = int(util) if util is not None else 0
                resets_at = info.get("resets_at")
                tip = f"{prefix}: {pct}% used"
                if resets_at:
                    tip += f" · resets in {_fmt_reset_time(resets_at)}"
                self._labels[lbl_key].set_text(f"{icon}{prefix} {pct}%")
                self._labels[lbl_key].set_tooltip_text(tip)

        return True


# ─── TerminalTab ──────────────────────────────────────────────────────────────


class TerminalTab(Gtk.Box):
    """Zakładka terminala — lokalny shell lub SSH."""

    def __init__(self, app, session=None, claude_config=None):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self.session = session
        self.claude_config = claude_config

        self.terminal = Vte.Terminal()
        self.terminal.set_font(Pango.FontDescription(FONT))
        self.terminal.set_scrollback_lines(SCROLLBACK_LINES)
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_audible_bell(False)

        # Catppuccin colors
        fg = _parse_color(CATPPUCCIN["text"])
        bg = _parse_color(CATPPUCCIN["base"])
        palette = [_parse_color(c) for c in TERMINAL_PALETTE]
        self.terminal.set_colors(fg, bg, palette)

        # Cursor color
        self.terminal.set_color_cursor(_parse_color(CATPPUCCIN["rosewater"]))
        self.terminal.set_color_cursor_foreground(_parse_color(CATPPUCCIN["crust"]))

        term_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        term_box.pack_start(self.terminal, True, True, 0)
        scrollbar = Gtk.Scrollbar(
            orientation=Gtk.Orientation.VERTICAL,
            adjustment=self.terminal.get_vadjustment(),
        )
        term_box.pack_start(scrollbar, False, False, 0)
        self.pack_start(term_box, True, True, 0)

        self.terminal.connect("child-exited", self._on_child_exited)
        self.terminal.connect("window-title-changed", self._on_title_changed)
        self.terminal.connect("key-press-event", self._on_key_press)
        self.terminal.connect("button-press-event", self._on_button_press)

        # Drag & drop — accept files, paste path into terminal
        self.terminal.drag_dest_set(
            Gtk.DestDefaults.ALL,
            [Gtk.TargetEntry.new("text/uri-list", 0, 0)],
            Gdk.DragAction.COPY,
        )
        self.terminal.connect("drag-data-received", self._on_terminal_drag_received)

        # Tab label references for in-place updates (avoid widget recreation)
        self._tab_label_box = None
        self._tab_label_widget = None
        self._tab_label_text = None
        self._pending_macro_timers = []

        # Auto-trigger for task list (Claude Code tabs only)
        self._task_idle_timer = None
        self._task_project = None
        self._task_session_id = str(uuid.uuid4())
        self._inject_pending = None  # (project, count, refresh_every) when rules inject is due
        self._stats_bar = None
        if claude_config:
            project_dir = claude_config.get("project_dir", "")
            if project_dir:
                self._task_project = _resolve_ctx_project_name(project_dir)
                self._stats_bar = SessionStatsBar(project_dir)
                self.pack_end(self._stats_bar, False, False, 0)
            self.terminal.connect("contents-changed", self._on_contents_changed_tasks)

        self.show_all()

        if claude_config:
            self.spawn_claude(claude_config)
        elif session:
            self.spawn_ssh(
                session["host"],
                session.get("port", 22),
                session["username"],
                session.get("key_file", ""),
            )
        else:
            self.spawn_local_shell()

    def spawn_local_shell(self):
        shell = _OPTIONS.get("shell") or os.environ.get("SHELL", "/bin/bash")
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.environ.get("HOME", "/"),
            [shell],
            None,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            None,
        )

    def spawn_ssh(self, host, port, username, key_file=""):
        argv = [SSH_PATH]
        if key_file:
            argv += ["-i", key_file]
        argv += ["-p", str(port), f"{username}@{host}"]

        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.environ.get("HOME", "/"),
            argv,
            None,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            None,
        )

    def spawn_claude(self, config):
        """Spawn Claude Code session — with sudo askpass helper or direct.

        Always runs inside bash so that when claude exits, the shell
        stays alive and the tab doesn't auto-close.
        """
        claude_path = CLAUDE_PATH or _find_claude_path()
        if not claude_path:
            work_dir = config.get("project_dir") or os.environ.get("HOME", "/")
            msg = (
                'printf "\\n\\033[1;31m━━━ Claude Code nie został znaleziony ━━━\\033[0m\\n\\n"\n'
                'printf "Sprawdzone lokalizacje:\\n"\n'
                'printf "  ~/.local/bin/claude\\n"\n'
                'printf "  ~/.npm-global/bin/claude\\n"\n'
                'printf "  /usr/local/bin/claude\\n"\n'
                'printf "  /usr/bin/claude\\n"\n'
                'printf "  /opt/homebrew/bin/claude\\n"\n'
                'printf "  ~/.nvm/versions/node/*/bin/claude\\n\\n"\n'
                'printf "Aby naprawić:\\n"\n'
                'printf "  1. Uruchom instalator ponownie: ./install.sh\\n"\n'
                'printf "  2. Lub zainstaluj ręcznie: npm install -g @anthropic-ai/claude-code\\n"\n'
                'printf "  3. Upewnij się, że ~/.npm-global/bin jest w PATH (~/.bashrc)\\n\\n"\n'
                'exec bash\n'
            )
            self.terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                work_dir,
                ["/bin/bash", "-c", msg],
                None,
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                -1,
                None,
                None,
            )
            return

        flags = []
        if config.get("resume"):
            flags.append("--resume")
        if config.get("skip_permissions"):
            flags.append("--dangerously-skip-permissions")

        custom_prompt = config.get("prompt", "")
        project_dir = config.get("project_dir", "")
        # Build prompt: always start with fresh intro, then append custom part
        if project_dir:
            project_name = _resolve_ctx_project_name(project_dir)
            prompt = _build_intro_prompt(project_name)
            if custom_prompt:
                prompt += "\n\n" + custom_prompt
        else:
            prompt = custom_prompt
        # Inject plugin session context
        for plugin in self.app._plugins.values():
            try:
                ctx = plugin.get_session_context()
                if ctx:
                    prompt = (prompt + "\n\n" + ctx) if prompt else ctx
            except Exception:
                pass
        prompt_arg = ""
        if prompt:
            escaped = prompt.replace("'", "'\\''")
            prompt_arg = f" '{escaped}'"

        flags_str = " ".join(flags)

        if config.get("sudo"):
            script = (
                'while true; do\n'
                '  read -rsp "Podaj hasło sudo: " SUDO_PW\n'
                '  echo\n'
                '  ASKPASS=$(mktemp /tmp/claude-askpass.XXXXXX)\n'
                '  chmod 700 "$ASKPASS"\n'
                '  printf \'#!/bin/bash\\necho "\'"%s"\'"\\n\' "$SUDO_PW" > "$ASKPASS"\n'
                '  export SUDO_ASKPASS="$ASKPASS"\n'
                '  if sudo -A true 2>/dev/null; then\n'
                '    unset SUDO_PW\n'
                '    break\n'
                '  fi\n'
                '  rm -f "$ASKPASS"\n'
                '  unset SUDO_PW\n'
                '  echo "Błędne hasło. Spróbuj ponownie."\n'
                'done\n'
                'trap \'rm -f "$ASKPASS"\' EXIT\n'
                f'{claude_path} {flags_str}{prompt_arg}\n'
                'exec bash\n'
            )
        else:
            script = f'{claude_path} {flags_str}{prompt_arg}\nexec bash\n'

        work_dir = config.get("project_dir") or os.environ.get("HOME", "/")
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            work_dir,
            ["/bin/bash", "-c", script],
            None,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            None,
        )

    def run_macro(self, macro):
        """Execute macro steps chained via GLib.timeout_add."""
        steps = macro.get("steps", [])
        if not steps:
            return

        def execute_steps(step_index):
            if step_index >= len(steps):
                return False
            step = steps[step_index]
            if step["type"] == "text":
                self.terminal.feed_child(step["value"].encode())
                GLib.timeout_add(50, execute_steps, step_index + 1)
            elif step["type"] == "key":
                key_str = KEY_MAP.get(step["value"], "")
                if key_str:
                    self.terminal.feed_child(key_str.encode())
                GLib.timeout_add(50, execute_steps, step_index + 1)
            elif step["type"] == "delay":
                GLib.timeout_add(int(step["value"]), execute_steps, step_index + 1)
            return False

        GLib.timeout_add(500, execute_steps, 0)

    def _on_key_press(self, terminal, event):
        mod = event.state & Gtk.accelerator_get_default_mod_mask()
        ctrl = Gdk.ModifierType.CONTROL_MASK
        shift = Gdk.ModifierType.SHIFT_MASK

        # Ctrl+Shift+C: copy
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_C, Gdk.KEY_c):
            if terminal.get_has_selection():
                terminal.copy_clipboard_format(Vte.Format.TEXT)
            return True

        # Ctrl+Shift+V: paste (clipboard image → save & paste path, else text)
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_V, Gdk.KEY_v):
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            if clipboard.wait_is_image_available():
                if self._paste_clipboard_image_path():
                    return True
            terminal.paste_clipboard()
            return True

        # Ctrl+T: new tab (forward to app)
        if mod == ctrl and event.keyval == Gdk.KEY_t:
            self.app.add_local_tab()
            return True

        # Ctrl+Shift+W: close tab
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_W, Gdk.KEY_w):
            self.app.close_tab(self)
            return True

        # Ctrl+PageUp/PageDown: switch tabs
        if mod == ctrl and event.keyval == Gdk.KEY_Page_Up:
            idx = self.app.notebook.get_current_page()
            if idx > 0:
                self.app.notebook.set_current_page(idx - 1)
            return True
        if mod == ctrl and event.keyval == Gdk.KEY_Page_Down:
            idx = self.app.notebook.get_current_page()
            if idx < self.app.notebook.get_n_pages() - 1:
                self.app.notebook.set_current_page(idx + 1)
            return True

        # Ctrl+G: toggle git panel (only for Claude Code tabs)
        if mod == ctrl and event.keyval == Gdk.KEY_g:
            if self.claude_config:
                self.app.toggle_git_panel()
            return True

        # Track Enter key for prompt counter (Claude Code sessions)
        if self._stats_bar and event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._stats_bar.increment_prompt()
            self._maybe_inject_rules()

        return False

    def _maybe_inject_rules(self):
        """After each prompt, check if it's time to inject rules or refresh CTX.

        Sets a pending flag — actual injection happens after Claude goes idle,
        so the message arrives at the next free prompt (not mid-processing).
        """
        if not self._stats_bar or not self.claude_config:
            return
        project_dir = self.claude_config.get("project_dir", "")
        if not project_dir:
            return
        project = self._task_project or os.path.basename(project_dir.rstrip("/"))
        count = self._stats_bar._prompt_count

        inject_every = 100
        refresh_every = 200
        try:
            if os.path.exists(CTX_DB):
                db = sqlite3.connect(CTX_DB)
                row = db.execute(
                    "SELECT inject_every, refresh_every FROM rules_config WHERE project = ?",
                    (project,),
                ).fetchone()
                db.close()
                if row:
                    inject_every = row[0]
                    refresh_every = row[1]
        except Exception:
            pass

        if count > 0 and (count == inject_every or count % inject_every == 0):
            self._inject_pending = (project, count, refresh_every)
            import datetime
            with open("/tmp/bterminal_inject.log", "a") as f:
                f.write(f"{datetime.datetime.now()}: pending set project={project} count={count}\n")

    def _do_inject_rules(self, project, count, refresh_every):
        """Send rules block (and optionally CTX refresh) into the terminal.

        Called when Claude is idle, so the message arrives at the free prompt.
        """
        self._inject_pending = None
        try:
            result = subprocess.run(
                ["ctx", "rules", "inject", project],
                capture_output=True, text=True, timeout=5,
            )
            project_block = result.stdout.strip()
        except Exception:
            project_block = ""

        global_rules = _read_global_rules()

        if not project_block and not global_rules:
            return

        readme_path = Path(__file__).parent / "README.md"
        readme_hint = f" README: {readme_path}" if readme_path.exists() else ""
        header = (f"Przypomnienie: pracujesz w środowisku BTerminal "
                  f"(ctx, consult, tasks, memory_wizard, skills).{readme_hint}")

        parts = [header]
        if global_rules:
            parts.append("--- Reguły globalne (BTerminal defaults) ---\n" +
                         "\n".join(f"- {r}" for r in global_rules))
        if project_block:
            parts.append(project_block)
        parts.append("--- Narzędzia ---\n\n" + _tools_help(project))
        block = "\n\n".join(parts)

        if count % refresh_every == 0:
            try:
                ctx_result = subprocess.run(
                    ["ctx", "get", project, "--shared"],
                    capture_output=True, text=True, timeout=5,
                )
                ctx_block = ctx_result.stdout.strip()
                if ctx_block:
                    block = ctx_block + "\n\n" + block
            except Exception:
                pass

        import datetime
        with open("/tmp/bterminal_inject.log", "a") as f:
            f.write(f"{datetime.datetime.now()}: injecting {len(block)} chars for {project}\n")
        self.terminal.feed_child(block.encode())
        GLib.timeout_add(100, lambda: self.terminal.feed_child(b"\r") or False)

    def _on_button_press(self, terminal, event):
        if event.button == 3:  # right click
            menu = Gtk.Menu()

            item_copy = Gtk.MenuItem(label="Copy")
            item_copy.set_sensitive(terminal.get_has_selection())
            item_copy.connect("activate", lambda _: terminal.copy_clipboard_format(Vte.Format.TEXT))
            menu.append(item_copy)

            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            has_image = clipboard.wait_is_image_available()

            item_paste = Gtk.MenuItem(label="Paste")
            if has_image:
                item_paste.connect("activate",
                                   lambda _: self._paste_clipboard_image_path() or
                                   terminal.paste_clipboard())
            else:
                item_paste.connect("activate", lambda _: terminal.paste_clipboard())
            menu.append(item_paste)

            menu.append(Gtk.SeparatorMenuItem())

            item_select_all = Gtk.MenuItem(label="Select All")
            item_select_all.connect("activate", lambda _: terminal.select_all())
            menu.append(item_select_all)

            menu.append(Gtk.SeparatorMenuItem())

            item_paste_img = Gtk.MenuItem(label="Paste Image")
            item_paste_img.set_sensitive(_clipboard_has_image_or_path())
            item_paste_img.connect("activate", lambda _: self._on_paste_image_to_ctx())
            menu.append(item_paste_img)

            menu.show_all()
            menu.popup_at_pointer(event)
            return True
        return False

    def _on_child_exited(self, terminal, status):
        self.app.on_tab_child_exited(self)

    def _on_title_changed(self, terminal):
        title = terminal.get_window_title()
        if title:
            if self.session:
                # SSH tab: keep session name, show VTE title in window title only
                self.app.update_tab_title(self, self.session.get("name", "SSH"))
            elif self.claude_config:
                # Claude Code tab: keep decorated tab name with number + emoji
                display = getattr(self, "_claude_tab_display", self.claude_config.get("name", "Claude Code"))
                self.app.update_tab_title(self, display)
            else:
                self.app.update_tab_title(self, title)

    def _on_contents_changed_tasks(self, terminal):
        """Reset idle timer on every terminal content change (Claude tabs only)."""
        if self._task_idle_timer:
            GLib.source_remove(self._task_idle_timer)
        self._task_idle_timer = GLib.timeout_add_seconds(
            10, self._on_task_idle_timeout
        )

    def _on_task_idle_timeout(self):
        """Called when Claude has been idle for 10 seconds — check for pending tasks and rule injections."""
        self._task_idle_timer = None

        # Inject rules if due (only when no task is about to fire)
        if self._inject_pending:
            project, count, refresh_every = self._inject_pending
            self._do_inject_rules(project, count, refresh_every)
            return False

        if not self._task_project:
            return False
        try:
            if not os.path.exists(CTX_DB):
                return False
            db = sqlite3.connect(CTX_DB)
            db.row_factory = sqlite3.Row

            # Check autorun flag
            config = db.execute(
                "SELECT autorun FROM task_config WHERE project = ?",
                (self._task_project,),
            ).fetchone()
            if not config or not config["autorun"]:
                db.close()
                return False

            # Atomically find and claim next open unclaimed task
            task = self._claim_next_task(db, self._task_project, self._task_session_id)
            db.close()

            if not task:
                return False

            # Trigger: feed task instruction with specific claimed task
            message = (
                f"[AUTO-TRIGGER] Twoje przypisane zadanie: {task['task_id']} — {task['description']}\n"
                f"Sprawdź pełną listę: tasks context {self._task_project} --session {self._task_session_id}\n"
                f"MUSISZ oznaczyć po wykonaniu: tasks done {self._task_project} {task['task_id']} (w Bash). "
                f"Pętla auto-trigger kończy się DOPIERO gdy WSZYSTKIE zadania są zamknięte (done). "
                f"Jeśli nie oznaczysz — ta wiadomość będzie się powtarzać."
            )
            terminal = self.terminal
            terminal.feed_child(message.encode())
            GLib.timeout_add(100, lambda: terminal.feed_child(b"\r") or False)

            # Refresh task panel if visible
            if hasattr(self.app, "task_panel"):
                GLib.idle_add(self.app.task_panel.refresh)
        except Exception:
            pass
        return False

    @staticmethod
    def _claim_next_task(db, project, session_id):
        """Atomically find and claim the next open unclaimed task. Returns task dict or None."""
        # First check if this session already has a claimed open task
        existing = db.execute(
            """SELECT t.task_id, t.description FROM tasks t
               JOIN task_claims c ON c.project = t.project AND c.task_id = t.task_id
               WHERE t.project = ? AND c.session_id = ? AND t.status = 'open'
               ORDER BY t.task_id LIMIT 1""",
            (project, session_id),
        ).fetchone()
        if existing:
            return existing

        # Find next open task not claimed by anyone
        rows = db.execute(
            """SELECT t.task_id, t.description FROM tasks t
               LEFT JOIN task_claims c ON c.project = t.project AND c.task_id = t.task_id
               WHERE t.project = ? AND t.status = 'open' AND c.task_id IS NULL""",
            (project,),
        ).fetchall()
        if not rows:
            return None

        # Sort by task_id naturally and pick the first
        def _sort_key(task_id):
            parts = task_id.split(".")
            result = []
            for p in parts:
                try:
                    result.append((0, int(p), ""))
                except ValueError:
                    result.append((1, 0, p))
            return result

        rows_sorted = sorted(rows, key=lambda r: _sort_key(r["task_id"]))
        task = rows_sorted[0]

        # Claim it
        try:
            db.execute(
                "INSERT INTO task_claims (project, task_id, session_id) VALUES (?, ?, ?)",
                (project, task["task_id"], session_id),
            )
            db.commit()
            return task
        except sqlite3.IntegrityError:
            # Race condition — another session claimed it between SELECT and INSERT
            return None

    def _paste_clipboard_image_path(self):
        """Save clipboard image to project copied_images/ and paste path.
        Returns True on success, False if no image could be retrieved."""
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        pixbuf = clipboard.wait_for_image()

        # Fallback: try raw PNG data from clipboard targets
        if not pixbuf:
            sel_data = clipboard.wait_for_contents(Gdk.Atom.intern("image/png", False))
            if sel_data:
                raw = sel_data.get_data()
                if raw:
                    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                    try:
                        loader.write(raw)
                        loader.close()
                        pixbuf = loader.get_pixbuf()
                    except GLib.Error:
                        pixbuf = None

        if not pixbuf:
            return False

        # Determine target directory
        base_dir = None
        if self.claude_config:
            proj_dir = self.claude_config.get("project_dir", "")
            if proj_dir and os.path.isdir(proj_dir):
                base_dir = proj_dir
        if not base_dir:
            base_dir = os.path.expanduser("~")
        images_dir = os.path.join(base_dir, "copied_images")
        os.makedirs(images_dir, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:12]}.png"
        dest = os.path.join(images_dir, filename)
        pixbuf.savev(dest, "png", [], [])
        # Replace clipboard with path text and use native VTE paste
        clipboard.set_text(dest, -1)
        clipboard.store()
        self.terminal.paste_clipboard()
        # Also register in ctx if available
        project = self._detect_ctx_project()
        if project:
            _save_ctx_image(project, dest, original_name="clipboard.png")
            if hasattr(self.app, "ctx_panel"):
                self.app.ctx_panel.refresh()
        return True

    def _detect_ctx_project(self):
        """Auto-detect ctx project from tab config, or ask user."""
        if not os.path.exists(CTX_DB):
            return None
        # Try auto-detect from claude config
        if self.claude_config:
            proj_dir = self.claude_config.get("project_dir", "")
            if proj_dir:
                candidate = os.path.basename(proj_dir.rstrip("/"))
                db = sqlite3.connect(CTX_DB)
                exists = db.execute(
                    "SELECT 1 FROM sessions WHERE name = ?", (candidate,)
                ).fetchone()
                db.close()
                if exists:
                    return candidate
        # Fallback: show dialog
        db = sqlite3.connect(CTX_DB)
        projects = [
            r[0] for r in db.execute(
                "SELECT name FROM sessions ORDER BY name"
            ).fetchall()
        ]
        db.close()
        if not projects:
            return None
        dlg = Gtk.Dialog(
            title="Save Image to Project",
            transient_for=self.app,
            modal=True,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dlg.set_default_size(300, -1)
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)
        lbl = Gtk.Label(label="Select project for image:")
        lbl.set_xalign(0)
        box.pack_start(lbl, False, False, 0)
        combo = Gtk.ComboBoxText()
        for p in projects:
            combo.append_text(p)
        # Pre-select project matching current Claude session
        preselect = 0
        if self.claude_config:
            proj_dir = self.claude_config.get("project_dir", "")
            if proj_dir:
                basename = os.path.basename(proj_dir.rstrip("/"))
                for i, p in enumerate(projects):
                    if p == basename:
                        preselect = i
                        break
        combo.set_active(preselect)
        box.pack_start(combo, False, False, 0)
        dlg.show_all()
        project = None
        if dlg.run() == Gtk.ResponseType.OK:
            project = combo.get_active_text()
        dlg.destroy()
        return project

    def _on_terminal_drag_received(self, widget, context, x, y, data, info, time):
        """Handle files dropped onto terminal — paste file path."""
        uris = data.get_uris()
        if not uris:
            return
        paths = []
        for uri in uris:
            if uri.startswith("file://"):
                try:
                    path = GLib.filename_from_uri(uri)[0]
                    paths.append(path)
                except Exception:
                    pass
        if paths:
            text = " ".join(paths)
            self.terminal.feed_child(text.encode("utf-8"))

    def _on_paste_image_to_ctx(self):
        """Paste clipboard image (bitmap or file path) to a ctx project."""
        pixbuf, file_path = _clipboard_get_image_or_path()
        if not pixbuf and not file_path:
            return
        if not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        projects = [
            r[0] for r in db.execute(
                "SELECT name FROM sessions ORDER BY name"
            ).fetchall()
        ]
        db.close()
        if not projects:
            return

        dlg = Gtk.Dialog(
            title="Paste Image to Project",
            transient_for=self.app,
            modal=True,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dlg.set_default_size(300, -1)
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)
        lbl = Gtk.Label(label="Select project:")
        lbl.set_xalign(0)
        box.pack_start(lbl, False, False, 0)
        combo = Gtk.ComboBoxText()
        for p in projects:
            combo.append_text(p)
        # Pre-select project matching current Claude session
        preselect = 0
        if self.claude_config:
            proj_dir = self.claude_config.get("project_dir", "")
            if proj_dir:
                basename = os.path.basename(proj_dir.rstrip("/"))
                for i, p in enumerate(projects):
                    if p == basename:
                        preselect = i
                        break
        combo.set_active(preselect)
        box.pack_start(combo, False, False, 0)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            project = combo.get_active_text()
            if project:
                source = pixbuf if pixbuf else file_path
                _save_ctx_image(project, source)
                if hasattr(self.app, "ctx_panel"):
                    self.app.ctx_panel.refresh()
        dlg.destroy()

    def get_label(self):
        if self.claude_config:
            return getattr(self, "_claude_tab_display", self.claude_config.get("name", "Claude Code"))
        if self.session:
            return self.session.get("name", "SSH")
        return "Terminal"


# ─── SessionSidebar ───────────────────────────────────────────────────────────

# TreeStore columns
COL_ICON = 0
COL_NAME = 1
COL_ID = 2
COL_TOOLTIP = 3
COL_COLOR = 4
COL_WEIGHT = 5


class SessionSidebar(Gtk.Box):
    """Panel lewy z listą zapisanych sesji SSH."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app

        # Header
        header = Gtk.Label(label=f"  {APP_NAME} Sessions")
        header.set_halign(Gtk.Align.FILL)
        header.set_xalign(0)
        header.get_style_context().add_class("sidebar-header")
        self.pack_start(header, False, False, 0)

        # TreeView
        self.store = Gtk.TreeStore(str, str, str, str, str, int)  # icon, name, id, tooltip, color, weight
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(False)
        self.tree.set_tooltip_column(COL_TOOLTIP)
        self.tree.set_activate_on_single_click(False)

        # Renderer
        col = Gtk.TreeViewColumn()

        cell_icon = Gtk.CellRendererText()
        col.pack_start(cell_icon, False)
        col.add_attribute(cell_icon, "text", COL_ICON)

        cell_name = Gtk.CellRendererText()
        cell_name.set_property("ellipsize", Pango.EllipsizeMode.END)
        col.pack_start(cell_name, True)
        col.add_attribute(cell_name, "text", COL_NAME)
        col.add_attribute(cell_name, "foreground", COL_COLOR)
        col.add_attribute(cell_name, "weight", COL_WEIGHT)

        self.tree.append_column(col)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.tree)
        self.pack_start(scrolled, True, True, 0)

        # Buttons
        btn_box = Gtk.Box(spacing=4)
        btn_box.set_border_width(6)

        btn_add = Gtk.MenuButton(label="Add \u25BE")
        btn_add.get_style_context().add_class("sidebar-btn")
        add_menu = Gtk.Menu()
        item_session = Gtk.MenuItem(label="SSH Session")
        item_session.connect("activate", lambda _: self._on_add(None))
        add_menu.append(item_session)
        item_terminal = Gtk.MenuItem(label="Local Terminal")
        item_terminal.connect("activate", lambda _: self.app.add_local_tab())
        add_menu.append(item_terminal)
        item_claude = Gtk.MenuItem(label="Claude Code")
        item_claude.connect("activate", lambda _: self._on_add_claude())
        add_menu.append(item_claude)
        add_menu.show_all()
        btn_add.set_popup(add_menu)

        btn_edit = Gtk.Button(label="Edit")
        btn_edit.get_style_context().add_class("sidebar-btn")
        btn_edit.connect("clicked", self._on_edit)

        btn_delete = Gtk.Button(label="Delete")
        btn_delete.get_style_context().add_class("sidebar-btn")
        btn_delete.connect("clicked", self._on_delete)

        btn_box.pack_start(btn_add, True, True, 0)
        btn_box.pack_start(btn_edit, True, True, 0)
        btn_box.pack_start(btn_delete, True, True, 0)
        self.pack_start(btn_box, False, False, 0)

        # Signals
        self.tree.connect("row-activated", self._on_row_activated)
        self.tree.connect("button-press-event", self._on_button_press)

        self.refresh()

    def _append_session(self, parent_iter, session):
        """Add a session node and its macro children to the tree store."""
        tooltip = f"{session.get('username', '')}@{session.get('host', '')}:{session.get('port', 22)}"
        session_iter = self.store.append(parent_iter, [
            "\U0001F5A5",
            session["name"],
            session["id"],
            tooltip,
            _session_color("ssh"),
            Pango.Weight.NORMAL,
        ])
        for macro in session.get("macros", []):
            macro_id = f"macro:{session['id']}:{macro['id']}"
            self.store.append(session_iter, [
                "\u25B6",  # ▶
                macro["name"],
                macro_id,
                f"Macro: {macro['name']}",
                CATPPUCCIN["green"],
                Pango.Weight.NORMAL,
            ])

    def _append_claude_session(self, parent_iter, session):
        """Add a Claude Code session node to the tree store."""
        opts = []
        if session.get("sudo"):
            opts.append("sudo")
        if session.get("resume"):
            opts.append("resume")
        if session.get("skip_permissions"):
            opts.append("skip-perms")
        tooltip = ", ".join(opts) if opts else "Claude Code"
        self.store.append(parent_iter, [
            "\U0001F916",  # 🤖
            session["name"],
            f"claude:{session['id']}",
            tooltip,
            _session_color("claude"),
            Pango.Weight.NORMAL,
        ])

    def refresh(self):
        expanded = _save_expanded(self.tree, self.store, COL_ID)
        self.store.clear()
        sessions = self.app.session_manager.all()

        folders = {}
        ungrouped = []

        for s in sessions:
            folder = s.get("folder", "").strip()
            if folder:
                folders.setdefault(folder, []).append(s)
            else:
                ungrouped.append(s)

        # Grouped sessions
        for folder_name in sorted(folders.keys()):
            count = len(folders[folder_name])
            parent = self.store.append(None, [
                "\U0001F4C1",  # folder icon
                f"{folder_name} ({count})",
                f"folder:{folder_name}",
                folder_name,
                CATPPUCCIN["subtext1"],
                Pango.Weight.BOLD,
            ])
            for s in folders[folder_name]:
                self._append_session(parent, s)

        # Ungrouped sessions
        for s in ungrouped:
            self._append_session(None, s)

        # ── Claude Code sessions ──
        claude_sessions = self.app.claude_manager.all()
        if claude_sessions:
            # Section header as parent node
            claude_root = self.store.append(None, [
                "\U0001F916",  # 🤖
                "Claude Code",
                "section:claude",
                "Claude Code sessions",
                CATPPUCCIN["mauve"],
                Pango.Weight.BOLD,
            ])

            claude_folders = {}
            claude_ungrouped = []
            for s in claude_sessions:
                folder = s.get("folder", "").strip()
                if folder:
                    claude_folders.setdefault(folder, []).append(s)
                else:
                    claude_ungrouped.append(s)

            for folder_name in sorted(claude_folders.keys()):
                count = len(claude_folders[folder_name])
                parent = self.store.append(claude_root, [
                    "\U0001F4C1",
                    f"{folder_name} ({count})",
                    f"cfolder:{folder_name}",
                    folder_name,
                    CATPPUCCIN["subtext1"],
                    Pango.Weight.BOLD,
                ])
                for s in claude_folders[folder_name]:
                    self._append_claude_session(parent, s)

            for s in claude_ungrouped:
                self._append_claude_session(claude_root, s)

        if expanded:
            _restore_expanded(self.tree, self.store, COL_ID, expanded)
        else:
            self.tree.expand_all()

    _FOLDER_PREFIXES = ("folder:", "cfolder:", "section:")

    def _get_selected_session_id(self):
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        if it is None:
            return None
        col_id = model.get_value(it, COL_ID)
        if col_id and not col_id.startswith("macro:") and not any(
            col_id.startswith(p) for p in self._FOLDER_PREFIXES
        ):
            return col_id
        return None

    def _on_row_activated(self, tree, path, column):
        it = self.store.get_iter(path)
        col_id = self.store.get_value(it, COL_ID)
        if col_id and col_id.startswith("macro:"):
            parts = col_id.split(":", 2)
            self._run_macro(parts[1], parts[2])
        elif col_id and col_id.startswith("claude:"):
            claude_id = col_id[7:]
            config = self.app.claude_manager.get(claude_id)
            if config:
                self.app.open_claude_tab(config)
        elif col_id and any(col_id.startswith(p) for p in self._FOLDER_PREFIXES):
            # Toggle expand/collapse for folder and section nodes
            if tree.row_expanded(path):
                tree.collapse_row(path)
            else:
                tree.expand_row(path, False)
        elif col_id:
            session = self.app.session_manager.get(col_id)
            if session:
                self.app.open_ssh_tab(session)

    def _build_move_to_folder_submenu(self, session_id, manager, current_folder=""):
        """Build 'Move to folder' submenu with existing folders + New/Remove."""
        submenu = Gtk.Menu()
        existing = sorted({
            s.get("folder", "").strip()
            for s in manager.all()
            if s.get("folder", "").strip()
        })
        for fname in existing:
            if fname == current_folder:
                continue
            item = Gtk.MenuItem(label=fname)
            item.connect("activate",
                         lambda _, sid=session_id, f=fname, m=manager:
                         self._move_to_folder(sid, f, m))
            submenu.append(item)
        if existing:
            submenu.append(Gtk.SeparatorMenuItem())
        item_new = Gtk.MenuItem(label="New folder\u2026")
        item_new.connect("activate",
                         lambda _, sid=session_id, m=manager:
                         self._move_to_new_folder(sid, m))
        submenu.append(item_new)
        if current_folder:
            submenu.append(Gtk.SeparatorMenuItem())
            item_rm = Gtk.MenuItem(label="Remove from folder")
            item_rm.connect("activate",
                            lambda _, sid=session_id, m=manager:
                            self._move_to_folder(sid, "", m))
            submenu.append(item_rm)
        return submenu

    def _on_button_press(self, widget, event):
        if event.button == 3:  # right click
            path_info = self.tree.get_path_at_pos(int(event.x), int(event.y))
            if path_info:
                path = path_info[0]
                self.tree.get_selection().select_path(path)
                it = self.store.get_iter(path)
                col_id = self.store.get_value(it, COL_ID)

                if col_id and col_id.startswith("macro:"):
                    # Macro context menu
                    parts = col_id.split(":", 2)
                    sid, mid = parts[1], parts[2]
                    menu = Gtk.Menu()

                    item_run = Gtk.MenuItem(label="Run")
                    item_run.connect("activate", lambda _, s=sid, m=mid: self._run_macro(s, m))
                    menu.append(item_run)

                    item_edit = Gtk.MenuItem(label="Edit")
                    item_edit.connect("activate", lambda _, s=sid, m=mid: self._edit_macro(s, m))
                    menu.append(item_edit)

                    item_delete = Gtk.MenuItem(label="Delete")
                    item_delete.connect("activate", lambda _, s=sid, m=mid: self._delete_macro(s, m))
                    menu.append(item_delete)

                    menu.show_all()
                    menu.popup_at_pointer(event)

                elif col_id and (col_id.startswith("folder:") or col_id.startswith("cfolder:")):
                    # Folder context menu
                    is_claude = col_id.startswith("cfolder:")
                    folder_name = col_id.split(":", 1)[1]
                    manager = self.app.claude_manager if is_claude else self.app.session_manager
                    menu = Gtk.Menu()

                    item_rename = Gtk.MenuItem(label="Rename folder\u2026")
                    item_rename.connect(
                        "activate",
                        lambda _, fn=folder_name, m=manager: self._rename_folder(fn, m))
                    menu.append(item_rename)

                    item_delete = Gtk.MenuItem(label="Ungroup all")
                    item_delete.connect(
                        "activate",
                        lambda _, fn=folder_name, m=manager: self._ungroup_folder(fn, m))
                    menu.append(item_delete)

                    menu.show_all()
                    menu.popup_at_pointer(event)

                elif col_id and col_id.startswith("claude:"):
                    # Claude Code session context menu
                    claude_id = col_id[7:]
                    config = self.app.claude_manager.get(claude_id)
                    menu = Gtk.Menu()

                    item_connect = Gtk.MenuItem(label="Connect")
                    item_connect.connect("activate", lambda _, cid=claude_id: self._connect_claude(cid))
                    menu.append(item_connect)

                    item_edit = Gtk.MenuItem(label="Edit")
                    item_edit.connect("activate", lambda _, cid=claude_id: self._edit_claude(cid))
                    menu.append(item_edit)

                    item_delete = Gtk.MenuItem(label="Delete")
                    item_delete.connect("activate", lambda _, cid=claude_id: self._delete_claude(cid))
                    menu.append(item_delete)

                    menu.append(Gtk.SeparatorMenuItem())

                    item_ctx = Gtk.MenuItem(label="Edit ctx\u2026")
                    item_ctx.connect("activate", lambda _, cid=claude_id: self._edit_ctx(cid))
                    menu.append(item_ctx)

                    project_dir = config.get("project_dir", "") if config else ""
                    if project_dir and os.path.isdir(project_dir):
                        menu.append(Gtk.SeparatorMenuItem())
                        item_open = Gtk.MenuItem(label="Open with \u25B8")
                        item_open.set_submenu(
                            self._build_open_with_submenu(project_dir))
                        menu.append(item_open)

                    menu.append(Gtk.SeparatorMenuItem())

                    item_folder = Gtk.MenuItem(label="Move to folder \u25B8")
                    cur_folder = config.get("folder", "").strip() if config else ""
                    item_folder.set_submenu(
                        self._build_move_to_folder_submenu(
                            claude_id, self.app.claude_manager, cur_folder))
                    menu.append(item_folder)

                    menu.show_all()
                    menu.popup_at_pointer(event)

                elif col_id and not col_id.startswith("section:"):
                    # Session context menu
                    session_id = col_id
                    session = self.app.session_manager.get(session_id)
                    menu = Gtk.Menu()

                    item_connect = Gtk.MenuItem(label="Connect")
                    item_connect.connect("activate", lambda _: self._connect_session(session_id))
                    menu.append(item_connect)

                    item_edit = Gtk.MenuItem(label="Edit")
                    item_edit.connect("activate", lambda _: self._edit_session(session_id))
                    menu.append(item_edit)

                    item_delete = Gtk.MenuItem(label="Delete")
                    item_delete.connect("activate", lambda _: self._delete_session(session_id))
                    menu.append(item_delete)

                    menu.append(Gtk.SeparatorMenuItem())

                    item_add_macro = Gtk.MenuItem(label="Add Macro...")
                    item_add_macro.connect("activate", lambda _: self._add_macro(session_id))
                    menu.append(item_add_macro)

                    menu.append(Gtk.SeparatorMenuItem())

                    item_folder = Gtk.MenuItem(label="Move to folder \u25B8")
                    cur_folder = session.get("folder", "").strip() if session else ""
                    item_folder.set_submenu(
                        self._build_move_to_folder_submenu(
                            session_id, self.app.session_manager, cur_folder))
                    menu.append(item_folder)

                    menu.show_all()
                    menu.popup_at_pointer(event)
            return True
        return False

    def _connect_session(self, session_id):
        session = self.app.session_manager.get(session_id)
        if session:
            self.app.open_ssh_tab(session)

    def _on_add(self, button):
        dlg = SessionDialog(self.app)
        while True:
            resp = dlg.run()
            if resp != Gtk.ResponseType.OK:
                break
            if dlg.validate():
                self.app.session_manager.add(dlg.get_data())
                self.refresh()
                break
        dlg.destroy()

    def _on_add_claude(self):
        dlg = ClaudeCodeDialog(self.app)
        while True:
            resp = dlg.run()
            if resp != Gtk.ResponseType.OK:
                break
            if dlg.validate():
                data = dlg.get_data()
                data = _run_ctx_wizard_if_needed(dlg, data)
                self.app.claude_manager.add(data)
                self.refresh()
                break
        dlg.destroy()

    def _on_edit(self, button):
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        if it is None:
            return
        col_id = model.get_value(it, COL_ID)
        if col_id and col_id.startswith("claude:"):
            self._edit_claude(col_id[7:])
        elif col_id and col_id.startswith("folder:"):
            self._rename_folder(col_id.split(":", 1)[1], self.app.session_manager)
        elif col_id and col_id.startswith("cfolder:"):
            self._rename_folder(col_id.split(":", 1)[1], self.app.claude_manager)
        elif col_id and not col_id.startswith("macro:") and not col_id.startswith("section:"):
            self._edit_session(col_id)

    def _edit_session(self, session_id):
        session = self.app.session_manager.get(session_id)
        if not session:
            return
        dlg = SessionDialog(self.app, session)
        while True:
            resp = dlg.run()
            if resp != Gtk.ResponseType.OK:
                break
            if dlg.validate():
                data = dlg.get_data()
                self.app.session_manager.update(session_id, data)
                self.refresh()
                break
        dlg.destroy()

    def _on_delete(self, button):
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        if it is None:
            return
        col_id = model.get_value(it, COL_ID)
        if col_id and col_id.startswith("claude:"):
            self._delete_claude(col_id[7:])
        elif col_id and col_id.startswith("folder:"):
            self._ungroup_folder(col_id.split(":", 1)[1], self.app.session_manager)
        elif col_id and col_id.startswith("cfolder:"):
            self._ungroup_folder(col_id.split(":", 1)[1], self.app.claude_manager)
        elif col_id and not col_id.startswith("macro:") and not col_id.startswith("section:"):
            self._delete_session(col_id)

    def _delete_session(self, session_id):
        session = self.app.session_manager.get(session_id)
        if not session:
            return
        dlg = Gtk.MessageDialog(
            transient_for=self.app,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete session \"{session['name']}\"?",
        )
        if dlg.run() == Gtk.ResponseType.YES:
            self.app.session_manager.delete(session_id)
            self.refresh()
        dlg.destroy()

    # ── Folder management ──

    def _move_to_folder(self, session_id, folder_name, manager):
        """Move a session to a folder (or remove from folder if empty)."""
        session = manager.get(session_id)
        if session:
            session["folder"] = folder_name
            manager.update(session_id, session)
            self.refresh()

    def _move_to_new_folder(self, session_id, manager):
        """Prompt for new folder name and move session there."""
        dlg = Gtk.Dialog(
            title="New folder",
            transient_for=self.app,
            modal=True,
            destroy_with_parent=True,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dlg.set_default_response(Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)
        lbl = Gtk.Label(label="Folder name:")
        box.pack_start(lbl, False, False, 0)
        entry = Gtk.Entry()
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            name = entry.get_text().strip()
            if name:
                self._move_to_folder(session_id, name, manager)
        dlg.destroy()

    def _rename_folder(self, old_name, manager):
        """Rename a folder — updates all sessions that belong to it."""
        dlg = Gtk.Dialog(
            title="Rename folder",
            transient_for=self.app,
            modal=True,
            destroy_with_parent=True,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dlg.set_default_response(Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)
        lbl = Gtk.Label(label="New name:")
        box.pack_start(lbl, False, False, 0)
        entry = Gtk.Entry()
        entry.set_text(old_name)
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            new_name = entry.get_text().strip()
            if new_name and new_name != old_name:
                for s in manager.all():
                    if s.get("folder", "").strip() == old_name:
                        s["folder"] = new_name
                        manager.update(s["id"], s)
                self.refresh()
        dlg.destroy()

    def _ungroup_folder(self, folder_name, manager):
        """Remove folder assignment from all sessions in this folder."""
        for s in manager.all():
            if s.get("folder", "").strip() == folder_name:
                s["folder"] = ""
                manager.update(s["id"], s)
        self.refresh()

    # ── Macro CRUD ──

    def _add_macro(self, session_id):
        session = self.app.session_manager.get(session_id)
        if not session:
            return
        dlg = MacroDialog(self.app)
        while True:
            resp = dlg.run()
            if resp != Gtk.ResponseType.OK:
                break
            if dlg.validate():
                data = dlg.get_data()
                data["id"] = str(uuid.uuid4())
                session.setdefault("macros", []).append(data)
                self.app.session_manager.save()
                self.refresh()
                break
        dlg.destroy()

    def _edit_macro(self, session_id, macro_id):
        session = self.app.session_manager.get(session_id)
        if not session:
            return
        macro = None
        for m in session.get("macros", []):
            if m["id"] == macro_id:
                macro = m
                break
        if not macro:
            return
        dlg = MacroDialog(self.app, macro)
        while True:
            resp = dlg.run()
            if resp != Gtk.ResponseType.OK:
                break
            if dlg.validate():
                data = dlg.get_data()
                macro.update(data)
                self.app.session_manager.save()
                self.refresh()
                break
        dlg.destroy()

    def _delete_macro(self, session_id, macro_id):
        session = self.app.session_manager.get(session_id)
        if not session:
            return
        macro_name = ""
        for m in session.get("macros", []):
            if m["id"] == macro_id:
                macro_name = m.get("name", "")
                break
        dlg = Gtk.MessageDialog(
            transient_for=self.app,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f'Delete macro "{macro_name}"?',
        )
        if dlg.run() == Gtk.ResponseType.YES:
            session["macros"] = [
                m for m in session.get("macros", []) if m["id"] != macro_id
            ]
            self.app.session_manager.save()
            self.refresh()
        dlg.destroy()

    def _run_macro(self, session_id, macro_id):
        session = self.app.session_manager.get(session_id)
        if not session:
            return
        macro = None
        for m in session.get("macros", []):
            if m["id"] == macro_id:
                macro = m
                break
        if macro:
            self.app.open_ssh_tab_with_macro(session, macro)

    # ── Open with ──

    def _build_open_with_submenu(self, project_dir):
        submenu = Gtk.Menu()

        item_fm = Gtk.MenuItem(label="File Manager")
        item_fm.connect("activate",
                        lambda _, d=project_dir: self._open_with_app("xdg-open", d))
        submenu.append(item_fm)

        for name, cmd in [("VS Code", "code"), ("Zed", "zed")]:
            if shutil.which(cmd):
                item = Gtk.MenuItem(label=name)
                item.connect("activate",
                             lambda _, c=cmd, d=project_dir: self._open_with_app(c, d))
                submenu.append(item)

        submenu.append(Gtk.SeparatorMenuItem())

        item_custom = Gtk.MenuItem(label="Custom\u2026")
        item_custom.connect("activate",
                            lambda _, d=project_dir: self._open_with_custom(d))
        submenu.append(item_custom)
        return submenu

    def _open_with_app(self, command, project_dir):
        try:
            subprocess.Popen([command, project_dir],
                             start_new_session=True,
                             stdout=subprocess.DEVNULL,
                             stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            dlg = Gtk.MessageDialog(
                transient_for=self.app, modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text=f"Command '{command}' not found.")
            dlg.run()
            dlg.destroy()

    def _open_with_custom(self, project_dir):
        dlg = Gtk.Dialog(
            title="Open with custom command",
            transient_for=self.app, modal=True)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OK, Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)

        lbl = Gtk.Label(label=f"Command to run in:\n{project_dir}")
        lbl.set_xalign(0)
        box.pack_start(lbl, False, False, 0)

        entry = Gtk.Entry()
        entry.set_placeholder_text("e.g. idea, nautilus, kitty")
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)

        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            cmd = entry.get_text().strip()
            if cmd:
                self._open_with_app(cmd, project_dir)
        dlg.destroy()

    # ── Claude Code CRUD ──

    def _edit_ctx(self, claude_id):
        config = self.app.claude_manager.get(claude_id)
        if not config:
            return
        project_dir = config.get("project_dir", "")
        if not project_dir:
            return
        ctx_project = _resolve_ctx_project_name(project_dir)
        dlg = CtxEditDialog(self.app, ctx_project, project_dir)
        dlg.run()
        dlg.destroy()

    def _connect_claude(self, claude_id):
        config = self.app.claude_manager.get(claude_id)
        if config:
            self.app.open_claude_tab(config)

    def _edit_claude(self, claude_id):
        config = self.app.claude_manager.get(claude_id)
        if not config:
            return
        dlg = ClaudeCodeDialog(self.app, config)
        while True:
            resp = dlg.run()
            if resp != Gtk.ResponseType.OK:
                break
            if dlg.validate():
                data = dlg.get_data()
                data = _run_ctx_wizard_if_needed(dlg, data)
                self.app.claude_manager.update(claude_id, data)
                self.refresh()
                break
        dlg.destroy()

    def _delete_claude(self, claude_id):
        config = self.app.claude_manager.get(claude_id)
        if not config:
            return
        dlg = Gtk.MessageDialog(
            transient_for=self.app,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete Claude session \"{config['name']}\"?",
        )
        if dlg.run() == Gtk.ResponseType.YES:
            self.app.claude_manager.delete(claude_id)
            # Ask about ctx cleanup
            project_dir = config.get("project_dir", "")
            if project_dir:
                ctx_name = _resolve_ctx_project_name(project_dir)
                if _is_ctx_available() and _is_ctx_project_registered(ctx_name):
                    ctx_dlg = Gtk.MessageDialog(
                        transient_for=self.app,
                        modal=True,
                        message_type=Gtk.MessageType.QUESTION,
                        buttons=Gtk.ButtonsType.YES_NO,
                        text=f"Also delete ctx project \"{ctx_name}\"?",
                    )
                    ctx_dlg.format_secondary_text(
                        "This will remove all context entries for this project from the ctx database."
                    )
                    if ctx_dlg.run() == Gtk.ResponseType.YES:
                        subprocess.run(
                            ["ctx", "delete", ctx_name],
                            capture_output=True, text=True,
                        )
                    ctx_dlg.destroy()
            self.refresh()
        dlg.destroy()


# ─── Ctx Import / Export ──────────────────────────────────────────────────────


class _CtxExportDialog(Gtk.Dialog):
    """Dialog for selectively exporting ctx data to a JSON file."""

    def __init__(self, parent):
        super().__init__(
            title="Export Context",
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            "Export", Gtk.ResponseType.OK,
        )
        self.set_default_size(500, 450)
        self.set_default_response(Gtk.ResponseType.OK)

        box = self.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)

        # Select all / Deselect all
        sel_box = Gtk.Box(spacing=8)
        btn_all = Gtk.Button(label="Select All")
        btn_all.connect("clicked", lambda _: self._set_all(True))
        btn_none = Gtk.Button(label="Deselect All")
        btn_none.connect("clicked", lambda _: self._set_all(False))
        sel_box.pack_start(btn_all, False, False, 0)
        sel_box.pack_start(btn_none, False, False, 0)
        box.pack_start(sel_box, False, False, 0)

        # Tree with checkboxes: toggle, icon, name, data_type, data_key
        self.store = Gtk.TreeStore(bool, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(False)

        col = Gtk.TreeViewColumn()
        cell_toggle = Gtk.CellRendererToggle()
        cell_toggle.connect("toggled", self._on_toggled)
        col.pack_start(cell_toggle, False)
        col.add_attribute(cell_toggle, "active", 0)

        cell_icon = Gtk.CellRendererText()
        col.pack_start(cell_icon, False)
        col.add_attribute(cell_icon, "text", 1)

        cell_name = Gtk.CellRendererText()
        cell_name.set_property("ellipsize", Pango.EllipsizeMode.END)
        col.pack_start(cell_name, True)
        col.add_attribute(cell_name, "text", 2)

        self.tree.append_column(col)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.tree)
        box.pack_start(scroll, True, True, 0)

        self._load_data()
        self.show_all()

    def _load_data(self):
        if not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row

        projects = db.execute(
            "SELECT name FROM sessions ORDER BY name"
        ).fetchall()
        for proj in projects:
            pname = proj["name"]
            proj_iter = self.store.append(None, [
                True, "\U0001f4c1", pname, "project", pname,
            ])
            entries = db.execute(
                "SELECT key FROM contexts WHERE project = ? ORDER BY key",
                (pname,),
            ).fetchall()
            for entry in entries:
                self.store.append(proj_iter, [
                    True, " ", entry["key"], "entry", entry["key"],
                ])
            scount = db.execute(
                "SELECT COUNT(*) as c FROM summaries WHERE project = ?",
                (pname,),
            ).fetchone()["c"]
            if scount:
                self.store.append(proj_iter, [
                    True, "\U0001f4cb", f"Summaries ({scount})", "summaries", pname,
                ])
            # Images
            _ensure_images_table()
            images = db.execute(
                "SELECT filename, original_name FROM images "
                "WHERE project = ? ORDER BY added_at",
                (pname,),
            ).fetchall()
            for img in images:
                self.store.append(proj_iter, [
                    True, "\U0001f5bc",
                    img["original_name"] or img["filename"],
                    "image", img["filename"],
                ])

        shared = db.execute("SELECT key FROM shared ORDER BY key").fetchall()
        if shared:
            shared_iter = self.store.append(None, [
                True, "\U0001f517", "Shared", "shared", "",
            ])
            for entry in shared:
                self.store.append(shared_iter, [
                    True, " ", entry["key"], "shared_entry", entry["key"],
                ])

        db.close()
        self.tree.expand_all()

    def _on_toggled(self, renderer, path):
        it = self.store.get_iter(path)
        new_val = not self.store.get_value(it, 0)
        self.store.set_value(it, 0, new_val)
        # Propagate to children
        child = self.store.iter_children(it)
        while child:
            self.store.set_value(child, 0, new_val)
            child = self.store.iter_next(child)
        # Update parent based on children
        parent = self.store.iter_parent(it)
        if parent:
            any_checked = False
            child = self.store.iter_children(parent)
            while child:
                if self.store.get_value(child, 0):
                    any_checked = True
                    break
                child = self.store.iter_next(child)
            self.store.set_value(parent, 0, any_checked)

    def _set_all(self, val):
        def _walk(it):
            while it:
                self.store.set_value(it, 0, val)
                child = self.store.iter_children(it)
                if child:
                    _walk(child)
                it = self.store.iter_next(it)
        root = self.store.get_iter_first()
        if root:
            _walk(root)

    def get_export_data(self):
        """Collect checked items and return export dict."""
        import base64
        if not os.path.exists(CTX_DB):
            return None
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row
        data = {
            "sessions": [], "contexts": [], "shared": [],
            "summaries": [], "images": [],
        }

        root = self.store.get_iter_first()
        while root:
            dtype = self.store.get_value(root, 3)
            dkey = self.store.get_value(root, 4)

            if dtype == "project":
                proj_name = dkey
                child = self.store.iter_children(root)
                checked_entries = []
                checked_images = []
                include_summaries = False
                while child:
                    if self.store.get_value(child, 0):
                        ctype = self.store.get_value(child, 3)
                        ckey = self.store.get_value(child, 4)
                        if ctype == "entry":
                            checked_entries.append(ckey)
                        elif ctype == "summaries":
                            include_summaries = True
                        elif ctype == "image":
                            checked_images.append(ckey)
                    child = self.store.iter_next(child)

                if (checked_entries or include_summaries
                        or checked_images or self.store.get_value(root, 0)):
                    row = db.execute(
                        "SELECT * FROM sessions WHERE name = ?", (proj_name,)
                    ).fetchone()
                    if row:
                        data["sessions"].append(dict(row))

                for ekey in checked_entries:
                    row = db.execute(
                        "SELECT project, key, value, updated_at FROM contexts "
                        "WHERE project = ? AND key = ?",
                        (proj_name, ekey),
                    ).fetchone()
                    if row:
                        data["contexts"].append(dict(row))

                if include_summaries:
                    rows = db.execute(
                        "SELECT project, summary, created_at FROM summaries "
                        "WHERE project = ?",
                        (proj_name,),
                    ).fetchall()
                    data["summaries"].extend(dict(r) for r in rows)

                for fname in checked_images:
                    img_path = os.path.join(CTX_IMAGES_DIR, proj_name, fname)
                    if os.path.exists(img_path):
                        with open(img_path, "rb") as f:
                            img_b64 = base64.b64encode(f.read()).decode()
                        orig = db.execute(
                            "SELECT original_name, added_at FROM images "
                            "WHERE project = ? AND filename = ?",
                            (proj_name, fname),
                        ).fetchone()
                        data["images"].append({
                            "project": proj_name,
                            "filename": fname,
                            "original_name": orig["original_name"] if orig else fname,
                            "added_at": orig["added_at"] if orig else "",
                            "data": img_b64,
                        })

            elif dtype == "shared":
                child = self.store.iter_children(root)
                while child:
                    if self.store.get_value(child, 0):
                        skey = self.store.get_value(child, 4)
                        row = db.execute(
                            "SELECT * FROM shared WHERE key = ?", (skey,)
                        ).fetchone()
                        if row:
                            data["shared"].append(dict(row))
                    child = self.store.iter_next(child)

            root = self.store.iter_next(root)
        db.close()

        data = {k: v for k, v in data.items() if v}
        if not data:
            return None
        data["_export_version"] = 1
        return data


class _CtxImportDialog(Gtk.Dialog):
    """Dialog for importing ctx data from a JSON file."""

    def __init__(self, parent):
        super().__init__(
            title="Import Context",
            transient_for=parent,
            modal=True,
            destroy_with_parent=True,
        )
        self.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            "Import", Gtk.ResponseType.OK,
        )
        self.set_default_size(500, 450)
        self.set_default_response(Gtk.ResponseType.OK)
        self.set_response_sensitive(Gtk.ResponseType.OK, False)

        box = self.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)

        # File chooser
        file_box = Gtk.Box(spacing=8)
        file_box.pack_start(Gtk.Label(label="File:"), False, False, 0)
        self.file_entry = Gtk.Entry(hexpand=True)
        self.file_entry.set_placeholder_text("Select JSON file\u2026")
        self.file_entry.set_editable(False)
        file_box.pack_start(self.file_entry, True, True, 0)
        btn_browse = Gtk.Button(label="Browse\u2026")
        btn_browse.connect("clicked", self._on_browse)
        file_box.pack_start(btn_browse, False, False, 0)
        box.pack_start(file_box, False, False, 0)

        # Select all / Deselect all
        sel_box = Gtk.Box(spacing=8)
        btn_all = Gtk.Button(label="Select All")
        btn_all.connect("clicked", lambda _: self._set_all(True))
        btn_none = Gtk.Button(label="Deselect All")
        btn_none.connect("clicked", lambda _: self._set_all(False))
        sel_box.pack_start(btn_all, False, False, 0)
        sel_box.pack_start(btn_none, False, False, 0)
        box.pack_start(sel_box, False, False, 0)

        # Preview tree: toggle, icon, name, data_type, data_key
        self.store = Gtk.TreeStore(bool, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(False)

        col = Gtk.TreeViewColumn()
        cell_toggle = Gtk.CellRendererToggle()
        cell_toggle.connect("toggled", self._on_toggled)
        col.pack_start(cell_toggle, False)
        col.add_attribute(cell_toggle, "active", 0)

        cell_icon = Gtk.CellRendererText()
        col.pack_start(cell_icon, False)
        col.add_attribute(cell_icon, "text", 1)

        cell_name = Gtk.CellRendererText()
        cell_name.set_property("ellipsize", Pango.EllipsizeMode.END)
        col.pack_start(cell_name, True)
        col.add_attribute(cell_name, "text", 2)

        self.tree.append_column(col)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.add(self.tree)
        box.pack_start(scroll, True, True, 0)

        # Overwrite option
        self.chk_overwrite = Gtk.CheckButton(label="Overwrite existing entries")
        box.pack_start(self.chk_overwrite, False, False, 0)

        self.import_data = None
        self.show_all()

    def _on_browse(self, button):
        dlg = Gtk.FileChooserDialog(
            title="Select context file",
            parent=self,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        filt = Gtk.FileFilter()
        filt.set_name("JSON files")
        filt.add_pattern("*.json")
        dlg.add_filter(filt)
        filt_all = Gtk.FileFilter()
        filt_all.set_name("All files")
        filt_all.add_pattern("*")
        dlg.add_filter(filt_all)
        if dlg.run() == Gtk.ResponseType.OK:
            path = dlg.get_filename()
            self.file_entry.set_text(path)
            self._load_preview(path)
        dlg.destroy()

    def _load_preview(self, path):
        self.store.clear()
        self.import_data = None
        self.set_response_sensitive(Gtk.ResponseType.OK, False)
        try:
            with open(path, "r") as f:
                data = json.load(f)
        except (json.JSONDecodeError, OSError) as e:
            dlg = Gtk.MessageDialog(
                transient_for=self,
                modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text=f"Failed to load file: {e}",
            )
            dlg.run()
            dlg.destroy()
            return

        self.import_data = data

        # Group by project
        sessions = {s["name"]: s for s in data.get("sessions", [])}
        contexts_by_proj = {}
        for ctx in data.get("contexts", []):
            contexts_by_proj.setdefault(ctx["project"], []).append(ctx)
        summaries_by_proj = {}
        for s in data.get("summaries", []):
            summaries_by_proj.setdefault(s["project"], []).append(s)
        images_by_proj = {}
        for img in data.get("images", []):
            images_by_proj.setdefault(img["project"], []).append(img)

        all_projects = sorted(
            set(sessions) | set(contexts_by_proj)
            | set(summaries_by_proj) | set(images_by_proj)
        )
        for proj_name in all_projects:
            proj_iter = self.store.append(None, [
                True, "\U0001f4c1", proj_name, "project", proj_name,
            ])
            for ctx in contexts_by_proj.get(proj_name, []):
                self.store.append(proj_iter, [
                    True, " ", ctx["key"], "entry", ctx["key"],
                ])
            scount = len(summaries_by_proj.get(proj_name, []))
            if scount:
                self.store.append(proj_iter, [
                    True, "\U0001f4cb", f"Summaries ({scount})", "summaries", proj_name,
                ])
            for img in images_by_proj.get(proj_name, []):
                self.store.append(proj_iter, [
                    True, "\U0001f5bc",
                    img.get("original_name") or img["filename"],
                    "image", img["filename"],
                ])

        shared = data.get("shared", [])
        if shared:
            shared_iter = self.store.append(None, [
                True, "\U0001f517", "Shared", "shared", "",
            ])
            for entry in shared:
                self.store.append(shared_iter, [
                    True, " ", entry["key"], "shared_entry", entry["key"],
                ])

        self.tree.expand_all()
        self.set_response_sensitive(Gtk.ResponseType.OK, True)

    def _on_toggled(self, renderer, path):
        it = self.store.get_iter(path)
        new_val = not self.store.get_value(it, 0)
        self.store.set_value(it, 0, new_val)
        child = self.store.iter_children(it)
        while child:
            self.store.set_value(child, 0, new_val)
            child = self.store.iter_next(child)
        parent = self.store.iter_parent(it)
        if parent:
            any_checked = False
            child = self.store.iter_children(parent)
            while child:
                if self.store.get_value(child, 0):
                    any_checked = True
                    break
                child = self.store.iter_next(child)
            self.store.set_value(parent, 0, any_checked)

    def _set_all(self, val):
        def _walk(it):
            while it:
                self.store.set_value(it, 0, val)
                child = self.store.iter_children(it)
                if child:
                    _walk(child)
                it = self.store.iter_next(it)
        root = self.store.get_iter_first()
        if root:
            _walk(root)

    def get_selected_data(self):
        """Return (filtered_data_dict, overwrite_bool) or (None, False)."""
        if not self.import_data:
            return None, False

        data = self.import_data
        overwrite = self.chk_overwrite.get_active()
        sessions_map = {s["name"]: s for s in data.get("sessions", [])}
        contexts_by_proj = {}
        for ctx in data.get("contexts", []):
            contexts_by_proj.setdefault(ctx["project"], []).append(ctx)
        summaries_by_proj = {}
        for s in data.get("summaries", []):
            summaries_by_proj.setdefault(s["project"], []).append(s)
        shared_map = {s["key"]: s for s in data.get("shared", [])}
        images_by_fname = {}
        for img in data.get("images", []):
            images_by_fname[(img["project"], img["filename"])] = img

        result = {
            "sessions": [], "contexts": [], "shared": [],
            "summaries": [], "images": [],
        }

        root = self.store.get_iter_first()
        while root:
            dtype = self.store.get_value(root, 3)
            dkey = self.store.get_value(root, 4)

            if dtype == "project":
                proj_name = dkey
                child = self.store.iter_children(root)
                checked_entries = []
                checked_images = []
                include_summaries = False
                while child:
                    if self.store.get_value(child, 0):
                        ctype = self.store.get_value(child, 3)
                        ckey = self.store.get_value(child, 4)
                        if ctype == "entry":
                            checked_entries.append(ckey)
                        elif ctype == "summaries":
                            include_summaries = True
                        elif ctype == "image":
                            checked_images.append(ckey)
                    child = self.store.iter_next(child)

                if checked_entries or include_summaries or checked_images:
                    if proj_name in sessions_map:
                        result["sessions"].append(sessions_map[proj_name])
                    for ekey in checked_entries:
                        for ctx in contexts_by_proj.get(proj_name, []):
                            if ctx["key"] == ekey:
                                result["contexts"].append(ctx)
                                break
                    if include_summaries:
                        result["summaries"].extend(
                            summaries_by_proj.get(proj_name, [])
                        )
                    for fname in checked_images:
                        key = (proj_name, fname)
                        if key in images_by_fname:
                            result["images"].append(images_by_fname[key])

            elif dtype == "shared":
                child = self.store.iter_children(root)
                while child:
                    if self.store.get_value(child, 0):
                        skey = self.store.get_value(child, 4)
                        if skey in shared_map:
                            result["shared"].append(shared_map[skey])
                    child = self.store.iter_next(child)

            root = self.store.iter_next(root)

        result = {k: v for k, v in result.items() if v}
        return (result if result else None), overwrite


# ─── CtxManagerPanel ──────────────────────────────────────────────────────────


class CtxManagerPanel(Gtk.Box):
    """Panel for browsing and managing ctx project contexts."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app

        # Paned: tree on top, detail on bottom
        paned = Gtk.VPaned()
        self.pack_start(paned, True, True, 0)

        # ── Tree ──
        # Columns: icon, display_name, project, key, color, weight, row_type
        self.store = Gtk.TreeStore(str, str, str, str, str, int, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(False)
        self.tree.set_activate_on_single_click(False)

        col = Gtk.TreeViewColumn()
        cell_icon = Gtk.CellRendererText()
        col.pack_start(cell_icon, False)
        col.add_attribute(cell_icon, "text", 0)

        cell_name = Gtk.CellRendererText()
        cell_name.set_property("ellipsize", Pango.EllipsizeMode.END)
        col.pack_start(cell_name, True)
        col.add_attribute(cell_name, "text", 1)
        col.add_attribute(cell_name, "foreground", 4)
        col.add_attribute(cell_name, "weight", 5)

        self.tree.append_column(col)

        tree_scroll = Gtk.ScrolledWindow()
        tree_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tree_scroll.add(self.tree)
        paned.pack1(tree_scroll, resize=True, shrink=True)

        # Drag & drop — accept image files
        self.tree.drag_dest_set(
            Gtk.DestDefaults.ALL,
            [Gtk.TargetEntry.new("text/uri-list", 0, 0)],
            Gdk.DragAction.COPY,
        )

        # ── Detail pane ──
        detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        self.detail_header = Gtk.Label(xalign=0)
        self.detail_header.set_margin_start(8)
        self.detail_header.set_margin_top(4)
        detail_box.pack_start(self.detail_header, False, False, 0)

        self.detail_stack = Gtk.Stack()

        # Text detail page
        detail_scroll = Gtk.ScrolledWindow()
        detail_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        detail_scroll.set_min_content_height(80)
        self.detail_view = Gtk.TextView()
        self.detail_view.set_editable(False)
        self.detail_view.set_cursor_visible(False)
        self.detail_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.detail_view.set_left_margin(8)
        self.detail_view.set_right_margin(8)
        self.detail_view.set_top_margin(4)
        self.detail_view.set_bottom_margin(4)
        self.detail_view.get_style_context().add_class("ctx-detail")
        detail_scroll.add(self.detail_view)
        self.detail_stack.add_named(detail_scroll, "text")

        # Image detail page
        img_scroll = Gtk.ScrolledWindow()
        img_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.detail_image = Gtk.Image()
        self.detail_image.set_halign(Gtk.Align.CENTER)
        self.detail_image.set_valign(Gtk.Align.START)
        img_scroll.add(self.detail_image)
        self.detail_stack.add_named(img_scroll, "image")

        detail_box.pack_start(self.detail_stack, True, True, 0)

        paned.pack2(detail_box, resize=False, shrink=True)
        paned.set_position(300)

        # ── Buttons ──
        btn_box = Gtk.Box(spacing=4)
        btn_box.set_border_width(6)

        btn_add = Gtk.MenuButton(label="Add \u25be")
        btn_add.get_style_context().add_class("sidebar-btn")
        add_menu = Gtk.Menu()
        item_proj = Gtk.MenuItem(label="New Project")
        item_proj.connect("activate", lambda _: self._on_add_project())
        add_menu.append(item_proj)
        item_entry = Gtk.MenuItem(label="New Entry")
        item_entry.connect("activate", lambda _: self._on_add_entry())
        add_menu.append(item_entry)
        item_img = Gtk.MenuItem(label="Add Image")
        item_img.connect("activate", lambda _: self._on_add_image())
        add_menu.append(item_img)
        add_menu.show_all()
        btn_add.set_popup(add_menu)

        btn_edit = Gtk.Button(label="Edit")
        btn_edit.get_style_context().add_class("sidebar-btn")
        btn_edit.connect("clicked", lambda _: self._on_edit())

        btn_del = Gtk.Button(label="Delete")
        btn_del.get_style_context().add_class("sidebar-btn")
        btn_del.connect("clicked", lambda _: self._on_delete())

        btn_refresh = Gtk.Button(label="\u21bb")
        btn_refresh.get_style_context().add_class("sidebar-btn")
        btn_refresh.set_tooltip_text("Refresh")
        btn_refresh.connect("clicked", lambda _: self.refresh())

        btn_more = Gtk.MenuButton(label="\u22ee")
        btn_more.get_style_context().add_class("sidebar-btn")
        btn_more.set_tooltip_text("More actions")
        more_menu = Gtk.Menu()
        item_export = Gtk.MenuItem(label="Export\u2026")
        item_export.connect("activate", lambda _: self._on_export())
        more_menu.append(item_export)
        item_import = Gtk.MenuItem(label="Import\u2026")
        item_import.connect("activate", lambda _: self._on_import())
        more_menu.append(item_import)
        more_menu.show_all()
        btn_more.set_popup(more_menu)

        btn_box.pack_start(btn_add, True, True, 0)
        btn_box.pack_start(btn_edit, True, True, 0)
        btn_box.pack_start(btn_del, True, True, 0)
        btn_box.pack_start(btn_refresh, False, False, 0)
        btn_box.pack_start(btn_more, False, False, 0)
        self.pack_start(btn_box, False, False, 0)

        # Signals
        self.tree.connect("row-activated", self._on_row_activated)
        self.tree.connect("button-press-event", self._on_button_press)
        self.tree.connect("drag-data-received", self._on_drag_data_received)
        self.tree.get_selection().connect("changed", self._on_selection_changed)

        self.refresh()

    def refresh(self):
        """Reload all data from the ctx database."""
        expanded = _save_expanded(self.tree, self.store, 1)
        self.store.clear()
        self.detail_header.set_text("")
        self.detail_view.get_buffer().set_text("")
        self.detail_stack.set_visible_child_name("text")
        if not os.path.exists(CTX_DB):
            return

        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row
        _ensure_images_table()

        projects = db.execute(
            "SELECT name, description, work_dir FROM sessions ORDER BY name"
        ).fetchall()

        for proj in projects:
            proj_iter = self.store.append(None, [
                "\U0001f4c1",
                proj["name"],
                proj["name"],
                "",
                CATPPUCCIN["blue"],
                Pango.Weight.BOLD,
                "project",
            ])
            entries = db.execute(
                "SELECT key FROM contexts WHERE project = ? ORDER BY key",
                (proj["name"],),
            ).fetchall()
            for entry in entries:
                self.store.append(proj_iter, [
                    " ",
                    entry["key"],
                    proj["name"],
                    entry["key"],
                    CATPPUCCIN["text"],
                    Pango.Weight.NORMAL,
                    "entry",
                ])
            # Images
            images = db.execute(
                "SELECT filename, original_name FROM images "
                "WHERE project = ? ORDER BY added_at",
                (proj["name"],),
            ).fetchall()
            for img in images:
                self.store.append(proj_iter, [
                    "\U0001f5bc",
                    img["original_name"] or img["filename"],
                    proj["name"],
                    img["filename"],
                    CATPPUCCIN["green"],
                    Pango.Weight.NORMAL,
                    "image",
                ])

        # Shared entries
        shared = db.execute("SELECT key FROM shared ORDER BY key").fetchall()
        if shared:
            shared_iter = self.store.append(None, [
                "\U0001f517",
                "Shared",
                "__shared__",
                "",
                CATPPUCCIN["peach"],
                Pango.Weight.BOLD,
                "shared_root",
            ])
            for entry in shared:
                self.store.append(shared_iter, [
                    " ",
                    entry["key"],
                    "__shared__",
                    entry["key"],
                    CATPPUCCIN["text"],
                    Pango.Weight.NORMAL,
                    "shared_entry",
                ])

        db.close()
        if expanded:
            _restore_expanded(self.tree, self.store, 1, expanded)
        else:
            self.tree.expand_all()

    def _get_selected_info(self):
        """Returns (project_name, key, row_type) of selected row."""
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        if it is None:
            return None, None, None
        return model.get_value(it, 2), model.get_value(it, 3), model.get_value(it, 6)

    def _on_selection_changed(self, selection):
        model, it = selection.get_selected()
        if it is None:
            self.detail_header.set_text("")
            self.detail_view.get_buffer().set_text("")
            self.detail_stack.set_visible_child_name("text")
            return
        project = model.get_value(it, 2)
        key = model.get_value(it, 3)
        rtype = model.get_value(it, 6)
        if rtype == "image":
            self._show_image_detail(project, key)
        elif key:
            self._show_entry_detail(project, key)
            self.detail_stack.set_visible_child_name("text")
        else:
            self._show_project_detail(project)
            self.detail_stack.set_visible_child_name("text")

    def _show_project_detail(self, project):
        if not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row

        if project == "__shared__":
            self.detail_header.set_markup("<b>\U0001f517 Shared</b>")
            self.detail_view.get_buffer().set_text(
                "Shared context entries available to all projects."
            )
            db.close()
            return

        proj = db.execute(
            "SELECT description, work_dir FROM sessions WHERE name = ?",
            (project,),
        ).fetchone()
        if not proj:
            db.close()
            return

        self.detail_header.set_markup(
            f"<b>\U0001f4c1 {GLib.markup_escape_text(project)}</b>"
        )
        lines = []
        if proj["description"]:
            lines.append(proj["description"])
        if proj["work_dir"]:
            lines.append(f"Dir: {proj['work_dir']}")

        count = db.execute(
            "SELECT COUNT(*) FROM contexts WHERE project = ?", (project,)
        ).fetchone()[0]
        lines.append(f"Entries: {count}")

        img_count = db.execute(
            "SELECT COUNT(*) FROM images WHERE project = ?", (project,)
        ).fetchone()[0]
        if img_count:
            lines.append(f"Images: {img_count}")

        # Last summary
        summary = db.execute(
            "SELECT summary, created_at FROM summaries "
            "WHERE project = ? ORDER BY created_at DESC LIMIT 1",
            (project,),
        ).fetchone()
        if summary:
            lines.append(
                f"\n\u2500\u2500 Last summary ({summary['created_at'][:10]}) \u2500\u2500"
            )
            lines.append(summary["summary"])

        # Associated Claude session prompt
        for cs in self.app.claude_manager.all():
            cs_dir = cs.get("project_dir", "").rstrip("/")
            if cs_dir and os.path.basename(cs_dir) == project:
                prompt = cs.get("prompt", "")
                if prompt:
                    lines.append("\n\u2500\u2500 Introductory prompt \u2500\u2500")
                    lines.append(prompt)
                break

        self.detail_view.get_buffer().set_text("\n".join(lines))
        db.close()

    def _show_entry_detail(self, project, key):
        if not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        if project == "__shared__":
            row = db.execute(
                "SELECT value FROM shared WHERE key = ?", (key,)
            ).fetchone()
        else:
            row = db.execute(
                "SELECT value FROM contexts WHERE project = ? AND key = ?",
                (project, key),
            ).fetchone()
        if row:
            self.detail_header.set_markup(
                f"<b>{GLib.markup_escape_text(key)}</b>"
            )
            self.detail_view.get_buffer().set_text(row[0])
        db.close()

    def _show_image_detail(self, project, filename):
        """Show image preview in detail pane."""
        self.detail_header.set_markup(
            f"<b>\U0001f5bc {GLib.markup_escape_text(filename)}</b>"
        )
        path = os.path.join(CTX_IMAGES_DIR, project, filename)
        if not os.path.exists(path):
            self.detail_view.get_buffer().set_text("Image file not found.")
            self.detail_stack.set_visible_child_name("text")
            return
        try:
            pixbuf = GdkPixbuf.Pixbuf.new_from_file(path)
            max_w, max_h = 230, 400
            w, h = pixbuf.get_width(), pixbuf.get_height()
            if w > max_w or h > max_h:
                scale = min(max_w / w, max_h / h)
                pixbuf = pixbuf.scale_simple(
                    int(w * scale), int(h * scale),
                    GdkPixbuf.InterpType.BILINEAR,
                )
            self.detail_image.set_from_pixbuf(pixbuf)
            self.detail_stack.set_visible_child_name("image")
        except Exception:
            self.detail_view.get_buffer().set_text("Failed to load image.")
            self.detail_stack.set_visible_child_name("text")

    def _on_row_activated(self, tree, path, column):
        self._on_edit()

    def _on_button_press(self, widget, event):
        if event.button != 3:
            return False
        path_info = self.tree.get_path_at_pos(int(event.x), int(event.y))
        if not path_info:
            return True
        path = path_info[0]
        self.tree.get_selection().select_path(path)
        it = self.store.get_iter(path)
        project = self.store.get_value(it, 2)
        key = self.store.get_value(it, 3)
        rtype = self.store.get_value(it, 6)

        menu = Gtk.Menu()
        if rtype == "project":
            item_add = Gtk.MenuItem(label="Add Entry")
            item_add.connect("activate", lambda _: self._on_add_entry())
            menu.append(item_add)

            item_add_img = Gtk.MenuItem(label="Add Image")
            item_add_img.connect("activate", lambda _: self._on_add_image())
            menu.append(item_add_img)

            item_paste_img = Gtk.MenuItem(label="Paste Image from Clipboard")
            item_paste_img.set_sensitive(_clipboard_has_image_or_path())
            item_paste_img.connect(
                "activate", lambda _, p=project: self._on_paste_image(p)
            )
            menu.append(item_paste_img)

            menu.append(Gtk.SeparatorMenuItem())

            item_edit = Gtk.MenuItem(label="Edit Project")
            item_edit.connect("activate", lambda _: self._on_edit())
            menu.append(item_edit)

            menu.append(Gtk.SeparatorMenuItem())

            item_del = Gtk.MenuItem(label="Delete Project")
            item_del.connect("activate", lambda _: self._on_delete())
            menu.append(item_del)
        elif rtype == "image":
            item_del = Gtk.MenuItem(label="Delete Image")
            item_del.connect(
                "activate", lambda _, p=project, f=key: self._delete_image(p, f)
            )
            menu.append(item_del)
        elif rtype in ("entry", "shared_entry"):
            item_edit = Gtk.MenuItem(label="Edit Entry")
            item_edit.connect("activate", lambda _: self._on_edit())
            menu.append(item_edit)

            item_del = Gtk.MenuItem(label="Delete Entry")
            item_del.connect("activate", lambda _: self._on_delete())
            menu.append(item_del)

        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _on_add_project(self):
        dlg = _CtxProjectDialog(self.app, "New Project")
        if dlg.run() == Gtk.ResponseType.OK:
            name, desc, work_dir = dlg.get_data()
            if name and desc:
                args = ["ctx", "init", name, desc]
                if work_dir:
                    args.append(work_dir)
                subprocess.run(args, capture_output=True, text=True)
                self.refresh()
        dlg.destroy()

    def _on_add_entry(self):
        project, _, _ = self._get_selected_info()
        if not project or project == "__shared__":
            return
        dlg = _CtxEntryDialog(self.app, f"Add entry to {project}")
        if dlg.run() == Gtk.ResponseType.OK:
            key, value = dlg.get_data()
            if key:
                subprocess.run(
                    ["ctx", "set", project, key, value],
                    capture_output=True, text=True,
                )
                self.refresh()
        dlg.destroy()

    def _on_edit(self):
        project, key, rtype = self._get_selected_info()
        if not project:
            return
        if rtype == "image":
            return  # images are not editable
        if project == "__shared__":
            if key:
                self._edit_shared_entry(key)
            return
        if key:
            self._edit_entry(project, key)
        else:
            self._edit_project(project)

    def _edit_project(self, project):
        if not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        row = db.execute(
            "SELECT description, work_dir FROM sessions WHERE name = ?",
            (project,),
        ).fetchone()
        db.close()
        if not row:
            return
        dlg = _CtxProjectDialog(
            self.app, "Edit Project", project, row[0] or "", row[1] or ""
        )
        dlg.entry_name.set_sensitive(False)
        if dlg.run() == Gtk.ResponseType.OK:
            _, desc, work_dir = dlg.get_data()
            if desc:
                args = ["ctx", "init", project, desc]
                if work_dir:
                    args.append(work_dir)
                subprocess.run(args, capture_output=True, text=True)
                self.refresh()
        dlg.destroy()

    def _edit_entry(self, project, key):
        if not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        row = db.execute(
            "SELECT value FROM contexts WHERE project = ? AND key = ?",
            (project, key),
        ).fetchone()
        db.close()
        if not row:
            return
        dlg = _CtxEntryDialog(self.app, f"Edit: {key}", key, row[0])
        if dlg.run() == Gtk.ResponseType.OK:
            new_key, value = dlg.get_data()
            if new_key:
                if new_key != key:
                    subprocess.run(
                        ["ctx", "delete", project, key],
                        capture_output=True, text=True,
                    )
                subprocess.run(
                    ["ctx", "set", project, new_key, value],
                    capture_output=True, text=True,
                )
                self.refresh()
        dlg.destroy()

    def _edit_shared_entry(self, key):
        if not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        row = db.execute(
            "SELECT value FROM shared WHERE key = ?", (key,)
        ).fetchone()
        db.close()
        if not row:
            return
        dlg = _CtxEntryDialog(self.app, f"Edit shared: {key}", key, row[0])
        if dlg.run() == Gtk.ResponseType.OK:
            new_key, value = dlg.get_data()
            if new_key:
                db = sqlite3.connect(CTX_DB)
                if new_key != key:
                    db.execute("DELETE FROM shared WHERE key = ?", (key,))
                db.execute(
                    "INSERT OR REPLACE INTO shared (key, value, updated_at) "
                    "VALUES (?, ?, datetime('now'))",
                    (new_key, value),
                )
                db.commit()
                db.close()
                self.refresh()
        dlg.destroy()

    def _on_delete(self):
        project, key, rtype = self._get_selected_info()
        if not project:
            return
        if rtype == "image":
            self._delete_image(project, key)
        elif key:
            self._delete_entry(project, key)
        elif project != "__shared__":
            self._delete_project(project)

    def _delete_entry(self, project, key):
        dlg = Gtk.MessageDialog(
            transient_for=self.app,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f'Delete entry "{key}" from {project}?',
        )
        if dlg.run() == Gtk.ResponseType.YES:
            if project == "__shared__":
                db = sqlite3.connect(CTX_DB)
                db.execute("DELETE FROM shared WHERE key = ?", (key,))
                db.commit()
                db.close()
            else:
                subprocess.run(
                    ["ctx", "delete", project, key],
                    capture_output=True, text=True,
                )
            self.refresh()
        dlg.destroy()

    def _delete_project(self, project):
        dlg = Gtk.MessageDialog(
            transient_for=self.app,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f'Delete project "{project}" and all its entries?',
        )
        if dlg.run() == Gtk.ResponseType.YES:
            subprocess.run(
                ["ctx", "delete", project],
                capture_output=True, text=True,
            )
            self.refresh()
        dlg.destroy()

    def _on_add_image(self):
        """Add image from file chooser to selected project."""
        project, _, _ = self._get_selected_info()
        if not project or project == "__shared__":
            return
        dlg = Gtk.FileChooserDialog(
            title=f"Add image to {project}",
            parent=self.app,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        filt = Gtk.FileFilter()
        filt.set_name("Images")
        for mime in ("image/png", "image/jpeg", "image/gif", "image/webp", "image/bmp"):
            filt.add_mime_type(mime)
        dlg.add_filter(filt)
        filt_all = Gtk.FileFilter()
        filt_all.set_name("All files")
        filt_all.add_pattern("*")
        dlg.add_filter(filt_all)
        if dlg.run() == Gtk.ResponseType.OK:
            path = dlg.get_filename()
            if path:
                _save_ctx_image(project, path)
                self.refresh()
        dlg.destroy()

    def _on_paste_image(self, project=None):
        """Paste image (bitmap or file path) from clipboard to a project."""
        if not project:
            project, _, _ = self._get_selected_info()
        if not project or project == "__shared__":
            return
        pixbuf, file_path = _clipboard_get_image_or_path()
        if pixbuf or file_path:
            source = pixbuf if pixbuf else file_path
            _save_ctx_image(project, source)
            self.refresh()

    def _delete_image(self, project, filename):
        """Delete an image with confirmation."""
        dlg = Gtk.MessageDialog(
            transient_for=self.app,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete image from {project}?",
        )
        if dlg.run() == Gtk.ResponseType.YES:
            _delete_ctx_image(project, filename)
            self.refresh()
        dlg.destroy()

    def _on_drag_data_received(self, widget, context, x, y, data, info, time):
        """Handle image files dropped on the tree."""
        uris = data.get_uris()
        if not uris:
            return
        path_info = self.tree.get_dest_row_at_pos(x, y)
        if not path_info:
            return
        tree_path, _ = path_info
        it = self.store.get_iter(tree_path)
        # Walk up to project row
        parent = self.store.iter_parent(it)
        if parent:
            it = parent
        project = self.store.get_value(it, 2)
        rtype = self.store.get_value(it, 6)
        if not project or project == "__shared__" or rtype not in ("project",):
            return
        img_exts = (".png", ".jpg", ".jpeg", ".gif", ".webp", ".bmp")
        added = False
        for uri in uris:
            if uri.startswith("file://"):
                filepath = GLib.filename_from_uri(uri)[0]
                if filepath.lower().endswith(img_exts):
                    _save_ctx_image(project, filepath)
                    added = True
        if added:
            self.refresh()

    def _on_export(self):
        dlg = _CtxExportDialog(self.app)
        if dlg.run() == Gtk.ResponseType.OK:
            data = dlg.get_export_data()
            if data:
                save_dlg = Gtk.FileChooserDialog(
                    title="Save export file",
                    parent=self.app,
                    action=Gtk.FileChooserAction.SAVE,
                )
                save_dlg.add_buttons(
                    Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                    Gtk.STOCK_SAVE, Gtk.ResponseType.OK,
                )
                save_dlg.set_do_overwrite_confirmation(True)
                save_dlg.set_current_name("ctx_export.json")
                filt = Gtk.FileFilter()
                filt.set_name("JSON files")
                filt.add_pattern("*.json")
                save_dlg.add_filter(filt)
                if save_dlg.run() == Gtk.ResponseType.OK:
                    path = save_dlg.get_filename()
                    try:
                        with open(path, "w") as f:
                            json.dump(data, f, indent=2, ensure_ascii=False)
                    except OSError as e:
                        err = Gtk.MessageDialog(
                            transient_for=self.app,
                            modal=True,
                            message_type=Gtk.MessageType.ERROR,
                            buttons=Gtk.ButtonsType.OK,
                            text=f"Failed to save: {e}",
                        )
                        err.run()
                        err.destroy()
                save_dlg.destroy()
        dlg.destroy()

    def _on_import(self):
        dlg = _CtxImportDialog(self.app)
        if dlg.run() == Gtk.ResponseType.OK:
            data, overwrite = dlg.get_selected_data()
            if data:
                self._do_import(data, overwrite)
                self.refresh()
        dlg.destroy()

    def _do_import(self, data, overwrite):
        import base64
        # Ensure database and tables exist
        subprocess.run(["ctx", "list"], capture_output=True, text=True)
        if not os.path.exists(CTX_DB):
            return

        db = sqlite3.connect(CTX_DB)
        mode = "REPLACE" if overwrite else "IGNORE"

        for session in data.get("sessions", []):
            db.execute(
                f"INSERT OR {mode} INTO sessions (name, description, work_dir, created_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    session["name"],
                    session.get("description", ""),
                    session.get("work_dir", ""),
                    session.get("created_at", ""),
                ),
            )

        for ctx in data.get("contexts", []):
            db.execute(
                f"INSERT OR {mode} INTO contexts (project, key, value, updated_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    ctx["project"],
                    ctx["key"],
                    ctx["value"],
                    ctx.get("updated_at", ""),
                ),
            )

        for shared in data.get("shared", []):
            db.execute(
                f"INSERT OR {mode} INTO shared (key, value, updated_at) "
                "VALUES (?, ?, ?)",
                (
                    shared["key"],
                    shared["value"],
                    shared.get("updated_at", ""),
                ),
            )

        for summary in data.get("summaries", []):
            db.execute(
                "INSERT INTO summaries (project, summary, created_at) "
                "VALUES (?, ?, ?)",
                (
                    summary["project"],
                    summary["summary"],
                    summary.get("created_at", ""),
                ),
            )

        db.commit()
        db.close()

        # Import images (files + DB entries)
        _ensure_images_table()
        for img in data.get("images", []):
            img_data = img.get("data")
            if not img_data:
                continue
            project = img["project"]
            proj_dir = os.path.join(CTX_IMAGES_DIR, project)
            os.makedirs(proj_dir, exist_ok=True)
            filename = img["filename"]
            dest = os.path.join(proj_dir, filename)
            if not overwrite and os.path.exists(dest):
                continue
            with open(dest, "wb") as f:
                f.write(base64.b64decode(img_data))
            db = sqlite3.connect(CTX_DB)
            db.execute(
                f"INSERT OR {mode} INTO images "
                "(project, filename, original_name, added_at) "
                "VALUES (?, ?, ?, ?)",
                (
                    project, filename,
                    img.get("original_name", filename),
                    img.get("added_at", ""),
                ),
            )
            db.commit()
            db.close()


# ─── ConsultPanel ────────────────────────────────────────────────────────────


class ConsultPanel(Gtk.Box):
    """Sidebar panel for managing external AI model consultation via OpenRouter."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self.manager = ConsultManager()

        # ── API Key section ──
        key_box = Gtk.Box(spacing=4)
        key_box.set_border_width(6)

        key_label = Gtk.Label(label="API Key:")
        key_label.set_xalign(0)
        key_box.pack_start(key_label, False, False, 0)

        self.key_entry = Gtk.Entry()
        self.key_entry.set_visibility(False)
        self.key_entry.set_text(self.manager.get_api_key())
        self.key_entry.set_placeholder_text("sk-or-...")
        key_box.pack_start(self.key_entry, True, True, 0)

        eye_btn = Gtk.ToggleButton(label="Show")
        eye_btn.get_style_context().add_class("sidebar-btn")
        eye_btn.set_relief(Gtk.ReliefStyle.NONE)
        eye_btn.connect(
            "toggled", lambda b: self.key_entry.set_visibility(b.get_active())
        )
        key_box.pack_start(eye_btn, False, False, 0)

        save_key_btn = Gtk.Button(label="Save")
        save_key_btn.get_style_context().add_class("sidebar-btn")
        save_key_btn.connect("clicked", self._on_save_key)
        key_box.pack_start(save_key_btn, False, False, 0)

        self.pack_start(key_box, False, False, 0)

        # ── Separator ──
        self.pack_start(Gtk.Separator(), False, False, 0)

        # ── Default model label ──
        self.default_label = Gtk.Label()
        self.default_label.set_xalign(0)
        self.default_label.set_margin_start(8)
        self.default_label.set_margin_top(4)
        self.default_label.set_margin_bottom(4)
        self.pack_start(self.default_label, False, False, 0)

        # ── Model list ──
        # Columns: enabled(bool), default_star(str), name(str), model_id(str)
        self.store = Gtk.ListStore(bool, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(False)
        self.tree.set_activate_on_single_click(False)

        # Toggle column
        toggle_renderer = Gtk.CellRendererToggle()
        toggle_renderer.connect("toggled", self._on_toggle)
        col_toggle = Gtk.TreeViewColumn("", toggle_renderer, active=0)
        col_toggle.set_min_width(30)
        self.tree.append_column(col_toggle)

        # Star + name column
        col_main = Gtk.TreeViewColumn()

        cell_star = Gtk.CellRendererText()
        col_main.pack_start(cell_star, False)
        col_main.add_attribute(cell_star, "text", 1)
        col_main.add_attribute(cell_star, "foreground", 1)
        # Use a cell data func to color the star
        col_main.set_cell_data_func(
            cell_star,
            lambda col, cell, model, it, _: cell.set_property(
                "foreground", CATPPUCCIN["yellow"] if model[it][1] else None
            ),
        )

        cell_name = Gtk.CellRendererText()
        cell_name.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_main.pack_start(cell_name, True)
        col_main.add_attribute(cell_name, "text", 2)

        self.tree.append_column(col_main)

        tree_scroll = Gtk.ScrolledWindow()
        tree_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tree_scroll.add(self.tree)
        self.pack_start(tree_scroll, True, True, 0)

        # ── Buttons row 1 ──
        btn_box = Gtk.Box(spacing=4)
        btn_box.set_border_width(6)

        btn_default = Gtk.Button(label="Set Default")
        btn_default.get_style_context().add_class("sidebar-btn")
        btn_default.connect("clicked", self._on_set_default)
        btn_box.pack_start(btn_default, True, True, 0)

        btn_add = Gtk.Button(label="Add")
        btn_add.get_style_context().add_class("sidebar-btn")
        btn_add.connect("clicked", self._on_add_model)
        btn_box.pack_start(btn_add, True, True, 0)

        btn_remove = Gtk.Button(label="Remove")
        btn_remove.get_style_context().add_class("sidebar-btn")
        btn_remove.connect("clicked", self._on_remove_model)
        btn_box.pack_start(btn_remove, True, True, 0)

        self.pack_start(btn_box, False, False, 0)

        # ── Buttons row 2 ──
        btn_box2 = Gtk.Box(spacing=4)
        btn_box2.set_border_width(6)
        btn_box2.set_margin_top(0)

        self.btn_fetch = Gtk.Button(label="Fetch Models from OpenRouter")
        self.btn_fetch.get_style_context().add_class("sidebar-btn")
        self.btn_fetch.connect("clicked", self._on_fetch_models)
        btn_box2.pack_start(self.btn_fetch, True, True, 0)

        self.pack_start(btn_box2, False, False, 0)

        # ── Tribunal section ──
        self.pack_start(Gtk.Separator(), False, False, 4)

        tribunal_header = Gtk.Label()
        tribunal_header.set_markup(
            f'<span foreground="{CATPPUCCIN["subtext0"]}">'
            f"Multi-Agent Debate</span>"
        )
        tribunal_header.set_xalign(0)
        tribunal_header.set_margin_start(8)
        tribunal_header.set_margin_top(2)
        self.pack_start(tribunal_header, False, False, 0)

        # Role dropdowns
        self.tribunal_combos = {}
        roles_grid = Gtk.Grid()
        roles_grid.set_column_spacing(4)
        roles_grid.set_row_spacing(2)
        roles_grid.set_border_width(6)

        for i, role in enumerate(("analyst", "advocate", "critic", "arbiter")):
            lbl = Gtk.Label(label=f"{role.title()}:")
            lbl.set_xalign(1)
            lbl.set_margin_end(4)
            roles_grid.attach(lbl, 0, i, 1, 1)

            combo = Gtk.ComboBoxText()
            combo.set_hexpand(True)
            roles_grid.attach(combo, 1, i, 1, 1)
            self.tribunal_combos[role] = combo

        self.pack_start(roles_grid, False, False, 0)

        # Rounds spinner
        rounds_box = Gtk.Box(spacing=4)
        rounds_box.set_border_width(6)
        rounds_lbl = Gtk.Label(label="Rounds:")
        rounds_lbl.set_xalign(0)
        rounds_box.pack_start(rounds_lbl, False, False, 0)
        self.rounds_spin = Gtk.SpinButton.new_with_range(1, 6, 1)
        self.rounds_spin.set_value(3)
        rounds_box.pack_start(self.rounds_spin, False, False, 0)
        self.single_pass_check = Gtk.CheckButton(label="Single pass")
        rounds_box.pack_start(self.single_pass_check, False, False, 4)
        self.pack_start(rounds_box, False, False, 0)

        # Project directory
        proj_lbl = Gtk.Label()
        proj_lbl.set_markup(
            f'<span foreground="{CATPPUCCIN["subtext0"]}">Project dir:</span>'
        )
        proj_lbl.set_xalign(0)
        proj_lbl.set_margin_start(8)
        self.pack_start(proj_lbl, False, False, 0)

        proj_box = Gtk.Box(spacing=4)
        proj_box.set_border_width(6)
        self.project_combo = Gtk.ComboBoxText()
        self.project_combo.set_hexpand(True)
        self.project_combo.connect("changed", self._on_project_combo_changed)
        proj_box.pack_start(self.project_combo, True, True, 0)
        self.pack_start(proj_box, False, False, 0)

        dir_entry_box = Gtk.Box(spacing=4)
        dir_entry_box.set_border_width(6)
        dir_entry_box.set_margin_top(0)
        self.project_dir_entry = Gtk.Entry()
        self.project_dir_entry.set_placeholder_text("Override path or pick from dropdown")
        dir_entry_box.pack_start(self.project_dir_entry, True, True, 0)
        browse_btn = Gtk.Button(label="...")
        browse_btn.set_tooltip_text("Browse")
        browse_btn.get_style_context().add_class("sidebar-btn")
        browse_btn.connect("clicked", self._on_browse_project_dir)
        dir_entry_box.pack_start(browse_btn, False, False, 0)
        self.pack_start(dir_entry_box, False, False, 0)

        self._refresh_project_combo()

        # Problem text
        problem_lbl = Gtk.Label()
        problem_lbl.set_markup(
            f'<span foreground="{CATPPUCCIN["subtext0"]}">Problem:</span>'
        )
        problem_lbl.set_xalign(0)
        problem_lbl.set_margin_start(8)
        self.pack_start(problem_lbl, False, False, 0)

        problem_scroll = Gtk.ScrolledWindow()
        problem_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        problem_scroll.set_min_content_height(60)
        problem_scroll.set_max_content_height(120)
        problem_scroll.set_border_width(6)
        self.problem_text = Gtk.TextView()
        self.problem_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.problem_text.set_left_margin(4)
        self.problem_text.set_right_margin(4)
        self.problem_text.set_top_margin(4)
        self.problem_text.set_bottom_margin(4)
        problem_scroll.add(self.problem_text)
        self.pack_start(problem_scroll, False, False, 0)

        # Run + Save buttons
        run_box = Gtk.Box(spacing=4)
        run_box.set_border_width(6)
        self.btn_save_preset = Gtk.Button(label="Save")
        self.btn_save_preset.set_tooltip_text("Save tribunal settings for selected project")
        self.btn_save_preset.get_style_context().add_class("sidebar-btn")
        self.btn_save_preset.connect("clicked", self._on_save_preset)
        run_box.pack_start(self.btn_save_preset, False, False, 0)
        self.btn_debate = Gtk.Button(label="Run Debate")
        self.btn_debate.get_style_context().add_class("sidebar-btn")
        self.btn_debate.connect("clicked", self._on_run_debate)
        run_box.pack_start(self.btn_debate, True, True, 0)
        self.pack_start(run_box, False, False, 0)

        # ── CLI info ──
        info_label = Gtk.Label()
        info_label.set_markup(
            f'<span size="small" foreground="{CATPPUCCIN["overlay1"]}">'
            "CLI: consult \"q\" | consult debate \"problem\" | consult models"
            "</span>"
        )
        info_label.set_xalign(0)
        info_label.set_line_wrap(True)
        info_label.set_margin_start(8)
        info_label.set_margin_bottom(6)
        self.pack_start(info_label, False, False, 0)

        self.refresh()

    def refresh(self):
        """Reload model list from config."""
        self.store.clear()
        self.manager.load()
        default = self.manager.get_default_model()
        models = self.manager.get_models()

        default_name = models.get(default, {}).get("name", default)
        self.default_label.set_markup(
            f'<span foreground="{CATPPUCCIN["subtext0"]}">'
            f"Default: </span>"
            f'<span foreground="{CATPPUCCIN["yellow"]}">'
            f"{default_name}</span>"
        )

        # Sort: enabled first, then by source (openrouter first), then alphabetically
        sorted_ids = sorted(
            models.keys(),
            key=lambda m: (
                not models[m].get("enabled", False),
                0 if models[m].get("source", "openrouter") == "openrouter" else 1,
                m,
            ),
        )

        for mid in sorted_ids:
            info = models[mid]
            star = " \u2605 " if mid == default else "   "
            source = info.get("source", "openrouter")
            src_tag = "[CC]" if source == "claude-code" else "[OR]"
            name = f"{src_tag} {info.get('name', mid)}  ({mid})"
            self.store.append([info.get("enabled", False), star, name, mid])

        # Refresh tribunal dropdowns
        enabled_models = [
            mid for mid in sorted_ids if models[mid].get("enabled", False)
        ]
        tribunal_cfg = self.manager.config.get("tribunal", {})

        for role, combo in self.tribunal_combos.items():
            combo.remove_all()
            saved = tribunal_cfg.get(f"{role}_model", "")
            active_idx = 0
            for i, mid in enumerate(enabled_models):
                source = models[mid].get("source", "openrouter")
                src_tag = "[CC]" if source == "claude-code" else "[OR]"
                name = models[mid].get("name", mid)
                combo.append(mid, f"{src_tag} {name}")
                if mid == saved:
                    active_idx = i
            if enabled_models:
                combo.set_active(active_idx)

        max_rounds = tribunal_cfg.get("max_rounds", 3)
        self.rounds_spin.set_value(max_rounds)

        # Refresh project dropdown
        self._refresh_project_combo()

    def _on_save_key(self, btn):
        key = self.key_entry.get_text().strip()
        self.manager.set_api_key(key)

    def _on_toggle(self, renderer, path):
        it = self.store.get_iter(path)
        enabled = not self.store[it][0]
        model_id = self.store[it][3]
        self.store[it][0] = enabled
        self.manager.set_model_enabled(model_id, enabled)
        self.refresh()

    def _on_set_default(self, btn):
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        if not it:
            return
        model_id = self.store[it][3]
        self.manager.set_default_model(model_id)
        self.refresh()

    def _on_add_model(self, btn):
        dlg = Gtk.Dialog(
            title="Add Model",
            transient_for=self.app,
            modal=True,
            destroy_with_parent=True,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)

        lbl_id = Gtk.Label(label="Model ID (e.g. google/gemini-2.5-pro):")
        lbl_id.set_xalign(0)
        box.add(lbl_id)
        entry_id = Gtk.Entry()
        entry_id.set_placeholder_text("provider/model-name")
        box.add(entry_id)

        lbl_name = Gtk.Label(label="Display Name:")
        lbl_name.set_xalign(0)
        box.add(lbl_name)
        entry_name = Gtk.Entry()
        entry_name.set_placeholder_text("Model Name")
        box.add(entry_name)

        dlg.show_all()

        while True:
            resp = dlg.run()
            if resp != Gtk.ResponseType.OK:
                break
            mid = entry_id.get_text().strip()
            name = entry_name.get_text().strip() or mid
            if not mid:
                continue
            self.manager.add_model(mid, name)
            self.refresh()
            break
        dlg.destroy()

    def _on_remove_model(self, btn):
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        if not it:
            return
        model_id = self.store[it][3]
        self.manager.remove_model(model_id)
        self.refresh()

    def _refresh_project_combo(self):
        """Populate project dropdown from Claude sessions with project_dir."""
        self.project_combo.remove_all()
        self.project_combo.append("", "(none)")
        seen = set()
        for s in self.app.claude_manager.all():
            pdir = s.get("project_dir", "").strip()
            if pdir and pdir not in seen:
                seen.add(pdir)
                name = s.get("name", "") or os.path.basename(pdir.rstrip("/"))
                self.project_combo.append(pdir, f"{name}  ({pdir})")
        self.project_combo.set_active(0)

    def _on_project_combo_changed(self, combo):
        """When a project is selected from dropdown, fill the entry and load preset."""
        pdir = combo.get_active_id() or ""
        self.project_dir_entry.set_text(pdir)
        if pdir:
            self._load_project_preset(pdir)

    def _load_project_preset(self, project_dir):
        """Load saved tribunal settings for the given project dir into UI."""
        preset = self.manager.get_project_preset(project_dir)
        if not preset:
            return
        for role, combo in self.tribunal_combos.items():
            saved = preset.get(f"{role}_model", "")
            if saved:
                combo.set_active_id(saved)
        if "max_rounds" in preset:
            self.rounds_spin.set_value(preset["max_rounds"])
        if "single_pass" in preset:
            self.single_pass_check.set_active(preset["single_pass"])

    def _on_save_preset(self, btn):
        """Save current tribunal settings for the selected project."""
        pdir = self.project_dir_entry.get_text().strip()
        if not pdir:
            dlg = Gtk.MessageDialog(
                transient_for=self.app,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Select a project directory first.",
            )
            dlg.run()
            dlg.destroy()
            return

        models = {}
        for role, combo in self.tribunal_combos.items():
            mid = combo.get_active_id()
            if mid:
                models[f"{role}_model"] = mid

        preset = {
            **models,
            "max_rounds": int(self.rounds_spin.get_value()),
            "single_pass": self.single_pass_check.get_active(),
        }
        self.manager.save_project_preset(pdir, preset)

    def _on_browse_project_dir(self, btn):
        """Open file chooser for project directory."""
        dlg = Gtk.FileChooserDialog(
            title="Select project directory",
            parent=self.app,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        current = self.project_dir_entry.get_text().strip()
        if current and os.path.isdir(current):
            dlg.set_current_folder(current)
        if dlg.run() == Gtk.ResponseType.OK:
            self.project_dir_entry.set_text(dlg.get_filename())
        dlg.destroy()

    def _get_debate_project_dir(self):
        """Return project dir for debate: entry overrides combo, fallback to HOME."""
        path = self.project_dir_entry.get_text().strip()
        if path and os.path.isdir(path):
            return path
        return os.environ.get("HOME", "/")

    def _on_run_debate(self, btn):
        """Launch a tribunal debate in a new terminal tab."""
        buf = self.problem_text.get_buffer()
        problem = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        if not problem:
            dlg = Gtk.MessageDialog(
                transient_for=self.app,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Enter a problem statement first.",
            )
            dlg.run()
            dlg.destroy()
            return

        # Gather selected models
        models = {}
        for role, combo in self.tribunal_combos.items():
            mid = combo.get_active_id()
            if mid:
                models[role] = mid

        if len(models) < 4:
            dlg = Gtk.MessageDialog(
                transient_for=self.app,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Select a model for each role.",
            )
            dlg.run()
            dlg.destroy()
            return

        # Check API key only if any OpenRouter models are used
        needs_api_key = any(
            not mid.startswith("claude-code/") for mid in models.values()
        )
        if needs_api_key and not self.manager.get_api_key():
            dlg = Gtk.MessageDialog(
                transient_for=self.app,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Set an API key first (needed for OpenRouter models).",
            )
            dlg.run()
            dlg.destroy()
            return

        # Save tribunal config (global + per-project)
        self.manager.load()
        if "tribunal" not in self.manager.config:
            self.manager.config["tribunal"] = {}
        self.manager.config["tribunal"]["analyst_model"] = models["analyst"]
        self.manager.config["tribunal"]["advocate_model"] = models["advocate"]
        self.manager.config["tribunal"]["critic_model"] = models["critic"]
        self.manager.config["tribunal"]["arbiter_model"] = models["arbiter"]
        self.manager.config["tribunal"]["max_rounds"] = int(self.rounds_spin.get_value())
        self.manager.save()

        # Auto-save per-project preset
        pdir = self.project_dir_entry.get_text().strip()
        if pdir:
            self._on_save_preset(None)

        # Build command
        rounds = int(self.rounds_spin.get_value())
        single = self.single_pass_check.get_active()

        # Escape problem for shell
        escaped = problem.replace("'", "'\\''")
        cmd = (
            f"consult debate '{escaped}'"
            f" --analyst {models['analyst']}"
            f" --advocate {models['advocate']}"
            f" --critic {models['critic']}"
            f" --arbiter {models['arbiter']}"
            f" --rounds {rounds}"
        )
        if single:
            cmd += " --single-pass"

        script = f"{cmd}\nexec bash\n"

        # Open new terminal tab
        tab = TerminalTab(self.app)
        label = self.app._build_tab_label("Tribunal", tab)
        idx = self.app.notebook.append_page(tab, label)
        self.app.notebook.set_current_page(idx)
        self.app.notebook.set_tab_reorderable(tab, True)

        project_dir = self._get_debate_project_dir()

        tab.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            project_dir,
            ["/bin/bash", "-c", script],
            None,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            None,
        )

    def _on_fetch_models(self, btn):
        """Fetch available models from OpenRouter in background thread."""
        api_key = self.manager.get_api_key()
        if not api_key:
            dlg = Gtk.MessageDialog(
                transient_for=self.app,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Set an API key first.",
            )
            dlg.run()
            dlg.destroy()
            return

        btn.set_sensitive(False)
        btn.set_label("Fetching...")

        def fetch():
            try:
                req = urllib.request.Request(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                models = data.get("data", [])
                GLib.idle_add(self._show_model_picker, models)
            except Exception as e:
                GLib.idle_add(self._fetch_error, str(e))
            finally:
                GLib.idle_add(self._fetch_done)

        threading.Thread(target=fetch, daemon=True).start()

    def _fetch_done(self):
        self.btn_fetch.set_sensitive(True)
        self.btn_fetch.set_label("Fetch Models from OpenRouter")

    def _fetch_error(self, msg):
        dlg = Gtk.MessageDialog(
            transient_for=self.app,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=f"Fetch failed: {msg}",
        )
        dlg.run()
        dlg.destroy()

    def _show_model_picker(self, models):
        """Show dialog for selecting models from OpenRouter catalog."""
        dlg = Gtk.Dialog(
            title="Select Models from OpenRouter",
            transient_for=self.app,
            modal=True,
        )
        dlg.set_default_size(550, 500)
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            "Add Selected", Gtk.ResponseType.OK,
        )

        box = dlg.get_content_area()
        box.set_spacing(4)

        # Search entry
        search = Gtk.SearchEntry()
        search.set_placeholder_text("Filter models...")
        search.set_margin_start(8)
        search.set_margin_end(8)
        search.set_margin_top(4)
        box.pack_start(search, False, False, 0)

        # Info label
        info = Gtk.Label()
        info.set_markup(
            f'<span size="small" foreground="{CATPPUCCIN["overlay1"]}">'
            f"{len(models)} models available. Check ones to add.</span>"
        )
        info.set_xalign(0)
        info.set_margin_start(8)
        box.pack_start(info, False, False, 0)

        # Model list: selected(bool), name(str), id(str), pricing(str)
        pick_store = Gtk.ListStore(bool, str, str, str)
        existing = set(self.manager.get_models().keys())

        for m in sorted(models, key=lambda x: x.get("id", "")):
            mid = m.get("id", "")
            name = m.get("name", mid)
            pricing = m.get("pricing", {})
            price_str = ""
            if pricing:
                try:
                    pp = float(pricing.get("prompt", "0")) * 1_000_000
                    cp = float(pricing.get("completion", "0")) * 1_000_000
                    price_str = f"${pp:.2f} / ${cp:.2f} per 1M"
                except (ValueError, TypeError):
                    pass
            pick_store.append([mid in existing, name, mid, price_str])

        # Filterable model
        filter_model = pick_store.filter_new()

        def visible_func(model, it, _data):
            text = search.get_text().lower()
            if not text:
                return True
            return text in model[it][1].lower() or text in model[it][2].lower()

        filter_model.set_visible_func(visible_func)
        search.connect("search-changed", lambda _: filter_model.refilter())

        tree = Gtk.TreeView(model=filter_model)
        tree.set_headers_visible(True)

        toggle = Gtk.CellRendererToggle()

        def on_pick_toggle(_renderer, path):
            real_it = filter_model.convert_iter_to_child_iter(
                filter_model.get_iter(path)
            )
            pick_store[real_it][0] = not pick_store[real_it][0]

        toggle.connect("toggled", on_pick_toggle)
        col_sel = Gtk.TreeViewColumn("", toggle, active=0)
        col_sel.set_min_width(30)
        tree.append_column(col_sel)

        cell_name = Gtk.CellRendererText()
        cell_name.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_name = Gtk.TreeViewColumn("Model", cell_name, text=1)
        col_name.set_expand(True)
        col_name.set_sort_column_id(1)
        tree.append_column(col_name)

        cell_id = Gtk.CellRendererText()
        cell_id.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_id = Gtk.TreeViewColumn("ID", cell_id, text=2)
        col_id.set_min_width(150)
        tree.append_column(col_id)

        cell_price = Gtk.CellRendererText()
        col_price = Gtk.TreeViewColumn("Price", cell_price, text=3)
        col_price.set_min_width(130)
        tree.append_column(col_price)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(tree)
        box.pack_start(scroll, True, True, 0)

        dlg.show_all()

        if dlg.run() == Gtk.ResponseType.OK:
            added = 0
            it = pick_store.get_iter_first()
            while it:
                if pick_store[it][0]:
                    mid = pick_store[it][2]
                    name = pick_store[it][1]
                    if mid not in existing:
                        self.manager.add_model(mid, name, enabled=True, source="openrouter")
                        added += 1
                it = pick_store.iter_next(it)
            if added:
                self.refresh()

        dlg.destroy()


# ─── TaskListPanel ────────────────────────────────────────────────────────────


def _ensure_tasks_tables():
    """Create tasks tables in ctx database if they don't exist."""
    if not os.path.exists(CTX_DB):
        return
    db = sqlite3.connect(CTX_DB)
    db.executescript("""
        CREATE TABLE IF NOT EXISTS tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL,
            task_id TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(project, task_id)
        );
        CREATE TABLE IF NOT EXISTS task_config (
            project TEXT PRIMARY KEY,
            autorun INTEGER DEFAULT 0
        );
        CREATE TABLE IF NOT EXISTS task_claims (
            project TEXT NOT NULL,
            task_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            claimed_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (project, task_id)
        );
    """)
    db.close()


def _task_sort_key(task_id):
    """Natural sort key for hierarchical task IDs like 1, 1.a, 1.b, 2, 10."""
    parts = task_id.split(".")
    result = []
    for p in parts:
        try:
            result.append((0, int(p), ""))
        except ValueError:
            result.append((1, 0, p))
    return result


class TaskListPanel(Gtk.Box):
    """Panel for managing per-project task lists with auto-trigger controls."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app

        # ── Project selector ──
        proj_box = Gtk.Box(spacing=4)
        proj_box.set_border_width(6)
        proj_lbl = Gtk.Label(label="Project:")
        proj_lbl.set_xalign(0)
        proj_box.pack_start(proj_lbl, False, False, 0)

        self.project_combo = Gtk.ComboBoxText()
        self.project_combo.connect("changed", lambda _: self._on_project_changed())
        proj_box.pack_start(self.project_combo, True, True, 0)
        self.pack_start(proj_box, False, False, 0)

        # ── Task list (TreeView) ──
        # Columns: done(bool), task_id(str), description(str), status(str), is_separator(bool)
        self.store = Gtk.ListStore(bool, str, str, str, bool)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)
        self.tree.set_row_separator_func(self._row_separator_func)

        # Checkbox column
        toggle_renderer = Gtk.CellRendererToggle()
        toggle_renderer.connect("toggled", self._on_task_toggled)
        col_done = Gtk.TreeViewColumn("", toggle_renderer, active=0)
        col_done.set_min_width(30)
        col_done.set_max_width(30)
        self.tree.append_column(col_done)

        # Task ID column
        cell_id = Gtk.CellRendererText()
        col_id = Gtk.TreeViewColumn("ID", cell_id, text=1)
        col_id.set_min_width(50)
        col_id.set_max_width(60)
        col_id.set_cell_data_func(cell_id, self._style_cell)
        self.tree.append_column(col_id)

        # Description column
        cell_desc = Gtk.CellRendererText()
        cell_desc.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_desc = Gtk.TreeViewColumn("Task", cell_desc, text=2)
        col_desc.set_expand(True)
        col_desc.set_cell_data_func(cell_desc, self._style_cell)
        self.tree.append_column(col_desc)

        tree_scroll = Gtk.ScrolledWindow()
        tree_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tree_scroll.add(self.tree)
        self.pack_start(tree_scroll, True, True, 0)

        # ── Auto-trigger controls ──
        auto_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        auto_box.set_border_width(6)

        self.auto_status = Gtk.Label()
        self.auto_status.set_xalign(0)
        self._update_auto_label(False)
        auto_box.pack_start(self.auto_status, False, False, 0)

        auto_btn_box = Gtk.Box(spacing=4)

        self.btn_start = Gtk.Button(label="\u25b6 Start")
        self.btn_start.get_style_context().add_class("sidebar-btn")
        self.btn_start.connect("clicked", lambda _: self._on_autorun_toggle(True))
        auto_btn_box.pack_start(self.btn_start, True, True, 0)

        self.btn_stop = Gtk.Button(label="\u25a0 Stop")
        self.btn_stop.get_style_context().add_class("sidebar-btn")
        self.btn_stop.connect("clicked", lambda _: self._on_autorun_toggle(False))
        auto_btn_box.pack_start(self.btn_stop, True, True, 0)

        auto_box.pack_start(auto_btn_box, False, False, 0)
        self.pack_start(auto_box, False, False, 0)

        # ── Task action buttons ──
        btn_box = Gtk.Box(spacing=4)
        btn_box.set_border_width(6)

        btn_add = Gtk.Button(label="Add")
        btn_add.get_style_context().add_class("sidebar-btn")
        btn_add.connect("clicked", lambda _: self._on_add_task())
        btn_box.pack_start(btn_add, True, True, 0)

        btn_edit = Gtk.Button(label="Edit")
        btn_edit.get_style_context().add_class("sidebar-btn")
        btn_edit.connect("clicked", lambda _: self._on_edit_task())
        btn_box.pack_start(btn_edit, True, True, 0)

        btn_del = Gtk.Button(label="Delete")
        btn_del.get_style_context().add_class("sidebar-btn")
        btn_del.connect("clicked", lambda _: self._on_delete_task())
        btn_box.pack_start(btn_del, True, True, 0)

        btn_more = Gtk.MenuButton(label="\u22ee")
        btn_more.get_style_context().add_class("sidebar-btn")
        btn_more.set_tooltip_text("More actions")
        more_menu = Gtk.Menu()

        item_clear = Gtk.MenuItem(label="Clear done tasks")
        item_clear.connect("activate", lambda _: self._on_clear_done())
        more_menu.append(item_clear)

        item_reset = Gtk.MenuItem(label="Reset all to open")
        item_reset.connect("activate", lambda _: self._on_reset_all())
        more_menu.append(item_reset)

        more_menu.show_all()
        btn_more.set_popup(more_menu)
        btn_box.pack_start(btn_more, False, False, 0)

        btn_refresh = Gtk.Button(label="\u21bb")
        btn_refresh.get_style_context().add_class("sidebar-btn")
        btn_refresh.set_tooltip_text("Refresh")
        btn_refresh.connect("clicked", lambda _: self.refresh())
        btn_box.pack_start(btn_refresh, False, False, 0)

        self.pack_start(btn_box, False, False, 0)

        self._db_mtime = 0
        self._reset_all_autorun()
        self.refresh()
        GLib.timeout_add(2000, self._poll_db_changes)

    def _reset_all_autorun(self):
        """Reset all autorun flags on startup so auto-trigger is always OFF."""
        if not os.path.exists(CTX_DB):
            return
        _ensure_tasks_tables()
        db = sqlite3.connect(CTX_DB)
        db.execute("UPDATE task_config SET autorun = 0 WHERE autorun = 1")
        db.execute("DELETE FROM task_claims")
        db.commit()
        db.close()

    def _poll_db_changes(self):
        """Check if ctx database mtime changed and refresh if so."""
        try:
            mtime = os.path.getmtime(CTX_DB)
        except OSError:
            return True
        if mtime != self._db_mtime:
            self._db_mtime = mtime
            self._load_tasks()
            self._load_autorun_state()
        return True

    def _get_selected_project(self):
        return self.project_combo.get_active_text()

    def _on_project_changed(self):
        self._load_tasks()
        self._load_autorun_state()

    def _update_auto_label(self, active):
        if active:
            self.auto_status.set_markup(
                f'<b><span foreground="{CATPPUCCIN["green"]}">'
                f'\u25cf Auto-trigger: ON</span></b>'
            )
        else:
            self.auto_status.set_markup(
                f'<span foreground="{CATPPUCCIN["overlay1"]}">'
                f'\u25cb Auto-trigger: OFF</span>'
            )

    def refresh(self):
        """Reload projects and tasks from database."""
        _ensure_tasks_tables()
        self._load_projects()
        self._load_tasks()
        self._load_autorun_state()
        try:
            self._db_mtime = os.path.getmtime(CTX_DB)
        except OSError:
            pass

    def _load_projects(self):
        current = self._get_selected_project()
        self.project_combo.remove_all()
        if not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row
        projects = db.execute(
            "SELECT name FROM sessions ORDER BY name"
        ).fetchall()
        db.close()
        active_idx = 0
        for i, p in enumerate(projects):
            self.project_combo.append_text(p["name"])
            if p["name"] == current:
                active_idx = i
        if projects:
            self.project_combo.set_active(active_idx)

    def _load_tasks(self):
        # Preserve scroll position and selection
        tree_scroll = self.tree.get_parent()
        vadj = tree_scroll.get_vadjustment() if tree_scroll else None
        scroll_pos = vadj.get_value() if vadj else 0
        sel = self.tree.get_selection()
        _, selected_iter = sel.get_selected()
        selected_task_id = self.store[selected_iter][1] if selected_iter else None

        self.store.clear()
        project = self._get_selected_project()
        if not project or not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT task_id, description, status FROM tasks WHERE project = ?",
            (project,),
        ).fetchall()
        db.close()

        # Split into active (newest first) and done (at bottom)
        active = [r for r in rows if r["status"] != "done"]
        done = [r for r in rows if r["status"] == "done"]
        active.sort(key=lambda r: _task_sort_key(r["task_id"]))
        done.sort(key=lambda r: _task_sort_key(r["task_id"]))

        restore_path = None
        for t in active:
            indent = "  " if "." in t["task_id"] else ""
            it = self.store.append([False, t["task_id"], f"{indent}{t['description']}", t["status"], False])
            if t["task_id"] == selected_task_id:
                restore_path = self.store.get_path(it)

        # Separator row between active and done
        if active and done:
            self.store.append([False, "", "", "", True])

        for t in done:
            indent = "  " if "." in t["task_id"] else ""
            it = self.store.append([True, t["task_id"], f"{indent}{t['description']}", t["status"], False])
            if t["task_id"] == selected_task_id:
                restore_path = self.store.get_path(it)

        # Restore selection and scroll position
        if restore_path:
            sel.select_path(restore_path)
        if vadj:
            GLib.idle_add(vadj.set_value, scroll_pos)

    @staticmethod
    def _row_separator_func(model, iter_, data=None):
        """Return True for separator rows."""
        return model[iter_][4]

    @staticmethod
    def _style_cell(column, cell, model, iter_, data=None):
        """Gray out done tasks."""
        is_done = model[iter_][0]
        if is_done:
            cell.set_property("foreground", CATPPUCCIN["overlay0"])
        else:
            cell.set_property("foreground", CATPPUCCIN["text"])

    def _load_autorun_state(self):
        project = self._get_selected_project()
        if not project or not os.path.exists(CTX_DB):
            self._update_auto_label(False)
            return
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT autorun FROM task_config WHERE project = ?", (project,)
        ).fetchone()
        db.close()
        active = bool(row and row["autorun"])
        self._update_auto_label(active)

    def _on_task_toggled(self, renderer, path):
        """Toggle task done/undone via checkbox and re-sort."""
        project = self._get_selected_project()
        if not project:
            return
        it = self.store.get_iter(path)
        if self.store[it][4]:  # separator row
            return
        task_id = self.store[it][1]
        current_done = self.store[it][0]
        new_status = "open" if current_done else "done"

        db = sqlite3.connect(CTX_DB)
        db.execute(
            """UPDATE tasks SET status = ?, updated_at = datetime('now')
               WHERE project = ? AND task_id = ?""",
            (new_status, project, task_id),
        )
        db.commit()
        db.close()
        # Reload to re-sort (active on top, done on bottom)
        self._load_tasks()

    def _on_autorun_toggle(self, enable):
        project = self._get_selected_project()
        if not project:
            return
        db = sqlite3.connect(CTX_DB)
        db.execute(
            """INSERT INTO task_config (project, autorun)
               VALUES (?, ?)
               ON CONFLICT(project) DO UPDATE SET autorun = excluded.autorun""",
            (project, 1 if enable else 0),
        )
        db.commit()
        db.close()
        self._update_auto_label(enable)

        # Immediately trigger first task when Start is clicked
        if enable:
            self._trigger_first_task(project)

    def _trigger_first_task(self, project):
        """Find Claude Code tabs matching this project and send claim-based triggers."""
        if not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row

        # Send trigger to each matching tab — each claims its own task
        for i in range(self.app.notebook.get_n_pages()):
            tab = self.app.notebook.get_nth_page(i)
            if isinstance(tab, TerminalTab) and tab._task_project == project:
                task = TerminalTab._claim_next_task(db, project, tab._task_session_id)
                if not task:
                    break
                message = (
                    f"[AUTO-TRIGGER] Twoje przypisane zadanie: {task['task_id']} — {task['description']}\n"
                    f"Sprawdź pełną listę: tasks context {project} --session {tab._task_session_id}\n"
                    f"MUSISZ oznaczyć po wykonaniu: tasks done {project} {task['task_id']} (w Bash). "
                    f"Pętla auto-trigger kończy się DOPIERO gdy WSZYSTKIE zadania są zamknięte (done). "
                    f"Jeśli nie oznaczysz — ta wiadomość będzie się powtarzać."
                )
                _t = tab.terminal
                _t.feed_child(message.encode())
                GLib.timeout_add(100, lambda t=_t: t.feed_child(b"\r") or False)
        db.close()

    def _on_add_task(self):
        project = self._get_selected_project()
        if not project:
            return
        dlg = Gtk.Dialog(
            title="Add Task", transient_for=self.app, modal=True,
            destroy_with_parent=True,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)

        # Task ID (optional)
        id_box = Gtk.Box(spacing=4)
        id_lbl = Gtk.Label(label="Task ID (optional):")
        id_lbl.set_xalign(0)
        id_box.pack_start(id_lbl, False, False, 0)
        id_entry = Gtk.Entry()
        id_entry.set_placeholder_text("auto")
        id_entry.set_width_chars(8)
        id_box.pack_start(id_entry, False, False, 0)
        box.pack_start(id_box, False, False, 0)

        # Description
        desc_lbl = Gtk.Label(label="Description:")
        desc_lbl.set_xalign(0)
        box.pack_start(desc_lbl, False, False, 0)
        desc_entry = Gtk.Entry()
        desc_entry.set_activates_default(True)
        box.pack_start(desc_entry, False, False, 0)

        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.show_all()

        if dlg.run() == Gtk.ResponseType.OK:
            description = desc_entry.get_text().strip()
            task_id = id_entry.get_text().strip()
            if description:
                db = sqlite3.connect(CTX_DB)
                db.row_factory = sqlite3.Row
                if not task_id:
                    # Auto-assign next number
                    rows = db.execute(
                        "SELECT task_id FROM tasks WHERE project = ?", (project,)
                    ).fetchall()
                    max_num = 0
                    for row in rows:
                        parts = row["task_id"].split(".")
                        try:
                            num = int(parts[0])
                            if num > max_num:
                                max_num = num
                        except ValueError:
                            pass
                    task_id = str(max_num + 1)
                try:
                    db.execute(
                        """INSERT INTO tasks (project, task_id, description, status)
                           VALUES (?, ?, ?, 'open')""",
                        (project, task_id, description),
                    )
                    db.commit()
                except sqlite3.IntegrityError:
                    pass
                db.close()
                self._load_tasks()
        dlg.destroy()

    def _on_edit_task(self):
        project = self._get_selected_project()
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        if not it or not project:
            return
        task_id = model[it][1]
        old_desc = model[it][2].strip()

        dlg = Gtk.Dialog(
            title="Edit Task", transient_for=self.app, modal=True,
            destroy_with_parent=True,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_border_width(12)

        lbl = Gtk.Label(label=f"Edit task {task_id}:")
        lbl.set_xalign(0)
        box.pack_start(lbl, False, False, 0)

        entry = Gtk.Entry()
        entry.set_text(old_desc)
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)

        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.show_all()

        if dlg.run() == Gtk.ResponseType.OK:
            new_desc = entry.get_text().strip()
            if new_desc:
                db = sqlite3.connect(CTX_DB)
                db.execute(
                    """UPDATE tasks SET description = ?, updated_at = datetime('now')
                       WHERE project = ? AND task_id = ?""",
                    (new_desc, project, task_id),
                )
                db.commit()
                db.close()
                self._load_tasks()
        dlg.destroy()

    def _on_delete_task(self):
        project = self._get_selected_project()
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        if not it or not project:
            return
        task_id = model[it][1]
        db = sqlite3.connect(CTX_DB)
        db.execute(
            "DELETE FROM tasks WHERE project = ? AND task_id = ?",
            (project, task_id),
        )
        db.commit()
        db.close()
        self._load_tasks()

    def _on_clear_done(self):
        project = self._get_selected_project()
        if not project:
            return
        db = sqlite3.connect(CTX_DB)
        db.execute(
            "DELETE FROM tasks WHERE project = ? AND status = 'done'",
            (project,),
        )
        db.commit()
        db.close()
        self._load_tasks()

    def _on_reset_all(self):
        project = self._get_selected_project()
        if not project:
            return
        db = sqlite3.connect(CTX_DB)
        db.execute(
            """UPDATE tasks SET status = 'open', updated_at = datetime('now')
               WHERE project = ?""",
            (project,),
        )
        db.commit()
        db.close()
        self._load_tasks()


# ─── Git Panel ────────────────────────────────────────────────────────────────


class GitPanel(Gtk.Box):
    """Right-side panel with accordion Git sections and file monitoring."""

    _REFRESH_INTERVAL_MS = 3000  # auto-refresh every 3s when visible

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self._git_dir = None
        self._monitors = []
        self._timer_id = None
        self.get_style_context().add_class("sidebar")
        self.get_style_context().add_class("git-panel")
        self.set_size_request(0, -1)

        # ── Header bar ──
        header = Gtk.Box(spacing=6)
        header.get_style_context().add_class("git-header")

        self._btn_toggle = Gtk.Button(label="▶")
        self._btn_toggle.get_style_context().add_class("sidebar-btn")
        self._btn_toggle.set_tooltip_text("Hide Git panel (Ctrl+G)")
        self._btn_toggle.connect("clicked", lambda _: self.app.toggle_git_panel())
        header.pack_start(self._btn_toggle, False, False, 0)

        lbl = Gtk.Label(label="Git")
        lbl.set_xalign(0)
        header.pack_start(lbl, True, True, 0)

        self._btn_refresh = Gtk.Button(label="⟳")
        self._btn_refresh.get_style_context().add_class("sidebar-btn")
        self._btn_refresh.set_tooltip_text("Refresh (F5)")
        self._btn_refresh.connect("clicked", lambda _: self.refresh())
        header.pack_end(self._btn_refresh, False, False, 0)

        self.pack_start(header, False, False, 0)

        # ── Scrollable content ──
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self._content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        scroll.add(self._content)
        self.pack_start(scroll, True, True, 0)

        # "No git" placeholder
        self._no_git_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
        self._no_git_box.set_margin_top(40)
        self._no_git_box.set_margin_start(16)
        self._no_git_box.set_margin_end(16)
        self._no_git_lbl = Gtk.Label()
        self._no_git_lbl.set_line_wrap(True)
        self._no_git_lbl.set_justify(Gtk.Justification.CENTER)
        self._no_git_box.pack_start(self._no_git_lbl, False, False, 0)
        self._btn_init = Gtk.Button(label="  git init  ")
        self._btn_init.get_style_context().add_class("sidebar-btn")
        self._btn_init.set_halign(Gtk.Align.CENTER)
        self._btn_init.connect("clicked", self._on_git_init)
        self._no_git_box.pack_start(self._btn_init, False, False, 0)
        self._content.pack_start(self._no_git_box, False, False, 0)

        # ── Sections ──
        self._sections_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self._content.pack_start(self._sections_box, True, True, 0)

        # Branch
        self._branch_label = Gtk.Label()
        self._branch_label.set_xalign(0)
        self._branch_label.set_line_wrap(True)
        self._branch_label.set_selectable(True)
        self._add_section("Branch", self._branch_label)

        # Changes
        changes_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        self._changes_summary = Gtk.Label()
        self._changes_summary.set_xalign(0)
        changes_box.pack_start(self._changes_summary, False, False, 0)
        # File list
        self._changes_store = Gtk.ListStore(str, str, str)
        self._changes_tree = Gtk.TreeView(model=self._changes_store)
        self._changes_tree.set_headers_visible(False)
        self._changes_tree.set_enable_search(False)
        r_status = Gtk.CellRendererText()
        c_status = Gtk.TreeViewColumn("", r_status, markup=0)
        c_status.set_min_width(24)
        c_status.set_max_width(28)
        self._changes_tree.append_column(c_status)
        r_file = Gtk.CellRendererText()
        r_file.set_property("ellipsize", Pango.EllipsizeMode.START)
        c_file = Gtk.TreeViewColumn("", r_file, text=1)
        c_file.set_expand(True)
        c_file.set_min_width(0)
        self._changes_tree.append_column(c_file)
        r_diff = Gtk.CellRendererText()
        c_diff = Gtk.TreeViewColumn("", r_diff, markup=2)
        c_diff.set_min_width(0)
        self._changes_tree.append_column(c_diff)
        ch_scroll = Gtk.ScrolledWindow()
        ch_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        ch_scroll.set_min_content_height(60)
        ch_scroll.set_max_content_height(180)
        ch_scroll.set_propagate_natural_height(True)
        ch_scroll.add(self._changes_tree)
        changes_box.pack_start(ch_scroll, True, True, 0)
        self._sec_changes = self._add_section("Changes", changes_box)

        # Stash
        self._stash_label = Gtk.Label()
        self._stash_label.set_xalign(0)
        self._stash_label.set_line_wrap(True)
        self._stash_label.set_selectable(True)
        self._sec_stash = self._add_section("Stash", self._stash_label)

        # LFS / Binary
        lfs_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self._lfs_label = Gtk.Label()
        self._lfs_label.set_xalign(0)
        self._lfs_label.set_line_wrap(True)
        self._lfs_label.set_selectable(True)
        lfs_box.pack_start(self._lfs_label, False, False, 0)
        self._btn_setup_lfs = Gtk.Button(label="  Setup Git LFS  ")
        self._btn_setup_lfs.get_style_context().add_class("sidebar-btn")
        self._btn_setup_lfs.set_halign(Gtk.Align.START)
        self._btn_setup_lfs.connect("clicked", self._on_setup_lfs)
        self._btn_setup_lfs.set_no_show_all(True)
        lfs_box.pack_start(self._btn_setup_lfs, False, False, 0)
        self._sec_lfs = self._add_section("LFS / Binary", lfs_box)

        # Activity
        self._activity_label = Gtk.Label()
        self._activity_label.set_xalign(0)
        self._activity_label.set_line_wrap(True)
        self._activity_label.set_selectable(True)
        self._add_section("Activity", self._activity_label)

        # Log (last — fills remaining space)
        self._log_view = Gtk.TextView()
        self._log_view.set_editable(False)
        self._log_view.set_cursor_visible(False)
        self._log_view.set_wrap_mode(Gtk.WrapMode.NONE)
        self._log_view.set_monospace(True)
        self._log_view.set_left_margin(4)
        self._log_view.set_right_margin(4)
        self._log_view.set_top_margin(2)
        log_scroll = Gtk.ScrolledWindow()
        log_scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        log_scroll.set_min_content_height(100)
        log_scroll.add(self._log_view)
        self._add_section("Log", log_scroll, expand=True)

    def _add_section(self, title, content_widget, expand=False):
        """Add a collapsible section with styled title bar. Returns the outer box."""
        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)

        # Title bar (clickable to toggle)
        title_btn = Gtk.Button()
        title_btn.set_relief(Gtk.ReliefStyle.NONE)
        title_lbl = Gtk.Label()
        title_lbl.set_markup(f"<small><b>▾ {title}</b></small>")
        title_lbl.set_xalign(0)
        title_btn.add(title_lbl)
        title_btn.get_style_context().add_class("git-section-title")
        outer.pack_start(title_btn, False, False, 0)

        # Body
        body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        body.get_style_context().add_class("git-section-body")
        body.pack_start(content_widget, True, True, 0)
        outer.pack_start(body, expand, expand, 0)

        # Toggle collapse
        def _toggle(_btn):
            if body.get_visible():
                body.hide()
                title_lbl.set_markup(f"<small><b>▸ {title}</b></small>")
            else:
                body.show_all()
                title_lbl.set_markup(f"<small><b>▾ {title}</b></small>")
        title_btn.connect("clicked", _toggle)

        self._sections_box.pack_start(outer, expand, expand, 0)
        return outer

    # ── Git commands ──

    def _git(self, *args, timeout=5):
        """Run a git command in the current git dir. Returns stdout or None."""
        if not self._git_dir:
            return None
        try:
            r = subprocess.run(
                ["git"] + list(args),
                cwd=self._git_dir, capture_output=True, text=True, timeout=timeout,
            )
            return r.stdout if r.returncode == 0 else None
        except (FileNotFoundError, subprocess.TimeoutExpired):
            return None

    def _is_git_repo(self):
        return self._git("rev-parse", "--is-inside-work-tree") is not None

    # ── Public API ──

    def set_project_dir(self, path):
        """Set (or clear) the git directory and refresh."""
        new_dir = path if path and os.path.isdir(path) else None
        if new_dir == self._git_dir:
            return
        self._git_dir = new_dir
        self._setup_monitors()
        self.refresh()

    def refresh(self):
        """Reload all git data."""
        is_repo = self._is_git_repo() if self._git_dir else False

        if not is_repo:
            self._sections_box.hide()
            self._no_git_box.show_all()
            if self._git_dir:
                self._no_git_lbl.set_text(f"Not a git repo:\n{self._git_dir}")
            else:
                self._no_git_lbl.set_text(
                    "No project selected.\n"
                    "Open a Claude Code session\nwith project dir.")
            return

        self._no_git_box.hide()
        self._sections_box.show_all()
        # Re-hide LFS button (has no_show_all)
        if not self._btn_setup_lfs.get_visible():
            self._btn_setup_lfs.hide()

        self._refresh_branch()
        self._refresh_changes()
        self._refresh_log()
        self._refresh_stash()
        self._refresh_lfs()
        self._refresh_activity()

    def _refresh_branch(self):
        # Current branch
        branch = (self._git("branch", "--show-current") or "").strip()
        if not branch:
            branch = (self._git("rev-parse", "--short", "HEAD") or "detached").strip()

        c_grn, c_red, c_dim = CATPPUCCIN["green"], CATPPUCCIN["red"], CATPPUCCIN["overlay0"]
        lines = [f"<span size='large' foreground='{c_grn}'><b>{branch}</b></span>"]

        # Upstream tracking
        upstream = self._git("rev-parse", "--abbrev-ref", "@{upstream}")
        if upstream:
            upstream = upstream.strip()
            ahead = (self._git("rev-list", "--count", f"{upstream}..HEAD") or "0").strip()
            behind = (self._git("rev-list", "--count", f"HEAD..{upstream}") or "0").strip()
            parts = []
            if ahead != "0":
                parts.append(f"<span foreground='{c_grn}'>↑{ahead}</span>")
            if behind != "0":
                parts.append(f"<span foreground='{c_red}'>↓{behind}</span>")
            if parts:
                lines.append(f"{' '.join(parts)}  vs  {upstream}")
            else:
                lines.append(f"✓ up to date with {upstream}")

        # Remotes
        remotes = (self._git("remote", "-v") or "").strip()
        if remotes:
            seen = set()
            for line in remotes.splitlines():
                name = line.split()[0]
                if name not in seen:
                    seen.add(name)
                    url = line.split()[1] if len(line.split()) > 1 else ""
                    lines.append(f"<small><span foreground='{c_dim}'>{name}  {url}</span></small>")

        self._branch_label.set_markup("\n".join(lines))

    def _refresh_changes(self):
        self._changes_store.clear()
        status = self._git("status", "--porcelain=v1") or ""
        files = status.rstrip().splitlines() if status.rstrip() else []
        total_add, total_del = 0, 0
        c_grn = CATPPUCCIN["green"]
        c_red = CATPPUCCIN["red"]
        c_yel = CATPPUCCIN["yellow"]

        numstat = {}
        for diff_args in [("diff", "--numstat", "HEAD")]:
            raw = self._git(*diff_args) or ""
            for line in raw.strip().splitlines():
                parts = line.split("\t", 2)
                if len(parts) == 3:
                    a = parts[0] if parts[0] != "-" else "bin"
                    d = parts[1] if parts[1] != "-" else "bin"
                    fname = parts[2]
                    if fname in numstat and numstat[fname] != ("bin", "bin"):
                        pa, pd = numstat[fname]
                        a = str(int(pa) + int(a)) if pa.isdigit() and a.isdigit() else a
                        d = str(int(pd) + int(d)) if pd.isdigit() and d.isdigit() else d
                    numstat[fname] = (a, d)

        for f in files:
            if len(f) < 4:
                continue
            st = f[:2]
            fname = f[3:]
            adds, dels = numstat.get(fname, ("", ""))
            if adds.isdigit():
                total_add += int(adds)
            if dels.isdigit():
                total_del += int(dels)
            stat_str = ""
            if adds or dels:
                parts = []
                if adds and adds != "0":
                    parts.append(f"<span foreground='{c_grn}'>+{adds}</span>")
                if dels and dels != "0":
                    parts.append(f"<span foreground='{c_red}'>-{dels}</span>")
                if adds == "bin":
                    parts = [f"<span foreground='{c_yel}'>bin</span>"]
                stat_str = " ".join(parts)
            s = st.strip()
            status_colors = {
                "M": c_yel, "A": c_grn, "D": c_red,
                "R": CATPPUCCIN["blue"], "C": CATPPUCCIN["blue"],
                "U": c_red, "??": CATPPUCCIN["overlay0"],
            }
            color = status_colors.get(s, CATPPUCCIN["text"])
            st_markup = f"<span foreground='{color}'><b>{s}</b></span>"
            self._changes_store.append([st_markup, fname, stat_str])

        n = len(files)
        if n:
            self._changes_summary.set_markup(
                f"{n} file{'s' if n != 1 else ''}  "
                f"<span foreground='{c_grn}'><b>+{total_add}</b></span>  "
                f"<span foreground='{c_red}'><b>-{total_del}</b></span>"
            )
        else:
            self._changes_summary.set_markup(
                f"<span foreground='{c_grn}'>✓ Working tree clean</span>")

    # ANSI color code → Catppuccin palette mapping for git log
    @staticmethod
    def _ansi_colors():
        return {
            "31": CATPPUCCIN["red"], "32": CATPPUCCIN["green"],
            "33": CATPPUCCIN["yellow"], "34": CATPPUCCIN["blue"],
            "35": CATPPUCCIN["pink"], "36": CATPPUCCIN["teal"],
            "1;31": CATPPUCCIN["red"], "1;32": CATPPUCCIN["green"],
            "1;33": CATPPUCCIN["yellow"], "1;34": CATPPUCCIN["blue"],
            "1;35": CATPPUCCIN["pink"], "1;36": CATPPUCCIN["teal"],
            "1": CATPPUCCIN["text"],
        }

    def _refresh_log(self):
        log = self._git(
            "log", "--oneline", "--decorate", "--graph", "--color=always", "-40",
            timeout=10,
        ) or "(no commits)"
        # Fresh buffer to get clean tags for current theme
        buf = Gtk.TextBuffer()
        ansi = self._ansi_colors()
        tags = {}
        end_iter = buf.get_end_iter
        for line in log.rstrip().splitlines():
            parts = re.split(r'\x1b\[([0-9;]*)m', line)
            current_tag = None
            for i, part in enumerate(parts):
                if i % 2 == 1:
                    if part == "0" or part == "":
                        current_tag = None
                    elif part in ansi:
                        if part not in tags:
                            tag = buf.create_tag(None, foreground=ansi[part])
                            if part.startswith("1"):
                                tag.set_property("weight", Pango.Weight.BOLD)
                            tags[part] = tag
                        current_tag = tags[part]
                else:
                    if part:
                        if current_tag:
                            buf.insert_with_tags(end_iter(), part, current_tag)
                        else:
                            buf.insert(end_iter(), part)
            buf.insert(end_iter(), "\n")
        self._log_view.set_buffer(buf)

    def _refresh_stash(self):
        stash = (self._git("stash", "list") or "").strip()
        if stash:
            self._stash_label.set_text(stash)
            self._sec_stash.show_all()
        else:
            self._sec_stash.hide()

    def _refresh_lfs(self):
        lines = []
        # Check if LFS is installed
        lfs_installed = shutil.which("git-lfs") is not None
        lfs_active = False
        gitattr_path = os.path.join(self._git_dir, ".gitattributes") if self._git_dir else None

        if gitattr_path and os.path.isfile(gitattr_path):
            try:
                with open(gitattr_path) as f:
                    content = f.read()
                if "filter=lfs" in content:
                    lfs_active = True
                    tracked = [
                        l.split()[0] for l in content.splitlines()
                        if "filter=lfs" in l
                    ]
                    lines.append(f"LFS tracking: {', '.join(tracked)}")
            except OSError:
                pass

        # Find large/binary tracked files
        big_files = []
        ls_out = self._git("ls-files", "-z") or ""
        if ls_out:
            for fname in ls_out.split("\0"):
                if not fname:
                    continue
                ext = os.path.splitext(fname)[1].lower()
                if ext in (".png", ".jpg", ".jpeg", ".gif", ".bmp", ".webp",
                           ".mp4", ".mp3", ".zip", ".tar", ".gz", ".bin",
                           ".pdf", ".psd", ".ai", ".sketch", ".fig",
                           ".woff", ".woff2", ".ttf", ".otf", ".ico",
                           ".so", ".dll", ".dylib", ".exe"):
                    fpath = os.path.join(self._git_dir, fname)
                    try:
                        sz = os.path.getsize(fpath)
                        if sz > 50_000:  # >50KB
                            big_files.append((fname, sz))
                    except OSError:
                        pass

        if big_files:
            lines.append("")
            lines.append(f"Binary files tracked ({len(big_files)}):")
            for fname, sz in big_files[:10]:
                h = f"{sz / 1024:.0f}KB" if sz < 1_000_000 else f"{sz / 1_000_000:.1f}MB"
                lines.append(f"  {fname} ({h})")
            if len(big_files) > 10:
                lines.append(f"  ... +{len(big_files) - 10} more")

        if not lfs_installed:
            lines.insert(0, "Git LFS: not installed")
            self._btn_setup_lfs.hide()
        elif not lfs_active and big_files:
            lines.insert(0, "Git LFS: not configured (recommended)")
            self._btn_setup_lfs.show()
        elif lfs_active:
            lines.insert(0, "Git LFS: active")
            self._btn_setup_lfs.hide()
        else:
            lines.insert(0, "Git LFS: not needed")
            self._btn_setup_lfs.hide()

        self._lfs_label.set_text("\n".join(lines) if lines else "No binary issues")

    def _refresh_activity(self):
        lines = []
        # Commits last 7 days
        week = self._git("rev-list", "--count", "--since=7 days ago", "HEAD")
        month = self._git("rev-list", "--count", "--since=30 days ago", "HEAD")
        total = self._git("rev-list", "--count", "HEAD")

        if week is not None:
            lines.append(f"Last 7 days: {week.strip()} commits")
        if month is not None:
            lines.append(f"Last 30 days: {month.strip()} commits")
        if total is not None:
            lines.append(f"Total: {total.strip()} commits")

        # Top authors last 30 days
        shortlog = self._git("shortlog", "-sn", "--since=30 days ago", "HEAD")
        if shortlog and shortlog.strip():
            lines.append("")
            lines.append("Authors (30d):")
            for line in shortlog.strip().splitlines()[:5]:
                lines.append(f"  {line.strip()}")

        # Tags
        tags = self._git("tag", "--sort=-creatordate", "-l")
        if tags and tags.strip():
            tag_list = tags.strip().splitlines()[:5]
            lines.append("")
            lines.append(f"Tags ({len(tags.strip().splitlines())} total):")
            for t in tag_list:
                lines.append(f"  {t.strip()}")

        self._activity_label.set_text("\n".join(lines) if lines else "No activity data")

    # ── File monitoring ──

    def _setup_monitors(self):
        """Watch .git dir and working tree for changes (Gio.FileMonitor)."""
        self._stop_monitors()
        if not self._git_dir:
            return
        git_internal = os.path.join(self._git_dir, ".git")
        paths_to_watch = []
        if os.path.isdir(git_internal):
            # Watch .git/HEAD, .git/index for branch/stage changes
            for name in ("HEAD", "index", "refs"):
                p = os.path.join(git_internal, name)
                if os.path.exists(p):
                    paths_to_watch.append(p)
        for p in paths_to_watch:
            try:
                gfile = Gio.File.new_for_path(p)
                if os.path.isdir(p):
                    mon = gfile.monitor_directory(Gio.FileMonitorFlags.NONE, None)
                else:
                    mon = gfile.monitor_file(Gio.FileMonitorFlags.NONE, None)
                mon.connect("changed", self._on_fs_changed)
                self._monitors.append(mon)
            except GLib.Error:
                pass
        # Periodic fallback (catches working tree changes)
        self._timer_id = GLib.timeout_add(self._REFRESH_INTERVAL_MS, self._on_timer)

    def _stop_monitors(self):
        for m in self._monitors:
            m.cancel()
        self._monitors.clear()
        if self._timer_id:
            GLib.source_remove(self._timer_id)
            self._timer_id = None

    def _on_fs_changed(self, monitor, file, other_file, event_type):
        """Debounced refresh on filesystem change."""
        if not hasattr(self, "_fs_pending"):
            self._fs_pending = False
        if not self._fs_pending:
            self._fs_pending = True
            GLib.timeout_add(500, self._on_fs_debounce)

    def _on_fs_debounce(self):
        self._fs_pending = False
        if self.get_visible():
            self.refresh()
        return False

    def _on_timer(self):
        if self.get_visible() and self._git_dir:
            self.refresh()
        return True  # keep running

    # ── Actions ──

    def _on_git_init(self, _btn):
        if not self._git_dir:
            return
        dlg = Gtk.MessageDialog(
            transient_for=self.app, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text="Initialize git repository?",
        )
        dlg.format_secondary_text(f"This will run 'git init' in:\n{self._git_dir}")
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.YES:
            return
        try:
            subprocess.run(
                ["git", "init"], cwd=self._git_dir,
                capture_output=True, timeout=10,
            )
            # Auto-create .gitignore with sensible defaults
            gi_path = os.path.join(self._git_dir, ".gitignore")
            if not os.path.exists(gi_path):
                with open(gi_path, "w") as f:
                    f.write(
                        "# Binary & generated\n"
                        "*.pyc\n__pycache__/\n*.o\n*.so\n"
                        "node_modules/\ndist/\nbuild/\n"
                        ".env\n.env.*\n"
                        "# Images (use Git LFS for large assets)\n"
                        "# *.png\n# *.jpg\n"
                        "copied_images/\n"
                    )
            self._setup_monitors()
            self.refresh()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    def _on_setup_lfs(self, _btn):
        if not self._git_dir:
            return
        try:
            subprocess.run(
                ["git", "lfs", "install"], cwd=self._git_dir,
                capture_output=True, timeout=10,
            )
            # Track common binary patterns
            for pattern in ["*.png", "*.jpg", "*.jpeg", "*.gif", "*.webp",
                            "*.pdf", "*.zip", "*.tar.gz", "*.mp4", "*.mp3",
                            "*.woff", "*.woff2", "*.ttf"]:
                subprocess.run(
                    ["git", "lfs", "track", pattern], cwd=self._git_dir,
                    capture_output=True, timeout=5,
                )
            self.refresh()
        except (FileNotFoundError, subprocess.TimeoutExpired):
            pass

    def destroy(self):
        self._stop_monitors()
        super().destroy()


# ─── BTerminalApp ─────────────────────────────────────────────────────────────


class ShrinkableBin(Gtk.Bin):
    """Container that reports minimum width as 0, allowing HPaned to shrink it
    without triggering GTK's right-alignment clipping behavior."""

    def do_get_preferred_width(self):
        return (0, 0)

    def do_get_preferred_width_for_height(self, height):
        return (0, 0)

    def do_size_allocate(self, allocation):
        self.set_allocation(allocation)
        child = self.get_child()
        if child and child.get_visible():
            child.size_allocate(allocation)


class BTerminalPlugin:
    """Base class for BTerminal plugins."""
    name = ""
    title = ""
    version = ""
    description = ""
    author = ""

    def activate(self, app):
        return None

    def deactivate(self):
        pass

    def get_keyboard_shortcuts(self):
        return []

    def on_sidebar_shown(self):
        pass

    def get_session_context(self):
        """Return extra context string to inject into Claude Code intro prompt, or None."""
        return None


class MemoryPanel(Gtk.Box):
    """Panel for managing Claude Code memory: rules, injection config, and session logs."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.app = app
        self._current_project = None
        self._rules_store = None

        header = Gtk.Label(label="Memory")
        header.get_style_context().add_class("sidebar-header")
        header.set_halign(Gtk.Align.START)
        header.set_hexpand(True)
        self.pack_start(header, False, False, 0)

        # Project selector
        proj_box = Gtk.Box(spacing=4)
        proj_box.set_margin_start(6)
        proj_box.set_margin_end(6)
        proj_box.set_margin_top(6)
        proj_lbl = Gtk.Label(label="Project:")
        proj_lbl.set_halign(Gtk.Align.START)
        self._proj_combo = Gtk.ComboBoxText()
        self._proj_combo.set_hexpand(True)
        self._proj_combo.connect("changed", self._on_project_changed)
        proj_box.pack_start(proj_lbl, False, False, 0)
        proj_box.pack_start(self._proj_combo, True, True, 0)
        btn_refresh_proj = Gtk.Button(label="⟳")
        btn_refresh_proj.set_tooltip_text("Refresh project list")
        btn_refresh_proj.get_style_context().add_class("sidebar-btn")
        btn_refresh_proj.connect("clicked", lambda _: self._load_projects())
        proj_box.pack_start(btn_refresh_proj, False, False, 0)
        self.pack_start(proj_box, False, False, 0)

        # Accordion sections in a scrolled window
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll.add(inner)
        self.pack_start(scroll, True, True, 0)

        # ── Injection config ─────────────────────────────────────────────
        cfg_frame = self._make_section("⚙ Injection Config")
        cfg_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        cfg_body.set_margin_start(8)
        cfg_body.set_margin_end(8)
        cfg_body.set_margin_bottom(6)

        inj_row = Gtk.Box(spacing=6)
        Gtk.Label(label="Inject rules every N prompts:").set_halign(Gtk.Align.START)
        inj_lbl = Gtk.Label(label="Inject rules every:")
        inj_lbl.set_halign(Gtk.Align.START)
        self._spin_inject = Gtk.SpinButton.new_with_range(1, 500, 1)
        self._spin_inject.set_value(100)
        self._spin_inject.set_width_chars(4)
        inj_row.pack_start(inj_lbl, True, True, 0)
        inj_row.pack_start(self._spin_inject, False, False, 0)
        inj_row.pack_start(Gtk.Label(label="prompts"), False, False, 0)
        cfg_body.pack_start(inj_row, False, False, 0)

        ref_row = Gtk.Box(spacing=6)
        ref_lbl = Gtk.Label(label="Refresh CTX every:")
        ref_lbl.set_halign(Gtk.Align.START)
        self._spin_refresh = Gtk.SpinButton.new_with_range(1, 1000, 1)
        self._spin_refresh.set_value(200)
        self._spin_refresh.set_width_chars(4)
        ref_row.pack_start(ref_lbl, True, True, 0)
        ref_row.pack_start(self._spin_refresh, False, False, 0)
        ref_row.pack_start(Gtk.Label(label="prompts"), False, False, 0)
        cfg_body.pack_start(ref_row, False, False, 0)

        apply_row = Gtk.Box(spacing=8)
        btn_save_cfg = Gtk.Button(label="Apply")
        btn_save_cfg.get_style_context().add_class("sidebar-btn")
        btn_save_cfg.connect("clicked", self._on_save_config)
        apply_row.pack_start(btn_save_cfg, False, False, 0)
        self._cfg_status_lbl = Gtk.Label(label="")
        self._cfg_status_lbl.get_style_context().add_class("dim-label")
        apply_row.pack_start(self._cfg_status_lbl, False, False, 0)
        cfg_body.pack_start(apply_row, False, False, 0)

        cfg_frame.add(cfg_body)
        inner.pack_start(cfg_frame, False, False, 0)

        # ── Rules list ───────────────────────────────────────────────────
        rules_frame = self._make_section("📋 Rules")
        rules_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        rules_body.set_margin_start(8)
        rules_body.set_margin_end(8)
        rules_body.set_margin_bottom(6)

        # TreeView: [✓/✗] [#id] [rule text]
        self._rules_store = Gtk.ListStore(bool, int, str)
        tv = Gtk.TreeView(model=self._rules_store)
        tv.set_headers_visible(False)
        tv.set_size_request(-1, 140)

        ren_toggle = Gtk.CellRendererToggle()
        ren_toggle.connect("toggled", self._on_rule_toggled)
        col_toggle = Gtk.TreeViewColumn("", ren_toggle, active=0)
        col_toggle.set_fixed_width(28)
        tv.append_column(col_toggle)

        ren_id = Gtk.CellRendererText()
        col_id = Gtk.TreeViewColumn("#", ren_id, text=1)
        col_id.set_fixed_width(32)
        tv.append_column(col_id)

        ren_text = Gtk.CellRendererText()
        ren_text.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_text = Gtk.TreeViewColumn("Rule", ren_text, text=2)
        col_text.set_expand(True)
        tv.append_column(col_text)

        self._rules_tv = tv
        tv_scroll = Gtk.ScrolledWindow()
        tv_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tv_scroll.set_min_content_height(100)
        tv_scroll.add(tv)
        rules_body.pack_start(tv_scroll, True, True, 0)

        # Add rule entry
        add_box = Gtk.Box(spacing=4)
        self._rule_entry = Gtk.Entry()
        self._rule_entry.set_placeholder_text("New rule…")
        self._rule_entry.set_hexpand(True)
        self._rule_entry.connect("activate", self._on_add_rule)
        btn_add = Gtk.Button(label="+")
        btn_add.get_style_context().add_class("sidebar-btn")
        btn_add.connect("clicked", self._on_add_rule)
        btn_del = Gtk.Button(label="✕")
        btn_del.get_style_context().add_class("sidebar-btn")
        btn_del.connect("clicked", self._on_remove_rule)
        add_box.pack_start(self._rule_entry, True, True, 0)
        add_box.pack_start(btn_add, False, False, 0)
        add_box.pack_start(btn_del, False, False, 0)
        rules_body.pack_start(add_box, False, False, 0)

        rules_frame.add(rules_body)
        inner.pack_start(rules_frame, False, False, 0)

        # ── Session logs ─────────────────────────────────────────────────
        logs_frame = self._make_section("📜 Session Logs")
        logs_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        logs_body.set_margin_start(8)
        logs_body.set_margin_end(8)
        logs_body.set_margin_bottom(6)

        self._logs_store = Gtk.ListStore(str, str)  # (display, filename)
        logs_tv = Gtk.TreeView(model=self._logs_store)
        logs_tv.set_headers_visible(False)
        logs_tv.set_size_request(-1, 100)
        ren_log = Gtk.CellRendererText()
        ren_log.set_property("ellipsize", Pango.EllipsizeMode.START)
        logs_tv.append_column(Gtk.TreeViewColumn("", ren_log, text=0))
        self._logs_tv = logs_tv
        logs_tv.connect("row-activated", self._on_log_activated)

        logs_scroll = Gtk.ScrolledWindow()
        logs_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        logs_scroll.set_min_content_height(80)
        logs_scroll.add(logs_tv)
        logs_body.pack_start(logs_scroll, True, True, 0)

        collect_row = Gtk.Box(spacing=8)
        btn_collect = Gtk.Button(label="Collect & View")
        btn_collect.get_style_context().add_class("sidebar-btn")
        btn_collect.connect("clicked", self._on_collect_log)
        collect_row.pack_start(btn_collect, False, False, 0)
        self._collect_status_lbl = Gtk.Label(label="↑ double-click to view")
        self._collect_status_lbl.get_style_context().add_class("dim-label")
        collect_row.pack_start(self._collect_status_lbl, False, False, 0)
        logs_body.pack_start(collect_row, False, False, 0)

        logs_frame.add(logs_body)
        inner.pack_start(logs_frame, False, False, 0)

        # ── Change history ───────────────────────────────────────────────────
        hist_frame = self._make_section("📖 Change History")
        hist_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hist_body.set_margin_start(8)
        hist_body.set_margin_end(8)
        hist_body.set_margin_bottom(6)

        hist_tabs = Gtk.Box(spacing=2)
        btn_hist_ctx = Gtk.Button(label="CTX")
        btn_hist_ctx.get_style_context().add_class("sidebar-btn")
        btn_hist_rules = Gtk.Button(label="Rules")
        btn_hist_rules.get_style_context().add_class("sidebar-btn")
        hist_tabs.pack_start(btn_hist_ctx, True, True, 0)
        hist_tabs.pack_start(btn_hist_rules, True, True, 0)
        hist_body.pack_start(hist_tabs, False, False, 0)

        self._hist_view = Gtk.TextView()
        self._hist_view.set_editable(False)
        self._hist_view.set_monospace(True)
        self._hist_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._hist_view.set_left_margin(4)
        self._hist_view.set_cursor_visible(False)
        hist_scroll = Gtk.ScrolledWindow()
        hist_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        hist_scroll.set_min_content_height(100)
        hist_scroll.add(self._hist_view)
        hist_body.pack_start(hist_scroll, True, True, 0)

        btn_hist_ctx.connect("clicked", lambda _: self._refresh_history("ctx"))
        btn_hist_rules.connect("clicked", lambda _: self._refresh_history("rules"))

        hist_frame.add(hist_body)
        inner.pack_start(hist_frame, False, False, 0)

        # ── Wizard ───────────────────────────────────────────────────────────
        wizard_frame = self._make_section("🧙 Auto-configure Wizard")
        wizard_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        wizard_body.set_margin_start(8)
        wizard_body.set_margin_end(8)
        wizard_body.set_margin_top(4)
        wizard_body.set_margin_bottom(6)

        wizard_lbl = Gtk.Label(
            label="Analyzes project context and proposes rules automatically."
        )
        wizard_lbl.set_line_wrap(True)
        wizard_lbl.set_xalign(0)
        wizard_body.pack_start(wizard_lbl, False, False, 0)

        btn_wizard = Gtk.Button(label="▶ Run Memory Wizard")
        btn_wizard.get_style_context().add_class("sidebar-btn")
        btn_wizard.connect("clicked", self._on_run_wizard)
        wizard_body.pack_start(btn_wizard, False, False, 0)

        wizard_frame.add(wizard_body)
        inner.pack_start(wizard_frame, False, False, 0)

        self.show_all()
        GLib.idle_add(self._load_projects)

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _make_section(self, title):
        frame = Gtk.Frame(label=title)
        frame.set_margin_start(6)
        frame.set_margin_end(6)
        frame.set_margin_top(6)
        frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        return frame

    def _load_projects(self, *_):
        self._proj_combo.remove_all()
        try:
            db = sqlite3.connect(CTX_DB)
            rows = db.execute("SELECT name FROM sessions ORDER BY name").fetchall()
            db.close()
            for row in rows:
                self._proj_combo.append_text(row[0])
            if rows:
                self._proj_combo.set_active(0)
        except Exception:
            pass

    def _get_project(self):
        return self._proj_combo.get_active_text() or ""

    def _on_project_changed(self, combo):
        project = combo.get_active_text()
        if project:
            self._current_project = project
            self._refresh_rules()
            self._refresh_config()
            self._refresh_logs()

    def _refresh_rules(self):
        project = self._get_project()
        if not project or not self._rules_store:
            return
        self._rules_store.clear()
        try:
            db = sqlite3.connect(CTX_DB)
            rows = db.execute(
                "SELECT id, rule, enabled FROM rules WHERE project = ? ORDER BY id",
                (project,),
            ).fetchall()
            db.close()
            for row in rows:
                self._rules_store.append([bool(row[2]), row[0], row[1]])
        except Exception:
            pass

    def _refresh_config(self):
        project = self._get_project()
        if not project:
            return
        try:
            db = sqlite3.connect(CTX_DB)
            row = db.execute(
                "SELECT inject_every, refresh_every FROM rules_config WHERE project = ?",
                (project,),
            ).fetchone()
            db.close()
            if row:
                self._spin_inject.set_value(row[0])
                self._spin_refresh.set_value(row[1])
            else:
                self._spin_inject.set_value(100)
                self._spin_refresh.set_value(200)
        except Exception:
            pass

    def _refresh_logs(self):
        project = self._get_project()
        self._logs_store.clear()
        if not project:
            return
        try:
            db = sqlite3.connect(CTX_DB)
            row = db.execute("SELECT work_dir FROM sessions WHERE name = ?", (project,)).fetchone()
            db.close()
            if not row or not row[0]:
                return
            log_dir = Path(row[0]) / "claude_log"
            if not log_dir.exists():
                return
            files = sorted(log_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
            for f in files[:30]:
                mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                size_kb = f.stat().st_size // 1024
                self._logs_store.append([f"{mtime}  {f.name}  ({size_kb} KB)", str(f)])
        except Exception:
            pass

    # ── Event handlers ───────────────────────────────────────────────────────

    def _on_save_config(self, _):
        project = self._get_project()
        if not project:
            return
        inject_every = int(self._spin_inject.get_value())
        refresh_every = int(self._spin_refresh.get_value())
        try:
            subprocess.run(
                ["ctx", "rules", "config", project,
                 "--inject-every", str(inject_every),
                 "--refresh-every", str(refresh_every)],
                check=True, capture_output=True, timeout=5,
            )
            self._cfg_status_lbl.set_text("✓ Saved")
        except Exception as e:
            self._cfg_status_lbl.set_text(f"✗ {e}")
        GLib.timeout_add_seconds(3, lambda: self._cfg_status_lbl.set_text("") or False)

    def _on_add_rule(self, _):
        project = self._get_project()
        rule = self._rule_entry.get_text().strip()
        if not project or not rule:
            return
        try:
            subprocess.run(
                ["ctx", "rules", "add", project, rule],
                check=True, capture_output=True, timeout=5,
            )
            self._rule_entry.set_text("")
            self._refresh_rules()
        except Exception:
            pass

    def _on_remove_rule(self, _):
        project = self._get_project()
        sel = self._rules_tv.get_selection()
        model, it = sel.get_selected()
        if not it:
            return
        rule_id = model[it][1]
        try:
            subprocess.run(
                ["ctx", "rules", "remove", project, str(rule_id)],
                check=True, capture_output=True, timeout=5,
            )
            self._refresh_rules()
        except Exception:
            pass

    def _on_rule_toggled(self, renderer, path):
        project = self._get_project()
        it = self._rules_store.get_iter(path)
        rule_id = self._rules_store[it][1]
        currently_enabled = self._rules_store[it][0]
        subcmd = "disable" if currently_enabled else "enable"
        try:
            subprocess.run(
                ["ctx", "rules", subcmd, project, str(rule_id)],
                check=True, capture_output=True, timeout=5,
            )
            self._rules_store[it][0] = not currently_enabled
        except Exception:
            pass

    def _on_log_activated(self, tv, path, column):
        model = tv.get_model()
        jsonl_path = model[path][1]
        if not jsonl_path or not os.path.exists(jsonl_path):
            return
        self._show_log_dialog(jsonl_path)

    def _show_log_dialog(self, jsonl_path):
        dlg = Gtk.Dialog(
            title=f"Session log: {os.path.basename(jsonl_path)}",
            transient_for=self.app,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dlg.set_default_size(700, 500)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        dlg.connect("response", lambda d, _: d.destroy())

        buf = Gtk.TextBuffer()
        tv = Gtk.TextView(buffer=buf)
        tv.set_editable(False)
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_left_margin(8)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(tv)
        dlg.get_content_area().pack_start(scroll, True, True, 0)

        try:
            result = subprocess.run(
                ["claude_log", "parse", jsonl_path, "--limit", "100"],
                capture_output=True, text=True, timeout=10,
            )
            buf.set_text(result.stdout or "(empty)")
        except Exception as e:
            buf.set_text(f"Error: {e}")

        dlg.show_all()

    def _on_collect_log(self, _):
        """Collect the current active Claude Code session's JSONL, then show newest log."""
        nb = getattr(self.app, "notebook", None)
        if nb is None:
            return
        current = nb.get_nth_page(nb.get_current_page())
        if not current or not getattr(current, "claude_config", None):
            show_info_dialog(self.app, "Collect log",
                             "Switch to an active Claude Code tab first.")
            return
        project_dir = current.claude_config.get("project_dir", "")
        if not project_dir:
            return

        self._collect_status_lbl.set_text("Collecting…")
        stats_bar = getattr(current, "_stats_bar", None)
        jsonl_path = None
        if stats_bar and getattr(stats_bar, "_reader", None):
            jsonl_path = stats_bar._reader._cached
        cmd = ["claude_log", "collect", project_dir]
        if jsonl_path:
            cmd.append(jsonl_path)

        import threading, datetime as _dt
        _log = open("/tmp/bterminal_collect.log", "a")
        _log.write(f"\n=== {_dt.datetime.now()} collect start, cmd={cmd}\n")
        _log.flush()

        def _run():
            error = None
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=15)
                _log.write(f"collect done rc={r.returncode} stderr={r.stderr[:100]}\n"); _log.flush()
            except Exception as e:
                error = str(e)
                _log.write(f"collect exception: {e}\n"); _log.flush()
            GLib.idle_add(_done, error)

        def _done(error):
            try:
                _log.write(f"_done called error={error}\n"); _log.flush()
                if error:
                    self._collect_status_lbl.set_text(f"✗ {error}")
                    GLib.timeout_add_seconds(4, lambda: self._collect_status_lbl.set_text("↑ double-click to view") or False)
                    return False
                _log.write("calling _refresh_logs\n"); _log.flush()
                self._refresh_logs()
                _log.write("_refresh_logs done\n"); _log.flush()
                log_dir = Path(project_dir) / "claude_log"
                files = sorted(log_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True) if log_dir.exists() else []
                _log.write(f"files found: {len(files)}, log_dir={log_dir}\n"); _log.flush()
                if files:
                    self._collect_status_lbl.set_text(f"✓ {len(files)} logs")
                    GLib.timeout_add_seconds(3, lambda: self._collect_status_lbl.set_text("↑ double-click to view") or False)
                    _log.write(f"calling _show_log_dialog({files[0]})\n"); _log.flush()
                    self._show_log_dialog(str(files[0]))
                    _log.write("_show_log_dialog returned OK\n"); _log.flush()
                else:
                    self._collect_status_lbl.set_text("✗ no logs found")
                    GLib.timeout_add_seconds(3, lambda: self._collect_status_lbl.set_text("↑ double-click to view") or False)
            except Exception as ex:
                import traceback
                _log.write(f"EXCEPTION in _done: {traceback.format_exc()}\n"); _log.flush()
                self._collect_status_lbl.set_text(f"✗ {ex}")
            return False

        threading.Thread(target=_run, daemon=True).start()

    def _refresh_history(self, kind: str):
        project = self._get_project()
        if not project:
            return
        try:
            if kind == "ctx":
                result = subprocess.run(
                    ["ctx", "log", project, "--limit", "40"],
                    capture_output=True, text=True, timeout=5,
                )
            else:
                result = subprocess.run(
                    ["ctx", "log-rules", project, "--limit", "40"],
                    capture_output=True, text=True, timeout=5,
                )
            text = result.stdout.strip() or "(no history yet)"
        except Exception as e:
            text = f"Error: {e}"
        self._hist_view.get_buffer().set_text(text)

    def _on_run_wizard(self, _):
        project = self._get_project()
        if not project:
            show_error_dialog(self.app, "Select a project first.")
            return
        project_dir = ""
        try:
            db = sqlite3.connect(CTX_DB)
            row = db.execute("SELECT work_dir FROM sessions WHERE name = ?", (project,)).fetchone()
            db.close()
            if row and row[0]:
                project_dir = row[0]
        except Exception:
            pass

        cmd = ["memory_wizard", project]
        if project_dir:
            cmd += ["--project-dir", project_dir]

        self.app.open_wizard_tab(project, cmd, on_done=self.refresh)

    def refresh(self):
        project = self._get_project()
        if project:
            self._refresh_rules()
            self._refresh_logs()


class SkillsPanel(Gtk.Box):
    """Panel for managing Claude Code skills (~/.claude/commands/ and .claude/commands/)."""

    GLOBAL_DIR = Path.home() / ".claude" / "commands"

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.app = app

        header = Gtk.Label(label="Skills")
        header.get_style_context().add_class("sidebar-header")
        header.set_halign(Gtk.Align.START)
        self.pack_start(header, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.set_margin_start(8)
        inner.set_margin_end(8)
        inner.set_margin_top(6)
        inner.set_margin_bottom(8)

        # ── Skill list ────────────────────────────────────────────────────
        # columns: scope (str), name (str), path (str), description (str)
        self._store = Gtk.ListStore(str, str, str, str)
        tv = Gtk.TreeView(model=self._store)
        tv.set_headers_visible(True)
        tv.set_size_request(-1, 160)
        tv.set_tooltip_column(3)
        self._tv = tv

        col_scope = Gtk.TreeViewColumn("Scope", Gtk.CellRendererText(), text=0)
        col_scope.set_fixed_width(60)
        tv.append_column(col_scope)

        ren_name = Gtk.CellRendererText()
        ren_name.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_name = Gtk.TreeViewColumn("Skill", ren_name, text=1)
        col_name.set_expand(True)
        tv.append_column(col_name)

        tv.connect("row-activated", self._on_view)

        tv_scroll = Gtk.ScrolledWindow()
        tv_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tv_scroll.set_min_content_height(120)
        tv_scroll.add(tv)
        inner.pack_start(tv_scroll, False, False, 0)

        # ── Location label ────────────────────────────────────────────────
        self._loc_lbl = Gtk.Label(label="")
        self._loc_lbl.get_style_context().add_class("dim-label")
        self._loc_lbl.set_halign(Gtk.Align.START)
        self._loc_lbl.set_ellipsize(Pango.EllipsizeMode.START)
        inner.pack_start(self._loc_lbl, False, False, 0)

        # ── Action buttons ────────────────────────────────────────────────
        btn_row = Gtk.Box(spacing=4)
        btn_view = Gtk.Button(label="View")
        btn_view.get_style_context().add_class("sidebar-btn")
        btn_view.connect("clicked", self._on_view)
        btn_row.pack_start(btn_view, True, True, 0)

        btn_move = Gtk.Button(label="Move ↕")
        btn_move.get_style_context().add_class("sidebar-btn")
        btn_move.set_tooltip_text("Move between global and project")
        btn_move.connect("clicked", self._on_move)
        btn_row.pack_start(btn_move, True, True, 0)

        btn_del = Gtk.Button(label="Delete")
        btn_del.get_style_context().add_class("sidebar-btn")
        btn_del.connect("clicked", self._on_delete)
        btn_row.pack_start(btn_del, True, True, 0)
        inner.pack_start(btn_row, False, False, 0)

        btn_row2 = Gtk.Box(spacing=4)
        btn_new = Gtk.Button(label="+ New skill")
        btn_new.get_style_context().add_class("sidebar-btn")
        btn_new.connect("clicked", self._on_new)
        btn_row2.pack_start(btn_new, True, True, 0)

        btn_refresh = Gtk.Button(label="↺")
        btn_refresh.get_style_context().add_class("sidebar-btn")
        btn_refresh.set_tooltip_text("Refresh list")
        btn_refresh.connect("clicked", lambda _: self._refresh())
        btn_row2.pack_start(btn_refresh, False, False, 0)
        inner.pack_start(btn_row2, False, False, 0)

        scroll.add(inner)
        self.pack_start(scroll, True, True, 0)

        tv.get_selection().connect("changed", self._on_selection_changed)
        self._refresh()

    # ── Helpers ────────────────────────────────────────────────────────────

    def _project_dir(self):
        nb = getattr(self.app, "notebook", None)
        if nb is None:
            return None
        page = nb.get_nth_page(nb.get_current_page())
        if page and getattr(page, "claude_config", None):
            pd = page.claude_config.get("project_dir", "")
            return pd or None
        return None

    def _project_commands_dir(self):
        pd = self._project_dir()
        if pd:
            return Path(pd) / ".claude" / "commands"
        return None

    def _skill_description(self, path: Path) -> str:
        try:
            text = path.read_text(errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
                if line and not line.startswith("---"):
                    return line[:80]
        except Exception:
            pass
        return ""

    def _bundled_names(self) -> set:
        try:
            return {f.stem for f in _BUNDLED_SKILLS_DIR.glob("*.md")}
        except Exception:
            return set()

    def _refresh(self):
        self._store.clear()
        seen = set()
        bundled = self._bundled_names()

        def _add_dir(directory: Path, scope: str):
            if not directory or not directory.exists():
                return
            for f in sorted(directory.glob("*.md")):
                name = f.stem
                key = (scope, name)
                if key in seen:
                    continue
                seen.add(key)
                desc = self._skill_description(f)
                label = ("📦" if name in bundled else scope)
                self._store.append([label, name, str(f), desc])

        _add_dir(self.GLOBAL_DIR, "🌍 global")
        proj_dir = self._project_commands_dir()
        _add_dir(proj_dir, "📁 project")

        self._loc_lbl.set_text(
            f"global: {self.GLOBAL_DIR}"
            + (f"\nproject: {proj_dir}" if proj_dir else "")
        )

    def _selected(self):
        model, it = self._tv.get_selection().get_selected()
        if not it:
            return None, None, None
        return model[it][0], model[it][1], model[it][2]  # scope, name, path

    def _on_selection_changed(self, sel):
        scope, name, path = self._selected()
        if path:
            self._loc_lbl.set_text(path)

    # ── Actions ────────────────────────────────────────────────────────────

    def _on_view(self, *_):
        scope, name, path = self._selected()
        if not path:
            return
        try:
            content = Path(path).read_text(errors="replace")
        except Exception as e:
            content = f"Error reading file: {e}"

        dlg = Gtk.Dialog(
            title=f"/{name}  ({scope})",
            transient_for=self.app,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dlg.set_default_size(680, 520)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        dlg.connect("response", lambda d, _: d.destroy())

        buf = Gtk.TextBuffer()
        buf.set_text(content)
        tv = Gtk.TextView(buffer=buf)
        tv.set_editable(False)
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_left_margin(8)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(tv)
        dlg.get_content_area().pack_start(sw, True, True, 0)
        dlg.show_all()

    def _on_move(self, _):
        scope, name, path = self._selected()
        if not path:
            return
        src = Path(path)

        if "global" in scope:
            dest_dir = self._project_commands_dir()
            if not dest_dir:
                show_info_dialog(self.app, "Move skill",
                                 "No active Claude Code tab with a project directory.")
                return
            dest_label = "project"
        else:
            dest_dir = self.GLOBAL_DIR
            dest_label = "global"

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name

        if dest.exists():
            show_info_dialog(self.app, "Move skill",
                             f"/{name} already exists in {dest_label}.")
            return

        try:
            import shutil as _shutil
            _shutil.move(str(src), str(dest))
        except Exception as e:
            show_error_dialog(self.app, f"Move failed: {e}")
            return

        self._refresh()

    def _on_delete(self, _):
        scope, name, path = self._selected()
        if not path:
            return

        dlg = Gtk.MessageDialog(
            transient_for=self.app,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete /{name}?",
        )
        dlg.format_secondary_text(f"{path}")
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.YES:
            return

        try:
            Path(path).unlink()
        except Exception as e:
            show_error_dialog(self.app, f"Delete failed: {e}")
            return

        self._refresh()

    def _on_new(self, _):
        # Ask name and scope
        dlg = Gtk.Dialog(
            title="New skill",
            transient_for=self.app,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dlg.set_default_size(400, -1)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)

        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        name_row = Gtk.Box(spacing=8)
        name_row.pack_start(Gtk.Label(label="Name:"), False, False, 0)
        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text("my-skill")
        name_entry.set_activates_default(True)
        name_row.pack_start(name_entry, True, True, 0)
        box.pack_start(name_row, False, False, 0)

        scope_row = Gtk.Box(spacing=8)
        scope_row.pack_start(Gtk.Label(label="Location:"), False, False, 0)
        radio_global = Gtk.RadioButton.new_with_label(None, "Global")
        radio_proj   = Gtk.RadioButton.new_with_label_from_widget(radio_global, "Project")
        scope_row.pack_start(radio_global, False, False, 0)
        scope_row.pack_start(radio_proj, False, False, 0)
        box.pack_start(scope_row, False, False, 0)

        box.show_all()
        resp = dlg.run()
        name = name_entry.get_text().strip().replace(" ", "-")
        use_project = radio_proj.get_active()
        dlg.destroy()

        if resp != Gtk.ResponseType.OK or not name:
            return

        if use_project:
            dest_dir = self._project_commands_dir()
            if not dest_dir:
                show_info_dialog(self.app, "New skill",
                                 "No active Claude Code tab with a project directory.")
                return
        else:
            dest_dir = self.GLOBAL_DIR

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{name}.md"

        if not dest.exists():
            dest.write_text(
                f"# /skill: {name}\n\n"
                f"## When to invoke\n\n"
                f"Describe when Claude should use this skill.\n\n"
                f"## Flow\n\n"
                f"Step-by-step instructions for Claude.\n"
            )

        self._refresh()

        # Open in editor
        try:
            subprocess.Popen(["xdg-open", str(dest)])
        except Exception:
            pass


class FilesPanel(Gtk.Box):
    """Sidebar file browser — shows project files, opens with meld by default."""

    # Dirs/files to skip in the tree
    _IGNORE = {".git", "__pycache__", ".claude", "node_modules", ".venv", "venv"}

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.app = app
        self._root_dir: str = ""
        self._pinned_dir: str = ""   # "" = follow active tab

        header = Gtk.Label(label="Files")
        header.get_style_context().add_class("sidebar-header")
        header.set_halign(Gtk.Align.START)
        self.pack_start(header, False, False, 0)

        # ── Project dropdown ─────────────────────────────────────────────────
        # ListStore: display label (str), project_dir (str)
        self._proj_store = Gtk.ListStore(str, str)
        self._combo = Gtk.ComboBox(model=self._proj_store)
        ren = Gtk.CellRendererText()
        ren.set_property("ellipsize", Pango.EllipsizeMode.END)
        self._combo.pack_start(ren, True)
        self._combo.add_attribute(ren, "text", 0)
        self._combo.set_margin_start(8)
        self._combo.set_margin_end(8)
        self._combo.set_margin_top(4)
        self._combo.set_margin_bottom(2)
        self._combo.connect("changed", self._on_combo_changed)
        self.pack_start(self._combo, False, False, 0)

        # ── TreeStore: display_name, full_path, is_dir ──────────────────────
        self._store = Gtk.TreeStore(str, str, bool)
        self._tv = Gtk.TreeView(model=self._store)
        self._tv.set_headers_visible(False)
        self._tv.set_enable_tree_lines(True)

        ren_icon = Gtk.CellRendererText()
        ren_name = Gtk.CellRendererText()
        ren_name.set_property("ellipsize", Pango.EllipsizeMode.END)

        col = Gtk.TreeViewColumn()
        col.pack_start(ren_icon, False)
        col.pack_start(ren_name, True)
        col.set_cell_data_func(ren_icon, self._render_icon)
        col.add_attribute(ren_name, "text", 0)
        col.set_expand(True)
        self._tv.append_column(col)

        self._tv.connect("row-activated", self._on_row_activated)
        self._tv.connect("button-press-event", self._on_button_press)
        self._tv.get_selection().connect("changed", self._on_selection_changed)

        tv_scroll = Gtk.ScrolledWindow()
        tv_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tv_scroll.set_vexpand(True)
        tv_scroll.add(self._tv)
        self.pack_start(tv_scroll, True, True, 0)

        # ── Buttons ──────────────────────────────────────────────────────────
        btn_row = Gtk.Box(spacing=4)
        btn_row.set_margin_start(8)
        btn_row.set_margin_end(8)
        btn_row.set_margin_top(4)
        btn_row.set_margin_bottom(6)

        self._btn_meld = Gtk.Button(label="Open in Meld")
        self._btn_meld.get_style_context().add_class("sidebar-btn")
        self._btn_meld.set_sensitive(False)
        self._btn_meld.connect("clicked", self._on_open_meld)
        btn_row.pack_start(self._btn_meld, True, True, 0)

        btn_refresh = Gtk.Button(label="↺")
        btn_refresh.get_style_context().add_class("sidebar-btn")
        btn_refresh.set_tooltip_text("Refresh file tree")
        btn_refresh.connect("clicked", lambda _: self._refresh())
        btn_row.pack_start(btn_refresh, False, False, 0)

        self.pack_start(btn_row, False, False, 0)

        self._selected_path: str = ""

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _render_icon(self, col, cell, model, it, _data):
        is_dir = model.get_value(it, 2)
        cell.set_property("text", "📁 " if is_dir else "  ")

    @staticmethod
    def _find_project_root(d: str) -> str:
        """Return the project root for d.

        If d itself is a git root → return d.
        If d's basename is a generic subdir name (docs, src, …) → walk up to
          the nearest git root (max 4 levels).
        Otherwise → return d as-is (the project_dir is already meaningful).
        """
        path = d.rstrip("/")
        if os.path.isdir(os.path.join(path, ".git")):
            return path
        basename = os.path.basename(path).lower()
        if basename not in _GENERIC_SUBDIRS:
            return d
        # Generic subdir — walk up looking for .git (bounded)
        current = os.path.dirname(path)
        for _ in range(4):
            if not current or current == os.path.dirname(current):
                break
            if os.path.isdir(os.path.join(current, ".git")):
                return current
            current = os.path.dirname(current)
        return d

    def _get_project_dir(self) -> str:
        if self._pinned_dir:
            return self._pinned_dir if os.path.isdir(self._pinned_dir) else ""
        # Auto: active Claude tab first
        nb = self.app.notebook
        page = nb.get_nth_page(nb.get_current_page())
        if page and getattr(page, "claude_config", None):
            d = page.claude_config.get("project_dir", "")
            if d and os.path.isdir(d):
                return self._find_project_root(d)
        # Fallback: first Claude tab with a valid project dir
        for i in range(nb.get_n_pages()):
            tab = nb.get_nth_page(i)
            if getattr(tab, "claude_config", None):
                d = tab.claude_config.get("project_dir", "")
                if d and os.path.isdir(d):
                    return self._find_project_root(d)
        return ""

    def _populate_combo(self):
        """Rebuild the project dropdown from saved Claude sessions + active tabs."""
        self._combo.handler_block_by_func(self._on_combo_changed)
        self._proj_store.clear()
        self._proj_store.append(["— Active tab —", ""])

        seen: set[str] = set()
        # Sessions from saved configs
        for cs in self.app.claude_manager.all():
            d = cs.get("project_dir", "").rstrip("/")
            name = cs.get("name", "") or os.path.basename(d)
            if d and d not in seen and os.path.isdir(d):
                seen.add(d)
                short = os.path.basename(d)
                self._proj_store.append([f"{name}  ({short})" if name != short else name, d])

        # Open tabs not in saved configs
        nb = self.app.notebook
        for i in range(nb.get_n_pages()):
            tab = nb.get_nth_page(i)
            if getattr(tab, "claude_config", None):
                d = tab.claude_config.get("project_dir", "").rstrip("/")
                if d and d not in seen and os.path.isdir(d):
                    seen.add(d)
                    self._proj_store.append([os.path.basename(d) + "  (tab)", d])

        # Restore selection
        target = self._pinned_dir
        active_idx = 0
        for i, row in enumerate(self._proj_store):
            if row[1] == target:
                active_idx = i
                break
        self._combo.set_active(active_idx)
        self._combo.handler_unblock_by_func(self._on_combo_changed)

    def _on_combo_changed(self, combo):
        it = combo.get_active_iter()
        if it is None:
            return
        self._pinned_dir = self._proj_store.get_value(it, 1)
        self._load_tree()

    def _populate(self, parent_iter, directory: str):
        try:
            entries = list(os.scandir(directory))
        except PermissionError:
            return
        dirs = sorted([e for e in entries if e.is_dir()
                       and e.name not in self._IGNORE], key=lambda e: e.name.lower())
        files = sorted([e for e in entries if e.is_file()
                        and not e.name.startswith(".")], key=lambda e: e.name.lower())
        for e in dirs:
            it = self._store.append(parent_iter, [e.name, e.path, True])
            # Add a dummy child so the expander arrow appears
            self._store.append(it, ["", "__dummy__", False])
        for e in files:
            self._store.append(parent_iter, [e.name, e.path, False])

    def _on_row_expanded(self, tv, it, path):
        # Remove dummy, populate real children
        first = self._store.iter_children(it)
        if first and self._store.get_value(first, 1) == "__dummy__":
            self._store.remove(first)
            self._populate(it, self._store.get_value(it, 1))

    # ── Public ────────────────────────────────────────────────────────────────

    def _refresh(self):
        """Rebuild dropdown then reload file tree."""
        self._populate_combo()
        self._load_tree()

    def _load_tree(self):
        """Reload the file tree for the currently selected project."""
        d = self._get_project_dir()
        self._root_dir = d
        self._store.clear()
        self._selected_path = ""
        self._btn_meld.set_sensitive(False)
        if not d:
            return
        self._tv.connect("row-expanded", self._on_row_expanded)
        self._populate(None, d)
        self._tv.expand_row(Gtk.TreePath.new_first(), False)

    # ── Callbacks ─────────────────────────────────────────────────────────────

    def _on_selection_changed(self, sel):
        model, it = sel.get_selected()
        if it:
            path = model.get_value(it, 1)
            self._selected_path = path if path != "__dummy__" else ""
        else:
            self._selected_path = ""
        self._btn_meld.set_sensitive(bool(self._selected_path))

    def _on_row_activated(self, tv, path, col):
        it = self._store.get_iter(path)
        fpath = self._store.get_value(it, 1)
        is_dir = self._store.get_value(it, 2)
        if is_dir:
            if tv.row_expanded(path):
                tv.collapse_row(path)
            else:
                tv.expand_row(path, False)
        else:
            self._show_diff_dialog(fpath)

    def _on_open_meld(self, _btn):
        if self._selected_path:
            self._show_diff_dialog(self._selected_path)

    def _open_with_meld(self, path: str):
        if not shutil.which("meld"):
            show_error_dialog(self.app, "meld not found.\nInstall it: sudo apt install meld")
            return
        try:
            subprocess.Popen(["meld", path])
        except Exception as e:
            show_error_dialog(self.app, f"Failed to open meld:\n{e}")

    def _get_git_root(self, path: str) -> str:
        """Return the git root for path, or empty string if not in a repo."""
        try:
            r = subprocess.run(
                ["git", "rev-parse", "--show-toplevel"],
                cwd=os.path.dirname(path) if os.path.isfile(path) else path,
                capture_output=True, text=True, timeout=5,
            )
            return r.stdout.strip() if r.returncode == 0 else ""
        except Exception:
            return ""

    def _get_recent_commits(self, git_root: str, n: int = 10) -> list[tuple[str, str]]:
        """Return list of (short_hash, subject) for the last n commits."""
        try:
            r = subprocess.run(
                ["git", "log", f"-{n}", "--pretty=format:%h %s"],
                cwd=git_root, capture_output=True, text=True, timeout=5,
            )
            commits = []
            for line in r.stdout.splitlines():
                parts = line.split(" ", 1)
                if len(parts) == 2:
                    commits.append((parts[0], parts[1]))
            return commits
        except Exception:
            return []

    def _show_diff_dialog(self, fpath: str):
        if not shutil.which("meld"):
            show_error_dialog(self.app, "meld not found.\nInstall it: sudo apt install meld")
            return

        git_root = self._get_git_root(fpath)
        commits = self._get_recent_commits(git_root) if git_root else []

        win = self.get_toplevel()
        dlg = Gtk.Dialog(title="Diff with commit", transient_for=win, modal=True)
        dlg.set_default_size(480, -1)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        "Open Meld",     Gtk.ResponseType.OK)

        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(12); box.set_margin_end(12)
        box.set_margin_top(12);   box.set_margin_bottom(12)

        name_lbl = Gtk.Label(label=f"File: {os.path.relpath(fpath, git_root) if git_root else fpath}")
        name_lbl.set_xalign(0)
        name_lbl.get_style_context().add_class("dim-label")
        box.pack_start(name_lbl, False, False, 0)

        # ── Commit dropdown ──────────────────────────────────────────────────
        commit_lbl = Gtk.Label(label="Compare with:")
        commit_lbl.set_xalign(0)
        box.pack_start(commit_lbl, False, False, 0)

        # ListStore: display (str), hash (str)
        combo_store = Gtk.ListStore(str, str)
        combo_store.append(["HEAD (last commit)", "HEAD"])
        for h, subj in commits[1:]:   # skip HEAD duplicate if present
            short_subj = subj[:60] + "…" if len(subj) > 60 else subj
            combo_store.append([f"{h}  {short_subj}", h])

        combo = Gtk.ComboBox(model=combo_store)
        ren = Gtk.CellRendererText()
        ren.set_property("ellipsize", Pango.EllipsizeMode.END)
        combo.pack_start(ren, True)
        combo.add_attribute(ren, "text", 0)
        combo.set_active(0)
        box.pack_start(combo, False, False, 0)

        # ── Custom hash entry ────────────────────────────────────────────────
        custom_lbl = Gtk.Label(label="Or enter commit hash / branch:")
        custom_lbl.set_xalign(0)
        box.pack_start(custom_lbl, False, False, 4)

        custom_entry = Gtk.Entry()
        custom_entry.set_placeholder_text("e.g. a1b2c3d  or  main  or  HEAD~5")
        custom_entry.set_activates_default(True)
        box.pack_start(custom_entry, False, False, 0)

        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.show_all()

        if not commits:
            combo.set_sensitive(False)
            combo_lbl_warn = Gtk.Label(label="(not a git repository)")
            combo_lbl_warn.get_style_context().add_class("dim-label")
            box.pack_start(combo_lbl_warn, False, False, 0)
            box.show_all()

        response = dlg.run()
        ref = custom_entry.get_text().strip()
        if not ref:
            it2 = combo.get_active_iter()
            ref = combo_store.get_value(it2, 1) if it2 else "HEAD"
        dlg.destroy()

        if response != Gtk.ResponseType.OK:
            return
        self._meld_diff_with_commit(fpath, git_root, ref)

    def _meld_diff_with_commit(self, fpath: str, git_root: str, ref: str):
        """Extract file at ref from git and open meld for diff."""
        if not git_root:
            show_error_dialog(self.app, "File is not in a git repository.")
            return
        rel = os.path.relpath(fpath, git_root)
        try:
            result = subprocess.run(
                ["git", "show", f"{ref}:{rel}"],
                cwd=git_root, capture_output=True, timeout=10,
            )
            if result.returncode != 0:
                msg = result.stderr.decode(errors="replace").strip()
                show_error_dialog(self.app, f"git show failed:\n{msg}")
                return
        except Exception as e:
            show_error_dialog(self.app, f"git show error:\n{e}")
            return

        import tempfile
        suffix = os.path.splitext(fpath)[1] or ".txt"
        short_ref = ref[:12]
        tmp = tempfile.NamedTemporaryFile(
            prefix=f"{os.path.basename(fpath)}.{short_ref}.",
            suffix=suffix, delete=False,
        )
        tmp.write(result.stdout)
        tmp.close()

        try:
            subprocess.Popen(["meld", tmp.name, fpath],
                             start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except Exception as e:
            os.unlink(tmp.name)
            show_error_dialog(self.app, f"Failed to open meld:\n{e}")

    def _copy_to_clipboard(self, text: str):
        Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD).set_text(text, -1)

    def _on_button_press(self, tv, event):
        if event.button != 3:
            return False
        info = tv.get_path_at_pos(int(event.x), int(event.y))
        if not info:
            return False
        tree_path, _col, _cx, _cy = info
        tv.get_selection().select_path(tree_path)
        it = self._store.get_iter(tree_path)
        fpath = self._store.get_value(it, 1)
        if not fpath or fpath == "__dummy__":
            return False

        rel = os.path.relpath(fpath, self._root_dir) if self._root_dir else fpath
        name = os.path.basename(fpath)
        is_dir = self._store.get_value(it, 2)

        menu = Gtk.Menu()

        def _item(label, cb):
            it2 = Gtk.MenuItem(label=label)
            it2.connect("activate", lambda _: cb())
            menu.append(it2)

        _item("Open in Meld",          lambda: self._open_with_meld(fpath))
        if not is_dir:
            _item("Diff with commit…",  lambda: self._show_diff_dialog(fpath))

        # "Open With ▸" submenu
        open_with_item = Gtk.MenuItem(label="Open With ▸")
        open_with_item.set_submenu(self._build_open_with_submenu(fpath))
        menu.append(open_with_item)

        menu.append(Gtk.SeparatorMenuItem())
        _item("Copy Path",             lambda: self._copy_to_clipboard(fpath))
        _item("Copy Relative Path",    lambda: self._copy_to_clipboard(rel))
        _item("Copy Name",             lambda: self._copy_to_clipboard(name))
        if not is_dir:
            menu.append(Gtk.SeparatorMenuItem())
            _item("Paste Path to Terminal", lambda: self._paste_to_terminal(fpath))

        menu.show_all()
        menu.popup_at_pointer(event)
        return True

    def _build_open_with_submenu(self, path: str) -> Gtk.Menu:
        submenu = Gtk.Menu()

        item_default = Gtk.MenuItem(label="Default App")
        item_default.connect("activate", lambda _, p=path: self._launch(["xdg-open", p]))
        submenu.append(item_default)

        submenu.append(Gtk.SeparatorMenuItem())

        for label, cmd in [("VS Code", "code"), ("Zed", "zed"),
                            ("gedit", "gedit"), ("kate", "kate"),
                            ("File Manager", "xdg-open")]:
            if cmd == "xdg-open" or shutil.which(cmd):
                it2 = Gtk.MenuItem(label=label)
                it2.connect("activate", lambda _, c=cmd, p=path: self._launch([c, p]))
                submenu.append(it2)

        submenu.append(Gtk.SeparatorMenuItem())

        item_custom = Gtk.MenuItem(label="Custom…")
        item_custom.connect("activate", lambda _, p=path: self._open_with_custom(p))
        submenu.append(item_custom)

        return submenu

    def _launch(self, argv: list):
        try:
            subprocess.Popen(argv, start_new_session=True,
                             stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
        except FileNotFoundError:
            show_error_dialog(self.app, f"Command not found: {argv[0]}")

    def _open_with_custom(self, path: str):
        win = self.get_toplevel()
        dlg = Gtk.Dialog(title="Open With", transient_for=win, modal=True)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OK,     Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(12); box.set_margin_end(12)
        box.set_margin_top(12);   box.set_margin_bottom(12)

        lbl = Gtk.Label(label=f"Command to open:\n{os.path.basename(path)}")
        lbl.set_xalign(0)
        box.pack_start(lbl, False, False, 0)

        entry = Gtk.Entry()
        entry.set_placeholder_text("e.g. code, gedit, idea, vim")
        entry.set_activates_default(True)
        box.pack_start(entry, False, False, 0)

        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            cmd = entry.get_text().strip()
            if cmd:
                self._launch([cmd, path])
        dlg.destroy()

    def _paste_to_terminal(self, path: str):
        nb = self.app.notebook
        tab = nb.get_nth_page(nb.get_current_page())
        terminal = getattr(tab, "terminal", None)
        if terminal:
            terminal.feed_child((path + " ").encode())


class PluginManagerPanel(Gtk.Box):
    """Panel for managing BTerminal plugins."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app

        # ── Plugin list (TreeView) ──
        # Columns: enabled(bool), name(str), version(str), author(str), status(str), mod_name(str)
        self.store = Gtk.ListStore(bool, str, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)

        # Enabled toggle column
        toggle_renderer = Gtk.CellRendererToggle()
        toggle_renderer.connect("toggled", self._on_enabled_toggled)
        col_enabled = Gtk.TreeViewColumn("", toggle_renderer, active=0)
        col_enabled.set_min_width(30)
        col_enabled.set_max_width(30)
        self.tree.append_column(col_enabled)

        # Name column
        cell_name = Gtk.CellRendererText()
        cell_name.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_name = Gtk.TreeViewColumn("Name", cell_name, text=1)
        col_name.set_expand(True)
        self.tree.append_column(col_name)

        # Version column
        cell_ver = Gtk.CellRendererText()
        col_ver = Gtk.TreeViewColumn("Ver", cell_ver, text=2)
        col_ver.set_min_width(40)
        col_ver.set_max_width(60)
        self.tree.append_column(col_ver)

        # Author column
        cell_author = Gtk.CellRendererText()
        cell_author.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_author = Gtk.TreeViewColumn("Author", cell_author, text=3)
        col_author.set_min_width(60)
        col_author.set_max_width(100)
        self.tree.append_column(col_author)

        # Status column (Loaded / Disabled / Error)
        cell_status = Gtk.CellRendererText()
        col_status = Gtk.TreeViewColumn("Status", cell_status, text=4)
        col_status.set_min_width(50)
        col_status.set_max_width(70)
        col_status.set_cell_data_func(cell_status, self._style_status_cell)
        self.tree.append_column(col_status)

        tree_scroll = Gtk.ScrolledWindow()
        tree_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tree_scroll.add(self.tree)

        # ── Detail area ──
        detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        detail_box.set_border_width(6)
        self.detail_label = Gtk.Label()
        self.detail_label.set_xalign(0)
        self.detail_label.set_yalign(0)
        self.detail_label.set_line_wrap(True)
        self.detail_label.set_selectable(True)
        self.detail_label.set_markup(
            f'<span foreground="{CATPPUCCIN["overlay1"]}">Select a plugin to see details</span>'
        )
        detail_box.pack_start(self.detail_label, True, True, 0)

        # VPaned: tree on top, detail on bottom
        paned = Gtk.VPaned()
        paned.pack1(tree_scroll, resize=True, shrink=True)
        paned.pack2(detail_box, resize=False, shrink=False)
        self.pack_start(paned, True, True, 0)

        self.tree.get_selection().connect("changed", self._on_selection_changed)

        # ── Action buttons ──
        btn_box = Gtk.Box(spacing=4)
        btn_box.set_border_width(6)

        btn_add = Gtk.Button(label="Add File")
        btn_add.get_style_context().add_class("sidebar-btn")
        btn_add.connect("clicked", lambda _: self._on_add_file())
        btn_box.pack_start(btn_add, True, True, 0)

        btn_add_dir = Gtk.Button(label="Add Folder")
        btn_add_dir.get_style_context().add_class("sidebar-btn")
        btn_add_dir.connect("clicked", lambda _: self._on_add_folder())
        btn_box.pack_start(btn_add_dir, True, True, 0)

        btn_remove = Gtk.Button(label="Remove")
        btn_remove.get_style_context().add_class("sidebar-btn")
        btn_remove.connect("clicked", lambda _: self._on_remove_plugin())
        btn_box.pack_start(btn_remove, True, True, 0)

        btn_refresh = Gtk.Button(label="\u21bb")
        btn_refresh.get_style_context().add_class("sidebar-btn")
        btn_refresh.set_tooltip_text("Refresh")
        btn_refresh.connect("clicked", lambda _: self.refresh())
        btn_box.pack_start(btn_refresh, False, False, 0)

        self.pack_start(btn_box, False, False, 0)

    # ── Config persistence ──

    def _load_config(self):
        if os.path.isfile(PLUGINS_CONFIG_FILE):
            try:
                with open(PLUGINS_CONFIG_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_config(self, config):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(config, f, indent=2)
            os.replace(tmp, PLUGINS_CONFIG_FILE)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)

    # ── Data ──

    def refresh(self):
        self.store.clear()
        config = self._load_config()
        if not os.path.isdir(PLUGINS_DIR):
            return
        for entry in sorted(os.listdir(PLUGINS_DIR)):
            path = os.path.join(PLUGINS_DIR, entry)
            if os.path.isfile(path) and entry.endswith(".py"):
                mod_name = entry[:-3]
            elif os.path.isdir(path) and os.path.isfile(os.path.join(path, "__init__.py")):
                mod_name = entry
            else:
                continue
            enabled = config.get(mod_name, True)
            name, version, author, status = mod_name, "", "", "Disabled"
            if enabled:
                if mod_name in self.app._plugins:
                    plugin = self.app._plugins[mod_name]
                    name = plugin.title or plugin.name
                    version = plugin.version
                    author = plugin.author
                    status = "Loaded"
                else:
                    status = "Error"
            self.store.append([enabled, name, version, author, status, mod_name])

    # ── Cell styling ──

    def _style_status_cell(self, column, cell, model, iter_, data=None):
        status = model.get_value(iter_, 4)
        if status == "Loaded":
            cell.set_property("foreground", CATPPUCCIN["green"])
        elif status == "Disabled":
            cell.set_property("foreground", CATPPUCCIN["overlay1"])
        elif status == "Error":
            cell.set_property("foreground", CATPPUCCIN["red"])
        else:
            cell.set_property("foreground", CATPPUCCIN["text"])

    # ── Selection / detail ──

    def _on_selection_changed(self, selection):
        model, iter_ = selection.get_selected()
        if iter_ is None:
            self.detail_label.set_markup(
                f'<span foreground="{CATPPUCCIN["overlay1"]}">Select a plugin to see details</span>'
            )
            return
        mod_name = model.get_value(iter_, 5)
        name = model.get_value(iter_, 1)
        version = model.get_value(iter_, 2)
        author = model.get_value(iter_, 3)
        status = model.get_value(iter_, 4)
        desc = ""
        if mod_name in self.app._plugins:
            desc = self.app._plugins[mod_name].description
        txt = CATPPUCCIN["text"]
        sub = CATPPUCCIN["subtext0"]
        lines = [f'<span foreground="{txt}" weight="bold">{GLib.markup_escape_text(name)}</span>']
        if version:
            lines.append(f'<span foreground="{sub}">Version: {GLib.markup_escape_text(version)}</span>')
        if author:
            lines.append(f'<span foreground="{sub}">Author: {GLib.markup_escape_text(author)}</span>')
        lines.append(f'<span foreground="{sub}">Status: {GLib.markup_escape_text(status)}</span>')
        lines.append(f'<span foreground="{sub}">Module: {GLib.markup_escape_text(mod_name)}</span>')
        if desc:
            lines.append("")
            lines.append(f'<span foreground="{sub}">{GLib.markup_escape_text(desc)}</span>')
        self.detail_label.set_markup("\n".join(lines))

    # ── Enable/disable toggle ──

    def _on_enabled_toggled(self, renderer, path):
        iter_ = self.store.get_iter(path)
        enabled = not self.store.get_value(iter_, 0)
        mod_name = self.store.get_value(iter_, 5)
        config = self._load_config()
        config[mod_name] = enabled
        self._save_config(config)
        action = "enabled" if enabled else "disabled"
        dlg = Gtk.MessageDialog(
            transient_for=self.app, modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=f'Plugin "{mod_name}" {action}.\nRestart BTerminal for changes to take effect.',
        )
        dlg.run()
        dlg.destroy()
        self.refresh()

    # ── Add plugin ──

    def _on_add_file(self):
        dlg = Gtk.FileChooserDialog(
            title="Add Plugin File", parent=self.app,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        filt = Gtk.FileFilter()
        filt.set_name("Python files (*.py)")
        filt.add_pattern("*.py")
        dlg.add_filter(filt)
        if dlg.run() == Gtk.ResponseType.OK:
            src = dlg.get_filename()
            if src:
                self._copy_plugin(src)
        dlg.destroy()

    def _on_add_folder(self):
        dlg = Gtk.FileChooserDialog(
            title="Add Plugin Folder", parent=self.app,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        if dlg.run() == Gtk.ResponseType.OK:
            src = dlg.get_filename()
            if src:
                self._copy_plugin(src)
        dlg.destroy()

    def _copy_plugin(self, src):
        os.makedirs(PLUGINS_DIR, exist_ok=True)
        dest = os.path.join(PLUGINS_DIR, os.path.basename(src))
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)
            self.refresh()
        except Exception as e:
            dlg = Gtk.MessageDialog(
                transient_for=self.app, modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text=f"Failed to add plugin: {e}",
            )
            dlg.run()
            dlg.destroy()

    # ── Remove plugin ──

    def _on_remove_plugin(self):
        model, iter_ = self.tree.get_selection().get_selected()
        if iter_ is None:
            return
        mod_name = model.get_value(iter_, 5)
        dlg = Gtk.MessageDialog(
            transient_for=self.app, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f'Remove plugin "{mod_name}"?\nThis will delete the plugin files.',
        )
        if dlg.run() == Gtk.ResponseType.YES:
            if mod_name in self.app._plugins:
                try:
                    self.app._plugins[mod_name].deactivate()
                except Exception:
                    pass
                del self.app._plugins[mod_name]
            path_py = os.path.join(PLUGINS_DIR, mod_name + ".py")
            path_dir = os.path.join(PLUGINS_DIR, mod_name)
            try:
                if os.path.isfile(path_py):
                    os.unlink(path_py)
                elif os.path.isdir(path_dir):
                    shutil.rmtree(path_dir)
            except Exception as e:
                print(f"[plugins] Failed to remove {mod_name}: {e}")
            config = self._load_config()
            config.pop(mod_name, None)
            self._save_config(config)
            self.refresh()
        dlg.destroy()


class OptionsDialog(Gtk.Dialog):
    """File → Options dialog."""

    def __init__(self, parent):
        super().__init__(title="Opcje BTerminal", transient_for=parent, modal=True)
        self.set_default_size(420, -1)
        self.set_border_width(0)
        self._app = parent

        content = self.get_content_area()
        grid = Gtk.Grid(column_spacing=16, row_spacing=14)
        grid.set_border_width(20)

        row = 0

        # ── Wygląd ────────────────────────────────────────────────────────────
        section = Gtk.Label()
        section.set_markup("<b>Wygląd</b>")
        section.set_halign(Gtk.Align.START)
        grid.attach(section, 0, row, 2, 1)
        row += 1

        grid.attach(Gtk.Label(label="Motyw:", halign=Gtk.Align.END), 0, row, 1, 1)
        self._theme_combo = Gtk.ComboBoxText()
        self._theme_combo.append("dark", "Ciemny (Mocha)")
        self._theme_combo.append("light", "Jasny (Latte)")
        self._theme_combo.set_active_id(_OPTIONS.get("theme", "dark"))
        grid.attach(self._theme_combo, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Label(label="Font terminala:", halign=Gtk.Align.END), 0, row, 1, 1)
        self._font_btn = Gtk.FontButton(font=_OPTIONS.get("font", "Monospace 11"))
        self._font_btn.set_use_font(True)
        self._font_btn.set_hexpand(True)
        grid.attach(self._font_btn, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 0, row, 2, 1)
        row += 1

        # ── Terminal ──────────────────────────────────────────────────────────
        section2 = Gtk.Label()
        section2.set_markup("<b>Terminal</b>")
        section2.set_halign(Gtk.Align.START)
        grid.attach(section2, 0, row, 2, 1)
        row += 1

        grid.attach(Gtk.Label(label="Domyślny shell:", halign=Gtk.Align.END), 0, row, 1, 1)
        self._shell_entry = Gtk.Entry(hexpand=True)
        self._shell_entry.set_placeholder_text(f"domyślny ({os.environ.get('SHELL', '/bin/bash')})")
        self._shell_entry.set_text(_OPTIONS.get("shell", ""))
        grid.attach(self._shell_entry, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 0, row, 2, 1)
        row += 1

        # ── Ogólne ────────────────────────────────────────────────────────────
        section3 = Gtk.Label()
        section3.set_markup("<b>Ogólne</b>")
        section3.set_halign(Gtk.Align.START)
        grid.attach(section3, 0, row, 2, 1)
        row += 1

        grid.attach(
            Gtk.Label(label="Sprawdzaj aktualizacje przy starcie:", halign=Gtk.Align.END),
            0, row, 1, 1,
        )
        self._updates_switch = Gtk.Switch()
        self._updates_switch.set_active(_OPTIONS.get("check_updates_on_start", True))
        self._updates_switch.set_halign(Gtk.Align.START)
        grid.attach(self._updates_switch, 1, row, 1, 1)
        row += 1

        content.pack_start(grid, True, True, 0)
        content.show_all()

        self.add_button("Anuluj", Gtk.ResponseType.CANCEL)
        btn_ok = self.add_button("Zapisz", Gtk.ResponseType.OK)
        btn_ok.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)

    def run_and_apply(self):
        if self.run() != Gtk.ResponseType.OK:
            self.destroy()
            return

        new_theme = self._theme_combo.get_active_id()
        new_font = self._font_btn.get_font()
        new_shell = self._shell_entry.get_text().strip()
        new_updates = self._updates_switch.get_active()

        _OPTIONS["theme"] = new_theme
        _OPTIONS["font"] = new_font
        _OPTIONS["shell"] = new_shell
        _OPTIONS["check_updates_on_start"] = new_updates
        _save_options(_OPTIONS)

        global FONT
        FONT = new_font

        # Apply theme if changed
        if new_theme != _current_theme:
            self._app._toggle_theme()

        # Apply font to all open terminals
        self._app._apply_font(new_font)

        self.destroy()


class BTerminalApp(Gtk.Window):
    """Główne okno aplikacji BTerminal."""

    def __init__(self):
        super().__init__(title=APP_NAME)
        self.set_default_size(1200, 700)
        self.set_icon_name("bterminal")

        # Apply CSS
        self._css_provider = Gtk.CssProvider()
        self._css_provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            self._css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Theme from options
        self._gtk_settings = Gtk.Settings.get_default()
        self._gtk_settings.set_property(
            "gtk-application-prefer-dark-theme", _current_theme == "dark"
        )

        # Session managers
        self.session_manager = SessionManager()
        self.claude_manager = ClaudeSessionManager()

        # Layout: VBox → menubar + HPaned
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root_box)

        root_box.pack_start(self._build_menubar(), False, False, 0)

        paned = Gtk.HPaned()
        root_box.pack_start(paned, True, True, 0)

        # Sidebar container with stack switcher
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.get_style_context().add_class("sidebar")
        sidebar_box.set_size_request(0, -1)

        self.sidebar_stack = Gtk.Stack()
        self.sidebar_stack.set_transition_type(
            Gtk.StackTransitionType.SLIDE_LEFT_RIGHT
        )

        self.sidebar = SessionSidebar(self)
        self.sidebar_stack.add_titled(self.sidebar, "sessions", "Sessions")

        self.ctx_panel = CtxManagerPanel(self)
        self.sidebar_stack.add_titled(self.ctx_panel, "ctx", "Ctx")

        self.consult_panel = ConsultPanel(self)
        self.sidebar_stack.add_titled(self.consult_panel, "consult", "Consult")

        self.task_panel = TaskListPanel(self)
        self.sidebar_stack.add_titled(self.task_panel, "tasks", "Tasks")

        self.memory_panel = MemoryPanel(self)
        self.sidebar_stack.add_titled(self.memory_panel, "memory", "Memory")

        self.skills_panel = SkillsPanel(self)
        self.sidebar_stack.add_titled(self.skills_panel, "skills", "Skills")

        self.files_panel = FilesPanel(self)
        self.sidebar_stack.add_titled(self.files_panel, "files", "Files")

        self.plugin_panel = PluginManagerPanel(self)
        self.sidebar_stack.add_titled(self.plugin_panel, "plugins", "Plugins")

        # Two-row compact tab switcher: row1 = main tabs, row2 = extra + toggle
        switcher = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        switcher.get_style_context().add_class("sidebar-switcher")

        row1 = Gtk.Box(spacing=0)
        row2 = Gtk.Box(spacing=0)

        _tab_defs_row1 = [("sessions", "Sessions"), ("ctx", "Ctx"),
                          ("consult", "Consult"), ("tasks", "Tasks")]
        _tab_defs_row2 = [("memory", "Memory"), ("skills", "Skills"), ("files", "Files"), ("plugins", "Plugins")]

        self._sidebar_tab_buttons = []
        for name, title in _tab_defs_row1:
            btn = Gtk.Button(label=title)
            btn.get_style_context().add_class("sidebar-tab")
            child = btn.get_child()
            if isinstance(child, Gtk.Label):
                child.set_ellipsize(Pango.EllipsizeMode.END)
            btn.connect("clicked", lambda _, n=name: self.sidebar_stack.set_visible_child_name(n))
            row1.pack_start(btn, True, True, 0)
            self._sidebar_tab_buttons.append(btn)

        for name, title in _tab_defs_row2:
            btn = Gtk.Button(label=title)
            btn.get_style_context().add_class("sidebar-tab")
            child = btn.get_child()
            if isinstance(child, Gtk.Label):
                child.set_ellipsize(Pango.EllipsizeMode.END)
            btn.connect("clicked", lambda _, n=name: self.sidebar_stack.set_visible_child_name(n))
            row2.pack_start(btn, True, True, 0)
            self._sidebar_tab_buttons.append(btn)

        # Toggle button at end of row2
        self._sidebar_toggle_btn = Gtk.Button(label="◀")
        self._sidebar_toggle_btn.get_style_context().add_class("sidebar-tab")
        self._sidebar_toggle_btn.set_tooltip_text("Hide sidebar (Ctrl+B)")
        self._sidebar_toggle_btn.connect("clicked", lambda _: self.toggle_sidebar())
        row2.pack_end(self._sidebar_toggle_btn, False, False, 0)

        switcher.pack_start(row1, False, False, 0)
        switcher.pack_start(row2, False, False, 0)
        self._sidebar_switcher = switcher
        self._sidebar_tab_names = (
            [n for n, _ in _tab_defs_row1] + [n for n, _ in _tab_defs_row2]
        )

        self.sidebar_stack.connect("notify::visible-child-name", self._on_sidebar_tab_changed)
        # Delay so this fires after all panel show_all() and idle callbacks.
        def _set_default_sidebar_tab():
            self.sidebar_stack.set_visible_child_name("sessions")
            self._on_sidebar_tab_changed(None, None)
            return False
        GLib.idle_add(_set_default_sidebar_tab, priority=GLib.PRIORITY_LOW)

        sidebar_box.pack_start(switcher, False, False, 0)
        sidebar_box.pack_start(self.sidebar_stack, True, True, 0)

        # Make all sidebar widgets genuinely shrinkable via ellipsize
        # (ellipsize reduces a label's true minimum width to ~"..." width)
        def _make_shrinkable(widget):
            if isinstance(widget, Gtk.Label):
                widget.set_ellipsize(Pango.EllipsizeMode.END)
            if isinstance(widget, (Gtk.Entry, Gtk.SpinButton)):
                widget.set_width_chars(1)
            if isinstance(widget, Gtk.ComboBoxText):
                widget.set_size_request(0, -1)
            if isinstance(widget, Gtk.TreeView):
                for col in widget.get_columns():
                    col.set_min_width(0)
                    col.set_max_width(-1)
                    col.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
            if isinstance(widget, Gtk.ScrolledWindow):
                widget.set_propagate_natural_width(False)
            if isinstance(widget, Gtk.Container):
                widget.forall(_make_shrinkable)
        # Process ALL stack children explicitly (forall may skip invisible pages)
        for panel in [self.sidebar, self.ctx_panel, self.consult_panel, self.task_panel, self.plugin_panel]:
            _make_shrinkable(panel)
        _make_shrinkable(switcher)

        self._sidebar_wrap = ShrinkableBin()
        self._sidebar_wrap.add(sidebar_box)
        self._paned = paned
        self._sidebar_visible = True
        self._sidebar_last_pos = 250
        paned.pack1(self._sidebar_wrap, resize=False, shrink=False)

        # Inner paned: notebook + git panel
        inner_paned = Gtk.HPaned()
        self._inner_paned = inner_paned

        # Notebook (tabs)
        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.set_show_border(False)
        self.notebook.popup_disable()
        inner_paned.pack1(self.notebook, resize=True, shrink=False)

        # Git panel (right side)
        self.git_panel = GitPanel(self)
        _make_shrinkable(self.git_panel)
        self._git_wrap = ShrinkableBin()
        self._git_wrap.add(self.git_panel)
        self._git_visible = False
        self._git_last_pos = 300
        inner_paned.pack2(self._git_wrap, resize=False, shrink=False)

        paned.pack2(inner_paned, resize=True, shrink=False)

        # Show-sidebar button (visible only when sidebar is hidden)
        self._show_sidebar_btn = Gtk.Button(label="▶")
        self._show_sidebar_btn.get_style_context().add_class("sidebar-btn")
        self._show_sidebar_btn.set_tooltip_text("Show sidebar (Ctrl+B)")
        self._show_sidebar_btn.set_no_show_all(True)
        self._show_sidebar_btn.connect("clicked", lambda _: self.toggle_sidebar())
        self.notebook.set_action_widget(self._show_sidebar_btn, Gtk.PackType.START)

        # Right action area: theme toggle + git button
        end_box = Gtk.Box(spacing=4)

        self._theme_btn = Gtk.Button(label="☀" if _current_theme == "dark" else "☾")
        self._theme_btn.get_style_context().add_class("theme-toggle")
        self._theme_btn.set_tooltip_text("Toggle light/dark theme")
        self._theme_btn.connect("clicked", lambda _: self._toggle_theme())
        end_box.pack_start(self._theme_btn, False, False, 0)

        self._show_git_btn = Gtk.Button(label="Git ◀")
        self._show_git_btn.get_style_context().add_class("sidebar-btn")
        self._show_git_btn.set_tooltip_text("Show Git panel (Ctrl+G)")
        self._show_git_btn.set_no_show_all(True)
        self._show_git_btn.connect("clicked", lambda _: self.toggle_git_panel())
        end_box.pack_start(self._show_git_btn, False, False, 0)

        end_box.show_all()
        self.notebook.set_action_widget(end_box, Gtk.PackType.END)

        paned.set_position(250)

        # Git panel starts fully hidden (no Claude tab active yet)
        self._git_wrap.set_no_show_all(True)
        self._git_wrap.hide()

        # Auto-refresh panels when switching to them
        def _on_sidebar_switch(stack, _param):
            child = stack.get_visible_child()
            if child is self.ctx_panel:
                self.ctx_panel.refresh()
            elif child is self.consult_panel:
                self.consult_panel.refresh()
            elif child is self.task_panel:
                self.task_panel.refresh()
            elif child is self.skills_panel:
                self.skills_panel._refresh()
            elif child is self.files_panel:
                self.files_panel._refresh()
            elif child is self.plugin_panel:
                self.plugin_panel.refresh()

        self.sidebar_stack.connect("notify::visible-child", _on_sidebar_switch)

        # Keyboard shortcuts
        self.connect("key-press-event", self._on_key_press)
        self.connect("delete-event", self._on_delete_event)
        self.notebook.connect("switch-page", self._on_switch_page)

        # Open initial local shell
        self.add_local_tab()

        self.show_all()

        self._plugins = {}
        self._plugin_shortcuts = []
        self._load_plugins()
        self.plugin_panel.refresh()

    def _build_menubar(self):
        menubar = Gtk.MenuBar()

        def _item(label, callback, shortcut=None):
            it = Gtk.MenuItem(label=label)
            if shortcut:
                it.set_accel_path(shortcut)
            it.connect("activate", lambda _: callback())
            return it

        def _sep():
            return Gtk.SeparatorMenuItem()

        # ── File ──────────────────────────────────────────────────────────────
        file_menu = Gtk.Menu()
        file_menu.append(_item("Nowa karta lokalna", self.add_local_tab))
        file_menu.append(_item("Nowa sesja SSH…", lambda: self.sidebar._on_add(None)))
        file_menu.append(_item("Nowa sesja Claude Code…", lambda: self.sidebar._on_add_claude()))
        file_menu.append(_sep())
        file_menu.append(_item("Opcje…", lambda: OptionsDialog(self).run_and_apply()))
        file_menu.append(_sep())
        file_menu.append(_item("Zamknij aplikację", self.destroy))
        file_root = Gtk.MenuItem(label="File")
        file_root.set_submenu(file_menu)
        menubar.append(file_root)

        # ── View ──────────────────────────────────────────────────────────────
        view_menu = Gtk.Menu()
        view_menu.append(_item("Przełącz sidebar (Ctrl+B)", self.toggle_sidebar))
        view_menu.append(_item("Przełącz panel Git (Ctrl+G)", self.toggle_git_panel))
        view_menu.append(_item("Przełącz motyw ☀/🌙", self._toggle_theme))
        view_menu.append(_sep())
        for panel_name, panel_title in [
            ("sessions", "Sessions"),
            ("ctx",      "Ctx"),
            ("consult",  "Consult"),
            ("tasks",    "Tasks"),
            ("plugins",  "Plugins"),
        ]:
            it = Gtk.MenuItem(label=panel_title)
            it.connect("activate", lambda _, n=panel_name: (
                self.sidebar_stack.set_visible_child_name(n),
                self._sidebar_visible or self.toggle_sidebar(),
            ))
            view_menu.append(it)
        view_root = Gtk.MenuItem(label="View")
        view_root.set_submenu(view_menu)
        menubar.append(view_root)

        # ── Tools ─────────────────────────────────────────────────────────────
        tools_menu = Gtk.Menu()
        tools_menu.append(_item("Sprawdź aktualizacje", lambda: _check_for_updates(self, manual=True)))
        tools_menu.append(_item("Errata…", lambda: _show_errata_dialog(self, _load_local_errata())))
        tools_root = Gtk.MenuItem(label="Tools")
        tools_root.set_submenu(tools_menu)
        menubar.append(tools_root)

        menubar.show_all()
        return menubar

    def _apply_font(self, font_str):
        desc = Pango.FontDescription(font_str)
        for i in range(self.notebook.get_n_pages()):
            tab = self.notebook.get_nth_page(i)
            if isinstance(tab, TerminalTab):
                tab.terminal.set_font(desc)

    def _update_window_title(self):
        """Update window title bar: 'BTerminal — tab_name [n/total]'."""
        n = self.notebook.get_n_pages()
        idx = self.notebook.get_current_page()
        if idx < 0 or n == 0:
            self.set_title(f"{APP_NAME} v{APP_VERSION}")
            return
        tab = self.notebook.get_nth_page(idx)
        if isinstance(tab, TerminalTab):
            name = tab.get_label()
        else:
            name = "Terminal"
        if n > 1:
            self.set_title(f"{APP_NAME} — {name} [{idx + 1}/{n}]")
        else:
            self.set_title(f"{APP_NAME} — {name}")

    def _on_switch_page(self, notebook, page, page_num):
        GLib.idle_add(self._update_window_title)
        # Auto-select project in Task panel based on active Claude Code tab
        if isinstance(page, TerminalTab) and page._task_project:
            GLib.idle_add(self._sync_task_panel_project, page._task_project)
        # Git panel: show only for Claude Code tabs
        is_claude = isinstance(page, TerminalTab) and page.claude_config is not None
        if is_claude:
            self._show_git_btn.show()
            if self._git_visible:
                GLib.idle_add(self._sync_git_panel)
        else:
            if self._git_visible:
                self.toggle_git_panel()
            self._show_git_btn.hide()

    def _sync_task_panel_project(self, project_name):
        """Set Task panel's project combo to match the active tab's project."""
        if not hasattr(self, "task_panel"):
            return
        combo = self.task_panel.project_combo
        model = combo.get_model()
        if not model:
            return
        for i, row in enumerate(model):
            if row[0] == project_name:
                combo.set_active(i)
                break

    def _build_tab_label(self, text, tab_widget):
        """Build a tab label with a close button.

        Stores label reference on tab_widget._tab_label for efficient updates.
        """
        box = Gtk.Box(spacing=4)

        label = Gtk.Label(label=text)
        box.pack_start(label, True, True, 0)

        close_btn = Gtk.Button(label="×")
        close_btn.get_style_context().add_class("tab-close-btn")
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.connect("clicked", lambda _: self.close_tab(tab_widget))
        box.pack_start(close_btn, False, False, 0)

        box.show_all()
        tab_widget._tab_label = label
        return box

    def add_local_tab(self):
        tab = TerminalTab(self)
        label = self._build_tab_label("Terminal", tab)
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        self._update_window_title()

    def open_wizard_tab(self, project: str, cmd: list, on_done=None):
        """Open a terminal tab running memory_wizard; calls on_done when it exits."""
        # Resolve wizard binary — check install dir first (GUI may have empty PATH)
        wizard_bin = (
            shutil.which("memory_wizard")
            or str(Path.home() / ".local" / "share" / "bterminal" / "memory_wizard")
            or str(Path.home() / ".local" / "bin" / "memory_wizard")
        )
        if not os.path.isfile(wizard_bin):
            show_error_dialog(self, "memory_wizard not found. Run install.sh first.")
            return

        tab = TerminalTab(self)
        label = self._build_tab_label(f"🧙 {project}", tab)
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        self._update_window_title()

        argv = [wizard_bin] + cmd[1:]  # cmd[0] is "memory_wizard"
        tab.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.environ.get("HOME", "/"),
            argv,
            None,
            GLib.SpawnFlags.DEFAULT,
            None, None, -1, None, None,
        )

        if on_done:
            def _on_exit(terminal, status):
                if on_done:
                    GLib.idle_add(on_done)
            tab.terminal.connect("child-exited", _on_exit)

    def open_ssh_tab(self, session):
        tab = TerminalTab(self, session=session)
        name = session.get("name", "SSH")
        label = self._build_tab_label(name, tab)
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        self._update_window_title()

    def open_ssh_tab_with_macro(self, session, macro):
        tab = TerminalTab(self, session=session)
        name = f"{session.get('name', 'SSH')} \u2014 {macro.get('name', 'Macro')}"
        label = self._build_tab_label(name, tab)
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        tab.run_macro(macro)
        self._update_window_title()

    _TAB_EMOJIS = [
        "🦊", "🐙", "🎯", "🚀", "⚡", "🔮", "🎲", "🌀", "🦋", "🐺",
        "🎸", "🌊", "🔥", "💎", "🦅", "🐍", "🎪", "🌵", "🦈", "🍄",
        "🎭", "🏴\u200d☠️", "🛸", "🧊", "🦎", "🐝", "🌻", "🎱", "🦜", "🐲",
    ]

    def open_claude_tab(self, config):
        tab = TerminalTab(self, claude_config=config)
        base_name = config.get("name", "Claude Code")
        # Count existing tabs with the same base config name
        count = 0
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            if isinstance(page, TerminalTab) and page.claude_config:
                if page.claude_config.get("name") == config.get("name"):
                    count += 1
        # Pick a random emoji not already used by sibling tabs
        used = set()
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            if isinstance(page, TerminalTab) and hasattr(page, "_claude_tab_emoji"):
                used.add(page._claude_tab_emoji)
        available = [e for e in self._TAB_EMOJIS if e not in used] or self._TAB_EMOJIS
        emoji = random.choice(available)
        tab._claude_tab_emoji = emoji
        tab_name = f"{base_name} #{count + 1} {emoji}"
        tab._claude_tab_display = tab_name
        label = self._build_tab_label(tab_name, tab)
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        self._update_window_title()

    def close_tab(self, tab):
        # Release task claims for this tab's session
        if getattr(tab, "_task_project", None) and getattr(tab, "_task_session_id", None):
            try:
                db = sqlite3.connect(CTX_DB)
                db.execute(
                    "DELETE FROM task_claims WHERE project = ? AND session_id = ?",
                    (tab._task_project, tab._task_session_id),
                )
                db.commit()
                db.close()
            except Exception:
                pass
        # Collect Claude Code session log on tab close
        _collect_claude_log(tab)
        idx = self.notebook.page_num(tab)
        if idx >= 0:
            self.notebook.remove_page(idx)
            tab.destroy()
        # No auto-open — user picks a session from the sidebar
        self._update_window_title()

    def on_tab_child_exited(self, tab):
        """Called when a terminal's child process exits.

        Starts a 30-second auto-close timer instead of closing immediately,
        so the user can read final output. Any keypress cancels the timer.
        """
        def _auto_close():
            tab._dead_timer_id = None
            self.close_tab(tab)
            return False

        def _cancel_timer(terminal, event):
            timer_id = getattr(tab, "_dead_timer_id", None)
            if timer_id:
                GLib.source_remove(timer_id)
                tab._dead_timer_id = None
            tab._dead_key_handler = None
            return False

        tab._dead_timer_id = GLib.timeout_add_seconds(30, _auto_close)
        tab._dead_key_handler = tab.terminal.connect("key-press-event", _cancel_timer)

    def update_tab_title(self, tab, title):
        """Update tab label when terminal title changes."""
        idx = self.notebook.page_num(tab)
        if idx >= 0:
            label = getattr(tab, "_tab_label", None)
            if label:
                label.set_text(title)
            else:
                label_widget = self._build_tab_label(title, tab)
                self.notebook.set_tab_label(tab, label_widget)
            self._update_window_title()

    def _get_current_terminal(self):
        idx = self.notebook.get_current_page()
        if idx < 0:
            return None
        tab = self.notebook.get_nth_page(idx)
        if isinstance(tab, TerminalTab):
            return tab.terminal
        return None

    def toggle_sidebar(self):
        """Show/hide the sidebar panel."""
        if self._sidebar_visible:
            self._sidebar_last_pos = self._paned.get_position()
            self._sidebar_wrap.hide()
            self._paned.set_position(0)
            self._show_sidebar_btn.show()
        else:
            self._sidebar_wrap.show()
            self._paned.set_position(self._sidebar_last_pos)
            self._show_sidebar_btn.hide()
        self._sidebar_visible = not self._sidebar_visible

    def _toggle_theme(self):
        """Switch between Catppuccin Mocha (dark) and Latte (light)."""
        global _current_theme, CSS
        if _current_theme == "dark":
            _current_theme = "light"
            CATPPUCCIN.update(CATPPUCCIN_LATTE)
            TERMINAL_PALETTE[:] = TERMINAL_PALETTE_LATTE
            self._gtk_settings.set_property("gtk-application-prefer-dark-theme", False)
            self._theme_btn.set_label("☾")
        else:
            _current_theme = "dark"
            CATPPUCCIN.update(CATPPUCCIN_MOCHA)
            TERMINAL_PALETTE[:] = TERMINAL_PALETTE_MOCHA
            self._gtk_settings.set_property("gtk-application-prefer-dark-theme", True)
            self._theme_btn.set_label("☀")
        _OPTIONS["theme"] = _current_theme
        _save_options(_OPTIONS)
        # Reload CSS
        CSS = _build_css(CATPPUCCIN)
        self._css_provider.load_from_data(CSS.encode())
        # Re-color all open terminals
        fg = _parse_color(CATPPUCCIN["text"])
        bg = _parse_color(CATPPUCCIN["base"])
        palette = [_parse_color(c) for c in TERMINAL_PALETTE]
        cursor = _parse_color(CATPPUCCIN["rosewater"])
        cursor_fg = _parse_color(CATPPUCCIN["crust"])
        for i in range(self.notebook.get_n_pages()):
            tab = self.notebook.get_nth_page(i)
            if isinstance(tab, TerminalTab):
                tab.terminal.set_colors(fg, bg, palette)
                tab.terminal.set_color_cursor(cursor)
                tab.terminal.set_color_cursor_foreground(cursor_fg)
                # Reset terminal colors for already-rendered content
                tab.terminal.feed(b"\x1b[0m")
        # Refresh sidebar (session colors change per theme)
        self.sidebar.refresh()
        # Refresh git panel if visible
        if self._git_visible:
            self.git_panel.refresh()

    def toggle_git_panel(self):
        """Show/hide the right-side Git panel (mirror of toggle_sidebar)."""
        if self._git_visible:
            # Save current panel width before hiding
            alloc = self._inner_paned.get_allocation()
            pos = self._inner_paned.get_position()
            self._git_last_pos = alloc.width - pos
            self._git_wrap.hide()
            self.git_panel.hide()
            # Push divider to far right so notebook takes full width
            self._inner_paned.set_position(alloc.width)
            self._show_git_btn.show()
            self._git_visible = False
        else:
            self._git_wrap.show()
            self.git_panel.show()
            self.git_panel.show_all()
            self._show_git_btn.hide()
            self._git_visible = True
            # Sync and position after GTK processes the show
            self._sync_git_panel()
            self.git_panel.refresh()
            GLib.idle_add(self._apply_git_panel_position)

    def _apply_git_panel_position(self):
        """Set inner paned position after GTK layout cycle."""
        alloc = self._inner_paned.get_allocation()
        width = self._git_last_pos if self._git_last_pos > 50 else 320
        if alloc.width > 0:
            self._inner_paned.set_position(alloc.width - width)
        return False

    def _sync_git_panel(self):
        """Update git panel to match the current tab's project directory."""
        idx = self.notebook.get_current_page()
        if idx < 0:
            self.git_panel.set_project_dir(None)
            return
        tab = self.notebook.get_nth_page(idx)
        if isinstance(tab, TerminalTab) and tab.claude_config:
            proj_dir = tab.claude_config.get("project_dir", "")
            self.git_panel.set_project_dir(proj_dir)
        else:
            # For local/SSH tabs, try CWD
            self.git_panel.set_project_dir(os.getcwd())

    def _on_sidebar_tab_changed(self, _stack, _param):
        """Update active tab styling in custom sidebar switcher."""
        active_name = self.sidebar_stack.get_visible_child_name()
        tab_names = getattr(self, "_sidebar_tab_names", []) + [
            p.name for p in self._plugins.values()
        ] if hasattr(self, '_plugins') else getattr(self, "_sidebar_tab_names", [])
        for btn, name in zip(self._sidebar_tab_buttons, tab_names):
            ctx = btn.get_style_context()
            if name == active_name:
                ctx.add_class("sidebar-tab-active")
            else:
                ctx.remove_class("sidebar-tab-active")

    def _load_plugins(self):
        """Scan PLUGINS_DIR and load all plugins."""
        if not os.path.isdir(PLUGINS_DIR):
            return
        plugin_config = {}
        if os.path.isfile(PLUGINS_CONFIG_FILE):
            try:
                with open(PLUGINS_CONFIG_FILE, "r") as f:
                    plugin_config = json.load(f)
            except Exception:
                pass
        for entry in sorted(os.listdir(PLUGINS_DIR)):
            path = os.path.join(PLUGINS_DIR, entry)
            # Accept .py files or packages with __init__.py
            if os.path.isfile(path) and entry.endswith(".py"):
                mod_name = entry[:-3]
            elif os.path.isdir(path) and os.path.isfile(os.path.join(path, "__init__.py")):
                mod_name = entry
            else:
                continue
            # Skip disabled plugins
            if not plugin_config.get(mod_name, True):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"bterminal_plugin_{mod_name}",
                    path if path.endswith(".py") else os.path.join(path, "__init__.py"),
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                plugin = module.create_plugin(self)
                self._register_plugin(plugin)
            except Exception as e:
                print(f"[plugins] Failed to load {entry}: {e}")

    def _register_plugin(self, plugin):
        """Register a plugin: activate, add sidebar panel and switcher button."""
        panel = plugin.activate(self)
        self._plugins[plugin.name] = plugin

        if panel is not None:
            self.sidebar_stack.add_titled(panel, plugin.name, plugin.title)

            # Make shrinkable (reuse the same logic as built-in panels)
            def _make_shrinkable(widget):
                if isinstance(widget, Gtk.Label):
                    widget.set_ellipsize(Pango.EllipsizeMode.END)
                if isinstance(widget, (Gtk.Entry, Gtk.SpinButton)):
                    widget.set_width_chars(1)
                if isinstance(widget, Gtk.ComboBoxText):
                    widget.set_size_request(0, -1)
                if isinstance(widget, Gtk.TreeView):
                    for col in widget.get_columns():
                        col.set_min_width(0)
                        col.set_max_width(-1)
                        col.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
                if isinstance(widget, Gtk.ScrolledWindow):
                    widget.set_propagate_natural_width(False)
                if isinstance(widget, Gtk.Container):
                    widget.forall(_make_shrinkable)
            _make_shrinkable(panel)

            # Add switcher button
            btn = Gtk.Button(label=plugin.title)
            btn.get_style_context().add_class("sidebar-tab")
            child = btn.get_child()
            if isinstance(child, Gtk.Label):
                child.set_ellipsize(Pango.EllipsizeMode.END)
            btn.connect("clicked", lambda _, n=plugin.name: self.sidebar_stack.set_visible_child_name(n))
            self._sidebar_switcher.pack_start(btn, True, True, 0)
            # Keep toggle button last
            self._sidebar_switcher.reorder_child(self._sidebar_toggle_btn, -1)
            self._sidebar_tab_buttons.append(btn)

            btn.show_all()
            panel.show_all()

        # Register keyboard shortcuts
        for shortcut in plugin.get_keyboard_shortcuts():
            mod, keyval, callback = shortcut
            self._plugin_shortcuts.append((mod, keyval, callback))

    def _unload_plugins(self):
        """Deactivate all loaded plugins."""
        for plugin in self._plugins.values():
            try:
                plugin.deactivate()
            except Exception as e:
                print(f"[plugins] Failed to deactivate {plugin.name}: {e}")
        self._plugins.clear()
        self._plugin_shortcuts.clear()

    def _on_key_press(self, widget, event):
        mod = event.state & Gtk.accelerator_get_default_mod_mask()
        ctrl = Gdk.ModifierType.CONTROL_MASK
        shift = Gdk.ModifierType.SHIFT_MASK

        # Ctrl+B: toggle sidebar
        if mod == ctrl and event.keyval == Gdk.KEY_b:
            self.toggle_sidebar()
            return True

        # Ctrl+G: toggle git panel
        if mod == ctrl and event.keyval == Gdk.KEY_g:
            self.toggle_git_panel()
            return True

        # Ctrl+T: new local tab
        if mod == ctrl and event.keyval == Gdk.KEY_t:
            self.add_local_tab()
            return True

        # Ctrl+Shift+W: close current tab
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_W, Gdk.KEY_w):
            idx = self.notebook.get_current_page()
            if idx >= 0:
                tab = self.notebook.get_nth_page(idx)
                self.close_tab(tab)
            return True

        # Ctrl+Shift+C: copy
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_C, Gdk.KEY_c):
            term = self._get_current_terminal()
            if term:
                term.copy_clipboard_format(Vte.Format.TEXT)
            return True

        # Ctrl+Shift+V: paste (delegate to tab for image handling)
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_V, Gdk.KEY_v):
            idx = self.notebook.get_current_page()
            if idx >= 0:
                tab = self.notebook.get_nth_page(idx)
                if isinstance(tab, TerminalTab):
                    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                    if clipboard.wait_is_image_available():
                        if tab._paste_clipboard_image_path():
                            return True
                    tab.terminal.paste_clipboard()
            return True

        # Ctrl+Tab: next tab (wrap around)
        if mod == ctrl and event.keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            n = self.notebook.get_n_pages()
            if n > 1:
                idx = self.notebook.get_current_page()
                if event.state & shift:
                    self.notebook.set_current_page((idx - 1) % n)
                else:
                    self.notebook.set_current_page((idx + 1) % n)
            return True

        # Ctrl+PageUp: previous tab
        if mod == ctrl and event.keyval == Gdk.KEY_Page_Up:
            idx = self.notebook.get_current_page()
            if idx > 0:
                self.notebook.set_current_page(idx - 1)
            return True

        # Ctrl+PageDown: next tab
        if mod == ctrl and event.keyval == Gdk.KEY_Page_Down:
            idx = self.notebook.get_current_page()
            if idx < self.notebook.get_n_pages() - 1:
                self.notebook.set_current_page(idx + 1)
            return True

        # Plugin keyboard shortcuts
        for p_mod, p_keyval, p_callback in self._plugin_shortcuts:
            if mod == p_mod and event.keyval == p_keyval:
                p_callback()
                return True

        return False

    def _on_delete_event(self, widget, event):
        self._unload_plugins()
        return False


# ─── Main ─────────────────────────────────────────────────────────────────────

def _load_local_errata():
    """Load errata.json from the local repo directory."""
    if not REPO_DIR:
        return []
    path = os.path.join(REPO_DIR, "errata.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


_UPDATE_TIMEOUT = 15


def _check_for_updates(window, manual=False):
    """Check for updates. Manual mode shows a live progress dialog with countdown."""
    if not REPO_DIR or not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        if manual:
            dlg = Gtk.MessageDialog(
                transient_for=window, modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Brak repozytorium",
            )
            dlg.format_secondary_text(
                "Nie można sprawdzić aktualizacji — katalog repozytorium nie został znaleziony."
            )
            dlg.run()
            dlg.destroy()
        return

    if manual:
        _manual_update_check(window)
    else:
        def _bg():
            try:
                subprocess.run(
                    ["git", "fetch", "origin", "master"],
                    cwd=REPO_DIR, capture_output=True, timeout=_UPDATE_TIMEOUT,
                )
                local = subprocess.run(
                    ["git", "rev-parse", "master"],
                    cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                remote = subprocess.run(
                    ["git", "rev-parse", "origin/master"],
                    cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                if local and remote and local != remote:
                    log = subprocess.run(
                        ["git", "log", "--oneline", f"{local}..{remote}"],
                        cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
                    ).stdout.strip()
                    errata_raw = subprocess.run(
                        ["git", "show", "origin/master:errata.json"],
                        cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
                    )
                    errata = []
                    if errata_raw.returncode == 0:
                        try:
                            errata = json.loads(errata_raw.stdout)
                        except Exception:
                            pass
                    GLib.idle_add(_prompt_update, window, log, errata)
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()


def _manual_update_check(window):
    """Show a live progress dialog with countdown, then display result inline."""
    dialog = Gtk.Dialog(
        title="Sprawdzanie aktualizacji",
        transient_for=window,
        modal=True,
    )
    dialog.set_default_size(400, -1)

    content = dialog.get_content_area()
    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    vbox.set_border_width(20)

    spinner = Gtk.Spinner()
    spinner.start()
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    row.pack_start(spinner, False, False, 0)
    status_lbl = Gtk.Label(label=f"Łączenie z serwerem... ({_UPDATE_TIMEOUT}s)")
    status_lbl.set_xalign(0)
    row.pack_start(status_lbl, True, True, 0)
    vbox.pack_start(row, False, False, 0)

    content.pack_start(vbox, True, True, 0)
    content.show_all()

    btn_close = dialog.add_button("Anuluj", Gtk.ResponseType.CANCEL)

    state = {"done": False, "remaining": _UPDATE_TIMEOUT, "result": None}

    def _countdown():
        if state["done"]:
            return False
        state["remaining"] -= 1
        if state["remaining"] <= 0:
            state["done"] = True
            spinner.stop()
            status_lbl.set_text("Nie można sprawdzić — przekroczono limit czasu.")
            btn_close.set_label("Zamknij")
            return False
        status_lbl.set_text(f"Łączenie z serwerem... ({state['remaining']}s)")
        return True

    GLib.timeout_add(1000, _countdown)

    def _finish():
        if state["done"]:
            return False
        state["done"] = True
        spinner.stop()
        res = state["result"]
        if res == "none":
            status_lbl.set_text("BTerminal jest aktualny. Brak nowych aktualizacji.")
            btn_close.set_label("Zamknij")
        elif isinstance(res, tuple) and res[0] == "updates":
            dialog.response(Gtk.ResponseType.OK)
        else:
            status_lbl.set_text("Nie można sprawdzić aktualizacji.")
            btn_close.set_label("Zamknij")
        return False

    def _fetch():
        try:
            subprocess.run(
                ["git", "fetch", "origin", "master"],
                cwd=REPO_DIR, capture_output=True, timeout=_UPDATE_TIMEOUT,
            )
            local = subprocess.run(
                ["git", "rev-parse", "master"],
                cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            remote = subprocess.run(
                ["git", "rev-parse", "origin/master"],
                cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if local and remote and local != remote:
                log = subprocess.run(
                    ["git", "log", "--oneline", f"{local}..{remote}"],
                    cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                errata_raw = subprocess.run(
                    ["git", "show", "origin/master:errata.json"],
                    cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
                )
                errata = []
                if errata_raw.returncode == 0:
                    try:
                        errata = json.loads(errata_raw.stdout)
                    except Exception:
                        pass
                state["result"] = ("updates", log, errata)
            else:
                state["result"] = "none"
        except Exception:
            state["result"] = "error"
        GLib.idle_add(_finish)

    threading.Thread(target=_fetch, daemon=True).start()

    dialog.run()
    dialog.destroy()

    res = state["result"]
    if isinstance(res, tuple) and res[0] == "updates":
        _prompt_update(window, res[1], res[2])


_RESP_ERRATA = 10
_RESP_RESTART = 11


def _prompt_update(window, log, errata=None):
    """Show update dialog on the main thread."""
    dialog = Gtk.Dialog(
        title="Nowa wersja BTerminal",
        transient_for=window,
        modal=True,
    )
    dialog.set_default_size(520, -1)
    dialog.set_border_width(0)

    content = dialog.get_content_area()
    content.set_spacing(0)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    vbox.set_border_width(20)

    title_lbl = Gtk.Label()
    title_lbl.set_markup("<b>Dostępna nowa wersja BTerminal</b>")
    title_lbl.set_halign(Gtk.Align.START)
    vbox.pack_start(title_lbl, False, False, 0)

    if errata:
        latest = errata[-1]
        admin_msg = latest.get("message", "").strip()
        if admin_msg:
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            vbox.pack_start(sep, False, False, 4)
            msg_lbl = Gtk.Label(label=admin_msg)
            msg_lbl.set_line_wrap(True)
            msg_lbl.set_xalign(0)
            msg_lbl.set_halign(Gtk.Align.FILL)
            vbox.pack_start(msg_lbl, False, False, 0)

    if log:
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        vbox.pack_start(sep2, False, False, 4)
        log_lbl = Gtk.Label()
        log_lbl.set_markup(f"<small>{GLib.markup_escape_text(log)}</small>")
        log_lbl.set_xalign(0)
        log_lbl.set_halign(Gtk.Align.START)
        log_lbl.set_selectable(True)
        vbox.pack_start(log_lbl, False, False, 0)

    content.pack_start(vbox, True, True, 0)
    content.show_all()

    dialog.add_button("Pokaż erratę", _RESP_ERRATA)
    dialog.add_button("Nie teraz", Gtk.ResponseType.CANCEL)
    btn_update = dialog.add_button("Aktualizuj i uruchom ponownie", Gtk.ResponseType.YES)
    btn_update.get_style_context().add_class("suggested-action")
    dialog.set_default_response(Gtk.ResponseType.YES)

    while True:
        response = dialog.run()
        if response == _RESP_ERRATA:
            _show_errata_dialog(window, errata or [])
            continue
        break

    dialog.destroy()
    if response == Gtk.ResponseType.YES:
        _do_update(window)
    return False


def _show_errata_dialog(window, errata):
    """Show all errata entries in a scrollable dialog."""
    dialog = Gtk.Dialog(
        title="Errata BTerminal",
        transient_for=window,
        modal=True,
    )
    dialog.set_default_size(560, 480)
    dialog.add_button("Zamknij", Gtk.ResponseType.CLOSE)

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_border_width(0)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    vbox.set_border_width(20)

    if not errata:
        empty = Gtk.Label(label="Brak wpisów errata.")
        empty.set_halign(Gtk.Align.START)
        vbox.pack_start(empty, False, False, 0)
    else:
        for entry in reversed(errata):
            date = entry.get("date", "")
            message = entry.get("message", "").strip()
            changes = entry.get("changes", [])

            entry_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

            header = Gtk.Label()
            header.set_markup(f"<b>{GLib.markup_escape_text(date)}</b>")
            header.set_halign(Gtk.Align.START)
            entry_box.pack_start(header, False, False, 0)

            if message:
                msg_lbl = Gtk.Label(label=message)
                msg_lbl.set_line_wrap(True)
                msg_lbl.set_xalign(0)
                msg_lbl.set_halign(Gtk.Align.FILL)
                entry_box.pack_start(msg_lbl, False, False, 0)

            for change in changes:
                row = Gtk.Label(label=f"• {change}")
                row.set_xalign(0)
                row.set_halign(Gtk.Align.START)
                row.set_line_wrap(True)
                entry_box.pack_start(row, False, False, 0)

            vbox.pack_start(entry_box, False, False, 0)
            vbox.pack_start(
                Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                False, False, 0,
            )

    scroll.add(vbox)
    dialog.get_content_area().pack_start(scroll, True, True, 0)
    dialog.show_all()
    dialog.run()
    dialog.destroy()


def _restart_bterminal():
    """Restart the BTerminal process in-place."""
    os.execv(sys.executable, [sys.executable] + sys.argv)


def _do_update(window):
    """Pull changes and run install.sh in a background thread."""
    spinner_dialog = Gtk.MessageDialog(
        transient_for=window,
        modal=True,
        message_type=Gtk.MessageType.INFO,
        text="Aktualizacja w toku...",
    )
    spinner_dialog.format_secondary_text("Pobieranie i instalacja nowej wersji.")
    spinner_dialog.set_deletable(False)
    spinner = Gtk.Spinner()
    spinner.start()
    spinner_dialog.get_content_area().pack_start(spinner, False, False, 10)
    spinner_dialog.show_all()

    def _run():
        try:
            result = subprocess.run(
                ["git", "pull", "origin", "master"],
                cwd=REPO_DIR, capture_output=True, text=True, timeout=30,
            )
            if result.returncode != 0:
                GLib.idle_add(_update_done, window, spinner_dialog,
                              f"git pull failed:\n{result.stderr}")
                return
            install = subprocess.run(
                ["bash", os.path.join(REPO_DIR, "install.sh"),
                 "--no-sudo"],
                capture_output=True, text=True, timeout=60,
            )
            if install.returncode != 0:
                GLib.idle_add(_update_done, window, spinner_dialog,
                              f"install.sh failed:\n{install.stderr}")
                return
            GLib.idle_add(_update_done, window, spinner_dialog, None)
        except Exception as e:
            GLib.idle_add(_update_done, window, spinner_dialog, str(e))

    threading.Thread(target=_run, daemon=True).start()


def _update_done(window, spinner_dialog, error):
    """Handle update result on the main thread."""
    spinner_dialog.destroy()
    if error:
        show_error_dialog(window, f"Update error:\n{error}")
    else:
        _restart_bterminal()
    return False


def main():
    GLib.set_prgname("bterminal")
    GLib.set_application_name("BTerminal")

    application = Gtk.Application(
        application_id="com.github.DexterFromLab.BTerminal",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )

    def on_activate(app):
        win = BTerminalApp()
        app.add_window(win)
        if _OPTIONS.get("check_updates_on_start", True):
            GLib.timeout_add(3000, lambda: _check_for_updates(win) or False)

    application.connect("activate", on_activate)
    application.run(None)


if __name__ == "__main__":
    main()
