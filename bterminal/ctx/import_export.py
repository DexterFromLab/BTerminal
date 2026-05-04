"""Ctx Import/Export dialogs.

_CtxExportDialog: dump entries from one project to a JSON file.
_CtxImportDialog: load entries from a JSON file into a chosen project,
with merge/overwrite strategy and image attachment handling.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ctx/import_export.py` in a later migration etap.
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


