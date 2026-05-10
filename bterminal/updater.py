"""BTerminal auto-update + errata system.

Functions:
  _check_for_updates(window, manual=False) — git fetch + show prompt if behind
  _prompt_update(window, log, errata) — modal dialog with changelog
  _show_errata_dialog(window, errata) — separate "what's new" dialog
  _do_update(window) — runs install.sh inside the modal with progress bar
                      and live log; rollback dialog on failure
  _load_local_errata() — read errata.json from REPO_DIR (latest first)
  _restart_bterminal() — exec self after successful update

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/updater.py` in a later migration etap.
"""

import json
import os
import re
import subprocess
import sys
import threading

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GLib, Gtk, Pango


from bterminal.config import REPO_DIR, show_error_dialog
from bterminal.i18n import _
from bterminal.license import _require_license_for_update


def _load_local_errata():
    """Load errata.json from the local repo directory."""
    if not REPO_DIR:
        return []
    path = os.path.join(REPO_DIR, "errata.json")
    try:
        with open(path, encoding="utf-8") as fh:
            return json.load(fh)
    except Exception:
        return []


_UPDATE_TIMEOUT = 15


def _check_for_updates(window, manual=False):
    """Check for updates. Manual mode shows a live progress dialog with countdown."""
    if not REPO_DIR or not os.path.isdir(os.path.join(REPO_DIR, ".git")):
        if manual:
            dlg = Gtk.MessageDialog(
                transient_for=window, modal=True,
                message_type=Gtk.MessageType.WARNING,
                buttons=Gtk.ButtonsType.OK,
                text=_("No repository"),
            )
            dlg.format_secondary_text(
                _("Cannot check for updates — repository directory not found.")
            )
            dlg.run()
            dlg.destroy()
        return

    if manual:
        _manual_update_check(window)
    else:
        def _bg():
            try:
                subprocess.run(
                    ["git", "fetch", "origin", "master"],
                    cwd=REPO_DIR, capture_output=True, timeout=_UPDATE_TIMEOUT,
                )
                local = subprocess.run(
                    ["git", "rev-parse", "master"],
                    cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                remote = subprocess.run(
                    ["git", "rev-parse", "origin/master"],
                    cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                if local and remote and local != remote:
                    log = subprocess.run(
                        ["git", "log", "--oneline", f"{local}..{remote}"],
                        cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
                    ).stdout.strip()
                    errata_raw = subprocess.run(
                        ["git", "show", "origin/master:errata.json"],
                        cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
                    )
                    errata = []
                    if errata_raw.returncode == 0:
                        try:
                            errata = json.loads(errata_raw.stdout)
                        except Exception:
                            pass
                    GLib.idle_add(_prompt_update, window, log, errata)
            except Exception:
                pass
        threading.Thread(target=_bg, daemon=True).start()


def _manual_update_check(window):
    """Show a live progress dialog with countdown, then display result inline."""
    dialog = Gtk.Dialog(
        title=_("Checking for updates"),
        transient_for=window,
        modal=True,
    )
    dialog.set_default_size(400, -1)

    content = dialog.get_content_area()
    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=12)
    vbox.set_border_width(20)

    spinner = Gtk.Spinner()
    spinner.start()
    row = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=10)
    row.pack_start(spinner, False, False, 0)
    status_lbl = Gtk.Label(
        label=_("Connecting to server... ({seconds}s)").format(seconds=_UPDATE_TIMEOUT)
    )
    status_lbl.set_xalign(0)
    row.pack_start(status_lbl, True, True, 0)
    vbox.pack_start(row, False, False, 0)

    content.pack_start(vbox, True, True, 0)
    content.show_all()

    btn_close = dialog.add_button(_("Cancel"), Gtk.ResponseType.CANCEL)

    state = {"done": False, "remaining": _UPDATE_TIMEOUT, "result": None}

    def _countdown():
        if state["done"]:
            return False
        state["remaining"] -= 1
        if state["remaining"] <= 0:
            state["done"] = True
            spinner.stop()
            status_lbl.set_text(_("Cannot check for updates — timed out."))
            btn_close.set_label(_("Close"))
            return False
        status_lbl.set_text(
            _("Connecting to server... ({seconds}s)").format(seconds=state["remaining"])
        )
        return True

    GLib.timeout_add(1000, _countdown)

    def _finish():
        if state["done"]:
            return False
        state["done"] = True
        spinner.stop()
        res = state["result"]
        if res == "none":
            status_lbl.set_text(_("BTerminal is up to date. No new updates."))
            btn_close.set_label(_("Close"))
        elif isinstance(res, tuple) and res[0] == "updates":
            dialog.response(Gtk.ResponseType.OK)
        else:
            status_lbl.set_text(_("Cannot check for updates."))
            btn_close.set_label(_("Close"))
        return False

    def _fetch():
        try:
            subprocess.run(
                ["git", "fetch", "origin", "master"],
                cwd=REPO_DIR, capture_output=True, timeout=_UPDATE_TIMEOUT,
            )
            local = subprocess.run(
                ["git", "rev-parse", "master"],
                cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            remote = subprocess.run(
                ["git", "rev-parse", "origin/master"],
                cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
            ).stdout.strip()
            if local and remote and local != remote:
                log = subprocess.run(
                    ["git", "log", "--oneline", f"{local}..{remote}"],
                    cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
                ).stdout.strip()
                errata_raw = subprocess.run(
                    ["git", "show", "origin/master:errata.json"],
                    cwd=REPO_DIR, capture_output=True, text=True, timeout=5,
                )
                errata = []
                if errata_raw.returncode == 0:
                    try:
                        errata = json.loads(errata_raw.stdout)
                    except Exception:
                        pass
                state["result"] = ("updates", log, errata)
            else:
                state["result"] = "none"
        except Exception:
            state["result"] = "error"
        GLib.idle_add(_finish)

    threading.Thread(target=_fetch, daemon=True).start()

    dialog.run()
    dialog.destroy()

    res = state["result"]
    if isinstance(res, tuple) and res[0] == "updates":
        _prompt_update(window, res[1], res[2])


_RESP_ERRATA = 10
_RESP_RESTART = 11


def _prompt_update(window, log, errata=None):
    """Show update dialog on the main thread."""
    dialog = Gtk.Dialog(
        title=_("New BTerminal version"),
        transient_for=window,
        modal=True,
    )
    dialog.set_default_size(520, 380)
    dialog.set_resizable(False)
    dialog.set_border_width(0)

    content = dialog.get_content_area()
    content.set_spacing(0)

    # Fixed-height scrollable area
    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_border_width(0)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    vbox.set_border_width(20)

    title_lbl = Gtk.Label()
    title_lbl.set_markup("<b>" + _("A new version of BTerminal is available") + "</b>")
    title_lbl.set_halign(Gtk.Align.START)
    vbox.pack_start(title_lbl, False, False, 0)

    if errata:
        latest = errata[0]
        admin_msg = latest.get("message", "").strip()
        if admin_msg:
            sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
            vbox.pack_start(sep, False, False, 4)
            msg_lbl = Gtk.Label(label=admin_msg)
            msg_lbl.set_line_wrap(True)
            msg_lbl.set_xalign(0)
            msg_lbl.set_halign(Gtk.Align.FILL)
            vbox.pack_start(msg_lbl, False, False, 0)

    if log:
        sep2 = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        vbox.pack_start(sep2, False, False, 4)
        log_lbl = Gtk.Label()
        log_lbl.set_markup(f"<small>{GLib.markup_escape_text(log)}</small>")
        log_lbl.set_xalign(0)
        log_lbl.set_halign(Gtk.Align.START)
        log_lbl.set_selectable(True)
        vbox.pack_start(log_lbl, False, False, 0)

    scroll.add(vbox)
    content.pack_start(scroll, True, True, 0)
    content.show_all()

    dialog.add_button(_("Show errata"), _RESP_ERRATA)
    dialog.add_button(_("Not now"), Gtk.ResponseType.CANCEL)
    btn_update = dialog.add_button(_("Update and restart"), Gtk.ResponseType.YES)
    btn_update.get_style_context().add_class("suggested-action")
    dialog.set_default_response(Gtk.ResponseType.YES)

    while True:
        response = dialog.run()
        if response == _RESP_ERRATA:
            _show_errata_dialog(window, errata or [])
            continue
        break

    dialog.destroy()
    if response == Gtk.ResponseType.YES:
        # License gate — show the LICENSE.md from origin/master (the
        # version about to be installed). Decline aborts the update;
        # if origin's LICENSE cannot be retrieved, fall back to the
        # local one so the user still has a chance to accept.
        new_license = _fetch_remote_license() or _read_local_license()
        if new_license is None:
            show_error_dialog(
                window,
                "Cannot read LICENSE.md (neither remote nor local). "
                "Update aborted.",
            )
            return False
        if not _require_license_for_update(window, new_license):
            return False
        _do_update(window)
    return False


def _remote_license_blob_path():
    """Resolve which LICENSE blob to fetch from origin/master.

    The repo root `LICENSE.md` is a symlink to
    `defaults/license/LICENSE.en.md`. `git show origin/master:LICENSE.md`
    on a symlink returns the SYMLINK TARGET (a path string), not the
    pointed-to file's contents — the dialog ends up showing that path
    instead of license text.

    We read the active UI language and target the per-language blob
    directly. Falls back to LICENSE.en.md if the active language has
    no translation in origin/master yet.
    """
    from bterminal.i18n import current_language
    lang = current_language() or "en"
    return f"defaults/license/LICENSE.{lang}.md"


def _git_show_origin(blob_path):
    """`git show origin/master:<blob_path>` → str or None on any error."""
    if not REPO_DIR:
        return None
    try:
        result = subprocess.run(
            ["git", "show", f"origin/master:{blob_path}"],
            cwd=REPO_DIR, capture_output=True, text=True, timeout=10,
        )
        if result.returncode == 0:
            return result.stdout
    except Exception:
        pass
    return None


def _fetch_remote_license():
    """Return remote LICENSE text from origin/master, or None on failure."""
    blob = _remote_license_blob_path()
    text = _git_show_origin(blob)
    if text is None and not blob.endswith("LICENSE.en.md"):
        text = _git_show_origin("defaults/license/LICENSE.en.md")
    return text


def _read_local_license():
    """Return on-disk LICENSE text (per-active-language), or None.

    Uses license._resolve_license_path() so we read the actual
    per-language file instead of dereferencing the root LICENSE.md
    symlink ourselves.
    """
    if not REPO_DIR:
        return None
    try:
        from bterminal.license import _resolve_license_path
        with open(_resolve_license_path(), encoding="utf-8") as fh:
            return fh.read()
    except OSError:
        return None


def _show_errata_dialog(window, errata):
    """Show all errata entries in a scrollable dialog."""
    dialog = Gtk.Dialog(
        title=_("BTerminal errata"),
        transient_for=window,
        modal=True,
    )
    dialog.set_default_size(560, 480)
    dialog.add_button(_("Close"), Gtk.ResponseType.CLOSE)

    scroll = Gtk.ScrolledWindow()
    scroll.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
    scroll.set_border_width(0)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=16)
    vbox.set_border_width(20)

    if not errata:
        empty = Gtk.Label(label=_("No errata entries."))
        empty.set_halign(Gtk.Align.START)
        vbox.pack_start(empty, False, False, 0)
    else:
        for entry in errata:
            date = entry.get("date", "")
            message = entry.get("message", "").strip()
            changes = entry.get("changes", [])

            entry_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=6)

            header = Gtk.Label()
            header.set_markup(f"<b>{GLib.markup_escape_text(date)}</b>")
            header.set_halign(Gtk.Align.START)
            entry_box.pack_start(header, False, False, 0)

            if message:
                msg_lbl = Gtk.Label(label=message)
                msg_lbl.set_line_wrap(True)
                msg_lbl.set_xalign(0)
                msg_lbl.set_halign(Gtk.Align.FILL)
                entry_box.pack_start(msg_lbl, False, False, 0)

            for change in changes:
                row = Gtk.Label(label=f"• {change}")
                row.set_xalign(0)
                row.set_halign(Gtk.Align.START)
                row.set_line_wrap(True)
                entry_box.pack_start(row, False, False, 0)

            vbox.pack_start(entry_box, False, False, 0)
            vbox.pack_start(
                Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL),
                False, False, 0,
            )

    scroll.add(vbox)
    dialog.get_content_area().pack_start(scroll, True, True, 0)
    dialog.show_all()
    dialog.run()
    dialog.destroy()


