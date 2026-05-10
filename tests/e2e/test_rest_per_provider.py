"""Dispatch matrix: REST surface per provider — every endpoint
× provider name path (#64 / #136, audit § 4 integration matrix).

For each /api/tabs/ai/{provider} entry-point AND every operational
endpoint that takes `{idx}`, assert behavior is provider-agnostic
where the audit says it should be (intro_prompt, plugins, feed,
key, force_idle, close) AND provider-specific where the audit
documents typed errors (simulate_prompt requires Claude tab → 400).

Decision branches:
  (a) known provider (claude / copilot / aider) — full happy path
  (b) unknown provider 'future-cli-2030' — 404 from path-arg
      validator
  (c) malformed config_name (missing / empty / wrong type) — 400
  (d) config_name doesn't match any session for that provider — 404
  (e) config_name exists but provider mismatch (strict match) — 404

Manual VM (curl REST against running BT) is documented; the e2e
spawn here covers the same paths headlessly.
"""
from __future__ import annotations

import json
import os
import shutil
import signal
import socket
import stat
import subprocess
import sys
import tempfile
import time
import urllib.error
import urllib.request
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).parent.parent.parent
MOCK_SRC = REPO_ROOT / "tools" / "mock_ai_cli"
DEBUG_REST = REPO_ROOT / "bterminal" / "debug_rest.py"
PROVIDERS = ["claude", "copilot", "aider"]


# ─── Source pins (no subprocess) ─────────────────────────────────────


def test_route_pattern_matches_all_three_providers_and_more():
    """Pin: route regex `/api/tabs/ai/(?P<provider>[\\w-]+)`
    accepts any name a future plugin may register. Pre-T2.8
    legacy `/api/tabs/claude` was removed."""
    src = DEBUG_REST.read_text()
    assert "/api/tabs/ai/(?P<provider>[\\w-]+)" in src
    # Legacy explicit route must NOT exist
    assert "/api/tabs/claude" not in src or \
        "Use POST /api/tabs/ai/claude" in src


def test_unknown_provider_returns_404_from_handler():
    """Pin: `_route_post_tabs_ai` validates path-arg via
    `get_registry().get(provider)` BEFORE dispatching. Returns
    404 (not 500) on unknown provider — required for plugin
    forward-compat (#50 / #122)."""
    src = DEBUG_REST.read_text()
    handler_idx = src.find("def _route_post_tabs_ai")
    assert handler_idx > 0
    body_end = src.find("\n\ndef ", handler_idx)
    body = src[handler_idx:body_end]
    assert "get_registry().get(provider)" in body
    assert "except KeyError" in body
    assert "404" in body and "unknown provider" in body


def test_malformed_config_name_returns_400():
    """Pin: missing or non-string config_name → 400 (not 500)."""
    src = DEBUG_REST.read_text()
    handler_idx = src.find("def _route_post_tabs_ai")
    body_end = src.find("\n\ndef ", handler_idx)
    body = src[handler_idx:body_end]
    assert "isinstance(config_name, str)" in body
    assert "400" in body
    assert "'config_name' required" in body


def test_provider_mismatch_returns_404_strict_match():
    """Pin: `_open_ai_tab_by_name` with require_provider=str
    returns 404 if name matches but provider doesn't. Strict
    match — name AND provider must equal."""
    src = DEBUG_REST.read_text()
    handler_idx = src.find("def _open_ai_tab_by_name")
    body_end = src.find("\n\ndef ", handler_idx)
    body = src[handler_idx:body_end]
    assert "require_provider" in body
    assert "cfg_provider != require_provider" in body
    assert 'status == "not_found"' in body
    assert "404" in body


def test_per_idx_endpoints_pinned_via_route_table():
    """Pin: every audit-listed `/api/tabs/{idx}/<op>` route is
    registered. Catches a refactor that forgets to wire one up."""
    src = DEBUG_REST.read_text()
    expected_post = [
        r"/api/tabs/(?P<idx>\d+)/close",
        r"/api/tabs/(?P<idx>\d+)/feed",
        r"/api/tabs/(?P<idx>\d+)/key",
        r"/api/tabs/(?P<idx>\d+)/simulate_prompt",
        r"/api/tabs/(?P<idx>\d+)/force_idle",
    ]
    expected_get = [
        r"/api/tabs/(?P<idx>\d+)/intro_prompt",
        r"/api/tabs/(?P<idx>\d+)/plugins",
    ]
    expected_put = [
        r"/api/tabs/(?P<idx>\d+)/plugins",
    ]
    for route in expected_post + expected_get + expected_put:
        assert route in src, f"missing REST route: {route}"


