"""BTerminal debug REST API.

Off by default; enabled via --debug-rest CLI flag or BTERMINAL_DEBUG_REST=1.

Provides a loopback-only HTTP server that exposes BTerminal's internal
state for self-testing (smoke + action-graph + random-walk explorer).
Bearer-token auth, append-only audit log, idle watchdog, and a regex-
based router with separate GET/POST/PUT tables.

All mutations cross from the REST handler thread to the GTK main thread
via _via_glib_idle, so route bodies never touch GTK widgets directly.

Currently flat at repo root next to bterminal.py — collapses into the
target `bterminal/` package together with config.py + models.py in a
later migration etap.
"""

import http.server
import json
import os
import queue
import re
import secrets
import sys
import tempfile
import threading
import time
import urllib.error
import urllib.parse
import urllib.request

import gi
gi.require_version("Gdk", "3.0")
gi.require_version("Gtk", "3.0")
from gi.repository import Gdk, GLib

from bterminal.config import (
    APP_VERSION,
    CONFIG_DIR,
    PLUGINS_CONFIG_FILE,
    PLUGINS_DIR,
    _OPTIONS,
)


# ─── Configuration ────────────────────────────────────────────────────────────
# All three timing knobs accept env overrides so tests (and ad-hoc smokes) can
# co-exist with a session-scoped instance on a different port and shorten the
# idle window from 30 min to a few seconds.

DEBUG_REST_PORT = int(os.environ.get("BTERMINAL_DEBUG_REST_PORT", "7780"))
DEBUG_TOKEN_FILE = os.path.join(CONFIG_DIR, "debug_token")
DEBUG_PID_FILE = os.path.join(CONFIG_DIR, "debug_pid")
DEBUG_LOG_DIR = os.path.expanduser("~/.cache/bterminal")
DEBUG_LOG_FILE = os.path.join(DEBUG_LOG_DIR, "debug-rest.log")
DEBUG_IDLE_TIMEOUT_SEC = int(os.environ.get("BTERMINAL_DEBUG_IDLE_TIMEOUT", "1800"))
DEBUG_IDLE_CHECK_SEC = int(os.environ.get("BTERMINAL_DEBUG_IDLE_CHECK", "60"))
DEBUG_LOG_MAX_BYTES = 10 * 1024 * 1024  # 10 MB → rotate
DEBUG_KEY_WHITELIST = {
    "enter", "tab", "escape", "ctrl-c", "ctrl-d",
    "up", "down", "left", "right", "backspace", "space",
}
DEBUG_KEY_BYTES = {
    "enter":     b"\n",
    "tab":       b"\t",
    "escape":    b"\x1b",
    "ctrl-c":    b"\x03",
    "ctrl-d":    b"\x04",
    "up":        b"\x1b[A",
    "down":      b"\x1b[B",
    "left":      b"\x1b[D",
    "right":     b"\x1b[C",
    "backspace": b"\x7f",
    "space":     b" ",
}
DEBUG_FEED_MAX_BYTES = 64 * 1024
DEBUG_REST_ENABLED = False  # set by bterminal.main() based on CLI flag / env

_audit_log_lock = threading.Lock()


# ─── Feed capture (testing) ─────────────────────────────────────────────────
# Cooperative capture of bytes BTerminal sends to AI CLI subprocess.
# When DEBUG_REST_ENABLED, sites of interest (intro prompt, auto-trigger
# message, rules injection) explicitly call record_feed(label, payload).
# Tests fetch via GET /api/debug/feed_log and assert structure of bytes.

_feed_log: list = []                 # list of {ts, label, tab_idx, bytes_b64}
_feed_log_lock = threading.Lock()
_FEED_LOG_MAX = 1000                 # circular cap


def record_feed(label: str, payload: bytes, tab_idx: int = -1) -> None:
    """Record outgoing bytes to AI CLI subprocess. No-op when debug REST off.

    `label` identifies the call site:
      - "intro_prompt" — initial prompt fed to spawn'd Claude/Aider/etc.
      - "auto_trigger" — task auto-trigger [AUTO-TRIGGER] message
      - "rules_inject" — periodic rules re-injection block
      - "ctx_refresh" — ctx refresh follow-up message
      - "macro" — SSH macro step
    `tab_idx` is the notebook page index (or -1 if pre-pack).
    """
    if not DEBUG_REST_ENABLED:
        return
    import base64
    with _feed_log_lock:
        _feed_log.append({
            "ts": time.time(),
            "label": label,
            "tab_idx": tab_idx,
            "bytes_b64": base64.b64encode(payload).decode("ascii"),
        })
        while len(_feed_log) > _FEED_LOG_MAX:
            _feed_log.pop(0)


# ─── Token + audit log ────────────────────────────────────────────────────────

def _generate_debug_token() -> str:
    """Generate fresh debug-REST token, persist with chmod 600."""
    os.makedirs(CONFIG_DIR, exist_ok=True)
    token = secrets.token_urlsafe(32)
    with open(DEBUG_TOKEN_FILE, "w") as f:
        f.write(token)
    os.chmod(DEBUG_TOKEN_FILE, 0o600)
    return token


def _load_or_create_debug_token() -> str:
    """Rotate token at every debug-mode startup, write pid file alongside."""
    token = _generate_debug_token()
    try:
        with open(DEBUG_PID_FILE, "w") as f:
            f.write(str(os.getpid()))
        os.chmod(DEBUG_PID_FILE, 0o600)
    except OSError:
        pass
    return token


def _rotate_debug_log_if_needed() -> None:
    try:
        if os.path.getsize(DEBUG_LOG_FILE) > DEBUG_LOG_MAX_BYTES:
            os.replace(DEBUG_LOG_FILE, DEBUG_LOG_FILE + ".1")
    except OSError:
        pass


def _audit_log(method: str, path: str, status: int, message: str = "") -> None:
    """Append one audit entry. Never raises — logging must not crash REST."""
    try:
        os.makedirs(DEBUG_LOG_DIR, exist_ok=True)
        with _audit_log_lock:
            _rotate_debug_log_if_needed()
            ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime())
            line = f"[{ts}] {method} {path} -> {status}"
            if message:
                line += f" ({message})"
            with open(DEBUG_LOG_FILE, "a") as f:
                f.write(line + "\n")
    except OSError:
        pass


# ─── Handler + Server ─────────────────────────────────────────────────────────

