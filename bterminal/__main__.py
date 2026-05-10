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
    parser.add_argument(
        "--installer",
        action="store_true",
        help=("Open the InstallerWizard (5-page GUI) instead of the main "
              "app. Used by install.sh's GTK auto-spawn (#6 / #78) + by "
              "the BT Tools → 'Install dependencies…' menu item. Requires "
              "BTERMINAL_REPO_DIR env var pointing at install.sh's parent "
              "(set automatically when spawned by install.sh)."),
    )
    # R1.1: strict — argparse exits 2 z "unrecognized arguments" na nieznane flagi.
    # R1.2: tylko flag, brak env var fallback (BTERMINAL_DEBUG_REST dropped).
    args = parser.parse_args()
    debug_rest.DEBUG_REST_ENABLED = bool(args.debug_rest)

    # Task #6: --installer mode — short-circuit to the wizard.
    if args.installer:
        return _run_installer_wizard()

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


def _run_installer_wizard() -> int:
    """Open the InstallerWizard standalone (no main app spawn).

    Resolves repo_dir in this priority:
      1. BTERMINAL_REPO_DIR env var (set by install.sh's GTK auto-spawn)
      2. PWD if it contains install.sh + bterminal/ui/installer_wizard.py
      3. ~/.local/share/bterminal/ if it has them (post-install Tools menu)

    Returns 0 on user-finish, 1 on cancel/error. install.sh treats
    non-zero as "fall back to bash flow" so the user always gets
    SOME working install path.
    """
    from pathlib import Path
    repo_dir = os.environ.get("BTERMINAL_REPO_DIR")
    if not repo_dir or not Path(repo_dir, "install.sh").is_file():
        # Walk a few candidates
        cwd = Path.cwd()
        candidates = [
            cwd,
            Path.home() / ".local" / "share" / "bterminal",
        ]
        for c in candidates:
            if (c / "install.sh").is_file():
                repo_dir = str(c)
                break
        else:
            print("[installer] Cannot locate install.sh; set "
                  "BTERMINAL_REPO_DIR.", flush=True)
            return 1

    GLib.set_prgname("bterminal-installer")
    GLib.set_application_name("BTerminal Installer")
    from bterminal.ui.installer_wizard import InstallerWizard
    wizard = InstallerWizard(parent=None, repo_dir=repo_dir)
    accepted = wizard.run_and_install()
    # Capture the action BEFORE destroy() — destroy() removes the
    # wizard's GTK widgets and clears Python attrs.
    final_action = getattr(wizard, "_action", "install")
    wizard.destroy()
    if not accepted:
        return 1

    # User clicked "Open BTerminal" on the summary page — actually
    # spawn the app. Without this, the wizard exits quietly and
    # the user sees nothing happen.
    # Skip on uninstall (BT no longer exists) — there the summary
    # button is "Close" with the same OK response code.
    if final_action == "uninstall":
        return 0

    # Propagate the wizard's license acceptance into BT's
    # options.json so the spawned BT app doesn't ask again
    # (2026-05-08 fix — was confusing users who'd accepted in
    # the wizard then saw a 2nd license dialog on first BT run).
    try:
        import hashlib as _hashlib
        import json as _json
        from datetime import datetime as _dt
        cfg_dir = Path.home() / ".config" / "bterminal"
        cfg_dir.mkdir(parents=True, exist_ok=True)
        license_path = (Path(repo_dir) / "defaults" / "license"
                        / "LICENSE.en.md")
        if license_path.is_file():
            license_hash = _hashlib.sha256(
                license_path.read_bytes()).hexdigest()
            opts_file = cfg_dir / "options.json"
            try:
                opts = _json.loads(opts_file.read_text(encoding="utf-8"))
            except (OSError, _json.JSONDecodeError):
                opts = {}
            opts["license_accepted_hash"] = license_hash
            opts["license_accepted_at"] = _dt.now().isoformat(
                timespec="seconds")
            opts.setdefault("language", "en")
            opts_file.write_text(
                _json.dumps(opts, indent=2), encoding="utf-8")
    except OSError:
        pass  # spawn anyway; user will just see the dialog once

    import os as _os
    import subprocess as _sp
    launcher = Path.home() / ".local" / "bin" / "bterminal"
    if launcher.is_file() or launcher.is_symlink():
        try:
            # Detached spawn — the wizard process exits, BT stays up.
            _sp.Popen(
                [str(launcher)],
                stdin=_sp.DEVNULL,
                stdout=_sp.DEVNULL,
                stderr=_sp.DEVNULL,
                start_new_session=True,
                env={**_os.environ,
                     "PATH": (f"{Path.home()/'.local/bin'}:"
                              f"{Path.home()/'.npm-global/bin'}:"
                              f"{_os.environ.get('PATH','')}")},
            )
        except OSError as exc:
            print(f"[installer] Failed to spawn bterminal: {exc}",
                  flush=True)
            return 1
    else:
        print(f"[installer] bterminal launcher not found at {launcher}",
              flush=True)
        return 1
    return 0


if __name__ == "__main__":
    rc = main()
    if rc is not None:
        raise SystemExit(rc)
