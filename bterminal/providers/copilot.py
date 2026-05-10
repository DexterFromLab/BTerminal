"""bterminal.providers.copilot — GitHub Copilot CLI provider skeleton.

T1.4 baseline: thin wrapper around defaults.json[providers.copilot] with
capabilities mostly = false. Real argv builder lands in T2.3, log
parser in T3.2, idle detection in T4.1, etc. The skeleton lets
T1.5 (ProviderRegistry) and T1.7 (migration) reference Copilot
without those subsequent tasks being blockers.

Per docs/cli-provider-abstraction-implementation-plan.md task T1.4.

GitHub Copilot CLI 2026 — agentic terminal AI. Public preview Sep 2025,
GA Feb 2026. Subscription-gated (Copilot Pro / Pro+ / Business / Enterprise).
Binary: `copilot` (npm @github/copilot, brew copilot-cli, winget GitHub.Copilot).
"""
from __future__ import annotations

import glob
import json
import os
import shutil
import threading
import time
from datetime import datetime
from typing import Any, Callable, Optional

from bterminal.providers.base import (
    AIProvider,
    ProviderCapabilities,
    ProviderDisplay,
    SessionStats,
)


# T4.1: Idle detection state machine.
#
# We watch events.jsonl and decide when the agent has stopped. Three
# event categories drive the decision:
#   - session.shutdown        → idle=True forever (permanent stop).
#   - tool.execution_complete → idle=True after `timeout_s` of quiet.
#   - tool.execution_start    → idle=False (work in progress).
# Anything else (session.start, prompt.user, prompt.assistant, etc.)
# updates last_event_ts but doesn't toggle the state directly.
#
# TODO(L2): once a Copilot subscription is available, capture a real
# session's events.jsonl and confirm the terminal-event names below
# (some early Copilot builds may emit different terminal events;
# adjust _IDLE_TERMINAL_TYPES if the real schema differs).
_IDLE_TERMINAL_TYPES = ("tool.execution_complete",)
_IDLE_PERMANENT_TYPES = ("session.shutdown",)
_IDLE_ACTIVE_TYPES = ("tool.execution_start",)


def evaluate_idle_state(
    events_log_lines: list[str],
    current_time: float,
    timeout_s: float = 10.0,
) -> dict:
    """Pure helper: decide whether the session is idle given the full
    events.jsonl contents and the current monotonic-clock reading.

    Returns dict with keys:
        idle (bool):              caller should fire its callback.
        reason (str):              "shutdown" | "quiet_after_complete" |
                                   "active" | "no_events" | "warming_up".
        last_event_type (str):     type of most recent valid event ("" if none).
        last_event_ts_iso (str):   ISO timestamp string of that event ("" if none).
        permanent (bool):          if True, idle is terminal — caller
                                   should stop the monitor (session.shutdown).
    """
    last_type = ""
    last_ts_iso = ""
    last_ts_epoch: Optional[float] = None
    for line in events_log_lines:
        line = line.strip()
        if not line:
            continue
        try:
            event = json.loads(line)
        except (json.JSONDecodeError, ValueError):
            # Malformed line — skip but preserve accumulated state.
            continue
        if not isinstance(event, dict):
            continue
        ev_type = event.get("type", "")
        if not isinstance(ev_type, str) or not ev_type:
            continue
        last_type = ev_type
        ts_str = event.get("timestamp", "")
        if isinstance(ts_str, str) and ts_str:
            last_ts_iso = ts_str
            try:
                dt = datetime.fromisoformat(ts_str.replace("Z", "+00:00"))
                last_ts_epoch = dt.timestamp()
            except (ValueError, TypeError):
                pass

    if not last_type:
        return {
            "idle": False, "reason": "no_events",
            "last_event_type": "", "last_event_ts_iso": "",
            "permanent": False,
        }

    if last_type in _IDLE_PERMANENT_TYPES:
        return {
            "idle": True, "reason": "shutdown",
            "last_event_type": last_type,
            "last_event_ts_iso": last_ts_iso,
            "permanent": True,
        }

    if last_type in _IDLE_ACTIVE_TYPES:
        return {
            "idle": False, "reason": "active",
            "last_event_type": last_type,
            "last_event_ts_iso": last_ts_iso,
            "permanent": False,
        }

    if last_type in _IDLE_TERMINAL_TYPES:
        if last_ts_epoch is None:
            # No timestamp on the complete event — best-effort idle.
            return {
                "idle": True, "reason": "quiet_after_complete",
                "last_event_type": last_type,
                "last_event_ts_iso": last_ts_iso,
                "permanent": False,
            }
        elapsed = current_time - last_ts_epoch
        if elapsed >= timeout_s:
            return {
                "idle": True, "reason": "quiet_after_complete",
                "last_event_type": last_type,
                "last_event_ts_iso": last_ts_iso,
                "permanent": False,
            }
        return {
            "idle": False, "reason": "warming_up",
            "last_event_type": last_type,
            "last_event_ts_iso": last_ts_iso,
            "permanent": False,
        }

    # Unknown event type (e.g. prompt.user) — treat as active.
    return {
        "idle": False, "reason": "active",
        "last_event_type": last_type,
        "last_event_ts_iso": last_ts_iso,
        "permanent": False,
    }


