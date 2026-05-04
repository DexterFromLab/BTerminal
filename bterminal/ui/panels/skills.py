"""SkillsPanel — sidebar listing of bundled skills + global default rules

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/panels/panel_skills.py` in a later migration etap.
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
from pathlib import Path

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


class SkillsPanel(Gtk.Box):
    """Panel for managing Claude Code skills (~/.claude/commands/ and .claude/commands/)."""

    # TODO(provider-abstraction): per-provider user_skills_dir.
    # Currently GLOBAL_DIR jest Claude-specific (~/.claude/commands/).
    # Per REQUIREMENTS.md Q35.1 — Claude-only for now. Future refactor:
    #   1. Capability `user_skills_dir: str | None` w providers.json:
    #        claude  → "~/.claude/commands"
    #        aider   → "~/.aider/commands"   (TBD when aider supports skills)
    #        copilot → null                  (no skill concept)
    #   2. SkillsPanel czyta merged view: bundled (defaults/skills/) +
    #      active providers' user dirs (one per provider).
    #   3. Refresh button → re-scan wszystkich katalogów + rerender list.
    #   4. UI: per-skill badge "{provider}" wskazujący źródło (Claude/Aider/...).
    #   5. Test: mock provider z user_skills_dir → assert skills loaded + badge widoczny.
    GLOBAL_DIR = Path.home() / ".claude" / "commands"

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        self.app = app

        header = Gtk.Label(label="Skills")
        header.get_style_context().add_class("sidebar-header")
        header.set_halign(Gtk.Align.START)
        self.pack_start(header, False, False, 0)

        scroll = Gtk.ScrolledWindow()
        scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        scroll.set_vexpand(True)

        inner = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        inner.set_margin_start(8)
        inner.set_margin_end(8)
        inner.set_margin_top(6)
        inner.set_margin_bottom(8)

        # ── Skill list ────────────────────────────────────────────────────
        # columns: scope (str), name (str), path (str), description (str)
        self._store = Gtk.ListStore(str, str, str, str)
        tv = Gtk.TreeView(model=self._store)
        tv.set_headers_visible(True)
        tv.set_size_request(-1, 160)
        tv.set_tooltip_column(3)
        self._tv = tv

        col_scope = Gtk.TreeViewColumn("Scope", Gtk.CellRendererText(), text=0)
        col_scope.set_fixed_width(60)
        tv.append_column(col_scope)

        ren_name = Gtk.CellRendererText()
        ren_name.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_name = Gtk.TreeViewColumn("Skill", ren_name, text=1)
        col_name.set_expand(True)
        tv.append_column(col_name)

        tv.connect("row-activated", self._on_view)

        tv_scroll = Gtk.ScrolledWindow()
        tv_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tv_scroll.set_min_content_height(120)
        tv_scroll.add(tv)
        inner.pack_start(tv_scroll, False, False, 0)

        # ── Location label ────────────────────────────────────────────────
        self._loc_lbl = Gtk.Label(label="")
        self._loc_lbl.get_style_context().add_class("dim-label")
        self._loc_lbl.set_halign(Gtk.Align.START)
        self._loc_lbl.set_ellipsize(Pango.EllipsizeMode.START)
        inner.pack_start(self._loc_lbl, False, False, 0)

        # ── Action buttons ────────────────────────────────────────────────
        btn_row = Gtk.Box(spacing=4)
        btn_view = Gtk.Button(label="View")
        btn_view.get_style_context().add_class("sidebar-btn")
        btn_view.connect("clicked", self._on_view)
        btn_row.pack_start(btn_view, True, True, 0)

        btn_move = Gtk.Button(label="Move ↕")
        btn_move.get_style_context().add_class("sidebar-btn")
        btn_move.set_tooltip_text("Move between global and project")
        btn_move.connect("clicked", self._on_move)
        btn_row.pack_start(btn_move, True, True, 0)

        btn_del = Gtk.Button(label="Delete")
        btn_del.get_style_context().add_class("sidebar-btn")
        btn_del.connect("clicked", self._on_delete)
        btn_row.pack_start(btn_del, True, True, 0)
        inner.pack_start(btn_row, False, False, 0)

        btn_row2 = Gtk.Box(spacing=4)
        btn_new = Gtk.Button(label="+ New skill")
        btn_new.get_style_context().add_class("sidebar-btn")
        btn_new.connect("clicked", self._on_new)
        btn_row2.pack_start(btn_new, True, True, 0)

        btn_refresh = Gtk.Button(label="↺")
        btn_refresh.get_style_context().add_class("sidebar-btn")
        btn_refresh.set_tooltip_text("Refresh list")
        btn_refresh.connect("clicked", lambda _: self._refresh())
        btn_row2.pack_start(btn_refresh, False, False, 0)
        inner.pack_start(btn_row2, False, False, 0)

        scroll.add(inner)
        self.pack_start(scroll, True, True, 0)

        tv.get_selection().connect("changed", self._on_selection_changed)
        self._refresh()

    # ── Public ─────────────────────────────────────────────────────────────

    def set_active_tab(self, tab):
        """Per-tab binding: skills lokalne projektu zależą od claude_config
        aktywnego taba (.claude/commands/). Reload na switch-page."""
        self._refresh()

    # ── Helpers ────────────────────────────────────────────────────────────

    def _project_dir(self):
        nb = getattr(self.app, "notebook", None)
        if nb is None:
            return None
        page = nb.get_nth_page(nb.get_current_page())
        if page and getattr(page, "claude_config", None):
            pd = page.claude_config.get("project_dir", "")
            return pd or None
        return None

    def _project_commands_dir(self):
        pd = self._project_dir()
        if pd:
            return Path(pd) / ".claude" / "commands"
        return None

    def _skill_description(self, path: Path) -> str:
        try:
            text = path.read_text(errors="replace")
            for line in text.splitlines():
                line = line.strip()
                if line.startswith("# "):
                    return line[2:].strip()
                if line and not line.startswith("---"):
                    return line[:80]
        except Exception:
            pass
        return ""

    def _bundled_names(self) -> set:
        try:
            return {f.stem for f in _BUNDLED_SKILLS_DIR.glob("*.md")}
        except Exception:
            return set()

    def _refresh(self):
        self._store.clear()
        seen = set()
        bundled = self._bundled_names()

        def _add_dir(directory: Path, scope: str):
            if not directory or not directory.exists():
                return
            for f in sorted(directory.glob("*.md")):
                name = f.stem
                key = (scope, name)
                if key in seen:
                    continue
                seen.add(key)
                desc = self._skill_description(f)
                label = ("📦" if name in bundled else scope)
                self._store.append([label, name, str(f), desc])

        _add_dir(self.GLOBAL_DIR, "🌍 global")
        proj_dir = self._project_commands_dir()
        _add_dir(proj_dir, "📁 project")

        self._loc_lbl.set_text(
            f"global: {self.GLOBAL_DIR}"
            + (f"\nproject: {proj_dir}" if proj_dir else "")
        )

    def _selected(self):
        model, it = self._tv.get_selection().get_selected()
        if not it:
            return None, None, None
        return model[it][0], model[it][1], model[it][2]  # scope, name, path

    def _on_selection_changed(self, sel):
        scope, name, path = self._selected()
        if path:
            self._loc_lbl.set_text(path)

    # ── Actions ────────────────────────────────────────────────────────────

    def _on_view(self, *_):
        scope, name, path = self._selected()
        if not path:
            return
        try:
            content = Path(path).read_text(errors="replace")
        except Exception as e:
            content = f"Error reading file: {e}"

        dlg = Gtk.Dialog(
            title=f"/{name}  ({scope})",
            transient_for=self.app,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dlg.set_default_size(680, 520)
        dlg.add_button("Close", Gtk.ResponseType.CLOSE)
        dlg.connect("response", lambda d, _: d.destroy())

        buf = Gtk.TextBuffer()
        buf.set_text(content)
        tv = Gtk.TextView(buffer=buf)
        tv.set_editable(False)
        tv.set_monospace(True)
        tv.set_wrap_mode(Gtk.WrapMode.WORD_CHAR)
        tv.set_left_margin(8)

        sw = Gtk.ScrolledWindow()
        sw.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        sw.add(tv)
        dlg.get_content_area().pack_start(sw, True, True, 0)
        dlg.show_all()

    def _on_move(self, _):
        scope, name, path = self._selected()
        if not path:
            return
        src = Path(path)

        if "global" in scope:
            dest_dir = self._project_commands_dir()
            if not dest_dir:
                show_info_dialog(self.app, "Move skill",
                                 "No active Claude Code tab with a project directory.")
                return
            dest_label = "project"
        else:
            dest_dir = self.GLOBAL_DIR
            dest_label = "global"

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / src.name

        if dest.exists():
            show_info_dialog(self.app, "Move skill",
                             f"/{name} already exists in {dest_label}.")
            return

        try:
            import shutil as _shutil
            _shutil.move(str(src), str(dest))
        except Exception as e:
            show_error_dialog(self.app, f"Move failed: {e}")
            return

        self._refresh()

    def _on_delete(self, _):
        scope, name, path = self._selected()
        if not path:
            return

        dlg = Gtk.MessageDialog(
            transient_for=self.app,
            modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete /{name}?",
        )
        dlg.format_secondary_text(f"{path}")
        resp = dlg.run()
        dlg.destroy()
        if resp != Gtk.ResponseType.YES:
            return

        try:
            Path(path).unlink()
        except Exception as e:
            show_error_dialog(self.app, f"Delete failed: {e}")
            return

        self._refresh()

    def _on_new(self, _):
        # Ask name and scope
        dlg = Gtk.Dialog(
            title="New skill",
            transient_for=self.app,
            flags=Gtk.DialogFlags.DESTROY_WITH_PARENT,
        )
        dlg.set_default_size(400, -1)
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OK, Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)

        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(8)
        box.set_margin_bottom(8)

        name_row = Gtk.Box(spacing=8)
        name_row.pack_start(Gtk.Label(label="Name:"), False, False, 0)
        name_entry = Gtk.Entry()
        name_entry.set_placeholder_text("my-skill")
        name_entry.set_activates_default(True)
        name_row.pack_start(name_entry, True, True, 0)
        box.pack_start(name_row, False, False, 0)

        scope_row = Gtk.Box(spacing=8)
        scope_row.pack_start(Gtk.Label(label="Location:"), False, False, 0)
        radio_global = Gtk.RadioButton.new_with_label(None, "Global")
        radio_proj   = Gtk.RadioButton.new_with_label_from_widget(radio_global, "Project")
        scope_row.pack_start(radio_global, False, False, 0)
        scope_row.pack_start(radio_proj, False, False, 0)
        box.pack_start(scope_row, False, False, 0)

        box.show_all()
        resp = dlg.run()
        name = name_entry.get_text().strip().replace(" ", "-")
        use_project = radio_proj.get_active()
        dlg.destroy()

        if resp != Gtk.ResponseType.OK or not name:
            return

        if use_project:
            dest_dir = self._project_commands_dir()
            if not dest_dir:
                show_info_dialog(self.app, "New skill",
                                 "No active Claude Code tab with a project directory.")
                return
        else:
            dest_dir = self.GLOBAL_DIR

        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{name}.md"

        if not dest.exists():
            dest.write_text(
                f"# /skill: {name}\n\n"
                f"## When to invoke\n\n"
                f"Describe when Claude should use this skill.\n\n"
                f"## Flow\n\n"
                f"Step-by-step instructions for Claude.\n"
            )

        self._refresh()

        # Open in editor
        try:
            subprocess.Popen(["xdg-open", str(dest)])
        except Exception:
            pass


