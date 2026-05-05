"""BTerminal license acceptance subsystem.

Reads LICENSE.md, hashes it, persists user acceptance in options.json
(`license_accepted_hash` + `license_accepted_at`), and shows a modal
GTK dialog with the full license text plus an "I accept" checkbox.

Triggers (per spec):
  - First launch after fresh install: `_require_license_acceptance` is
    called from __main__ before BTerminalApp is created. Decline →
    application quits before showing the main window.
  - Before each update: `_require_license_for_update` is called from
    updater._prompt_update after the user clicks "Aktualizuj…" but
    before _do_update runs. The license text shown is the one fetched
    from origin/master (the version about to be installed). Decline →
    update is aborted.

After acceptance the SHA-256 of the accepted license text is stored
in options.json. On subsequent first-run checks the current LICENSE.md
hash is compared against the stored hash; mismatch (e.g. local edits,
manual git pull that bumped LICENSE without going through the updater
prompt) re-triggers the dialog.
"""

import hashlib
import sys
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk

from bterminal.config import _OPTIONS, _save_options
from bterminal.i18n import _, current_language


# License files live under defaults/license/LICENSE.<lang>.md (live-symlinked
# from the repo via install.sh). LICENSE_DIR resolves to:
#   - dev:       /home/.../ssh_client/defaults/license/
#   - installed: ~/.local/share/bterminal/defaults/license/  (defaults/ is a
#                live symlink into the repo, so files update on `git pull`).
LICENSE_DIR = Path(__file__).parent.parent / "defaults" / "license"

# Fallback language used when the requested locale has no translated license.
_FALLBACK_LANG = "en"


def _resolve_license_path(language=None):
    """Return the absolute path to LICENSE.<language>.md, falling back to
    LICENSE.en.md if the language-specific file does not exist.

    `language` defaults to the active i18n language (`current_language()`).
    The fallback ensures every install has *some* license to display, even
    if a language was added to the UI before its LICENSE.md translation
    landed.
    """
    lang = language or current_language()
    candidate = LICENSE_DIR / f"LICENSE.{lang}.md"
    if not candidate.exists():
        candidate = LICENSE_DIR / f"LICENSE.{_FALLBACK_LANG}.md"
    return str(candidate)


# Backward-compat alias. Tests and external callers may read this as a
# module attribute. Kept as a property-like callable via __getattr__ would
# be cleaner, but plain assignment + a refresh helper is simpler and
# matches existing test patterns (`monkeypatch.setattr(lic, "LICENSE_PATH", ...)`).
LICENSE_PATH = _resolve_license_path()


def _read_license_text(language=None):
    """Return contents of the resolved LICENSE.<lang>.md, or None if
    unreadable. Re-resolves the path each call so a runtime language
    switch (init_locale) is reflected immediately."""
    path = _resolve_license_path(language)
    try:
        with open(path, encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _hash_text(text):
    """SHA-256 of UTF-8 encoded text, hex digest."""
    return hashlib.sha256(text.encode("utf-8")).hexdigest()


def _is_accepted_for(text):
    """True iff the user has accepted exactly this license text."""
    stored = _OPTIONS.get("license_accepted_hash")
    return bool(stored) and stored == _hash_text(text)


def _record_acceptance(text):
    """Persist acceptance of `text` (hash + ISO-8601 timestamp)."""
    _OPTIONS["license_accepted_hash"] = _hash_text(text)
    _OPTIONS["license_accepted_at"] = datetime.now().isoformat(timespec="seconds")
    _save_options(_OPTIONS)


def _show_license_dialog(window, license_text, *, context):
    """Modal license dialog. Returns True iff user accepted.

    The Accept button is disabled until the "I accept" checkbox is
    ticked. Window-close ([X]) is disabled — the user must explicitly
    Accept or Decline.
    """
    dialog = Gtk.Dialog(
        title=_("BTerminal — License Agreement ({context})").format(context=context),
        transient_for=window,
        modal=True,
    )
    dialog.set_default_size(640, 560)
    dialog.set_resizable(True)
    dialog.set_deletable(False)

    dialog.add_button(_("Decline and exit"), Gtk.ResponseType.CANCEL)
    btn_accept = dialog.add_button(_("Accept"), Gtk.ResponseType.ACCEPT)
    btn_accept.get_style_context().add_class("suggested-action")
    btn_accept.set_sensitive(False)
    dialog.set_default_response(Gtk.ResponseType.ACCEPT)

    content = dialog.get_content_area()
    content.set_spacing(0)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    vbox.set_border_width(16)

    intro = Gtk.Label()
    intro.set_markup(
        "<b>" + _("Please read the license agreement below.") + "</b>\n"
        + _("You must accept these terms to use BTerminal.")
    )
    intro.set_xalign(0)
    intro.set_line_wrap(True)
    vbox.pack_start(intro, False, False, 0)

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
    scroll.set_shadow_type(Gtk.ShadowType.IN)
    scroll.set_min_content_height(380)

    buf = Gtk.TextBuffer()
    buf.set_text(license_text)
    view = Gtk.TextView(buffer=buf)
    view.set_editable(False)
    view.set_cursor_visible(False)
    view.set_wrap_mode(Gtk.WrapMode.WORD)
    view.set_left_margin(10)
    view.set_right_margin(10)
    view.set_top_margin(8)
    view.set_bottom_margin(8)
    scroll.add(view)
    vbox.pack_start(scroll, True, True, 0)

    chk = Gtk.CheckButton(label=_("I have read and accept the license terms."))
    chk.connect("toggled", lambda c: btn_accept.set_sensitive(c.get_active()))
    vbox.pack_start(chk, False, False, 0)

    content.pack_start(vbox, True, True, 0)
    content.show_all()

    response = dialog.run()
    dialog.destroy()
    return response == Gtk.ResponseType.ACCEPT


def _require_license_acceptance(window=None):
    """First-run guard. Returns True iff the app may continue.

    Called from __main__ before BTerminalApp is created. Reads the
    on-disk LICENSE.md, compares its hash to options.license_accepted_hash;
    if missing / mismatched, shows the dialog. Returns True on accept
    OR if LICENSE.md is unreadable (fail-open with stderr warning so a
    misconfigured install doesn't lock the user out).
    """
    text = _read_license_text()
    if text is None:
        print(
            f"[bterminal] WARN: cannot read {_resolve_license_path()} — "
            f"license check skipped",
            file=sys.stderr,
        )
        return True
    if _is_accepted_for(text):
        return True
    if not _show_license_dialog(window, text, context=_("First run")):
        return False
    _record_acceptance(text)
    return True


def _require_license_for_update(window, license_text):
    """Pre-update prompt. Returns True iff user accepted the new license.

    `license_text` is the LICENSE.md content fetched from origin/master
    (the version about to be installed). Acceptance is recorded
    optimistically; if the update later rolls back, the next first-run
    check will compare against the rolled-back LICENSE.md hash and
    re-prompt.
    """
    if not _show_license_dialog(window, license_text, context=_("Update")):
        return False
    _record_acceptance(license_text)
    return True
