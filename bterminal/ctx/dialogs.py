"""Ctx dialogs — wizard + editor + entry/project sub-dialogs.

CtxSetupWizard: 4-step setup wizard for new projects (intro → project
description → Claude integration → done).
_CtxEntryDialog / _CtxProjectDialog: small CRUD dialogs.
CtxEditDialog: main editor for a project's ctx entries (rules, images,
log links, schemas).

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ctx/{wizard,editor}.py` in a later migration etap.
"""

import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from bterminal.config import (
    CATPPUCCIN,
    CTX_DB,
    CTX_IMAGES_DIR,
    show_error_dialog,
    show_info_dialog,
)
from bterminal.ctx.helpers import _smart_project_name


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