def _restart_bterminal():
    """Restart the BTerminal process in-place.

    BUG#15: cannot simply re-exec `sys.executable + sys.argv` — when
    BTerminal was launched via `python -m bterminal`, sys.argv[0] is
    set to the FULL path of the package's __main__.py file (PEP 338).
    Running `python <that_path>` directly puts the bterminal/ dir
    itself onto sys.path[0], which means `from bterminal import …`
    inside __main__.py fails with ModuleNotFoundError.

    Correct restart paths, in order of preference:
      1. The launcher symlink (~/.local/bin/bterminal) — its wrapper
         shell script `cd`s into the install dir + execs
         `python3 -m bterminal`, so the package is importable.
      2. Fallback: `python3 -m bterminal` directly. Requires that
         the install dir is on sys.path AND the package is found
         there. Works when launcher is missing but the install is
         in the standard location.

    sys.argv[1:] (user flags, e.g. --debug-rest) is passed through
    in both cases. sys.argv[0] is dropped — it points at the
    module file, not a re-launch entry point.
    """
    launcher = os.path.expanduser("~/.local/bin/bterminal")
    user_args = sys.argv[1:]
    if os.path.isfile(launcher) or os.path.islink(launcher):
        os.execv(launcher, [launcher] + user_args)
    else:
        # Fallback — works as long as bterminal package is importable
        # from CWD or PYTHONPATH (true for the standard install dir).
        os.execv(sys.executable,
                 [sys.executable, "-m", "bterminal"] + user_args)


