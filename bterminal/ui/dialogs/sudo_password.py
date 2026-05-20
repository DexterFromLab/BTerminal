"""SudoPasswordDialog — GTK modal okno do wprowadzenia hasła sudo.

Dialog jest częścią mechanizmu shared askpass (BUG#31): hasło wpisane raz
jest zapamiętane w SudoAskpassCache na poziomie BTerminalApp, a wszystkie
kolejne taby AI z sudo używają tego samego tempfile askpass bez ponownego
pytania.

Walidacja:
    run_and_validate(cache) wywołuje cache.ensure(password). Maksymalnie
    3 próby; przy 3 błędnych dialog się zamyka i zwraca False — wtedy
    TerminalTab._build_spawn_script wraca do interactive per-tab read.
"""

from __future__ import annotations

from typing import TYPE_CHECKING

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from bterminal.i18n import _

if TYPE_CHECKING:
    from bterminal.sudo_askpass import SudoAskpassCache


_MAX_ATTEMPTS = 3


class SudoPasswordDialog(Gtk.Dialog):
    """Modal dialog z polem hasła + retry loop z licznikiem prób.

    Usage:
        dlg = SudoPasswordDialog(parent)
        ok = dlg.run_and_validate(app.sudo_askpass)
        if ok:
            # cache.get_path() ma teraz ważny askpass tempfile
            ...
    """

    def __init__(self, parent: Gtk.Window | None = None) -> None:
        super().__init__(
            title=_("BTerminal — sudo"),
            transient_for=parent,
            modal=True,
        )
        self.set_default_size(380, -1)

        self.add_button(_("Anuluj"), Gtk.ResponseType.CANCEL)
        self.add_button(_("OK"), Gtk.ResponseType.OK)
        self.set_default_response(Gtk.ResponseType.OK)

        box = self.get_content_area()
        box.set_spacing(10)
        box.set_margin_start(20)
        box.set_margin_end(20)
        box.set_margin_top(10)
        box.set_margin_bottom(10)

        self.label = Gtk.Label(
            label=_("Podaj hasło sudo (wspólne dla sesji AI):"),
            xalign=0,
        )
        self.label.set_line_wrap(True)
        box.pack_start(self.label, False, False, 0)

        self.entry = Gtk.Entry()
        self.entry.set_visibility(False)
        self.entry.set_activates_default(True)
        box.pack_start(self.entry, False, False, 0)

        self.error_label = Gtk.Label(xalign=0)
        self.error_label.get_style_context().add_class("error")
        self.error_label.set_no_show_all(True)
        box.pack_start(self.error_label, False, False, 0)

        self.show_all()

    def _show_error(self, text: str) -> None:
        self.error_label.set_text(text)
        self.error_label.show()

    def run_and_validate(self, cache: "SudoAskpassCache") -> bool:
        """Loop max 3 razy: OK → walidacja przez cache.ensure().

        Zwraca True gdy cache ma teraz ważny askpass path; False gdy
        user anulował lub wyczerpał próby.
        """
        attempts = 0
        try:
            while attempts < _MAX_ATTEMPTS:
                response = self.run()
                if response != Gtk.ResponseType.OK:
                    return False

                password = self.entry.get_text()
                if not password:
                    self._show_error(_("Hasło nie może być puste"))
                    self.entry.grab_focus()
                    continue

                if cache.ensure(password):
                    return True

                attempts += 1
                if attempts >= _MAX_ATTEMPTS:
                    return False
                self._show_error(
                    _("Błędne hasło sudo. Próba %d/3") % (attempts + 1)
                )
                self.entry.set_text("")
                self.entry.grab_focus()
            return False
        finally:
            self.destroy()
