"""OptionsDialog — File → Options dialog (theme/font/shell/check_updates).

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/dialogs/options.py` in a later migration etap.
"""

import os

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


class OptionsDialog(Gtk.Dialog):
    """File → Options dialog."""

    def __init__(self, parent):
        super().__init__(title="Opcje BTerminal", transient_for=parent, modal=True)
        self.set_default_size(420, -1)
        self.set_border_width(0)
        self._app = parent

        content = self.get_content_area()
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

        content.pack_start(grid, True, True, 0)
        content.show_all()

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

        _OPTIONS["theme"] = new_theme
        _OPTIONS["font"] = new_font
        _OPTIONS["shell"] = new_shell
        _OPTIONS["check_updates_on_start"] = new_updates
        _OPTIONS["language"] = new_language
        _OPTIONS["tell_ai_language"] = new_tell_ai
        _save_options(_OPTIONS)

        global FONT
        FONT = new_font

        # Apply theme if changed
        if new_theme != _current_theme:
            self._app._toggle_theme()

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


