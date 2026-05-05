"""TaskListPanel — sqlite-backed task tracker with project dropdown + auto-trigger

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/panels/panel_tasks.py` in a later migration etap.
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
    CTX_DB,
    CTX_IMAGES_DIR,
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


def _ensure_tasks_tables():
    """Create tasks tables in ctx database if they don't exist."""
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
        CREATE TABLE IF NOT EXISTS task_claims (
            project TEXT NOT NULL,
            task_id TEXT NOT NULL,
            session_id TEXT NOT NULL,
            claimed_at TEXT DEFAULT (datetime('now')),
            PRIMARY KEY (project, task_id)
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
        # Guard: True while we mutate project_combo programmatically
        # (refresh, tab-switch sync). Suppresses the user-intent branch in
        # _on_project_changed so we don't overwrite tab._task_project.
        self._suspend_changed = False

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
        # Columns: done(bool), task_id(str), description(str), status(str), is_separator(bool)
        self.store = Gtk.ListStore(bool, str, str, str, bool)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)
        self.tree.set_row_separator_func(self._row_separator_func)

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
        col_id.set_cell_data_func(cell_id, self._style_cell)
        self.tree.append_column(col_id)

        # Description column
        cell_desc = Gtk.CellRendererText()
        cell_desc.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_desc = Gtk.TreeViewColumn("Task", cell_desc, text=2)
        col_desc.set_expand(True)
        col_desc.set_cell_data_func(cell_desc, self._style_cell)
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
        _ensure_tasks_tables()
        db = sqlite3.connect(CTX_DB)
        db.execute("UPDATE task_config SET autorun = 0 WHERE autorun = 1")
        db.execute("DELETE FROM task_claims")
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
        # Per-tab binding: a user-driven dropdown change pins the chosen
        # project to the currently active tab. This decouples per-tab task
        # tracking from claude_config.project_dir (which only seeds the
        # default at tab creation). Programmatic combo updates (refresh,
        # _sync_task_panel_project, set_active_tab) wrap themselves in
        # _suspend_changed.
        if self._suspend_changed:
            return
        project = self._get_selected_project()
        if not project:
            return
        nb = self.app.notebook
        idx = nb.get_current_page()
        if idx < 0:
            return
        tab = nb.get_nth_page(idx)
        from bterminal import TerminalTab  # lazy: TerminalTab still in bterminal.py
        if isinstance(tab, TerminalTab):
            tab._task_project = project

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

    def set_active_tab(self, tab):
        """Per-tab binding: ustaw project combo na project z taba (jeśli ma).

        Reuses istniejącego mechanizmu — _task_project ustawiany przez
        TerminalTab.spawn_claude. Brak claude_config → no-op."""
        if tab is None:
            return
        project = getattr(tab, "_task_project", None)
        if not project:
            return
        model = self.project_combo.get_model()
        if not model:
            return
        self._suspend_changed = True
        try:
            for i, row in enumerate(model):
                if row[0] == project:
                    self.project_combo.set_active(i)
                    return
        finally:
            self._suspend_changed = False
        try:
            self._db_mtime = os.path.getmtime(CTX_DB)
        except OSError:
            pass

    def _load_projects(self):
        self._suspend_changed = True
        try:
            current = self._get_selected_project()
            self.project_combo.remove_all()
            if not os.path.exists(CTX_DB):
                return
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
        finally:
            self._suspend_changed = False

    def _load_tasks(self):
        # Preserve scroll position and selection
        tree_scroll = self.tree.get_parent()
        vadj = tree_scroll.get_vadjustment() if tree_scroll else None
        scroll_pos = vadj.get_value() if vadj else 0
        sel = self.tree.get_selection()
        _, selected_iter = sel.get_selected()
        selected_task_id = self.store[selected_iter][1] if selected_iter else None

        self.store.clear()
        project = self._get_selected_project()
        if not project or not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row
        rows = db.execute(
            "SELECT task_id, description, status FROM tasks WHERE project = ?",
            (project,),
        ).fetchall()
        db.close()

        # Split into active (newest first) and done (at bottom)
        active = [r for r in rows if r["status"] != "done"]
        done = [r for r in rows if r["status"] == "done"]
        active.sort(key=lambda r: _task_sort_key(r["task_id"]))
        done.sort(key=lambda r: _task_sort_key(r["task_id"]))

        restore_path = None
        for t in active:
            indent = "  " if "." in t["task_id"] else ""
            it = self.store.append([False, t["task_id"], f"{indent}{t['description']}", t["status"], False])
            if t["task_id"] == selected_task_id:
                restore_path = self.store.get_path(it)

        # Separator row between active and done
        if active and done:
            self.store.append([False, "", "", "", True])

        for t in done:
            indent = "  " if "." in t["task_id"] else ""
            it = self.store.append([True, t["task_id"], f"{indent}{t['description']}", t["status"], False])
            if t["task_id"] == selected_task_id:
                restore_path = self.store.get_path(it)

        # Restore selection and scroll position
        if restore_path:
            sel.select_path(restore_path)
        if vadj:
            GLib.idle_add(vadj.set_value, scroll_pos)

    @staticmethod
    def _row_separator_func(model, iter_, data=None):
        """Return True for separator rows."""
        return model[iter_][4]

    @staticmethod
    def _style_cell(column, cell, model, iter_, data=None):
        """Gray out done tasks."""
        is_done = model[iter_][0]
        if is_done:
            cell.set_property("foreground", CATPPUCCIN["overlay0"])
        else:
            cell.set_property("foreground", CATPPUCCIN["text"])

    def _load_autorun_state(self):
        project = self._get_selected_project()
        if not project or not os.path.exists(CTX_DB):
            self._update_auto_label(False)
            return
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row
        row = db.execute(
            "SELECT autorun FROM task_config WHERE project = ?", (project,)
        ).fetchone()
        db.close()
        active = bool(row and row["autorun"])
        self._update_auto_label(active)

    def _on_task_toggled(self, renderer, path):
        """Toggle task done/undone via checkbox and re-sort."""
        project = self._get_selected_project()
        if not project:
            return
        it = self.store.get_iter(path)
        if self.store[it][4]:  # separator row
            return
        task_id = self.store[it][1]
        current_done = self.store[it][0]
        new_status = "open" if current_done else "done"

        db = sqlite3.connect(CTX_DB)
        db.execute(
            """UPDATE tasks SET status = ?, updated_at = datetime('now')
               WHERE project = ? AND task_id = ?""",
            (new_status, project, task_id),
        )
        db.commit()
        db.close()
        # Reload to re-sort (active on top, done on bottom)
        self._load_tasks()

    def _on_autorun_toggle(self, enable):
        project = self._get_selected_project()
        if not project:
            return
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
        """Find Claude Code tabs matching this project and send claim-based triggers."""
        if not os.path.exists(CTX_DB):
            return
        from bterminal import TerminalTab  # lazy: TerminalTab still in bterminal.py
        db = sqlite3.connect(CTX_DB)
        db.row_factory = sqlite3.Row

        # Send trigger to each matching tab — each claims its own task
        for i in range(self.app.notebook.get_n_pages()):
            tab = self.app.notebook.get_nth_page(i)
            if isinstance(tab, TerminalTab) and tab._task_project == project:
                task = TerminalTab._claim_next_task(db, project, tab._task_session_id)
                if not task:
                    break
                message = (
                    f"[AUTO-TRIGGER] Your assigned task: {task['task_id']} — {task['description']}\n"
                    f"Check the full list: tasks context {project} --session {tab._task_session_id}\n"
                    f"You MUST mark it after completion: tasks done {project} {task['task_id']} (in Bash). "
                    f"The auto-trigger loop only ends when ALL tasks are closed (done). "
                    f"If you do not mark it — this message will keep repeating."
                )
                _t = tab.terminal
                _t.feed_child(message.encode())
                GLib.timeout_add(100, lambda t=_t: t.feed_child(b"\r") or False)
        db.close()

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
        db = sqlite3.connect(CTX_DB)
        db.execute(
            """UPDATE tasks SET status = 'open', updated_at = datetime('now')
               WHERE project = ?""",
            (project,),
        )
        db.commit()
        db.close()
        self._load_tasks()


