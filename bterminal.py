#!/usr/bin/env python3
"""BTerminal — Terminal SSH z panelem sesji, w stylu MobaXterm."""

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
gi.require_version("Gdk", "3.0")

import json
import os
import subprocess
import tempfile
import threading
import urllib.error
import urllib.request
import uuid

from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, Vte

# ─── Stałe i konfiguracja ────────────────────────────────────────────────────

APP_NAME = "BTerminal"
CONFIG_DIR = os.path.expanduser("~/.config/bterminal")
SESSIONS_FILE = os.path.join(CONFIG_DIR, "sessions.json")
CLAUDE_SESSIONS_FILE = os.path.join(CONFIG_DIR, "claude_sessions.json")
CONSULT_CONFIG_FILE = os.path.join(CONFIG_DIR, "consult.json")
SSH_PATH = "/usr/bin/ssh"

def _find_claude_path():
    for p in [
        os.path.expanduser("~/.local/bin/claude"),
        "/usr/local/bin/claude",
        "/usr/bin/claude",
    ]:
        if os.path.isfile(p) and os.access(p, os.X_OK):
            return p
    import shutil
    return shutil.which("claude") or "claude"

CLAUDE_PATH = _find_claude_path()

FONT = "Monospace 11"
SCROLLBACK_LINES = 10000