class BTerminalDebugHandler(http.server.BaseHTTPRequestHandler):
    """Per-request handler. Bearer auth + dispatch via server._routes_*."""

    def log_message(self, format, *args):
        return  # silence default stderr access log; we use _audit_log

    def _verify_auth(self) -> bool:
        token = getattr(self.server, "token", "") or ""
        header = self.headers.get("Authorization", "") or ""
        expected = f"Bearer {token}"
        if not token or not secrets.compare_digest(header, expected):
            self._send_error(401, "unauthorized")
            return False
        return True

    def _send_json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _audit_log(self.command, self.path, status)

    def _send_error(self, status: int, message: str) -> None:
        body = json.dumps({"error": message}).encode("utf-8")
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)
        _audit_log(self.command, self.path, status, message)

    def _dispatch(self, routes: list) -> None:
        if not self._verify_auth():
            return
        self.server.last_request_ts = time.time()
        parsed = urllib.parse.urlparse(self.path)
        path = parsed.path
        self._query = urllib.parse.parse_qs(parsed.query)
        for pattern, handler in routes:
            m = re.fullmatch(pattern, path)
            if not m:
                continue
            try:
                handler(self, **m.groupdict())
            except Exception as exc:  # noqa: BLE001 — REST must not propagate
                self._send_error(500, f"{type(exc).__name__}: {exc}")
            return
        self._send_error(404, "not found")

    def _read_json_body(self):
        """Read + parse JSON request body. On failure sends error and returns None."""
        length = int(self.headers.get("Content-Length", "0") or "0")
        if length == 0:
            return {}
        if length > DEBUG_FEED_MAX_BYTES:
            self._send_error(413, f"body > {DEBUG_FEED_MAX_BYTES} bytes")
            return None
        raw = self.rfile.read(length)
        try:
            return json.loads(raw)
        except json.JSONDecodeError as exc:
            self._send_error(400, f"invalid JSON: {exc.msg}")
            return None

    def _query_flag(self, name: str) -> bool:
        """Return True if ?name=true present in query string."""
        vals = self._query.get(name, [])
        return bool(vals) and vals[0].lower() in ("1", "true", "yes")

    def do_GET(self):
        self._dispatch(self.server._routes_get)

    def do_POST(self):
        self._dispatch(self.server._routes_post)

    def do_PUT(self):
        self._dispatch(self.server._routes_put)


class BTerminalDebugServer(http.server.HTTPServer):
    """Loopback-only debug REST server. Single-threaded — GTK calls via GLib.idle_add."""

    def __init__(self, app, token: str):
        super().__init__(("127.0.0.1", DEBUG_REST_PORT), BTerminalDebugHandler)
        self.app = app
        self.token = token
        self.last_request_ts = time.time()
        self._routes_get: list = []   # list of (regex_str, handler)
        self._routes_post: list = []
        self._routes_put: list = []


def _via_glib_idle(callable_, timeout: float = 5.0):
    """Run a no-arg callable on the GTK main loop, return its result.

    Bridges REST handler thread → GTK thread. Raises TimeoutError or
    the callable's own exception.
    """
    result_q: queue.Queue = queue.Queue(1)

    def _wrapper():
        try:
            result_q.put(("ok", callable_()))
        except Exception as exc:  # noqa: BLE001
            result_q.put(("err", exc))
        return False

    GLib.idle_add(_wrapper)
    try:
        kind, val = result_q.get(timeout=timeout)
    except queue.Empty as exc:
        raise TimeoutError(f"GTK callback exceeded {timeout}s") from exc
    if kind == "err":
        raise val
    return val


def _resolve_tab(app, idx: int):
    if idx < 0 or idx >= app.notebook.get_n_pages():
        return None
    return app.notebook.get_nth_page(idx)


def _terminal_tab_class():
    """Lazy import of TerminalTab from bterminal — avoids circular imports
    at module load time. Routes call this when they need to isinstance-check
    a tab widget."""
    from bterminal import TerminalTab
    return TerminalTab


# ─── Read-only routes ─────────────────────────────────────────────────────────

def _route_health(h: BTerminalDebugHandler) -> None:
    h._send_json(200, {
        "ok": True,
        "version": APP_VERSION,
        "debug_mode": True,
        "idle_seconds": int(time.time() - h.server.last_request_ts),
    })


def _route_state(h: BTerminalDebugHandler) -> None:
    app = h.server.app

    def _gather():
        return {
            "version": APP_VERSION,
            "debug_mode": True,
            "tabs_count": app.notebook.get_n_pages(),
            "current_tab": app.notebook.get_current_page(),
            "plugins_loaded": sorted(app._plugins.keys()),
            "sidecars": [],
            "options": {
                "theme": _OPTIONS.get("theme"),
                "font": _OPTIONS.get("font"),
            },
        }

    h._send_json(200, _via_glib_idle(_gather))


def _route_tabs(h: BTerminalDebugHandler) -> None:
    app = h.server.app
    TerminalTab = _terminal_tab_class()

    def _gather():
        out = []
        for idx in range(app.notebook.get_n_pages()):
            tab = app.notebook.get_nth_page(idx)
            if not isinstance(tab, TerminalTab):
                continue
            entry = {"idx": idx, "title": tab.get_label()}
            if getattr(tab, "ai_config", None):
                entry["type"] = "claude"
                entry["claude_config_name"] = tab.ai_config.get("name")
                # Task #65 (2026-05-07): provider name explicit in
                # payload so tests / external tooling don't have to
                # parse emoji prefix from title (which is gone after
                # the SVG-pixbuf migration).
                entry["provider"] = tab.ai_config.get("provider", "claude")
            elif getattr(tab, "session", None):
                entry["type"] = "ssh"
                entry["session_name"] = tab.session.get("name")
            else:
                entry["type"] = "local"
            if getattr(tab, "_task_project", None):
                entry["task_project"] = tab._task_project
            out.append(entry)
        return out

    h._send_json(200, {"tabs": _via_glib_idle(_gather)})