# ─── V1: dirty-tree-safe git pull (pure helpers, testable) ─────────────────


def _git_repo_is_dirty(cwd: str) -> bool:
    """True iff `git status --porcelain` reports any modified, staged
    or untracked files. Used by `_do_update` to detect when an auto-
    stash is needed before `git pull` (V1 fix — image bug 2026-05-06).
    """
    try:
        result = subprocess.run(
            ["git", "status", "--porcelain"],
            cwd=cwd, capture_output=True, text=True, timeout=10,
        )
    except (OSError, subprocess.TimeoutExpired):
        return False
    if result.returncode != 0:
        return False
    return bool(result.stdout.strip())


def _git_pull_with_autostash(cwd: str, stash_msg: str = "bterminal-auto-update") -> dict:
    """V1 — git pull that survives uncommitted local changes.

    Flow:
      1. If dirty → `git stash push -u -m <msg>` (untracked too).
      2. `git pull origin master`.
      3. If we stashed → `git stash pop`. Conflict during pop leaves
         the stash intact so the user can resolve manually.

    Returns dict:
      ok            (bool)  — True iff pull succeeded AND stash pop
                              succeeded (when applicable).
      stashed       (bool)  — True iff we created a stash.
      stash_popped  (bool)  — True iff the stash was successfully popped
                              (False on conflict — stash kept for user).
      error         (str|None) — diagnostic on failure.
      pull_stdout   (str)   — captured pull output (status display).
    """
    dirty = _git_repo_is_dirty(cwd)

    stashed = False
    if dirty:
        stash = subprocess.run(
            ["git", "stash", "push", "-u", "-m", stash_msg],
            cwd=cwd, capture_output=True, text=True, timeout=15,
        )
        if stash.returncode != 0:
            return {
                "ok": False, "stashed": False, "stash_popped": False,
                "error": (
                    "git stash push failed:\n"
                    + (stash.stderr or stash.stdout)
                ),
                "pull_stdout": "",
            }
        stashed = True

    pull = subprocess.run(
        ["git", "pull", "origin", "master"],
        cwd=cwd, capture_output=True, text=True, timeout=30,
    )

    if pull.returncode != 0:
        # Pull failed — restore stash before bailing so user's tree
        # comes back to its pre-update state.
        if stashed:
            subprocess.run(
                ["git", "stash", "pop"], cwd=cwd,
                capture_output=True, text=True, timeout=15,
            )
            stashed = False
        return {
            "ok": False, "stashed": False, "stash_popped": False,
            "error": f"git pull failed:\n{pull.stderr or pull.stdout}",
            "pull_stdout": pull.stdout,
        }

    if not stashed:
        return {
            "ok": True, "stashed": False, "stash_popped": False,
            "error": None, "pull_stdout": pull.stdout,
        }

    # Pull succeeded — restore stash
    pop = subprocess.run(
        ["git", "stash", "pop"], cwd=cwd,
        capture_output=True, text=True, timeout=15,
    )
    if pop.returncode != 0:
        # Conflict on pop. Keep the stash; user resolves later via
        # `git stash list` + manual merge.
        return {
            "ok": False, "stashed": True, "stash_popped": False,
            "error": (
                "git stash pop produced conflicts — stash kept for "
                "manual resolution.\n"
                + (pop.stderr or pop.stdout)
            ),
            "pull_stdout": pull.stdout,
        }

    return {
        "ok": True, "stashed": True, "stash_popped": True,
        "error": None, "pull_stdout": pull.stdout,
    }


