"""Tests for bterminal.openai_compat (task #2 / #74).

Mock urllib so tests don't hit the network. Coverage:
  - Request shape (URL, headers, body) with + without API key
  - Response parsing (text + usage extraction)
  - Typed exception hierarchy on 401/429/500/transport/wrapped errors
  - call_simple convenience wrapper builds the right messages array
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bterminal import openai_compat as oc


# ─── Test helpers ──────────────────────────────────────────────────────────


def _mock_response(payload: dict, status: int = 200):
    """Mock urllib's urlopen(req).read() to return JSON bytes."""
    raw = json.dumps(payload).encode("utf-8")
    resp = MagicMock()
    resp.read.return_value = raw
    resp.status = status
    return resp


def _capture_request(captured: dict):
    """urlopen replacement that stashes the Request object then returns
    a stub response. Lets tests inspect URL/headers/body."""
    def _fake(req, timeout=None):
        captured["url"] = req.full_url
        captured["headers"] = dict(req.headers)
        captured["body"] = req.data.decode("utf-8") if req.data else None
        captured["timeout"] = timeout
        return _mock_response(captured.get("response_payload", {
            "choices": [{"message": {"content": "ok"}}],
            "usage": {"prompt_tokens": 10, "completion_tokens": 5},
        }))
    return _fake


# ─── Request shape ──────────────────────────────────────────────────────────


def test_call_chat_completion_appends_endpoint_to_base_url():
    captured: dict = {}
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        oc.call_chat_completion(
            base_url="https://openrouter.ai/api/v1",
            api_key="sk-test",
            model="x",
            messages=[{"role": "user", "content": "hi"}],
        )
    assert captured["url"] == "https://openrouter.ai/api/v1/chat/completions"


def test_call_chat_completion_strips_trailing_slash_in_base_url():
    captured: dict = {}
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        oc.call_chat_completion(
            base_url="http://localhost:11434/v1/",
            api_key=None,
            model="qwen2.5-coder:0.5b",
            messages=[{"role": "user", "content": "test"}],
        )
    assert captured["url"] == "http://localhost:11434/v1/chat/completions"


def test_call_chat_completion_includes_bearer_when_api_key_set():
    captured: dict = {}
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        oc.call_chat_completion(
            base_url="https://api.example.com/v1",
            api_key="sk-secret",
            model="m",
            messages=[{"role": "user", "content": "x"}],
        )
    # urllib lowercases header keys
    auth = captured["headers"].get("Authorization") \
           or captured["headers"].get("authorization")
    assert auth == "Bearer sk-secret"


def test_call_chat_completion_omits_auth_header_for_local_endpoint():
    """Local LLM (Ollama / llama.cpp) accepts no auth — header MUST
    be absent so the request matches what the daemon expects."""
    captured: dict = {}
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        oc.call_chat_completion(
            base_url="http://localhost:11434/v1",
            api_key=None,
            model="qwen2.5-coder:0.5b",
            messages=[{"role": "user", "content": "x"}],
        )
    headers_lc = {k.lower() for k in captured["headers"]}
    assert "authorization" not in headers_lc


def test_call_chat_completion_omits_auth_header_for_empty_api_key():
    """Empty string treated same as None — no header."""
    captured: dict = {}
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        oc.call_chat_completion(
            base_url="http://localhost:11434/v1",
            api_key="",
            model="x",
            messages=[{"role": "user", "content": "x"}],
        )
    headers_lc = {k.lower() for k in captured["headers"]}
    assert "authorization" not in headers_lc


def test_call_chat_completion_payload_includes_messages_and_params():
    captured: dict = {}
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        oc.call_chat_completion(
            base_url="https://api.example.com/v1",
            api_key="k",
            model="my-model",
            messages=[
                {"role": "system", "content": "S"},
                {"role": "user",   "content": "U"},
            ],
            max_tokens=2000,
            temperature=0.3,
        )
    body = json.loads(captured["body"])
    assert body["model"] == "my-model"
    assert body["max_tokens"] == 2000
    assert body["temperature"] == 0.3
    assert body["messages"] == [
        {"role": "system", "content": "S"},
        {"role": "user",   "content": "U"},
    ]