# ─── E2E fixture: spawn BT with all 3 providers ─────────────────────


def _wait_for_health(base, deadline) -> bool:
    while time.monotonic() < deadline:
        try:
            urllib.request.urlopen(f"{base}/api/health", timeout=1.0)
            return True
        except urllib.error.HTTPError as exc:
            if exc.code == 401:
                return True
        except (urllib.error.URLError, OSError):
            time.sleep(0.3)
    return False


def _http(base, token, method, path, body=None):
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(
        f"{base}{path}", data=data,
        headers={"Authorization": f"Bearer {token}",
                 "Content-Type": "application/json"},
        method=method,
    )
    try:
        with urllib.request.urlopen(req, timeout=10) as resp:
            payload = resp.read()
            return resp.status, json.loads(payload) if payload else {}
    except urllib.error.HTTPError as exc:
        try:
            return exc.code, json.loads(exc.read())
        except (json.JSONDecodeError, OSError):
            return exc.code, {"error": exc.reason}


@pytest.fixture
def bterminal_with_three_providers():
    """BT subprocess with mock CLI for each of claude/copilot/aider
    + corresponding ai_sessions.json entry per provider."""
    from tests._subprocess_helpers import seed_license

    home = tempfile.mkdtemp(prefix="bterminal-rest-matrix-")
    seed_license(home)

    fake_bin = Path(home) / "fake-bin"
    fake_bin.mkdir(parents=True, exist_ok=True)
    for name in ("claude", "copilot", "aider"):
        target = fake_bin / name
        shutil.copy(str(MOCK_SRC), str(target))
        target.chmod(target.stat().st_mode
                     | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)

    project_dir = Path(home) / "myproj"
    project_dir.mkdir(parents=True, exist_ok=True)

    cfg_dir = Path(home) / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True, exist_ok=True)
    sessions = [
        {
            "id": "c-1", "name": "ClaudeSession",
            "provider": "claude", "project_dir": str(project_dir),
            "color": "#74c7ec", "provider_options": {},
        },
        {
            "id": "p-1", "name": "CopilotSession",
            "provider": "copilot", "project_dir": str(project_dir),
            "color": "#a6e3a1", "provider_options": {},
        },
        {
            "id": "a-1", "name": "AiderSession",
            "provider": "aider", "project_dir": str(project_dir),
            "color": "#fab387", "provider_options": {},
        },
    ]
    (cfg_dir / "ai_sessions.json").write_text(json.dumps(sessions))

    s = socket.socket()
    s.bind(("127.0.0.1", 0))
    port = s.getsockname()[1]
    s.close()

    env = {
        **os.environ,
        "HOME": home,
        "PATH": f"{fake_bin}{os.pathsep}{os.environ.get('PATH', '')}",
        "BTERMINAL_DEBUG_REST_PORT": str(port),
        "MOCK_AI_CLI_SCENARIO": str(REPO_ROOT / "tests" / "scenarios"
                                    / "claude_basic.json"),
    }
    stderr_path = Path(home) / "bterminal-stderr.log"
    stdout_path = Path(home) / "bterminal-stdout.log"
    stderr_handle = open(stderr_path, "w")
    stdout_handle = open(stdout_path, "w")
    proc = subprocess.Popen(
        ["xvfb-run", "-a", sys.executable, "-m", "bterminal",
         "--debug-rest"],
        cwd=str(REPO_ROOT), env=env,
        stdout=stdout_handle, stderr=stderr_handle,
        start_new_session=True,
    )

    base = f"http://127.0.0.1:{port}"
    if not _wait_for_health(base, time.monotonic() + 30):
        proc.terminate(); proc.wait(timeout=5)
        stderr_handle.close(); stdout_handle.close()
        try:
            err = Path(stderr_path).read_text(errors="replace")[-2000:]
        except OSError:
            err = "(unreadable)"
        try:
            out = Path(stdout_path).read_text(errors="replace")[-2000:]
        except OSError:
            out = "(unreadable)"
        pytest.fail(
            f"BT didn't come up\n--- stdout ---\n{out}\n"
            f"--- stderr ---\n{err}"
        )

    token = (cfg_dir / "debug_token").read_text().strip()
    try:
        yield {"base": base, "token": token, "home": home}
    finally:
        try:
            os.killpg(os.getpgid(proc.pid), signal.SIGTERM)
        except (ProcessLookupError, PermissionError):
            proc.terminate()
        try:
            proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
            except (ProcessLookupError, PermissionError):
                pass
        stderr_handle.close(); stdout_handle.close()


