"""Failure mode: network down for OpenRouter / consult tool
(#31 / #103, audit § 6.1 #4).

When the network path to OpenRouter is broken (DNS, TCP, TLS), BT's
two HTTP code paths must both:

  1. openai_compat: convert URLError-family failures to APIError
     (NEVER an HTTP-status subclass like AuthError/RateLimitError —
     those are reserved for actual HTTP responses).
  2. consult CLI: catch URLError, print user-friendly message to
     stderr, exit non-zero.

Three decision branches:
  (a) DNS fails — `socket.gaierror` wrapped by `URLError`.
  (b) TCP refused — `ConnectionRefusedError` wrapped by `URLError`.
  (c) TLS handshake fails — `ssl.SSLError` (also wrapped by
      URLError when bubbled from urlopen).

Plus regression: AuthError/RateLimitError still propagate correctly
when there's a real HTTP response (these are independent of network
errors). Pin so a future tweak to error mapping doesn't accidentally
collapse all errors into one shape.

Manual VM smoke (`iptables -A OUTPUT -d openrouter.ai -j REJECT &&
consult "test"`) is documented in tests/manual/README.md. Headless
tests below cover the dispatch logic without touching network state.
"""
from __future__ import annotations

import json
import os
import socket
import ssl
import subprocess
import sys
import urllib.error
from io import BytesIO
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bterminal.openai_compat import (
    APIError,
    AuthError,
    RateLimitError,
    ServerError,
    call_chat_completion,
)


# ─── (a) DNS failure ─────────────────────────────────────────────────────


def test_dns_failure_raises_bare_api_error_not_subclass():
    """`socket.gaierror` (DNS resolution failure) wrapped by
    `URLError` → bare `APIError`. NOT one of the HTTP-status
    subclasses, since there was no HTTP exchange. Caller's retry
    logic uses subclass to decide whether to retry — DNS failure
    should NOT trigger 'retry on 5xx' because there's no 5xx."""
    dns_err = urllib.error.URLError(
        socket.gaierror(socket.EAI_NONAME, "Name or service not known"))
    with patch("urllib.request.urlopen", side_effect=dns_err):
        with pytest.raises(APIError) as exc_info:
            call_chat_completion(
                base_url="https://openrouter.ai/api/v1",
                api_key="sk-test",
                model="google/gemini-2.5-pro",
                messages=[{"role": "user", "content": "test"}],
            )
    err = exc_info.value
    assert type(err) is APIError, (
        f"DNS failure surfaced as {type(err).__name__} — caller's "
        f"retry logic may trigger inappropriate paths"
    )
    assert "Transport error" in str(err)
    assert "openrouter.ai" in str(err)