def _route_plugins(h: BTerminalDebugHandler) -> None:
    app = h.server.app

    def _gather():
        out = []
        loaded_names = set(app._plugins.keys())
        for name, plugin in app._plugins.items():
            out.append({
                "name": name,
                "title": getattr(plugin, "title", name) or name,
                "version": getattr(plugin, "version", "") or "",
                "author": getattr(plugin, "author", "") or "",
                "description": getattr(plugin, "description", "") or "",
                "loaded": True,
                "enabled": True,
            })
        if os.path.isdir(PLUGINS_DIR):
            cfg = {}
            if os.path.isfile(PLUGINS_CONFIG_FILE):
                try:
                    with open(PLUGINS_CONFIG_FILE) as f:
                        cfg = json.load(f)
                except (OSError, json.JSONDecodeError):
                    cfg = {}
            for entry in sorted(os.listdir(PLUGINS_DIR)):
                path = os.path.join(PLUGINS_DIR, entry)
                if os.path.isfile(path) and entry.endswith(".py"):
                    mod = entry[:-3]
                elif os.path.isdir(path) and os.path.isfile(
                    os.path.join(path, "__init__.py")
                ):
                    mod = entry
                else:
                    continue
                if mod in loaded_names:
                    continue
                out.append({
                    "name": mod,
                    "title": mod,
                    "loaded": False,
                    "enabled": cfg.get(mod, True),
                })
        return out

    h._send_json(200, {"plugins": _via_glib_idle(_gather)})


def _route_sidecars(h: BTerminalDebugHandler) -> None:
    app = h.server.app
    runner = app.sidecar_runner
    out = [
        {
            "name": m.name,
            "title": m.title or m.name,
            "description": m.description,
            "plugin_address": m.plugin_address,
            "healthcheck_url": m.healthcheck_url,
            "running": runner.is_running(m.name),
            "default_in_session": m.default_in_session,
            "auto_start": m.auto_start,
        }
        for m in app.sidecar_manifests.values()
    ]
    h._send_json(200, {"sidecars": out})


def _route_window_screenshot(h: BTerminalDebugHandler) -> None:
    app = h.server.app

    def _capture():
        gdk_window = app.get_window()
        if gdk_window is None:
            raise RuntimeError("window not realized yet")
        w = gdk_window.get_width()
        h_ = gdk_window.get_height()
        pixbuf = Gdk.pixbuf_get_from_window(gdk_window, 0, 0, w, h_)
        if pixbuf is None:
            raise RuntimeError("pixbuf_get_from_window returned None")
        fd, tmp = tempfile.mkstemp(prefix="bterminal-shot-", suffix=".png")
        os.close(fd)
        pixbuf.savev(tmp, "png", [], [])
        return tmp, w, h_

    path, width, height = _via_glib_idle(_capture, timeout=10.0)
    h._send_json(200, {
        "path": path,
        "width": width,
        "height": height,
        "taken_at": time.strftime("%Y-%m-%dT%H:%M:%S"),
    })


def _route_debug_log(h: BTerminalDebugHandler) -> None:
    lines: list[str] = []
    if os.path.isfile(DEBUG_LOG_FILE):
        try:
            with open(DEBUG_LOG_FILE) as f:
                lines = f.readlines()[-200:]
        except OSError:
            pass
    h._send_json(200, {"lines": [ln.rstrip("\n") for ln in lines]})


def _route_debug_sudo_state(h: BTerminalDebugHandler) -> None:
    """BUG#31g: read-only snapshot of the shared sudo askpass cache.

    Returns ``{has_path, pending_dialog}``. Never returns the password or
    the helper path itself — those are secrets.
    """
    app = h.server.app
    cache = getattr(app, "sudo_askpass", None)
    has = bool(cache.is_set()) if cache is not None else False
    pending = bool(getattr(app, "_sudo_dialog_pending", False))
    h._send_json(200, {"has_path": has, "pending_dialog": pending})


def _route_debug_sudo_submit(h: BTerminalDebugHandler) -> None:
    """BUG#31g: test-only bypass of the GTK SudoPasswordDialog.

    Gated by ``BTERMINAL_TEST_FAKE_SUDO=1`` — without that env the endpoint
    returns 403. With it, calls ``cache.ensure(password)`` directly and
    clears the pending-dialog flag so subsequent state polls report
    ``has_path: True, pending_dialog: False``.
    """
    if os.environ.get("BTERMINAL_TEST_FAKE_SUDO") != "1":
        h._send_error(403, "forbidden — set BTERMINAL_TEST_FAKE_SUDO=1")
        return
    body = h._read_json_body()
    if body is None:
        return  # error already sent by helper
    password = body.get("password", "")
    app = h.server.app
    cache = getattr(app, "sudo_askpass", None)
    if cache is None:
        h._send_error(500, "sudo_askpass cache not initialized")
        return
    ok = cache.ensure(password)
    app._sudo_dialog_pending = False
    h._send_json(200, {"ok": ok, "has_path": cache.is_set()})


def _route_feed_log(h: BTerminalDebugHandler) -> None:
    """GET /api/debug/feed_log[?since=<ts>][&label=<l>] — captured feed events.

    Returns list of {ts, label, tab_idx, bytes_b64}. Used by E2E tests to
    assert what BTerminal sent to AI CLI subprocesses (intro prompt,
    auto-trigger, rules inject)."""
    since_raw = h._query.get("since", ["0"])
    label_raw = h._query.get("label", [None])
    try:
        since = float(since_raw[0]) if since_raw else 0.0
    except ValueError:
        since = 0.0
    label = label_raw[0] if label_raw else None
    with _feed_log_lock:
        out = [
            e for e in _feed_log
            if e["ts"] > since and (label is None or e["label"] == label)
        ]
    h._send_json(200, {"events": out, "total_captured": len(_feed_log)})


# ─── Mutating routes ──────────────────────────────────────────────────────────

def _route_post_tabs_local(h: BTerminalDebugHandler) -> None:
    app = h.server.app

    def _open():
        app.add_local_tab()
        return app.notebook.get_current_page()

    new_idx = _via_glib_idle(_open)
    h._send_json(200, {"ok": True, "idx": new_idx})


