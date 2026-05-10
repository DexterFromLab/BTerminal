"""bterminal.ui.installer_wizard — GTK installer wizard (task #5 / #77).

5-page wizard that orchestrates `install.sh` instead of forcing users
through bash output. Pattern forked from CtxSetupWizard
(bterminal/ctx/dialogs.py:43+) — same `Gtk.Stack` + state machine.

Pages:
  1. Welcome      — License accept (text shown, OK enables Next)
  2. Inventory    — live diagnostics.audit() table (✓/✗ per dep)
  3. Picks        — checkboxes per 'auto'/'optional' dep + Llama opt-in
  4. Progress     — install.sh subprocess + log streaming + progress bar
  5. Summary      — final ✓/✗ table + 'Open BTerminal' button

Communication with install.sh (#76):
  - Spawned with `--headless --selected csv --status-json --no-sudo?`
    so progress bar steps deterministically through phase boundaries.
  - JSON status lines parsed → progress bar update + phase label.
  - Stdout (everything else, including ANSI) → log Gtk.TextView with
    a tiny ANSI strip pass.

Testability:
  - Pure-helper `parse_status_json_line(line) -> dict|None` decoupled
    from GTK so tests don't need a display.
  - Pure-helper `strip_ansi(text) -> str` likewise.
  - Pure-helper `build_install_argv(repo_dir, selected_deps, no_sudo)`
    so tests can verify the exact command without spawning anything.
"""
from __future__ import annotations

import os
import re
import shlex
import shutil
import sys
from pathlib import Path
from typing import Optional

import gi
gi.require_version("Gtk", "3.0")
from gi.repository import GLib, Gio, Gtk, Pango


# ─── Pure helpers (no GTK; testable without display) ───────────────────────


_ANSI_RE = re.compile(r"\x1b\[[0-9;?]*[a-zA-Z]")
_OSC_RE = re.compile(r"\x1b\][0-9]*;[^\x07\x1b]*[\x07\x1b]")


def strip_ansi(text: str) -> str:
    """Remove ANSI CSI + OSC escape sequences. Used by the log
    TextView so coloured install.sh output reads cleanly. Doesn't
    handle every edge case (no DCS / SOS / PM / APC strings) — those
    don't appear in our installer."""
    text = _ANSI_RE.sub("", text)
    text = _OSC_RE.sub("", text)
    return text


def parse_status_json_line(line: str) -> Optional[dict]:
    """Try to interpret a stdout line as install.sh's status JSON.

    Returns the parsed dict when line is a complete JSON object with
    the expected schema (phase, status, progress, label) — else None.
    Other stdout content (echo headers, `info`/`warn`/`fail` chrome
    from install.sh) is ignored at the parser level and lands in the
    log view instead.
    """
    import json as _json
    s = line.strip()
    if not s.startswith("{") or not s.endswith("}"):
        return None
    try:
        obj = _json.loads(s)
    except _json.JSONDecodeError:
        return None
    if not isinstance(obj, dict):
        return None
    # Schema gate — only accept lines that look like status_json output
    if not {"phase", "status", "progress", "label"} <= obj.keys():
        return None
    return obj


def build_install_argv(repo_dir: str, selected_deps: list,
                        no_sudo: bool = False,
                        action: str = "install",
                        purge: bool = False) -> list[str]:
    """Compose the install.sh argv that the wizard will spawn.

    Always passes --headless + --status-json (the wizard's two
    contracts). --selected is included only when the list is
    non-empty so legacy 'all auto deps' behaviour stays available
    via an empty pick page.

    action: "install" (default), "fix", or "uninstall". Each maps
            to the corresponding install.sh flag.
    purge:  only relevant for action="uninstall" — passes --purge
            to also drop user configs + DB.
    """
    install_sh = str(Path(repo_dir) / "install.sh")
    argv = ["bash", install_sh, "--headless", "--status-json"]
    if action == "fix":
        argv.append("--fix")
    elif action == "uninstall":
        argv.append("--uninstall")
        if purge:
            argv.append("--purge")
    if no_sudo:
        argv.append("--no-sudo")
    if action == "install" and selected_deps:
        # CSV with no embedded commas (dep names are simple identifiers)
        argv.extend(["--selected", ",".join(selected_deps)])
    return argv


def detect_install_state(home: Optional[Path] = None) -> str:
    """Pure helper: classify current BTerminal install on disk.

    Args:
        home: base HOME path. Defaults to $HOME — the override is for
              unit tests that simulate broken state in tmp_path.

    Returns: "not_installed" | "installed" | "broken"
      - not_installed: no launcher AND no install dir
      - installed: launcher + pkg __init__.py + companion CLIs
                   (ctx/tasks/consult/memory_wizard/claude_log) all
                   present AND any installed AI CLI binary passes
                   validate (no stub markers, executable)
      - broken: any subset of the above is missing/corrupt — covers
                5 known break scenarios from task #143:
                  (a) launcher symlink missing
                  (b) bterminal/__init__.py missing
                  (c) AI CLI binary is a stub (no +x or stub-marker)
                  (d) install.lock with stale fake PID present
                      (treated as broken so wizard offers Fix)
                  (e) one or more companion CLI symlinks missing
    """
    home = home or Path(os.path.expanduser("~"))
    launcher = home / ".local" / "bin" / "bterminal"
    install_dir = home / ".local" / "share" / "bterminal"
    pkg_init = install_dir / "bterminal" / "__init__.py"

    launcher_ok = launcher.is_symlink() or launcher.is_file()
    pkg_ok = pkg_init.is_file()

    # Bare metal "no install" — quick exit
    if not launcher_ok and not pkg_ok:
        return "not_installed"

    # (a)/(b): top-level pieces missing
    if not launcher_ok or not pkg_ok:
        return "broken"

    # (e): companion CLI symlinks must all exist
    bin_dir = home / ".local" / "bin"
    for tool in ("ctx", "tasks", "consult",
                 "memory_wizard", "claude_log"):
        p = bin_dir / tool
        if not (p.is_symlink() or p.is_file()):
            return "broken"

    # (c): AI CLI binaries — if symlink exists, binary must be sane
    # (executable, not a stub). Use a quick scan of first 256 bytes
    # for known stub markers; mirror validate_npm_cli in install.sh
    # (which is the source of truth at install time).
    npm_global = home / ".npm-global"
    for cli in ("claude", "copilot"):
        link = bin_dir / cli
        # Only check if installer claimed to install it (symlink exists)
        if link.exists() or link.is_symlink():
            try:
                real = link.resolve(strict=True)
            except (OSError, RuntimeError):
                return "broken"  # dangling symlink
            if not os.access(real, os.X_OK):
                return "broken"  # +x missing
            try:
                head = real.open("rb").read(256)
                if b"native binary not installed" in head \
                        or b"postinstall did not run" in head:
                    return "broken"
            except OSError:
                return "broken"
        else:
            # Symlink missing under bin_dir but maybe lives directly
            # under npm-global (install.sh creates both). Check that
            # one too — if present-but-broken, classify as broken.
            npm_bin = npm_global / "bin" / cli
            if npm_bin.exists() or npm_bin.is_symlink():
                # Symlink wasn't relinked under bin_dir; bin_dir was
                # supposed to mirror this — treat as broken.
                return "broken"

    # (d): stale install.lock (PID dead) — treat as broken so wizard
    # offers Fix (which calls install.sh; install.sh's own stale-lock
    # recovery wipes the file before flock).
    lockfile = home / ".config" / "bterminal" / "install.lock"
    if lockfile.is_file():
        try:
            recorded = lockfile.read_text().strip().split("\n", 1)[0]
            pid = int(recorded) if recorded.isdigit() else 0
            if pid > 0:
                try:
                    os.kill(pid, 0)  # signal 0 — just probe existence
                except (OSError, ProcessLookupError):
                    return "broken"  # PID dead → stale lock
        except (OSError, ValueError):
            pass

    return "installed"


# ─── License helper (reuse from #1 license.py — fail-soft) ─────────────────


def _read_license_text_for_wizard() -> str:
    """Best-effort license text. Falls back to a one-liner if the
    license module isn't importable yet (chicken/egg during early
    install)."""
    try:
        from bterminal.license import _read_license_text
        text = _read_license_text()
        if text:
            return text
    except Exception:
        pass
    return ("BTerminal is licensed under the BTerminal License Agreement.\n"
            "See LICENSE.md for full terms.")


