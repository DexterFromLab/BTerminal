#!/usr/bin/env python3
"""BTerminal — Terminal SSH z panelem sesji, w stylu MobaXterm."""

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
gi.require_version("Gdk", "3.0")

import json
import os
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

        for i, text in enumerate(["Name:", "Folder:", "Color:"]):
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
            prompt = session.get("prompt", "")
            if prompt:
                self.textview.get_buffer().set_text(prompt)

        self.show_all()

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

        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.environ.get("HOME", "/"),
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
                self.app.claude_manager.add(dlg.get_data())
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
            self.refresh()
        dlg.destroy()


# ─── BTerminalApp ─────────────────────────────────────────────────────────────


class BTerminalApp(Gtk.Window):
    """Główne okno aplikacji BTerminal."""

    def __init__(self):
        super().__init__(title=APP_NAME)
        self.set_default_size(1200, 700)
        self.set_icon_name("utilities-terminal")

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
        Gtk.main_quit()
        return False


# ─── Main ─────────────────────────────────────────────────────────────────────

def main():
    app = BTerminalApp()
    Gtk.main()


if __name__ == "__main__":
    main()
