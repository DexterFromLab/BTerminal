"""ClaudeCodeDialog + _build_intro_prompt — Claude Code session config dialog.

The dialog collects: project_dir, name, color, prompt, resume flag,
skip_permissions flag, enabled_plugins (per-tab override).
_build_intro_prompt assembles the per-session header (rules block,
session context, Tools help) injected at session start.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/dialogs/claude_code.py` in a later migration etap.
"""

import os
import re
import shlex
import subprocess
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from bterminal.config import (
    CATPPUCCIN,
    CONFIG_DIR,
    CTX_DB,
    SESSIONS_FILE,
    _OPTIONS,
    _parse_color,
    show_error_dialog,
    show_info_dialog,
)
from bterminal.ctx.helpers import _resolve_ctx_project_name, _smart_project_name


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

        # Per-tab plugin selector (Etap 8)
        box.pack_start(Gtk.Separator(), False, False, 2)
        lbl_plugins = Gtk.Label(
            label="Plugins for this project:", halign=Gtk.Align.START,
        )
        box.pack_start(lbl_plugins, False, False, 0)
        lbl_plugins_hint = Gtk.Label(
            label="Odznacz pluginy które nie mają być wstrzykiwane do intro promptu tej sesji. "
                  "Zapisuje się per projekt — kolejne otwarcie tej sesji respektuje wybór.",
            halign=Gtk.Align.START,
            xalign=0,
            wrap=True,
            max_width_chars=60,
        )
        lbl_plugins_hint.get_style_context().add_class("dim-label")
        box.pack_start(lbl_plugins_hint, False, False, 0)

        self._plugin_checks: dict = {}
        saved_enabled = None
        if session and isinstance(session.get("enabled_plugins"), list):
            saved_enabled = set(session["enabled_plugins"])

        plugins_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        available = _list_available_plugins(parent) if hasattr(parent, "_plugins") else []
        for entry in available:
            name = entry["name"]
            tag = "[GTK]" if entry["type"] == "gtk" else "[sidecar]"
            label = f"{tag} {entry['title']}"
            if entry["title"] != name:
                label += f"  ({name})"
            chk = Gtk.CheckButton(label=label)
            if saved_enabled is not None:
                chk.set_active(name in saved_enabled)
            else:
                chk.set_active(
                    entry["default_in_session"]
                    and entry["currently_enabled_globally"]
                )
            self._plugin_checks[name] = chk
            plugins_box.pack_start(chk, False, False, 0)
        if not available:
            empty_lbl = Gtk.Label(
                label="(no plugins or sidecar manifests available)",
                halign=Gtk.Align.START,
            )
            empty_lbl.get_style_context().add_class("dim-label")
            plugins_box.pack_start(empty_lbl, False, False, 0)

        plugins_scroll = Gtk.ScrolledWindow()
        plugins_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        plugins_scroll.set_min_content_height(120)
        plugins_scroll.add(plugins_box)
        box.pack_start(plugins_scroll, False, False, 0)

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
            "enabled_plugins": sorted(
                name for name, chk in self._plugin_checks.items() if chk.get_active()
            ),
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


