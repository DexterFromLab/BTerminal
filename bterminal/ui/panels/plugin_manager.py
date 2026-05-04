"""PluginManagerPanel — sidebar panel for managing in-process GTK plugins.

Lists discovered plugins from PLUGINS_DIR, shows enable/disable toggle,
load/unload via the app's _hot_load_plugin / _hot_unload_plugin methods,
and "Install from .py" / "Open Plugins Directory" actions.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/panels/plugin_manager.py` in a later migration etap.
"""

import importlib.util
import json
import os
import shutil

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gtk, Pango
import tempfile

from bterminal.config import (
    CATPPUCCIN,
    CONFIG_DIR,
    PLUGINS_CONFIG_FILE,
    PLUGINS_DIR,
    show_error_dialog,
    show_info_dialog,
)


class PluginManagerPanel(Gtk.Box):
    """Panel for managing BTerminal plugins."""

    def __init__(self, app):
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        # Aktywny tab — ustawiany przez App._on_switch_page. Gdy ustawiony
        # i ma claude_config: checkboxy reprezentują tab.enabled_plugins
        # (per-projekt). Klik toggle modyfikuje tab + persist do session
        # configu. Gdy None lub SSH tab: globalny config (jak wcześniej).
        self._active_tab = None

        # ── Header with active-tab indicator ──
        self._scope_lbl = Gtk.Label(xalign=0)
        self._scope_lbl.set_margin_start(6)
        self._scope_lbl.set_margin_top(4)
        self._scope_lbl.set_margin_bottom(2)
        self._scope_lbl.get_style_context().add_class("dim-label")
        self.pack_start(self._scope_lbl, False, False, 0)

        # ── Plugin list (TreeView) ──
        # Columns: enabled(bool), name(str), version(str), author(str), status(str), mod_name(str)
        self.store = Gtk.ListStore(bool, str, str, str, str, str)
        self.tree = Gtk.TreeView(model=self.store)
        self.tree.set_headers_visible(True)

        # Enabled toggle column
        toggle_renderer = Gtk.CellRendererToggle()
        toggle_renderer.connect("toggled", self._on_enabled_toggled)
        col_enabled = Gtk.TreeViewColumn("", toggle_renderer, active=0)
        col_enabled.set_min_width(30)
        col_enabled.set_max_width(30)
        self.tree.append_column(col_enabled)

        # Name column
        cell_name = Gtk.CellRendererText()
        cell_name.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_name = Gtk.TreeViewColumn("Name", cell_name, text=1)
        col_name.set_expand(True)
        self.tree.append_column(col_name)

        # Version column
        cell_ver = Gtk.CellRendererText()
        col_ver = Gtk.TreeViewColumn("Ver", cell_ver, text=2)
        col_ver.set_min_width(40)
        col_ver.set_max_width(60)
        self.tree.append_column(col_ver)

        # Author column
        cell_author = Gtk.CellRendererText()
        cell_author.set_property("ellipsize", Pango.EllipsizeMode.END)
        col_author = Gtk.TreeViewColumn("Author", cell_author, text=3)
        col_author.set_min_width(60)
        col_author.set_max_width(100)
        self.tree.append_column(col_author)

        # Status column (Loaded / Disabled / Error)
        cell_status = Gtk.CellRendererText()
        col_status = Gtk.TreeViewColumn("Status", cell_status, text=4)
        col_status.set_min_width(50)
        col_status.set_max_width(70)
        col_status.set_cell_data_func(cell_status, self._style_status_cell)
        self.tree.append_column(col_status)

        tree_scroll = Gtk.ScrolledWindow()
        tree_scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        tree_scroll.add(self.tree)

        # ── Detail area ──
        detail_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=2)
        detail_box.set_border_width(6)
        self.detail_label = Gtk.Label()
        self.detail_label.set_xalign(0)
        self.detail_label.set_yalign(0)
        self.detail_label.set_line_wrap(True)
        self.detail_label.set_selectable(True)
        self.detail_label.set_markup(
            f'<span foreground="{CATPPUCCIN["overlay1"]}">Select a plugin to see details</span>'
        )
        detail_box.pack_start(self.detail_label, True, True, 0)

        # VPaned: tree on top, detail on bottom
        paned = Gtk.VPaned()
        paned.pack1(tree_scroll, resize=True, shrink=True)
        paned.pack2(detail_box, resize=False, shrink=False)
        self.pack_start(paned, True, True, 0)

        self.tree.get_selection().connect("changed", self._on_selection_changed)

        # ── Action buttons ──
        btn_box = Gtk.Box(spacing=4)
        btn_box.set_border_width(6)

        btn_add = Gtk.Button(label="Add File")
        btn_add.get_style_context().add_class("sidebar-btn")
        btn_add.connect("clicked", lambda _: self._on_add_file())
        btn_box.pack_start(btn_add, True, True, 0)

        btn_add_dir = Gtk.Button(label="Add Folder")
        btn_add_dir.get_style_context().add_class("sidebar-btn")
        btn_add_dir.connect("clicked", lambda _: self._on_add_folder())
        btn_box.pack_start(btn_add_dir, True, True, 0)

        btn_remove = Gtk.Button(label="Remove")
        btn_remove.get_style_context().add_class("sidebar-btn")
        btn_remove.connect("clicked", lambda _: self._on_remove_plugin())
        btn_box.pack_start(btn_remove, True, True, 0)

        btn_refresh = Gtk.Button(label="\u21bb")
        btn_refresh.get_style_context().add_class("sidebar-btn")
        btn_refresh.set_tooltip_text("Refresh")
        btn_refresh.connect("clicked", lambda _: self.refresh())
        btn_box.pack_start(btn_refresh, False, False, 0)

        self.pack_start(btn_box, False, False, 0)

    # ── Config persistence ──

    def _load_config(self):
        if os.path.isfile(PLUGINS_CONFIG_FILE):
            try:
                with open(PLUGINS_CONFIG_FILE, "r") as f:
                    return json.load(f)
            except Exception:
                pass
        return {}

    def _save_config(self, config):
        os.makedirs(CONFIG_DIR, exist_ok=True)
        fd, tmp = tempfile.mkstemp(dir=CONFIG_DIR, suffix=".tmp")
        try:
            with os.fdopen(fd, "w") as f:
                json.dump(config, f, indent=2)
            os.replace(tmp, PLUGINS_CONFIG_FILE)
        except Exception:
            if os.path.exists(tmp):
                os.unlink(tmp)
            raise

    # ── Per-tab binding ──

    def set_active_tab(self, tab):
        """Reload panel relatively to active tab. Called by App._on_switch_page.

        Tab z claude_config → checkboxy reprezentują tab.enabled_plugins
        (per-projekt). Tab bez claude_config (SSH/local) → globalny config.
        """
        self._active_tab = tab
        self.refresh()

    def _per_tab_mode(self) -> bool:
        """Czy aktywny tab to Claude Code z konfiguracją projektu?"""
        tab = self._active_tab
        return tab is not None and getattr(tab, "claude_config", None) is not None

    def _tab_enabled_set(self):
        """Zwraca set nazw pluginów aktywnych dla aktywnego taba.

        None = klucz nieobecny w session config (backwards compat: wszystkie
        globally-enabled plugins on). Zwracamy wtedy fallback: wszystkie
        modules z globalnego config.
        """
        tab = self._active_tab
        if tab is None:
            return None
        return getattr(tab, "enabled_plugins", None)

    # ── Data ──

    def refresh(self):
        self.store.clear()
        config = self._load_config()
        per_tab = self._per_tab_mode()
        tab_enabled = self._tab_enabled_set() if per_tab else None
        # Update scope indicator label
        if per_tab:
            tab = self._active_tab
            tab_name = (
                getattr(tab, "_claude_tab_display", None)
                or (tab.claude_config or {}).get("name", "tab")
            )
            self._scope_lbl.set_markup(
                f"<small>Scope: <b>{GLib.markup_escape_text(str(tab_name))}</b> (per-projekt)</small>"
            )
        else:
            self._scope_lbl.set_markup("<small>Scope: <b>globalny</b></small>")
        if not os.path.isdir(PLUGINS_DIR):
            return
        for entry in sorted(os.listdir(PLUGINS_DIR)):
            path = os.path.join(PLUGINS_DIR, entry)
            if os.path.isfile(path) and entry.endswith(".py"):
                mod_name = entry[:-3]
            elif os.path.isdir(path) and os.path.isfile(os.path.join(path, "__init__.py")):
                mod_name = entry
            else:
                continue
            globally_enabled = config.get(mod_name, True)
            if per_tab:
                # Per-tab: checkbox = czy plugin context wstrzykiwany do
                # intro tej sesji. Zaznaczone, gdy mod_name w tab_enabled
                # (None = backwards compat = wszystkie globally-enabled).
                if tab_enabled is None:
                    enabled = globally_enabled
                else:
                    enabled = mod_name in tab_enabled
            else:
                enabled = globally_enabled
            name, version, author, status = mod_name, "", "", "Disabled"
            if globally_enabled and mod_name in self.app._plugins:
                plugin = self.app._plugins[mod_name]
                name = plugin.title or plugin.name
                version = plugin.version
                author = plugin.author
                status = "Loaded" if enabled else "Off (tab)"
            elif globally_enabled:
                status = "Error"
            self.store.append([enabled, name, version, author, status, mod_name])

    # ── Cell styling ──

    def _style_status_cell(self, column, cell, model, iter_, data=None):
        status = model.get_value(iter_, 4)
        if status == "Loaded":
            cell.set_property("foreground", CATPPUCCIN["green"])
        elif status == "Disabled":
            cell.set_property("foreground", CATPPUCCIN["overlay1"])
        elif status == "Error":
            cell.set_property("foreground", CATPPUCCIN["red"])
        else:
            cell.set_property("foreground", CATPPUCCIN["text"])

    # ── Selection / detail ──

    def _on_selection_changed(self, selection):
        model, iter_ = selection.get_selected()
        if iter_ is None:
            self.detail_label.set_markup(
                f'<span foreground="{CATPPUCCIN["overlay1"]}">Select a plugin to see details</span>'
            )
            return
        mod_name = model.get_value(iter_, 5)
        name = model.get_value(iter_, 1)
        version = model.get_value(iter_, 2)
        author = model.get_value(iter_, 3)
        status = model.get_value(iter_, 4)
        desc = ""
        if mod_name in self.app._plugins:
            desc = self.app._plugins[mod_name].description
        txt = CATPPUCCIN["text"]
        sub = CATPPUCCIN["subtext0"]
        lines = [f'<span foreground="{txt}" weight="bold">{GLib.markup_escape_text(name)}</span>']
        if version:
            lines.append(f'<span foreground="{sub}">Version: {GLib.markup_escape_text(version)}</span>')
        if author:
            lines.append(f'<span foreground="{sub}">Author: {GLib.markup_escape_text(author)}</span>')
        lines.append(f'<span foreground="{sub}">Status: {GLib.markup_escape_text(status)}</span>')
        lines.append(f'<span foreground="{sub}">Module: {GLib.markup_escape_text(mod_name)}</span>')
        if desc:
            lines.append("")
            lines.append(f'<span foreground="{sub}">{GLib.markup_escape_text(desc)}</span>')
        self.detail_label.set_markup("\n".join(lines))

    # ── Enable/disable toggle ──

    def _on_enabled_toggled(self, renderer, path):
        iter_ = self.store.get_iter(path)
        enabled = not self.store.get_value(iter_, 0)
        mod_name = self.store.get_value(iter_, 5)

        if self._per_tab_mode():
            # Per-tab: toggle w tab.enabled_plugins + persist do session
            # configu via claude_manager.update. Plugin zostaje globally
            # załadowany — tylko intro prompt nowych sesji tego projektu
            # będzie/nie będzie miał plugin context.
            tab = self._active_tab
            current = self._tab_enabled_set()
            if current is None:
                # Klucz nieobecny — backwards compat default: wszystkie
                # globally-enabled. Tworzymy explicit set z aktualnego
                # globalnego state.
                cfg = self._load_config()
                current = {n for n, v in cfg.items() if v}
                # Plus aktywne pluginy bez explicit config entry (default True)
                for name in self.app._plugins:
                    if name not in cfg:
                        current.add(name)
            new_set = set(current)
            if enabled:
                new_set.add(mod_name)
            else:
                new_set.discard(mod_name)
            tab.enabled_plugins = new_set
            # Persist do session configu
            cc = tab.claude_config
            if cc:
                cc["enabled_plugins"] = sorted(new_set)
                claude_id = cc.get("id")
                if claude_id:
                    try:
                        self.app.claude_manager.update(claude_id, cc)
                    except Exception:
                        pass
            self.refresh()
            return

        # Global toggle (SSH tab albo brak claude_config)
        config = self._load_config()
        config[mod_name] = enabled
        self._save_config(config)

        # Etap 8: hot toggle — no restart needed.
        err = None
        try:
            if enabled:
                self.app._hot_load_plugin(mod_name)
            else:
                self.app._hot_unload_plugin(mod_name)
        except Exception as exc:  # noqa: BLE001
            err = f"{type(exc).__name__}: {exc}"

        if err is not None:
            dlg = Gtk.MessageDialog(
                transient_for=self.app, modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=f'Plugin "{mod_name}" toggle failed:\n{err}',
            )
            dlg.run()
            dlg.destroy()
        self.refresh()

    # ── Add plugin ──

    def _on_add_file(self):
        dlg = Gtk.FileChooserDialog(
            title="Add Plugin File", parent=self.app,
            action=Gtk.FileChooserAction.OPEN,
        )
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        filt = Gtk.FileFilter()
        filt.set_name("Python files (*.py)")
        filt.add_pattern("*.py")
        dlg.add_filter(filt)
        if dlg.run() == Gtk.ResponseType.OK:
            src = dlg.get_filename()
            if src:
                self._copy_plugin(src)
        dlg.destroy()

    def _on_add_folder(self):
        dlg = Gtk.FileChooserDialog(
            title="Add Plugin Folder", parent=self.app,
            action=Gtk.FileChooserAction.SELECT_FOLDER,
        )
        dlg.add_buttons(Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
                        Gtk.STOCK_OPEN, Gtk.ResponseType.OK)
        if dlg.run() == Gtk.ResponseType.OK:
            src = dlg.get_filename()
            if src:
                self._copy_plugin(src)
        dlg.destroy()

    def _copy_plugin(self, src):
        os.makedirs(PLUGINS_DIR, exist_ok=True)
        dest = os.path.join(PLUGINS_DIR, os.path.basename(src))
        try:
            if os.path.isdir(src):
                shutil.copytree(src, dest)
            else:
                shutil.copy2(src, dest)
            self.refresh()
        except Exception as e:
            dlg = Gtk.MessageDialog(
                transient_for=self.app, modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text=f"Failed to add plugin: {e}",
            )
            dlg.run()
            dlg.destroy()

    # ── Remove plugin ──

    def _on_remove_plugin(self):
        model, iter_ = self.tree.get_selection().get_selected()
        if iter_ is None:
            return
        mod_name = model.get_value(iter_, 5)
        dlg = Gtk.MessageDialog(
            transient_for=self.app, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f'Remove plugin "{mod_name}"?\nThis will delete the plugin files.',
        )
        if dlg.run() == Gtk.ResponseType.YES:
            if mod_name in self.app._plugins:
                try:
                    self.app._plugins[mod_name].deactivate()
                except Exception:
                    pass
                del self.app._plugins[mod_name]
            path_py = os.path.join(PLUGINS_DIR, mod_name + ".py")
            path_dir = os.path.join(PLUGINS_DIR, mod_name)
            try:
                if os.path.isfile(path_py):
                    os.unlink(path_py)
                elif os.path.isdir(path_dir):
                    shutil.rmtree(path_dir)
            except Exception as e:
                print(f"[plugins] Failed to remove {mod_name}: {e}")
            config = self._load_config()
            config.pop(mod_name, None)
            self._save_config(config)
            self.refresh()
        dlg.destroy()