# Catppuccin Mocha
CATPPUCCIN = {
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

TERMINAL_PALETTE = [
    "#45475a", "#f38ba8", "#a6e3a1", "#f9e2af",
    "#89b4fa", "#f5c2e7", "#94e2d5", "#bac2de",
    "#585b70", "#f38ba8", "#a6e3a1", "#f9e2af",
    "#89b4fa", "#f5c2e7", "#94e2d5", "#a6adc8",
]

SESSION_COLORS = [
    "#89b4fa", "#a6e3a1", "#f9e2af", "#f38ba8",
    "#f5c2e7", "#94e2d5", "#fab387", "#b4befe",
    "#74c7ec", "#cba6f7",
]

KEY_MAP = {
    "Enter": "\r",
    "Tab": "\t",
    "Escape": "\x1b",
    "Ctrl+C": "\x03",
    "Ctrl+D": "\x04",
}

CSS = f"""
window {{
    background-color: {CATPPUCCIN['base']};
}}
.sidebar {{
    background-color: {CATPPUCCIN['mantle']};
    border-right: 1px solid {CATPPUCCIN['surface0']};
}}
.sidebar-header {{
    background-color: {CATPPUCCIN['crust']};
    padding: 8px 12px;
    font-weight: bold;
    font-size: 13px;
    color: {CATPPUCCIN['blue']};
    border-bottom: 1px solid {CATPPUCCIN['surface0']};
}}
.sidebar-btn {{
    background: {CATPPUCCIN['surface0']};
    border: none;
    border-radius: 4px;
    color: {CATPPUCCIN['text']};
    padding: 4px 10px;
    min-height: 28px;
}}
.sidebar-btn:hover {{
    background: {CATPPUCCIN['surface1']};
}}
.sidebar-btn:active {{
    background: {CATPPUCCIN['surface2']};
}}
notebook header tab {{
    background: {CATPPUCCIN['mantle']};
    color: {CATPPUCCIN['subtext0']};
    border: none;
    padding: 4px 12px;
    border-radius: 6px 6px 0 0;
    margin: 0 1px;
}}
notebook header tab:checked {{
    background: {CATPPUCCIN['surface0']};
    color: {CATPPUCCIN['text']};
}}
notebook header {{
    background: {CATPPUCCIN['crust']};
}}
notebook {{
    background: {CATPPUCCIN['base']};
}}
treeview {{
    background-color: {CATPPUCCIN['mantle']};
    color: {CATPPUCCIN['text']};
}}
treeview:selected {{
    background-color: {CATPPUCCIN['surface1']};
    color: {CATPPUCCIN['text']};
}}
treeview:hover {{
    background-color: {CATPPUCCIN['surface0']};
}}
.tab-close-btn {{
    background: transparent;
    border: none;
    border-radius: 4px;
    padding: 0;
    min-width: 20px;
    min-height: 20px;
    color: {CATPPUCCIN['overlay1']};
}}
.tab-close-btn:hover {{
    background: {CATPPUCCIN['surface2']};
    color: {CATPPUCCIN['red']};
}}
stackswitcher {{
    background: {CATPPUCCIN['crust']};
    border-bottom: 1px solid {CATPPUCCIN['surface0']};
}}
stackswitcher button {{
    background: {CATPPUCCIN['crust']};
    color: {CATPPUCCIN['subtext0']};
    border: none;
    border-radius: 0;
    padding: 6px 16px;
    border-bottom: 2px solid transparent;
    font-weight: bold;
    font-size: 12px;
}}
stackswitcher button:checked {{
    background: {CATPPUCCIN['mantle']};
    color: {CATPPUCCIN['blue']};
    border-bottom: 2px solid {CATPPUCCIN['blue']};
}}
stackswitcher button:hover {{
    background: {CATPPUCCIN['surface0']};
}}
textview.ctx-detail {{
    font-family: monospace;
    font-size: 10pt;
}}
textview.ctx-detail text {{
    background-color: {CATPPUCCIN['crust']};
    color: {CATPPUCCIN['subtext1']};
}}
"""


def _parse_color(hex_str):
    """Parse hex color string to Gdk.RGBA."""
    c = Gdk.RGBA()
    c.parse(hex_str)
    return c


# ─── SessionManager ──────────────────────────────────────────────────────────


class SessionManager:
    """Zarządzanie zapisanymi sesjami SSH (CRUD + plik JSON)."""

    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.sessions = []
        self.load()

    def load(self):
        if os.path.exists(SESSIONS_FILE):
            try:
                with open(SESSIONS_FILE, "r") as f:
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
            os.replace(tmp, SESSIONS_FILE)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def add(self, session):
        session["id"] = str(uuid.uuid4())
        self.sessions.append(session)
        self.save()
        return session

    def update(self, session_id, data):
        for i, s in enumerate(self.sessions):
            if s["id"] == session_id:
                self.sessions[i].update(data)
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


# ─── ClaudeSessionManager ────────────────────────────────────────────────────


class ClaudeSessionManager:
    """Zarządzanie zapisanymi konfiguracjami Claude Code (CRUD + plik JSON)."""

    def __init__(self):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        self.sessions = []
        self.load()

    def load(self):
        if os.path.exists(CLAUDE_SESSIONS_FILE):
            try:
                with open(CLAUDE_SESSIONS_FILE, "r") as f:
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
            os.replace(tmp, CLAUDE_SESSIONS_FILE)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    def add(self, session):
        session["id"] = str(uuid.uuid4())
        self.sessions.append(session)
        self.save()
        return session

    def update(self, session_id, data):
        for i, s in enumerate(self.sessions):
            if s["id"] == session_id:
                self.sessions[i].update(data)
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

        labels = ["Name:", "Host:", "Port:", "Username:", "SSH Key:", "Folder:", "Color:"]
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
        self.entry_folder = Gtk.Entry(hexpand=True)
        self.entry_folder.set_placeholder_text("(optional) folder for grouping")

        self.color_combo = Gtk.ComboBoxText()
        for c in SESSION_COLORS:
            self.color_combo.append(c, c)
        self.color_combo.set_active(0)

        grid.attach(self.entry_name, 1, 0, 1, 1)
        grid.attach(self.entry_host, 1, 1, 1, 1)
        grid.attach(self.entry_port, 1, 2, 1, 1)
        grid.attach(self.entry_username, 1, 3, 1, 1)
        grid.attach(self.entry_key, 1, 4, 1, 1)
        grid.attach(self.entry_folder, 1, 5, 1, 1)
        grid.attach(self.color_combo, 1, 6, 1, 1)

        # Edit mode: fill fields
        if session:
            self.entry_name.set_text(session.get("name", ""))
            self.entry_host.set_text(session.get("host", ""))
            self.entry_port.set_value(int(session.get("port", 22)))
            self.entry_username.set_text(session.get("username", ""))
            self.entry_key.set_text(session.get("key_file", ""))
            self.entry_folder.set_text(session.get("folder", ""))
            color = session.get("color", SESSION_COLORS[0])
            self.color_combo.set_active_id(color)

        self.show_all()

    def get_data(self):
        return {
            "name": self.entry_name.get_text().strip(),
            "host": self.entry_host.get_text().strip(),
            "port": int(self.entry_port.get_value()),
            "username": self.entry_username.get_text().strip(),
            "key_file": self.entry_key.get_text().strip(),
            "folder": self.entry_folder.get_text().strip(),
            "color": self.color_combo.get_active_id() or SESSION_COLORS[0],
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
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=msg,
        )
        dlg.run()
        dlg.destroy()


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
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=msg,
        )
        dlg.run()
        dlg.destroy()


