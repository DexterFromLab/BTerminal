"""BTerminalApp — main GTK window orchestrator.

Composes all panels (sidebar/git/built-in panels), notebook of tabs
(SSH/local/Claude Code), menu bar, sidecar runtime, and debug REST
server (when enabled). After Etap 5-9 extractions this is now a
relatively thin shim that wires up imported components.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/app.py` in a later migration etap.
"""

import atexit
import importlib.util
import json
import os
import re
import shutil
import subprocess
import sys
import threading
import time
import urllib.request
from collections import defaultdict
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
gi.require_version("Vte", "2.91")
from gi.repository import Gdk, GdkPixbuf, Gio, GLib, Gtk, Pango, Vte
import random
import sqlite3

from bterminal import debug_rest
from bterminal.debug_rest import (
    DEBUG_REST_PORT,
    DEBUG_TOKEN_FILE,
    _load_or_create_debug_token,
    _start_debug_rest_server,
    _start_idle_watchdog,
    _stop_debug_rest_server,
)
from bterminal.config import (
    APP_NAME,
    APP_VERSION,
    CATPPUCCIN,
    CATPPUCCIN_LATTE,
    CATPPUCCIN_MOCHA,
    CONFIG_DIR,
    CTX_DB,
    PLUGINS_CONFIG_FILE,
    PLUGINS_DIR,
    REPO_DIR,
    TERMINAL_PALETTE,
    TERMINAL_PALETTE_LATTE,
    TERMINAL_PALETTE_MOCHA,
    _OPTIONS,
    _build_css,
    _parse_color,
    _save_options,
    show_error_dialog,
    show_info_dialog,
)
from bterminal.ctx.helpers import (
    _collect_claude_log,
    _resolve_ctx_project_name,
    _smart_project_name,
)
from bterminal.ctx.dialogs import CtxEditDialog
from bterminal.ui.dialogs.claude_code import ClaudeCodeDialog
from bterminal.ui.dialogs.options import OptionsDialog
from bterminal.models import ClaudeSessionManager, ConsultManager, SessionManager
from bterminal.ui.panels.consult import ConsultPanel
from bterminal.ui.panels.ctx_manager import CtxManagerPanel
from bterminal.ui.panels.files import FilesPanel
from bterminal.ui.panels.git import GitPanel
from bterminal.ui.panels.memory import MemoryPanel
from bterminal.ui.panels.plugin_manager import PluginManagerPanel
from bterminal.ui.sidebar import SessionSidebar
from bterminal.ui.panels.skills import SkillsPanel
from bterminal.ui.stats import SessionStatsBar
from bterminal.ui.panels.tasks import TaskListPanel
from bterminal.ui.terminal_tab import TerminalTab
from bterminal.plugin_runtime import BTerminalPlugin
from bterminal.sidecar_runtime import (
    HealthChecker,
    SidecarDiscovery,
    SidecarManifest,
    SidecarRunner,
)
from bterminal.updater import (
    _check_for_updates,
    _load_local_errata,
    _show_errata_dialog,
)


# App-runtime mutable state (reassigned by _toggle_theme via `global`).
# Live here rather than in config.py because they're not pure configuration —
# they're the *current* runtime view that gets rebuilt when the user toggles
# the theme. The `global` keyword in _toggle_theme only reaches the module
# the method belongs to, so these must sit alongside BTerminalApp.
_current_theme = _OPTIONS.get("theme", "dark")
CSS = _build_css(CATPPUCCIN)


class ShrinkableBin(Gtk.Bin):
    """Container that reports minimum width as 0, allowing HPaned to shrink it
    without triggering GTK's right-alignment clipping behavior."""

    def do_get_preferred_width(self):
        return (0, 0)

    def do_get_preferred_width_for_height(self, height):
        return (0, 0)

    def do_size_allocate(self, allocation):
        self.set_allocation(allocation)
        child = self.get_child()
        if child and child.get_visible():
            child.size_allocate(allocation)


