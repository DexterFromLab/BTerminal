"""BTerminal entry point — `python -m bterminal [--debug-rest]`.

Runs argparse + GLib/Gtk.Application bootstrap. Imports trigger
bterminal/__init__.py (re-exports + helper injection) before main()
starts so all submodules are wired before the GTK loop runs.
"""

import argparse
import os

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gio, GLib, Gtk

from bterminal import debug_rest
from bterminal.app import BTerminalApp
from bterminal.config import _OPTIONS
from bterminal.i18n import init_locale
from bterminal.license import _require_license_acceptance
from bterminal.updater import _check_for_updates


def main():
    # When BTerminal is launched from a desktop entry / file manager, the
    # inherited PATH typically excludes ~/.local/bin and ~/.npm-global/bin,
    # so subprocess.run(["ctx", ...]) (and tasks / consult / memory_wizard /
    # claude) raises FileNotFoundError — surfaced as "Nie ma takiego pliku"
    # in dialogs. Prepend the user paths unconditionally; harmless if
    # already present.
    _user_bins = [
        os.path.expanduser("~/.local/bin"),
        os.path.expanduser("~/.npm-global/bin"),
    ]
    _path_parts = os.environ.get("PATH", "").split(os.pathsep)
    for _bin in reversed(_user_bins):
        if _bin not in _path_parts:
            _path_parts.insert(0, _bin)
    os.environ["PATH"] = os.pathsep.join(_path_parts)

    # i18n — resolve language from options.json / LANGUAGE / LANG / 'en'
    # and install the matching gettext catalog. Must run BEFORE any GTK
    # widget is created so that `_()` calls in dialogs already resolve
    # to the active language. Safe to call before parse_args (no UI yet).
    init_locale(_OPTIONS.get("language"))

    parser = argparse.ArgumentParser(prog="bterminal", add_help=True)
    parser.add_argument(
        "--debug-rest",
        action="store_true",
        help="Enable loopback debug REST API on :7780 (token in ~/.config/bterminal/debug_token).",
    )
    # R1.1: strict — argparse exits 2 z "unrecognized arguments" na nieznane flagi.
    # R1.2: tylko flag, brak env var fallback (BTERMINAL_DEBUG_REST dropped).
    args = parser.parse_args()
    debug_rest.DEBUG_REST_ENABLED = bool(args.debug_rest)

    GLib.set_prgname("bterminal")
    GLib.set_application_name("BTerminal")

    application = Gtk.Application(
        application_id="com.github.DexterFromLab.BTerminal",
        flags=Gio.ApplicationFlags.NON_UNIQUE,
    )

    def on_activate(app_inst):
        # License gate — must accept before the main window is created.
        # On decline the app quits before any session/UI state loads.
        if not _require_license_acceptance(window=None):
            app_inst.quit()
            return
        win = BTerminalApp()
        app_inst.add_window(win)
        if _OPTIONS.get("check_updates_on_start", True):
            GLib.timeout_add(3000, lambda: _check_for_updates(win) or False)

    application.connect("activate", on_activate)
    application.run(None)


if __name__ == "__main__":
    main()