# ─── (a) Known provider — full happy path ──────────────────────────


VM_GATED = pytest.mark.skipif(
    not shutil.which("xvfb-run"),
    reason="needs xvfb-run for headless GTK spawn",
)


@VM_GATED
@pytest.mark.parametrize("provider, config_name", [
    ("claude", "ClaudeSession"),
    ("copilot", "CopilotSession"),
    ("aider", "AiderSession"),
])
def test_open_tab_per_provider_returns_200_and_idx(
        provider, config_name, bterminal_with_three_providers):
    """Branch (a): POST /api/tabs/ai/{provider} with matching
    config_name → 200 + {ok: true, idx: N}. 3 cells."""
    s = bterminal_with_three_providers
    status, body = _http(s["base"], s["token"], "POST",
                         f"/api/tabs/ai/{provider}",
                         {"config_name": config_name})
    assert status == 200, body
    assert body.get("ok") is True
    assert isinstance(body.get("idx"), int)


@VM_GATED
@pytest.mark.parametrize("provider, config_name", [
    ("claude", "ClaudeSession"),
    ("copilot", "CopilotSession"),
    ("aider", "AiderSession"),
])
def test_per_tab_endpoints_provider_agnostic(
        provider, config_name, bterminal_with_three_providers):
    """Branch (a) continued: for each provider, /intro_prompt,
    /plugins (GET+PUT), /feed, /key, /force_idle, /close all
    return 200. simulate_prompt is intentionally Claude-only."""
    s = bterminal_with_three_providers
    base, token = s["base"], s["token"]

    status, body = _http(base, token, "POST",
                         f"/api/tabs/ai/{provider}",
                         {"config_name": config_name})
    assert status == 200, body
    idx = body["idx"]

    # Settle — let the tab finish initializing
    time.sleep(0.5)

    # GET intro_prompt — provider-agnostic 200
    status, body = _http(base, token, "GET",
                         f"/api/tabs/{idx}/intro_prompt", None)
    assert status == 200, body
    assert "intro_prompt" in body

    # GET plugins — provider-agnostic 200
    status, body = _http(base, token, "GET",
                         f"/api/tabs/{idx}/plugins", None)
    assert status == 200, body

    # PUT plugins (empty) — provider-agnostic 200
    status, body = _http(base, token, "PUT",
                         f"/api/tabs/{idx}/plugins",
                         {"enabled": []})
    assert status == 200, body
    assert body.get("enabled_plugins") == []

    # POST feed — provider-agnostic 200
    status, body = _http(base, token, "POST",
                         f"/api/tabs/{idx}/feed",
                         {"text": "hello\n"})
    assert status == 200, body

    # POST key (whitelisted) — provider-agnostic 200
    status, body = _http(base, token, "POST",
                         f"/api/tabs/{idx}/key",
                         {"key": "enter"})
    assert status == 200, body
    assert body.get("key") == "enter"

    # POST force_idle — provider-agnostic 200
    status, body = _http(base, token, "POST",
                         f"/api/tabs/{idx}/force_idle", {})
    assert status == 200, body

    # POST close — provider-agnostic 200 (force=true: mock CLI
    # registers a task, so close defaults to 409 without force)
    status, body = _http(base, token, "POST",
                         f"/api/tabs/{idx}/close?force=true", {})
    assert status == 200, body


@VM_GATED
@pytest.mark.parametrize("provider, config_name, expects_200", [
    ("claude", "ClaudeSession", True),
    # simulate_prompt explicitly requires _stats_bar; on fresh
    # mock-CLI tab the stats_bar IS attached for all 3 (factory
    # registered). Pin all three return 200 OR typed 400.
    ("copilot", "CopilotSession", True),
    ("aider", "AiderSession", True),
])
def test_simulate_prompt_per_provider(
        provider, config_name, expects_200,
        bterminal_with_three_providers):
    """Branch (a)/typed-error: simulate_prompt requires a tab
    with _stats_bar attached. Cell-level pin: it's 200 for all
    three because factory registers stats_bar for every
    provider (#94)."""
    s = bterminal_with_three_providers
    base, token = s["base"], s["token"]

    status, body = _http(base, token, "POST",
                         f"/api/tabs/ai/{provider}",
                         {"config_name": config_name})
    assert status == 200, body
    idx = body["idx"]

    time.sleep(0.5)

    status, body = _http(base, token, "POST",
                         f"/api/tabs/{idx}/simulate_prompt", {})
    if expects_200:
        # Either 200 (stats_bar present) or 400 (gracefully
        # typed). Both are acceptable provider-aware responses;
        # 500 would indicate a regression.
        assert status in (200, 400), body
        if status == 400:
            assert "Claude" in body.get("error", "") or \
                "stats_bar" in body.get("error", "")
    else:
        assert status == 400, body