class BTerminalApp(Gtk.Window):
    """Główne okno aplikacji BTerminal."""

    def __init__(self):
        super().__init__(title=APP_NAME)
        self.set_default_size(1200, 700)
        self.set_icon_name("bterminal")

        # Apply CSS
        self._css_provider = Gtk.CssProvider()
        self._css_provider.load_from_data(CSS.encode())
        Gtk.StyleContext.add_provider_for_screen(
            Gdk.Screen.get_default(),
            self._css_provider,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )

        # Theme from options
        self._gtk_settings = Gtk.Settings.get_default()
        self._gtk_settings.set_property(
            "gtk-application-prefer-dark-theme", _current_theme == "dark"
        )

        # Session managers
        self.session_manager = SessionManager()
        self.claude_manager = ClaudeSessionManager()

        # Layout: VBox → menubar + HPaned
        root_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        self.add(root_box)

        root_box.pack_start(self._build_menubar(), False, False, 0)

        if debug_rest.DEBUG_REST_ENABLED:
            debug_bar = Gtk.Box()
            debug_bar.get_style_context().add_class("debug-rest-bar")
            debug_bar.set_size_request(-1, 2)
            root_box.pack_start(debug_bar, False, False, 0)

        paned = Gtk.HPaned()
        root_box.pack_start(paned, True, True, 0)

        # Sidebar container with stack switcher
        sidebar_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL)
        sidebar_box.get_style_context().add_class("sidebar")
        sidebar_box.set_size_request(0, -1)

        self.sidebar_stack = Gtk.Stack()
        self.sidebar_stack.set_transition_type(
            Gtk.StackTransitionType.SLIDE_LEFT_RIGHT
        )

        self.sidebar = SessionSidebar(self)
        self.sidebar_stack.add_titled(self.sidebar, "sessions", "Sessions")

        self.ctx_panel = CtxManagerPanel(self)
        self.sidebar_stack.add_titled(self.ctx_panel, "ctx", "Ctx")

        self.consult_panel = ConsultPanel(self)
        self.sidebar_stack.add_titled(self.consult_panel, "consult", "Consult")

        self.task_panel = TaskListPanel(self)
        self.sidebar_stack.add_titled(self.task_panel, "tasks", "Tasks")

        self.memory_panel = MemoryPanel(self)
        self.sidebar_stack.add_titled(self.memory_panel, "memory", "Memory")

        self.skills_panel = SkillsPanel(self)
        self.sidebar_stack.add_titled(self.skills_panel, "skills", "Skills")

        self.files_panel = FilesPanel(self)
        self.sidebar_stack.add_titled(self.files_panel, "files", "Files")

        self.plugin_panel = PluginManagerPanel(self)
        self.sidebar_stack.add_titled(self.plugin_panel, "plugins", "Plugins")

        # Two-row compact tab switcher: row1 = main tabs, row2 = extra + toggle
        switcher = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        switcher.get_style_context().add_class("sidebar-switcher")

        row1 = Gtk.Box(spacing=0)
        row2 = Gtk.Box(spacing=0)

        _tab_defs_row1 = [("sessions", "Sessions"), ("ctx", "Ctx"),
                          ("consult", "Consult"), ("tasks", "Tasks")]
        _tab_defs_row2 = [("memory", "Memory"), ("skills", "Skills"), ("files", "Files"), ("plugins", "Plugins")]

        self._sidebar_tab_buttons = []
        for name, title in _tab_defs_row1:
            btn = Gtk.Button(label=title)
            btn.get_style_context().add_class("sidebar-tab")
            child = btn.get_child()
            if isinstance(child, Gtk.Label):
                child.set_ellipsize(Pango.EllipsizeMode.END)
            btn.connect("clicked", lambda _, n=name: self.sidebar_stack.set_visible_child_name(n))
            row1.pack_start(btn, True, True, 0)
            self._sidebar_tab_buttons.append(btn)

        for name, title in _tab_defs_row2:
            btn = Gtk.Button(label=title)
            btn.get_style_context().add_class("sidebar-tab")
            child = btn.get_child()
            if isinstance(child, Gtk.Label):
                child.set_ellipsize(Pango.EllipsizeMode.END)
            btn.connect("clicked", lambda _, n=name: self.sidebar_stack.set_visible_child_name(n))
            row2.pack_start(btn, True, True, 0)
            self._sidebar_tab_buttons.append(btn)

        # Toggle button at end of row2
        self._sidebar_toggle_btn = Gtk.Button(label="◀")
        self._sidebar_toggle_btn.get_style_context().add_class("sidebar-tab")
        self._sidebar_toggle_btn.set_tooltip_text("Hide sidebar (Ctrl+B)")
        self._sidebar_toggle_btn.connect("clicked", lambda _: self.toggle_sidebar())
        row2.pack_end(self._sidebar_toggle_btn, False, False, 0)

        switcher.pack_start(row1, False, False, 0)
        switcher.pack_start(row2, False, False, 0)
        self._sidebar_switcher = switcher
        self._sidebar_tab_names = (
            [n for n, _ in _tab_defs_row1] + [n for n, _ in _tab_defs_row2]
        )

        self.sidebar_stack.connect("notify::visible-child-name", self._on_sidebar_tab_changed)
        # Delay so this fires after all panel show_all() and idle callbacks.
        def _set_default_sidebar_tab():
            self.sidebar_stack.set_visible_child_name("sessions")
            self._on_sidebar_tab_changed(None, None)
            return False
        GLib.idle_add(_set_default_sidebar_tab, priority=GLib.PRIORITY_LOW)

        sidebar_box.pack_start(switcher, False, False, 0)
        sidebar_box.pack_start(self.sidebar_stack, True, True, 0)

        # Make all sidebar widgets genuinely shrinkable via ellipsize
        # (ellipsize reduces a label's true minimum width to ~"..." width)
        def _make_shrinkable(widget):
            if isinstance(widget, Gtk.Label):
                widget.set_ellipsize(Pango.EllipsizeMode.END)
            if isinstance(widget, (Gtk.Entry, Gtk.SpinButton)):
                widget.set_width_chars(1)
            if isinstance(widget, Gtk.ComboBoxText):
                widget.set_size_request(0, -1)
            if isinstance(widget, Gtk.TreeView):
                for col in widget.get_columns():
                    col.set_min_width(0)
                    col.set_max_width(-1)
                    col.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
            if isinstance(widget, Gtk.ScrolledWindow):
                widget.set_propagate_natural_width(False)
            if isinstance(widget, Gtk.Container):
                widget.forall(_make_shrinkable)
        # Process ALL stack children explicitly (forall may skip invisible pages)
        for panel in [self.sidebar, self.ctx_panel, self.consult_panel, self.task_panel, self.plugin_panel]:
            _make_shrinkable(panel)
        _make_shrinkable(switcher)

        self._sidebar_wrap = ShrinkableBin()
        self._sidebar_wrap.add(sidebar_box)
        self._paned = paned
        self._sidebar_visible = True
        self._sidebar_last_pos = 250
        paned.pack1(self._sidebar_wrap, resize=False, shrink=False)

        # Inner paned: notebook + git panel
        inner_paned = Gtk.HPaned()
        self._inner_paned = inner_paned

        # Notebook (tabs)
        self.notebook = Gtk.Notebook()
        self.notebook.set_scrollable(True)
        self.notebook.set_show_border(False)
        self.notebook.popup_disable()
        inner_paned.pack1(self.notebook, resize=True, shrink=False)

        # Git panel (right side)
        self.git_panel = GitPanel(self)
        _make_shrinkable(self.git_panel)
        self._git_wrap = ShrinkableBin()
        self._git_wrap.add(self.git_panel)
        self._git_visible = False
        self._git_last_pos = 300
        inner_paned.pack2(self._git_wrap, resize=False, shrink=False)

        paned.pack2(inner_paned, resize=True, shrink=False)

        # Show-sidebar button (visible only when sidebar is hidden)
        self._show_sidebar_btn = Gtk.Button(label="▶")
        self._show_sidebar_btn.get_style_context().add_class("sidebar-btn")
        self._show_sidebar_btn.set_tooltip_text("Show sidebar (Ctrl+B)")
        self._show_sidebar_btn.set_no_show_all(True)
        self._show_sidebar_btn.connect("clicked", lambda _: self.toggle_sidebar())
        self.notebook.set_action_widget(self._show_sidebar_btn, Gtk.PackType.START)

        # Right action area: theme toggle + git button
        end_box = Gtk.Box(spacing=4)

        self._theme_btn = Gtk.Button(label="☀" if _current_theme == "dark" else "☾")
        self._theme_btn.get_style_context().add_class("theme-toggle")
        self._theme_btn.set_tooltip_text("Toggle light/dark theme")
        self._theme_btn.connect("clicked", lambda _: self._toggle_theme())
        end_box.pack_start(self._theme_btn, False, False, 0)

        self._show_git_btn = Gtk.Button(label="Git ◀")
        self._show_git_btn.get_style_context().add_class("sidebar-btn")
        self._show_git_btn.set_tooltip_text("Show Git panel (Ctrl+G)")
        self._show_git_btn.set_no_show_all(True)
        self._show_git_btn.connect("clicked", lambda _: self.toggle_git_panel())
        end_box.pack_start(self._show_git_btn, False, False, 0)

        end_box.show_all()
        self.notebook.set_action_widget(end_box, Gtk.PackType.END)

        paned.set_position(250)

        # Git panel starts fully hidden (no Claude tab active yet)
        self._git_wrap.set_no_show_all(True)
        self._git_wrap.hide()

        # Auto-refresh panels when switching to them
        def _on_sidebar_switch(stack, _param):
            child = stack.get_visible_child()
            if child is self.ctx_panel:
                self.ctx_panel.refresh()
            elif child is self.consult_panel:
                self.consult_panel.refresh()
            elif child is self.task_panel:
                self.task_panel.refresh()
            elif child is self.skills_panel:
                self.skills_panel._refresh()
            elif child is self.files_panel:
                self.files_panel._refresh()
            elif child is self.plugin_panel:
                self.plugin_panel.refresh()

        self.sidebar_stack.connect("notify::visible-child", _on_sidebar_switch)

        # Keyboard shortcuts
        self.connect("key-press-event", self._on_key_press)
        self.connect("delete-event", self._on_delete_event)
        self.notebook.connect("switch-page", self._on_switch_page)

        # Open initial local shell
        self.add_local_tab()

        self.show_all()

        # R1.f2: pokaż użytkownikowi dialog jeśli options.json był uszkodzony.
        # Self-heal już nadpisał plik defaultami w config._load_options.
        from bterminal import config as _config
        if _config._options_load_error is not None:
            show_error_dialog(
                self,
                f"Plik ~/.config/bterminal/options.json był uszkodzony — "
                f"przywrócono ustawienia domyślne.\n\n"
                f"Przyczyna: {type(_config._options_load_error).__name__}: "
                f"{_config._options_load_error}",
            )
            _config._options_load_error = None  # nie pokazuj ponownie

        self._plugins = {}
        self._plugin_shortcuts = []
        self._load_plugins()
        self.plugin_panel.refresh()

        # ── Sidecar runtime (Etap 5) — discovery only, no auto-start ──
        self.sidecar_discovery = SidecarDiscovery()
        self.sidecar_runner = SidecarRunner()
        self.sidecar_manifests = self.sidecar_discovery.load_all()
        # Per-tab refcount (Etap 8): start sidecar when first tab claims it,
        # stop when last tab drops it.
        self.sidecar_refcounts: dict[str, int] = defaultdict(int)
        atexit.register(self.sidecar_runner.stop_all)

        # ── Debug REST (off unless --debug-rest / BTERMINAL_DEBUG_REST=1) ──
        self._debug_token = None
        self._debug_server = None
        if debug_rest.DEBUG_REST_ENABLED:
            self._debug_token = _load_or_create_debug_token()
            self._debug_server = _start_debug_rest_server(self, self._debug_token)
            _start_idle_watchdog(self._debug_server)
            atexit.register(_stop_debug_rest_server, self._debug_server)
            sys.stderr.write(
                f"[debug-rest] listening on http://127.0.0.1:{DEBUG_REST_PORT} "
                f"(token in {DEBUG_TOKEN_FILE})\n"
            )

    def _build_menubar(self):
        menubar = Gtk.MenuBar()

        def _item(label, callback, shortcut=None):
            it = Gtk.MenuItem(label=label)
            if shortcut:
                it.set_accel_path(shortcut)
            it.connect("activate", lambda _: callback())
            return it

        def _sep():
            return Gtk.SeparatorMenuItem()

        # ── File ──────────────────────────────────────────────────────────────
        file_menu = Gtk.Menu()
        file_menu.append(_item("Nowa karta lokalna", self.add_local_tab))
        file_menu.append(_item("Nowa sesja SSH…", lambda: self.sidebar._on_add(None)))
        file_menu.append(_item("Nowa sesja Claude Code…", lambda: self.sidebar._on_add_claude()))
        file_menu.append(_sep())
        file_menu.append(_item("Opcje…", lambda: OptionsDialog(self).run_and_apply()))
        file_menu.append(_sep())
        file_menu.append(_item("Zamknij aplikację", self.destroy))
        file_root = Gtk.MenuItem(label="File")
        file_root.set_submenu(file_menu)
        menubar.append(file_root)

        # ── View ──────────────────────────────────────────────────────────────
        view_menu = Gtk.Menu()
        view_menu.append(_item("Przełącz sidebar (Ctrl+B)", self.toggle_sidebar))
        view_menu.append(_item("Przełącz panel Git (Ctrl+G)", self.toggle_git_panel))
        view_menu.append(_item("Przełącz motyw ☀/🌙", self._toggle_theme))
        view_menu.append(_sep())
        for panel_name, panel_title in [
            ("sessions", "Sessions"),
            ("ctx",      "Ctx"),
            ("consult",  "Consult"),
            ("tasks",    "Tasks"),
            ("plugins",  "Plugins"),
        ]:
            it = Gtk.MenuItem(label=panel_title)
            it.connect("activate", lambda _, n=panel_name: (
                self.sidebar_stack.set_visible_child_name(n),
                self._sidebar_visible or self.toggle_sidebar(),
            ))
            view_menu.append(it)
        view_root = Gtk.MenuItem(label="View")
        view_root.set_submenu(view_menu)
        menubar.append(view_root)

        # ── Tools ─────────────────────────────────────────────────────────────
        tools_menu = Gtk.Menu()
        tools_menu.append(_item("Sprawdź aktualizacje", lambda: _check_for_updates(self, manual=True)))
        tools_menu.append(_item("Errata…", lambda: _show_errata_dialog(self, _load_local_errata())))
        tools_root = Gtk.MenuItem(label="Tools")
        tools_root.set_submenu(tools_menu)
        menubar.append(tools_root)

        menubar.show_all()
        return menubar

    def _apply_font(self, font_str):
        desc = Pango.FontDescription(font_str)
        for i in range(self.notebook.get_n_pages()):
            tab = self.notebook.get_nth_page(i)
            if isinstance(tab, TerminalTab):
                tab.terminal.set_font(desc)

    def _update_window_title(self):
        """Update window title bar: 'BTerminal — tab_name [n/total]'."""
        suffix = f" [DEBUG-REST :{DEBUG_REST_PORT}]" if debug_rest.DEBUG_REST_ENABLED else ""
        n = self.notebook.get_n_pages()
        idx = self.notebook.get_current_page()
        if idx < 0 or n == 0:
            self.set_title(f"{APP_NAME} v{APP_VERSION}{suffix}")
            return
        tab = self.notebook.get_nth_page(idx)
        if isinstance(tab, TerminalTab):
            name = tab.get_label()
        else:
            name = "Terminal"
        if n > 1:
            self.set_title(f"{APP_NAME} — {name} [{idx + 1}/{n}]{suffix}")
        else:
            self.set_title(f"{APP_NAME} — {name}{suffix}")

    def _on_switch_page(self, notebook, page, page_num):
        GLib.idle_add(self._update_window_title)
        # Auto-select project in Task panel based on active Claude Code tab
        if isinstance(page, TerminalTab) and page._task_project:
            GLib.idle_add(self._sync_task_panel_project, page._task_project)
        # Per-tab sidebar binding: wszystkie panele reloadują stan
        # względem aktywnego taba. Każdy panel ma metodę
        # set_active_tab(tab) o uniform signature; jeśli tab=None lub
        # bez claude_config, panele zachowują poprzedni stan.
        active_tab = page if isinstance(page, TerminalTab) else None
        for panel_attr in (
            "plugin_panel", "files_panel", "skills_panel",
            "memory_panel", "task_panel", "ctx_panel",
        ):
            panel = getattr(self, panel_attr, None)
            if panel is not None and hasattr(panel, "set_active_tab"):
                GLib.idle_add(panel.set_active_tab, active_tab)
        # Git panel: show only for Claude Code tabs
        is_claude = isinstance(page, TerminalTab) and page.claude_config is not None
        if is_claude:
            self._show_git_btn.show()
            if self._git_visible:
                GLib.idle_add(self._sync_git_panel)
        else:
            if self._git_visible:
                self.toggle_git_panel()
            self._show_git_btn.hide()

    def _sync_task_panel_project(self, project_name):
        """Set Task panel's project combo to match the active tab's project.

        Wraps the programmatic combo.set_active() in _suspend_changed so the
        GTK 'changed' signal does not write back into tab._task_project
        (which would clobber the per-tab value with the value we synced FROM
        another tab)."""
        if not hasattr(self, "task_panel"):
            return
        combo = self.task_panel.project_combo
        model = combo.get_model()
        if not model:
            return
        self.task_panel._suspend_changed = True
        try:
            for i, row in enumerate(model):
                if row[0] == project_name:
                    combo.set_active(i)
                    break
        finally:
            self.task_panel._suspend_changed = False

    def _build_tab_label(self, text, tab_widget):
        """Build a tab label with a close button.

        Stores label reference on tab_widget._tab_label for efficient updates.
        """
        box = Gtk.Box(spacing=4)

        label = Gtk.Label(label=text)
        box.pack_start(label, True, True, 0)

        close_btn = Gtk.Button(label="×")
        close_btn.get_style_context().add_class("tab-close-btn")
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.connect("clicked", lambda _: self.close_tab(tab_widget))
        box.pack_start(close_btn, False, False, 0)

        box.show_all()
        tab_widget._tab_label = label
        return box

    def add_local_tab(self):
        tab = TerminalTab(self)
        label = self._build_tab_label("Terminal", tab)
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        self._update_window_title()

    def open_wizard_tab(self, project: str, cmd: list, on_done=None):
        """Open a terminal tab running memory_wizard; calls on_done when it exits."""
        # Resolve wizard binary — check install dir first (GUI may have empty PATH)
        wizard_bin = (
            shutil.which("memory_wizard")
            or str(Path.home() / ".local" / "share" / "bterminal" / "memory_wizard")
            or str(Path.home() / ".local" / "bin" / "memory_wizard")
        )
        if not os.path.isfile(wizard_bin):
            show_error_dialog(self, "memory_wizard not found. Run install.sh first.")
            return

        tab = TerminalTab(self)
        label = self._build_tab_label(f"🧙 {project}", tab)
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        self._update_window_title()

        argv = [wizard_bin] + cmd[1:]  # cmd[0] is "memory_wizard"
        tab.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.environ.get("HOME", "/"),
            argv,
            None,
            GLib.SpawnFlags.DEFAULT,
            None, None, -1, None, None,
        )

        if on_done:
            def _on_exit(terminal, status):
                if on_done:
                    GLib.idle_add(on_done)
            tab.terminal.connect("child-exited", _on_exit)

    def open_ssh_tab(self, session):
        tab = TerminalTab(self, session=session)
        name = session.get("name", "SSH")
        label = self._build_tab_label(name, tab)
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        self._update_window_title()

    def open_ssh_tab_with_macro(self, session, macro):
        tab = TerminalTab(self, session=session)
        name = f"{session.get('name', 'SSH')} \u2014 {macro.get('name', 'Macro')}"
        label = self._build_tab_label(name, tab)
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        tab.run_macro(macro)
        self._update_window_title()

    _TAB_EMOJIS = [
        "🦊", "🐙", "🎯", "🚀", "⚡", "🔮", "🎲", "🌀", "🦋", "🐺",
        "🎸", "🌊", "🔥", "💎", "🦅", "🐍", "🎪", "🌵", "🦈", "🍄",
        "🎭", "🏴\u200d☠️", "🛸", "🧊", "🦎", "🐝", "🌻", "🎱", "🦜", "🐲",
    ]

    def open_claude_tab(self, config):
        # Per-tab plugin gating: PRZEKAŻ enabled_plugins do TerminalTab
        # constructor PRZED spawn_claude — bo spawn liczy intro prompt
        # i filtruje per tab.enabled_plugins. Bug fix 2026-05-04: previously
        # was set AFTER constructor returned (zbyt późno).
        enabled = config.get("enabled_plugins")
        tab = TerminalTab(self, claude_config=config, enabled_plugins=enabled)
        if tab.enabled_plugins is not None:
            # Acquire sidecars referenced by this tab. The first tab to
            # claim a sidecar starts it; the last to release it stops it
            # (close_tab handles release in Etap 8.k).
            for name in tab.enabled_plugins:
                if name in self.sidecar_manifests:
                    self._sidecar_acquire(name)
        base_name = config.get("name", "Claude Code")
        # Count existing tabs with the same base config name
        count = 0
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            if isinstance(page, TerminalTab) and page.claude_config:
                if page.claude_config.get("name") == config.get("name"):
                    count += 1
        # Pick a random emoji not already used by sibling tabs
        used = set()
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            if isinstance(page, TerminalTab) and hasattr(page, "_claude_tab_emoji"):
                used.add(page._claude_tab_emoji)
        available = [e for e in self._TAB_EMOJIS if e not in used] or self._TAB_EMOJIS
        emoji = random.choice(available)
        tab._claude_tab_emoji = emoji
        tab_name = f"{base_name} #{count + 1} {emoji}"
        tab._claude_tab_display = tab_name
        label = self._build_tab_label(tab_name, tab)
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        self._update_window_title()

    def close_tab(self, tab):
        # Release task claims for this tab's session
        if getattr(tab, "_task_project", None) and getattr(tab, "_task_session_id", None):
            try:
                db = sqlite3.connect(CTX_DB)
                db.execute(
                    "DELETE FROM task_claims WHERE project = ? AND session_id = ?",
                    (tab._task_project, tab._task_session_id),
                )
                db.commit()
                db.close()
            except Exception:
                pass
        # Release sidecar refcounts (Etap 8.k). When the last tab using a
        # sidecar closes, _sidecar_release fires runner.stop and the port
        # is freed.
        enabled = getattr(tab, "enabled_plugins", None)
        if enabled is not None:
            for name in enabled:
                if name in self.sidecar_manifests:
                    self._sidecar_release(name)
        # Collect Claude Code session log on tab close
        _collect_claude_log(tab)
        idx = self.notebook.page_num(tab)
        if idx >= 0:
            self.notebook.remove_page(idx)
            tab.destroy()
        # No auto-open — user picks a session from the sidebar
        self._update_window_title()

    def on_tab_child_exited(self, tab):
        """Called when a terminal's child process exits.

        Starts a 30-second auto-close timer instead of closing immediately,
        so the user can read final output. Any keypress cancels the timer.
        """
        def _auto_close():
            tab._dead_timer_id = None
            self.close_tab(tab)
            return False

        def _cancel_timer(terminal, event):
            timer_id = getattr(tab, "_dead_timer_id", None)
            if timer_id:
                GLib.source_remove(timer_id)
                tab._dead_timer_id = None
            tab._dead_key_handler = None
            return False

        tab._dead_timer_id = GLib.timeout_add_seconds(30, _auto_close)
        tab._dead_key_handler = tab.terminal.connect("key-press-event", _cancel_timer)

    def update_tab_title(self, tab, title):
        """Update tab label when terminal title changes."""
        idx = self.notebook.page_num(tab)
        if idx >= 0:
            label = getattr(tab, "_tab_label", None)
            if label:
                label.set_text(title)
            else:
                label_widget = self._build_tab_label(title, tab)
                self.notebook.set_tab_label(tab, label_widget)
            self._update_window_title()

    def _get_current_terminal(self):
        idx = self.notebook.get_current_page()
        if idx < 0:
            return None
        tab = self.notebook.get_nth_page(idx)
        if isinstance(tab, TerminalTab):
            return tab.terminal
        return None

    def toggle_sidebar(self):
        """Show/hide the sidebar panel."""
        if self._sidebar_visible:
            self._sidebar_last_pos = self._paned.get_position()
            self._sidebar_wrap.hide()
            self._paned.set_position(0)
            self._show_sidebar_btn.show()
        else:
            self._sidebar_wrap.show()
            self._paned.set_position(self._sidebar_last_pos)
            self._show_sidebar_btn.hide()
        self._sidebar_visible = not self._sidebar_visible

    def _toggle_theme(self):
        """Switch between Catppuccin Mocha (dark) and Latte (light)."""
        global _current_theme, CSS
        if _current_theme == "dark":
            _current_theme = "light"
            CATPPUCCIN.update(CATPPUCCIN_LATTE)
            TERMINAL_PALETTE[:] = TERMINAL_PALETTE_LATTE
            self._gtk_settings.set_property("gtk-application-prefer-dark-theme", False)
            self._theme_btn.set_label("☾")
        else:
            _current_theme = "dark"
            CATPPUCCIN.update(CATPPUCCIN_MOCHA)
            TERMINAL_PALETTE[:] = TERMINAL_PALETTE_MOCHA
            self._gtk_settings.set_property("gtk-application-prefer-dark-theme", True)
            self._theme_btn.set_label("☀")
        _OPTIONS["theme"] = _current_theme
        _save_options(_OPTIONS)
        # Reload CSS
        CSS = _build_css(CATPPUCCIN)
        self._css_provider.load_from_data(CSS.encode())
        # Re-color all open terminals
        fg = _parse_color(CATPPUCCIN["text"])
        bg = _parse_color(CATPPUCCIN["base"])
        palette = [_parse_color(c) for c in TERMINAL_PALETTE]
        cursor = _parse_color(CATPPUCCIN["rosewater"])
        cursor_fg = _parse_color(CATPPUCCIN["crust"])
        for i in range(self.notebook.get_n_pages()):
            tab = self.notebook.get_nth_page(i)
            if isinstance(tab, TerminalTab):
                tab.terminal.set_colors(fg, bg, palette)
                tab.terminal.set_color_cursor(cursor)
                tab.terminal.set_color_cursor_foreground(cursor_fg)
                # Reset terminal colors for already-rendered content
                tab.terminal.feed(b"\x1b[0m")
        # Refresh sidebar (session colors change per theme)
        self.sidebar.refresh()
        # Refresh git panel if visible
        if self._git_visible:
            self.git_panel.refresh()

    def toggle_git_panel(self):
        """Show/hide the right-side Git panel (mirror of toggle_sidebar)."""
        if self._git_visible:
            # Save current panel width before hiding
            alloc = self._inner_paned.get_allocation()
            pos = self._inner_paned.get_position()
            self._git_last_pos = alloc.width - pos
            self._git_wrap.hide()
            self.git_panel.hide()
            # Push divider to far right so notebook takes full width
            self._inner_paned.set_position(alloc.width)
            self._show_git_btn.show()
            self._git_visible = False
        else:
            self._git_wrap.show()
            self.git_panel.show()
            self.git_panel.show_all()
            self._show_git_btn.hide()
            self._git_visible = True
            # Sync and position after GTK processes the show
            self._sync_git_panel()
            self.git_panel.refresh()
            GLib.idle_add(self._apply_git_panel_position)

    def _apply_git_panel_position(self):
        """Set inner paned position after GTK layout cycle."""
        alloc = self._inner_paned.get_allocation()
        width = self._git_last_pos if self._git_last_pos > 50 else 320
        if alloc.width > 0:
            self._inner_paned.set_position(alloc.width - width)
        return False

    def _sync_git_panel(self):
        """Update git panel to match the current tab's project directory."""
        idx = self.notebook.get_current_page()
        if idx < 0:
            self.git_panel.set_project_dir(None)
            return
        tab = self.notebook.get_nth_page(idx)
        if isinstance(tab, TerminalTab) and tab.claude_config:
            proj_dir = tab.claude_config.get("project_dir", "")
            self.git_panel.set_project_dir(proj_dir)
        else:
            # For local/SSH tabs, try CWD
            self.git_panel.set_project_dir(os.getcwd())

    def _on_sidebar_tab_changed(self, _stack, _param):
        """Update active tab styling in custom sidebar switcher."""
        active_name = self.sidebar_stack.get_visible_child_name()
        tab_names = getattr(self, "_sidebar_tab_names", []) + [
            p.name for p in self._plugins.values()
        ] if hasattr(self, '_plugins') else getattr(self, "_sidebar_tab_names", [])
        for btn, name in zip(self._sidebar_tab_buttons, tab_names):
            ctx = btn.get_style_context()
            if name == active_name:
                ctx.add_class("sidebar-tab-active")
            else:
                ctx.remove_class("sidebar-tab-active")

    def _load_plugins(self):
        """Scan PLUGINS_DIR and load all plugins."""
        if not os.path.isdir(PLUGINS_DIR):
            return
        plugin_config = {}
        if os.path.isfile(PLUGINS_CONFIG_FILE):
            try:
                with open(PLUGINS_CONFIG_FILE, "r") as f:
                    plugin_config = json.load(f)
            except Exception:
                pass
        for entry in sorted(os.listdir(PLUGINS_DIR)):
            path = os.path.join(PLUGINS_DIR, entry)
            # Accept .py files or packages with __init__.py
            if os.path.isfile(path) and entry.endswith(".py"):
                mod_name = entry[:-3]
            elif os.path.isdir(path) and os.path.isfile(os.path.join(path, "__init__.py")):
                mod_name = entry
            else:
                continue
            # Skip disabled plugins
            if not plugin_config.get(mod_name, True):
                continue
            try:
                spec = importlib.util.spec_from_file_location(
                    f"bterminal_plugin_{mod_name}",
                    path if path.endswith(".py") else os.path.join(path, "__init__.py"),
                )
                module = importlib.util.module_from_spec(spec)
                spec.loader.exec_module(module)
                plugin = module.create_plugin(self)
                self._register_plugin(plugin)
            except Exception as e:
                print(f"[plugins] Failed to load {entry}: {e}")

    def _register_plugin(self, plugin):
        """Register a plugin: activate, add sidebar panel and switcher button."""
        panel = plugin.activate(self)
        self._plugins[plugin.name] = plugin

        if panel is not None:
            self.sidebar_stack.add_titled(panel, plugin.name, plugin.title)

            # Make shrinkable (reuse the same logic as built-in panels)
            def _make_shrinkable(widget):
                if isinstance(widget, Gtk.Label):
                    widget.set_ellipsize(Pango.EllipsizeMode.END)
                if isinstance(widget, (Gtk.Entry, Gtk.SpinButton)):
                    widget.set_width_chars(1)
                if isinstance(widget, Gtk.ComboBoxText):
                    widget.set_size_request(0, -1)
                if isinstance(widget, Gtk.TreeView):
                    for col in widget.get_columns():
                        col.set_min_width(0)
                        col.set_max_width(-1)
                        col.set_sizing(Gtk.TreeViewColumnSizing.AUTOSIZE)
                if isinstance(widget, Gtk.ScrolledWindow):
                    widget.set_propagate_natural_width(False)
                if isinstance(widget, Gtk.Container):
                    widget.forall(_make_shrinkable)
            _make_shrinkable(panel)

            # Add switcher button
            btn = Gtk.Button(label=plugin.title)
            btn.get_style_context().add_class("sidebar-tab")
            child = btn.get_child()
            if isinstance(child, Gtk.Label):
                child.set_ellipsize(Pango.EllipsizeMode.END)
            btn.connect("clicked", lambda _, n=plugin.name: self.sidebar_stack.set_visible_child_name(n))
            self._sidebar_switcher.pack_start(btn, True, True, 0)
            # Keep toggle button last
            self._sidebar_switcher.reorder_child(self._sidebar_toggle_btn, -1)
            self._sidebar_tab_buttons.append(btn)

            btn.show_all()
            panel.show_all()

        # Register keyboard shortcuts
        for shortcut in plugin.get_keyboard_shortcuts():
            mod, keyval, callback = shortcut
            self._plugin_shortcuts.append((mod, keyval, callback))

    def _unload_plugins(self):
        """Deactivate all loaded plugins."""
        for plugin in self._plugins.values():
            try:
                plugin.deactivate()
            except Exception as e:
                print(f"[plugins] Failed to deactivate {plugin.name}: {e}")
        self._plugins.clear()
        self._plugin_shortcuts.clear()

    # ── Hot toggle (Etap 8) ──

    def _hot_load_plugin(self, mod_name):
        """Load a single plugin from PLUGINS_DIR and register it without restart."""
        if mod_name in self._plugins:
            return
        py_file = os.path.join(PLUGINS_DIR, mod_name + ".py")
        pkg_init = os.path.join(PLUGINS_DIR, mod_name, "__init__.py")
        if os.path.isfile(py_file):
            path = py_file
        elif os.path.isfile(pkg_init):
            path = pkg_init
        else:
            raise FileNotFoundError(
                f"plugin '{mod_name}' not found in {PLUGINS_DIR}"
            )
        spec = importlib.util.spec_from_file_location(
            f"bterminal_plugin_{mod_name}", path,
        )
        module = importlib.util.module_from_spec(spec)
        spec.loader.exec_module(module)
        plugin = module.create_plugin(self)
        self._register_plugin(plugin)

    def _hot_unload_plugin(self, mod_name):
        """Deactivate + remove from sidebar without restart.

        Shortcuts are flat-tracked (no per-plugin id), so we rebuild them
        from the remaining plugins to drop entries that referenced bound
        methods of the deactivated instance.
        """
        plugin = self._plugins.get(mod_name)
        if plugin is None:
            return
        panel = self.sidebar_stack.get_child_by_name(plugin.name)
        if panel is not None:
            self.sidebar_stack.remove(panel)
        for btn in list(self._sidebar_tab_buttons):
            if btn.get_label() == plugin.title:
                self._sidebar_switcher.remove(btn)
                self._sidebar_tab_buttons.remove(btn)
                break
        try:
            plugin.deactivate()
        except Exception as exc:  # noqa: BLE001 — never raise on disable
            print(f"[plugins] deactivate {mod_name} raised: {exc}")
        del self._plugins[mod_name]
        # Rebuild shortcuts from remaining loaded plugins
        self._plugin_shortcuts.clear()
        for p in self._plugins.values():
            try:
                for shortcut in p.get_keyboard_shortcuts():
                    self._plugin_shortcuts.append(shortcut)
            except Exception:
                pass

    # ── Sidecar refcount (Etap 8) ──

    def _sidecar_acquire(self, name):
        """Increment refcount for sidecar `name`. On 0→1 transition, start it.

        No-op if `name` is not a known manifest (defensive against stale tab
        state pointing at a manifest that was deleted between sessions).
        """
        manifest = self.sidecar_manifests.get(name)
        if manifest is None:
            return
        prev = self.sidecar_refcounts[name]
        self.sidecar_refcounts[name] = prev + 1
        if prev == 0:
            try:
                self.sidecar_runner.start(name, manifest)
            except Exception as exc:  # noqa: BLE001
                # Roll back the refcount so the next acquire can retry the
                # start. We deliberately swallow the error — the user will
                # see it next time they hit /api/sidecars/{name}/health.
                self.sidecar_refcounts[name] = prev
                print(f"[sidecar] start {name} failed: {exc}")

    def _sidecar_release(self, name):
        """Decrement refcount. On 1→0 transition, stop the sidecar."""
        if name not in self.sidecar_refcounts:
            return
        prev = self.sidecar_refcounts[name]
        if prev <= 0:
            return
        self.sidecar_refcounts[name] = prev - 1
        if prev == 1:
            try:
                self.sidecar_runner.stop(name)
            except Exception as exc:  # noqa: BLE001
                print(f"[sidecar] stop {name} failed: {exc}")

    def _on_key_press(self, widget, event):
        mod = event.state & Gtk.accelerator_get_default_mod_mask()
        ctrl = Gdk.ModifierType.CONTROL_MASK
        shift = Gdk.ModifierType.SHIFT_MASK

        # Ctrl+B: toggle sidebar
        if mod == ctrl and event.keyval == Gdk.KEY_b:
            self.toggle_sidebar()
            return True

        # Ctrl+G: toggle git panel
        if mod == ctrl and event.keyval == Gdk.KEY_g:
            self.toggle_git_panel()
            return True

        # Ctrl+T: new local tab
        if mod == ctrl and event.keyval == Gdk.KEY_t:
            self.add_local_tab()
            return True

        # Ctrl+Shift+W: close current tab
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_W, Gdk.KEY_w):
            idx = self.notebook.get_current_page()
            if idx >= 0:
                tab = self.notebook.get_nth_page(idx)
                self.close_tab(tab)
            return True

        # Ctrl+Shift+C: copy
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_C, Gdk.KEY_c):
            term = self._get_current_terminal()
            if term:
                term.copy_clipboard_format(Vte.Format.TEXT)
            return True

        # Ctrl+Shift+V: paste (delegate to tab for image handling)
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_V, Gdk.KEY_v):
            idx = self.notebook.get_current_page()
            if idx >= 0:
                tab = self.notebook.get_nth_page(idx)
                if isinstance(tab, TerminalTab):
                    clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
                    if clipboard.wait_is_image_available():
                        if tab._paste_clipboard_image_path():
                            return True
                    tab.terminal.paste_clipboard()
            return True

        # Ctrl+Tab: next tab (wrap around)
        if mod == ctrl and event.keyval in (Gdk.KEY_Tab, Gdk.KEY_ISO_Left_Tab):
            n = self.notebook.get_n_pages()
            if n > 1:
                idx = self.notebook.get_current_page()
                if event.state & shift:
                    self.notebook.set_current_page((idx - 1) % n)
                else:
                    self.notebook.set_current_page((idx + 1) % n)
            return True

        # Ctrl+PageUp: previous tab
        if mod == ctrl and event.keyval == Gdk.KEY_Page_Up:
            idx = self.notebook.get_current_page()
            if idx > 0:
                self.notebook.set_current_page(idx - 1)
            return True

        # Ctrl+PageDown: next tab
        if mod == ctrl and event.keyval == Gdk.KEY_Page_Down:
            idx = self.notebook.get_current_page()
            if idx < self.notebook.get_n_pages() - 1:
                self.notebook.set_current_page(idx + 1)
            return True

        # Plugin keyboard shortcuts
        for p_mod, p_keyval, p_callback in self._plugin_shortcuts:
            if mod == p_mod and event.keyval == p_keyval:
                p_callback()
                return True

        return False

    def _on_delete_event(self, widget, event):
        self._unload_plugins()
        return False


