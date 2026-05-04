"""ConsultPanel — manage consult API key + model registry, send queries

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/panels/panel_consult.py` in a later migration etap.
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
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, Vte
import urllib.error
import urllib.request

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
from bterminal.models import ConsultManager


class ConsultPanel(Gtk.Box):
    """Sidebar panel for managing external AI model consultation via OpenRouter."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self.manager = ConsultManager()

        # ── API Key section ──
        key_box = Gtk.Box(spacing=4)
        key_box.set_border_width(6)

        key_label = Gtk.Label(label="API Key:")
        key_label.set_xalign(0)
        key_box.pack_start(key_label, False, False, 0)

        self.key_entry = Gtk.Entry()
        self.key_entry.set_visibility(False)
        self.key_entry.set_text(self.manager.get_api_key())
        self.key_entry.set_placeholder_text("sk-or-...")
        key_box.pack_start(self.key_entry, True, True, 0)

        eye_btn = Gtk.ToggleButton(label="Show")
        eye_btn.get_style_context().add_class("sidebar-btn")
        eye_btn.set_relief(Gtk.ReliefStyle.NONE)
        eye_btn.connect(
            "toggled", lambda b: self.key_entry.set_visibility(b.get_active())
        )
        key_box.pack_start(eye_btn, False, False, 0)

        save_key_btn = Gtk.Button(label="Save")
        save_key_btn.get_style_context().add_class("sidebar-btn")
        save_key_btn.connect("clicked", self._on_save_key)
        key_box.pack_start(save_key_btn, False, False, 0)

        self.pack_start(key_box, False, False, 0)

        # ── Separator ──
        self.pack_start(Gtk.Separator(), False, False, 0)

        # ── Default model label ──
        self.default_label = Gtk.Label()
        self.default_label.set_xalign(0)
        self.default_label.set_margin_start(8)
        self.default_label.set_margin_top(4)
        self.default_label.set_margin_bottom(4)
        self.pack_start(self.default_label, False, False, 0)

        # ── Model list ──
        # Columns: enabled(bool), default_star(str), name(str), model_id(str)
        self.store = Gtk.ListStore(bool, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(False)
        self.tree.set_activate_on_single_click(False)

        # Toggle column
        toggle_renderer = Gtk.CellRendererToggle()
        toggle_renderer.connect("toggled", self._on_toggle)
        col_toggle = Gtk.TreeViewColumn("", toggle_renderer, active=0)
        col_toggle.set_min_width(30)
        self.tree.append_column(col_toggle)

        # Star + name column
        col_main = Gtk.TreeViewColumn()

        cell_star = Gtk.CellRendererText()
        col_main.pack_start(cell_star, False)
        col_main.add_attribute(cell_star, "text", 1)
        col_main.add_attribute(cell_star, "foreground", 1)
        # Use a cell data func to color the star
        col_main.set_cell_data_func(
            cell_star,
            lambda col, cell, model, it, _: cell.set_property(
                "foreground", CATPPUCCIN["yellow"] if model[it][1] else None
            ),
        )

        cell_name = Gtk.CellRendererText()
        cell_name.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_main.pack_start(cell_name, True)
        col_main.add_attribute(cell_name, "text", 2)

        self.tree.append_column(col_main)

        tree_scroll = Gtk.ScrolledWindow()
        tree_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tree_scroll.add(self.tree)
        self.pack_start(tree_scroll, True, True, 0)

        # ── Buttons row 1 ──
        btn_box = Gtk.Box(spacing=4)
        btn_box.set_border_width(6)

        btn_default = Gtk.Button(label="Set Default")
        btn_default.get_style_context().add_class("sidebar-btn")
        btn_default.connect("clicked", self._on_set_default)
        btn_box.pack_start(btn_default, True, True, 0)

        btn_add = Gtk.Button(label="Add")
        btn_add.get_style_context().add_class("sidebar-btn")
        btn_add.connect("clicked", self._on_add_model)
        btn_box.pack_start(btn_add, True, True, 0)

        btn_remove = Gtk.Button(label="Remove")
        btn_remove.get_style_context().add_class("sidebar-btn")
        btn_remove.connect("clicked", self._on_remove_model)
        btn_box.pack_start(btn_remove, True, True, 0)

        self.pack_start(btn_box, False, False, 0)

        # ── Buttons row 2 ──
        btn_box2 = Gtk.Box(spacing=4)
        btn_box2.set_border_width(6)
        btn_box2.set_margin_top(0)

        self.btn_fetch = Gtk.Button(label="Fetch Models from OpenRouter")
        self.btn_fetch.get_style_context().add_class("sidebar-btn")
        self.btn_fetch.connect("clicked", self._on_fetch_models)
        btn_box2.pack_start(self.btn_fetch, True, True, 0)

        self.pack_start(btn_box2, False, False, 0)

        # ── Tribunal section ──
        self.pack_start(Gtk.Separator(), False, False, 4)

        tribunal_header = Gtk.Label()
        tribunal_header.set_markup(
            f'<span foreground="{CATPPUCCIN["subtext0"]}">'
            f"Multi-Agent Debate</span>"
        )
        tribunal_header.set_xalign(0)
        tribunal_header.set_margin_start(8)
        tribunal_header.set_margin_top(2)
        self.pack_start(tribunal_header, False, False, 0)

        # Role dropdowns
        self.tribunal_combos = {}
        roles_grid = Gtk.Grid()
        roles_grid.set_column_spacing(4)
        roles_grid.set_row_spacing(2)
        roles_grid.set_border_width(6)

        FIXED_OPUS = "claude-code/opus"
        for i, role in enumerate(("analyst", "advocate", "critic", "arbiter")):
            lbl = Gtk.Label(label=f"{role.title()}:")
            lbl.set_xalign(1)
            lbl.set_margin_end(4)
            roles_grid.attach(lbl, 0, i, 1, 1)

            if role in ("analyst", "arbiter"):
                fixed_lbl = Gtk.Label(label="[CC] Claude Opus  (fixed)")
                fixed_lbl.set_xalign(0)
                fixed_lbl.get_style_context().add_class("dim-label")
                roles_grid.attach(fixed_lbl, 1, i, 1, 1)
                self.tribunal_combos[role] = None  # sentinel — not user-selectable
            else:
                combo = Gtk.ComboBoxText()
                combo.set_hexpand(True)
                roles_grid.attach(combo, 1, i, 1, 1)
                self.tribunal_combos[role] = combo

        self.pack_start(roles_grid, False, False, 0)

        # Rounds spinner
        rounds_box = Gtk.Box(spacing=4)
        rounds_box.set_border_width(6)
        rounds_lbl = Gtk.Label(label="Rounds:")
        rounds_lbl.set_xalign(0)
        rounds_box.pack_start(rounds_lbl, False, False, 0)
        self.rounds_spin = Gtk.SpinButton.new_with_range(1, 6, 1)
        self.rounds_spin.set_value(3)
        rounds_box.pack_start(self.rounds_spin, False, False, 0)
        self.single_pass_check = Gtk.CheckButton(label="Single pass")
        rounds_box.pack_start(self.single_pass_check, False, False, 4)
        self.pack_start(rounds_box, False, False, 0)

        # Project directory
        proj_lbl = Gtk.Label()
        proj_lbl.set_markup(
            f'<span foreground="{CATPPUCCIN["subtext0"]}">Project dir:</span>'
        )
        proj_lbl.set_xalign(0)
        proj_lbl.set_margin_start(8)
        self.pack_start(proj_lbl, False, False, 0)

        proj_box = Gtk.Box(spacing=4)
        proj_box.set_border_width(6)
        self.project_combo = Gtk.ComboBoxText()
        self.project_combo.set_hexpand(True)
        self.project_combo.connect("changed", self._on_project_combo_changed)
        proj_box.pack_start(self.project_combo, True, True, 0)
        self.pack_start(proj_box, False, False, 0)

        dir_entry_box = Gtk.Box(spacing=4)
        dir_entry_box.set_border_width(6)
        dir_entry_box.set_margin_top(0)
        self.project_dir_entry = Gtk.Entry()
        self.project_dir_entry.set_placeholder_text("Override path or pick from dropdown")
        dir_entry_box.pack_start(self.project_dir_entry, True, True, 0)
        browse_btn = Gtk.Button(label="...")
        browse_btn.set_tooltip_text("Browse")
        browse_btn.get_style_context().add_class("sidebar-btn")
        browse_btn.connect("clicked", self._on_browse_project_dir)
        dir_entry_box.pack_start(browse_btn, False, False, 0)
        self.pack_start(dir_entry_box, False, False, 0)

        self._refresh_project_combo()

        # Problem text
        problem_lbl = Gtk.Label()
        problem_lbl.set_markup(
            f'<span foreground="{CATPPUCCIN["subtext0"]}">Problem:</span>'
        )
        problem_lbl.set_xalign(0)
        problem_lbl.set_margin_start(8)
        self.pack_start(problem_lbl, False, False, 0)

        problem_scroll = Gtk.ScrolledWindow()
        problem_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        problem_scroll.set_min_content_height(60)
        problem_scroll.set_max_content_height(120)
        problem_scroll.set_border_width(6)
        self.problem_text = Gtk.TextView()
        self.problem_text.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        self.problem_text.set_left_margin(4)
        self.problem_text.set_right_margin(4)
        self.problem_text.set_top_margin(4)
        self.problem_text.set_bottom_margin(4)
        problem_scroll.add(self.problem_text)
        self.pack_start(problem_scroll, False, False, 0)

        # Run + Save buttons
        run_box = Gtk.Box(spacing=4)
        run_box.set_border_width(6)
        self.btn_save_preset = Gtk.Button(label="Save")
        self.btn_save_preset.set_tooltip_text("Save tribunal settings for selected project")
        self.btn_save_preset.get_style_context().add_class("sidebar-btn")
        self.btn_save_preset.connect("clicked", self._on_save_preset)
        run_box.pack_start(self.btn_save_preset, False, False, 0)
        self.btn_debate = Gtk.Button(label="Run Debate")
        self.btn_debate.get_style_context().add_class("sidebar-btn")
        self.btn_debate.connect("clicked", self._on_run_debate)
        run_box.pack_start(self.btn_debate, True, True, 0)
        self.pack_start(run_box, False, False, 0)

        # ── CLI info ──
        info_label = Gtk.Label()
        info_label.set_markup(
            f'<span size="small" foreground="{CATPPUCCIN["overlay1"]}">'
            "CLI: consult \"q\" | consult debate \"problem\" | consult models"
            "</span>"
        )
        info_label.set_xalign(0)
        info_label.set_line_wrap(True)
        info_label.set_margin_start(8)
        info_label.set_margin_bottom(6)
        self.pack_start(info_label, False, False, 0)

        self.refresh()

    def refresh(self):
        """Reload model list from config."""
        self.store.clear()
        self.manager.load()
        default = self.manager.get_default_model()
        models = self.manager.get_models()

        default_name = models.get(default, {}).get("name", default)
        self.default_label.set_markup(
            f'<span foreground="{CATPPUCCIN["subtext0"]}">'
            f"Default: </span>"
            f'<span foreground="{CATPPUCCIN["yellow"]}">'
            f"{default_name}</span>"
        )

        # Sort: enabled first, then by source (openrouter first), then alphabetically
        sorted_ids = sorted(
            models.keys(),
            key=lambda m: (
                not models[m].get("enabled", False),
                0 if models[m].get("source", "openrouter") == "openrouter" else 1,
                m,
            ),
        )

        for mid in sorted_ids:
            info = models[mid]
            star = " \u2605 " if mid == default else "   "
            source = info.get("source", "openrouter")
            src_tag = "[CC]" if source == "claude-code" else "[OR]"
            name = f"{src_tag} {info.get('name', mid)}  ({mid})"
            self.store.append([info.get("enabled", False), star, name, mid])

        # Refresh tribunal dropdowns
        enabled_models = [
            mid for mid in sorted_ids if models[mid].get("enabled", False)
        ]
        tribunal_cfg = self.manager.config.get("tribunal", {})

        for role, combo in self.tribunal_combos.items():
            if combo is None:
                continue  # fixed to claude-code/opus
            combo.remove_all()
            saved = tribunal_cfg.get(f"{role}_model", "")
            active_idx = 0
            for i, mid in enumerate(enabled_models):
                source = models[mid].get("source", "openrouter")
                src_tag = "[CC]" if source == "claude-code" else "[OR]"
                name = models[mid].get("name", mid)
                combo.append(mid, f"{src_tag} {name}")
                if mid == saved:
                    active_idx = i
            if enabled_models:
                combo.set_active(active_idx)

        max_rounds = tribunal_cfg.get("max_rounds", 3)
        self.rounds_spin.set_value(max_rounds)

        # Refresh project dropdown
        self._refresh_project_combo()

    def _on_save_key(self, btn):
        key = self.key_entry.get_text().strip()
        self.manager.set_api_key(key)

    def _on_toggle(self, renderer, path):
        it = self.store.get_iter(path)
        enabled = not self.store[it][0]
        model_id = self.store[it][3]
        self.store[it][0] = enabled
        self.manager.set_model_enabled(model_id, enabled)
        self.refresh()

    def _on_set_default(self, btn):
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        if not it:
            return
        model_id = self.store[it][3]
        self.manager.set_default_model(model_id)
        self.refresh()

    def _on_add_model(self, btn):
        dlg = Gtk.Dialog(
            title="Add Model",
            transient_for=self.app,
            modal=True,
            destroy_with_parent=True,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)

        lbl_id = Gtk.Label(label="Model ID (e.g. google/gemini-2.5-pro):")
        lbl_id.set_xalign(0)
        box.add(lbl_id)
        entry_id = Gtk.Entry()
        entry_id.set_placeholder_text("provider/model-name")
        box.add(entry_id)

        lbl_name = Gtk.Label(label="Display Name:")
        lbl_name.set_xalign(0)
        box.add(lbl_name)
        entry_name = Gtk.Entry()
        entry_name.set_placeholder_text("Model Name")
        box.add(entry_name)

        dlg.show_all()

        while True:
            resp = dlg.run()
            if resp != Gtk.ResponseType.OK:
                break
            mid = entry_id.get_text().strip()
            name = entry_name.get_text().strip() or mid
            if not mid:
                continue
            self.manager.add_model(mid, name)
            self.refresh()
            break
        dlg.destroy()

    def _on_remove_model(self, btn):
        sel = self.tree.get_selection()
        model, it = sel.get_selected()
        if not it:
            return
        model_id = self.store[it][3]
        self.manager.remove_model(model_id)
        self.refresh()

    def _refresh_project_combo(self):
        """Populate project dropdown from Claude sessions with project_dir."""
        self.project_combo.remove_all()
        self.project_combo.append("", "(none)")
        seen = set()
        for s in self.app.claude_manager.all():
            pdir = s.get("project_dir", "").strip()
            if pdir and pdir not in seen:
                seen.add(pdir)
                name = s.get("name", "") or os.path.basename(pdir.rstrip("/"))
                self.project_combo.append(pdir, f"{name}  ({pdir})")
        self.project_combo.set_active(0)

    def _on_project_combo_changed(self, combo):
        """When a project is selected from dropdown, fill the entry and load preset."""
        pdir = combo.get_active_id() or ""
        self.project_dir_entry.set_text(pdir)
        if pdir:
            self._load_project_preset(pdir)

    def _load_project_preset(self, project_dir):
        """Load saved tribunal settings for the given project dir into UI."""
        preset = self.manager.get_project_preset(project_dir)
        if not preset:
            return
        for role, combo in self.tribunal_combos.items():
            saved = preset.get(f"{role}_model", "")
            if saved:
                combo.set_active_id(saved)
        if "max_rounds" in preset:
            self.rounds_spin.set_value(preset["max_rounds"])
        if "single_pass" in preset:
            self.single_pass_check.set_active(preset["single_pass"])

    def _on_save_preset(self, btn):
        """Save current tribunal settings for the selected project."""
        pdir = self.project_dir_entry.get_text().strip()
        if not pdir:
            dlg = Gtk.MessageDialog(
                transient_for=self.app,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Select a project directory first.",
            )
            dlg.run()
            dlg.destroy()
            return

        models = {}
        for role, combo in self.tribunal_combos.items():
            mid = combo.get_active_id()
            if mid:
                models[f"{role}_model"] = mid

        preset = {
            **models,
            "max_rounds": int(self.rounds_spin.get_value()),
            "single_pass": self.single_pass_check.get_active(),
        }
        self.manager.save_project_preset(pdir, preset)

    def _on_browse_project_dir(self, btn):
        """Open file chooser for project directory."""
        dlg = Gtk.FileChooserDialog(
            title="Select project directory",
            parent=self.app,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OPEN, Gtk.ResponseType.OK,
        )
        current = self.project_dir_entry.get_text().strip()
        if current and os.path.isdir(current):
            dlg.set_current_folder(current)
        if dlg.run() == Gtk.ResponseType.OK:
            self.project_dir_entry.set_text(dlg.get_filename())
        dlg.destroy()

    def _get_debate_project_dir(self):
        """Return project dir for debate: entry overrides combo, fallback to HOME."""
        path = self.project_dir_entry.get_text().strip()
        if path and os.path.isdir(path):
            return path
        return os.environ.get("HOME", "/")

    def _on_run_debate(self, btn):
        """Launch a tribunal debate in a new terminal tab."""
        buf = self.problem_text.get_buffer()
        problem = buf.get_text(buf.get_start_iter(), buf.get_end_iter(), False).strip()
        if not problem:
            dlg = Gtk.MessageDialog(
                transient_for=self.app,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Enter a problem statement first.",
            )
            dlg.run()
            dlg.destroy()
            return

        # Gather selected models — analyst/arbiter always fixed to Claude Opus
        FIXED_OPUS = "claude-code/opus"
        models = {"analyst": FIXED_OPUS, "arbiter": FIXED_OPUS}
        for role in ("advocate", "critic"):
            combo = self.tribunal_combos[role]
            mid = combo.get_active_id() if combo else None
            if mid:
                models[role] = mid

        if len(models) < 4:
            dlg = Gtk.MessageDialog(
                transient_for=self.app,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Select a model for Advocate and Critic.",
            )
            dlg.run()
            dlg.destroy()
            return

        # Check API key only if any OpenRouter models are used
        needs_api_key = any(
            not mid.startswith("claude-code/") for mid in models.values()
        )
        if needs_api_key and not self.manager.get_api_key():
            dlg = Gtk.MessageDialog(
                transient_for=self.app,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Set an API key first (needed for OpenRouter models).",
            )
            dlg.run()
            dlg.destroy()
            return

        # Save tribunal config (global + per-project)
        self.manager.load()
        if "tribunal" not in self.manager.config:
            self.manager.config["tribunal"] = {}
        self.manager.config["tribunal"]["analyst_model"] = models["analyst"]
        self.manager.config["tribunal"]["advocate_model"] = models["advocate"]
        self.manager.config["tribunal"]["critic_model"] = models["critic"]
        self.manager.config["tribunal"]["arbiter_model"] = models["arbiter"]
        self.manager.config["tribunal"]["max_rounds"] = int(self.rounds_spin.get_value())
        self.manager.save()

        # Auto-save per-project preset
        pdir = self.project_dir_entry.get_text().strip()
        if pdir:
            self._on_save_preset(None)

        # Build command
        rounds = int(self.rounds_spin.get_value())
        single = self.single_pass_check.get_active()

        # Escape problem for shell
        escaped = problem.replace("'", "'\\''")
        cmd = (
            f"consult debate '{escaped}'"
            f" --analyst {models['analyst']}"
            f" --advocate {models['advocate']}"
            f" --critic {models['critic']}"
            f" --arbiter {models['arbiter']}"
            f" --rounds {rounds}"
        )
        if single:
            cmd += " --single-pass"

        script = f"{cmd}\nexec bash\n"

        # Open new terminal tab
        from bterminal import TerminalTab  # lazy: TerminalTab still in bterminal.py
        tab = TerminalTab(self.app)
        label = self.app._build_tab_label("Tribunal", tab)
        idx = self.app.notebook.append_page(tab, label)
        self.app.notebook.set_current_page(idx)
        self.app.notebook.set_tab_reorderable(tab, True)

        project_dir = self._get_debate_project_dir()

        tab.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            project_dir,
            ["/bin/bash", "-c", script],
            None,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            None,
        )

    def _on_fetch_models(self, btn):
        """Fetch available models from OpenRouter in background thread."""
        api_key = self.manager.get_api_key()
        if not api_key:
            dlg = Gtk.MessageDialog(
                transient_for=self.app,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="Set an API key first.",
            )
            dlg.run()
            dlg.destroy()
            return

        btn.set_sensitive(False)
        btn.set_label("Fetching...")

        def fetch():
            try:
                req = urllib.request.Request(
                    "https://openrouter.ai/api/v1/models",
                    headers={"Authorization": f"Bearer {api_key}"},
                )
                with urllib.request.urlopen(req, timeout=30) as resp:
                    data = json.loads(resp.read().decode())
                models = data.get("data", [])
                GLib.idle_add(self._show_model_picker, models)
            except Exception as e:
                GLib.idle_add(self._fetch_error, str(e))
            finally:
                GLib.idle_add(self._fetch_done)

        threading.Thread(target=fetch, daemon=True).start()

    def _fetch_done(self):
        self.btn_fetch.set_sensitive(True)
        self.btn_fetch.set_label("Fetch Models from OpenRouter")

    def _fetch_error(self, msg):
        dlg = Gtk.MessageDialog(
            transient_for=self.app,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=f"Fetch failed: {msg}",
        )
        dlg.run()
        dlg.destroy()

    def _show_model_picker(self, models):
        """Show dialog for selecting models from OpenRouter catalog."""
        dlg = Gtk.Dialog(
            title="Select Models from OpenRouter",
            transient_for=self.app,
            modal=True,
        )
        dlg.set_default_size(550, 500)
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            "Add Selected", Gtk.ResponseType.OK,
        )

        box = dlg.get_content_area()
        box.set_spacing(4)

        # Search entry
        search = Gtk.SearchEntry()
        search.set_placeholder_text("Filter models...")
        search.set_margin_start(8)
        search.set_margin_end(8)
        search.set_margin_top(4)
        box.pack_start(search, False, False, 0)

        # Info label
        info = Gtk.Label()
        info.set_markup(
            f'<span size="small" foreground="{CATPPUCCIN["overlay1"]}">'
            f"{len(models)} models available. Check ones to add.</span>"
        )
        info.set_xalign(0)
        info.set_margin_start(8)
        box.pack_start(info, False, False, 0)

        # Model list: selected(bool), name(str), id(str), pricing(str)
        pick_store = Gtk.ListStore(bool, str, str, str)
        existing = set(self.manager.get_models().keys())

        for m in sorted(models, key=lambda x: x.get("id", "")):
            mid = m.get("id", "")
            name = m.get("name", mid)
            pricing = m.get("pricing", {})
            price_str = ""
            if pricing:
                try:
                    pp = float(pricing.get("prompt", "0")) * 1_000_000
                    cp = float(pricing.get("completion", "0")) * 1_000_000
                    price_str = f"${pp:.2f} / ${cp:.2f} per 1M"
                except (ValueError, TypeError):
                    pass
            pick_store.append([mid in existing, name, mid, price_str])

        # Filterable model
        filter_model = pick_store.filter_new()

        def visible_func(model, it, _data):
            text = search.get_text().lower()
            if not text:
                return True
            return text in model[it][1].lower() or text in model[it][2].lower()

        filter_model.set_visible_func(visible_func)
        search.connect("search-changed", lambda _: filter_model.refilter())

        tree = Gtk.TreeView(model=filter_model)
        tree.set_headers_visible(True)

        toggle = Gtk.CellRendererToggle()

        def on_pick_toggle(_renderer, path):
            real_it = filter_model.convert_iter_to_child_iter(
                filter_model.get_iter(path)
            )
            pick_store[real_it][0] = not pick_store[real_it][0]

        toggle.connect("toggled", on_pick_toggle)
        col_sel = Gtk.TreeViewColumn("", toggle, active=0)
        col_sel.set_min_width(30)
        tree.append_column(col_sel)

        cell_name = Gtk.CellRendererText()
        cell_name.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_name = Gtk.TreeViewColumn("Model", cell_name, text=1)
        col_name.set_expand(True)
        col_name.set_sort_column_id(1)
        tree.append_column(col_name)

        cell_id = Gtk.CellRendererText()
        cell_id.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_id = Gtk.TreeViewColumn("ID", cell_id, text=2)
        col_id.set_min_width(150)
        tree.append_column(col_id)

        cell_price = Gtk.CellRendererText()
        col_price = Gtk.TreeViewColumn("Price", cell_price, text=3)
        col_price.set_min_width(130)
        tree.append_column(col_price)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scroll.add(tree)
        box.pack_start(scroll, True, True, 0)

        dlg.show_all()

        if dlg.run() == Gtk.ResponseType.OK:
            added = 0
            it = pick_store.get_iter_first()
            while it:
                if pick_store[it][0]:
                    mid = pick_store[it][2]
                    name = pick_store[it][1]
                    if mid not in existing:
                        self.manager.add_model(mid, name, enabled=True, source="openrouter")
                        added += 1
                it = pick_store.iter_next(it)
            if added:
                self.refresh()

        dlg.destroy()


