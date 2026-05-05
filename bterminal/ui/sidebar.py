"""SessionSidebar — left panel with saved SSH/Claude session tree.

Provides Add/Edit/Delete/Connect actions, drag-and-drop reordering,
right-click context menu, and folder management.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/sidebar.py` in a later migration etap.
"""

import json
import os
import shlex
import subprocess
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango
import shutil
import uuid

from bterminal.i18n import _, register_translatable
from bterminal.config import (
    APP_NAME,
    CATPPUCCIN,
    CONFIG_DIR,
    SSH_PATH,
    _OPTIONS,
    _parse_color,
    _session_color,
    show_error_dialog,
    show_info_dialog,
)


# TreeStore columns
COL_ICON = 0
COL_NAME = 1
COL_ID = 2
COL_TOOLTIP = 3
COL_COLOR = 4
COL_WEIGHT = 5


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


class SessionSidebar(Gtk.Box):
    """Panel lewy z listą zapisanych sesji SSH."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app

        # Header — registered for live refresh so language change updates it.
        # The leading whitespace is presentation-only and stays out of msgid.
        header = Gtk.Label()
        def _set_header(w):
            w.set_label("  " + _("{app} Sessions").format(app=APP_NAME))
        _set_header(header)
        register_translatable(header, "{app} Sessions",
                              lambda w, _t: _set_header(w))
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
        from bterminal import SessionDialog  # lazy: dialog still in bterminal.py (Etap 7)
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
        from bterminal import ClaudeCodeDialog, _run_ctx_wizard_if_needed  # lazy: Etap 7
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
        from bterminal import SessionDialog  # lazy: dialog still in bterminal.py (Etap 7)
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
        from bterminal import MacroDialog  # lazy: dialog still in bterminal.py (Etap 7)
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
        from bterminal import MacroDialog  # lazy: dialog still in bterminal.py (Etap 7)
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
        from bterminal import CtxEditDialog, _resolve_ctx_project_name  # lazy: Etap 7
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
        from bterminal import ClaudeCodeDialog, _run_ctx_wizard_if_needed  # lazy: Etap 7
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
        from bterminal import _resolve_ctx_project_name, _is_ctx_available, _is_ctx_project_registered  # lazy: Etap 7
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