def _open_ai_tab_by_name(
    h: BTerminalDebugHandler,
    config_name: str,
    enabled_override,
    *,
    require_provider: str | None = None,
) -> None:
    """Shared logic for /api/tabs/ai/{provider} (legacy /api/tabs/claude
    removed in T4.6.1).

    require_provider=None:  match by name only (kept for the path where
                            no provider filter is desired — currently no
                            public route uses it after T4.6.1).
    require_provider=str:   strict match — name AND provider must equal.
    """
    app = h.server.app

    def _open():
        for cfg in app.ai_manager.all():
            if cfg.get("name") != config_name:
                continue
            if require_provider is not None:
                cfg_provider = cfg.get("provider", "claude")
                if cfg_provider != require_provider:
                    continue
            config = dict(cfg)
            if isinstance(enabled_override, list):
                config["enabled_plugins"] = enabled_override
            app.open_claude_tab(config)
            return ("ok", app.notebook.get_current_page())
        return ("not_found", None)

    status, idx = _via_glib_idle(_open, timeout=10.0)
    if status == "not_found":
        provider_label = require_provider if require_provider else "claude"
        h._send_error(404, f"{provider_label} session '{config_name}' not found")
        return
    h._send_json(200, {"ok": True, "idx": idx})


# T4.6.1 (2026-05-07): the legacy `/api/tabs/claude` route was removed.
# Use POST /api/tabs/ai/claude instead. Pre-T2.8 REST consumers will
# now get 404 from the route dispatcher — by design; the new endpoint
# is provider-aware and a strict drop-in replacement.


def _route_post_tabs_ai(h: BTerminalDebugHandler, provider: str) -> None:
    """T2.8: provider-aware tab open.

    POST /api/tabs/ai/{provider} with body {"config_name": "..."}.
    Strict match — session must have both `name == config_name` and
    `provider == {path-arg}`. Validates the provider exists in the
    registry first so an unknown provider returns 404 (not 500).
    """
    body = h._read_json_body()
    if body is None:
        return

    # Validate the path-arg provider against the registry.
    from bterminal.providers import get_registry
    try:
        get_registry().get(provider)
    except KeyError:
        h._send_error(404, f"unknown provider '{provider}'")
        return

    config_name = body.get("config_name")
    if not isinstance(config_name, str) or not config_name:
        h._send_error(400, "'config_name' required (string)")
        return

    _open_ai_tab_by_name(
        h, config_name, body.get("enabled_plugins"),
        require_provider=provider,
    )


def _route_post_sidebar_context_menu(
    h: BTerminalDebugHandler, session_id: str,
) -> None:
    """Task #63: REST analog of right-click → 'Run as ▸' / 'Resume'.

    POST /api/sidebar/context_menu/<session_id>?action=<action>[&provider=<name>]

    Actions:
      run_as: requires &provider=<name>, must differ from saved
              session.provider; spawns a one-off tab with the override.
      resume: gated on session.provider's capabilities.resume_flag;
              spawns with force_options={'resume': True}.

    Always preserves the saved session JSON (open_ai_tab_one_off
    deep-clones); body returns {'ok': true, 'idx': <new_tab_idx>}.
    """
    app = h.server.app

    # Resolve session by id first — 404 if unknown (uniform across actions).
    session = app.ai_manager.get(session_id)
    if session is None:
        h._send_error(404, f"AI session '{session_id}' not found")
        return

    action = h._query.get("action", [None])[0]
    if action not in ("run_as", "resume"):
        h._send_error(
            400, "query 'action' must be 'run_as' or 'resume'",
        )
        return

    if action == "run_as":
        provider = h._query.get("provider", [None])[0]
        if not provider:
            h._send_error(400, "'run_as' requires query 'provider'")
            return
        from bterminal.providers import get_registry
        try:
            get_registry().get(provider)
        except KeyError:
            h._send_error(404, f"unknown provider '{provider}'")
            return
        saved_provider = session.get("provider", "claude")
        if provider == saved_provider:
            h._send_error(
                400,
                f"'run_as' provider matches saved session.provider "
                f"({saved_provider!r}) — use Connect instead",
            )
            return

        def _do_run_as():
            app.open_ai_tab_one_off(session, override_provider=provider)
            return ("ok", app.notebook.get_current_page())
        status, idx = _via_glib_idle(_do_run_as, timeout=10.0)
        h._send_json(200, {"ok": True, "idx": idx})
        return

    # action == "resume"
    from bterminal.ui.sidebar import session_supports_resume_menu
    from bterminal.providers import get_registry
    if not session_supports_resume_menu(session, get_registry()):
        h._send_error(
            400,
            f"session provider {session.get('provider', 'claude')!r} "
            f"has no resume capability",
        )
        return

    def _do_resume():
        app.open_ai_tab_one_off(session, force_options={"resume": True})
        return ("ok", app.notebook.get_current_page())
    status, idx = _via_glib_idle(_do_resume, timeout=10.0)
    h._send_json(200, {"ok": True, "idx": idx})


def _route_post_tab_close(h: BTerminalDebugHandler, idx: str) -> None:
    app = h.server.app
    idx_int = int(idx)
    force = h._query_flag("force")
    TerminalTab = _terminal_tab_class()

    def _close():
        tab = _resolve_tab(app, idx_int)
        if tab is None:
            return ("not_found", None)
        if not isinstance(tab, TerminalTab):
            return ("not_terminal", None)
        if getattr(tab, "_task_project", None) and not force:
            return ("conflict", tab._task_project)
        app.close_tab(tab)
        return ("ok", None)

    status, info = _via_glib_idle(_close)
    if status == "not_found":
        h._send_error(404, f"no tab at idx {idx_int}")
    elif status == "not_terminal":
        h._send_error(400, f"tab {idx_int} is not a TerminalTab")
    elif status == "conflict":
        h._send_error(409, f"tab has active task '{info}'; pass ?force=true")
    else:
        h._send_json(200, {"ok": True})


def _route_post_tab_feed(h: BTerminalDebugHandler, idx: str) -> None:
    body = h._read_json_body()
    if body is None:
        return  # _read_json_body already sent error
    text = body.get("text", "")
    if not isinstance(text, str):
        h._send_error(400, "'text' must be a string")
        return
    payload = text.encode("utf-8")
    if len(payload) > DEBUG_FEED_MAX_BYTES:
        h._send_error(413, f"text > {DEBUG_FEED_MAX_BYTES} bytes")
        return
    app = h.server.app
    idx_int = int(idx)
    TerminalTab = _terminal_tab_class()

    def _feed():
        tab = _resolve_tab(app, idx_int)
        if tab is None or not isinstance(tab, TerminalTab):
            return False
        tab.terminal.feed_child(payload)
        return True

    ok = _via_glib_idle(_feed)
    if not ok:
        h._send_error(404, f"no terminal tab at idx {idx_int}")
        return
    preview = text[:80].replace("\n", "\\n")
    _audit_log("POST", f"/api/tabs/{idx_int}/feed", 200, f"len={len(payload)} preview={preview!r}")
    h._send_json(200, {"ok": True, "bytes": len(payload)})