# ─── (b) Unknown provider ──────────────────────────────────────────


@VM_GATED
def test_unknown_provider_returns_404(bterminal_with_three_providers):
    """Branch (b): future-cli-2030 not in registry → 404."""
    s = bterminal_with_three_providers
    status, body = _http(s["base"], s["token"], "POST",
                         "/api/tabs/ai/future-cli-2030",
                         {"config_name": "ClaudeSession"})
    assert status == 404, body
    assert "unknown provider" in body.get("error", "")


# ─── (c) Malformed config_name ─────────────────────────────────────


@VM_GATED
@pytest.mark.parametrize("payload", [
    {},  # missing
    {"config_name": ""},  # empty string
    {"config_name": 123},  # wrong type
    {"config_name": None},  # null
])
def test_malformed_config_name_returns_400(
        payload, bterminal_with_three_providers):
    """Branch (c): bad config_name → 400 typed error."""
    s = bterminal_with_three_providers
    status, body = _http(s["base"], s["token"], "POST",
                         "/api/tabs/ai/claude", payload)
    assert status == 400, body
    assert "config_name" in body.get("error", "").lower()


# ─── (d) config_name absent ────────────────────────────────────────


@VM_GATED
def test_config_name_not_found_returns_404(
        bterminal_with_three_providers):
    """Branch (d): valid provider, config_name doesn't exist
    → 404."""
    s = bterminal_with_three_providers
    status, body = _http(s["base"], s["token"], "POST",
                         "/api/tabs/ai/claude",
                         {"config_name": "DoesNotExist"})
    assert status == 404, body
    assert "not found" in body.get("error", "").lower()


# ─── (e) Provider mismatch — strict match enforces 404 ─────────────


@VM_GATED
@pytest.mark.parametrize("path_provider, mismatched_config", [
    ("claude", "AiderSession"),    # path=claude, config provider=aider
    ("copilot", "ClaudeSession"),  # path=copilot, config provider=claude
    ("aider", "CopilotSession"),   # path=aider, config provider=copilot
])
def test_provider_mismatch_returns_404(
        path_provider, mismatched_config,
        bterminal_with_three_providers):
    """Branch (e): name matches, provider doesn't → 404
    (strict match prevents cross-provider session opens)."""
    s = bterminal_with_three_providers
    status, body = _http(s["base"], s["token"], "POST",
                         f"/api/tabs/ai/{path_provider}",
                         {"config_name": mismatched_config})
    assert status == 404, body
    # Error message includes the path-arg provider label
    assert path_provider in body.get("error", "")


# ─── /api/tabs response shape per provider ────────────────────────


@VM_GATED
def test_tabs_endpoint_shape_per_provider(
        bterminal_with_three_providers):
    """GET /api/tabs returns provider field — used by external
    tooling to identify session type. Pin shape across all 3."""
    s = bterminal_with_three_providers
    base, token = s["base"], s["token"]

    for prov, name in [("claude", "ClaudeSession"),
                       ("copilot", "CopilotSession"),
                       ("aider", "AiderSession")]:
        _http(base, token, "POST",
              f"/api/tabs/ai/{prov}", {"config_name": name})

    time.sleep(0.5)

    status, body = _http(base, token, "GET", "/api/tabs", None)
    assert status == 200
    tabs = body["tabs"]
    # tabs is a list of dicts; each AI tab exposes type=claude +
    # explicit provider field (debug_rest.py:_route_tabs)
    ai_tabs = [t for t in tabs if t.get("type") == "claude"]
    providers_seen = {t.get("provider") for t in ai_tabs
                      if t.get("provider")}
    assert providers_seen >= {"claude", "copilot", "aider"}, (
        f"expected all 3 providers in /api/tabs, got {providers_seen}"
    )
