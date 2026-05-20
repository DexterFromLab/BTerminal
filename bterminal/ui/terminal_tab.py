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


# ─── R7a visual marker helpers (T2.7) ───────────────────────────────────────
#
# Per R7a (REQUIREMENTS.md): every tab carries a visual marker that
# tells the user at a glance which kind of session is running.
#   AI tabs  → provider.display.icon ("✨", "🤖", ...) + tooltip with
#              long_label, optional underline in provider.display.color.
#   SSH tabs → 🔐
#   local    → 💻
#
# compute_tab_label() is a pure function — testable without GTK — so
# tests can verify the icon/tooltip/color combinations directly.

_DEFAULT_AI_FALLBACK_ICON = "🤖"
_SSH_TAB_ICON = "🔐"
_LOCAL_TAB_ICON = "💻"


def compute_tab_label(
    ai_config: dict | None,
    session_name: str,
    count: int = 0,
    registry=None,
    kind: str = "ai",
) -> dict:
    """Compute display text / tooltip / color for a tab label.

    ai_config:    AI session config dict (with `provider` field) or None.
    session_name: base name of the session.
    count:        how many sibling tabs already share this name; >0
                  appends ` #{count+1}` suffix to disambiguate.
    registry:     ProviderRegistry instance — looked up to fetch the
                  provider's display metadata. None or unknown name
                  → fallback to generic AI icon.
    kind:         "ai" (default), "ssh", "local". When ai_config is
                  None, this picks SSH/local-specific icons.

    Returns dict with keys: "display", "tooltip", "color".
    """
    suffix = f" #{count + 1}" if count > 0 else ""

    if kind == "ssh" or (ai_config is None and kind == "ssh"):
        return {
            "display": f"{_SSH_TAB_ICON} {session_name}{suffix}",
            "tooltip": f"SSH: {session_name}",
            "color": None,
        }
    if kind == "local" or (ai_config is None and kind == "local"):
        return {
            "display": f"{_LOCAL_TAB_ICON} {session_name}",
            "tooltip": f"Local terminal: {session_name}",
            "color": None,
        }

    # AI session (default)
    provider_name = (ai_config or {}).get("provider", "claude")
    icon = _DEFAULT_AI_FALLBACK_ICON
    long_label = "AI session"
    provider_color: str | None = None
    if registry is not None and registry.has(provider_name):
        provider = registry.get(provider_name)
        icon = provider.display.icon
        long_label = provider.display.long_label
        provider_color = provider.display.color

    # session.color overrides provider.display.color
    color = (ai_config or {}).get("color") or provider_color
    return {
        "display": f"{icon} {session_name}{suffix}",
        "tooltip": f"{long_label}: {session_name}",
        "color": color,
    }


# #124 (audit § 6.6 #25): cap rules block size at 50 MB. PTY feed
# of multi-megabyte writes blocks the GTK main loop on the kernel
# pipe writes (PIPE_BUF=4 KB → 50 MB chunks into ~12 800 syscalls).
# Above that, BT's UI freezes for seconds — `ctx rules inject` is
# expected to produce O(KB) bytes, not O(MB). Anything beyond this
# threshold is almost certainly a corrupt context or an accidental
# `ctx set` of file contents — refuse loudly so the user notices.
_RULES_INJECT_MAX_BYTES = 50 * 1024 * 1024  # 50 MB hard cap

# Key groups used by the typing guard (_typing_state_after_key).
# Raw GDK integer values so the sets are importable without GTK,
# which makes them unit-testable outside the GTK process.
_TYPING_SUBMIT_KEYS = frozenset({
    0xff0d,  # Gdk.KEY_Return
    0xff8d,  # Gdk.KEY_KP_Enter
    0xff1b,  # Gdk.KEY_Escape
})
_TYPING_CTRL_ABORT_KEYS = frozenset({
    0x0063, 0x0043,  # Gdk.KEY_c / KEY_C  (Ctrl+C — interrupt)
    0x0064, 0x0044,  # Gdk.KEY_d / KEY_D  (Ctrl+D — EOF)
})
_TYPING_PURE_MODIFIER_KEYS = frozenset({
    0xffe1, 0xffe2,  # Shift_L / Shift_R
    0xffe3, 0xffe4,  # Control_L / Control_R
    0xffe9, 0xffea,  # Alt_L / Alt_R
    0xffeb, 0xffec,  # Super_L / Super_R
    0xffe7, 0xffe8,  # Meta_L / Meta_R
    0xffe5,          # Caps_Lock
    0xff7f,          # Num_Lock
    0xff14,          # Scroll_Lock
    0xfe03,          # ISO_Level3_Shift (AltGr on some layouts)
})