# ─── ClaudeCodeDialog ─────────────────────────────────────────────────────────


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

        for i, text in enumerate(["Name:", "Folder:", "Color:", "Project dir:"]):
            lbl = Gtk.Label(label=text, halign=Gtk.Align.END)
            grid.attach(lbl, 0, i, 1, 1)

        self.entry_name = Gtk.Entry(hexpand=True)
        grid.attach(self.entry_name, 1, 0, 1, 1)

        self.entry_folder = Gtk.Entry(hexpand=True)
        self.entry_folder.set_placeholder_text("(optional) folder for grouping")
        grid.attach(self.entry_folder, 1, 1, 1, 1)

        self.color_combo = Gtk.ComboBoxText()
        for c in SESSION_COLORS:
            self.color_combo.append(c, c)
        self.color_combo.set_active(0)
        grid.attach(self.color_combo, 1, 2, 1, 1)

        dir_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=4)
        self.entry_project_dir = Gtk.Entry(hexpand=True)
        self.entry_project_dir.set_placeholder_text("(optional) path to project directory")
        dir_box.pack_start(self.entry_project_dir, True, True, 0)
        btn_browse = Gtk.Button(label="Browse…")
        btn_browse.connect("clicked", self._on_browse_dir)
        dir_box.pack_start(btn_browse, False, False, 0)
        grid.attach(dir_box, 1, 3, 1, 1)

        self.lbl_ctx_status = Gtk.Label(xalign=0)
        grid.attach(self.lbl_ctx_status, 1, 4, 1, 1)

        # Separator
        box.pack_start(Gtk.Separator(), False, False, 2)

        # Sudo checkbox
        self.chk_sudo = Gtk.CheckButton(label="Run with sudo (asks for password)")
        box.pack_start(self.chk_sudo, False, False, 0)

        # Resume session checkbox
        self.chk_resume = Gtk.CheckButton(label="Resume last session (--resume)")
        self.chk_resume.set_active(True)
        box.pack_start(self.chk_resume, False, False, 0)

        # Skip permissions checkbox
        self.chk_skip_perms = Gtk.CheckButton(label="Skip permissions (--dangerously-skip-permissions)")
        self.chk_skip_perms.set_active(True)
        box.pack_start(self.chk_skip_perms, False, False, 0)

        # Initial prompt
        lbl = Gtk.Label(label="Initial prompt (optional):", halign=Gtk.Align.START)
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
            self.entry_folder.set_text(session.get("folder", ""))
            color = session.get("color", SESSION_COLORS[0])
            self.color_combo.set_active_id(color)
            self.chk_sudo.set_active(session.get("sudo", False))
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
            "folder": self.entry_folder.get_text().strip(),
            "color": self.color_combo.get_active_id() or SESSION_COLORS[0],
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
        return True

    def _show_error(self, msg):
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=msg,
        )
        dlg.run()
        dlg.destroy()

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
            # Auto-fill prompt with ctx instructions if empty or auto-generated
            buf = self.textview.get_buffer()
            start, end = buf.get_bounds()
            current = buf.get_text(start, end, False).strip()
            if not current or current.startswith("Wczytaj kontekst"):
                prompt = (
                    f"Wczytaj kontekst projektu poleceniem: ctx get {basename}\n"
                    f"Wykonaj tę komendę i zapoznaj się z kontekstem zanim zaczniesz pracę.\n"
                    f"Kontekst zarządzasz przez: ctx --help\n"
                    f"Ważne odkrycia zapisuj: ctx set {basename} <key> <value>\n"
                    f"Przed zakończeniem sesji: ctx summary {basename} \"<co zrobiliśmy>\"\n"
                    f"\n"
                    f"Konsultacje z zewnętrznymi modelami AI (OpenRouter): consult \"pytanie\"\n"
                    f"Dostępne modele i opcje: consult\n"
                    f"\n"
                    f"Dostępne narzędzie 'tasks' — lista zadań zarządzana przez użytkownika.\n"
                    f"NIE pobieraj ani nie wykonuj zadań z listy samodzielnie.\n"
                    f"Jeśli system auto-trigger wyśle Ci polecenie z listą zadań — wtedy wykonuj.\n"
                    f"Pomoc: tasks --help"
                )
                buf.set_text(prompt)
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
    import sqlite3
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
    import sqlite3
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
    import sqlite3
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