def _route_post_tab_simulate_prompt(h: BTerminalDebugHandler, idx: str) -> None:
    """Debug helper: simulate a Claude-Code prompt (Enter) without going
    through X11 / VTE. Increments the tab's stats_bar prompt counter and
    runs the same _maybe_inject_rules pass that the real Enter handler
    does. Returns the resulting state so tests can assert that
    _inject_pending was set when count crosses inject_every.
    """
    app = h.server.app
    idx_int = int(idx)
    TerminalTab = _terminal_tab_class()

    def _do():
        tab = _resolve_tab(app, idx_int)
        if tab is None or not isinstance(tab, TerminalTab):
            return ("not_found", None)
        if tab._stats_bar is None or tab.ai_config is None:
            return ("not_claude_tab", None)
        tab._stats_bar.increment_prompt()
        tab._maybe_inject_rules()
        pending = tab._inject_pending
        return ("ok", {
            "prompt_count": tab._stats_bar._prompt_count,
            "inject_pending": list(pending) if pending else None,
            "user_is_typing": tab._user_is_typing,
        })

    status, info = _via_glib_idle(_do)
    if status == "not_found":
        h._send_error(404, f"no terminal tab at idx {idx_int}")
        return
    if status == "not_claude_tab":
        h._send_error(400, f"tab {idx_int} is not a Claude Code tab (no _stats_bar)")
        return
    h._send_json(200, {"ok": True, **info})


def _route_get_tab_inject_state(h: BTerminalDebugHandler, idx: str) -> None:
    """GET /api/debug/tabs/{idx}/inject_state — read-only snapshot of the
    typing-guard and injection state for a tab.  Used by E2E tests to
    assert that _user_is_typing blocks rule injection while the user is
    composing a message.
    """
    app = h.server.app
    idx_int = int(idx)
    TerminalTab = _terminal_tab_class()

    def _do():
        tab = _resolve_tab(app, idx_int)
        if tab is None or not isinstance(tab, TerminalTab):
            return ("not_found", None)
        pending = tab._inject_pending
        return ("ok", {
            "user_is_typing": tab._user_is_typing,
            "inject_pending": list(pending) if pending else None,
            "prompt_count": tab._stats_bar._prompt_count if tab._stats_bar else None,
        })

    status, info = _via_glib_idle(_do)
    if status == "not_found":
        h._send_error(404, f"no terminal tab at idx {idx_int}")
        return
    h._send_json(200, info)


def _route_post_tab_force_idle(h: BTerminalDebugHandler, idx: str) -> None:
    """Debug helper: trigger _on_task_idle_timeout immediately rather
    than waiting 10s of real silence. Flushes any pending rules
    injection. Returns whether something was actually injected.
    """
    app = h.server.app
    idx_int = int(idx)
    TerminalTab = _terminal_tab_class()

    def _do():
        tab = _resolve_tab(app, idx_int)
        if tab is None or not isinstance(tab, TerminalTab):
            return ("not_found", None)
        had_pending = tab._inject_pending is not None
        # Cancel any real timer so we don't double-fire.
        if tab._task_idle_timer:
            try:
                GLib.source_remove(tab._task_idle_timer)
            except Exception:
                pass
            tab._task_idle_timer = None
        tab._on_task_idle_timeout()
        return ("ok", {
            "had_pending": had_pending,
            "still_pending": tab._inject_pending is not None,
        })

    status, info = _via_glib_idle(_do, timeout=15.0)
    if status == "not_found":
        h._send_error(404, f"no terminal tab at idx {idx_int}")
        return
    h._send_json(200, {"ok": True, **info})


def _route_post_tab_key(h: BTerminalDebugHandler, idx: str) -> None:
    body = h._read_json_body()
    if body is None:
        return
    key = body.get("key", "")
    if key not in DEBUG_KEY_WHITELIST:
        h._send_error(400, f"key '{key}' not in whitelist {sorted(DEBUG_KEY_WHITELIST)}")
        return
    payload = DEBUG_KEY_BYTES[key]
    app = h.server.app
    idx_int = int(idx)
    TerminalTab = _terminal_tab_class()

    def _send():
        tab = _resolve_tab(app, idx_int)
        if tab is None or not isinstance(tab, TerminalTab):
            return False
        tab.terminal.feed_child(payload)
        return True

    ok = _via_glib_idle(_send)
    if not ok:
        h._send_error(404, f"no terminal tab at idx {idx_int}")
        return
    h._send_json(200, {"ok": True, "key": key})


def _route_post_sidebar_show(h: BTerminalDebugHandler) -> None:
    """Switch the sidebar's active stack child by name. Lets tests
    deterministically open Memory/Plugins/etc. without hunting for the
    right pixel to xdotool-click.
    """
    body = h._read_json_body()
    if body is None:
        return
    name = body.get("name", "")
    if not isinstance(name, str) or not name:
        h._send_error(400, "'name' required (string)")
        return
    app = h.server.app

    def _switch():
        if not app._sidebar_visible:
            app.toggle_sidebar()
        if app.sidebar_stack.get_child_by_name(name) is None:
            return ("not_found", None)
        app.sidebar_stack.set_visible_child_name(name)
        return ("ok", app.sidebar_stack.get_visible_child_name())

    status, current = _via_glib_idle(_switch)
    if status == "not_found":
        h._send_error(404, f"sidebar child '{name}' not found")
        return
    h._send_json(200, {"ok": True, "active": current})


def _route_post_toggle_sidebar(h: BTerminalDebugHandler) -> None:
    app = h.server.app

    def _toggle():
        app.toggle_sidebar()
        return app._sidebar_visible

    visible = _via_glib_idle(_toggle)
    h._send_json(200, {"ok": True, "visible": visible})


def _route_post_toggle_git_panel(h: BTerminalDebugHandler) -> None:
    app = h.server.app

    def _toggle():
        app.toggle_git_panel()
        return app._git_visible

    visible = _via_glib_idle(_toggle)
    h._send_json(200, {"ok": True, "visible": visible})