def extract_rules_inject_bytes(
        provider_name: str, project_name: str, rules_stdout: str) -> bytes:
    """Compose the EXACT bytes BT feeds via VTE feed_child during a
    rules_inject pass.

    R7b (pinned by #93): the rules block format MUST be identical
    across all AI providers — Claude/Copilot/Aider all receive the
    same bytes. The format is whatever `ctx rules inject <project>`
    produces, stripped of trailing whitespace, encoded as UTF-8. The
    `provider_name` argument is kept in the signature on purpose:
    it documents that this function MUST stay provider-agnostic, and
    tests pass all 3 providers through it to assert byte equality.

    Used by:
      - _do_inject_rules at the rules_inject feed site (production)
      - test_rules_inject_provider_parity.py (asserts parity)

    The carriage-return ('\\r') feed that follows is sent separately
    via GLib.timeout_add(100, …); it's not included here because it's
    universal across all feed paths (rules, ctx_refresh, intro, …),
    not specific to rules_inject.

    #124: bytes exceeding `_RULES_INJECT_MAX_BYTES` (50 MB) return
    empty `b""` + a stderr warning. Caller (`_do_inject_rules`) sees
    the empty bytes and treats it as 'no rules to inject', avoiding
    a multi-second main loop block.
    """
    # Intentionally NOT branching on provider_name — the contract is
    # 'same bytes for everyone'. If a future provider ever needs a
    # different format, reify a per-provider hook in capabilities and
    # invalidate this docstring.
    del provider_name  # marker that this function ignores it
    encoded = rules_stdout.strip().encode()
    if len(encoded) > _RULES_INJECT_MAX_BYTES:
        import sys as _sys
        print(
            f"[bterminal] WARN: rules_inject block for project "
            f"{project_name!r} is {len(encoded)} bytes "
            f"(> {_RULES_INJECT_MAX_BYTES}-byte cap) — refusing to "
            f"feed. Check `ctx rules inject {project_name}` output "
            f"for accidental file content.",
            file=_sys.stderr,
        )
        return b""
    return encoded


def should_inject_rules(ai_config: dict | None, registry) -> bool:
    """T3.7 capability dispatch — periodic rules re-injection runs only
    for providers that declare `rules_inject: true` in their capabilities.

    PTY feed_child works identically across providers (any TTY-backed
    CLI receives the bytes), so this is essentially an opt-out flag —
    a future provider that wants its own injection format flips the
    capability off and registers a custom hook.

    Returns False (skip) when:
      - ai_config is empty (SSH / local tabs).
      - provider isn't registered.
      - capabilities.rules_inject is False.
    """
    if not ai_config:
        return False
    provider_name = ai_config.get("provider", "claude")
    try:
        provider = registry.get(provider_name)
    except (KeyError, AttributeError):
        return False
    return bool(provider.capabilities.rules_inject)


def should_run_auto_trigger(ai_config: dict | None, registry) -> bool:
    """T3.6 capability dispatch — auto-trigger runs only for providers
    that declare `task_auto_trigger: true` in their capabilities.

    Returns False (skip auto-trigger) when:
      - ai_config is empty (SSH / local tabs — never had this feature).
      - provider isn't in the registry (unknown future-version provider).
      - capabilities.task_auto_trigger is False (Copilot at T3 baseline;
        T4.1 wires up events.jsonl tail-f and flips this True).
    """
    if not ai_config:
        return False
    provider_name = ai_config.get("provider", "claude")
    try:
        provider = registry.get(provider_name)
    except (KeyError, AttributeError):
        return False
    return bool(provider.capabilities.task_auto_trigger)


