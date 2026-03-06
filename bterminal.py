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
import uuid

from gi.repository import Gdk, Gio, GLib, Gtk, Pango, Vte

# ─── Stałe i konfiguracja ────────────────────────────────────────────────────

APP_NAME = "BTerminal"
CONFIG_DIR = os.path.expanduser("~/.config/bterminal")
SESSIONS_FILE = os.path.join(CONFIG_DIR, "sessions.json")
CLAUDE_SESSIONS_FILE = os.path.join(CONFIG_DIR, "claude_sessions.json")
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
    "Enter": "\n",
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
                    f"Przed zakończeniem sesji: ctx summary {basename} \"<co zrobiliśmy>\""
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
            f'Przed zako\u0144czeniem sesji: ctx summary {name} "<co zrobili\u015bmy>"'
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
        self.terminal.set_scroll_on_output(True)
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

        # Ctrl+Shift+V: paste
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_V, Gdk.KEY_v):
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
            else:
                self.app.update_tab_title(self, title)

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
        self.get_style_context().add_class("sidebar")
        self.set_size_request(250, -1)

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

        # Sidebar
        self.sidebar = SessionSidebar(self)
        paned.pack1(self.sidebar, resize=False, shrink=False)

        # Notebook (tabs)
        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.set_show_border(False)
        self.notebook.popup_disable()
        paned.pack2(self.notebook, resize=True, shrink=False)

        paned.set_position(250)

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

        # Ctrl+Shift+V: paste
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_V, Gdk.KEY_v):
            term = self._get_current_terminal()
            if term:
                term.paste_clipboard()
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
