"""CtxManagerPanel — sidebar panel for managing the ctx database.

Tree view of projects/keys/values with detail pane on the right.
Provides Add/Edit/Delete/Import/Export actions and image attachment
preview. Refreshes from disk on demand to pick up external `ctx` CLI
edits.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/panels/ctx_manager.py` in a later migration etap.
"""

import json
import os
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
from bterminal.ctx.dialogs import _CtxEntryDialog, _CtxProjectDialog
from bterminal.ctx.import_export import _CtxExportDialog, _CtxImportDialog
from bterminal.ui.sidebar import _restore_expanded, _save_expanded


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

    def set_active_tab(self, tab):
        """Per-tab binding: rozwiń projekt z aktywnego taba w drzewie ctx.

        Tab z claude_config → resolve project_name → znajdź wiersz +
        ustaw selection. Tab bez project_dir → no-op."""
        if tab is None or getattr(tab, "claude_config", None) is None:
            return
        project_dir = (tab.claude_config or {}).get("project_dir", "")
        if not project_dir:
            return
        from bterminal.ctx.helpers import _resolve_ctx_project_name
        target = _resolve_ctx_project_name(project_dir)
        # Walk top-level rows (projects); match by display name (col 1).
        it = self.store.get_iter_first()
        while it is not None:
            if self.store.get_value(it, 1) == target:
                path = self.store.get_path(it)
                self.tree.expand_row(path, False)
                self.tree.get_selection().select_iter(it)
                self.tree.scroll_to_cell(path, None, True, 0.0, 0.0)
                return
            it = self.store.iter_next(it)

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