class TerminalTab(Gtk.Box):
    """Zakładka terminala — lokalny shell lub SSH."""

    def __init__(self, app, session=None, ai_config=None, enabled_plugins=None,
                 claude_config=None):
        """Construct tab + spawn child process per session/ai_config.

        `ai_config`: AI CLI session config dict (R4.2 schema, with
        `provider` field). Replaces the legacy `claude_config` kwarg —
        the old name is still accepted for one release as a backward-
        compat alias (T1.8 → cleanup in T4.6).

        `enabled_plugins`: per-tab plugin gating. Must be passed PRZED
        spawn_claude() bo intro prompt computer woła _list_available_plugins
        + filtruje per tab.enabled_plugins. None = wszystkie globally-enabled
        plugins included (backwards compat).
        """
        super().__init__(orientation=Gtk.Orientation.VERTICAL)
        self.app = app
        self.session = session
        # T1.8: ai_config is the new canonical name. claude_config kwarg is
        # the deprecated alias (kept until T4.6 cleanup).
        self.ai_config = ai_config if ai_config is not None else claude_config

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
        self._inject_pending_ts = 0.0  # monotonic ts when pending was set (for hard-cap force-fire)
        self._last_content_change = 0.0  # monotonic ts of last VTE contents-changed event
        self._user_is_typing = False  # True while user has uncommitted text in the input line
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
        if self.ai_config:
            project_dir = self.ai_config.get("project_dir", "")
            if project_dir:
                # lazy: helpers still in bterminal.py (move in Etap 7)
                from bterminal import _claude_log_dir, _resolve_ctx_project_name
                self._task_project = _resolve_ctx_project_name(project_dir)

                # T3.5: capability dispatch — bar is mounted only if
                # the provider declares stats_bar=true; the matching
                # reader (Claude JSONL / Copilot events.jsonl / ...)
                # is wired in by the factory.
                # T3.9: stats_widget_options_for_ai_config supplies
                # `hide_plan_usage` for providers without a usage API
                # (Copilot — no public plan-usage endpoint).
                from bterminal.providers import get_registry
                from bterminal.ui.stats import (
                    create_stats_reader_for_ai_config,
                    stats_widget_options_for_ai_config,
                )
                _reg = get_registry()
                reader = create_stats_reader_for_ai_config(
                    self.ai_config, _reg,
                )
                if reader is not None:
                    widget_opts = stats_widget_options_for_ai_config(
                        self.ai_config, _reg,
                    )
                    self._stats_bar = SessionStatsBar(
                        project_dir, reader=reader, **widget_opts,
                    )
                    self.pack_end(self._stats_bar, False, False, 0)

                _claude_log_dir(project_dir).mkdir(parents=True, exist_ok=True)
            self.terminal.connect("contents-changed", self._on_contents_changed_tasks)

        self.show_all()

        if self.ai_config:
            self.spawn_ai_cli(self.ai_config)
        elif session:
            self.spawn_ssh(
                session["host"],
                session.get("port", 22),
                session["username"],
                session.get("key_file", ""),
            )
        else:
            self.spawn_local_shell()

    # ─── Backward-compat property (T1.8 → cleanup T4.6) ─────────────────────

    @property
    def claude_config(self):
        """Deprecated alias for ai_config (T1.8).

        Returns ai_config when the tab's provider == "claude" so legacy
        readers (panels, plugins, REST consumers) continue to work for
        Claude tabs. Returns None for non-Claude providers — that way
        Claude-specific code paths (auto-trigger, rules inject, stats
        bar in pre-T3 dispatch) silently skip Copilot tabs instead of
        treating their config as a Claude config. Cleanup in T4.6.
        """
        cfg = self.ai_config
        if cfg and cfg.get("provider", "claude") == "claude":
            return cfg
        return None

    @claude_config.setter
    def claude_config(self, value):
        """Setter for legacy callers — assigns through to ai_config."""
        self.ai_config = value

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

    # T2.1: Sudo askpass prologue extracted as a constant so the pure
    # build_spawn_script function below stays simple to test. Runs
    # before the CLI binary, captures user's sudo password into a
    # tempfile, and exports SUDO_ASKPASS so the CLI's sudo invocations
    # don't prompt mid-session.
    #
    # BUG#31d: per-terminal read-loop refactored out so the same fallback
    # can be reused when the shared askpass (set at the app level) fails
    # its pre-check (cache expired).
    _INTERACTIVE_SUDO_READ_LOOP = (
        'while true; do\n'
        '  read -rsp "Enter sudo password: " SUDO_PW\n'
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
        '  echo "Incorrect password. Please try again."\n'
        'done\n'
        'trap \'rm -f "$ASKPASS"\' EXIT\n'
    )
    # Backwards-compat alias for callers/tests pre-BUG#31d.
    _SUDO_ASKPASS_PROLOGUE = _INTERACTIVE_SUDO_READ_LOOP

    @staticmethod
    def _build_shared_askpass_prologue(askpass_path):
        """BUG#31d: prologue using a shared askpass tempfile created by
        SudoAskpassCache. The `sudo -A true` pre-check graceful-fallbacks
        to the interactive read-loop when the cached sudo timestamp has
        expired."""
        quoted = shlex.quote(askpass_path)
        # BUG#31g: when BTERMINAL_TEST_FAKE_SUDO=1, SudoAskpassCache.ensure()
        # accepts any non-empty password without contacting real sudo, so the
        # askpass script holds a bogus password. Skip the verify-then-fallback
        # branch in that env so component tests can validate the happy path
        # end-to-end without granting the test runner root.
        # No `sudo -A true` pre-check: ensure() already validated the password
        # and a `sudo -A true` call in bash hangs on some systems (PAM/D-Bus
        # blocking without a proper PTY context). SUDO_ASKPASS is set; sudo
        # calls the script automatically when it needs the password.
        return f'export SUDO_ASKPASS={quoted}\n'

    @staticmethod
    def _build_binary_not_found_script(provider):
        """Generic 'binary not found' error message for any provider.

        Generalized from the Claude-only printf in pre-T2.1 spawn_claude:
        reads search paths from provider.binary spec and renders them
        in the terminal so the user knows which locations were checked.
        """
        name = provider.display.long_label
        search_paths = (
            getattr(provider, "_binary_spec", {}) or {}
        ).get("search_paths", [])
        paths_lines = "".join(
            f'printf "  {shlex.quote(p)[1:-1]}\\n"\n' if "'" in p
            else f'printf "  {p}\\n"\n'
            for p in search_paths
        )
        return (
            f'printf "\\n\\033[1;31m━━━ {name} not found ━━━\\033[0m\\n\\n"\n'
            'printf "Locations checked:\\n"\n'
            f'{paths_lines}'
            'printf "\\n"\n'
            'printf "To fix:\\n"\n'
            'printf "  1. Re-run the installer: ./install.sh\\n"\n'
            'printf "  2. Or install the CLI manually for your provider\\n"\n'
            'printf "  3. Make sure ~/.npm-global/bin is in PATH (~/.bashrc)\\n\\n"\n'
            'exec bash\n'
        )

    @staticmethod
    def _materialize_rules_file(config):
        """BUG#3: materialize ctx rules to a per-spawn temp file and
        store path in `config["provider_options"]["rules_file"]`.
        Provider's build_argv may surface this as --read so the LLM
        sees rules from prompt #1 instead of waiting for the periodic
        PTY-feed inject_every threshold."""
        project_dir = config.get("project_dir")
        if not project_dir:
            return
        try:
            from bterminal.ctx.helpers import _resolve_ctx_project_name
            proj = _resolve_ctx_project_name(project_dir)
        except Exception:
            return
        if not proj:
            return
        try:
            result = subprocess.run(
                ["ctx", "rules", "inject", proj],
                capture_output=True, text=True, timeout=5,
            )
            block = result.stdout.strip()
        except Exception:
            return
        if not block:
            return
        # Per-spawn temp file. PID + monotonic time = unique enough.
        import tempfile
        fd, path = tempfile.mkstemp(
            prefix=f"_bt_aider_rules_{os.getpid()}_",
            suffix=".md",
        )
        try:
            with os.fdopen(fd, "w") as f:
                f.write(block)
        except Exception:
            return
        opts = config.setdefault("provider_options", {})
        opts["rules_file"] = path

    @staticmethod
    def _build_spawn_script(provider, config, intro_prompt, askpass_path=None):
        """Pure function: bash -c script for spawning an AI CLI binary.

        argv comes from provider.build_argv(); we shlex-quote each
        element so prompts containing quotes/spaces stay safe. The
        trailing `exec bash` keeps the shell alive when the CLI exits
        (so users can inspect output / open another tool in the same tab).

        Sudo wrapping fires only when (a) the session config requests
        sudo (legacy top-level OR provider_options.sudo) AND (b) the
        provider declares supports_sudo capability.

        BUG#31d: when `askpass_path` is provided (caller resolved it from
        the app-level SudoAskpassCache), use a shared-askpass prologue
        that skips the read-loop. On cache miss (None) or post-cancel
        fallback we revert to the legacy per-tab interactive read-loop.
        """
        TerminalTab._materialize_rules_file(config)
        argv = provider.build_argv(config, intro_prompt)
        if not argv:
            # Provider couldn't build argv (typically: binary missing).
            # Caller should already have routed to the not-found script
            # via find_binary() check; this is a defensive fallback.
            return TerminalTab._build_binary_not_found_script(provider)

        cmd_str = " ".join(shlex.quote(x) for x in argv)
        opts = config.get("provider_options") or config
        if opts.get("sudo") and provider.capabilities.supports_sudo:
            if askpass_path:
                prologue = TerminalTab._build_shared_askpass_prologue(
                    askpass_path
                )
            else:
                prologue = TerminalTab._INTERACTIVE_SUDO_READ_LOOP
            return prologue + f"{cmd_str}\nexec bash\n"
        return f"{cmd_str}\nexec bash\n"

    def _resolve_sudo_askpass_path(self):
        """BUG#31d: pull the shared askpass path from the app-level cache.

        If the cache is empty and the app exposes prompt_sudo_password()
        (task 31c), trigger the modal dialog synchronously; otherwise
        return None to let _build_spawn_script fall back to the per-tab
        read-loop. Defensive against pre-31c app instances missing the
        attribute entirely.
        """
        cache = getattr(self.app, "sudo_askpass", None)
        if cache is None:
            return None
        path = cache.get_path()
        if path:
            return path
        prompt = getattr(self.app, "prompt_sudo_password", None)
        if prompt is None:
            return None
        prompt()
        return cache.get_path()

    def _aider_resolve_model(self, provider, config):
        """Mirror AiderProvider.build_argv's --model resolution chain.

        Priority: per-session opts.model → global default_local_model_for_provider
        → provider.capabilities.default_model → hardcoded fallback.
        """
        opts = config.get("provider_options") or {}
        model = opts.get("model")
        if not model:
            try:
                from bterminal.config import _OPTIONS
                mapping = _OPTIONS.get("default_local_model_for_provider") or {}
                model = mapping.get("aider")
            except Exception:
                model = None
        return (model
                or getattr(provider.capabilities, "default_model", None)
                or "openai/qwen2.5-coder:0.5b")

    def _aider_resolve_missing_model(self, provider, config):
        """Pre-spawn safety net for aider — see BUG#19.

        Returns:
          - original (or amended) `config` dict to proceed with spawn
          - None to abort spawn (user cancelled the dialog)
        """
        from bterminal.providers.aider import (
            is_model_available, list_installed_models,
        )
        model = self._aider_resolve_model(provider, config)
        if is_model_available(model):
            return config

        action, picked = self._aider_show_missing_model_dialog(
            model, list_installed_models())

        if action == "skip":
            return config  # spawn anyway — user accepts raw litellm error
        if action == "pick" and picked:
            new_config = dict(config)
            new_opts = dict(new_config.get("provider_options") or {})
            new_opts["model"] = picked
            new_config["provider_options"] = new_opts
            return new_config
        if action == "wizard":
            # BUG#22: hand off to the CLI wizard in a fresh tab.
            # After it exits, app._on_aider_wizard_done checks the sentinel
            # and (if it matches this session_id) spawns aider with the
            # newly-chosen model — no user click required.
            # idle_add: we're still inside the dialog response handler.
            # Deferring lets the dialog tear down before append_page +
            # set_current_page run, otherwise GTK keeps focus on the
            # (about-to-be-aborted) aider tab.
            GLib.idle_add(self.app.open_aider_wizard_tab, config)
            return None  # current spawn aborts; the wizard takes over
        return None  # cancel / closed

    def _aider_show_missing_model_dialog(self, missing_tag, installed):
        """Show 3-option dialog. Returns (action, picked_model_or_None).

        action ∈ {'wizard', 'pick', 'skip', 'cancel'}
        """
        try:
            from bterminal.config import _
        except Exception:
            def _(s):
                return s

        primary = _("Brakuje modelu lokalnego dla aidera")
        secondary = _(
            "Model '%s' nie jest pobrany w Ollamie. Aider wystartuje, "
            "ale przy pierwszym promptcie wypisze litellm.NotFoundError. "
            "Co chcesz zrobić?"
        ) % missing_tag

        dlg = Gtk.MessageDialog(
            transient_for=self.app,
            modal=True,
            destroy_with_parent=True,
            message_type=Gtk.MessageType.WARNING,
            text=primary,
        )
        dlg.format_secondary_text(secondary)
        dlg.add_button(_("Uruchom wizarda"), 1)
        if installed:
            dlg.add_button(_("Wybierz inny model"), 2)
        dlg.add_button(_("Pomiń (uruchom mimo to)"), 3)
        dlg.add_button(_("Anuluj"), Gtk.ResponseType.CANCEL)
        resp = dlg.run()
        dlg.destroy()

        if resp == 1:
            return ("wizard", None)
        if resp == 2:
            picked = self._aider_pick_installed_model(installed)
            return ("pick", picked) if picked else ("cancel", None)
        if resp == 3:
            return ("skip", None)
        return ("cancel", None)

    def _aider_pick_installed_model(self, installed):
        """Modal combo dialog over `installed`. Returns tag or None."""
        try:
            from bterminal.config import _
        except Exception:
            def _(s):
                return s

        dlg = Gtk.Dialog(
            title=_("Wybierz model lokalny"),
            transient_for=self.app,
            modal=True,
            destroy_with_parent=True,
        )
        dlg.add_buttons(
            _("Anuluj"), Gtk.ResponseType.CANCEL,
            _("OK"), Gtk.ResponseType.OK,
        )
        box = dlg.get_content_area()
        box.set_spacing(8)
        box.set_margin_start(12)
        box.set_margin_end(12)
        box.set_margin_top(12)
        box.set_margin_bottom(12)
        box.pack_start(Gtk.Label(label=_("Dostępne modele Ollama:")), False, False, 0)
        combo = Gtk.ComboBoxText()
        for tag in installed:
            combo.append_text(tag)
        combo.set_active(0)
        box.pack_start(combo, False, False, 0)
        dlg.show_all()
        resp = dlg.run()
        picked = combo.get_active_text() if resp == Gtk.ResponseType.OK else None
        dlg.destroy()
        # Aider expects `openai/<tag>` (litellm prefix), preserve convention.
        if picked and "/" not in picked:
            picked = "openai/" + picked
        return picked

    def spawn_ai_cli(self, config):
        """Spawn an AI CLI session — provider-aware dispatch (T2.1).

        Reads `config["provider"]` (default "claude") and routes to
        the matching AIProvider via the registry. Falls back to a
        Claude session when the field is absent so legacy session
        files keep working.

        Always runs inside bash so the shell survives the CLI's exit
        (the tab doesn't auto-close). Sudo askpass prologue is added
        only when both the session requests it and the provider
        capability supports_sudo is True.

        Raises KeyError if the named provider isn't registered. The
        caller (TerminalTab.__init__) shows a fallback error in the
        terminal rather than crashing the GTK main loop.
        """
        from bterminal.providers import get_registry

        provider_name = config.get("provider", "claude")
        try:
            provider = get_registry().get(provider_name)
        except KeyError:
            # Re-raise with a friendlier message so callers can decide
            # whether to surface in-terminal or in the GTK statusbar.
            raise

        binary = provider.find_binary()
        work_dir = config.get("project_dir") or os.environ.get("HOME", "/")

        # BUG#19: aider pre-spawn check — without an installed Ollama model
        # the user would see a raw `litellm.NotFoundError` in VTE.
        if binary and provider_name == "aider":
            config = self._aider_resolve_missing_model(provider, config)
            if config is None:
                return  # user picked Anuluj / closed dialog

        intro_prompt = ""
        if not binary:
            script = self._build_binary_not_found_script(provider)
        else:
            from bterminal.helpers import _compute_intro_prompt_for_tab
            intro_prompt = _compute_intro_prompt_for_tab(self.app, self) or ""
            from bterminal.debug_rest import record_feed
            record_feed("intro_prompt", intro_prompt.encode())
            # BUG#31d: resolve shared askpass path before spawn so the
            # modal dialog (if needed) runs synchronously on the main
            # loop before terminal.spawn_async kicks in.
            opts = config.get("provider_options") or config
            askpass_path = None
            if opts.get("sudo") and provider.capabilities.supports_sudo:
                askpass_path = self._resolve_sudo_askpass_path()
            script = self._build_spawn_script(
                provider, config, intro_prompt, askpass_path=askpass_path,
            )

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

        # BUG#27: providers that can't take intro_prompt via argv (aider —
        # no --message-init) deliver it through the PTY after a delay.
        # Default base-class implementation is a no-op, so Claude/Copilot
        # paths are unchanged.
        if binary and intro_prompt:
            provider.inject_intro_prompt(self.terminal, intro_prompt)

    # T4.6.1 (2026-05-07): the `spawn_claude` legacy alias was removed.
    # Callers must use spawn_ai_cli(config) directly. Sessions whose
    # config lacks a `provider` key still default to "claude" inside
    # spawn_ai_cli — backward-compat for legacy session data.

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

        # Typing guard: update _user_is_typing before any shortcut handling.
        # This prevents rules injection from firing while the user has
        # uncommitted text in the input line (pauses mid-sentence > 2 s).
        self._user_is_typing = self._typing_state_after_key(
            self._user_is_typing,
            event.keyval,
            bool(mod & ctrl),
        )

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
            if self.ai_config:
                self.app.toggle_git_panel()
            return True

        # Track Enter key for prompt counter (Claude Code sessions)
        if self._stats_bar and event.keyval in (Gdk.KEY_Return, Gdk.KEY_KP_Enter):
            self._stats_bar.increment_prompt()
            self._maybe_inject_rules()

        return False

    def _maybe_inject_rules(self):
        """After each prompt, check if it's time to inject rules or refresh CTX.

        Sets a pending flag — actual injection happens after the AI CLI
        goes idle, so the message arrives at the next free prompt
        (not mid-processing).

        T3.7: capability gate — providers without `rules_inject` skip
        the entire flow. Both Claude and Copilot opt in by default
        (PTY feed_child works identically); future providers can opt
        out via `providers.json` override.
        """
        if not self._stats_bar or not self.ai_config:
            return
        from bterminal.providers import get_registry
        if not should_inject_rules(self.ai_config, get_registry()):
            return
        project_dir = self.ai_config.get("project_dir", "")
        if not project_dir:
            return
        project = self._task_project or os.path.basename(project_dir.rstrip("/"))
        count = self._stats_bar._prompt_count

        from bterminal.providers.ctx_defaults import (
            DEFAULT_INJECT_EVERY, DEFAULT_REFRESH_EVERY,
        )
        inject_every = DEFAULT_INJECT_EVERY
        refresh_every = DEFAULT_REFRESH_EVERY
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
            # Preserve refresh boundary: if a pending injection already exists,
            # don't overwrite it. The earliest pending wins so a refresh
            # boundary (count % refresh_every == 0) can't be lost by a
            # subsequent prompt that lands on a non-refresh boundary.
            if self._inject_pending is not None:
                return
            self._inject_pending = (project, count, refresh_every)
            self._inject_pending_ts = time.monotonic()
            # Ensure poll loop is running (if VTE happens to be quiet right now,
            # contents-changed won't fire to start it).
            if self._task_idle_timer is None:
                self._last_content_change = time.monotonic()
                self._task_idle_timer = GLib.timeout_add_seconds(
                    self._IDLE_POLL_SEC, self._idle_check_tick
                )
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

        # #93: use the shared extract_rules_inject_bytes helper so the
        # bytes flowing into VTE here are identical to what the parity
        # test asserts. Provider name pulled from ai_config for the
        # documentation contract — the helper is required to ignore it.
        provider_name = (self.ai_config or {}).get("provider", "claude")
        rules_bytes = extract_rules_inject_bytes(
            provider_name, project, project_block)

        import datetime
        with open("/tmp/bterminal_inject.log", "a") as f:
            f.write(f"{datetime.datetime.now()}: injecting {len(project_block)} chars (rules) for {project}\n")
        from bterminal.debug_rest import record_feed
        record_feed("rules_inject", rules_bytes)
        self.terminal.feed_child(rules_bytes)
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
            f"=== project context refresh [{project}] ===\n\n"
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
            elif self.ai_config:
                # Claude Code tab: keep decorated tab name with number + emoji
                display = getattr(self, "_claude_tab_display", self.ai_config.get("name", "Claude Code"))
                self.app.update_tab_title(self, display)
            else:
                self.app.update_tab_title(self, title)

    # Adaptive idle thresholds — see _idle_check_tick docstring for rationale.
    _IDLE_QUIET_SEC = 2.0
    _IDLE_HARD_CAP_SEC = 60.0
    _IDLE_POLL_SEC = 1

    def _on_contents_changed_tasks(self, terminal):
        """Record monotonic ts of last VTE content change.

        Previously this re-armed a fixed 10s GLib timer on every byte from
        the AI CLI. Spinner ticks (~1Hz) and streaming response chunks
        (every few ms) reset the timer continuously — so 10s of full
        silence was rarely reached during an active conversation, and
        pending injections waited minutes for a long pause.

        New approach: just timestamp the change and let `_idle_check_tick`
        (polled every 1s) decide when to fire. Polling starts lazily —
        only when there's actually something pending or task autorun
        is armed.
        """
        self._last_content_change = time.monotonic()
        if self._task_idle_timer is None and (self._inject_pending or self._task_project):
            self._task_idle_timer = GLib.timeout_add_seconds(
                self._IDLE_POLL_SEC, self._idle_check_tick
            )

    def _idle_check_tick(self):
        """Polled every 1s while pending / autorun is armed.

        Fires `_on_task_idle_timeout` when EITHER:
          - >= 2s since last content change (Claude likely awaiting input)
          - >= 60s since pending was set (hard cap — force-fire even if VTE
            is still streaming, e.g. a never-ending agent loop)

        Returns True to keep polling, False to stop.
        """
        # Never inject while the user has uncommitted text in the input line.
        # The poll loop continues so we fire as soon as they press Enter.
        if self._user_is_typing:
            return True

        now = time.monotonic()
        quiet_for = now - self._last_content_change
        pending_age = (now - self._inject_pending_ts) if self._inject_pending else 0.0

        should_fire = quiet_for >= self._IDLE_QUIET_SEC or pending_age >= self._IDLE_HARD_CAP_SEC
        if not should_fire:
            return True

        self._task_idle_timer = None
        self._on_task_idle_timeout()
        return False

    def _on_task_idle_timeout(self):
        """Called when the AI CLI has been idle for ~10s — fire pending
        rule injection or auto-trigger the next task.

        T3.6: capability dispatch — providers with
        `task_auto_trigger=false` (Copilot at T3 baseline) skip the
        auto-trigger flow entirely. Rules injection runs regardless
        because it's gated separately by `_inject_pending` + the
        `rules_inject` capability (T3.7).
        """
        self._task_idle_timer = None

        # Inject rules if due (only when no task is about to fire).
        # Gated by T3.7's rules_inject capability inside _do_inject_rules.
        if self._inject_pending:
            project, count, refresh_every = self._inject_pending
            self._do_inject_rules(project, count, refresh_every)
            return False

        # T3.6: capability gate — skip auto-trigger for non-supporting
        # providers. Pure-helper `should_run_auto_trigger` is exported
        # for unit tests without GTK.
        from bterminal.providers import get_registry
        if not should_run_auto_trigger(self.ai_config, get_registry()):
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
                f"[AUTO-TRIGGER] Your assigned task: {task['task_id']} — {task['description']}\n"
                f"Check the full list: tasks context {self._task_project} --session {self._task_session_id}\n"
                f"You MUST mark it after completion: tasks done {self._task_project} {task['task_id']} (in Bash). "
                f"The auto-trigger loop only ends when ALL tasks are closed (done). "
                f"If you do not mark it — this message will keep repeating."
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
        """Atomically find and claim the next open unclaimed task. Returns task dict or None.

        #109 / audit § 6.2 #10: wraps the SELECT-then-INSERT window
        in `BEGIN IMMEDIATE` so concurrent callers serialize at the
        SQL layer. Pre-#109, two threads' SELECTs both saw the same
        task as 'unclaimed' and both INSERT'd per-session rows
        (PRIMARY KEY shape allows it), producing duplicate claims
        for the same task.

        With BEGIN IMMEDIATE, the second caller either waits for the
        first's COMMIT (then sees the claim and skips the task) or
        hits SQLITE_BUSY → OperationalError (caught upstream by
        _on_task_idle_timeout's bare except).
        """
        # If the connection is already inside a transaction (e.g.
        # caller seeded an INSERT first), BEGIN IMMEDIATE raises
        # OperationalError — the existing implicit txn already
        # provides serialization, so we fall through silently.
        try:
            db.execute("BEGIN IMMEDIATE")
        except sqlite3.OperationalError:
            pass

        # First check if this session already has a claimed open task
        existing = db.execute(
            """SELECT t.task_id, t.description FROM tasks t
               JOIN task_claims c ON c.project = t.project AND c.task_id = t.task_id
               WHERE t.project = ? AND c.session_id = ? AND t.status = 'open'
               ORDER BY t.task_id LIMIT 1""",
            (project, session_id),
        ).fetchone()
        if existing:
            db.commit()
            return existing

        # Find next open task not claimed by anyone
        rows = db.execute(
            """SELECT t.task_id, t.description FROM tasks t
               LEFT JOIN task_claims c ON c.project = t.project AND c.task_id = t.task_id
               WHERE t.project = ? AND t.status = 'open' AND c.task_id IS NULL""",
            (project,),
        ).fetchall()
        if not rows:
            db.commit()
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
            db.rollback()
            return None

    @staticmethod
    def _typing_state_after_key(is_typing: bool, keyval: int, has_ctrl: bool) -> bool:
        """Pure typing-guard state machine — testable without GTK.

        Returns the updated _user_is_typing value after processing a key event.
        Enter / Escape clear the flag (message submitted or line cancelled).
        Ctrl+C / Ctrl+D clear it (interrupt / EOF — not composing any more).
        Pure modifier keys leave the flag unchanged.
        Everything else sets it to True (user is composing).
        """
        if keyval in _TYPING_SUBMIT_KEYS:
            return False
        if has_ctrl and keyval in _TYPING_CTRL_ABORT_KEYS:
            return False
        if keyval not in _TYPING_PURE_MODIFIER_KEYS:
            return True
        return is_typing

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
        if self.ai_config:
            proj_dir = self.ai_config.get("project_dir", "")
            if proj_dir and os.path.isdir(proj_dir):
                base_dir = proj_dir
        if not base_dir:
            base_dir = os.path.expanduser("~")
        images_dir = os.path.join(base_dir, "copied_images")
        os.makedirs(images_dir, exist_ok=True)
        filename = f"{uuid.uuid4().hex[:12]}.png"
        dest = os.path.join(images_dir, filename)
        pixbuf.savev(dest, "png", [], [])

        # Task #69 (2026-05-07): provider-aware vision hint. AI tabs
        # whose provider sets `argv.image_paste_template` get a
        # wrapped hint instead of the bare path so the model
        # deterministically calls Read on the image (Copilot needs
        # this; Claude doesn't, so its template is null).
        paste_text = self._format_image_paste_for_provider(dest)
        clipboard.set_text(paste_text, -1)
        clipboard.store()
        self.terminal.paste_clipboard()
        # Also register in ctx if available
        project = self._detect_ctx_project()
        if project:
            _save_ctx_image(project, dest, original_name="clipboard.png")
            if hasattr(self.app, "ctx_panel"):
                self.app.ctx_panel.refresh()
        return True

    def _format_image_paste_for_provider(self, image_path: str) -> str:
        """Resolve the highest-priority image-paste template available
        for this tab and format `image_path` with it.

        Priority chain (first match wins):
          1. Session override (#71) — non-empty
             `ai_config.provider_options.image_paste_template`. User
             set a custom phrasing in AISessionDialog; takes precedence
             over EVERYTHING (even when the global toggle is off, the
             session-level explicit choice wins).
          2. Tab has no ai_config (SSH / local) → bare path.
          3. Global toggle `image_paste_hint_enabled` (#70) is False
             → bare path. Kill-switch from Options.
          4. Provider unknown (forward-compat) → bare path.
          5. Provider's `argv.image_paste_template` (#69) — null/empty
             → bare path; non-empty → format with `{path}`.
        """
        from bterminal.helpers import format_image_paste_hint

        # 1. Session-level override: highest priority. Empty string
        # is treated as "no override" (lets users clear the Entry to
        # fall back to provider default without deleting the JSON key).
        if self.ai_config:
            opts = self.ai_config.get("provider_options") or {}
            session_template = opts.get("image_paste_template")
            if session_template:
                return format_image_paste_hint(session_template, image_path)

        # 2. SSH / local tabs — no provider, no template.
        if not self.ai_config:
            return image_path

        # 3. Global kill-switch.
        from bterminal.config import _OPTIONS
        if not _OPTIONS.get("image_paste_hint_enabled", True):
            return image_path

        # 4. Provider lookup (forward-compat).
        from bterminal.providers import get_registry
        try:
            provider = get_registry().get(
                self.ai_config.get("provider", "claude"))
        except (KeyError, AttributeError):
            return image_path

        # 5. Provider default.
        template = (provider._argv_spec or {}).get("image_paste_template")
        return format_image_paste_hint(template, image_path)

    def _detect_ctx_project(self):
        """Auto-detect ctx project from tab config, or ask user."""
        if not os.path.exists(CTX_DB):
            return None
        # Try auto-detect from claude config
        if self.ai_config:
            proj_dir = self.ai_config.get("project_dir", "")
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
        if self.ai_config:
            proj_dir = self.ai_config.get("project_dir", "")
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
        if self.ai_config:
            proj_dir = self.ai_config.get("project_dir", "")
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
        if self.ai_config:
            return getattr(self, "_claude_tab_display", self.ai_config.get("name", "Claude Code"))
        if self.session:
            return self.session.get("name", "SSH")
        return "Terminal"