def _route_window_state(h: BTerminalDebugHandler) -> None:
    """GET /api/window/state — reads sidebar/git/theme state without
    mutating anything. Used by View menu E2E (#158) for assertions."""
    app = h.server.app

    def _gather():
        return {
            "sidebar_visible": bool(getattr(app, "_sidebar_visible", False)),
            "sidebar_active_panel": (
                app.sidebar_stack.get_visible_child_name()
                if hasattr(app, "sidebar_stack") and app.sidebar_stack else None
            ),
            "git_visible": bool(getattr(app, "_git_visible", False)),
            "theme": _OPTIONS.get("theme"),
        }

    h._send_json(200, _via_glib_idle(_gather))


def _route_sessions_list(h: BTerminalDebugHandler) -> None:
    """GET /api/sessions — read-only inventory of saved sidebar entries
    (SSH + AI). Used by Sidebar CRUD E2E (#160) to verify Add/Edit/
    Delete operations.
    """
    app = h.server.app

    def _gather():
        ssh = []
        for s in app.session_manager.all():
            ssh.append({
                "id": s.get("id"),
                "name": s.get("name", ""),
                "host": s.get("host", ""),
                "user": s.get("username", ""),
                "folder": s.get("folder", ""),
            })
        ai = []
        for s in app.ai_manager.all():
            ai.append({
                "id": s.get("id"),
                "name": s.get("name", ""),
                "provider": s.get("provider", "claude"),
                "project_dir": s.get("project_dir", ""),
                "folder": s.get("folder", ""),
            })
        return {"ssh": ssh, "ai": ai}

    h._send_json(200, _via_glib_idle(_gather))


def _route_session_add_ssh(h: BTerminalDebugHandler) -> None:
    """POST /api/sessions/ssh — programmatic equivalent of
    File → New SSH session → fill form → OK. Used by Sidebar CRUD
    E2E (#160) where xdotool typing into a Gtk.SpinButton (Port)
    discards the text. Body: {"name", "host", optionally "user"/"port"}."""
    body = h._read_json_body()
    if body is None:
        return
    name = body.get("name", "")
    host = body.get("host", "")
    if not name or not host:
        h._send_error(400, "'name' and 'host' required")
        return
    app = h.server.app
    entry = {
        "name": name,
        "host": host,
        "port": int(body.get("port", 22)),
        "username": body.get("user", ""),
        "key": body.get("key", ""),
        "folder": body.get("folder", ""),
    }

    def _add():
        return app.session_manager.add(entry)

    saved = _via_glib_idle(_add)
    h._send_json(200, {"ok": True, "id": saved.get("id"), "kind": "ssh"})


def _route_session_add_ai(h: BTerminalDebugHandler) -> None:
    """POST /api/sessions/ai — programmatic equivalent of
    File → New Claude Code session → fill form → OK. Body:
    {"name", optionally "provider", "project_dir", "folder",
     "custom_prompt"}. Defaults to provider=claude."""
    body = h._read_json_body()
    if body is None:
        return
    name = body.get("name", "")
    if not name:
        h._send_error(400, "'name' required")
        return
    provider = body.get("provider", "claude")
    app = h.server.app
    # #108: reject duplicate session names. UI dialog (claude_code.py)
    # validates same way; REST endpoint must enforce same constraint
    # so test fixtures don't accidentally bypass uniqueness.
    for existing in app.ai_manager.all():
        if existing.get("name", "").strip() == name:
            h._send_error(
                409, f"session name '{name}' already in use"
            )
            return
    entry = {
        "name": name,
        "provider": provider,
        "project_dir": body.get("project_dir", ""),
        "folder": body.get("folder", ""),
        "custom_prompt": body.get("custom_prompt", ""),
        "resume": False,
        "skip_permissions": False,
        "permissions_allowlist": "",
    }

    def _add():
        return app.ai_manager.add(entry)

    saved = _via_glib_idle(_add)
    # #113: parity with sidebar.py's UI flow — when the user adds an AI
    # session through Add ▼ → Claude Code → OK, sidebar.py:701 runs
    # _run_ctx_wizard_if_needed which materializes CLAUDE.md and the
    # per-provider mirrors (AGENTS.md, AIDER.md). REST callers (test
    # fixtures, automation) deserve the same on-disk scaffolding.
    # Headless: skip the GUI wizard, write the default template +
    # mirror via bootstrap_provider_context_files.
    if entry["project_dir"]:
        try:
            from bterminal.ctx.helpers import (
                bootstrap_provider_context_files,
            )
            bootstrap_provider_context_files(entry["project_dir"])
        except Exception:
            pass  # non-fatal; session itself was saved successfully

    h._send_json(200, {"ok": True, "id": saved.get("id"), "kind": "ai"})


def _route_session_update(
    h: BTerminalDebugHandler, session_id: str,
) -> None:
    """POST /api/sessions/<session_id>/update — programmatic equivalent
    of Edit dialog → change field → OK. Body: dict of fields to merge
    into the existing session JSON."""
    body = h._read_json_body()
    if body is None:
        return
    app = h.server.app

    def _update():
        # Try AI manager first, then SSH.
        if app.ai_manager.get(session_id):
            return ("ai", app.ai_manager.update(session_id, body))
        if app.session_manager.get(session_id):
            return ("ssh", app.session_manager.update(session_id, body))
        return (None, None)

    kind, updated = _via_glib_idle(_update)
    if kind is None:
        h._send_error(404, f"session '{session_id}' not found")
        return
    h._send_json(200, {"ok": True, "kind": kind, "id": session_id})


def _route_session_delete(
    h: BTerminalDebugHandler, session_id: str,
) -> None:
    """POST /api/sessions/<session_id>/delete — remove sidebar entry by
    id. Searches both SSH and AI managers."""
    app = h.server.app

    def _delete():
        if app.session_manager.get(session_id):
            app.session_manager.delete(session_id)
            return "ssh"
        if app.ai_manager.get(session_id):
            app.ai_manager.delete(session_id)
            return "ai"
        return None

    kind = _via_glib_idle(_delete)
    if kind is None:
        h._send_error(404, f"session '{session_id}' not found")
        return
    h._send_json(200, {"ok": True, "deleted": session_id, "kind": kind})


def _route_post_quit(h: BTerminalDebugHandler) -> None:
    if not h._query_flag("confirm"):
        h._send_error(400, "destructive action — pass ?confirm=true")
        return
    app = h.server.app
    GLib.idle_add(lambda: (GLib.timeout_add(100, app.destroy), False)[1])
    h._send_json(200, {"ok": True, "scheduled": True})