def test_extra_headers_merged_into_request():
    captured: dict = {}
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        oc.call_chat_completion(
            base_url="https://openrouter.ai/api/v1",
            api_key="k", model="x",
            messages=[{"role": "user", "content": "x"}],
            extra_headers={
                "HTTP-Referer": "https://github.com/X/Y",
                "X-Title": "Test",
            },
        )
    # urllib lowercases header keys when passed via Request.headers
    keys = {k.lower(): v for k, v in captured["headers"].items()}
    assert keys.get("http-referer") == "https://github.com/X/Y"
    assert keys.get("x-title") == "Test"


def test_extra_payload_merged_into_body():
    captured: dict = {}
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        oc.call_chat_completion(
            base_url="https://api.example.com/v1",
            api_key="k", model="m",
            messages=[{"role": "user", "content": "x"}],
            extra_payload={"top_p": 0.9, "stop": ["\\n\\n"]},
        )
    body = json.loads(captured["body"])
    assert body["top_p"] == 0.9
    assert body["stop"] == ["\\n\\n"]


def test_timeout_passed_to_urlopen():
    captured: dict = {}
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        oc.call_chat_completion(
            base_url="https://api.example.com/v1",
            api_key="k", model="m",
            messages=[{"role": "user", "content": "x"}],
            timeout=42.0,
        )
    assert captured["timeout"] == 42.0


# ─── Response parsing ──────────────────────────────────────────────────────


def test_call_chat_completion_returns_text_and_usage():
    captured: dict = {
        "response_payload": {
            "choices": [{
                "message": {"content": "Hello, world!"},
            }],
            "usage": {
                "prompt_tokens": 8,
                "completion_tokens": 3,
                "total_tokens": 11,
            },
        }
    }
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        text, usage = oc.call_chat_completion(
            base_url="https://api.example.com/v1",
            api_key="k", model="m",
            messages=[{"role": "user", "content": "x"}],
        )
    assert text == "Hello, world!"
    assert usage == {"prompt_tokens": 8, "completion_tokens": 3,
                     "total_tokens": 11}


def test_returns_empty_text_when_choices_array_missing():
    """Defensive: bad backends or partial responses don't crash."""
    captured: dict = {"response_payload": {}}
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        text, usage = oc.call_chat_completion(
            base_url="http://x", api_key=None, model="m",
            messages=[{"role": "user", "content": "x"}],
        )
    assert text == ""
    assert usage == {}


def test_returns_empty_when_message_content_null():
    captured: dict = {
        "response_payload": {
            "choices": [{"message": {"content": None}}],
        }
    }
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        text, _ = oc.call_chat_completion(
            base_url="http://x", api_key=None, model="m",
            messages=[{"role": "user", "content": "x"}],
        )
    assert text == ""


# ─── Typed errors ──────────────────────────────────────────────────────────


def _http_error(status: int, body: bytes = b""):
    """Build urllib.error.HTTPError that mimics a real raised error."""
    err = oc.urllib.error.HTTPError(
        url="http://x", code=status, msg="Test", hdrs=None,  # type: ignore
        fp=io.BytesIO(body),
    )
    return err