def _resolve_ctx_project_name(project_dir):
    """Resolve ctx project name from a project directory path.

    Looks up the sessions table by work_dir. Falls back to basename.
    """
    if not project_dir or not os.path.exists(CTX_DB):
        return os.path.basename(project_dir.rstrip("/")) if project_dir else None
    import sqlite3
    normalized = project_dir.rstrip("/")
    try:
        db = sqlite3.connect(CTX_DB)
        row = db.execute(
            "SELECT name FROM sessions WHERE RTRIM(work_dir, '/') = ?",
            (normalized,),
        ).fetchone()
        db.close()
        if row:
            return row[0]
    except sqlite3.Error:
        pass
    return os.path.basename(normalized)


def _is_ctx_project_registered(project_name):
    """Check if a ctx project is already registered in the database."""
    if not os.path.exists(CTX_DB):
        return False
    import sqlite3
    try:
        db = sqlite3.connect(CTX_DB)
        row = db.execute(
            "SELECT 1 FROM sessions WHERE name = ?", (project_name,)
        ).fetchone()
        db.close()
        return row is not None
    except sqlite3.Error:
        return False


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
    project_name = os.path.basename(project_dir.rstrip("/"))
    if _is_ctx_project_registered(project_name):
        return data
    wizard = CtxSetupWizard(parent, project_dir)
    if wizard.run_wizard():
        if not data["prompt"] or data["prompt"].startswith("Wczytaj kontekst"):
            data["prompt"] = wizard.result_prompt
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
        self.project_name = os.path.basename(project_dir.rstrip("/"))
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
        self.w_key.set_placeholder_text("e.g. repo, stack, architecture")
        grid.attach(self.w_key, 1, 0, 1, 1)

        grid.attach(
            Gtk.Label(label="Value:", halign=Gtk.Align.END, valign=Gtk.Align.START),
            0, 1, 1, 1,
        )
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(90)
        self.w_value = Gtk.TextView(wrap_mode=Gtk.WrapMode.WORD_CHAR)
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
                        f"On session start, load context:\n"
                        f"```bash\n"
                        f"ctx get {name}\n"
                        f"```\n\n"
                        f"Context manager: `ctx --help`\n\n"
                        f"During work:\n"
                        f"- Save important discoveries: `ctx set {name} <key> <value>`\n"
                        f"- Append to existing: `ctx append {name} <key> <value>`\n"
                        f'- Before ending session: `ctx summary {name} "<what was done>"`\n'
                        f"\n"
                        f"## External AI consultation (OpenRouter)\n\n"
                        f"Consult other models (GPT, Gemini, DeepSeek, etc.) for code review, cross-checks, or analysis:\n"
                        f"```bash\n"
                        f'consult "question"                        # ask default model\n'
                        f'consult -m model_id "question"            # ask specific model\n'
                        f'consult -f file.py "review this code"     # include file\n'
                        f"consult                                   # show available models\n"
                        f"```\n\n"
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
        self.result_prompt = (
            f"Wczytaj kontekst projektu poleceniem: ctx get {name}\n"
            f"Wykonaj t\u0119 komend\u0119 i zapoznaj si\u0119 z kontekstem zanim zaczniesz prac\u0119.\n"
            f"Kontekst zarz\u0105dzasz przez: ctx --help\n"
            f"Wa\u017cne odkrycia zapisuj: ctx set {name} <key> <value>\n"
            f'Przed zako\u0144czeniem sesji: ctx summary {name} "<co zrobili\u015bmy>"\n'
            f"\n"
            f"Konsultacje z zewn\u0119trznymi modelami AI (OpenRouter): consult \"pytanie\"\n"
            f"Dost\u0119pne modele i opcje: consult\n"
            f"\n"
            f"Dostępne narzędzie 'tasks' — ZEWNĘTRZNY CLI tool uruchamiany w Bash (NIE wbudowany TaskCreate/TaskList).\n"
            f"NIE pobieraj ani nie wykonuj zadań z listy samodzielnie.\n"
            f"Jeśli system auto-trigger wyśle Ci polecenie z listą zadań — wtedy wykonuj.\n"
            f"Po każdym wykonanym zadaniu MUSISZ oznaczyć je jako done: tasks done <project> <task_id>\n"
            f"Pomoc: tasks --help"
        )
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
        import sqlite3
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

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scrolled.add(self.terminal)
        self.pack_start(scrolled, True, True, 0)

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
        if claude_config:
            project_dir = claude_config.get("project_dir", "")
            if project_dir:
                self._task_project = _resolve_ctx_project_name(project_dir)
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
        shell = os.environ.get("SHELL", "/bin/bash")
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
        flags = []
        if config.get("resume"):
            flags.append("--resume")
        if config.get("skip_permissions"):
            flags.append("--dangerously-skip-permissions")

        prompt = config.get("prompt", "")
        # Dynamically inject task instructions if project has tasks support
        project_dir = config.get("project_dir", "")
        if prompt and project_dir and "tasks" not in prompt:
            prompt += (
                f"\n\n"
                f"Dostępne narzędzie 'tasks' — ZEWNĘTRZNY CLI tool uruchamiany w Bash (NIE wbudowany TaskCreate/TaskList).\n"
                f"NIE pobieraj ani nie wykonuj zadań z listy samodzielnie.\n"
                f"Jeśli system auto-trigger wyśle Ci polecenie z listą zadań — wtedy wykonuj.\n"
                f"Po każdym wykonanym zadaniu MUSISZ oznaczyć je jako done: tasks done <project> <task_id>\n"
                f"Pomoc: tasks --help"
            )
        prompt_arg = ""
        if prompt:
            escaped = prompt.replace("'", "'\\''")
            prompt_arg = f" '{escaped}'"

        flags_str = " ".join(flags)

        if config.get("sudo"):
            script = (
                'set -euo pipefail\n'
                'read -rsp "Podaj hasło sudo: " SUDO_PW\n'
                'echo\n'
                'ASKPASS=$(mktemp /tmp/claude-askpass.XXXXXX)\n'
                'chmod 700 "$ASKPASS"\n'
                'cat > "$ASKPASS" <<ASKEOF\n'
                '#!/bin/bash\n'
                'echo \'$SUDO_PW\'\n'
                'ASKEOF\n'
                'export SUDO_ASKPASS="$ASKPASS"\n'
                'if ! sudo -A true 2>/dev/null; then\n'
                '  rm -f "$ASKPASS"\n'
                '  echo "Błędne hasło sudo."\n'
                '  read -p "Naciśnij Enter..."\n'
                '  exit 1\n'
                'fi\n'
                'unset SUDO_PW\n'
                'trap \'rm -f "$ASKPASS"\' EXIT\n'
                f'{CLAUDE_PATH} {flags_str}{prompt_arg}\n'
                'exec bash\n'
            )
        else:
            script = f'{CLAUDE_PATH} {flags_str}{prompt_arg}\nexec bash\n'

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

        # Ctrl+Shift+V: paste (clipboard image → save to ctx & paste path)
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_V, Gdk.KEY_v):
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            if clipboard.wait_is_image_available():
                self._paste_clipboard_image_path()
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

        return False

    def _on_button_press(self, terminal, event):
        if event.button == 3:  # right click
            menu = Gtk.Menu()

            item_copy = Gtk.MenuItem(label="Copy")
            item_copy.set_sensitive(terminal.get_has_selection())
            item_copy.connect("activate", lambda _: terminal.copy_clipboard_format(Vte.Format.TEXT))
            menu.append(item_copy)

            item_paste = Gtk.MenuItem(label="Paste")
            item_paste.connect("activate", lambda _: terminal.paste_clipboard())
            menu.append(item_paste)

            menu.append(Gtk.SeparatorMenuItem())

            item_select_all = Gtk.MenuItem(label="Select All")
            item_select_all.connect("activate", lambda _: terminal.select_all())
            menu.append(item_select_all)

            menu.append(Gtk.SeparatorMenuItem())

            item_paste_img = Gtk.MenuItem(label="Paste Image to Ctx")
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
                # Claude Code tab: keep config name instead of generic VTE title
                self.app.update_tab_title(self, self.claude_config.get("name", "Claude Code"))
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
        """Called when Claude has been idle for 10 seconds — check for pending tasks."""
        self._task_idle_timer = None
        if not self._task_project:
            return False
        try:
            import sqlite3
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

            # Check pending tasks
            count = db.execute(
                "SELECT COUNT(*) as c FROM tasks WHERE project = ? AND status = 'open'",
                (self._task_project,),
            ).fetchone()
            db.close()

            if count["c"] == 0:
                return False

            # Trigger: feed task instruction to Claude Code terminal
            message = (
                f"[AUTO-TRIGGER] Sprawdź listę zadań: tasks context {self._task_project} "
                f"— wykonaj następne otwarte zadanie. "
                f"MUSISZ oznaczyć KAŻDE wykonane zadanie: tasks done {self._task_project} <task_id> (w Bash). "
                f"Pętla auto-trigger kończy się DOPIERO gdy WSZYSTKIE zadania są zamknięte (done). "
                f"Jeśli nie oznaczysz — ta wiadomość będzie się powtarzać.\r"
            )
            self.terminal.feed_child(message.encode())

            # Refresh task panel if visible
            if hasattr(self.app, "task_panel"):
                GLib.idle_add(self.app.task_panel.refresh)
        except Exception:
            pass
        return False

    def _paste_clipboard_image_path(self):
        """Save clipboard image to ctx and paste its path into terminal."""
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        pixbuf = clipboard.wait_for_image()
        if not pixbuf:
            return
        project = self._detect_ctx_project()
        if not project:
            return
        filename = _save_ctx_image(project, pixbuf)
        path = os.path.join(CTX_IMAGES_DIR, project, filename)
        # Replace clipboard with path text and use native VTE paste
        clipboard.set_text(path, -1)
        clipboard.store()
        self.terminal.paste_clipboard()
        if hasattr(self.app, "ctx_panel"):
            self.app.ctx_panel.refresh()

    def _detect_ctx_project(self):
        """Auto-detect ctx project from tab config, or ask user."""
        import sqlite3
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
        import sqlite3
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
            return self.claude_config.get("name", "Claude Code")
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
        header.set_halign(Gtk.Align.START)
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
            session.get("color", SESSION_COLORS[0]),
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
            session.get("color", SESSION_COLORS[0]),
            Pango.Weight.NORMAL,
        ])

    def refresh(self):
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
            parent = self.store.append(None, [
                "\U0001F4C1",  # folder icon
                folder_name,
                "",
                folder_name,
                CATPPUCCIN["subtext1"],
                Pango.Weight.NORMAL,
            ])
            for s in folders[folder_name]:
                self._append_session(parent, s)

        # Ungrouped sessions
        for s in ungrouped:
            self._append_session(None, s)

        # ── Claude Code sessions ──
        claude_sessions = self.app.claude_manager.all()
        if claude_sessions:
            # Visual separator
            self.store.append(None, [
                "",
                "\u2500" * 26,  # ────────────
                "",
                "",
                CATPPUCCIN["surface2"],
                Pango.Weight.NORMAL,
            ])
            # Section header (bold)
            self.store.append(None, [
                "\U0001F916",  # 🤖
                "Claude Code",
                "",
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
                parent = self.store.append(None, [
                    "\U0001F4C1",
                    folder_name,
                    "",
                    folder_name,
                    CATPPUCCIN["subtext1"],
                    Pango.Weight.NORMAL,
                ])
                for s in claude_folders[folder_name]:
                    self._append_claude_session(parent, s)

            for s in claude_ungrouped:
                self._append_claude_session(None, s)

        self.tree.expand_all()

    def _get_selected_session_id(self):
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        if it is None:
            return None
        col_id = model.get_value(it, COL_ID)
        if col_id and not col_id.startswith("macro:"):
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
        elif col_id:
            session = self.app.session_manager.get(col_id)
            if session:
                self.app.open_ssh_tab(session)

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

                elif col_id and col_id.startswith("claude:"):
                    # Claude Code session context menu
                    claude_id = col_id[7:]
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

                    menu.show_all()
                    menu.popup_at_pointer(event)

                elif col_id:
                    # Session context menu
                    session_id = col_id
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
        elif col_id and not col_id.startswith("macro:"):
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
        elif col_id and not col_id.startswith("macro:"):
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

    # ── Claude Code CRUD ──

    def _edit_ctx(self, claude_id):
        config = self.app.claude_manager.get(claude_id)
        if not config:
            return
        project_dir = config.get("project_dir", "")
        if not project_dir:
            return
        ctx_project = os.path.basename(project_dir.rstrip("/"))
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
                ctx_name = os.path.basename(project_dir.rstrip("/"))
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
        import sqlite3
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
        import sqlite3
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
        paned.pack1(tree_scroll, resize=True, shrink=False)

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

        paned.pack2(detail_box, resize=False, shrink=False)
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
        self.store.clear()
        self.detail_header.set_text("")
        self.detail_view.get_buffer().set_text("")
        self.detail_stack.set_visible_child_name("text")
        if not os.path.exists(CTX_DB):
            return

        import sqlite3
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
        import sqlite3
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
        import sqlite3
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
        import sqlite3
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
        import sqlite3
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
        import sqlite3
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
                import sqlite3
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
        import sqlite3
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
    import sqlite3
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
        # Columns: done(bool), task_id(str), description(str), status(str)
        self.store = Gtk.ListStore(bool, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)

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
        self.tree.append_column(col_id)

        # Description column
        cell_desc = Gtk.CellRendererText()
        cell_desc.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_desc = Gtk.TreeViewColumn("Task", cell_desc, text=2)
        col_desc.set_expand(True)
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
        import sqlite3
        db = sqlite3.connect(CTX_DB)
        db.execute("UPDATE task_config SET autorun = 0 WHERE autorun = 1")
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
        import sqlite3
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
        self.store.clear()
        project = self._get_selected_project()
        if not project or not os.path.exists(CTX_DB):
            return
        import sqlite3
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT task_id, description, status FROM tasks WHERE project = ?",
            (project,),
        ).fetchall()
        db.close()
        tasks = sorted(rows, key=lambda r: _task_sort_key(r["task_id"]))
        for t in tasks:
            done = t["status"] == "done"
            indent = "  " if "." in t["task_id"] else ""
            self.store.append([done, t["task_id"], f"{indent}{t['description']}", t["status"]])

    def _load_autorun_state(self):
        project = self._get_selected_project()
        if not project or not os.path.exists(CTX_DB):
            self._update_auto_label(False)
            return
        import sqlite3
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT autorun FROM task_config WHERE project = ?", (project,)
        ).fetchone()
        db.close()
        active = bool(row and row["autorun"])
        self._update_auto_label(active)

    def _on_task_toggled(self, renderer, path):
        """Toggle task done/undone via checkbox."""
        project = self._get_selected_project()
        if not project:
            return
        it = self.store.get_iter(path)
        task_id = self.store[it][1]
        current_done = self.store[it][0]
        new_status = "open" if current_done else "done"

        import sqlite3
        db = sqlite3.connect(CTX_DB)
        db.execute(
            """UPDATE tasks SET status = ?, updated_at = datetime('now')
               WHERE project = ? AND task_id = ?""",
            (new_status, project, task_id),
        )
        db.commit()
        db.close()
        self.store[it][0] = not current_done
        self.store[it][3] = new_status

    def _on_autorun_toggle(self, enable):
        project = self._get_selected_project()
        if not project:
            return
        import sqlite3
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
        """Find a Claude Code tab matching this project and send trigger."""
        import sqlite3
        if not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row
        count = db.execute(
            "SELECT COUNT(*) as c FROM tasks WHERE project = ? AND status = 'open'",
            (project,),
        ).fetchone()
        db.close()
        if count["c"] == 0:
            return

        # Find matching Claude Code tab
        for i in range(self.app.notebook.get_n_pages()):
            tab = self.app.notebook.get_nth_page(i)
            if isinstance(tab, TerminalTab) and tab._task_project == project:
                message = (
                    f"[AUTO-TRIGGER] Sprawdź listę zadań: tasks context {project} "
                    f"— wykonaj następne otwarte zadanie. "
                    f"MUSISZ oznaczyć KAŻDE wykonane zadanie: tasks done {project} <task_id> (w Bash). "
                    f"Pętla auto-trigger kończy się DOPIERO gdy WSZYSTKIE zadania są zamknięte (done). "
                    f"Jeśli nie oznaczysz — ta wiadomość będzie się powtarzać.\r"
                )
                tab.terminal.feed_child(message.encode())
                return

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
                import sqlite3
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
                import sqlite3
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
        import sqlite3
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
        import sqlite3
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
        import sqlite3
        db = sqlite3.connect(CTX_DB)
        db.execute(
            """UPDATE tasks SET status = 'open', updated_at = datetime('now')
               WHERE project = ?""",
            (project,),
        )
        db.commit()
        db.close()
        self._load_tasks()