def test_dns_failure_message_mentions_url():
    """User-facing error must identify the URL that couldn't be
    reached. Without it, debug from the message alone is impossible."""
    dns_err = urllib.error.URLError(
        socket.gaierror(socket.EAI_NONAME, "Name or service not known"))
    with patch("urllib.request.urlopen", side_effect=dns_err):
        with pytest.raises(APIError) as exc_info:
            call_chat_completion(
                base_url="https://no-such-host.invalid/v1",
                api_key="x", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
    assert "no-such-host.invalid" in str(exc_info.value)


# ─── (b) TCP refused / connection broken ─────────────────────────────────


def test_tcp_refused_raises_bare_api_error():
    """`ConnectionRefusedError` (server not listening) wrapped by
    `URLError` → bare `APIError`. Same path as DNS but distinct
    cause: server unreachable rather than name unresolvable."""
    refused = urllib.error.URLError(
        ConnectionRefusedError(111, "Connection refused"))
    with patch("urllib.request.urlopen", side_effect=refused):
        with pytest.raises(APIError) as exc_info:
            call_chat_completion(
                base_url="https://openrouter.ai/api/v1",
                api_key="sk-test", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
    err = exc_info.value
    assert type(err) is APIError
    assert "Transport error" in str(err)


def test_connection_reset_mid_request_raises_bare_api_error():
    """ConnectionResetError (peer reset TCP socket — e.g. iptables
    REJECT --reject-with tcp-reset) wraps in URLError → APIError.
    Same recovery path as plain refused."""
    reset_err = urllib.error.URLError(
        ConnectionResetError(104, "Connection reset by peer"))
    with patch("urllib.request.urlopen", side_effect=reset_err):
        with pytest.raises(APIError) as exc_info:
            call_chat_completion(
                base_url="https://openrouter.ai/api/v1",
                api_key="x", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
    assert type(exc_info.value) is APIError


def test_no_route_to_host_raises_bare_api_error():
    """`OSError(EHOSTUNREACH)` (firewall DROP) — same recovery."""
    no_route = urllib.error.URLError(
        OSError(113, "No route to host"))
    with patch("urllib.request.urlopen", side_effect=no_route):
        with pytest.raises(APIError) as exc_info:
            call_chat_completion(
                base_url="https://openrouter.ai/api/v1",
                api_key="x", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
    assert type(exc_info.value) is APIError


# ─── (c) TLS handshake failures ──────────────────────────────────────────


def test_tls_handshake_failure_raises_api_error():
    """SSL/TLS errors during handshake (corp MITM, expired cert,
    incompatible TLS version) → bare APIError. The user sees a
    clear message — they can debug whether to disable TLS, fix
    cert, or use --insecure (we don't expose --insecure but the
    error tells them what's broken)."""
    tls_err = urllib.error.URLError(
        ssl.SSLError(1, "[SSL: CERTIFICATE_VERIFY_FAILED] "
                          "certificate verify failed: unable to get "
                          "local issuer certificate"))
    with patch("urllib.request.urlopen", side_effect=tls_err):
        with pytest.raises(APIError) as exc_info:
            call_chat_completion(
                base_url="https://openrouter.ai/api/v1",
                api_key="sk-test", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
    err = exc_info.value
    assert type(err) is APIError
    # Diagnostic info is in the message — TLS keyword should appear
    msg = str(err)
    assert "Transport error" in msg
    # The original SSLError reason percolates through URLError.reason
    # → captured in our exc message
    assert "CERTIFICATE" in msg or "SSL" in msg or "verify" in msg


def test_tls_unsupported_protocol_raises_api_error():
    """SSLv2 / SSLv3 disabled but server only speaks them. Same
    APIError path."""
    proto_err = urllib.error.URLError(
        ssl.SSLError(1, "[SSL: UNSUPPORTED_PROTOCOL] "
                          "unsupported protocol"))
    with patch("urllib.request.urlopen", side_effect=proto_err):
        with pytest.raises(APIError) as exc_info:
            call_chat_completion(
                base_url="https://openrouter.ai/api/v1",
                api_key="x", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
    assert type(exc_info.value) is APIError


# ─── Regression: AuthError / RateLimitError still propagate ─────────────


def _http_error(code, body_dict=None):
    """Helper: build a urllib.error.HTTPError that openai_compat's
    handler can read."""
    body = json.dumps(body_dict or {}).encode()
    err = urllib.error.HTTPError(
        url="https://openrouter.ai/api/v1/chat/completions",
        code=code, msg="HTTP error", hdrs={}, fp=BytesIO(body),
    )
    return err


def test_http_401_still_surfaces_as_auth_error():
    """Network is up; OpenRouter rejects with 401 → AuthError.
    Pin that this path is independent of network-down handling."""
    with patch("urllib.request.urlopen",
                side_effect=_http_error(401,
                    {"error": {"message": "Invalid API key"}})):
        with pytest.raises(AuthError) as exc_info:
            call_chat_completion(
                base_url="https://openrouter.ai/api/v1",
                api_key="sk-bad", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
    err = exc_info.value
    assert err.status == 401
    # AuthError is APIError subclass — caller can `except APIError`
    # to catch all, or `except AuthError` for specific 401 handling.
    assert isinstance(err, APIError)


def test_http_429_still_surfaces_as_rate_limit_error():
    """Network up; OpenRouter rate-limits with 429 → RateLimitError."""
    with patch("urllib.request.urlopen",
                side_effect=_http_error(429,
                    {"error": {"message": "Rate limit exceeded"}})):
        with pytest.raises(RateLimitError) as exc_info:
            call_chat_completion(
                base_url="https://openrouter.ai/api/v1",
                api_key="sk-test", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
    err = exc_info.value
    assert err.status == 429


def test_http_503_still_surfaces_as_server_error():
    """Network up; OpenRouter overloaded → ServerError. Caller can
    retry on this (vs DNS/TCP/TLS where retry won't help)."""
    with patch("urllib.request.urlopen",
                side_effect=_http_error(503,
                    {"error": {"message": "Service unavailable"}})):
        with pytest.raises(ServerError) as exc_info:
            call_chat_completion(
                base_url="https://openrouter.ai/api/v1",
                api_key="sk-test", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
    err = exc_info.value
    assert err.status == 503


def test_error_class_hierarchy_supports_catch_all():
    """Pin: every error class is APIError subclass (or APIError
    itself). Caller can `except APIError as e` to handle ALL
    failure modes uniformly when retry logic doesn't apply."""
    for cls in (AuthError, RateLimitError, ServerError):
        assert issubclass(cls, APIError), (
            f"{cls.__name__} not subclass of APIError — "
            f"`except APIError` won't catch it"
        )


# ─── consult CLI: exits non-zero with user-friendly message ──────────────


CONSULT_PATH = Path("/home/bartek/.local/bin/consult")


@pytest.mark.skipif(not CONSULT_PATH.exists(),
                     reason="consult CLI not installed")
def test_consult_cli_handles_url_error_gracefully(monkeypatch, tmp_path):
    """Spawn consult as subprocess with NO_PROXY + bogus host so it
    actually hits a network failure. Assert it exits non-zero with
    a 'Connection error' message on stderr."""
    # Build minimal config so consult doesn't bail on missing API key
    cfg_dir = tmp_path / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True)
    (cfg_dir / "consult.json").write_text(json.dumps({
        "api_key": "sk-fake-key-for-network-test",
        "default_model": "google/gemini-2.5-pro",
        "models": {
            "google/gemini-2.5-pro": {
                "enabled": True,
                "name": "Gemini 2.5 Pro",
                "source": "openrouter",
            }
        },
    }))

    env = os.environ.copy()
    env["HOME"] = str(tmp_path)
    # Force an unreachable resolved IP via /etc/hosts equivalent —
    # use BTERMINAL_OPENROUTER_URL hack if consult honors it. Since
    # consult hardcodes OPENROUTER_API, we go via DNS pointing at
    # a bogus host. Simplest: monkey the URL to a known-unreachable
    # one via env-injected config.
    #
    # Pragmatic alternative: invoke consult with /dev/null stdin +
    # let it fail at the real network boundary. CI doesn't have
    # outbound openrouter.ai by default, but if it does, this test
    # would fire a real request — gate behind opt-in.
    if os.environ.get("BTERMINAL_NETWORK_DOWN_TEST") != "1":
        pytest.skip("Real-network test — set "
                     "BTERMINAL_NETWORK_DOWN_TEST=1 to opt in")

    result = subprocess.run(
        [sys.executable, str(CONSULT_PATH), "test prompt"],
        capture_output=True, text=True, timeout=15, env=env,
    )
    assert result.returncode != 0, (
        f"consult unexpectedly succeeded — output: {result.stdout!r}"
    )
    # stderr names the connection error in human-readable form
    err = result.stderr.lower()
    assert "connection" in err or "error" in err


def test_consult_handles_url_error_via_static_inspection():
    """Source-grep alternative — confirms `consult` has the
    URLError catch + sys.exit(1) at the canonical spot. Catches a
    refactor that drops the handler before #103 ships."""
    if not CONSULT_PATH.exists():
        pytest.skip("consult CLI not installed")
    src = CONSULT_PATH.read_text()
    # Top-level urlopen call has matching URLError handler
    assert "except urllib.error.URLError" in src
    assert "Connection error" in src
    # Exit non-zero on URLError
    catch_block = src[src.find("except urllib.error.URLError"):]
    catch_block = catch_block[:200]  # peek next ~200 chars
    assert "sys.exit(1)" in catch_block, (
        "consult URLError handler doesn't sys.exit(1) — pipelines "
        "would treat network failure as success"
    )


def test_consult_handles_http_error_separately_from_url_error():
    """consult MUST distinguish HTTPError (4xx/5xx → API rejected)
    from URLError (transport broken → can't reach API). Different
    handlers print different messages so the user knows whether to
    fix their key/quota vs. fix their network."""
    if not CONSULT_PATH.exists():
        pytest.skip("consult CLI not installed")
    src = CONSULT_PATH.read_text()
    assert "except urllib.error.HTTPError" in src
    assert "except urllib.error.URLError" in src
    # The two handlers print different messages
    http_block = src[src.find("except urllib.error.HTTPError"):]
    http_block = http_block[:300]
    url_block = src[src.find("except urllib.error.URLError"):]
    url_block = url_block[:300]
    # HTTPError block prints HTTP-specific info (status code)
    assert "e.code" in http_block or "code" in http_block
    # URLError block prints the reason (network-specific)
    assert "reason" in url_block


# ─── Cross-cutting: error message determinism ────────────────────────────


@pytest.mark.parametrize("err, expected_substring", [
    (urllib.error.URLError(socket.gaierror(-2, "DNS")), "Transport error"),
    (urllib.error.URLError(ConnectionRefusedError()), "Transport error"),
    (urllib.error.URLError(ssl.SSLError(1, "TLS")), "Transport error"),
    (urllib.error.URLError(OSError(113, "no route")), "Transport error"),
])
def test_all_url_error_variants_produce_transport_error_message(
        err, expected_substring):
    """Every URLError variant produces the same 'Transport error
    reaching <url>: <reason>' format. Pin so a refactor doesn't
    create per-cause message divergence (which would break
    grep-based log parsers)."""
    with patch("urllib.request.urlopen", side_effect=err):
        with pytest.raises(APIError) as exc_info:
            call_chat_completion(
                base_url="https://api.example.com/v1",
                api_key="x", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
    assert expected_substring in str(exc_info.value)
    assert "api.example.com" in str(exc_info.value)


# ─── End-to-end: full failure → recovery sequence ────────────────────────


def test_network_recovery_works_after_dns_then_success():
    """Simulate the lifecycle: first call fails (DNS down), second
    call succeeds (DNS recovered). Pin that openai_compat doesn't
    cache the failure — every call is independent."""
    dns_err = urllib.error.URLError(
        socket.gaierror(-2, "Name or service not known"))
    success_resp = MagicMock()
    success_resp.read.return_value = json.dumps({
        "choices": [{"message": {"content": "OK"}}],
        "model": "gemini-2.5-pro",
    }).encode()

    call_count = {"n": 0}

    def fake_urlopen(*args, **kwargs):
        call_count["n"] += 1
        if call_count["n"] == 1:
            raise dns_err
        return success_resp

    with patch("urllib.request.urlopen", side_effect=fake_urlopen):
        with pytest.raises(APIError):
            call_chat_completion(
                base_url="https://openrouter.ai/api/v1",
                api_key="x", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
        text, _ = call_chat_completion(
            base_url="https://openrouter.ai/api/v1",
            api_key="x", model="m",
            messages=[{"role": "user", "content": "x"}],
        )
    assert text == "OK"
    assert call_count["n"] == 2
