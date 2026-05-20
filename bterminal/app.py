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
from bterminal.i18n import _, N_, tr, register_translatable
from bterminal.ctx.helpers import (
    _collect_claude_log,
    _resolve_ctx_project_name,
    _smart_project_name,
)
from bterminal.ctx.dialogs import CtxEditDialog
from bterminal.ui.dialogs.claude_code import ClaudeCodeDialog
from bterminal.ui.dialogs.options import OptionsDialog
from bterminal.ui.dialogs.sudo_password import SudoPasswordDialog
from bterminal.sudo_askpass import SudoAskpassCache
from bterminal.models import AISessionManager, ConsultManager, SessionManager
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
        # New canonical name (T1.6); claude_manager kept as alias for
        # backward-compat with debug_rest, dialogs, and tests until T4.6
        # cleanup. Both names point at the SAME instance.
        self.ai_manager = AISessionManager()
        self.claude_manager = self.ai_manager

        # Shared sudo password cache for AI sessions (BUG#31). Lazily
        # populated via prompt_sudo_password(); cleared on window close.
        self.sudo_askpass = SudoAskpassCache()
        # BUG#31g: flag for /api/debug/sudo_state — True while a modal
        # SudoPasswordDialog is on screen (allows component tests to
        # observe the awaiting_sudo_password state without scraping GTK).
        self._sudo_dialog_pending = False

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

        # Stack child titles (used by GtkStack accessibility / fallback
        # switcher). Registered for live refresh — Gtk.Stack stores per-child
        # 'title' as a child-property, refreshed via child_set_property().
        def _add_panel(child, name, msgid):
            self.sidebar_stack.add_titled(child, name, _(msgid))
            register_translatable(
                self.sidebar_stack, msgid,
                lambda stack, t, c=child: stack.child_set_property(c, "title", t),
            )

        self.sidebar = SessionSidebar(self)
        _add_panel(self.sidebar, "sessions", N_("Sessions"))

        self.ctx_panel = CtxManagerPanel(self)
        _add_panel(self.ctx_panel, "ctx", N_("Ctx"))

        self.consult_panel = ConsultPanel(self)
        _add_panel(self.consult_panel, "consult", N_("Consult"))

        self.task_panel = TaskListPanel(self)
        _add_panel(self.task_panel, "tasks", N_("Tasks"))

        self.memory_panel = MemoryPanel(self)
        _add_panel(self.memory_panel, "memory", N_("Memory"))

        self.skills_panel = SkillsPanel(self)
        _add_panel(self.skills_panel, "skills", N_("Skills"))

        self.files_panel = FilesPanel(self)
        _add_panel(self.files_panel, "files", N_("Files"))

        self.plugin_panel = PluginManagerPanel(self)
        _add_panel(self.plugin_panel, "plugins", N_("Plugins"))

        # Two-row compact tab switcher: row1 = main tabs, row2 = extra + toggle
        switcher = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        switcher.get_style_context().add_class("sidebar-switcher")

        row1 = Gtk.Box(spacing=0)
        row2 = Gtk.Box(spacing=0)

        # Tab definitions: (name, msgid). Labels are built+registered for
        # live refresh via tr() so language change updates them in place.
        _tab_defs_row1 = [("sessions", N_("Sessions")), ("ctx", N_("Ctx")),
                          ("consult", N_("Consult")), ("tasks", N_("Tasks"))]
        _tab_defs_row2 = [("memory", N_("Memory")), ("skills", N_("Skills")),
                          ("files", N_("Files")), ("plugins", N_("Plugins"))]

        self._sidebar_tab_buttons = []
        for name, msgid in _tab_defs_row1 + _tab_defs_row2:
            btn = Gtk.Button()
            tr(btn, "set_label", msgid)
            btn.get_style_context().add_class("sidebar-tab")
            child = btn.get_child()
            if isinstance(child, Gtk.Label):
                child.set_ellipsize(Pango.EllipsizeMode.END)
            btn.connect("clicked", lambda _w, n=name: self.sidebar_stack.set_visible_child_name(n))
            target_row = row1 if (name, msgid) in _tab_defs_row1 else row2
            target_row.pack_start(btn, True, True, 0)
            self._sidebar_tab_buttons.append(btn)

        # Toggle button at end of row2
        self._sidebar_toggle_btn = Gtk.Button(label="◀")
        self._sidebar_toggle_btn.get_style_context().add_class("sidebar-tab")
        tr(self._sidebar_toggle_btn, "set_tooltip_text", "Hide sidebar (Ctrl+B)")
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
        tr(self._show_sidebar_btn, "set_tooltip_text", "Show sidebar (Ctrl+B)")
        self._show_sidebar_btn.set_no_show_all(True)
        self._show_sidebar_btn.connect("clicked", lambda _: self.toggle_sidebar())
        self.notebook.set_action_widget(self._show_sidebar_btn, Gtk.PackType.START)

        # Right action area: theme toggle + git button
        end_box = Gtk.Box(spacing=4)

        self._theme_btn = Gtk.Button(label="☀" if _current_theme == "dark" else "☾")
        self._theme_btn.get_style_context().add_class("theme-toggle")
        tr(self._theme_btn, "set_tooltip_text", "Toggle light/dark theme")
        self._theme_btn.connect("clicked", lambda _: self._toggle_theme())
        end_box.pack_start(self._theme_btn, False, False, 0)

        self._show_git_btn = Gtk.Button(label="Git ◀")
        self._show_git_btn.get_style_context().add_class("sidebar-btn")
        tr(self._show_git_btn, "set_tooltip_text", "Show Git panel (Ctrl+G)")
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
                _(
                    "The file ~/.config/bterminal/options.json was corrupted — "
                    "default settings have been restored.\n\n"
                    "Cause: {exc_type}: {exc}"
                ).format(
                    exc_type=type(_config._options_load_error).__name__,
                    exc=_config._options_load_error,
                ),
            )
            _config._options_load_error = None  # don't show again

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

        def _item(msgid, callback, shortcut=None):
            """Build a translatable menu item. msgid is registered for
            live refresh so the label updates on locale change."""
            it = Gtk.MenuItem()
            tr(it, "set_label", msgid)
            if shortcut:
                it.set_accel_path(shortcut)
            it.connect("activate", lambda _w: callback())
            return it

        def _sep():
            return Gtk.SeparatorMenuItem()

        def _root(msgid, submenu):
            """Build a translatable top-level menubar entry."""
            it = Gtk.MenuItem()
            tr(it, "set_label", msgid)
            it.set_submenu(submenu)
            return it

        # ── File ──────────────────────────────────────────────────────────────
        file_menu = Gtk.Menu()
        file_menu.append(_item(N_("New local tab"), self.add_local_tab))
        file_menu.append(_item(N_("New SSH session…"), lambda: self.sidebar._on_add(None)))
        file_menu.append(_item(N_("New AI session…"), lambda: self.sidebar._on_add_ai_session()))
        file_menu.append(_sep())
        file_menu.append(_item(N_("Options…"), lambda: OptionsDialog(self).run_and_apply()))
        file_menu.append(_sep())
        file_menu.append(_item(N_("Quit"), self._on_quit))
        menubar.append(_root(N_("File"), file_menu))

        # ── View ──────────────────────────────────────────────────────────────
        view_menu = Gtk.Menu()
        view_menu.append(_item(N_("Toggle sidebar (Ctrl+B)"), self.toggle_sidebar))
        view_menu.append(_item(N_("Toggle Git panel (Ctrl+G)"), self.toggle_git_panel))
        view_menu.append(_item(N_("Toggle theme ☀/🌙"), self._toggle_theme))
        view_menu.append(_sep())
        for panel_name, panel_msgid in [
            ("sessions", N_("Sessions")),
            ("ctx",      N_("Ctx")),
            ("consult",  N_("Consult")),
            ("tasks",    N_("Tasks")),
            ("plugins",  N_("Plugins")),
        ]:
            it = Gtk.MenuItem()
            tr(it, "set_label", panel_msgid)
            it.connect("activate", lambda _w, n=panel_name: (
                self.sidebar_stack.set_visible_child_name(n),
                self._sidebar_visible or self.toggle_sidebar(),
            ))
            view_menu.append(it)
        menubar.append(_root(N_("View"), view_menu))

        # ── Tools ─────────────────────────────────────────────────────────────
        tools_menu = Gtk.Menu()
        tools_menu.append(_item(N_("Check for updates"), lambda: _check_for_updates(self, manual=True)))
        tools_menu.append(_item(N_("Errata…"), lambda: _show_errata_dialog(self, _load_local_errata())))
        # Task #62: live audit of system deps — same data as install.sh's
        # [SUMMARY] block, refreshed by re-running shutil.which on demand.
        tools_menu.append(_item(N_("Diagnostics…"), self._show_diagnostics_dialog))
        # Task #6 (#78): GUI installer entry point. Re-runs
        # InstallerWizard for users who want to install/update deps
        # without re-running install.sh from a terminal. Repo_dir is
        # derived from REPO_DIR (set during initial install) so the
        # wizard knows which install.sh to invoke.
        tools_menu.append(_item(
            N_("Install dependencies…"),
            self._show_installer_wizard,
        ))
        # BUG#23: manual entry to the aider model wizard. Unlike the
        # spawn-time path (BUG#19 dialog → 'Uruchom wizarda'), this one
        # has no source session — wizard runs without --session-id so
        # nothing auto-relaunches after it exits. Used when the user
        # wants to pre-pull a model or change the default.
        tools_menu.append(_item(
            N_("Configure local model (aider)…"),
            self._on_open_aider_wizard,
        ))

        # BUG#31e: shared sudo password for AI sessions. "Set" force-prompts
        # even when a cache exists (user may need to re-enter after expiry).
        # "Clear" is greyed out unless a helper is currently cached — we
        # refresh sensitivity on every 'show' of the Tools menu, since the
        # cache state can flip without GUI interaction (e.g. AI session
        # itself calling prompt_sudo_password).
        tools_menu.append(_sep())
        tools_menu.append(_item(
            N_("Set sudo password…"),
            self.prompt_sudo_password,
        ))
        self._clear_sudo_menu_item = _item(
            N_("Clear sudo password"),
            self._on_clear_sudo_password,
        )
        tools_menu.append(self._clear_sudo_menu_item)
        tools_menu.connect(
            "show", lambda _m: self._refresh_sudo_menu_sensitivity()
        )

        tools_root = Gtk.MenuItem()
        tr(tools_root, "set_label", N_("Tools"))
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
        is_claude = isinstance(page, TerminalTab) and page.ai_config is not None
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

    def _build_tab_label(self, text, tab_widget, tooltip=None,
                          icon_pixbuf=None, tab_emoji=None, on_rename=None):
        """Build a tab label with a close button.

        Layout (left → right):
            [provider SVG] [name (renamable)] [random emoji] [×]

        tooltip:     optional hover text (R7a — provider's long_label).
        icon_pixbuf: task #57 — optional GdkPixbuf for the provider logo
                     prepended as a Gtk.Image on the LEFT. Callers must
                     strip the emoji prefix from `text` themselves.
        tab_emoji:   task #67 (2026-05-07) — random per-tab disambiguator
                     emoji rendered as a Gtk.Label on the RIGHT (replaces
                     the color dot from task #65). Each open tab gets a
                     unique emoji from a 30-character pool (collision-
                     avoid then fall back to full pool when ≥30 tabs).
        on_rename:   task #65 — when set, double-click on the label
                     swaps it for an inline Gtk.Entry; pressing Enter
                     calls on_rename(new_text) and restores the label.
                     Used for local terminal tabs so the user can give
                     a generic 'Terminal' tab a meaningful name.
        Stores label reference on tab_widget._tab_label for efficient updates.
        """
        box = Gtk.Box(spacing=4)
        if tooltip:
            box.set_tooltip_text(tooltip)

        if icon_pixbuf is not None:
            img = Gtk.Image.new_from_pixbuf(icon_pixbuf)
            box.pack_start(img, False, False, 0)

        label = Gtk.Label()
        label.set_text(text)
        # Wrap label in EventBox so we can catch double-click without
        # interfering with notebook tab selection (single click).
        evbox = Gtk.EventBox()
        evbox.add(label)
        evbox.set_visible_window(False)
        if on_rename is not None:
            evbox.connect(
                "button-press-event",
                lambda _w, ev: self._maybe_start_rename(
                    ev, label, evbox, on_rename),
            )
        box.pack_start(evbox, True, True, 0)

        # Task #67: random per-tab emoji disambiguator on the RIGHT.
        # Survives VTE title-change because it's a separate widget,
        # not part of the label text.
        if tab_emoji:
            emoji_label = Gtk.Label(label=tab_emoji)
            emoji_label.set_valign(Gtk.Align.CENTER)
            box.pack_start(emoji_label, False, False, 0)

        close_btn = Gtk.Button(label="×")
        close_btn.get_style_context().add_class("tab-close-btn")
        close_btn.set_relief(Gtk.ReliefStyle.NONE)
        close_btn.connect("clicked", lambda _: self.close_tab(tab_widget))
        box.pack_start(close_btn, False, False, 0)

        box.show_all()
        tab_widget._tab_label = label
        # Stash tooltip + emoji so update_tab_title knows what to keep
        tab_widget._tab_tooltip = tooltip
        tab_widget._tab_emoji = tab_emoji
        return box

    def _maybe_start_rename(self, event, label, evbox, on_rename):
        """Task #65: swap a tab Label for an Entry on double-click."""
        if event.type != Gdk.EventType.DOUBLE_BUTTON_PRESS:
            return False
        current = label.get_text()
        entry = Gtk.Entry()
        entry.set_text(current)
        entry.set_width_chars(max(8, len(current) + 2))
        entry.select_region(0, -1)

        def _commit(new_text):
            evbox.remove(entry)
            label.set_text(new_text or current)
            evbox.add(label)
            evbox.show_all()
            if new_text and new_text != current:
                on_rename(new_text)

        def _on_activate(e):
            _commit(e.get_text().strip())

        def _on_focus_out(e, _ev):
            _commit(e.get_text().strip())
            return False

        def _on_key(e, ev):
            if ev.keyval == Gdk.KEY_Escape:
                _commit(current)  # cancel
                return True
            return False

        entry.connect("activate", _on_activate)
        entry.connect("focus-out-event", _on_focus_out)
        entry.connect("key-press-event", _on_key)
        evbox.remove(label)
        evbox.add(entry)
        evbox.show_all()
        entry.grab_focus()
        return True

    def add_local_tab(self):
        from bterminal.ui.terminal_tab import compute_tab_label
        from bterminal.ui.sidebar import _load_icon_pixbuf
        tab = TerminalTab(self)
        label_data = compute_tab_label(None, "Terminal", kind="local")

        # Task #65: SVG icon for local tab too (defaults/icons/local.svg)
        # + double-click rename so users can give a generic 'Terminal'
        # tab a meaningful name without dialog.
        tab_pix = _load_icon_pixbuf("icons/local.svg", size=21)
        if tab_pix is not None:
            display_text = label_data["display"].split(" ", 1)[1] \
                if " " in label_data["display"] else label_data["display"]
        else:
            display_text = label_data["display"]

        def _rename(new_name, t=tab):
            t._local_name = new_name
            self._update_window_title()

        label = self._build_tab_label(
            display_text, tab,
            tooltip=label_data["tooltip"],
            icon_pixbuf=tab_pix,
            tab_emoji=self._pick_tab_emoji(),
            on_rename=_rename,
        )
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

    def open_aider_wizard_tab(self, session_config, on_done=None):
        """Spawn `tools/aider_setup_wizard` in a new tab (BUG#22).

        session_config: the aider session config that triggered the wizard
        (its `id` becomes `--session-id`, so the wizard's sentinel can be
        matched back here in `_on_wizard_done`).

        Sequence end-to-end:
          BUG#19 dialog → 'Uruchom wizarda' → this method →
          wizard tab opens (rich CLI) → user picks & pulls model →
          wizard writes sentinel → child-exited → _on_wizard_done →
          spawn aider tab with the new model in provider_options.
        """
        # Locate wizard binary (mirrors open_wizard_tab's policy — installed
        # tree is preferred, repo path is a fallback for dev).
        candidates = [
            shutil.which("aider_setup_wizard"),
            # install.sh flattens tools/ into INSTALL_DIR (~/.local/share/bterminal/),
            # same convention as memory_wizard / ctx / tasks. This is the
            # production path on installed hosts.
            str(Path.home() / ".local" / "share" / "bterminal"
                / "aider_setup_wizard"),
            # Dev path — running `python -m bterminal` straight from the repo.
            str(Path(__file__).resolve().parent.parent
                / "tools" / "aider_setup_wizard"),
        ]
        wizard_bin = next(
            (p for p in candidates if p and os.path.isfile(p)), None,
        )
        if not wizard_bin:
            show_error_dialog(
                self,
                _(
                    "tools/aider_setup_wizard nie znaleziony. "
                    "Uruchom install.sh aby zaktualizować instalację."
                ),
            )
            return

        session_id = session_config.get("id") or ""
        tab = TerminalTab(self)
        tab.is_aider_wizard_tab = True
        tab.wizard_session_config = session_config
        label = self._build_tab_label("🧙 aider setup", tab)
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        self._update_window_title()

        argv = ["/usr/bin/env", "python3", wizard_bin]
        if session_id:
            argv += ["--session-id", session_id]
        tab.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.environ.get("HOME", "/"),
            argv,
            None,
            GLib.SpawnFlags.DEFAULT,
            None, None, -1, None, None,
        )

        def _on_exit(_terminal, _status):
            GLib.idle_add(self._on_aider_wizard_done, tab, on_done)
        tab.terminal.connect("child-exited", _on_exit)

    def _on_aider_wizard_done(self, wizard_tab, on_done):
        """Wizard tab exited — check sentinel, maybe relaunch aider."""
        from bterminal.providers import aider_probe
        relaunched = False
        original = getattr(wizard_tab, "wizard_session_config", None) or {}
        payload = aider_probe.read_sentinel()
        new_config = aider_probe.compute_relaunch_config(payload, original)
        if new_config is not None:
            # Spawn aider with the chosen model. Reuses normal ai_config
            # entry point so intro prompt / stats bar / rules injection
            # all hook in identically to a user-initiated open.
            from bterminal.ui.terminal_tab import TerminalTab as _TT
            new_tab = _TT(self, ai_config=new_config)
            tab_name = new_config.get("name", "aider")
            new_label = self._build_tab_label(f"🧪 {tab_name}", new_tab)
            new_idx = self.notebook.append_page(new_tab, new_label)
            self.notebook.set_current_page(new_idx)
            self.notebook.set_tab_reorderable(new_tab, True)
            relaunched = True
            try:
                Path(aider_probe.SENTINEL_PATH).unlink()
            except OSError:
                pass
        if on_done:
            on_done(relaunched)
        return False  # idle_add: do not repeat

    def open_ssh_tab(self, session):
        from bterminal.ui.terminal_tab import compute_tab_label
        tab = TerminalTab(self, session=session)
        name = session.get("name", "SSH")
        label_data = compute_tab_label(None, name, kind="ssh")
        label = self._build_tab_label(
            label_data["display"], tab,
            tooltip=label_data["tooltip"],
            tab_emoji=self._pick_tab_emoji(),
        )
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        self._update_window_title()

    def open_ssh_tab_with_macro(self, session, macro):
        from bterminal.ui.terminal_tab import compute_tab_label
        tab = TerminalTab(self, session=session)
        name = f"{session.get('name', 'SSH')} \u2014 {macro.get('name', 'Macro')}"
        label_data = compute_tab_label(None, name, kind="ssh")
        label = self._build_tab_label(
            label_data["display"], tab,
            tooltip=label_data["tooltip"],
            tab_emoji=self._pick_tab_emoji(),
        )
        idx = self.notebook.append_page(tab, label)
        self.notebook.set_current_page(idx)
        self.notebook.set_tab_reorderable(tab, True)
        tab.terminal.grab_focus()
        tab.run_macro(macro)
        self._update_window_title()

    # Task #67 (2026-05-07): per-tab random emoji disambiguator
    # restored. Provider identity is now carried by the SVG pixbuf on
    # the LEFT (task #57); the random emoji on the RIGHT distinguishes
    # MULTIPLE tabs of the same session ("test 🦊", "test 🐙") which
    # is friendlier than the "test #2 #3" suffix from the
    # provider-abstraction era.
    _TAB_EMOJIS: list[str] = [
        "🦊", "🐙", "🎯", "🚀", "⚡", "🔮", "🎲", "🌀", "🦋", "🐺",
        "🎸", "🌊", "🔥", "💎", "🦅", "🐍", "🎪", "🌵", "🦈", "🍄",
        "🎭", "🏴‍☠️", "🛸", "🧊", "🦎", "🐝", "🌻", "🎱", "🦜", "🐲",
    ]

    def _pick_tab_emoji(self) -> str:
        """Return a random emoji from _TAB_EMOJIS, preferring one not
        currently assigned to any open tab. Falls back to the full
        pool when ≥30 tabs are already open (Pythonic mod-pick rather
        than blocking)."""
        import random
        used = set()
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            e = getattr(page, "_tab_emoji", None)
            if e:
                used.add(e)
        available = [e for e in self._TAB_EMOJIS if e not in used] \
                    or self._TAB_EMOJIS
        return random.choice(available)

    def open_ai_tab_one_off(self, config, override_provider=None,
                              force_options=None):
        """Spawn an AI tab with ad-hoc overrides (tasks #60 / #61).

        Use cases:
          - 'Run as Copilot' on a Claude session (override_provider)
          - 'Resume last session' (force_options={'resume': True})
            even when the saved session has resume=False

        Saved session JSON (claude_manager / ai_manager) is NOT
        mutated — we clone the dict, apply overrides locally, and
        spawn through the normal open_claude_tab flow. provider_options
        carries through; build_argv tolerates spurious keys per
        task #54 backcompat (Claude's `sudo` is simply ignored when
        Copilot's argv builder runs).

        override_provider=None / force_options=None / both → no-op
        clone-and-spawn (useful as generic mutation-safe helper).
        """
        cloned = dict(config or {})
        # provider_options is a nested dict — copy it too so future
        # widget mutations on the saved config don't bleed through.
        opts = dict(cloned.get("provider_options") or {})
        if force_options:
            opts.update(force_options)
        # Always assign so the cloned dict has its own provider_options
        # — even when the input lacked the key entirely (legacy session).
        cloned["provider_options"] = opts
        if override_provider:
            cloned["provider"] = override_provider
        self.open_claude_tab(cloned)

    def open_claude_tab(self, config):
        # Per-tab plugin gating: PRZEKAŻ enabled_plugins do TerminalTab
        # constructor PRZED spawn_claude — bo spawn liczy intro prompt
        # i filtruje per tab.enabled_plugins. Bug fix 2026-05-04: previously
        # was set AFTER constructor returned (zbyt późno).
        enabled = config.get("enabled_plugins")
        tab = TerminalTab(self, ai_config=config, enabled_plugins=enabled)
        if tab.enabled_plugins is not None:
            # Acquire sidecars referenced by this tab. The first tab to
            # claim a sidecar starts it; the last to release it stops it
            # (close_tab handles release in Etap 8.k).
            for name in tab.enabled_plugins:
                if name in self.sidecar_manifests:
                    self._sidecar_acquire(name)
        base_name = config.get("name", "Claude Code")
        # Count existing tabs with the same base config name (for ` #N`
        # disambiguation when the user opens the same session multiple times).
        count = 0
        for i in range(self.notebook.get_n_pages()):
            page = self.notebook.get_nth_page(i)
            if isinstance(page, TerminalTab) and page.ai_config:
                if page.ai_config.get("name") == config.get("name"):
                    count += 1

        # T2.7 / R7a: deterministic tab marker from provider.display
        # (no more random emoji per tab).
        from bterminal.providers import get_registry
        from bterminal.ui.terminal_tab import compute_tab_label
        label_data = compute_tab_label(
            ai_config=config,
            session_name=base_name,
            count=count,
            registry=get_registry(),
            kind="ai",
        )
        # task #57: prefer the provider's pixbuf logo over the emoji
        # prefix that compute_tab_label baked into `display`. When we
        # have a pixbuf, strip the leading "<emoji> " from the display
        # text so the rendered tab is "<logo> <session_name>" not
        # "<logo> <emoji> <session_name>".
        from bterminal.ui.sidebar import _provider_pixbuf
        provider = get_registry().get(config.get("provider", "claude")) \
            if get_registry().has(config.get("provider", "claude")) else None
        # 21px matches the sidebar row size (task #57 / 2026-05-07
        # +30% bump per user feedback).
        tab_pix = _provider_pixbuf(provider, size=21) if provider else None
        if tab_pix is not None:
            display_text = label_data["display"].split(" ", 1)[1] \
                if " " in label_data["display"] else label_data["display"]
        else:
            display_text = label_data["display"]

        # Stash deprecated names so legacy readers (sidebar, terminal_tab
        # window-title-changed handler) keep working until T4.6 cleanup.
        # Bug #65 (2026-05-07): _claude_tab_display MUST be the
        # post-pixbuf-strip text — _on_title_changed pushes it through
        # update_tab_title which set_text's it on the label. Storing
        # the unstripped "🤖 test1" caused the emoji to reappear next
        # to the SVG pixbuf on every VTE title-change event.
        tab._claude_tab_emoji = label_data["display"].split(" ", 1)[0]
        tab._claude_tab_display = display_text

        label = self._build_tab_label(
            display_text, tab,
            tooltip=label_data["tooltip"],
            icon_pixbuf=tab_pix,
            tab_emoji=self._pick_tab_emoji(),
        )
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
        """Update tab label when terminal title changes.

        Task #67 (2026-05-07): visual disambiguator is the right-side
        random emoji widget, not Pango markup. VTE title-change just
        replaces the name label text; the emoji widget keeps its glyph
        independently because it lives in its own Gtk.Label.
        """
        idx = self.notebook.page_num(tab)
        if idx >= 0:
            label = getattr(tab, "_tab_label", None)
            if label:
                label.set_text(title)
            else:
                label_widget = self._build_tab_label(
                    title, tab,
                    tooltip=getattr(tab, "_tab_tooltip", None),
                    tab_emoji=getattr(tab, "_tab_emoji", None),
                )
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

    def _on_open_aider_wizard(self):
        """Tools → Configure local model (aider)… (BUG#23).

        Manual entry to the aider model wizard — opens it WITHOUT a
        session_id so the post-wizard sentinel won't trigger an
        automatic aider spawn (matches the user expectation: 'I'm
        just pre-pulling a model, not starting a session right now').
        Reuses the same TerminalTab + child-exited plumbing as the
        BUG#22 spawn-time path.
        """
        # session_config={} → no `id`, so the wizard runs without
        # --session-id; the sentinel (if written) won't match anything
        # in compute_relaunch_config and BT silently does nothing on
        # child-exited. The new model still lands in options.json
        # (that's the point of the manual entry).
        self.open_aider_wizard_tab({})

    def _show_installer_wizard(self):
        """Tools → Install dependencies… (task #6 / #78).

        Re-runs the same 5-page wizard the install.sh GTK auto-spawn
        opens. Useful for users who picked a minimal set initially
        and want to add meld / latex / ollama later without re-running
        install.sh from a terminal.

        repo_dir lookup priority: bterminal.config.REPO_DIR (canonical,
        written by install.sh's last successful run) → ~/.local/share/
        bterminal (post-install location).
        """
        from pathlib import Path
        from bterminal.config import REPO_DIR
        from bterminal.ui.installer_wizard import InstallerWizard

        candidates = [
            REPO_DIR if REPO_DIR else None,
            os.path.expanduser("~/.local/share/bterminal"),
        ]
        repo_dir = None
        for c in candidates:
            if c and os.path.isfile(os.path.join(c, "install.sh")):
                repo_dir = c
                break
        if repo_dir is None:
            show_error_dialog(
                self,
                "Cannot locate install.sh — set ~/.config/bterminal/repo_path.",
            )
            return

        wiz = InstallerWizard(parent=self, repo_dir=repo_dir)
        wiz.run_and_install()
        wiz.destroy()

    def _show_diagnostics_dialog(self):
        """Tools → Diagnostics… (task #62) — live audit of system deps.

        Shows the same content install.sh's [SUMMARY] block emits at
        the end of installation, but refreshed live each time the
        dialog is opened so users can verify that an apt install they
        ran AFTER BTerminal install actually landed.
        """
        from bterminal.diagnostics import (
            audit, audit_ai_providers, format_summary_text,
            missing_features,
        )

        statuses = audit()
        # #109: include AI providers (Claude/Copilot/Aider) in summary
        ai_statuses = audit_ai_providers()
        text = format_summary_text(statuses, ai_statuses=ai_statuses)
        missing = missing_features(statuses)

        dialog = Gtk.Dialog(
            title="BTerminal — Diagnostics",
            transient_for=self, modal=True,
        )
        dialog.set_default_size(560, 480)
        dialog.add_button("Close", Gtk.ResponseType.CLOSE)

        content = dialog.get_content_area()
        content.set_border_width(12)
        content.set_spacing(8)

        if missing:
            warn = Gtk.Label()
            warn.set_markup(
                f"<b>{len(missing)} feature(s) currently disabled "
                f"by missing tools.</b>"
            )
            warn.set_xalign(0)
            warn.set_line_wrap(True)
            content.pack_start(warn, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        view = Gtk.TextView()
        view.set_editable(False)
        view.set_monospace(True)
        view.set_cursor_visible(False)
        view.set_left_margin(8)
        view.set_right_margin(8)
        view.set_top_margin(6)
        view.set_bottom_margin(6)
        view.get_buffer().set_text(text)
        scrolled.add(view)
        content.pack_start(scrolled, True, True, 0)

        content.show_all()
        dialog.run()
        dialog.destroy()

    def _set_theme(self, target: str):
        """BUG#14 fix: target-driven idempotent theme setter. Apply
        the requested theme regardless of `_current_theme` state.
        Calling with current target is a no-op (does NOT flip).

        The legacy `_toggle_theme` (now a thin wrapper) flipped
        based on the global, which broke when state drifted between
        OptionsDialog combo state and `_current_theme` global —
        users picking Dark would sometimes flip to Light and vice
        versa.
        """
        global _current_theme, CSS
        if target == _current_theme:
            return
        if target == "light":
            CATPPUCCIN.update(CATPPUCCIN_LATTE)
            TERMINAL_PALETTE[:] = TERMINAL_PALETTE_LATTE
            self._gtk_settings.set_property(
                "gtk-application-prefer-dark-theme", False)
            self._theme_btn.set_label("☾")
        elif target == "dark":
            CATPPUCCIN.update(CATPPUCCIN_MOCHA)
            TERMINAL_PALETTE[:] = TERMINAL_PALETTE_MOCHA
            self._gtk_settings.set_property(
                "gtk-application-prefer-dark-theme", True)
            self._theme_btn.set_label("☀")
        else:
            raise ValueError(f"unknown theme: {target!r}")
        _current_theme = target
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

    def _toggle_theme(self, *_):
        """Legacy headerbar / View-menu callback: flip dark↔light.
        Delegates to the target-driven _set_theme so callers picking
        a specific value (OptionsDialog) and callers wanting flip
        (toggle button) share the same code path."""
        opposite = "light" if _current_theme == "dark" else "dark"
        self._set_theme(opposite)

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
        if isinstance(tab, TerminalTab) and tab.ai_config:
            proj_dir = tab.ai_config.get("project_dir", "")
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

    def prompt_sudo_password(self):
        """Show modal SudoPasswordDialog and validate via self.sudo_askpass.

        Returns True if a working sudo password is now cached, False if the
        user cancelled or exhausted attempts. The pending flag lets the
        debug-REST surface report awaiting_sudo_password to tests.
        """
        self._sudo_dialog_pending = True
        try:
            dialog = SudoPasswordDialog(self)
            return dialog.run_and_validate(self.sudo_askpass)
        finally:
            self._sudo_dialog_pending = False

    def _on_clear_sudo_password(self):
        """Wipe the cached askpass helper and confirm to the user."""
        self.sudo_askpass.clear()
        dlg = Gtk.MessageDialog(
            transient_for=self,
            modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=_("Hasło sudo wyczyszczone z pamięci"),
        )
        dlg.run()
        dlg.destroy()

    def _refresh_sudo_menu_sensitivity(self):
        """Grey out 'Clear sudo password' when there is nothing to clear.

        Called on Tools menu 'show' so the state is always fresh — sudo
        cache can flip without GUI interaction (e.g. an AI session that
        calls prompt_sudo_password from spawn flow).
        """
        if getattr(self, "_clear_sudo_menu_item", None) is not None:
            self._clear_sudo_menu_item.set_sensitive(self.sudo_askpass.is_set())

    def _on_delete_event(self, widget, event):
        self.sudo_askpass.clear()
        self._unload_plugins()
        return False

    def _on_quit(self):
        """File→Quit handler — same shutdown chain as window close.

        BUG#31i: file_menu.Quit used to call self.destroy() directly,
        which emits 'destroy' but skips the 'delete-event' chain where
        sudo_askpass.clear() lives. Funnel both paths through the same
        cleanup to keep the /tmp/bt-askpass-shared-* tempfile from leaking.
        """
        self.sudo_askpass.clear()
        self._unload_plugins()
        self.destroy()