class _CopilotIdleMonitor:
    """Background-thread idle watcher for one Copilot session.

    Polls events.jsonl every `poll_interval_s` and runs
    `evaluate_idle_state`. Fires `on_idle_callback(state)` once per
    transition into idle (latched until session goes active again).
    Pass `signal_via=GLib.idle_add` from the GTK side so the callback
    runs on the main loop instead of the watcher thread.

    Lifecycle:
        monitor = _CopilotIdleMonitor(events_path, callback)
        monitor.start()
        ...
        monitor.stop()
    """

    def __init__(
        self,
        events_path: str,
        on_idle_callback: Callable[[dict], None],
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.5,
        signal_via: Optional[Callable[[Callable, Any], Any]] = None,
        clock: Callable[[], float] = time.time,
    ):
        self._events_path = events_path
        self._on_idle = on_idle_callback
        self._timeout_s = timeout_s
        self._poll_interval_s = poll_interval_s
        self._signal_via = signal_via
        self._clock = clock
        self._stop = threading.Event()
        self._thread: Optional[threading.Thread] = None
        # Latch: prevents repeated callbacks while the session stays
        # idle. Resets when an active event arrives.
        self._signaled = False

    def start(self) -> None:
        if self._thread is not None and self._thread.is_alive():
            return
        self._stop.clear()
        self._thread = threading.Thread(
            target=self._run, daemon=True,
            name="copilot-idle-monitor",
        )
        self._thread.start()

    def stop(self, join_timeout: float = 1.0) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=join_timeout)

    def is_running(self) -> bool:
        return self._thread is not None and self._thread.is_alive()

    def poll_once(self) -> dict:
        """Run one evaluation cycle synchronously — testable without
        starting the thread. Returns the evaluator's output dict."""
        lines: list[str] = []
        try:
            if os.path.isfile(self._events_path):
                with open(self._events_path, encoding="utf-8",
                          errors="replace") as fh:
                    lines = fh.read().splitlines()
        except OSError:
            lines = []
        state = evaluate_idle_state(
            lines, self._clock(), self._timeout_s,
        )
        if state["idle"] and not self._signaled:
            self._signaled = True
            self._dispatch_callback(state)
        elif not state["idle"]:
            # Active again — allow next idle to re-fire.
            self._signaled = False
        return state

    def _dispatch_callback(self, state: dict) -> None:
        if self._signal_via is not None:
            # Mirror GLib.idle_add(callable, arg) shape.
            self._signal_via(self._on_idle, state)
        else:
            self._on_idle(state)

    def _run(self) -> None:
        while not self._stop.is_set():
            try:
                state = self.poll_once()
                if state.get("permanent"):
                    return
            except Exception:
                # Daemon thread mustn't crash on transient errors
                # (file disappearing mid-read, decode glitches).
                pass
            self._stop.wait(self._poll_interval_s)