def _do_update(window):
    """Pull changes and run install.sh in a background thread."""
    dialog = Gtk.Dialog(title=_("BTerminal update"), transient_for=window, modal=True)
    dialog.set_default_size(480, 220)
    dialog.set_resizable(False)
    dialog.set_deletable(False)

    vbox = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)
    vbox.set_border_width(20)

    title_lbl = Gtk.Label()
    title_lbl.set_markup("<b>" + _("Update in progress…") + "</b>")
    title_lbl.set_halign(Gtk.Align.START)
    vbox.pack_start(title_lbl, False, False, 0)

    progress = Gtk.ProgressBar()
    progress.set_pulse_step(0.08)
    vbox.pack_start(progress, False, False, 0)

    log_lbl = Gtk.Label(label="")
    log_lbl.set_xalign(0)
    log_lbl.set_halign(Gtk.Align.START)
    log_lbl.set_line_wrap(False)
    log_lbl.set_ellipsize(3)  # PANGO_ELLIPSIZE_END
    log_lbl.get_style_context().add_class("dim-label")
    vbox.pack_start(log_lbl, False, False, 0)

    dialog.get_content_area().pack_start(vbox, True, True, 0)
    dialog.show_all()

    # Pulse the progress bar every 80 ms
    pulse_source = GLib.timeout_add(80, lambda: (progress.pulse(), True)[1])

    log_lines: list[str] = []

    _ansi_re = re.compile(r"\033\[[0-9;]*[a-zA-Z]")

    def _append_line(line: str):
        line = _ansi_re.sub("", line).rstrip()
        if not line:
            return False
        log_lines.append(line)
        log_lbl.set_text(log_lines[-1])
        return False

    spinner_dialog = dialog  # keep alias for _update_done compatibility

    def _run():
        stderr_buf: list[str] = []
        try:
            # V1: dirty-tree-safe pull. Stashes uncommitted changes
            # before pull and restores them after; bails cleanly on
            # any failure path (image bug from 2026-05-06).
            pull_result = _git_pull_with_autostash(REPO_DIR)
            if pull_result["stashed"]:
                GLib.idle_add(_append_line,
                              "stashed local changes (will restore after pull)")
            GLib.idle_add(
                _append_line,
                "git pull: " + ("OK" if pull_result["ok"] else "failed"),
            )
            if not pull_result["ok"]:
                GLib.idle_add(GLib.source_remove, pulse_source)
                GLib.idle_add(_update_done, window, dialog,
                              pull_result["error"]
                              or "git pull failed (unknown error)")
                return
            if pull_result["stashed"] and pull_result["stash_popped"]:
                GLib.idle_add(_append_line,
                              "restored local changes from stash")

            proc = subprocess.Popen(
                ["bash", os.path.join(REPO_DIR, "install.sh"), "--no-sudo"],
                stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                text=True, bufsize=1,
            )
            import select as _select
            while True:
                reads = [proc.stdout, proc.stderr]
                ready, _, _ = _select.select(reads, [], [], 0.1)
                for fd in ready:
                    line = fd.readline()
                    if not line:
                        continue
                    if fd is proc.stderr:
                        stderr_buf.append(line.rstrip())
                    else:
                        GLib.idle_add(_append_line, line)
                if proc.poll() is not None:
                    # drain remaining
                    for line in proc.stdout:
                        GLib.idle_add(_append_line, line)
                    for line in proc.stderr:
                        stderr_buf.append(line.rstrip())
                    break

            GLib.idle_add(GLib.source_remove, pulse_source)
            stderr_str = "\n".join(stderr_buf)
            if proc.returncode != 0:
                if "BTERMINAL_ROLLBACK_OK" in stderr_str:
                    msg = _(
                        "The new version of BTerminal could not be installed.\n\n"
                        "The previous version was restored automatically — "
                        "BTerminal continues to work normally."
                    )
                else:
                    msg = _(
                        "Installation failed and no previous version is available "
                        "to restore.\n\nDetails:\n{details}"
                    ).format(details=stderr_str or ''.join(log_lines[-5:]))
                GLib.idle_add(_update_done, window, dialog, msg)
                return
            GLib.idle_add(_update_done, window, dialog, None)
        except Exception as e:
            GLib.idle_add(GLib.source_remove, pulse_source)
            GLib.idle_add(_update_done, window, dialog, str(e))

    threading.Thread(target=_run, daemon=True).start()


def _update_done(window, spinner_dialog, error):
    """Handle update result on the main thread."""
    spinner_dialog.destroy()
    if error:
        show_error_dialog(window, error)
    else:
        _restart_bterminal()
    return False


