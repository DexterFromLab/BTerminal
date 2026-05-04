"""SessionDialog + MacroDialog + MacroStepRow — SSH session config dialogs.

SessionDialog: edit/create an SSH session entry (host, user, port, etc.).
MacroDialog: edit/create a macro (sequence of typed steps + waits).
MacroStepRow: one row in the MacroDialog listbox.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/dialogs/sessions.py` in a later migration etap.
"""

import json
import os
import shlex
import subprocess
import uuid
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from bterminal.config import (
    CATPPUCCIN,
    CONFIG_DIR,
    SSH_PATH,
    _OPTIONS,
    _parse_color,
    show_error_dialog,
    show_info_dialog,
)


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


