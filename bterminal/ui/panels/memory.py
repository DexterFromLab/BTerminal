"""MemoryPanel — manage Claude Code memory (rules, injection config, session logs)

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/panels/panel_memory.py` in a later migration etap.
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


class MemoryPanel(Gtk.Box):
    """Panel for managing Claude Code memory: rules, injection config, and session logs."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.app = app
        self._current_project = None
        self._rules_store = None

        header = Gtk.Label(label="Memory")
        header.get_style_context().add_class("sidebar-header")
        header.set_halign(Gtk.Align.START)
        header.set_hexpand(True)
        self.pack_start(header, False, False, 0)

        # Project selector
        proj_box = Gtk.Box(spacing=4)
        proj_box.set_margin_start(6)
        proj_box.set_margin_end(6)
        proj_box.set_margin_top(6)
        proj_lbl = Gtk.Label(label="Project:")
        proj_lbl.set_halign(Gtk.Align.START)
        self._proj_combo = Gtk.ComboBoxText()
        self._proj_combo.set_hexpand(True)
        self._proj_combo.connect("changed", self._on_project_changed)
        proj_box.pack_start(proj_lbl, False, False, 0)
        proj_box.pack_start(self._proj_combo, True, True, 0)
        btn_refresh_proj = Gtk.Button(label="⟳")
        btn_refresh_proj.set_tooltip_text("Refresh project list")
        btn_refresh_proj.get_style_context().add_class("sidebar-btn")
        btn_refresh_proj.connect("clicked", lambda _: self._load_projects())
        proj_box.pack_start(btn_refresh_proj, False, False, 0)
        self.pack_start(proj_box, False, False, 0)

        # Accordion sections in a scrolled window
        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_hexpand(True)
        scroll.set_vexpand(True)
        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scroll.add(inner)
        self.pack_start(scroll, True, True, 0)

        # ── Injection config ─────────────────────────────────────────────
        cfg_frame = self._make_section("⚙ Injection Config")
        cfg_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        cfg_body.set_margin_start(8)
        cfg_body.set_margin_end(8)
        cfg_body.set_margin_bottom(6)

        inj_row = Gtk.Box(spacing=6)
        Gtk.Label(label="Inject rules every N prompts:").set_halign(Gtk.Align.START)
        inj_lbl = Gtk.Label(label="Inject rules every:")
        inj_lbl.set_halign(Gtk.Align.START)
        self._spin_inject = Gtk.SpinButton.new_with_range(1, 500, 1)
        self._spin_inject.set_value(100)
        self._spin_inject.set_width_chars(4)
        inj_row.pack_start(inj_lbl, True, True, 0)
        inj_row.pack_start(self._spin_inject, False, False, 0)
        inj_row.pack_start(Gtk.Label(label="prompts"), False, False, 0)
        cfg_body.pack_start(inj_row, False, False, 0)

        ref_row = Gtk.Box(spacing=6)
        ref_lbl = Gtk.Label(label="Refresh CTX every:")
        ref_lbl.set_halign(Gtk.Align.START)
        self._spin_refresh = Gtk.SpinButton.new_with_range(1, 1000, 1)
        self._spin_refresh.set_value(200)
        self._spin_refresh.set_width_chars(4)
        ref_row.pack_start(ref_lbl, True, True, 0)
        ref_row.pack_start(self._spin_refresh, False, False, 0)
        ref_row.pack_start(Gtk.Label(label="prompts"), False, False, 0)
        cfg_body.pack_start(ref_row, False, False, 0)

        apply_row = Gtk.Box(spacing=8)
        btn_save_cfg = Gtk.Button(label="Apply")
        btn_save_cfg.get_style_context().add_class("sidebar-btn")
        btn_save_cfg.connect("clicked", self._on_save_config)
        apply_row.pack_start(btn_save_cfg, False, False, 0)
        self._cfg_status_lbl = Gtk.Label(label="")
        self._cfg_status_lbl.get_style_context().add_class("dim-label")
        apply_row.pack_start(self._cfg_status_lbl, False, False, 0)
        cfg_body.pack_start(apply_row, False, False, 0)

        cfg_frame.add(cfg_body)
        inner.pack_start(cfg_frame, False, False, 0)

        # ── Rules list ───────────────────────────────────────────────────
        rules_frame = self._make_section("📋 Rules")
        rules_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        rules_body.set_margin_start(8)
        rules_body.set_margin_end(8)
        rules_body.set_margin_bottom(6)

        # TreeView: [✓/✗] [#id] [rule text]
        self._rules_store = Gtk.ListStore(bool, int, str)
        tv = Gtk.TreeView(model=self._rules_store)
        tv.set_headers_visible(False)
        tv.set_size_request(-1, 140)

        ren_toggle = Gtk.CellRendererToggle()
        ren_toggle.connect("toggled", self._on_rule_toggled)
        col_toggle = Gtk.TreeViewColumn("", ren_toggle, active=0)
        col_toggle.set_fixed_width(28)
        tv.append_column(col_toggle)

        ren_id = Gtk.CellRendererText()
        col_id = Gtk.TreeViewColumn("#", ren_id, text=1)
        col_id.set_fixed_width(32)
        tv.append_column(col_id)

        ren_text = Gtk.CellRendererText()
        ren_text.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_text = Gtk.TreeViewColumn("Rule", ren_text, text=2)
        col_text.set_expand(True)
        tv.append_column(col_text)

        self._rules_tv = tv
        tv_scroll = Gtk.ScrolledWindow()
        tv_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tv_scroll.set_min_content_height(100)
        tv_scroll.add(tv)
        rules_body.pack_start(tv_scroll, True, True, 0)

        # Add rule entry
        add_box = Gtk.Box(spacing=4)
        self._rule_entry = Gtk.Entry()
        self._rule_entry.set_placeholder_text("New rule…")
        self._rule_entry.set_hexpand(True)
        self._rule_entry.connect("activate", self._on_add_rule)
        btn_add = Gtk.Button(label="+")
        btn_add.get_style_context().add_class("sidebar-btn")
        btn_add.connect("clicked", self._on_add_rule)
        btn_del = Gtk.Button(label="✕")
        btn_del.get_style_context().add_class("sidebar-btn")
        btn_del.connect("clicked", self._on_remove_rule)
        add_box.pack_start(self._rule_entry, True, True, 0)
        add_box.pack_start(btn_add, False, False, 0)
        add_box.pack_start(btn_del, False, False, 0)
        rules_body.pack_start(add_box, False, False, 0)

        rules_frame.add(rules_body)
        inner.pack_start(rules_frame, False, False, 0)

        # ── Session logs ─────────────────────────────────────────────────
        logs_frame = self._make_section("📜 Session Logs")
        logs_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        logs_body.set_margin_start(8)
        logs_body.set_margin_end(8)
        logs_body.set_margin_bottom(6)

        self._logs_store = Gtk.ListStore(str, str)  # (display, filename)
        logs_tv = Gtk.TreeView(model=self._logs_store)
        logs_tv.set_headers_visible(False)
        logs_tv.set_size_request(-1, 100)
        ren_log = Gtk.CellRendererText()
        ren_log.set_property("ellipsize", Pango.EllipsizeMode.START)
        logs_tv.append_column(Gtk.TreeViewColumn("", ren_log, text=0))
        self._logs_tv = logs_tv
        logs_tv.connect("row-activated", self._on_log_activated)

        logs_scroll = Gtk.ScrolledWindow()
        logs_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        logs_scroll.set_min_content_height(80)
        logs_scroll.add(logs_tv)
        logs_body.pack_start(logs_scroll, True, True, 0)

        collect_row = Gtk.Box(spacing=8)
        btn_collect = Gtk.Button(label="Collect & View")
        btn_collect.get_style_context().add_class("sidebar-btn")
        btn_collect.connect("clicked", self._on_collect_log)
        collect_row.pack_start(btn_collect, False, False, 0)
        self._collect_status_lbl = Gtk.Label(label="↑ double-click to view")
        self._collect_status_lbl.get_style_context().add_class("dim-label")
        collect_row.pack_start(self._collect_status_lbl, False, False, 0)
        logs_body.pack_start(collect_row, False, False, 0)

        logs_frame.add(logs_body)
        inner.pack_start(logs_frame, False, False, 0)

        # ── Change history ───────────────────────────────────────────────────
        hist_frame = self._make_section("📖 Change History")
        hist_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        hist_body.set_margin_start(8)
        hist_body.set_margin_end(8)
        hist_body.set_margin_bottom(6)

        hist_tabs = Gtk.Box(spacing=2)
        btn_hist_ctx = Gtk.Button(label="CTX")
        btn_hist_ctx.get_style_context().add_class("sidebar-btn")
        btn_hist_rules = Gtk.Button(label="Rules")
        btn_hist_rules.get_style_context().add_class("sidebar-btn")
        hist_tabs.pack_start(btn_hist_ctx, True, True, 0)
        hist_tabs.pack_start(btn_hist_rules, True, True, 0)
        hist_body.pack_start(hist_tabs, False, False, 0)

        self._hist_view = Gtk.TextView()
        self._hist_view.set_editable(False)
        self._hist_view.set_monospace(True)
        self._hist_view.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self._hist_view.set_left_margin(4)
        self._hist_view.set_cursor_visible(False)
        hist_scroll = Gtk.ScrolledWindow()
        hist_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        hist_scroll.set_min_content_height(100)
        hist_scroll.add(self._hist_view)
        hist_body.pack_start(hist_scroll, True, True, 0)

        btn_hist_ctx.connect("clicked", lambda _: self._refresh_history("ctx"))
        btn_hist_rules.connect("clicked", lambda _: self._refresh_history("rules"))

        hist_frame.add(hist_body)
        inner.pack_start(hist_frame, False, False, 0)

        # ── Wizard ───────────────────────────────────────────────────────────
        wizard_frame = self._make_section("🧙 Auto-configure Wizard")
        wizard_body = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        wizard_body.set_margin_start(8)
        wizard_body.set_margin_end(8)
        wizard_body.set_margin_top(4)
        wizard_body.set_margin_bottom(6)

        wizard_lbl = Gtk.Label(
            label="Analyzes project context and proposes rules automatically."
        )
        wizard_lbl.set_line_wrap(True)
        wizard_lbl.set_xalign(0)
        wizard_body.pack_start(wizard_lbl, False, False, 0)

        btn_wizard = Gtk.Button(label="▶ Run Memory Wizard")
        btn_wizard.get_style_context().add_class("sidebar-btn")
        btn_wizard.connect("clicked", self._on_run_wizard)
        wizard_body.pack_start(btn_wizard, False, False, 0)

        wizard_frame.add(wizard_body)
        inner.pack_start(wizard_frame, False, False, 0)

        self.show_all()
        GLib.idle_add(self._load_projects)
        # Auto-refresh whenever the Memory tab becomes visible. Without this
        # the panel only reads rules + rules_config on init / project change,
        # so any edit done from the `ctx rules` CLI (or another window)
        # leaves the spinners and rules list showing stale values.
        self.connect("map", lambda *_: self._refresh_for_current_project())

    def _refresh_for_current_project(self):
        """Re-read rules + rules_config + logs for the active project.
        Called from map-event so the panel reflects DB truth on every show.
        """
        if self._get_project():
            self._refresh_rules()
            self._refresh_config()
            self._refresh_logs()

    def set_active_tab(self, tab):
        """Per-tab binding: ustaw combo projektu na project_name z taba.

        Tab z claude_config → resolve project_name (basename project_dir
        lub ctx alias) → set combo. Tab bez claude_config → no-op
        (zachowaj poprzednio wybrany).
        """
        if tab is None or getattr(tab, "claude_config", None) is None:
            return
        project_dir = (tab.claude_config or {}).get("project_dir", "")
        if not project_dir:
            return
        # Lazy import — avoid circular
        from bterminal.ctx.helpers import _resolve_ctx_project_name
        target = _resolve_ctx_project_name(project_dir)
        for i, row in enumerate(self._proj_combo.get_model() or []):
            if row[0] == target:
                self._proj_combo.set_active(i)
                return

    # ── Helpers ──────────────────────────────────────────────────────────────

    def _make_section(self, title):
        frame = Gtk.Frame(label=title)
        frame.set_margin_start(6)
        frame.set_margin_end(6)
        frame.set_margin_top(6)
        frame.set_shadow_type(Gtk.ShadowType.ETCHED_IN)
        return frame

    def _load_projects(self, *_):
        self._proj_combo.remove_all()
        try:
            db = sqlite3.connect(CTX_DB)
            rows = db.execute("SELECT name FROM sessions ORDER BY name").fetchall()
            db.close()
            for row in rows:
                self._proj_combo.append_text(row[0])
            if rows:
                self._proj_combo.set_active(0)
        except Exception:
            pass

    def _get_project(self):
        return self._proj_combo.get_active_text() or ""

    def _on_project_changed(self, combo):
        project = combo.get_active_text()
        if project:
            self._current_project = project
            self._refresh_rules()
            self._refresh_config()
            self._refresh_logs()

    def _refresh_rules(self):
        project = self._get_project()
        if not project or not self._rules_store:
            return
        self._rules_store.clear()
        try:
            db = sqlite3.connect(CTX_DB)
            rows = db.execute(
                "SELECT id, rule, enabled FROM rules WHERE project = ? ORDER BY id",
                (project,),
            ).fetchall()
            db.close()
            for row in rows:
                self._rules_store.append([bool(row[2]), row[0], row[1]])
        except Exception:
            pass

    def _refresh_config(self):
        project = self._get_project()
        if not project:
            return
        try:
            db = sqlite3.connect(CTX_DB)
            row = db.execute(
                "SELECT inject_every, refresh_every FROM rules_config WHERE project = ?",
                (project,),
            ).fetchone()
            db.close()
            if row:
                self._spin_inject.set_value(row[0])
                self._spin_refresh.set_value(row[1])
            else:
                self._spin_inject.set_value(100)
                self._spin_refresh.set_value(200)
        except Exception:
            pass

    def _refresh_logs(self):
        project = self._get_project()
        self._logs_store.clear()
        if not project:
            return
        try:
            db = sqlite3.connect(CTX_DB)
            row = db.execute("SELECT work_dir FROM sessions WHERE name = ?", (project,)).fetchone()
            db.close()
            if not row or not row[0]:
                return
            from bterminal import _claude_log_dir  # lazy: still in bterminal.py
            log_dir = _claude_log_dir(row[0])
            if not log_dir.exists():
                return
            files = sorted(log_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True)
            for f in files[:30]:
                mtime = datetime.fromtimestamp(f.stat().st_mtime).strftime("%Y-%m-%d %H:%M")
                size_kb = f.stat().st_size // 1024
                self._logs_store.append([f"{mtime}  {f.name}  ({size_kb} KB)", str(f)])
        except Exception:
            pass

    # ── Event handlers ───────────────────────────────────────────────────────

    def _on_save_config(self, _):
        project = self._get_project()
        if not project:
            return
        inject_every = int(self._spin_inject.get_value())
        refresh_every = int(self._spin_refresh.get_value())
        try:
            subprocess.run(
                ["ctx", "rules", "config", project,
                 "--inject-every", str(inject_every),
                 "--refresh-every", str(refresh_every)],
                check=True, capture_output=True, timeout=5,
            )
            self._cfg_status_lbl.set_text("✓ Saved")
        except Exception as e:
            self._cfg_status_lbl.set_text(f"✗ {e}")
        GLib.timeout_add_seconds(3, lambda: self._cfg_status_lbl.set_text("") or False)

    def _on_add_rule(self, _):
        project = self._get_project()
        rule = self._rule_entry.get_text().strip()
        if not project or not rule:
            return
        try:
            subprocess.run(
                ["ctx", "rules", "add", project, rule],
                check=True, capture_output=True, timeout=5,
            )
            self._rule_entry.set_text("")
            self._refresh_rules()
        except Exception:
            pass

    def _on_remove_rule(self, _):
        project = self._get_project()
        sel = self._rules_tv.get_selection()
        model, it = sel.get_selected()
        if not it:
            return
        rule_id = model[it][1]
        try:
            subprocess.run(
                ["ctx", "rules", "remove", project, str(rule_id)],
                check=True, capture_output=True, timeout=5,
            )
            self._refresh_rules()
        except Exception:
            pass

    def _on_rule_toggled(self, renderer, path):
        project = self._get_project()
        it = self._rules_store.get_iter(path)
        rule_id = self._rules_store[it][1]
        currently_enabled = self._rules_store[it][0]
        subcmd = "disable" if currently_enabled else "enable"
        try:
            subprocess.run(
                ["ctx", "rules", subcmd, project, str(rule_id)],
                check=True, capture_output=True, timeout=5,
            )
            self._rules_store[it][0] = not currently_enabled
        except Exception:
            pass

    def _on_log_activated(self, tv, path, column):
        model = tv.get_model()
        jsonl_path = model[path][1]
        if not jsonl_path or not os.path.exists(jsonl_path):
            return
        self._show_log_dialog(jsonl_path)

    def _show_log_dialog(self, jsonl_path):
        dlg = Gtk.Dialog(
            title=f"Session log: {os.path.basename(jsonl_path)}",
            transient_for=self.app,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dlg.set_default_size(700, 500)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        dlg.connect("response", lambda d, _: d.destroy())

        buf = Gtk.TextBuffer()
        tv = Gtk.TextView(buffer=buf)
        tv.set_editable(False)
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_left_margin(8)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(tv)
        dlg.get_content_area().pack_start(scroll, True, True, 0)

        try:
            result = subprocess.run(
                ["claude_log", "parse", jsonl_path, "--limit", "100"],
                capture_output=True, text=True, timeout=10,
            )
            buf.set_text(result.stdout or "(empty)")
        except Exception as e:
            buf.set_text(f"Error: {e}")

        dlg.show_all()

    def _on_collect_log(self, _):
        """Collect the current active Claude Code session's JSONL, then show newest log."""
        nb = getattr(self.app, "notebook", None)
        if nb is None:
            return
        current = nb.get_nth_page(nb.get_current_page())
        if not current or not getattr(current, "claude_config", None):
            show_info_dialog(self.app, "Collect log",
                             "Switch to an active Claude Code tab first.")
            return
        project_dir = current.claude_config.get("project_dir", "")
        if not project_dir:
            return

        self._collect_status_lbl.set_text("Collecting…")
        stats_bar = getattr(current, "_stats_bar", None)
        jsonl_path = None
        if stats_bar and getattr(stats_bar, "_reader", None):
            jsonl_path = stats_bar._reader._cached
        cmd = ["claude_log", "collect", project_dir]
        if jsonl_path:
            cmd.append(jsonl_path)

        import threading, datetime as _dt
        _log = open("/tmp/bterminal_collect.log", "a")
        _log.write(f"\n=== {_dt.datetime.now()} collect start, cmd={cmd}\n")
        _log.flush()

        def _run():
            error = None
            try:
                r = subprocess.run(cmd, capture_output=True, timeout=15)
                _log.write(f"collect done rc={r.returncode} stderr={r.stderr[:100]}\n"); _log.flush()
            except Exception as e:
                error = str(e)
                _log.write(f"collect exception: {e}\n"); _log.flush()
            GLib.idle_add(_done, error)

        def _done(error):
            try:
                _log.write(f"_done called error={error}\n"); _log.flush()
                if error:
                    self._collect_status_lbl.set_text(f"✗ {error}")
                    GLib.timeout_add_seconds(4, lambda: self._collect_status_lbl.set_text("↑ double-click to view") or False)
                    return False
                _log.write("calling _refresh_logs\n"); _log.flush()
                self._refresh_logs()
                _log.write("_refresh_logs done\n"); _log.flush()
                log_dir = _claude_log_dir(project_dir)
                files = sorted(log_dir.glob("*.jsonl"), key=lambda f: f.stat().st_mtime, reverse=True) if log_dir.exists() else []
                _log.write(f"files found: {len(files)}, log_dir={log_dir}\n"); _log.flush()
                if files:
                    self._collect_status_lbl.set_text(f"✓ {len(files)} logs")
                    GLib.timeout_add_seconds(3, lambda: self._collect_status_lbl.set_text("↑ double-click to view") or False)
                    _log.write(f"calling _show_log_dialog({files[0]})\n"); _log.flush()
                    self._show_log_dialog(str(files[0]))
                    _log.write("_show_log_dialog returned OK\n"); _log.flush()
                else:
                    self._collect_status_lbl.set_text("✗ no logs found")
                    GLib.timeout_add_seconds(3, lambda: self._collect_status_lbl.set_text("↑ double-click to view") or False)
            except Exception as ex:
                import traceback
                _log.write(f"EXCEPTION in _done: {traceback.format_exc()}\n"); _log.flush()
                self._collect_status_lbl.set_text(f"✗ {ex}")
            return False

        threading.Thread(target=_run, daemon=True).start()

    def _refresh_history(self, kind: str):
        project = self._get_project()
        if not project:
            return
        try:
            if kind == "ctx":
                result = subprocess.run(
                    ["ctx", "log", project, "--limit", "40"],
                    capture_output=True, text=True, timeout=5,
                )
            else:
                result = subprocess.run(
                    ["ctx", "log-rules", project, "--limit", "40"],
                    capture_output=True, text=True, timeout=5,
                )
            text = result.stdout.strip() or "(no history yet)"
        except Exception as e:
            text = f"Error: {e}"
        self._hist_view.get_buffer().set_text(text)

    def _on_run_wizard(self, _):
        project = self._get_project()
        if not project:
            show_error_dialog(self.app, "Select a project first.")
            return
        project_dir = ""
        try:
            db = sqlite3.connect(CTX_DB)
            row = db.execute("SELECT work_dir FROM sessions WHERE name = ?", (project,)).fetchone()
            db.close()
            if row and row[0]:
                project_dir = row[0]
        except Exception:
            pass

        cmd = ["memory_wizard", project]
        if project_dir:
            cmd += ["--project-dir", project_dir]

        self.app.open_wizard_tab(project, cmd, on_done=self.refresh)

    def refresh(self):
        project = self._get_project()
        if project:
            self._refresh_rules()
            self._refresh_logs()


