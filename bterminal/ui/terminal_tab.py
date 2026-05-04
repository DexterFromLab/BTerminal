"""TerminalTab — VTE-based terminal tab for SSH/local/Claude Code sessions.

Each tab owns a Vte.Terminal widget plus session-specific state:
  - SSH session: spawn ssh subprocess, wire up disconnect handling
  - local: spawn user's shell
  - Claude Code: spawn `claude` with --resume + intro prompt; track
    stats bar, rule injection, task auto-trigger, idle timeout

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/ui/terminal_tab.py` in a later migration etap.
"""

import json
import os
import re
import shlex
import shutil
import subprocess
import threading
import time
import uuid
from datetime import datetime
from pathlib import Path

import gi
gi.require_version("Gtk", "3.0")
gi.require_version("Vte", "2.91")
gi.require_version("Gdk", "3.0")
from gi.repository import Gdk, GdkPixbuf, GLib, Gtk, Pango, Vte
import sqlite3

from bterminal.config import (
    CATPPUCCIN,
    CONFIG_DIR,
    CTX_DB,
    FONT,
    KEY_MAP,
    SCROLLBACK_LINES,
    SSH_PATH,
    _OPTIONS,
    _parse_color,
    _session_color,
    show_error_dialog,
    show_info_dialog,
    TERMINAL_PALETTE,
)
from bterminal.ui.stats import SessionStatsBar