# ─── Wizard ────────────────────────────────────────────────────────────────


_WIZARD_NEXT = 1001
_WIZARD_BACK = 1002


class InstallerWizard(Gtk.Dialog):
    """5-page install wizard. Use as a one-shot:

        wiz = InstallerWizard(parent=None,
                              repo_dir="/path/to/cloned/bterminal")
        wiz.run_and_install()      # blocks until user finishes / cancels
        wiz.destroy()

    repo_dir: where install.sh lives. Required — the wizard refuses
              to launch without a real script (no dry run mode).
    """

    PAGES = ("welcome", "inventory", "picks", "uninstall_confirm",
             "progress", "summary")
    # Page sequences per action — controls which pages the wizard
    # shows when navigating Next/Back. Indices map into PAGES.
    PAGES_BY_ACTION = {
        "install":   (0, 1, 2, 4, 5),       # full 5-page flow
        "fix":       (0, 4, 5),              # welcome → progress → summary
        "uninstall": (0, 3, 4, 5),           # welcome → confirm → progress → summary
    }
    # Header text per (action, page-index-in-PAGES).
    HEADERS_BY_ACTION = {
        "install": {
            0: "Step 1 of 5: Welcome + License",
            1: "Step 2 of 5: System inventory",
            2: "Step 3 of 5: Pick what to install",
            4: "Step 4 of 5: Installing…",
            5: "Step 5 of 5: Summary",
        },
        "fix": {
            0: "Step 1 of 3: Welcome — repair existing install",
            4: "Step 2 of 3: Repairing…",
            5: "Step 3 of 3: Summary",
        },
        "uninstall": {
            0: "Step 1 of 4: Welcome — uninstall BTerminal",
            3: "Step 2 of 4: Confirm what to remove",
            4: "Step 3 of 4: Uninstalling…",
            5: "Step 4 of 4: Summary",
        },
    }

    def __init__(self, parent=None, repo_dir: Optional[str] = None):
        super().__init__(
            title="BTerminal Installer",
            transient_for=parent,
            modal=True,
            destroy_with_parent=parent is not None,
        )
        self.set_default_size(640, 540)
        self.repo_dir = repo_dir

        # Page state
        self._current_page = 0
        self._license_accepted = False
        self._selected_deps: list[str] = []
        self._install_proc: Optional[Gio.Subprocess] = None
        self._install_stdin = None  # held to prevent GC
        self._final_status_seen = False
        self._cancelled = False

        # Sudo state (audit § headless-sudo, 2026-05-08): wizard
        # pre-prompts for sudo password and keeps the cache alive
        # so install.sh's apt-installs + ollama curl|sh don't hang
        # on prompts the user can't see.
        self._sudo_authenticated = False
        self._sudo_keepalive_source = 0  # GLib timeout id, 0 = stopped

        # Action state — install (default) / fix / uninstall. Set
        # from the radio group on the welcome page; controls which
        # page sequence runs and which install.sh flag is passed.
        self._action = "install"
        self._purge = False  # only relevant for action="uninstall"
        self._install_state = detect_install_state()

        # Layout
        box = self.get_content_area()
        box.set_border_width(16)
        box.set_spacing(12)

        self.lbl_header = Gtk.Label(xalign=0)
        # Initial header — _show_page(0) below resets it via
        # HEADERS_BY_ACTION; this is just a placeholder for first paint.
        self.lbl_header.set_markup(
            f"<b>{self.HEADERS_BY_ACTION['install'][0]}</b>")
        box.pack_start(self.lbl_header, False, False, 0)
        box.pack_start(Gtk.Separator(), False, False, 0)

        self.stack = Gtk.Stack()
        self.stack.set_transition_type(Gtk.StackTransitionType.SLIDE_LEFT_RIGHT)
        box.pack_start(self.stack, True, True, 0)

        self.lbl_status = Gtk.Label(xalign=0, wrap=True, max_width_chars=72)
        box.pack_start(self.lbl_status, False, False, 0)

        # Build pages — order MUST match PAGES tuple
        self._build_page_welcome()
        self._build_page_inventory()
        self._build_page_picks()
        self._build_page_uninstall_confirm()
        self._build_page_progress()
        self._build_page_summary()

        # Action buttons (responses captured by run loop). Marked
        # no-show-all so subsequent self.show_all() passes don't
        # un-hide buttons that the state machine just hid.
        self.btn_cancel = self.add_button("Cancel", Gtk.ResponseType.CANCEL)
        self.btn_back = self.add_button("← Back", _WIZARD_BACK)
        self.btn_next = self.add_button("Next →", _WIZARD_NEXT)
        self.btn_finish = self.add_button(
            "Open BTerminal", Gtk.ResponseType.OK)
        for b in (self.btn_cancel, self.btn_back, self.btn_next,
                  self.btn_finish):
            b.set_no_show_all(True)

        self.show_all()
        self._show_page(0)

    # ─── Page builders ─────────────────────────────────────────────────────

    def _build_page_welcome(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        intro = Gtk.Label(xalign=0, wrap=True, max_width_chars=68)
        intro.set_markup(
            "<b>Welcome to BTerminal.</b> This wizard installs the GTK"
            " terminal emulator and its companion CLI tools — or"
            " repairs / removes an existing install."
        )
        page.pack_start(intro, False, False, 0)

        # ─── Action selector ─────────────────────────────────────────────
        # Three radio buttons (Install / Fix / Uninstall). Sensitivity
        # depends on whether BT is already installed:
        #   not_installed → only Install enabled
        #   installed     → Install greyed out (already done — Fix/Uninstall)
        #   broken        → Install + Fix enabled (Uninstall optional)
        action_frame = Gtk.Frame(label=" What would you like to do? ")
        action_box = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=4)
        action_box.set_border_width(8)
        action_frame.add(action_box)

        state_lbl = Gtk.Label(xalign=0, wrap=True, max_width_chars=70)
        if self._install_state == "not_installed":
            state_lbl.set_markup(
                "<small>Detected: BTerminal is <b>not yet installed</b>"
                " on this system.</small>"
            )
        elif self._install_state == "installed":
            state_lbl.set_markup(
                "<small>Detected: BTerminal is <b>already installed</b>"
                " — choose Fix to validate/repair, or Uninstall to remove.</small>"
            )
        else:  # broken
            state_lbl.set_markup(
                "<small>Detected: BTerminal install looks <b>incomplete</b>"
                " — Fix will repair missing pieces, Install will replace,"
                " Uninstall will clean everything up.</small>"
            )
        state_lbl.get_style_context().add_class("dim-label")
        action_box.pack_start(state_lbl, False, False, 0)

        self._action_radios = {}

        rb_install = Gtk.RadioButton.new_with_label_from_widget(
            None, "Install BTerminal")
        rb_fix = Gtk.RadioButton.new_with_label_from_widget(
            rb_install, "Fix existing install (validate + repair)")
        rb_uninstall = Gtk.RadioButton.new_with_label_from_widget(
            rb_install, "Uninstall BTerminal")
        self._action_radios["install"] = rb_install
        self._action_radios["fix"] = rb_fix
        self._action_radios["uninstall"] = rb_uninstall

        # Sensitivity per state
        rb_install.set_sensitive(self._install_state != "installed")
        rb_fix.set_sensitive(self._install_state in ("installed", "broken"))
        rb_uninstall.set_sensitive(self._install_state in ("installed", "broken"))

        # Default selection
        if self._install_state == "installed":
            rb_fix.set_active(True)
            self._action = "fix"
        else:
            rb_install.set_active(True)
            self._action = "install"

        for name, rb in self._action_radios.items():
            rb.connect("toggled", self._on_action_changed, name)
            action_box.pack_start(rb, False, False, 0)

        page.pack_start(action_frame, False, False, 0)

        # Read-only license text (scrolls when long).
        license_label = Gtk.Label(xalign=0)
        license_label.set_markup("<b>License terms:</b>")
        page.pack_start(license_label, False, False, 0)
        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_min_content_height(180)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        view = Gtk.TextView(editable=False, cursor_visible=False)
        view.set_wrap_mode(Gtk.WrapMode.WORD)
        view.get_buffer().set_text(_read_license_text_for_wizard())
        scrolled.add(view)
        page.pack_start(scrolled, True, True, 0)

        # Underscore-prefix = GTK mnemonic. Alt+I toggles the
        # checkbox without mouse — works for keyboard navigation
        # AND for automated tests (xdotool key alt+i).
        self.chk_accept = Gtk.CheckButton.new_with_mnemonic(
            "_I have read and accept the license terms.")
        self.chk_accept.connect(
            "toggled", lambda w: self._update_nav_buttons())
        page.pack_start(self.chk_accept, False, False, 0)

        self.stack.add_named(page, "welcome")

    def _on_action_changed(self, button, action_name: str):
        """Radio selection changed — record the new action."""
        if button.get_active():
            self._action = action_name
            self._update_nav_buttons()

    def _build_page_uninstall_confirm(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=10)

        warn = Gtk.Label(xalign=0, wrap=True, max_width_chars=70)
        warn.set_markup(
            "<b>This will remove:</b>\n"
            "  • BTerminal package files (~/.local/share/bterminal/)\n"
            "  • CLI symlinks (bterminal, ctx, tasks, consult, "
            "memory_wizard, claude_log)\n"
            "  • Desktop entry + icon\n"
            "  • npm-installed Claude Code (@anthropic-ai/claude-code)\n"
            "  • npm-installed Copilot CLI (@github/copilot)"
        )
        page.pack_start(warn, False, False, 0)

        keep = Gtk.Label(xalign=0, wrap=True, max_width_chars=70)
        keep.set_markup(
            "<b>By default these are kept</b> (use <i>Also delete user data</i> below to remove):\n"
            "  • ~/.config/bterminal/ — sessions, options, install logs\n"
            "  • ~/.claude-context/ — ctx + tasks SQLite DB"
        )
        page.pack_start(keep, False, False, 0)

        note = Gtk.Label(xalign=0, wrap=True, max_width_chars=70)
        note.set_markup(
            "<small>Ollama (if installed) is NOT removed automatically — "
            "remove manually with <tt>sudo rm -rf /usr/local/lib/ollama "
            "/usr/local/bin/ollama</tt>.</small>"
        )
        note.get_style_context().add_class("dim-label")
        page.pack_start(note, False, False, 0)

        self.chk_purge = Gtk.CheckButton(
            label="Also delete my user data (sessions, ctx + tasks DB)")
        self.chk_purge.set_active(False)
        self.chk_purge.connect(
            "toggled",
            lambda w: setattr(self, "_purge", w.get_active()),
        )
        page.pack_start(self.chk_purge, False, False, 0)

        self.stack.add_named(page, "uninstall_confirm")

    def _build_page_inventory(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        info = Gtk.Label(xalign=0, wrap=True, max_width_chars=72)
        info.set_markup(
            "Detected system tools (live audit). "
            "Missing items can be installed on the next page."
        )
        page.pack_start(info, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        self.txt_inventory = Gtk.TextView(editable=False, cursor_visible=False)
        self.txt_inventory.set_monospace(True)
        scrolled.add(self.txt_inventory)
        page.pack_start(scrolled, True, True, 0)

        self.stack.add_named(page, "inventory")

    def _build_page_picks(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)
        info = Gtk.Label(xalign=0, wrap=True, max_width_chars=72)
        info.set_markup(
            "<b>Optional dependencies</b> — tick to install. Required "
            "deps (git, ssh) install automatically without asking."
        )
        page.pack_start(info, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.checks_box = Gtk.Box(
            orientation=Gtk.Orientation.VERTICAL, spacing=4)
        self.checks_box.set_border_width(8)
        scrolled.add(self.checks_box)
        page.pack_start(scrolled, True, True, 0)

        # Checkboxes get populated lazily on first inventory load
        # (so DEPENDENCIES can be re-fetched after sync).
        self._dep_checkboxes: dict[str, Gtk.CheckButton] = {}
        self._llama_check: Optional[Gtk.CheckButton] = None

        self.stack.add_named(page, "picks")

    def _build_page_progress(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self.lbl_phase = Gtk.Label(xalign=0)
        self.lbl_phase.set_markup("<i>Preparing…</i>")
        page.pack_start(self.lbl_phase, False, False, 0)

        self.progress = Gtk.ProgressBar()
        self.progress.set_show_text(True)
        page.pack_start(self.progress, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.AUTOMATIC, Gtk.PolicyType.AUTOMATIC)
        scrolled.set_shadow_type(Gtk.ShadowType.IN)
        self.txt_log = Gtk.TextView(editable=False, cursor_visible=False)
        self.txt_log.set_monospace(True)
        self.txt_log.set_wrap_mode(Gtk.WrapMode.NONE)
        scrolled.add(self.txt_log)
        page.pack_start(scrolled, True, True, 0)

        self.stack.add_named(page, "progress")

    def _build_page_summary(self):
        page = Gtk.Box(orientation=Gtk.Orientation.VERTICAL, spacing=8)

        self.lbl_summary_header = Gtk.Label(xalign=0)
        self.lbl_summary_header.set_markup(
            "<b>Installation finished.</b>")
        page.pack_start(self.lbl_summary_header, False, False, 0)

        scrolled = Gtk.ScrolledWindow()
        scrolled.set_policy(Gtk.PolicyType.NEVER, Gtk.PolicyType.AUTOMATIC)
        self.txt_summary = Gtk.TextView(editable=False, cursor_visible=False)
        self.txt_summary.set_monospace(True)
        scrolled.add(self.txt_summary)
        page.pack_start(scrolled, True, True, 0)

        # ─── Diagnostic bundle button row ─────────────────────────────────
        # Lets users save a tar.gz of install logs + system info for
        # bug reports without poking around in ~/.config/bterminal.
        diag_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL, spacing=6)
        btn_save_diag = Gtk.Button(label="Save diagnostic report (.tar.gz)…")
        btn_save_diag.set_tooltip_text(
            "Save a single archive containing install.log + per-run logs "
            "+ install_errors.json + OS info. Attach this to bug reports."
        )
        btn_save_diag.connect("clicked",
                               lambda _w: self._save_diagnostic_bundle())
        diag_box.pack_start(btn_save_diag, False, False, 0)

        btn_open_logs = Gtk.Button(label="Open logs folder…")
        btn_open_logs.set_tooltip_text(
            "Open ~/.config/bterminal/ in the file manager"
        )
        btn_open_logs.connect("clicked",
                               lambda _w: self._open_logs_folder())
        diag_box.pack_start(btn_open_logs, False, False, 0)

        page.pack_start(diag_box, False, False, 0)

        self.stack.add_named(page, "summary")

    def _save_diagnostic_bundle(self):
        """Bundle install.log + install-runs/* + install_errors.json
        + system snapshot into a single .tar.gz the user can attach
        to a bug report. Opens FileChooserDialog to pick the
        destination."""
        import tarfile
        from datetime import datetime as _dt
        cfg_dir = (Path(os.path.expanduser("~"))
                   / ".config" / "bterminal")
        # If --purge wiped the config dir, fall back to the temp
        # log saved during purge (do_uninstall stashes a copy in
        # /tmp/bterminal-uninstall-final-*.log).
        purge_log = None
        if not cfg_dir.is_dir():
            tmp_logs = sorted(
                Path("/tmp").glob("bterminal-uninstall-final.*.log"),
                key=lambda p: p.stat().st_mtime if p.exists() else 0,
                reverse=True,
            )
            if tmp_logs:
                purge_log = tmp_logs[0]
            else:
                self._show_error_dialog(
                    "No diagnostic data found",
                    "~/.config/bterminal/ was purged and no fallback "
                    "log is available in /tmp.\n\nNothing to bundle."
                )
                return

        chooser = Gtk.FileChooserDialog(
            title="Save diagnostic report",
            transient_for=self,
            action=Gtk.FileChooserAction.SAVE,
        )
        chooser.add_buttons(
            "Cancel", Gtk.ResponseType.CANCEL,
            "Save", Gtk.ResponseType.OK,
        )
        chooser.set_do_overwrite_confirmation(True)
        default_name = (
            f"bterminal-diag-{_dt.utcnow().strftime('%Y%m%d-%H%M%S')}.tar.gz"
        )
        chooser.set_current_name(default_name)
        # Default to ~/Desktop or ~ as starting folder
        for candidate in (Path.home() / "Desktop", Path.home()):
            if candidate.is_dir():
                chooser.set_current_folder(str(candidate))
                break

        response = chooser.run()
        target = chooser.get_filename() if response == Gtk.ResponseType.OK \
                 else None
        chooser.destroy()
        if not target:
            return

        try:
            # Snapshot OS / hardware diagnostics fresh into a tmpfile
            # so the bundle includes a current view (install.log
            # captured a snapshot at install start, but state may
            # have changed by the time user generates the bundle).
            import tempfile
            snap_fd, snap_path = tempfile.mkstemp(
                prefix="bterminal-diag-", suffix=".txt")
            os.close(snap_fd)
            with open(snap_path, "w", encoding="utf-8") as snap:
                snap.write(self._collect_runtime_diagnostics())

            with tarfile.open(target, "w:gz") as tar:
                # Purge fallback: only the /tmp copy survived
                if purge_log is not None and purge_log.is_file():
                    tar.add(str(purge_log),
                            arcname="install.log.purge-fallback")
                # install.log (structured) + install_errors.json (summary)
                if (cfg_dir / "install.log").is_file():
                    tar.add(str(cfg_dir / "install.log"),
                            arcname="install.log")
                if (cfg_dir / "install_errors.json").is_file():
                    tar.add(str(cfg_dir / "install_errors.json"),
                            arcname="install_errors.json")
                # All per-run logs
                runs_dir = cfg_dir / "install-runs"
                if runs_dir.is_dir():
                    for log in sorted(runs_dir.glob("*.log")):
                        tar.add(str(log),
                                arcname=f"install-runs/{log.name}")
                # Snapshot of current diagnostics
                tar.add(snap_path, arcname="diagnostics.txt")

            os.unlink(snap_path)
            self._show_info_dialog(
                "Diagnostic report saved",
                f"Saved to:\n{target}\n\n"
                "Attach this file to your bug report."
            )
        except (OSError, tarfile.TarError) as exc:
            self._show_error_dialog(
                "Failed to save diagnostic report",
                f"{type(exc).__name__}: {exc}"
            )

    def _collect_runtime_diagnostics(self) -> str:
        """Snapshot OS / hardware / runtime state as a plain-text
        block. Captures more detail than install.sh's [DIAG] lines
        — for inclusion in bug-report bundles."""
        import platform as _plat
        import subprocess as _sp
        from datetime import datetime as _dt

        def _safe_run(cmd: list, timeout: int = 5) -> str:
            try:
                r = _sp.run(cmd, capture_output=True, text=True,
                            timeout=timeout)
                return (r.stdout + r.stderr).strip()
            except (OSError, _sp.TimeoutExpired) as exc:
                return f"(failed: {exc})"

        sections = [
            f"# BTerminal diagnostic snapshot",
            f"# Generated: {_dt.utcnow().isoformat()}Z",
            "",
            "## Python",
            f"version: {_plat.python_version()}",
            f"executable: {sys.executable}",
            "",
            "## Operating system",
            f"system: {_plat.system()} {_plat.release()}",
            f"machine: {_plat.machine()}",
            f"node: {_plat.node()}",
            f"libc: {_plat.libc_ver()}",
            "",
            "## /etc/os-release",
        ]
        try:
            sections.append(Path("/etc/os-release").read_text(
                encoding="utf-8", errors="replace"))
        except OSError:
            sections.append("(unavailable)")
        sections.extend([
            "",
            "## uname -a",
            _safe_run(["uname", "-a"]),
            "",
            "## Memory",
            _safe_run(["free", "-h"]),
            "",
            "## Disk",
            _safe_run(["df", "-h", str(Path.home())]),
            "",
            "## Locale",
            f"LANG={os.environ.get('LANG','')}  "
            f"LC_ALL={os.environ.get('LC_ALL','')}",
            "",
            "## DISPLAY",
            f"DISPLAY={os.environ.get('DISPLAY','unset')}  "
            f"WAYLAND_DISPLAY={os.environ.get('WAYLAND_DISPLAY','unset')}",
            "",
            "## node + npm",
            f"node: {_safe_run(['node', '--version'])}",
            f"npm:  {_safe_run(['npm', '--version'])}",
            "",
            "## AI CLIs",
            f"claude:  {_safe_run(['claude', '--version'])}",
            f"copilot: {_safe_run(['copilot', '--version'])}",
            f"ollama:  {_safe_run(['ollama', '--version'])}",
            "",
            "## GTK",
            f"GTK: {_safe_run(['python3', '-c', 'import gi; gi.require_version(\"Gtk\",\"3.0\"); from gi.repository import Gtk; print(Gtk.MAJOR_VERSION,Gtk.MINOR_VERSION,Gtk.MICRO_VERSION)'])}",
            "",
            "## Wizard state",
            f"action: {self._action}",
            f"sudo_authenticated: {self._sudo_authenticated}",
            f"selected_deps: {self._selected_deps}",
            f"install_state_at_start: {self._install_state}",
        ])
        return "\n".join(sections) + "\n"

    def _open_logs_folder(self):
        """Open ~/.config/bterminal/ in the system file manager.

        Tries xdg-open first; falls back to gio open. If the folder
        was purged (--uninstall --purge), shows the user where the
        last install log copy was saved (/tmp/bterminal-uninstall-final-*).
        """
        import subprocess as _sp
        cfg_dir = (Path(os.path.expanduser("~"))
                   / ".config" / "bterminal")

        # Handle purge-removed config dir gracefully
        if not cfg_dir.is_dir():
            # Look for the final-log temp file from the purge path
            tmp_logs = sorted(
                Path("/tmp").glob("bterminal-uninstall-final.*.log"),
                key=lambda p: p.stat().st_mtime if p.exists() else 0,
                reverse=True,
            )
            extra = ""
            if tmp_logs:
                extra = f"\n\nLast uninstall log saved to:\n  {tmp_logs[0]}"
            self._show_info_dialog(
                "Logs folder removed",
                f"~/.config/bterminal/ was removed by --purge "
                f"during uninstall.{extra}"
            )
            return

        target_uri = f"file://{cfg_dir}"
        # Strategy 1: xdg-open (Linux desktops)
        for cmd in (["xdg-open", str(cfg_dir)],
                    ["gio", "open", str(cfg_dir)]):
            try:
                proc = _sp.Popen(cmd, stdout=_sp.DEVNULL, stderr=_sp.DEVNULL)
                # Don't block — just check the binary launched
                # successfully. xdg-open returns 0 immediately if it
                # successfully delegated to a handler.
                try:
                    rc = proc.wait(timeout=2)
                    if rc == 0:
                        return
                except _sp.TimeoutExpired:
                    # xdg-open often spawns a subprocess that outlives
                    # the wait — that's still success.
                    return
            except OSError:
                continue
        # Strategy 2: GTK's own file:// handler — works even when no
        # xdg-open / gio binary is on PATH.
        try:
            from gi.repository import Gio as _Gio
            _Gio.AppInfo.launch_default_for_uri(target_uri, None)
            return
        except Exception:
            pass
        # All strategies failed — at least tell the user the path
        self._show_error_dialog(
            "Cannot open folder",
            "No working file-manager handler found.\n\n"
            f"Folder path:\n{cfg_dir}\n\n"
            "Try opening it manually:\n"
            f"  xdg-open {cfg_dir}"
        )

    def _show_info_dialog(self, primary: str, secondary: str = ""):
        dlg = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.INFO,
            buttons=Gtk.ButtonsType.OK,
            text=primary,
        )
        if secondary:
            dlg.format_secondary_text(secondary)
        dlg.run()
        dlg.destroy()

    def _show_error_dialog(self, primary: str, secondary: str = ""):
        dlg = Gtk.MessageDialog(
            transient_for=self, modal=True,
            message_type=Gtk.MessageType.ERROR,
            buttons=Gtk.ButtonsType.OK,
            text=primary,
        )
        if secondary:
            dlg.format_secondary_text(secondary)
        dlg.run()
        dlg.destroy()

    # ─── Page show / nav ───────────────────────────────────────────────────

    def _action_sequence(self) -> tuple:
        """Page indices the wizard visits for the current action."""
        return self.PAGES_BY_ACTION.get(self._action,
                                         self.PAGES_BY_ACTION["install"])

    def _next_page_idx(self, current: int) -> Optional[int]:
        """Index of the page after `current` in the active sequence,
        or None when current is the last page."""
        seq = self._action_sequence()
        try:
            pos = seq.index(current)
        except ValueError:
            # Page not in sequence (action changed) — start from welcome
            return seq[0]
        if pos + 1 < len(seq):
            return seq[pos + 1]
        return None

    def _prev_page_idx(self, current: int) -> Optional[int]:
        """Index of the page before `current` in the active sequence,
        or None when current is the first page."""
        seq = self._action_sequence()
        try:
            pos = seq.index(current)
        except ValueError:
            return None
        if pos > 0:
            return seq[pos - 1]
        return None

    def _show_page(self, idx: int):
        self._current_page = idx
        self.stack.set_visible_child_name(self.PAGES[idx])
        # Header from action-specific table
        header = self.HEADERS_BY_ACTION.get(self._action, {}).get(
            idx, f"Step: {self.PAGES[idx]}")
        self.lbl_header.set_markup(f"<b>{header}</b>")
        self.lbl_status.set_text("")

        # Lazy populate when the page first appears
        if idx == 1:  # inventory
            self._populate_inventory()
        elif idx == 2:  # picks
            self._populate_picks()
        elif idx == 4:  # progress
            self._start_install()
        elif idx == 5:  # summary
            self._populate_summary()

        self._update_nav_buttons()

        # Make Next/Install/Repair the default response so Enter
        # advances regardless of which child widget has keyboard
        # focus. Without this, Return on a focused checkbox toggles
        # the checkbox instead of clicking the Next button —
        # confusing for users AND broke the automated wizard test
        # which lost a randomly-focused checkbox state on Return.
        # Summary page → OK is default; progress page → no default
        # (no Next visible, only Cancel).
        if idx == 5:  # summary
            self.set_default_response(Gtk.ResponseType.OK)
        elif idx != 4:  # any input page
            self.set_default_response(_WIZARD_NEXT)
            try:
                self.btn_next.set_can_default(True)
                self.btn_next.grab_default()
            except Exception:
                pass

    def _update_nav_buttons(self):
        idx = self._current_page
        seq = self._action_sequence()
        is_last_input = (idx in seq) and \
            (self._next_page_idx(idx) == 4)  # next leads to progress
        is_progress = idx == 4
        is_summary = idx == 5

        def _toggle(btn, visible: bool):
            if visible:
                btn.show()
            else:
                btn.hide()

        _toggle(self.btn_cancel, not is_summary)
        _toggle(self.btn_back,
                self._prev_page_idx(idx) is not None and not is_progress)
        _toggle(self.btn_next, not is_progress and not is_summary)
        _toggle(self.btn_finish, is_summary)

        # Finish-button label depends on action AND install result:
        #   - uninstall  → "Close" always
        #   - install/fix with rc != 0 → "Close" (BT may be unusable)
        #   - install/fix with rc == 0 → "Open BTerminal"
        # Pre-2026-05-08 bug: button always said "Open BTerminal"
        # for install action, even after exit code 7 (lock failure)
        # — confusing because clicking it crashed since BT wasn't
        # actually installed.
        if is_summary:
            rc = getattr(self, "_install_rc", 0)
            if self._action == "uninstall" or rc != 0:
                self.btn_finish.set_label("Close")
            else:
                self.btn_finish.set_label("Open BTerminal")

        # Final-input pages get a friendlier Next label
        if is_last_input:
            label = {
                "install": "Install →",
                "fix": "Repair →",
                "uninstall": "Uninstall →",
            }.get(self._action, "Next →")
            self.btn_next.set_label(label)
        else:
            self.btn_next.set_label("Next →")

        # Sensitivity
        if idx == 0:
            # Welcome: Next requires license accepted AND a non-disabled
            # action selected
            self.btn_next.set_sensitive(self.chk_accept.get_active())
        elif idx == 3:
            # Uninstall confirmation — always allowed to proceed
            self.btn_next.set_sensitive(True)
        elif idx == 1 or idx == 2:
            self.btn_next.set_sensitive(True)
        elif idx == 4:
            self.btn_back.set_sensitive(False)  # can't go back during install

    # ─── Page-3 (picks) population ─────────────────────────────────────────

    def _populate_picks(self):
        # Re-check existing children — only build once
        if self._dep_checkboxes:
            return

        from bterminal.diagnostics import DEPENDENCIES, audit
        statuses = {s.spec.cmd: s for s in audit()}

        for dep in DEPENDENCIES:
            if dep.tier == "required":
                continue  # required deps install unconditionally
            present = statuses.get(dep.cmd)
            present_mark = "✓ installed" if present and present.present \
                           else "✗ missing"
            label = f"{dep.label}  ({dep.tier} — {present_mark})"
            chk = Gtk.CheckButton(label=label)
            # Default ON for ALL items (auto AND optional tier),
            # including already-installed ones — user explicitly
            # asked for opt-out semantics (uncheck what you don't
            # want) over opt-in. install.sh detects already-present
            # tools and skips them, so re-checking is idempotent.
            chk.set_active(True)
            if dep.feature:
                chk.set_tooltip_text(dep.feature)
            self.checks_box.pack_start(chk, False, False, 0)
            # Sub-label: visible "what does this do" line under each
            # checkbox (not just hover tooltip — many users miss those).
            # Source of truth: DEPENDENCIES[].feature (mirrors
            # `description` from defaults/dependencies.json).
            if dep.feature:
                sub_lbl = Gtk.Label(xalign=0)
                sub_lbl.set_markup(
                    f"<small>    {GLib.markup_escape_text(dep.feature)}</small>"
                )
                sub_lbl.get_style_context().add_class("dim-label")
                sub_lbl.set_line_wrap(True)
                sub_lbl.set_max_width_chars(70)
                self.checks_box.pack_start(sub_lbl, False, False, 0)
            self._dep_checkboxes[dep.cmd] = chk

        # Llama / Ollama opt-in section (audit § 5)
        sep = Gtk.Separator(orientation=Gtk.Orientation.HORIZONTAL)
        self.checks_box.pack_start(sep, False, False, 4)

        lbl_llama = Gtk.Label(xalign=0)
        lbl_llama.set_markup(
            "<b>Local LLM (optional)</b> — install Ollama so the Aider "
            "provider can run open-source models locally.")
        lbl_llama.set_line_wrap(True)
        lbl_llama.set_max_width_chars(70)
        self.checks_box.pack_start(lbl_llama, False, False, 0)

        ollama_present = bool(shutil.which("ollama"))
        # Realistic size: Ollama binary + ROCm/CUDA libs ≈ 1.5 GB
        # download (was incorrectly labeled "~50MB" pre-2026-05-08).
        llama_label = "Install Ollama (~1.5 GB download)"
        if ollama_present:
            llama_label += "  ✓ already installed"
        self._llama_check = Gtk.CheckButton(label=llama_label)
        # Default ON always — same policy as the rest of the picks
        # list (opt-out, not opt-in). install.sh's ollama install
        # branch detects an existing binary and skips network calls.
        self._llama_check.set_active(True)
        self._llama_check.set_tooltip_text(
            "Pulls ollama.com/install.sh + sh. Required for the Aider "
            "provider unless you point it at a remote endpoint."
        )
        self.checks_box.pack_start(self._llama_check, False, False, 0)
        # Sub-label with purpose + reason for size
        ollama_sub = Gtk.Label(xalign=0)
        ollama_sub.set_markup(
            "<small>    Local AI server. Lets the Aider provider run"
            " open-source models (Qwen, Llama 3, CodeGemma) on your"
            " machine — no API key needed. Includes ROCm/CUDA libs.</small>"
        )
        ollama_sub.get_style_context().add_class("dim-label")
        ollama_sub.set_line_wrap(True)
        ollama_sub.set_max_width_chars(70)
        self.checks_box.pack_start(ollama_sub, False, False, 0)

        self.checks_box.show_all()

    def _populate_inventory(self):
        from bterminal.diagnostics import audit, format_summary_text
        text = format_summary_text(audit())
        self.txt_inventory.get_buffer().set_text(text)

    def _populate_summary(self):
        from bterminal.diagnostics import audit, format_summary_text

        # Three states: cancelled / failed (rc!=0) / success
        rc = getattr(self, "_install_rc", 0)
        verb = {"install": "install", "fix": "repair",
                "uninstall": "uninstall"}.get(self._action, "install")

        if self._cancelled:
            self.lbl_summary_header.set_markup(
                f'<span foreground="orange"><b>Cancelled — partial {verb}.</b></span>\n'
                f"Re-run the wizard to finish."
            )
        elif rc != 0:
            self.lbl_summary_header.set_markup(
                f'<span foreground="red"><b>{verb.title()} FAILED'
                f' (exit code {rc}).</b></span>\n'
                f"Click <i>Save diagnostic report</i> below and"
                f" check the install log for details."
            )
            # Hide "Open BTerminal" since BT may be unusable
            self.btn_finish.set_label("Close")
        else:
            done_label = {
                "install": "Installation finished.",
                "fix": "Repair finished.",
                "uninstall": "Uninstall finished.",
            }.get(self._action, "Done.")
            self.lbl_summary_header.set_markup(f"<b>{done_label}</b>")

        # Re-run audit so it reflects post-install state
        text = format_summary_text(audit())
        self.txt_summary.get_buffer().set_text(text)

    def _gather_selected_deps(self) -> list[str]:
        out = []
        for cmd, chk in self._dep_checkboxes.items():
            if chk.get_active():
                out.append(cmd)
        if self._llama_check is not None and self._llama_check.get_active():
            out.append("llama")
        self._selected_deps = out
        return out

    # ─── Page-4 (progress) — spawn install.sh ──────────────────────────────

    # ─── Sudo handling (headless-friendly password caching) ────────────────

    def _picks_need_sudo(self, selected_deps: list) -> bool:
        """True if any selected dep requires root (apt-installs or ollama).
        npm-installed CLI (claude/copilot) live in ~/.npm-global so they
        don't need sudo. Required deps (git, ssh) trigger sudo too."""
        if not selected_deps:
            return False  # legacy "all auto" still hits apt; conservative=False
        sudo_needing = {
            # apt-tier deps — install.sh runs `sudo apt-get install`
            "meld", "pdflatex", "latexmk", "pdftoppm", "pandoc", "git-lfs",
            # Ollama installer writes to /usr/local
            "llama", "ollama",
        }
        return bool(set(selected_deps) & sudo_needing)

    def _prompt_sudo_password(self, error_hint: Optional[str] = None
                                ) -> Optional[str]:
        """Modal GTK password prompt. Returns the typed password or
        None when the user cancels.

        error_hint: optional red message shown when this is a retry
                    after a failed attempt."""
        dlg = Gtk.Dialog(
            title="Administrator password required",
            transient_for=self,
            modal=True,
        )
        dlg.set_default_size(440, 200)
        dlg.add_button("Cancel", Gtk.ResponseType.CANCEL)
        dlg.add_button("OK", Gtk.ResponseType.OK)
        dlg.set_default_response(Gtk.ResponseType.OK)

        box = dlg.get_content_area()
        box.set_border_width(16)
        box.set_spacing(8)

        msg = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
        msg.set_markup(
            "Some installers (apt + Ollama) need administrator privileges.\n"
            "Enter your sudo password to authorize the installation."
        )
        box.pack_start(msg, False, False, 0)

        if error_hint:
            err_lbl = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
            err_lbl.set_markup(
                f'<span foreground="red">{GLib.markup_escape_text(error_hint)}</span>'
            )
            box.pack_start(err_lbl, False, False, 0)

        entry = Gtk.Entry()
        entry.set_visibility(False)  # password masking
        entry.set_invisible_char("•")
        entry.set_activates_default(True)  # Enter triggers OK
        box.pack_start(entry, False, False, 0)

        hint = Gtk.Label(xalign=0, wrap=True, max_width_chars=60)
        hint.set_markup(
            "<small>The password is held in memory only for this install"
            " session and never written to disk.</small>"
        )
        hint.get_style_context().add_class("dim-label")
        box.pack_start(hint, False, False, 0)

        dlg.show_all()
        entry.grab_focus()
        response = dlg.run()
        password = entry.get_text() if response == Gtk.ResponseType.OK else None
        dlg.destroy()
        return password

    def _prompt_sudo_failed_choice(self) -> str:
        """Modal asking what to do after 3 failed sudo attempts /
        Cancel. Returns 'abort' (default — stop install) or
        'skip_sudo' (continue without root, AI CLIs only)."""
        dlg = Gtk.Dialog(
            title="Sudo authentication failed",
            transient_for=self,
            modal=True,
        )
        dlg.set_default_size(480, 220)
        # Order matters — left-most is non-destructive default.
        dlg.add_button("Continue without sudo", 1)
        btn_abort = dlg.add_button("Abort install", 2)
        # Mark Abort as default so Enter / Esc both abort
        dlg.set_default_response(2)
        btn_abort.grab_focus()

        box = dlg.get_content_area()
        box.set_border_width(16)
        box.set_spacing(8)

        msg = Gtk.Label(xalign=0, wrap=True, max_width_chars=64)
        msg.set_markup(
            "<b>Sudo authentication failed.</b>\n\n"
            "The installer needs root access for apt-installed deps "
            "(meld, pdflatex, latexmk, pandoc) and Ollama.\n\n"
            "<b>Abort install</b> — stop now, fix sudo, re-run wizard.\n"
            "<b>Continue without sudo</b> — install BTerminal core +"
            " AI CLIs (Claude, Copilot via npm). System tools + Ollama"
            " skipped; you can install them manually later."
        )
        box.pack_start(msg, False, False, 0)

        dlg.show_all()
        response = dlg.run()
        dlg.destroy()
        return "abort" if response == 2 else "skip_sudo"

    def _setup_sudo_askpass(self, password: str) -> Optional[str]:
        """Create a tmp `SUDO_ASKPASS` script holding the password.
        install.sh's apt_install() picks this up via the env var
        and uses `sudo -A` so the cache works across TTY boundaries
        (sudo 1.9+ defaults to tty_tickets, breaking simple
        `sudo -v` cache shared between wizard + spawned install.sh).

        Security: file mode 0700 (owner-only), placed under
        /tmp, deleted in `_on_install_done`. Password lives in
        memory + this file for the duration of install only.
        """
        import tempfile
        try:
            fd, askpass_path = tempfile.mkstemp(
                prefix="bt-askpass-", suffix=".sh", dir="/tmp")
            # Use single-quote escaping in case password has $ etc.
            esc = password.replace("'", "'\\''")
            os.write(fd,
                     f"#!/bin/sh\nprintf '%s\\n' '{esc}'\n".encode("utf-8"))
            os.close(fd)
            os.chmod(askpass_path, 0o700)
            self._sudo_askpass_path = askpass_path
            return askpass_path
        except OSError:
            return None

    def _cleanup_sudo_askpass(self):
        """Remove the SUDO_ASKPASS temp script. Called on install
        finish (success or fail) so the password file doesn't
        survive past the install session."""
        path = getattr(self, "_sudo_askpass_path", None)
        if path:
            try:
                os.unlink(path)
            except OSError:
                pass
            self._sudo_askpass_path = None

    def _cache_sudo(self, password: str) -> tuple:
        """Pipe password to `sudo -S -v` to cache credentials.

        Returns: (success: bool, diagnostic: str)
          success — True iff sudo accepted the password (cache
                    populated for ~15 min).
          diagnostic — short message extracted from sudo's stderr,
                       used to show the user *why* it failed.
                       Empty on success.

        Timeout is 30 s — long enough that slow VMs / NIS lookups
        don't trip a false negative."""
        import subprocess as _sp
        try:
            # `-k` first to clear any stale cached credentials so a
            # bad password definitely fails fast.
            _sp.run(["sudo", "-k"], capture_output=True, timeout=5)
        except (OSError, _sp.TimeoutExpired):
            pass
        try:
            # Use bytes mode + explicit utf-8 encode so non-ASCII
            # passwords (Polish ą/ę, German ü, etc.) are passed
            # through correctly. `text=True` uses sys.getdefaultencoding()
            # which can be wrong under exotic locales.
            #
            # Force LANG=C in env so sudo's error messages come back
            # in English — easier to parse + show to the user.
            env_c = dict(os.environ)
            env_c["LANG"] = "C"
            env_c["LC_ALL"] = "C"
            env_c["LC_MESSAGES"] = "C"
            result = _sp.run(
                ["sudo", "-S", "-p", "", "-v"],
                input=(password + "\n").encode("utf-8"),
                capture_output=True,
                timeout=30,
                env=env_c,
            )
            stderr_text = result.stderr.decode("utf-8", errors="replace")
            # Log sudo diagnostics to install.log (file) — NOT to the
            # GUI log view (would clutter the user-facing display with
            # internal subprocess noise). The diagnostic_bundle picks
            # the install.log file up either way.
            try:
                cfg_dir = (Path(os.path.expanduser("~"))
                           / ".config" / "bterminal")
                cfg_dir.mkdir(parents=True, exist_ok=True)
                with open(cfg_dir / "install.log", "a",
                           encoding="utf-8") as f:
                    from datetime import datetime as _dt
                    ts = _dt.utcnow().strftime("%Y-%m-%dT%H:%M:%SZ")
                    f.write(
                        f"{ts} [SUDO] rc={result.returncode}; "
                        f"stderr={stderr_text.strip()[:300]}\n"
                    )
            except OSError:
                pass
            if result.returncode == 0:
                return (True, "")
            # Translate sudo's stderr into a short user-friendly hint
            stderr_lc = stderr_text.lower()
            if "incorrect password" in stderr_lc or \
               "try again" in stderr_lc:
                hint = "Wrong password"
            elif "no tty" in stderr_lc or "no askpass" in stderr_lc:
                hint = "sudo refuses to read password (no TTY/askpass)"
            elif "not in the sudoers" in stderr_lc or \
                 "not allowed" in stderr_lc:
                hint = "Your user is not allowed to use sudo"
            elif "password is required" in stderr_lc or \
                 "no password was provided" in stderr_lc:
                hint = "sudo got no password — input pipe issue"
            else:
                # Unknown — show first line of sudo's stderr
                first_line = stderr_text.strip().split("\n")[0][:120]
                hint = first_line or f"sudo exit code {result.returncode}"
            return (False, hint)
        except _sp.TimeoutExpired:
            return (False, "sudo timed out (30 s) — slow system?")
        except OSError as exc:
            return (False, f"sudo not runnable: {exc}")

    def _start_sudo_keepalive(self):
        """Keep sudo cache fresh for the duration of the install.
        sudo's default cache window is 15 min; we refresh every 4 min
        to stay well under that even on slow machines."""
        def _tick():
            # Don't refresh if install already finished
            if self._install_proc is None:
                return False
            try:
                import subprocess as _sp
                _sp.run(
                    ["sudo", "-n", "-v"],
                    capture_output=True, timeout=5,
                )
            except (OSError, _sp.TimeoutExpired):
                pass
            return True  # keep firing
        self._sudo_keepalive_source = GLib.timeout_add_seconds(240, _tick)

    def _stop_sudo_keepalive(self):
        if self._sudo_keepalive_source:
            GLib.source_remove(self._sudo_keepalive_source)
            self._sudo_keepalive_source = 0

    def _start_install(self):
        if self._install_proc is not None:
            return  # idempotent — already running
        if not self.repo_dir or not Path(self.repo_dir).is_dir():
            self._append_log("ERROR: repo_dir not set or invalid.\n")
            self.lbl_phase.set_markup(
                '<span foreground="red">Cannot start install.</span>')
            return

        # For uninstall + fix actions, the dep list is irrelevant
        # (install.sh's --uninstall / --fix branches do their own thing).
        # For install, gather user picks from the picks page.
        deps = (self._gather_selected_deps()
                if self._action == "install" else [])

        # Sudo handling: only the install path may need sudo (for apt
        # deps + ollama). uninstall removes only user-owned files.
        # fix re-runs install.sh which may hit sudo if previously chosen.
        needs_sudo = (self._action == "install"
                      and self._picks_need_sudo(deps))
        if needs_sudo and not self._sudo_authenticated:
            # Retry up to 3 times before giving up — typo-friendly
            # without inviting brute-force attempts.
            password = None
            last_hint = None
            for attempt in range(1, 4):
                error_hint = None
                if attempt > 1:
                    # Show the actual reason from sudo's stderr if we
                    # have one — much more useful than a generic
                    # "wrong password" because the cause might be
                    # tty/askpass/sudoers, not a typo.
                    error_hint = (
                        f"{last_hint or 'Wrong password'} — try again "
                        f"({attempt}/3)"
                    )
                password = self._prompt_sudo_password(error_hint=error_hint)
                if password is None:
                    break  # user cancelled
                ok, hint = self._cache_sudo(password)
                if ok:
                    self._sudo_authenticated = True
                    self._start_sudo_keepalive()
                    # Persist the password in a SUDO_ASKPASS script
                    # so install.sh's apt_install() can use sudo
                    # across the TTY boundary (sudo 1.9 tty_tickets
                    # makes per-process cache unshared without this).
                    self._setup_sudo_askpass(password)
                    break
                last_hint = hint
                if attempt == 3:
                    password = None

            if self._sudo_authenticated:
                argv = build_install_argv(
                    self.repo_dir, deps, no_sudo=False,
                    action=self._action, purge=self._purge)
            else:
                # 3× wrong password OR Cancel → ask the user how to
                # proceed. Default action is to abort (matching what
                # most users expect after typing the wrong password
                # repeatedly).
                choice = self._prompt_sudo_failed_choice()
                if choice == "abort":
                    self._append_log(
                        "Install aborted — sudo authentication failed.\n"
                        "  Re-run the wizard to try again.\n"
                    )
                    self._cancelled = True
                    self.lbl_phase.set_markup(
                        '<span foreground="red">Install aborted —'
                        ' sudo authentication failed.</span>'
                    )
                    self.progress.set_fraction(0.0)
                    self.progress.set_text("Aborted")
                    # Skip subprocess spawn entirely; user stays on
                    # progress page with the abort message until they
                    # cancel the dialog. Summary won't auto-advance.
                    return
                # choice == "skip_sudo" — original fallback flow
                self._append_log(
                    "Continuing without sudo — apt deps + Ollama are\n"
                    "skipped. AI CLIs (Claude, Copilot) install via\n"
                    "npm and DO work without sudo.\n"
                )
                argv = build_install_argv(
                    self.repo_dir, deps, no_sudo=True,
                    action=self._action, purge=self._purge)
        else:
            argv = build_install_argv(
                self.repo_dir, deps,
                no_sudo=(self._action != "install" or not needs_sudo),
                action=self._action, purge=self._purge)
        self._append_log("$ " + " ".join(shlex.quote(a) for a in argv) + "\n\n")

        # Open a per-run log file — full subprocess stdout streamed
        # here in addition to the in-memory log view. Lets users
        # attach the raw output to bug reports even after dismissing
        # the wizard. Persists alongside install.sh's own structured
        # install.log under ~/.config/bterminal/install-runs/.
        try:
            from datetime import datetime as _dt
            log_dir = (Path(os.path.expanduser("~"))
                       / ".config" / "bterminal" / "install-runs")
            log_dir.mkdir(parents=True, exist_ok=True)
            stamp = _dt.utcnow().strftime("%Y%m%d-%H%M%SZ")
            self._run_log_path = log_dir / f"wizard-run-{stamp}.log"
            self._run_log_fp = open(self._run_log_path, "w",
                                     encoding="utf-8")
            # Header — echo argv so log is self-contained
            self._run_log_fp.write(
                "# BTerminal install wizard run\n"
                f"# Started: {_dt.utcnow().isoformat()}Z\n"
                f"# Action: {self._action}\n"
                f"# Argv: {' '.join(shlex.quote(a) for a in argv)}\n"
                "#\n"
            )
            self._run_log_fp.flush()
        except OSError:
            self._run_log_fp = None
            self._run_log_path = None

        try:
            launcher = Gio.SubprocessLauncher.new(
                Gio.SubprocessFlags.STDOUT_PIPE
                | Gio.SubprocessFlags.STDERR_MERGE)
            # Pass SUDO_ASKPASS so install.sh's apt_install() can
            # use `sudo -A` to authenticate (works across TTY
            # boundary, unlike `sudo -n` which is gated by
            # tty_tickets in sudo 1.9+).
            askpass = getattr(self, "_sudo_askpass_path", None)
            if askpass:
                launcher.setenv("SUDO_ASKPASS", askpass, True)
            self._install_proc = launcher.spawnv(argv)
        except GLib.Error as exc:
            self._append_log(f"ERROR: spawn failed: {exc.message}\n")
            return

        # Read stdout in chunks via async readline loop.
        stdout = self._install_proc.get_stdout_pipe()
        self._install_stdin = Gio.DataInputStream.new(stdout)
        self._install_stdin.read_line_async(
            GLib.PRIORITY_DEFAULT, None, self._on_install_line, None,
        )
        self._install_proc.wait_async(None, self._on_install_done, None)

    def _on_install_line(self, source, result, _user):
        try:
            data, _length = source.read_line_finish_utf8(result)
        except GLib.Error:
            return
        if data is None:  # EOF
            return
        self._handle_install_line(data)
        # Schedule next read
        source.read_line_async(
            GLib.PRIORITY_DEFAULT, None, self._on_install_line, None,
        )

    # Pre-compiled regex for download progress lines from curl /
    # ollama install.sh — short lines that are pure `XX.X%` or
    # `#### XX.X%` (carriage-return-driven progress meters which
    # GTK TextView interprets as separate lines, flooding the log).
    # Match: optional `#`/`=`/`O`/`-` decoration, whitespace, `dd.d%`, EOL.
    _RX_PROGRESS_PCT = re.compile(
        r"^[\s#=O\-]*\s*(\d+(?:\.\d+)?)%\s*$"
    )
    # curl's default header line (printed once per download).
    _RX_CURL_HEADER = re.compile(
        r"^\s*(% Total|Dload\s+Upload).*"
    )
    # curl's multi-column data row — all-numeric / time / percent.
    _RX_CURL_DATA_ROW = re.compile(
        r"^[\s\d:%\-.]+$"
    )

    def _handle_install_line(self, line: str):
        """Parse one stdout line — JSON status update, download
        progress, or plain log."""
        # Always tee the raw line to per-run log file (if open) — keeps
        # bug-report attachments unmolested by ANSI stripping / parsing.
        if getattr(self, "_run_log_fp", None) is not None:
            try:
                self._run_log_fp.write(line)
                self._run_log_fp.flush()
            except OSError:
                pass
        status = parse_status_json_line(line)
        if status is not None:
            progress = max(0, min(100, int(status.get("progress", 0))))
            self.progress.set_fraction(progress / 100.0)
            self.progress.set_text(f"{progress}%")
            label = status.get("label", "")
            phase = status.get("phase", "")
            self.lbl_phase.set_markup(f"<i>{GLib.markup_escape_text(label)}</i>")
            if status.get("status") == "ok" and phase == "done":
                self._final_status_seen = True
            return

        # Detect download-progress noise lines from sub-installers
        # (curl / ollama / wget). They use `\r` to update one slot,
        # but TextView treats each `\r`-update as a fresh line —
        # without filtering you get hundreds of `1.0% / 1.1% / ...`
        # lines flooding the log view. Surface the % in the progress
        # bar instead, drop the line.
        stripped = line.rstrip("\r\n").lstrip()
        if stripped:
            m = self._RX_PROGRESS_PCT.match(stripped)
            if m:
                try:
                    pct = float(m.group(1))
                    # Show download as a sub-progress on the bar
                    # only when our main install hasn't reached the
                    # next phase yet (so we don't override status_json
                    # progress in non-download phases).
                    self.progress.set_text(f"Downloading… {pct:.1f}%")
                except ValueError:
                    pass
                return  # don't append to log
            if self._RX_CURL_HEADER.match(stripped):
                return  # silence curl multi-column header
            if self._RX_CURL_DATA_ROW.match(stripped) and " " in stripped:
                # All-numeric multi-column curl row — use as label
                # but don't flood the log
                self.lbl_phase.set_markup(
                    f"<small>{GLib.markup_escape_text(stripped[:80])}</small>")
                return

        # Otherwise: plain log line
        self._append_log(strip_ansi(line) + "\n")

    def _on_install_done(self, proc, result, _user):
        try:
            proc.wait_finish(result)
        except GLib.Error:
            pass
        # Capture exit code so summary page can show success vs failure
        try:
            self._install_rc = proc.get_exit_status()
        except (GLib.Error, AttributeError):
            self._install_rc = -1
        # Close per-run log file
        if getattr(self, "_run_log_fp", None) is not None:
            try:
                self._run_log_fp.write(
                    f"\n# Install finished. Exit code: {self._install_rc}\n")
                self._run_log_fp.close()
            except OSError:
                pass
            self._run_log_fp = None
        # Stop sudo keepalive — install is over, no more sudo needed.
        # Cached creds will expire naturally in ~15 min.
        self._stop_sudo_keepalive()
        # Wipe the SUDO_ASKPASS temp script holding the password.
        self._cleanup_sudo_askpass()
        # #111: transition to summary page in BOTH cases — user
        # who cancelled deserves a 'partial install' summary just
        # as much as a successful one. Pre-#111 only the success
        # path advanced; cancel left the wizard stuck on progress.
        # Summary page index (PAGES[5] = "summary"). Was hardcoded to
        # 4 pre-2026-05-08 when "uninstall_confirm" was inserted at
        # index 3 — bumping summary from 4 to 5.
        GLib.idle_add(lambda: (self._show_page(5), False)[1])

    def _append_log(self, text: str):
        buf = self.txt_log.get_buffer()
        end = buf.get_end_iter()
        buf.insert(end, text)
        # Scroll to bottom
        mark = buf.create_mark(None, buf.get_end_iter(), False)
        self.txt_log.scroll_mark_onscreen(mark)
        buf.delete_mark(mark)

    def _cancel_install(self):
        """Stop the install subprocess (Cancel mid-progress).

        #111 (audit § 6.2 #12): use SIGTERM (graceful) so install.sh's
        `trap '_on_interrupt' INT TERM` handler from #105 fires and
        rolls back from BACKUP_DIR. SIGKILL (force_exit) would skip
        the trap entirely, leaving partial state with no rollback.

        Schedule a follow-up SIGKILL after 5 s in case install.sh's
        rollback hangs (e.g. mid-`sudo apt-get install` waiting for
        password) — covers the worst case while letting the common
        case clean up gracefully.
        """
        self._cancelled = True
        if self._install_proc is not None:
            try:
                self._install_proc.send_signal(15)  # SIGTERM
            except GLib.Error:
                pass
            # Hard kill fallback if rollback hangs
            proc_ref = self._install_proc

            def _force_kill_if_alive():
                if proc_ref is None:
                    return False
                try:
                    proc_ref.force_exit()
                except GLib.Error:
                    pass
                return False

            GLib.timeout_add_seconds(5, _force_kill_if_alive)
            self._install_proc = None

    # ─── Public driver ─────────────────────────────────────────────────────

    def run_and_install(self) -> bool:
        """Run the wizard until the user finishes / cancels.

        Returns True when install completed AND user clicked
        'Open BTerminal' (caller should proceed with launch). Returns
        False on cancel or error.

        Task #8 + #9 (#80/#81): on successful completion, fires
        diagnostics.invalidate_cache() so subscribed UI panels (Files,
        sidebar Open With submenu) re-evaluate meld/xdg-open
        availability without requiring a BT restart.
        """
        try:
            while True:
                resp = self.run()
                if resp == _WIZARD_NEXT:
                    nxt = self._next_page_idx(self._current_page)
                    if nxt is not None:
                        self._show_page(nxt)
                elif resp == _WIZARD_BACK:
                    prv = self._prev_page_idx(self._current_page)
                    if prv is not None:
                        self._show_page(prv)
                elif resp == Gtk.ResponseType.OK:
                    self._notify_deps_changed()
                    return True
                elif resp == Gtk.ResponseType.CANCEL or resp < 0:
                    self._cancel_install()
                    return False
        finally:
            self._cancel_install()

    def _notify_deps_changed(self):
        """Drop diagnostics cache + notify subscribed panels."""
        try:
            from bterminal.diagnostics import invalidate_cache
            invalidate_cache()
        except Exception:
            # Don't let cache-invalidate failure block 'Open BTerminal'.
            pass


__all__ = [
    "InstallerWizard",
    "build_install_argv",
    "parse_status_json_line",
    "strip_ansi",
]