class CopilotProvider(AIProvider):
    """GitHub Copilot CLI provider — T1.4 skeleton.

    All the heavy lifting (argv with --no-banner/--no-mouse/--plain-diff
    + -p/-i intro modes, events.jsonl parsing, tail-f idle detection)
    is deferred to later tasks. This class exists so the registry +
    session migration can refer to provider="copilot" today.
    """

    name = "copilot"

    def __init__(self, config: dict):
        self._config = config
        self.display = ProviderDisplay(**config["display"])
        self.capabilities = ProviderCapabilities(**config["capabilities"])
        self.pricing = config.get("pricing", {})
        self._argv_spec = config.get("argv", {})
        self._binary_spec = config.get("binary", {})
        self._auth_spec = config.get("auth", {})

    # ─── Binary lookup (works today) ────────────────────────────────────────

    def find_binary(self) -> Optional[str]:
        """Locate `copilot` binary across configured paths + PATH fallback.

        Same approach as Claude: configured search_paths first (with
        glob expansion), then shutil.which on augmented PATH so GUI
        launches missing ~/.npm-global/bin still resolve.
        """
        for entry in self._binary_spec.get("search_paths", []):
            expanded = os.path.expanduser(entry)
            if "*" in expanded:
                for p in sorted(glob.glob(expanded), reverse=True):
                    if os.path.isfile(p) and os.access(p, os.X_OK):
                        return p
            elif os.path.isfile(expanded) and os.access(expanded, os.X_OK):
                return expanded
        extra = os.pathsep.join([
            os.path.expanduser("~/.npm-global/bin"),
            os.path.expanduser("~/.local/bin"),
        ])
        env_path = os.environ.get("PATH", "") + os.pathsep + extra
        return shutil.which(self.name, path=env_path)

    # ─── argv (T2.3 will flesh this out) ────────────────────────────────────

    def build_argv(self, config: dict, intro_prompt: str) -> list[str]:
        """Build argv list for spawn (first element = binary path).

        Always prepends `tui_safe` flags (--no-banner --no-mouse
        --plain-diff --no-color) — without these, Copilot's TUI uses
        alt-screen + mouse codes that VTE renders poorly. The rest is
        capability-gated so future-version configs that disable a
        feature gracefully drop the flag rather than emitting it
        anyway.

        config: session config dict. Reads `provider_options` (R4.2
                schema) first; falls back to top-level keys for
                backward-compat with legacy session entries.
        intro_prompt: pre-rendered intro text (may be empty).

        Resume note: pre-T4.5 we pass `--resume` without an explicit
        session UUID — Copilot then opens its own session picker. T4.5
        adds the SQLite session-store reader so we can pass the exact
        UUID the user picked in BTerminal's sidebar.
        """
        binary = self.find_binary()
        if not binary:
            return []

        argv: list[str] = [binary]
        opts = config.get("provider_options") or config

        # Always: TUI-safe flags so Copilot's output is renderable in VTE.
        argv.extend(self._argv_spec.get(
            "tui_safe", ["--no-banner", "--no-mouse", "--plain-diff"]))

        # Resume / continue (mutually exclusive — resume wins if both)
        if opts.get("resume") and self.capabilities.resume_flag:
            argv.extend(self._argv_spec.get("resume", ["--resume"]))
        elif opts.get("continue") and self.capabilities.continue_flag:
            argv.extend(self._argv_spec.get("continue", ["--continue"]))

        # Yolo / skip permissions (Copilot equivalent: --yolo / --allow-all)
        if opts.get("skip_permissions") and self.capabilities.skip_permissions:
            argv.extend(self._argv_spec.get("yolo", ["--yolo"]))

        # Model override (claude-sonnet-4-5 default per defaults.json)
        model = opts.get("model")
        if model:
            template = self._argv_spec.get("model", ["--model", "{model}"])
            argv.extend(s.format(model=model) for s in template)

        # Project dir → --add-dir (allowed paths + cwd hint)
        project_dir = config.get("project_dir")
        if project_dir:
            argv.extend(["--add-dir", project_dir])

        # JSON output mode — used by T4.1 idle detection that parses
        # events.jsonl semantics off stdout. Default: off (TUI mode).
        if opts.get("json_output"):
            argv.extend(self._argv_spec.get(
                "output_format_json", ["--output-format", "json"]))

        # T4.4: plan mode — Copilot starts in read-only planning mode
        # (Shift+Tab toggles same thing in TUI). Capability-gated so
        # future configs can disable the UI knob without code change.
        if opts.get("plan_mode") and self.capabilities.plan_mode:
            argv.extend(self._argv_spec.get("plan", ["--plan"]))

        # Task #68 (2026-05-07): scrollback_friendly + --screen-reader
        # was added in #66 hoping it'd disable alt-screen. Confirmed
        # via TERM-matrix testing on real copilot v1.0.43: the alt-
        # screen escape (\x1b[?1049h) is hardcoded irrespective of
        # TERM or --screen-reader. The checkbox was removed (false
        # promise). Alt-screen scrollback is a fundamental VT100
        # limitation — see README "Known limitations".

        # Intro prompt — interactive (-i) by default, or headless (-p)
        # when the session config requests it. Gated on capabilities.
        if intro_prompt and self.capabilities.intro_prompt:
            if opts.get("headless"):
                flag = self._argv_spec.get("intro_prompt_flag", "-p")
            else:
                flag = self._argv_spec.get("intro_prompt_flag_interactive", "-i")
            argv.extend([flag, intro_prompt])

        return argv

    # ─── Session log / stats (T3.2 will implement) ──────────────────────────

    def session_log_glob(self, project_dir: str) -> Optional[str]:
        """Return None until T3.2 enables session_log capability.

        When capability flips True, this returns
        `~/.copilot/session-state/*/events.jsonl` (per docs/chronicle).
        """
        if not self.capabilities.session_log:
            return None
        template = self.capabilities.session_log_path
        if not template:
            return None
        path = template.format(session_id="*")
        return os.path.expanduser(path)

    def parse_session_stats(self, log_path: str) -> SessionStats:
        """Empty stats — T3.2 implements `events.jsonl` parser.

        Real impl will iterate `events.jsonl`, accumulate tokens from
        each `tool.execution_complete`, and read final cost from
        `session.shutdown.modelMetrics.*.requests.cost`.
        """
        return SessionStats()

    # ─── Plan usage — Copilot has no public API ─────────────────────────────

    def fetch_plan_usage(self) -> Optional[dict]:
        """Always None.

        GitHub Copilot has no public plan-usage endpoint analogous to
        Claude's /api/oauth/usage. The interactive `/usage` slash
        command shows it in-session, but isn't programmatically
        accessible to a wrapper. capabilities.usage_api stays False.
        """
        return None

    # ─── Dialog schema (T2.6) ──────────────────────────────────────────────

    def get_dialog_schema(self) -> list[tuple]:
        """Copilot-specific fields rendered in AISessionDialog.

        skip_permissions → --yolo (vs Claude's --dangerously-skip-permissions).
        plan_mode → --plan (T4.4, read-only planning).
        allowed_tools (T4.3) → textarea, one rule per line, rendered
        only when granular_permissions capability is True.

        Task #54 (2026-05-07): the previously-rendered Model combo was
        removed because hardcoding model names in UI quickly diverges
        from the actual model fleet (Sonnet 4.5 → 4.6 → 4.7 in months).
        Spawn argv still respects an explicit `provider_options.model`
        in saved session config (legacy backcompat) but new sessions
        let the CLI's own default pick. Switch models at runtime via
        the native `/model <name>` slash command inside the Copilot
        TUI session.
        """
        schema: list[tuple] = [
            ("skip_permissions", "checkbox",
             "Yolo mode (--yolo / --allow-all)"),
        ]
        if self.capabilities.plan_mode:
            schema.append((
                "plan_mode", "checkbox",
                "Start in plan mode (--plan, read-only planning)",
            ))
        if self.capabilities.granular_permissions:
            # 4-tuple: (key, type, label, placeholder/helptext).
            schema.append((
                "allowed_tools", "textarea",
                "Allowed tools (one rule per line):",
                "shell(rm)\nshell(curl)\nMy-MCP-Server\n# rules per line",
            ))
        # Task #71 (2026-05-07): per-session override for the image
        # paste template (#69 default). Empty Entry → falls back to
        # provider default; non-empty → wraps pasted image paths
        # with the user's custom phrasing. Placeholder shows the
        # current default so users see what they're overriding.
        default_template = (self._argv_spec or {}).get(
            "image_paste_template") or ""
        schema.append((
            "image_paste_template", "text",
            "Image paste template (optional):",
            default_template,
        ))
        return schema

    # ─── Idle detection (T4.1) ──────────────────────────────────────────────

    def detect_idle(
        self,
        terminal: Any,
        session_id: Optional[str],
        timeout_s: float = 10.0,
    ) -> bool:
        """Synchronous best-effort: check the current events.jsonl once
        and report whether the session looks idle.

        For continuous monitoring (the auto-trigger path), prefer
        `create_idle_monitor()` — it spawns a background thread and
        signals via GLib.idle_add so the callback runs on the GTK main
        loop. T4.2 wires that into terminal_tab's task auto-trigger.
        """
        events_path = self._resolve_events_path(session_id)
        if not events_path or not os.path.isfile(events_path):
            return True  # nothing to inspect → assume nothing in flight
        try:
            with open(events_path, encoding="utf-8", errors="replace") as fh:
                lines = fh.read().splitlines()
        except OSError:
            return True
        state = evaluate_idle_state(lines, time.time(), timeout_s)
        return bool(state["idle"])

    def create_idle_monitor(
        self,
        events_path: str,
        on_idle_callback: Callable[[dict], None],
        timeout_s: float = 10.0,
        poll_interval_s: float = 0.5,
        signal_via: Optional[Callable[[Callable, Any], Any]] = None,
    ) -> "_CopilotIdleMonitor":
        """Factory: build a `_CopilotIdleMonitor` for `events_path`.

        Caller starts/stops it — Copilot session lifecycle owns the
        monitor (terminal_tab's auto-trigger path in T4.2).
        """
        return _CopilotIdleMonitor(
            events_path=events_path,
            on_idle_callback=on_idle_callback,
            timeout_s=timeout_s,
            poll_interval_s=poll_interval_s,
            signal_via=signal_via,
        )

    def _resolve_events_path(self, session_id: Optional[str]) -> Optional[str]:
        """Map a session_id to its events.jsonl path. Without an id,
        fall back to the newest session-state directory (matches
        CopilotStatsReader's behavior)."""
        template = self.capabilities.session_log_path
        if not template:
            return None
        if session_id:
            return os.path.expanduser(
                template.format(session_id=session_id),
            )
        # Newest available — same heuristic as CopilotStatsReader.
        pattern = template.format(session_id="*")
        files = glob.glob(os.path.expanduser(pattern))
        if not files:
            return None
        return max(files, key=os.path.getmtime)


__all__ = [
    "CopilotProvider",
    "_CopilotIdleMonitor",
    "evaluate_idle_state",
    "_IDLE_TERMINAL_TYPES",
    "_IDLE_PERMANENT_TYPES",
    "_IDLE_ACTIVE_TYPES",
]