# ─── BTerminalApp ─────────────────────────────────────────────────────────────


class BTerminalApp(Gtk.Window):
    """Główne okno aplikacji BTerminal."""

    def __init__(self):
        super().__init__(title=APP_NAME)
        self.set_default_size(1200, 700)
        self.set_icon_name("bterminal")

        # Apply CSS
        css_provider = Gtk.CssProvider()
        css_provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Dark theme
        settings = Gtk.Settings.get_default()
        settings.set_property("gtk-application-prefer-dark-theme", True)

        # Session managers
        self.session_manager = SessionManager()
        self.claude_manager = ClaudeSessionManager()

        # Layout: HPaned
        paned = Gtk.HPaned()
        self.add(paned)

        # Sidebar container with stack switcher
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.get_style_context().add_class("sidebar")
        sidebar_box.set_size_request(250, -1)

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

        switcher = Gtk.StackSwitcher()
        switcher.set_stack(self.sidebar_stack)
        switcher.set_halign(Gtk.Align.FILL)
        switcher.set_homogeneous(True)

        sidebar_box.pack_start(switcher, False, False, 0)
        sidebar_box.pack_start(self.sidebar_stack, True, True, 0)

        paned.pack1(sidebar_box, resize=False, shrink=False)

        # Notebook (tabs)
        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.set_show_border(False)
        self.notebook.popup_disable()
        paned.pack2(self.notebook, resize=True, shrink=False)

        paned.set_position(250)

        # Auto-refresh panels when switching to them
        def _on_sidebar_switch(stack, _param):
            child = stack.get_visible_child()
            if child is self.ctx_panel:
                self.ctx_panel.refresh()
            elif child is self.consult_panel:
                self.consult_panel.refresh()
            elif child is self.task_panel:
                self.task_panel.refresh()

        self.sidebar_stack.connect("notify::visible-child", _on_sidebar_switch)

        # Keyboard shortcuts
        self.connect("key-press-event", self._on_key_press)
        self.connect("delete-event", self._on_delete_event)
        self.notebook.connect("switch-page", self._on_switch_page)

        # Open initial local shell
        self.add_local_tab()

        self.show_all()

    def _update_window_title(self):
        """Update window title bar: 'BTerminal — tab_name [n/total]'."""
        n = self.notebook.get_n_pages()
        idx = self.notebook.get_current_page()
        if idx < 0 or n == 0:
            self.set_title(APP_NAME)
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
        """Build a tab label with a close button."""
        box = Gtk.Box(spacing=4)

        label = Gtk.Label(label=text)
        box.pack_start(label, True, True, 0)

        close_btn = Gtk.Button(label="×")
        close_btn.get_style_context().add_class("tab-close-btn")
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.connect("clicked", lambda _: self.close_tab(tab_widget))
        box.pack_start(close_btn, False, False, 0)

        box.show_all()
        return box

    def add_local_tab(self):
        tab = TerminalTab(self)
        label = self._build_tab_label("Terminal", tab)
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        self._update_window_title()

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

    def open_claude_tab(self, config):
        tab = TerminalTab(self, claude_config=config)
        tab_name = config.get("name", "Claude Code")
        label = self._build_tab_label(tab_name, tab)
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        self._update_window_title()

    def close_tab(self, tab):
        idx = self.notebook.page_num(tab)
        if idx >= 0:
            self.notebook.remove_page(idx)
            tab.destroy()
        # If no tabs left, open a new local shell
        if self.notebook.get_n_pages() == 0:
            self.add_local_tab()
        self._update_window_title()

    def on_tab_child_exited(self, tab):
        """Called when a terminal's child process exits."""
        GLib.idle_add(self.close_tab, tab)

    def update_tab_title(self, tab, title):
        """Update tab label when terminal title changes."""
        idx = self.notebook.page_num(tab)
        if idx >= 0:
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

    def _on_key_press(self, widget, event):
        mod = event.state & Gtk.accelerator_get_default_mod_mask()
        ctrl = Gdk.ModifierType.CONTROL_MASK
        shift = Gdk.ModifierType.SHIFT_MASK

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
                        tab._paste_clipboard_image_path()
                        return True
                    tab.terminal.paste_clipboard()
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

        return False

    def _on_delete_event(self, widget, event):
        return False


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    GLib.set_prgname("bterminal")
    GLib.set_application_name("BTerminal")

    application = Gtk.Application(
        application_id="com.github.DexterFromLab.BTerminal",
        flags=Gio.ApplicationFlags.FLAGS_NONE,
    )

    def on_activate(app):
        windows = app.get_windows()
        if windows:
            windows[0].present()
            return
        win = BTerminalApp()
        app.add_window(win)

    application.connect("activate", on_activate)
    application.run(None)


if __name__ == "__main__":
    main()