def _route_get_tab_intro_prompt(h: BTerminalDebugHandler, idx: str) -> None:
    """Read-only: return the intro prompt that would be injected into Claude
    Code if it started in this tab right now. No side effects."""
    app = h.server.app
    idx_int = int(idx)
    TerminalTab = _terminal_tab_class()
    from bterminal import _compute_intro_prompt_for_tab

    def _build():
        tab = _resolve_tab(app, idx_int)
        if tab is None or not isinstance(tab, TerminalTab):
            return ("not_found", None)
        return ("ok", _compute_intro_prompt_for_tab(app, tab))

    status, prompt = _via_glib_idle(_build)
    if status == "not_found":
        h._send_error(404, f"no terminal tab at idx {idx_int}")
        return
    h._send_json(200, {"idx": idx_int, "intro_prompt": prompt})


def _route_get_tab_plugins(h: BTerminalDebugHandler, idx: str) -> None:
    app = h.server.app
    idx_int = int(idx)
    TerminalTab = _terminal_tab_class()

    def _read():
        tab = _resolve_tab(app, idx_int)
        if tab is None or not isinstance(tab, TerminalTab):
            return ("not_found", None)
        if tab.enabled_plugins is None:
            return ("ok", None)
        return ("ok", sorted(tab.enabled_plugins))

    status, value = _via_glib_idle(_read)
    if status == "not_found":
        h._send_error(404, f"no terminal tab at idx {idx_int}")
        return
    h._send_json(200, {"idx": idx_int, "enabled_plugins": value})


def _route_put_tab_plugins(h: BTerminalDebugHandler, idx: str) -> None:
    body = h._read_json_body()
    if body is None:
        return
    enabled = body.get("enabled")
    if not isinstance(enabled, list) or not all(isinstance(x, str) for x in enabled):
        h._send_error(400, "'enabled' must be a list of strings")
        return
    new_set = set(enabled)
    app = h.server.app
    idx_int = int(idx)
    TerminalTab = _terminal_tab_class()

    def _apply():
        tab = _resolve_tab(app, idx_int)
        if tab is None or not isinstance(tab, TerminalTab):
            return ("not_found", None)
        old_set = tab.enabled_plugins if tab.enabled_plugins is not None else set()
        to_acquire = new_set - old_set
        to_release = old_set - new_set
        # Only sidecars participate in refcount; GTK plugins are global.
        for name in to_acquire:
            if name in app.sidecar_manifests:
                app._sidecar_acquire(name)
        for name in to_release:
            if name in app.sidecar_manifests:
                app._sidecar_release(name)
        tab.enabled_plugins = new_set
        return ("ok", {
            "acquired": sorted(to_acquire & set(app.sidecar_manifests)),
            "released": sorted(to_release & set(app.sidecar_manifests)),
        })

    status, info = _via_glib_idle(_apply)
    if status == "not_found":
        h._send_error(404, f"no terminal tab at idx {idx_int}")
        return
    h._send_json(200, {
        "ok": True,
        "idx": idx_int,
        "enabled_plugins": sorted(new_set),
        "diff": info,
    })


def _route_post_plugin_enable(h: BTerminalDebugHandler, name: str) -> None:
    app = h.server.app

    def _do():
        cfg = {}
        if os.path.isfile(PLUGINS_CONFIG_FILE):
            try:
                with open(PLUGINS_CONFIG_FILE) as fh:
                    cfg = json.load(fh)
            except (OSError, json.JSONDecodeError):
                cfg = {}
        cfg[name] = True
        os.makedirs(os.path.dirname(PLUGINS_CONFIG_FILE), exist_ok=True)
        with open(PLUGINS_CONFIG_FILE, "w") as fh:
            json.dump(cfg, fh, indent=2)
        already = name in app._plugins
        if not already:
            app._hot_load_plugin(name)
        if hasattr(app, "plugin_panel"):
            try:
                app.plugin_panel.refresh()
            except Exception:
                pass
        return {"already_loaded": already, "loaded_now": name in app._plugins}

    try:
        result = _via_glib_idle(_do, timeout=10.0)
    except Exception as exc:  # noqa: BLE001
        h._send_error(400, f"{type(exc).__name__}: {exc}")
        return
    h._send_json(200, {"ok": True, "name": name, **result})


def _route_post_plugin_disable(h: BTerminalDebugHandler, name: str) -> None:
    app = h.server.app

    def _do():
        cfg = {}
        if os.path.isfile(PLUGINS_CONFIG_FILE):
            try:
                with open(PLUGINS_CONFIG_FILE) as fh:
                    cfg = json.load(fh)
            except (OSError, json.JSONDecodeError):
                cfg = {}
        cfg[name] = False
        os.makedirs(os.path.dirname(PLUGINS_CONFIG_FILE), exist_ok=True)
        with open(PLUGINS_CONFIG_FILE, "w") as fh:
            json.dump(cfg, fh, indent=2)
        was_loaded = name in app._plugins
        if was_loaded:
            app._hot_unload_plugin(name)
        if hasattr(app, "plugin_panel"):
            try:
                app.plugin_panel.refresh()
            except Exception:
                pass
        return {"was_loaded": was_loaded}

    try:
        result = _via_glib_idle(_do, timeout=10.0)
    except Exception as exc:  # noqa: BLE001
        h._send_error(400, f"{type(exc).__name__}: {exc}")
        return
    h._send_json(200, {"ok": True, "name": name, **result})


def _route_post_sidecar_start(h: BTerminalDebugHandler, name: str) -> None:
    app = h.server.app
    manifest = app.sidecar_manifests.get(name)
    if manifest is None:
        h._send_error(404, f"sidecar '{name}' not found")
        return
    try:
        result = app.sidecar_runner.start(name, manifest)
    except (RuntimeError, FileNotFoundError, OSError) as exc:
        h._send_error(400, f"{type(exc).__name__}: {exc}")
        return
    h._send_json(200, {
        "ok": True,
        "name": name,
        "pid": result["pid"],
        "already_running": result["already_running"],
    })


def _route_post_sidecar_stop(h: BTerminalDebugHandler, name: str) -> None:
    app = h.server.app
    if name not in app.sidecar_manifests:
        h._send_error(404, f"sidecar '{name}' not found")
        return
    result = app.sidecar_runner.stop(name)
    h._send_json(200, {
        "ok": True,
        "name": name,
        "was_running": result["was_running"],
    })


