"""OptionsDialog — File → Options dialog (theme/font/shell/check_updates).

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/dialogs/options.py` in a later migration etap.
"""

import os
import re

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from bterminal.config import _OPTIONS, _save_options
from bterminal.i18n import (
    _,
    SUPPORTED_LANGUAGES,
    current_language,
    init_locale,
    refresh_translatables,
)

# BUG#4: parse param count from common ollama tag suffixes.
# Examples: "qwen2.5-coder:7b" → 7.0, "qwen2.5-coder:0.5b" → 0.5,
# "model:500m" → 0.5, "custom:latest" → None.
_TAG_SIZE_RE = re.compile(r":(\d+(?:\.\d+)?)([bm])$", re.IGNORECASE)


def _model_param_count_b(tag: str) -> float | None:
    """Return parameter count in billions, or None if tag has no
    recognisable size suffix. The b/m suffix maps to billions/millions
    (m → /1000)."""
    if not isinstance(tag, str):
        return None
    m = _TAG_SIZE_RE.search(tag.strip().lower())
    if not m:
        return None
    n = float(m.group(1))
    if m.group(2) == "m":
        n /= 1000.0
    return n


# Threshold below which models are flagged as too small for Aider.
_SMALL_MODEL_THRESHOLD_B = 3.0


class OptionsDialog(Gtk.Dialog):
    """File → Options dialog."""

    def __init__(self, parent):
        super().__init__(title="Opcje BTerminal", transient_for=parent, modal=True)
        # Cap dialog height to 80% of monitor so expanded sections
        # don't push it off-screen (#152). Use Gdk.Display so it
        # respects multi-monitor + HiDPI.
        screen_h = 720  # safe fallback if Gdk geometry unavailable
        try:
            from gi.repository import Gdk as _Gdk
            display = _Gdk.Display.get_default()
            if display:
                monitor = display.get_primary_monitor() \
                          or display.get_monitor(0)
                if monitor:
                    geom = monitor.get_workarea()
                    screen_h = geom.height
        except Exception:
            pass
        # BUG#5 fix: PL strings are 30-40% wider than EN. Bumping
        # default size + min size from (560,…)/(560,480) to
        # (720,…)/(680,480) accommodates the longer labels in PL/DE/FR
        # locales without ucięcia (e.g. 'Sprawdzaj aktualizacje przy
        # starcie:' ≈ 240px just for the label column).
        self.set_default_size(720, min(720, int(screen_h * 0.8)))
        # #153: pin a floor on the dialog so expander collapse can't
        # shrink it below a usable height — otherwise Save/Cancel +
        # the other expander vanish into a 1-line window. Width 680
        # for PL fits; height 480 keeps top sections natural.
        self.set_size_request(680, 480)
        # BUG#5: allow user to expand wider if even 720 not enough
        # for their locale (e.g. some German strings exceed Polish).
        self.set_resizable(True)
        self.set_border_width(0)
        self._app = parent

        # Wrap the entire scrollable content in a ScrolledWindow so
        # expanded sections (AI Providers / Local Models) stay
        # navigable instead of overflowing the dialog (#152). Without
        # this, GtkExpander.set_resize_toplevel() pushed Save/Cancel
        # below the screen edge.
        outer_content = self.get_content_area()
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC,
                            Gtk.PolicyType.AUTOMATIC)
        # BUG#7: lower min-content-height so PL+expanded reliably
        # overflows → scrollbar visible. Was 560 which on 944px screen
        # left enough room for some content layouts to fit naturally.
        scrolled.set_min_content_height(min(440, int(screen_h * 0.55)))
        # propagate-natural-height=True would make the ScrolledWindow
        # grow to fit children → defeats the cap. Keep False so the
        # vertical scrollbar appears once content exceeds min height.
        scrolled.set_propagate_natural_height(False)
        # BUG#7 fix: GTK overlay scrolling makes scrollbars disappear
        # until hover, and the default GtkAdwaita "thin scrollbar" is
        # only ~5px wide. Force a usable persistent gutter.
        scrolled.set_overlay_scrolling(False)
        _scrollbar_css = Gtk.CssProvider()
        _scrollbar_css.load_from_data(
            b"scrolledwindow scrollbar, "
            b"scrolledwindow scrollbar slider { min-width: 12px; }"
        )
        scrolled.get_style_context().add_provider(
            _scrollbar_css,
            Gtk.STYLE_PROVIDER_PRIORITY_APPLICATION,
        )
        outer_content.pack_start(scrolled, True, True, 0)
        # All section widgets get packed into `content` (a vertical
        # box) which lives inside the ScrolledWindow.
        content = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=0)
        scrolled.add(content)

        grid = Gtk.Grid(column_spacing=16, row_spacing=14)
        grid.set_border_width(20)

        row = 0

        # ── Appearance ────────────────────────────────────────────────────────
        section = Gtk.Label()
        section.set_markup("<b>" + _("Appearance") + "</b>")
        section.set_halign(Gtk.Align.START)
        grid.attach(section, 0, row, 2, 1)
        row += 1

        grid.attach(Gtk.Label(label=_("Theme:"), halign=Gtk.Align.END), 0, row, 1, 1)
        self._theme_combo = Gtk.ComboBoxText()
        self._theme_combo.append("dark", _("Dark (Mocha)"))
        self._theme_combo.append("light", _("Light (Latte)"))
        self._theme_combo.set_active_id(_OPTIONS.get("theme", "dark"))
        grid.attach(self._theme_combo, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Label(label=_("Terminal font:"), halign=Gtk.Align.END), 0, row, 1, 1)
        self._font_btn = Gtk.FontButton(font=_OPTIONS.get("font", "Monospace 11"))
        self._font_btn.set_use_font(True)
        self._font_btn.set_hexpand(True)
        grid.attach(self._font_btn, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 0, row, 2, 1)
        row += 1

        # ── Terminal ──────────────────────────────────────────────────────────
        section2 = Gtk.Label()
        section2.set_markup("<b>" + _("Terminal") + "</b>")
        section2.set_halign(Gtk.Align.START)
        grid.attach(section2, 0, row, 2, 1)
        row += 1

        grid.attach(Gtk.Label(label=_("Default shell:"), halign=Gtk.Align.END), 0, row, 1, 1)
        self._shell_entry = Gtk.Entry(hexpand=True)
        self._shell_entry.set_placeholder_text(
            _("default ({shell})").format(shell=os.environ.get("SHELL", "/bin/bash"))
        )
        self._shell_entry.set_text(_OPTIONS.get("shell", ""))
        grid.attach(self._shell_entry, 1, row, 1, 1)
        row += 1

        grid.attach(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 0, row, 2, 1)
        row += 1

        # ── General ───────────────────────────────────────────────────────────
        section3 = Gtk.Label()
        section3.set_markup("<b>" + _("General") + "</b>")
        section3.set_halign(Gtk.Align.START)
        grid.attach(section3, 0, row, 2, 1)
        row += 1

        grid.attach(
            Gtk.Label(label=_("Check for updates at startup:"), halign=Gtk.Align.END),
            0, row, 1, 1,
        )
        self._updates_switch = Gtk.Switch()
        self._updates_switch.set_active(_OPTIONS.get("check_updates_on_start", True))
        self._updates_switch.set_halign(Gtk.Align.START)
        grid.attach(self._updates_switch, 1, row, 1, 1)
        row += 1

        # ── Language ──────────────────────────────────────────────────────────
        grid.attach(Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL), 0, row, 2, 1)
        row += 1

        section4 = Gtk.Label()
        section4.set_markup("<b>" + _("Language") + "</b>")
        section4.set_halign(Gtk.Align.START)
        grid.attach(section4, 0, row, 2, 1)
        row += 1

        grid.attach(
            Gtk.Label(label=_("Interface language:"), halign=Gtk.Align.END),
            0, row, 1, 1,
        )
        self._language_combo = Gtk.ComboBoxText()
        # First entry: Auto-detect (None) — fall through to LANGUAGE / LANG env.
        self._language_combo.append("__auto__", _("Auto-detect"))
        for code, native_name, _en_name in SUPPORTED_LANGUAGES:
            self._language_combo.append(code, native_name)
        # Persisted option uses None for "auto"; map back to sentinel for combo.
        saved_lang = _OPTIONS.get("language")
        self._language_combo.set_active_id(saved_lang or "__auto__")
        # Remember initial value so run_and_apply() can decide whether to
        # show the "Restart required" notice.
        self._initial_language = saved_lang
        grid.attach(self._language_combo, 1, row, 1, 1)
        row += 1

        # Inline notice — hidden until user picks a different language.
        self._lang_restart_lbl = Gtk.Label()
        self._lang_restart_lbl.set_markup(
            "<small><i>" + _("Restart BTerminal to apply language change.") + "</i></small>"
        )
        self._lang_restart_lbl.set_halign(Gtk.Align.START)
        self._lang_restart_lbl.set_xalign(0)
        self._lang_restart_lbl.set_no_show_all(True)
        self._lang_restart_lbl.hide()
        grid.attach(self._lang_restart_lbl, 1, row, 1, 1)
        row += 1
        # Show / hide on change.
        self._language_combo.connect(
            "changed", lambda _c: self._update_restart_notice()
        )

        # Tell-AI checkbox.
        self._tell_ai_check = Gtk.CheckButton(
            label=_("Tell the AI agent which language I speak"),
        )
        self._tell_ai_check.set_active(_OPTIONS.get("tell_ai_language", True))
        grid.attach(self._tell_ai_check, 1, row, 1, 1)
        row += 1

        # Task #70: image-paste vision hint toggle. Default ON —
        # Copilot needs the prefix to call Read tool reliably; Claude
        # ignores the prefix anyway because its template is null.
        # Toggling OFF makes BT paste bare path everywhere.
        self._image_hint_check = Gtk.CheckButton(
            label=_("Auto-add vision hint when pasting images "
                    "into Copilot sessions"),
        )
        self._image_hint_check.set_active(
            _OPTIONS.get("image_paste_hint_enabled", True),
        )
        grid.attach(self._image_hint_check, 1, row, 1, 1)
        row += 1

        content.pack_start(grid, True, True, 0)

        # Task #11 (#83): AI Providers — enable/disable bundled
        # providers. Hidden providers vanish from the Add AI Session
        # dropdown but existing sessions keep rendering. Lazy-built.
        self._providers_expander = Gtk.Expander(label=_("AI Providers"))
        # #153: collapse must NOT shrink the toplevel — otherwise the
        # other expander + Save/Cancel disappear. resize_toplevel=False
        # is the GTK default but we set it explicitly to pin behavior
        # in case a theme/style overrides.
        self._providers_expander.set_resize_toplevel(False)
        self._providers_built = False
        self._provider_checks: dict = {}
        self._providers_expander.connect(
            "notify::expanded",
            lambda exp, _ps: self._lazy_build_providers(),
        )
        content.pack_start(self._providers_expander, False, False, 0)

        # Task #7 (#79): Local Models (Ollama) section. Wrapped in an
        # expander so the dialog stays compact for users who never
        # touch local models. Lazy-built when first expanded.
        self._local_models_expander = Gtk.Expander(
            label=_("Local Models (Ollama)"))
        self._local_models_expander.set_use_markup(False)
        # #153: pin resize_toplevel=False so collapse never shrinks
        # the dialog; the ScrolledWindow handles fit instead.
        self._local_models_expander.set_resize_toplevel(False)
        self._local_models_built = False
        self._local_models_expander.connect(
            "notify::expanded",
            lambda exp, _ps: self._lazy_build_local_models(),
        )
        content.pack_start(self._local_models_expander, False, False, 0)

        content.show_all()
        # Bug fix #115: ScrolledWindow itself wasn't getting show_all
        # so the entire viewport stayed hidden on real GTK + dark theme,
        # body rendering as solid black. content.show_all() recurses
        # only inside the box; the wrapping ScrolledWindow + Dialog
        # outer_content vbox need explicit show_all to make the
        # widget tree visible.
        scrolled.show_all()
        outer_content.show_all()

        self.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        btn_ok = self.add_button(_("Save"), Gtk.ResponseType.OK)
        btn_ok.get_style_context().add_class("suggested-action")
        self.set_default_response(Gtk.ResponseType.OK)

    def _update_restart_notice(self):
        """Toggle the inline 'Restart required' label whenever the user
        picks a language different from the one currently active."""
        picked = self._language_combo.get_active_id()
        new_lang = None if picked == "__auto__" else picked
        if new_lang != self._initial_language:
            self._lang_restart_lbl.show()
        else:
            self._lang_restart_lbl.hide()

    # ─── Task #11 (#83): AI Providers enable/disable ───────────────────────

    def _lazy_build_providers(self):
        """Populate the AI Providers expander on first open."""
        if self._providers_built:
            return
        if not self._providers_expander.get_expanded():
            return
        self._providers_built = True

        from bterminal.providers import get_registry
        registry = get_registry()
        disabled = set(_OPTIONS.get("disabled_providers") or [])

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)
        outer.set_border_width(8)

        info = Gtk.Label(xalign=0, wrap=True, max_width_chars=64)
        info.set_markup(
            "<small>Untick a provider to hide it from the "
            "<b>Add AI Session</b> dropdown. Existing sessions keep "
            "working — only new-session UI filters.</small>"
        )
        outer.pack_start(info, False, False, 0)

        for p in registry.all():
            row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
            chk = Gtk.CheckButton()
            chk.set_active(p.name not in disabled)
            chk.set_label(f"{p.display.icon} {p.display.long_label}")
            row.pack_start(chk, False, False, 0)

            # Status: binary present?
            binary = None
            try:
                binary = p.find_binary()
            except Exception:
                binary = None
            status = Gtk.Label(xalign=0)
            if binary:
                status.set_markup(
                    f'<small><span foreground="green">'
                    f'✓ {binary}</span></small>')
            else:
                status.set_markup(
                    '<small><span foreground="orange">'
                    '⚠ binary not found on $PATH</span></small>')
            row.pack_end(status, False, False, 0)

            outer.pack_start(row, False, False, 0)
            self._provider_checks[p.name] = chk

        # Warning when user disables every provider
        warn = Gtk.Label(xalign=0)
        warn.set_markup(
            '<small><i>Disabling every provider leaves the dropdown '
            'empty — at least one must stay ticked.</i></small>')
        warn.set_no_show_all(True)
        warn.hide()
        outer.pack_start(warn, False, False, 4)
        self._providers_warn_label = warn

        # Live update on toggle
        for chk in self._provider_checks.values():
            chk.connect(
                "toggled", lambda _w: self._update_providers_warning())
        self._update_providers_warning()

        self._providers_expander.add(outer)
        outer.show_all()

    def _update_providers_warning(self):
        if not self._provider_checks:
            return
        any_enabled = any(c.get_active()
                          for c in self._provider_checks.values())
        if any_enabled:
            self._providers_warn_label.hide()
        else:
            self._providers_warn_label.show()

    # ─── Task #7 (#79): Local Models section ───────────────────────────────

    def _lazy_build_local_models(self):
        """Build the Local Models section the first time the expander
        opens — saves a few hundred ms of subprocess spawn for users
        who don't touch this feature."""
        if self._local_models_built:
            return
        if not self._local_models_expander.get_expanded():
            return
        self._local_models_built = True

        from bterminal import ollama_client, system_probe

        outer = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        outer.set_border_width(8)

        # ─── Daemon status row with Start/Stop controls (#151) ────
        # Replaces the old "open a terminal and run `ollama serve &`"
        # text instruction with in-UI buttons. is_daemon_running()
        # uses a 1s HTTP probe to :11434/api/tags so the status is
        # always live (not cached).
        cli_installed = ollama_client.is_cli_installed()
        if not cli_installed:
            banner = Gtk.Label(xalign=0)
            banner.set_markup(
                '<span foreground="orange">'
                '⚠ Ollama not installed. Run <tt>./install.sh '
                '--selected llama</tt> or pick "Install dependencies…" '
                'from the Tools menu.</span>'
            )
            banner.set_line_wrap(True)
            banner.set_max_width_chars(70)
            outer.pack_start(banner, False, False, 0)
        else:
            self._daemon_row = Gtk.Box(
                orientation=Gtk.Orientation.HORIZONTAL, spacing=8)
            self._daemon_status_label = Gtk.Label(xalign=0)
            self._daemon_btn_start = Gtk.Button(label=_("Start daemon"))
            self._daemon_btn_stop = Gtk.Button(label=_("Stop daemon"))
            self._daemon_btn_refresh = Gtk.Button(label=_("Refresh"))
            self._daemon_btn_start.connect(
                "clicked", self._on_ollama_start_clicked)
            self._daemon_btn_stop.connect(
                "clicked", self._on_ollama_stop_clicked)
            self._daemon_btn_refresh.connect(
                "clicked", lambda _w: self._refresh_daemon_status())
            self._daemon_row.pack_start(
                self._daemon_status_label, True, True, 0)
            self._daemon_row.pack_start(
                self._daemon_btn_start, False, False, 0)
            self._daemon_row.pack_start(
                self._daemon_btn_stop, False, False, 0)
            self._daemon_row.pack_start(
                self._daemon_btn_refresh, False, False, 0)
            outer.pack_start(self._daemon_row, False, False, 0)
            self._refresh_daemon_status()

        # Recommendations panel — system_probe → recommend_models text
        rec_label = Gtk.Label(xalign=0)
        rec_label.set_markup("<b>" + _("Recommendations for this machine:")
                              + "</b>")
        outer.pack_start(rec_label, False, False, 0)

        probe = system_probe.probe_system()
        recs = system_probe.recommend_models(probe)
        rec_text = (f"RAM: {probe['ram_gb']} GB  · "
                    f"CPU cores: {probe['cpu_cores']}  · "
                    f"Disk free: {probe['disk_free_gb']} GB\n")
        if probe["gpu_nvidia"] or probe["gpu_amd"]:
            gpus = probe["gpu_nvidia"] + probe["gpu_amd"]
            rec_text += "GPU: " + ", ".join(
                f"{g['name']} ({g['vram_gb']}GB)" for g in gpus
            ) + "\n"
        else:
            rec_text += "GPU: none detected (CPU-only inference)\n"
        rec_text += "\nFits this machine:\n"
        for r in recs:
            mark = "🚀" if r["fits_in_vram"] and r["vram_gb_helpful"] > 0 \
                   else "💻"
            rec_text += f"  {mark} {r['ollama_tag']:<24s}  {r['friendly_name']}\n"
        rec_view = Gtk.TextView(editable=False, cursor_visible=False)
        rec_view.set_monospace(True)
        rec_view.get_buffer().set_text(rec_text)
        rec_scroll = Gtk.ScrolledWindow()
        rec_scroll.set_policy(
            Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        rec_scroll.set_min_content_height(120)
        rec_scroll.add(rec_view)
        outer.pack_start(rec_scroll, False, False, 0)

        # Installed models list
        installed_lbl = Gtk.Label(xalign=0)
        installed_lbl.set_markup("<b>" + _("Installed models:") + "</b>")
        outer.pack_start(installed_lbl, False, False, 0)

        # ListStore: name, size_gb, modified
        self._models_store = Gtk.ListStore(str, str, str)
        self._populate_models_store(ollama_client)
        models_tree = Gtk.TreeView(model=self._models_store)
        for i, title in enumerate(("Name", "Size", "Modified")):
            col = Gtk.TreeViewColumn(
                title, Gtk.CellRendererText(), text=i)
            col.set_resizable(True)
            models_tree.append_column(col)
        self._models_tree = models_tree
        models_scroll = Gtk.ScrolledWindow()
        models_scroll.set_policy(
            Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        models_scroll.set_min_content_height(140)
        models_scroll.add(models_tree)
        outer.pack_start(models_scroll, True, True, 0)

        # Buttons row
        btns = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_pull = Gtk.Button(label=_("Pull…"))
        btn_pull.connect("clicked", lambda _b: self._on_pull_model())
        btns.pack_start(btn_pull, False, False, 0)

        btn_delete = Gtk.Button(label=_("Delete"))
        btn_delete.connect("clicked", lambda _b: self._on_delete_model())
        btns.pack_start(btn_delete, False, False, 0)

        btn_set_default = Gtk.Button(label=_("Set as default for…"))
        btn_set_default.connect(
            "clicked", lambda _b: self._on_set_default_model())
        btns.pack_start(btn_set_default, False, False, 0)

        btn_refresh = Gtk.Button(label=_("Refresh"))
        btn_refresh.connect(
            "clicked",
            lambda _b: self._populate_models_store(ollama_client))
        btns.pack_end(btn_refresh, False, False, 0)
        outer.pack_start(btns, False, False, 0)

        # Disable the buttons that need an installed model when none
        for b in (btn_delete, btn_set_default):
            b.set_sensitive(len(self._models_store) > 0)

        self._local_models_expander.add(outer)
        outer.show_all()

    def _populate_models_store(self, ollama_client) -> None:
        """Refresh the TreeView model from `ollama_client.list_models()`."""
        self._models_store.clear()
        for m in ollama_client.list_models():
            self._models_store.append([
                m.name,
                f"{m.size_gb} GB" if m.size_gb else "?",
                m.modified or "",
            ])

    def _selected_model_name(self) -> str | None:
        sel = self._models_tree.get_selection()
        model, it = sel.get_selected()
        if it is None:
            return None
        return model.get_value(it, 0)

    # ─── Ollama daemon status handlers (task #151) ─────────────────────────

    def _refresh_daemon_status(self):
        """Update status label + button sensitivity to match
        current daemon state. Called after Start/Stop and on
        Refresh button click."""
        from bterminal import ollama_client
        running = ollama_client.is_daemon_running()
        if running:
            self._daemon_status_label.set_markup(
                '<span foreground="lightgreen">'
                '✓ Ollama daemon running on :11434</span>'
            )
            self._daemon_btn_start.set_sensitive(False)
            self._daemon_btn_stop.set_sensitive(True)
        else:
            self._daemon_status_label.set_markup(
                '<span foreground="orange">'
                '⚠ Ollama daemon not running</span>'
            )
            self._daemon_btn_start.set_sensitive(True)
            self._daemon_btn_stop.set_sensitive(False)

    def _on_ollama_start_clicked(self, _button):
        from bterminal import ollama_client
        # Disable button immediately so user can't double-click
        # (Popen already in flight).
        self._daemon_btn_start.set_sensitive(False)
        self._daemon_status_label.set_markup(
            "<i>Starting Ollama daemon…</i>")
        # Process events so the label change paints before we
        # block on the 5s polling loop in start_daemon.
        while Gtk.events_pending():
            Gtk.main_iteration()
        ok, msg = ollama_client.start_daemon()
        self._refresh_daemon_status()
        if not ok:
            self._show_status_dialog(
                "Failed to start Ollama daemon", msg, error=True)

    def _on_ollama_stop_clicked(self, _button):
        from bterminal import ollama_client
        self._daemon_btn_stop.set_sensitive(False)
        self._daemon_status_label.set_markup(
            "<i>Stopping Ollama daemon…</i>")
        while Gtk.events_pending():
            Gtk.main_iteration()
        ok, msg = ollama_client.stop_daemon()
        self._refresh_daemon_status()
        if not ok:
            self._show_status_dialog(
                "Failed to stop Ollama daemon", msg, error=True)

    def _show_status_dialog(self, primary, secondary, error=False):
        msg_type = (Gtk.MessageType.ERROR if error
                    else Gtk.MessageType.INFO)
        dlg = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=msg_type, buttons=Gtk.ButtonsType.OK,
            text=primary,
        )
        if secondary:
            dlg.format_secondary_text(secondary)
        dlg.run()
        dlg.destroy()

    def _on_pull_model(self):
        """Modal entry asking for a model name; fires `ollama pull`
        in a Gio.Subprocess so the UI stays responsive."""
        from bterminal import ollama_client, system_probe

        dlg = Gtk.Dialog(
            title=_("Pull Ollama model"),
            transient_for=self, modal=True,
        )
        dlg.set_default_size(440, -1)
        dlg.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dlg.add_button(_("Pull"), Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)

        lbl = Gtk.Label(xalign=0, wrap=True, max_width_chars=50)
        recs = system_probe.recommend_models(system_probe.probe_system())
        rec_hint = (recs[0]["ollama_tag"] if recs
                    else "qwen2.5-coder:7b")
        # BUG#4 fix: hint that models below 3B params don't work with
        # aider's edit format. Helps the user pick before they hit Pull.
        lbl.set_markup(
            _("Pick a model from the list below or type a custom tag.")
            + "\n<small>"
            + _("Note: models below 3B parameters often fail with "
                "Aider's edit format.")
            + "</small>")
        box.pack_start(lbl, False, False, 0)

        # BUG#8 fix: ComboBoxText.new_with_entry() — dropdown with
        # 7 curated tags + free-form Entry for new releases. User picks
        # from list OR types custom tag.
        _CURATED_OLLAMA_MODELS = [
            "qwen2.5-coder:7b",       # primary aider recommendation
            "qwen2.5-coder:3b",       # smallest still-usable for aider
            "deepseek-coder-v2:16b",  # heavyweight, RAM-permitting
            "codellama:7b",           # fallback general coder
            "llama3.1:8b",            # general-purpose, multi-task
            "qwen2.5:14b",            # general-purpose, larger
            "llava:13b",              # vision-capable
        ]
        combo = Gtk.ComboBoxText.new_with_entry()
        for tag in _CURATED_OLLAMA_MODELS:
            combo.append_text(tag)
        combo.set_active(0)  # default to first curated
        # The internal Entry inherits placeholder/activates_default;
        # set both so Enter triggers OK button.
        entry = combo.get_child()
        entry.set_placeholder_text(rec_hint)
        entry.set_activates_default(True)
        box.pack_start(combo, False, False, 0)

        # BUG#8 fix: link to ollama.com/library for power users who
        # want to browse the full catalog without leaving the dialog.
        link_btn = Gtk.LinkButton.new_with_label(
            "https://ollama.com/library",
            _("Browse all models on ollama.com →"),
        )
        link_btn.set_halign(Gtk.Align.START)
        box.pack_start(link_btn, False, False, 0)

        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.show_all()

        if dlg.run() == Gtk.ResponseType.OK:
            name = combo.get_active_text() or ""
            name = name.strip() or rec_hint
            dlg.destroy()
            # BUG#4: warn-and-confirm for sub-3B models. If user
            # picks YES → fall through to pull. NO → bail out.
            size = _model_param_count_b(name)
            if size is not None and size < _SMALL_MODEL_THRESHOLD_B:
                confirm = Gtk.MessageDialog(
                    transient_for=self, modal=True,
                    message_type=Gtk.MessageType.WARNING,
                    buttons=Gtk.ButtonsType.YES_NO,
                    text=_("{name} has only {size}B parameters").format(
                        name=name, size=size),
                    secondary_text=_(
                        "Models below 3B parameters often fail to "
                        "follow Aider's edit format and produce empty "
                        "or repeated responses. Are you sure you want "
                        "to pull this model?"),
                )
                response = confirm.run()
                confirm.destroy()
                if response != Gtk.ResponseType.YES:
                    return
            self._pull_model_blocking(name, ollama_client)
        else:
            dlg.destroy()

    def _pull_model_blocking(self, name: str, ollama_client):
        """Run pull synchronously with a 'please wait' dialog. We
        keep it simple — pulls are minutes-long but rare; users tolerate
        a modal spinner. Future: Gio.Subprocess + log streaming."""
        info_dlg = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.NONE,
            text=f"Pulling {name}…",
            secondary_text="This may take several minutes for larger "
                           "models. Watch a terminal: ollama pull "
                           f"{name} for live progress.",
        )
        info_dlg.show_all()
        # Pump the GTK main loop so the dialog actually paints before
        # we block on subprocess.
        from gi.repository import GLib
        while GLib.MainContext.default().pending():
            GLib.MainContext.default().iteration(False)

        ok, msg = ollama_client.pull_model(name)
        info_dlg.destroy()

        result = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=(Gtk.MessageType.INFO if ok
                           else Gtk.MessageType.ERROR),
            buttons=Gtk.ButtonsType.OK,
            text=(_("Model pulled") if ok else _("Pull failed")),
            secondary_text=msg,
        )
        result.run()
        result.destroy()
        self._populate_models_store(ollama_client)

    def _on_delete_model(self):
        from bterminal import ollama_client
        name = self._selected_model_name()
        if not name:
            return
        confirm = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.QUESTION,
            buttons=Gtk.ButtonsType.YES_NO,
            text=f"Delete model {name}?",
            secondary_text="Frees disk space; you can re-pull later.",
        )
        resp = confirm.run()
        confirm.destroy()
        if resp != Gtk.ResponseType.YES:
            return
        ok, msg = ollama_client.delete_model(name)
        if ok:
            self._populate_models_store(ollama_client)
        else:
            err = Gtk.MessageDialog(
                transient_for=self, modal=True,
                message_type=Gtk.MessageType.ERROR,
                buttons=Gtk.ButtonsType.OK,
                text="Delete failed",
                secondary_text=msg,
            )
            err.run()
            err.destroy()

    def _on_set_default_model(self):
        """Map the selected model to a provider — writes
        _OPTIONS['default_local_model_for_provider'][provider] = name."""
        name = self._selected_model_name()
        if not name:
            return

        from bterminal.providers import get_registry
        try:
            providers_with_local = [
                p for p in get_registry().all()
                if p.capabilities.local_endpoint_url
            ]
        except Exception:
            providers_with_local = []
        if not providers_with_local:
            err = Gtk.MessageDialog(
                transient_for=self, modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text="No local-LLM providers registered.",
                secondary_text="The Aider provider should appear here "
                               "after install. Restart BTerminal.",
            )
            err.run()
            err.destroy()
            return

        dlg = Gtk.Dialog(
            title=f"Set {name} as default for…",
            transient_for=self, modal=True,
        )
        dlg.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)
        dlg.add_button(_("Apply"), Gtk.ResponseType.OK)
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)
        lbl = Gtk.Label(label="Pick a provider whose new sessions will "
                              "default to this model:",
                        wrap=True, xalign=0, max_width_chars=46)
        box.pack_start(lbl, False, False, 0)
        combo = Gtk.ComboBoxText()
        for p in providers_with_local:
            combo.append(p.name,
                          f"{p.display.icon} {p.display.long_label}")
        combo.set_active(0)
        box.pack_start(combo, False, False, 0)
        dlg.set_default_response(Gtk.ResponseType.OK)
        dlg.show_all()
        resp = dlg.run()
        provider_name = combo.get_active_id()
        dlg.destroy()
        if resp != Gtk.ResponseType.OK or not provider_name:
            return

        # Persist into _OPTIONS — fully written to options.json on
        # Save (same flow as theme/font/etc).
        mapping = dict(
            _OPTIONS.get("default_local_model_for_provider") or {})
        mapping[provider_name] = name
        _OPTIONS["default_local_model_for_provider"] = mapping

    def run_and_apply(self):
        if self.run() != Gtk.ResponseType.OK:
            self.destroy()
            return

        new_theme = self._theme_combo.get_active_id()
        new_font = self._font_btn.get_font()
        new_shell = self._shell_entry.get_text().strip()
        new_updates = self._updates_switch.get_active()
        picked_lang = self._language_combo.get_active_id()
        new_language = None if picked_lang == "__auto__" else picked_lang
        new_tell_ai = self._tell_ai_check.get_active()
        new_image_hint = self._image_hint_check.get_active()

        _OPTIONS["theme"] = new_theme
        _OPTIONS["font"] = new_font
        _OPTIONS["shell"] = new_shell
        _OPTIONS["check_updates_on_start"] = new_updates
        _OPTIONS["language"] = new_language
        _OPTIONS["tell_ai_language"] = new_tell_ai
        _OPTIONS["image_paste_hint_enabled"] = new_image_hint
        # Task #7: default_local_model_for_provider already mutated
        # in-place by _on_set_default_model; persist with the rest.
        # Task #11 (#83): collect provider enable/disable state from
        # the AI Providers expander (only if user opened it — _provider_
        # checks empty otherwise). Refuses to write a list that hides
        # every provider (warning shown live in the UI).
        if self._provider_checks:
            disabled_now = [
                name for name, chk in self._provider_checks.items()
                if not chk.get_active()
            ]
            # Safety: don't let the user save 'all disabled'. Keep the
            # previous list (or empty if first time) so the dropdown
            # never goes empty mid-session.
            from bterminal.providers import get_registry
            all_names = {p.name for p in get_registry().all()}
            if set(disabled_now) < all_names:
                _OPTIONS["disabled_providers"] = disabled_now
            # else: silently keep existing _OPTIONS value
        _save_options(_OPTIONS)

        global FONT
        FONT = new_font

        # BUG#14 fix: target-driven setter so user's combo pick is
        # honored exactly, regardless of `_current_theme` global state.
        self._app._set_theme(new_theme)

        # Apply font to all open terminals
        self._app._apply_font(new_font)

        # Live language switch — re-init the gettext catalog and refresh
        # every widget that registered itself as translatable. Avoids the
        # restart users would otherwise need to see PL ↔ EN in menubar,
        # sidebar tabs, tooltips, etc. Newly-opened dialogs already use
        # the new locale via plain `_()`.
        if new_language != self._initial_language:
            init_locale(new_language)
            refresh_translatables()

        self.destroy()