class TerminalTab(Gtk.Box):
    """Zakładka terminala — lokalny shell lub SSH."""

    def __init__(self, app, session=None, claude_config=None, enabled_plugins=None):
        """Construct tab + spawn child process per session/claude_config.

        `enabled_plugins`: per-tab plugin gating. Must be passed PRZED
        spawn_claude() bo intro prompt computer woła _list_available_plugins
        + filtruje per tab.enabled_plugins. None = wszystkie globally-enabled
        plugins included (backwards compat).
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self.session = session
        self.claude_config = claude_config

        self.terminal = Vte.Terminal()
        self.terminal.set_font(Pango.FontDescription(FONT))
        self.terminal.set_scrollback_lines(SCROLLBACK_LINES)
        self.terminal.set_scroll_on_output(False)
        self.terminal.set_scroll_on_keystroke(True)
        self.terminal.set_audible_bell(False)

        # Catppuccin colors
        fg = _parse_color(CATPPUCCIN["text"])
        bg = _parse_color(CATPPUCCIN["base"])
        palette = [_parse_color(c) for c in TERMINAL_PALETTE]
        self.terminal.set_colors(fg, bg, palette)

        # Cursor color
        self.terminal.set_color_cursor(_parse_color(CATPPUCCIN["rosewater"]))
        self.terminal.set_color_cursor_foreground(_parse_color(CATPPUCCIN["crust"]))

        term_box = Gtk.Box(orientation=Gtk.Orientation.HORIZONTAL)
        term_box.pack_start(self.terminal, True, True, 0)
        scrollbar = Gtk.Scrollbar(
            orientation=Gtk.Orientation.VERTICAL,
            adjustment=self.terminal.get_vadjustment(),
        )
        term_box.pack_start(scrollbar, False, False, 0)
        self.pack_start(term_box, True, True, 0)

        self.terminal.connect("child-exited", self._on_child_exited)
        self.terminal.connect("window-title-changed", self._on_title_changed)
        self.terminal.connect("key-press-event", self._on_key_press)
        self.terminal.connect("button-press-event", self._on_button_press)

        # Drag & drop — accept files, paste path into terminal
        self.terminal.drag_dest_set(
            Gtk.DestDefaults.ALL,
            [Gtk.TargetEntry.new("text/uri-list", 0, 0)],
            Gdk.DragAction.COPY,
        )
        self.terminal.connect("drag-data-received", self._on_terminal_drag_received)

        # Tab label references for in-place updates (avoid widget recreation)
        self._tab_label_box = None
        self._tab_label_widget = None
        self._tab_label_text = None
        self._pending_macro_timers = []

        # Auto-trigger for task list (Claude Code tabs only)
        self._task_idle_timer = None
        self._task_project = None
        self._task_session_id = str(uuid.uuid4())
        self._inject_pending = None  # (project, count, refresh_every) when rules inject is due
        self._stats_bar = None
        # Per-tab plugin gating (Etap 8). None = backwards-compat: every
        # globally-enabled plugin contributes to this tab's intro prompt and
        # gets sidecar-acquired. A set means an explicit allow-list (e.g.
        # the user picked checkboxes in ClaudeCodeDialog).
        # R8.x: enabled_plugins MUSI być ustawione PRZED spawn_claude (które
        # woła _compute_intro_prompt_for_tab → _list_available_plugins).
        # Bug fix 2026-05-04: was previously set in app.py AFTER constructor
        # returned, which meant intro prompt always used None-default (=all).
        self.enabled_plugins: set[str] | None = (
            set(enabled_plugins) if isinstance(enabled_plugins, list) else None
        )
        if claude_config:
            project_dir = claude_config.get("project_dir", "")
            if project_dir:
                # lazy: helpers still in bterminal.py (move in Etap 7)
                from bterminal import _claude_log_dir, _resolve_ctx_project_name
                self._task_project = _resolve_ctx_project_name(project_dir)
                self._stats_bar = SessionStatsBar(project_dir)
                self.pack_end(self._stats_bar, False, False, 0)
                _claude_log_dir(project_dir).mkdir(parents=True, exist_ok=True)
            self.terminal.connect("contents-changed", self._on_contents_changed_tasks)

        self.show_all()

        if claude_config:
            self.spawn_claude(claude_config)
        elif session:
            self.spawn_ssh(
                session["host"],
                session.get("port", 22),
                session["username"],
                session.get("key_file", ""),
            )
        else:
            self.spawn_local_shell()

    def spawn_local_shell(self):
        shell = _OPTIONS.get("shell") or os.environ.get("SHELL", "/bin/bash")
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.environ.get("HOME", "/"),
            [shell],
            None,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            None,
        )

    def spawn_ssh(self, host, port, username, key_file=""):
        argv = [SSH_PATH]
        if key_file:
            argv += ["-i", key_file]
        argv += ["-p", str(port), f"{username}@{host}"]

        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            os.environ.get("HOME", "/"),
            argv,
            None,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            None,
        )

    def spawn_claude(self, config):
        """Spawn Claude Code session — with sudo askpass helper or direct.

        Always runs inside bash so that when claude exits, the shell
        stays alive and the tab doesn't auto-close.
        """
        # lazy: helpers still in bterminal.py (move in Etap 7/10)
        from bterminal import CLAUDE_PATH, _find_claude_path
        claude_path = CLAUDE_PATH or _find_claude_path()
        if not claude_path:
            work_dir = config.get("project_dir") or os.environ.get("HOME", "/")
            msg = (
                'printf "\\n\\033[1;31m━━━ Claude Code nie został znaleziony ━━━\\033[0m\\n\\n"\n'
                'printf "Sprawdzone lokalizacje:\\n"\n'
                'printf "  ~/.local/bin/claude\\n"\n'
                'printf "  ~/.npm-global/bin/claude\\n"\n'
                'printf "  /usr/local/bin/claude\\n"\n'
                'printf "  /usr/bin/claude\\n"\n'
                'printf "  /opt/homebrew/bin/claude\\n"\n'
                'printf "  ~/.nvm/versions/node/*/bin/claude\\n\\n"\n'
                'printf "Aby naprawić:\\n"\n'
                'printf "  1. Uruchom instalator ponownie: ./install.sh\\n"\n'
                'printf "  2. Lub zainstaluj ręcznie: npm install -g @anthropic-ai/claude-code\\n"\n'
                'printf "  3. Upewnij się, że ~/.npm-global/bin jest w PATH (~/.bashrc)\\n\\n"\n'
                'exec bash\n'
            )
            self.terminal.spawn_async(
                Vte.PtyFlags.DEFAULT,
                work_dir,
                ["/bin/bash", "-c", msg],
                None,
                GLib.SpawnFlags.DEFAULT,
                None,
                None,
                -1,
                None,
                None,
            )
            return

        flags = []
        if config.get("resume"):
            flags.append("--resume")
        if config.get("skip_permissions"):
            flags.append("--dangerously-skip-permissions")

        from bterminal import _compute_intro_prompt_for_tab  # lazy: still in bterminal.py
        prompt = _compute_intro_prompt_for_tab(self.app, self)
        # Record intro prompt for testing — prompt jest argumentem CLI, nie
        # feed_child, ale logicznie to "co BTerminal wysyła do AI CLI".
        from bterminal.debug_rest import record_feed
        record_feed("intro_prompt", (prompt or "").encode())
        prompt_arg = ""
        if prompt:
            escaped = prompt.replace("'", "'\\''")
            prompt_arg = f" '{escaped}'"

        flags_str = " ".join(flags)

        if config.get("sudo"):
            script = (
                'while true; do\n'
                '  read -rsp "Podaj hasło sudo: " SUDO_PW\n'
                '  echo\n'
                '  ASKPASS=$(mktemp /tmp/claude-askpass.XXXXXX)\n'
                '  chmod 700 "$ASKPASS"\n'
                '  printf \'#!/bin/bash\\necho "\'"%s"\'"\\n\' "$SUDO_PW" > "$ASKPASS"\n'
                '  export SUDO_ASKPASS="$ASKPASS"\n'
                '  if sudo -A true 2>/dev/null; then\n'
                '    unset SUDO_PW\n'
                '    break\n'
                '  fi\n'
                '  rm -f "$ASKPASS"\n'
                '  unset SUDO_PW\n'
                '  echo "Błędne hasło. Spróbuj ponownie."\n'
                'done\n'
                'trap \'rm -f "$ASKPASS"\' EXIT\n'
                f'{claude_path} {flags_str}{prompt_arg}\n'
                'exec bash\n'
            )
        else:
            script = f'{claude_path} {flags_str}{prompt_arg}\nexec bash\n'

        work_dir = config.get("project_dir") or os.environ.get("HOME", "/")
        self.terminal.spawn_async(
            Vte.PtyFlags.DEFAULT,
            work_dir,
            ["/bin/bash", "-c", script],
            None,
            GLib.SpawnFlags.DEFAULT,
            None,
            None,
            -1,
            None,
            None,
        )

    def run_macro(self, macro):
        """Execute macro steps chained via GLib.timeout_add."""
        steps = macro.get("steps", [])
        if not steps:
            return

        def execute_steps(step_index):
            if step_index >= len(steps):
                return False
            step = steps[step_index]
            if step["type"] == "text":
                self.terminal.feed_child(step["value"].encode())
                GLib.timeout_add(50, execute_steps, step_index + 1)
            elif step["type"] == "key":
                key_str = KEY_MAP.get(step["value"], "")
                if key_str:
                    self.terminal.feed_child(key_str.encode())
                GLib.timeout_add(50, execute_steps, step_index + 1)
            elif step["type"] == "delay":
                GLib.timeout_add(int(step["value"]), execute_steps, step_index + 1)
            return False

        GLib.timeout_add(500, execute_steps, 0)

    def _on_key_press(self, terminal, event):
        mod = event.state & Gtk.accelerator_get_default_mod_mask()
        ctrl = Gdk.ModifierType.CONTROL_MASK
        shift = Gdk.ModifierType.SHIFT_MASK

        # Ctrl+Shift+C: copy
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_C, Gdk.KEY_c):
            if terminal.get_has_selection():
                terminal.copy_clipboard_format(Vte.Format.TEXT)
            return True

        # Ctrl+Shift+V: paste (clipboard image → save & paste path, else text)
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_V, Gdk.KEY_v):
            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            if clipboard.wait_is_image_available():
                if self._paste_clipboard_image_path():
                    return True
            terminal.paste_clipboard()
            return True

        # Ctrl+T: new tab (forward to app)
        if mod == ctrl and event.keyval == Gdk.KEY_t:
            self.app.add_local_tab()
            return True

        # Ctrl+Shift+W: close tab
        if mod == (ctrl | shift) and event.keyval in (Gdk.KEY_W, Gdk.KEY_w):
            self.app.close_tab(self)
            return True

        # Ctrl+PageUp/PageDown: switch tabs
        if mod == ctrl and event.keyval == Gdk.KEY_Page_Up:
            idx = self.app.notebook.get_current_page()
            if idx > 0:
                self.app.notebook.set_current_page(idx - 1)
            return True
        if mod == ctrl and event.keyval == Gdk.KEY_Page_Down:
            idx = self.app.notebook.get_current_page()
            if idx < self.app.notebook.get_n_pages() - 1:
                self.app.notebook.set_current_page(idx + 1)
            return True

        # Ctrl+G: toggle git panel (only for Claude Code tabs)
        if mod == ctrl and event.keyval == Gdk.KEY_g:
            if self.claude_config:
                self.app.toggle_git_panel()
            return True

        # Track Enter key for prompt counter (Claude Code sessions)
        if self._stats_bar and event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._stats_bar.increment_prompt()
            self._maybe_inject_rules()

        return False

    def _maybe_inject_rules(self):
        """After each prompt, check if it's time to inject rules or refresh CTX.

        Sets a pending flag — actual injection happens after Claude goes idle,
        so the message arrives at the next free prompt (not mid-processing).
        """
        if not self._stats_bar or not self.claude_config:
            return
        project_dir = self.claude_config.get("project_dir", "")
        if not project_dir:
            return
        project = self._task_project or os.path.basename(project_dir.rstrip("/"))
        count = self._stats_bar._prompt_count

        inject_every = 100
        refresh_every = 200
        try:
            if os.path.exists(CTX_DB):
                db = sqlite3.connect(CTX_DB)
                row = db.execute(
                    "SELECT inject_every, refresh_every FROM rules_config WHERE project = ?",
                    (project,),
                ).fetchone()
                db.close()
                if row:
                    inject_every = row[0]
                    refresh_every = row[1]
        except Exception:
            pass

        if count > 0 and (count == inject_every or count % inject_every == 0):
            self._inject_pending = (project, count, refresh_every)
            import datetime
            with open("/tmp/bterminal_inject.log", "a") as f:
                f.write(f"{datetime.datetime.now()}: pending set project={project} count={count}\n")

    def _do_inject_rules(self, project, count, refresh_every):
        """Inject the project's rules block (and, on refresh boundaries, a
        separate ctx refresh block) into the terminal.

        Header / global rules / tools-help are NOT included here — they are
        already part of the session intro prompt at start_claude_session and
        re-injecting them on every interval would bury the actual rules in
        ~2500 chars of repeated boilerplate. The injected block is now just
        what `ctx rules inject <project>` returns. If a ctx refresh is also
        due (count % refresh_every == 0) it is sent as a SECOND, clearly
        labelled message after a short delay so Claude treats the two as
        distinct reminders.
        """
        self._inject_pending = None
        try:
            result = subprocess.run(
                ["ctx", "rules", "inject", project],
                capture_output=True, text=True, timeout=5,
            )
            project_block = result.stdout.strip()
        except Exception:
            project_block = ""

        if not project_block:
            return

        import datetime
        with open("/tmp/bterminal_inject.log", "a") as f:
            f.write(f"{datetime.datetime.now()}: injecting {len(project_block)} chars (rules) for {project}\n")
        from bterminal.debug_rest import record_feed
        record_feed("rules_inject", project_block.encode())
        self.terminal.feed_child(project_block.encode())
        GLib.timeout_add(100, lambda: self.terminal.feed_child(b"\r") or False)

        if count % refresh_every == 0:
            # Schedule the ctx refresh separately — 800ms later so Claude
            # finishes processing the rules-only message first.
            GLib.timeout_add(800, self._do_inject_ctx_refresh, project)

    def _do_inject_ctx_refresh(self, project):
        """Send only the ctx context refresh block. Separate concern from
        rules so the user's intent ('every N prompts remind me of rules')
        is not conflated with 'every M prompts re-load project context'.
        """
        try:
            ctx_result = subprocess.run(
                ["ctx", "get", project, "--shared"],
                capture_output=True, text=True, timeout=5,
            )
            ctx_block = ctx_result.stdout.strip()
        except Exception:
            ctx_block = ""

        if not ctx_block:
            return False

        labelled = (
            f"=== odświeżenie kontekstu projektu [{project}] ===\n\n"
            f"{ctx_block}"
        )

        import datetime
        with open("/tmp/bterminal_inject.log", "a") as f:
            f.write(f"{datetime.datetime.now()}: injecting {len(labelled)} chars (ctx refresh) for {project}\n")
        from bterminal.debug_rest import record_feed
        record_feed("ctx_refresh", labelled.encode())
        self.terminal.feed_child(labelled.encode())
        GLib.timeout_add(100, lambda: self.terminal.feed_child(b"\r") or False)
        return False  # don't repeat (used as GLib.timeout_add callback)

    def _on_button_press(self, terminal, event):
        if event.button == 3:  # right click
            menu = Gtk.Menu()

            item_copy = Gtk.MenuItem(label="Copy")
            item_copy.set_sensitive(terminal.get_has_selection())
            item_copy.connect("activate", lambda _: terminal.copy_clipboard_format(Vte.Format.TEXT))
            menu.append(item_copy)

            clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
            has_image = clipboard.wait_is_image_available()

            item_paste = Gtk.MenuItem(label="Paste")
            if has_image:
                item_paste.connect("activate",
                                   lambda _: self._paste_clipboard_image_path() or
                                   terminal.paste_clipboard())
            else:
                item_paste.connect("activate", lambda _: terminal.paste_clipboard())
            menu.append(item_paste)

            menu.append(Gtk.SeparatorMenuItem())

            item_select_all = Gtk.MenuItem(label="Select All")
            item_select_all.connect("activate", lambda _: terminal.select_all())
            menu.append(item_select_all)

            menu.append(Gtk.SeparatorMenuItem())

            item_paste_img = Gtk.MenuItem(label="Paste Image")
            item_paste_img.set_sensitive(_clipboard_has_image_or_path())
            item_paste_img.connect("activate", lambda _: self._on_paste_image_to_ctx())
            menu.append(item_paste_img)

            menu.show_all()
            menu.popup_at_pointer(event)
            return True
        return False

    def _on_child_exited(self, terminal, status):
        self.app.on_tab_child_exited(self)

    def _on_title_changed(self, terminal):
        title = terminal.get_window_title()
        if title:
            if self.session:
                # SSH tab: keep session name, show VTE title in window title only
                self.app.update_tab_title(self, self.session.get("name", "SSH"))
            elif self.claude_config:
                # Claude Code tab: keep decorated tab name with number + emoji
                display = getattr(self, "_claude_tab_display", self.claude_config.get("name", "Claude Code"))
                self.app.update_tab_title(self, display)
            else:
                self.app.update_tab_title(self, title)

    def _on_contents_changed_tasks(self, terminal):
        """Reset idle timer on every terminal content change (Claude tabs only)."""
        if self._task_idle_timer:
            GLib.source_remove(self._task_idle_timer)
        self._task_idle_timer = GLib.timeout_add_seconds(
            10, self._on_task_idle_timeout
        )

    def _on_task_idle_timeout(self):
        """Called when Claude has been idle for 10 seconds — check for pending tasks and rule injections."""
        # TODO(provider-abstraction): generalize for multi-CLI (Copilot/Aider/...).
        # Currently hardcoded to Claude (requires self._stats_bar + self.claude_config).
        # Per REQUIREMENTS.md Q21.3 — Claude-only for now. Future refactor:
        #   1. Provider capability flag `task_auto_trigger: bool` w providers.json
        #      (Aider TAK — terminal-based; Copilot NIE — different invocation model).
        #   2. Move logic from TerminalTab.{_on_task_idle_timeout, _claim_next_task}
        #      to generic AISessionMixin or Provider.handle_idle().
        #   3. Decouple from _stats_bar (use tab.provider zamiast claude_config check).
        #   4. Test contract: mock provider with capability=true + scenario z 3 zadaniami,
        #      assert że trigger fires na każdym idle.
        self._task_idle_timer = None

        # Inject rules if due (only when no task is about to fire)
        if self._inject_pending:
            project, count, refresh_every = self._inject_pending
            self._do_inject_rules(project, count, refresh_every)
            return False

        if not self._task_project:
            return False
        try:
            if not os.path.exists(CTX_DB):
                return False
            db = sqlite3.connect(CTX_DB)
            db.row_factory = sqlite3.Row

            # Check autorun flag
            config = db.execute(
                "SELECT autorun FROM task_config WHERE project = ?",
                (self._task_project,),
            ).fetchone()
            if not config or not config["autorun"]:
                db.close()
                return False

            # Atomically find and claim next open unclaimed task
            task = self._claim_next_task(db, self._task_project, self._task_session_id)
            db.close()

            if not task:
                return False

            # Trigger: feed task instruction with specific claimed task
            message = (
                f"[AUTO-TRIGGER] Twoje przypisane zadanie: {task['task_id']} — {task['description']}\n"
                f"Sprawdź pełną listę: tasks context {self._task_project} --session {self._task_session_id}\n"
                f"MUSISZ oznaczyć po wykonaniu: tasks done {self._task_project} {task['task_id']} (w Bash). "
                f"Pętla auto-trigger kończy się DOPIERO gdy WSZYSTKIE zadania są zamknięte (done). "
                f"Jeśli nie oznaczysz — ta wiadomość będzie się powtarzać."
            )
            terminal = self.terminal
            from bterminal.debug_rest import record_feed
            record_feed("auto_trigger", message.encode())
            terminal.feed_child(message.encode())
            GLib.timeout_add(100, lambda: terminal.feed_child(b"\r") or False)

            # Refresh task panel if visible
            if hasattr(self.app, "task_panel"):
                GLib.idle_add(self.app.task_panel.refresh)
        except Exception:
            pass
        return False

    @staticmethod
    def _claim_next_task(db, project, session_id):
        """Atomically find and claim the next open unclaimed task. Returns task dict or None."""
        # First check if this session already has a claimed open task
        existing = db.execute(
            """SELECT t.task_id, t.description FROM tasks t
               JOIN task_claims c ON c.project = t.project AND c.task_id = t.task_id
               WHERE t.project = ? AND c.session_id = ? AND t.status = 'open'
               ORDER BY t.task_id LIMIT 1""",
            (project, session_id),
        ).fetchone()
        if existing:
            return existing

        # Find next open task not claimed by anyone
        rows = db.execute(
            """SELECT t.task_id, t.description FROM tasks t
               LEFT JOIN task_claims c ON c.project = t.project AND c.task_id = t.task_id
               WHERE t.project = ? AND t.status = 'open' AND c.task_id IS NULL""",
            (project,),
        ).fetchall()
        if not rows:
            return None

        # Sort by task_id naturally and pick the first
        def _sort_key(task_id):
            parts = task_id.split(".")
            result = []
            for p in parts:
                try:
                    result.append((0, int(p), ""))
                except ValueError:
                    result.append((1, 0, p))
            return result

        rows_sorted = sorted(rows, key=lambda r: _sort_key(r["task_id"]))
        task = rows_sorted[0]

        # Claim it
        try:
            db.execute(
                "INSERT INTO task_claims (project, task_id, session_id) VALUES (?, ?, ?)",
                (project, task["task_id"], session_id),
            )
            db.commit()
            return task
        except sqlite3.IntegrityError:
            # Race condition — another session claimed it between SELECT and INSERT
            return None

    def _paste_clipboard_image_path(self):
        """Save clipboard image to project copied_images/ and paste path.
        Returns True on success, False if no image could be retrieved."""
        clipboard = Gtk.Clipboard.get(Gdk.SELECTION_CLIPBOARD)
        pixbuf = clipboard.wait_for_image()

        # Fallback: try raw PNG data from clipboard targets
        if not pixbuf:
            sel_data = clipboard.wait_for_contents(Gdk.Atom.intern("image/png", False))
            if sel_data:
                raw = sel_data.get_data()
                if raw:
                    loader = GdkPixbuf.PixbufLoader.new_with_type("png")
                    try:
                        loader.write(raw)
                        loader.close()
                        pixbuf = loader.get_pixbuf()
                    except GLib.Error:
                        pixbuf = None

        if not pixbuf:
            return False

        # Determine target directory
        base_dir = None
        if self.claude_config:
            proj_dir = self.claude_config.get("project_dir", "")
            if proj_dir and os.path.isdir(proj_dir):
                base_dir = proj_dir
        if not base_dir:
            base_dir = os.path.expanduser("~")
        images_dir = os.path.join(base_dir, "copied_images")
        os.makedirs(images_dir, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:12]}.png"
        dest = os.path.join(images_dir, filename)
        pixbuf.savev(dest, "png", [], [])
        # Replace clipboard with path text and use native VTE paste
        clipboard.set_text(dest, -1)
        clipboard.store()
        self.terminal.paste_clipboard()
        # Also register in ctx if available
        project = self._detect_ctx_project()
        if project:
            _save_ctx_image(project, dest, original_name="clipboard.png")
            if hasattr(self.app, "ctx_panel"):
                self.app.ctx_panel.refresh()
        return True

    def _detect_ctx_project(self):
        """Auto-detect ctx project from tab config, or ask user."""
        if not os.path.exists(CTX_DB):
            return None
        # Try auto-detect from claude config
        if self.claude_config:
            proj_dir = self.claude_config.get("project_dir", "")
            if proj_dir:
                candidate = os.path.basename(proj_dir.rstrip("/"))
                db = sqlite3.connect(CTX_DB)
                exists = db.execute(
                    "SELECT 1 FROM sessions WHERE name = ?", (candidate,)
                ).fetchone()
                db.close()
                if exists:
                    return candidate
        # Fallback: show dialog
        db = sqlite3.connect(CTX_DB)
        projects = [
            r[0] for r in db.execute(
                "SELECT name FROM sessions ORDER BY name"
            ).fetchall()
        ]
        db.close()
        if not projects:
            return None
        dlg = Gtk.Dialog(
            title="Save Image to Project",
            transient_for=self.app,
            modal=True,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dlg.set_default_size(300, -1)
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)
        lbl = Gtk.Label(label="Select project for image:")
        lbl.set_xalign(0)
        box.pack_start(lbl, False, False, 0)
        combo = Gtk.ComboBoxText()
        for p in projects:
            combo.append_text(p)
        # Pre-select project matching current Claude session
        preselect = 0
        if self.claude_config:
            proj_dir = self.claude_config.get("project_dir", "")
            if proj_dir:
                basename = os.path.basename(proj_dir.rstrip("/"))
                for i, p in enumerate(projects):
                    if p == basename:
                        preselect = i
                        break
        combo.set_active(preselect)
        box.pack_start(combo, False, False, 0)
        dlg.show_all()
        project = None
        if dlg.run() == Gtk.ResponseType.OK:
            project = combo.get_active_text()
        dlg.destroy()
        return project

    def _on_terminal_drag_received(self, widget, context, x, y, data, info, time):
        """Handle files dropped onto terminal — paste file path."""
        uris = data.get_uris()
        if not uris:
            return
        paths = []
        for uri in uris:
            if uri.startswith("file://"):
                try:
                    path = GLib.filename_from_uri(uri)[0]
                    paths.append(path)
                except Exception:
                    pass
        if paths:
            text = " ".join(paths)
            self.terminal.feed_child(text.encode("utf-8"))

    def _on_paste_image_to_ctx(self):
        """Paste clipboard image (bitmap or file path) to a ctx project."""
        pixbuf, file_path = _clipboard_get_image_or_path()
        if not pixbuf and not file_path:
            return
        if not os.path.exists(CTX_DB):
            return
        db = sqlite3.connect(CTX_DB)
        projects = [
            r[0] for r in db.execute(
                "SELECT name FROM sessions ORDER BY name"
            ).fetchall()
        ]
        db.close()
        if not projects:
            return

        dlg = Gtk.Dialog(
            title="Paste Image to Project",
            transient_for=self.app,
            modal=True,
        )
        dlg.add_buttons(
            Gtk.STOCK_CANCEL, Gtk.ResponseType.CANCEL,
            Gtk.STOCK_OK, Gtk.ResponseType.OK,
        )
        dlg.set_default_size(300, -1)
        box = dlg.get_content_area()
        box.set_border_width(12)
        box.set_spacing(8)
        lbl = Gtk.Label(label="Select project:")
        lbl.set_xalign(0)
        box.pack_start(lbl, False, False, 0)
        combo = Gtk.ComboBoxText()
        for p in projects:
            combo.append_text(p)
        # Pre-select project matching current Claude session
        preselect = 0
        if self.claude_config:
            proj_dir = self.claude_config.get("project_dir", "")
            if proj_dir:
                basename = os.path.basename(proj_dir.rstrip("/"))
                for i, p in enumerate(projects):
                    if p == basename:
                        preselect = i
                        break
        combo.set_active(preselect)
        box.pack_start(combo, False, False, 0)
        dlg.show_all()
        if dlg.run() == Gtk.ResponseType.OK:
            project = combo.get_active_text()
            if project:
                source = pixbuf if pixbuf else file_path
                _save_ctx_image(project, source)
                if hasattr(self.app, "ctx_panel"):
                    self.app.ctx_panel.refresh()
        dlg.destroy()

    def get_label(self):
        if self.claude_config:
            return getattr(self, "_claude_tab_display", self.claude_config.get("name", "Claude Code"))
        if self.session:
            return self.session.get("name", "SSH")
        return "Terminal"