def test_401_raises_auth_error():
    def _raise(req, timeout=None):
        raise _http_error(401, b'{"error":{"message":"bad key"}}')
    with patch.object(oc.urllib.request, "urlopen", _raise):
        with pytest.raises(oc.AuthError) as ei:
            oc.call_chat_completion(
                base_url="http://x", api_key="bad", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
        assert ei.value.status == 401
        assert "bad key" in ei.value.body


def test_429_raises_rate_limit_error():
    def _raise(req, timeout=None):
        raise _http_error(429, b"too many")
    with patch.object(oc.urllib.request, "urlopen", _raise):
        with pytest.raises(oc.RateLimitError) as ei:
            oc.call_chat_completion(
                base_url="http://x", api_key="k", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
        assert ei.value.status == 429


@pytest.mark.parametrize("status", [500, 502, 503, 504])
def test_5xx_raises_server_error(status):
    def _raise(req, timeout=None):
        raise _http_error(status, b"backend down")
    with patch.object(oc.urllib.request, "urlopen", _raise):
        with pytest.raises(oc.ServerError) as ei:
            oc.call_chat_completion(
                base_url="http://x", api_key="k", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
        assert ei.value.status == status


def test_other_4xx_raises_generic_api_error():
    """Non-401/429 4xx → APIError (e.g. 400 bad request)."""
    def _raise(req, timeout=None):
        raise _http_error(400, b"malformed")
    with patch.object(oc.urllib.request, "urlopen", _raise):
        with pytest.raises(oc.APIError) as ei:
            oc.call_chat_completion(
                base_url="http://x", api_key="k", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
        assert ei.value.status == 400
        # Must NOT be one of the typed subclasses
        assert not isinstance(ei.value, (oc.AuthError, oc.RateLimitError,
                                         oc.ServerError))


def test_transport_error_raises_api_error():
    """Connection refused → APIError, status=None."""
    def _raise(req, timeout=None):
        raise oc.urllib.error.URLError("Connection refused")
    with patch.object(oc.urllib.request, "urlopen", _raise):
        with pytest.raises(oc.APIError) as ei:
            oc.call_chat_completion(
                base_url="http://localhost:99999/v1",
                api_key=None, model="m",
                messages=[{"role": "user", "content": "x"}],
            )
        assert ei.value.status is None
        assert "Transport error" in str(ei.value)


def test_wrapped_error_in_200_response_raises_api_error():
    """Some backends return HTTP 200 with {error: {...}} body — still
    must be raised as APIError."""
    captured: dict = {
        "response_payload": {
            "error": {"message": "you ran out of credits"},
        }
    }
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        with pytest.raises(oc.APIError) as ei:
            oc.call_chat_completion(
                base_url="http://x", api_key="k", model="m",
                messages=[{"role": "user", "content": "x"}],
            )
        assert "credits" in str(ei.value)


def test_non_json_response_raises_api_error():
    """Wrong content-type / HTML error page from a proxy."""
    raw_resp = MagicMock()
    raw_resp.read.return_value = b"<html>503 backend</html>"
    with patch.object(oc.urllib.request, "urlopen",
                      lambda req, timeout=None: raw_resp):
        with pytest.raises(oc.APIError) as ei:
            oc.call_chat_completion(
                base_url="http://x", api_key=None, model="m",
                messages=[{"role": "user", "content": "x"}],
            )
        assert "Non-JSON" in str(ei.value)


# ─── call_simple convenience ───────────────────────────────────────────────


def test_call_simple_builds_two_message_array():
    captured: dict = {}
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        oc.call_simple(
            base_url="https://api.example.com/v1",
            api_key="k",
            model="m",
            system_prompt="System rule",
            user_prompt="User question",
        )
    body = json.loads(captured["body"])
    assert body["messages"] == [
        {"role": "system", "content": "System rule"},
        {"role": "user",   "content": "User question"},
    ]


def test_call_simple_passes_kwargs_through():
    captured: dict = {}
    with patch.object(oc.urllib.request, "urlopen", _capture_request(captured)):
        oc.call_simple(
            base_url="http://x", api_key=None, model="m",
            system_prompt="S", user_prompt="U",
            temperature=0.1, max_tokens=500,
        )
    body = json.loads(captured["body"])
    assert body["temperature"] == 0.1
    assert body["max_tokens"] == 500


# ─── Exception class hierarchy sanity ──────────────────────────────────────


def test_typed_exceptions_inherit_from_api_error():
    """Calls handling APIError must catch the typed subclasses too —
    isinstance check encodes the contract."""
    assert issubclass(oc.AuthError, oc.APIError)
    assert issubclass(oc.RateLimitError, oc.APIError)
    assert issubclass(oc.ServerError, oc.APIError)


def test_api_error_carries_status_and_body():
    e = oc.APIError("test", status=404, body="nope")
    assert e.status == 404
    assert e.body == "nope"
    assert "test" in str(e)


def test_api_error_defaults():
    e = oc.APIError("plain")
    assert e.status is None
    assert e.body == ""