def _route_get_sidecar_health(h: BTerminalDebugHandler, name: str) -> None:
    app = h.server.app
    manifest = app.sidecar_manifests.get(name)
    if manifest is None:
        h._send_error(404, f"sidecar '{name}' not found")
        return
    if not manifest.healthcheck_url:
        h._send_json(200, {
            "ok": False, "url": "", "reason": "no healthcheck_url in manifest",
        })
        return
    if not app.sidecar_runner.is_running(name):
        h._send_json(200, {
            "ok": False, "url": manifest.healthcheck_url, "reason": "not running",
        })
        return
    started = time.monotonic()
    status_code = None
    ok = False
    error = None
    try:
        with urllib.request.urlopen(manifest.healthcheck_url, timeout=2.0) as resp:
            status_code = resp.status
            ok = status_code < 500
    except urllib.error.HTTPError as exc:
        status_code = exc.code
        ok = exc.code < 500
    except (urllib.error.URLError, OSError) as exc:
        error = f"{type(exc).__name__}: {exc}"
    payload = {
        "ok": ok,
        "url": manifest.healthcheck_url,
        "latency_ms": int((time.monotonic() - started) * 1000),
    }
    if status_code is not None:
        payload["status_code"] = status_code
    if error:
        payload["error"] = error
    h._send_json(200, payload)


# ─── Lifecycle ────────────────────────────────────────────────────────────────

def _start_debug_rest_server(app, token: str) -> BTerminalDebugServer:
    """Bind 127.0.0.1:7780, register routes, start serve_forever in daemon thread."""
    server = BTerminalDebugServer(app, token)
    server._routes_get.extend([
        (r"/api/health", _route_health),
        (r"/api/state", _route_state),
        (r"/api/tabs", _route_tabs),
        (r"/api/plugins", _route_plugins),
        (r"/api/sidecars", _route_sidecars),
        (r"/api/sidecars/(?P<name>[\w.-]+)/health", _route_get_sidecar_health),
        (r"/api/tabs/(?P<idx>\d+)/plugins", _route_get_tab_plugins),
        (r"/api/tabs/(?P<idx>\d+)/intro_prompt", _route_get_tab_intro_prompt),
        (r"/api/window/screenshot", _route_window_screenshot),
        (r"/api/window/state", _route_window_state),
        (r"/api/sessions", _route_sessions_list),
        (r"/api/debug/log", _route_debug_log),
        (r"/api/debug/feed_log", _route_feed_log),
        (r"/api/debug/sudo_state", _route_debug_sudo_state),
        (r"/api/tabs/(?P<idx>\d+)/inject_state", _route_get_tab_inject_state),
    ])
    server._routes_put.extend([
        (r"/api/tabs/(?P<idx>\d+)/plugins", _route_put_tab_plugins),
    ])
    server._routes_post.extend([
        (r"/api/tabs/local", _route_post_tabs_local),
        # T2.8: provider-aware. Path-arg `provider` matches a-zA-Z0-9_-
        # (subset of route regex) — validated against the registry inside
        # the handler so unknown providers return 404 rather than 500.
        (r"/api/tabs/ai/(?P<provider>[\w-]+)", _route_post_tabs_ai),
        (r"/api/tabs/(?P<idx>\d+)/close", _route_post_tab_close),
        (r"/api/tabs/(?P<idx>\d+)/feed", _route_post_tab_feed),
        (r"/api/tabs/(?P<idx>\d+)/key", _route_post_tab_key),
        (r"/api/tabs/(?P<idx>\d+)/simulate_prompt", _route_post_tab_simulate_prompt),
        (r"/api/tabs/(?P<idx>\d+)/force_idle", _route_post_tab_force_idle),
        (r"/api/window/toggle_sidebar", _route_post_toggle_sidebar),
        (r"/api/window/sidebar/show", _route_post_sidebar_show),
        (r"/api/window/toggle_git_panel", _route_post_toggle_git_panel),
        (r"/api/quit", _route_post_quit),
        (r"/api/sessions/ssh", _route_session_add_ssh),
        (r"/api/sessions/ai", _route_session_add_ai),
        (r"/api/sessions/(?P<session_id>[\w.-]+)/update", _route_session_update),
        (r"/api/sessions/(?P<session_id>[\w.-]+)/delete", _route_session_delete),
        (r"/api/plugins/(?P<name>[\w.-]+)/enable", _route_post_plugin_enable),
        (r"/api/plugins/(?P<name>[\w.-]+)/disable", _route_post_plugin_disable),
        (r"/api/sidecars/(?P<name>[\w.-]+)/start", _route_post_sidecar_start),
        (r"/api/sidecars/(?P<name>[\w.-]+)/stop", _route_post_sidecar_stop),
        # Task #63: REST analog of right-click → 'Run as ▸' / 'Resume'.
        (r"/api/sidebar/context_menu/(?P<session_id>[\w.-]+)",
         _route_post_sidebar_context_menu),
        # BUG#31g: test-only bypass for the SudoPasswordDialog (gated by env).
        (r"/api/debug/sudo_submit", _route_debug_sudo_submit),
    ])
    thread = threading.Thread(target=server.serve_forever, daemon=True, name="debug-rest")
    thread.start()
    server._thread = thread
    return server


def _stop_debug_rest_server(server) -> None:
    """Idempotent shutdown."""
    if server is None:
        return
    try:
        server.shutdown()
        server.server_close()
    except Exception:
        pass


def _start_idle_watchdog(server: BTerminalDebugServer) -> None:
    """Monitor last_request_ts; auto-stop after DEBUG_IDLE_TIMEOUT_SEC of silence."""
    def _loop():
        while getattr(server, "_alive", True):
            time.sleep(DEBUG_IDLE_CHECK_SEC)
            if time.time() - server.last_request_ts > DEBUG_IDLE_TIMEOUT_SEC:
                sys.stderr.write(
                    f"[debug-rest] idle for {DEBUG_IDLE_TIMEOUT_SEC}s — shutting down server\n"
                )
                server._alive = False
                _stop_debug_rest_server(server)
                if hasattr(server.app, "_debug_server"):
                    server.app._debug_server = None
                return

    server._alive = True
    t = threading.Thread(target=_loop, daemon=True, name="debug-rest-idle")
    t.start()
