"""FilesPanel — sidebar file browser for the active Claude project

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/panels/panel_files.py` in a later migration etap.
"""

import datetime
import importlib.util
import json
import os
import re
import shutil
import sqlite3
import subprocess
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango

from bterminal.config import (
    CATPPUCCIN,
    CONFIG_DIR,
    CLAUDE_SESSIONS_FILE,
    CONSULT_CONFIG_FILE,
    OPTIONS_FILE,
    PLUGINS_CONFIG_FILE,
    PLUGINS_DIR,
    SESSIONS_FILE,
    _OPTIONS,
    _parse_color,
    _session_color,
    show_error_dialog,
    show_info_dialog,
)


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

        # Task #8 (#80) / #9 (#81): re-evaluate meld button sensitivity
        # after install completes. invalidate_cache → listener fires
        # → we re-trigger _on_selection_changed (which now re-checks
        # is_feature_available). No-op when no selection yet.
        from bterminal.diagnostics import subscribe_invalidation
        subscribe_invalidation(self._on_deps_changed)

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
        if page and getattr(page, "ai_config", None):
            d = page.ai_config.get("project_dir", "")
            if d and os.path.isdir(d):
                return self._find_project_root(d)
        # Fallback: first Claude tab with a valid project dir
        for i in range(nb.get_n_pages()):
            tab = nb.get_nth_page(i)
            if getattr(tab, "ai_config", None):
                d = tab.ai_config.get("project_dir", "")
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
            if getattr(tab, "ai_config", None):
                d = tab.ai_config.get("project_dir", "").rstrip("/")
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

    def set_active_tab(self, tab):
        """Per-tab binding: jeśli combo na '— Active tab —' (default),
        reload tree z project_dir aktywnego taba."""
        if not self._pinned_dir:
            self._load_tree()

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

    def _on_deps_changed(self):
        """Re-evaluate widget sensitivity after diagnostics.invalidate_cache
        (task #9 / #81 — typically fired by InstallerWizard on success).
        Triggers _on_selection_changed via the existing GTK signal so we
        don't have to duplicate the meld-availability logic."""
        self._on_selection_changed(self._tv.get_selection())

    def _on_selection_changed(self, sel):
        model, it = sel.get_selected()
        if it:
            path = model.get_value(it, 1)
            self._selected_path = path if path != "__dummy__" else ""
        else:
            self._selected_path = ""
        # Task #8: button sensitive iff selection AND meld actually
        # available — used to be just selection-based which left a
        # dead button visible on hosts without meld.
        from bterminal.diagnostics import is_feature_available
        self._btn_meld.set_sensitive(
            bool(self._selected_path) and is_feature_available("meld"),
        )

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
        # Task #8 (#80): centralized feature gating via diagnostics
        # cache; cuts shutil.which() spam on rapid panel refresh.
        from bterminal.diagnostics import is_feature_available
        if not is_feature_available("meld"):
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
        from bterminal.diagnostics import is_feature_available
        if not is_feature_available("meld"):
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

        from bterminal.diagnostics import is_feature_available
        for label, cmd in [("VS Code", "code"), ("Zed", "zed"),
                            ("gedit", "gedit"), ("kate", "kate"),
                            ("File Manager", "xdg-open")]:
            if cmd == "xdg-open" or is_feature_available(cmd):
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


