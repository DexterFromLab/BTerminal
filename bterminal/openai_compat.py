"""bterminal.openai_compat — minimal OpenAI-compatible chat client.

Audit doc § 2: extracted as a reusable client so future providers
(Aider in #75, direct local-LLM integrations later) don't have to
re-implement HTTP plumbing. Pure-stdlib (urllib + json) — no
`requests` dep, no streaming for now (POST one shot, parse JSON).

Compatible with:
  - OpenRouter             (https://openrouter.ai/api/v1)
  - Ollama                 (http://localhost:11434/v1)
  - llama.cpp --api        (http://localhost:8080/v1)
  - vLLM                   (http://localhost:8000/v1)
  - Any service exposing /chat/completions with OpenAI shape.

Usage:
    from bterminal.openai_compat import call_chat_completion

    text, usage = call_chat_completion(
        base_url="http://localhost:11434/v1",
        api_key=None,                     # local LLM — no auth needed
        model="qwen2.5-coder:0.5b",
        messages=[
            {"role": "system", "content": "You are a code reviewer."},
            {"role": "user",   "content": "Review this:\\n```py\\n..."},
        ],
    )

`tools/consult` still uses its own embedded `call_openrouter` so the
binary stays standalone (consult is shipped to ~/.local/bin and may be
invoked outside the bterminal package's import path). When a feature
becomes redundant between the two, consult can grow a soft-import
shim — for now keeping them isolated avoids cross-deployment fragility.
"""
from __future__ import annotations

import json
import urllib.error
import urllib.request
from typing import Iterable, Optional


DEFAULT_TIMEOUT_SEC = 300
DEFAULT_MAX_TOKENS = 16384
DEFAULT_TEMPERATURE = 0.7


# ─── Typed exceptions ──────────────────────────────────────────────────────


class APIError(RuntimeError):
    """Base class for chat completion errors. `status` is HTTP status
    code (None for transport failures); `body` is the raw response
    body when available."""

    def __init__(self, message: str, *, status: Optional[int] = None,
                 body: str = "") -> None:
        super().__init__(message)
        self.status = status
        self.body = body


class AuthError(APIError):
    """HTTP 401 — invalid / missing API key."""


class RateLimitError(APIError):
    """HTTP 429 — quota / rate limit exceeded. Caller may retry."""


class ServerError(APIError):
    """HTTP 5xx — backend issue. Caller may retry with backoff."""


# ─── Public API ────────────────────────────────────────────────────────────


def call_chat_completion(
    base_url: str,
    api_key: Optional[str],
    model: str,
    messages: Iterable[dict],
    *,
    max_tokens: int = DEFAULT_MAX_TOKENS,
    temperature: float = DEFAULT_TEMPERATURE,
    timeout: float = DEFAULT_TIMEOUT_SEC,
    extra_headers: Optional[dict] = None,
    extra_payload: Optional[dict] = None,
) -> tuple[str, dict]:
    """One-shot non-streaming chat completion. Returns (text, usage).

    Args:
        base_url:    API root, e.g. "http://localhost:11434/v1" or
                     "https://openrouter.ai/api/v1". Trailing /chat/
                     /completions is appended automatically.
        api_key:     Bearer token; pass None or empty string for local
                     endpoints (Ollama / llama.cpp serve / vLLM
                     without auth) — Authorization header is then
                     omitted entirely.
        model:       Provider-specific model identifier. Examples:
                     "openai/gpt-4o", "qwen2.5-coder:0.5b",
                     "meta-llama/llama-4-maverick".
        messages:    OpenAI-shape list: [{"role": "system|user|
                     assistant", "content": "..."}].
        max_tokens:  Response cap. 16k default; many local models max
                     out at 4k.
        temperature: Sampling temperature.
        timeout:     Socket timeout in seconds (default 5 min — local
                     CPU inference can be slow).
        extra_headers: Per-call headers (e.g. OpenRouter's HTTP-Referer
                     / X-Title). Merged on top of the built-in set.
        extra_payload: Extra JSON keys merged into the payload (e.g.
                     `stop`, `top_p`, `presence_penalty`).

    Returns:
        (text, usage) tuple where text is the assistant's first
        response content (string, possibly empty) and usage is the
        dict from the API's `usage` field
        ({prompt_tokens, completion_tokens, total_tokens}) or {} if
        the backend doesn't report it.

    Raises:
        AuthError      on HTTP 401 (invalid/missing key)
        RateLimitError on HTTP 429
        ServerError    on HTTP 5xx
        APIError       on any other non-2xx OR transport failure
                       OR API-shape error (response has top-level
                       "error" key)
    """
    url = base_url.rstrip("/") + "/chat/completions"

    payload_dict = {
        "model": model,
        "messages": list(messages),
        "max_tokens": max_tokens,
        "temperature": temperature,
    }
    if extra_payload:
        payload_dict.update(extra_payload)
    payload = json.dumps(payload_dict).encode("utf-8")

    headers = {"Content-Type": "application/json"}
    if api_key:
        headers["Authorization"] = f"Bearer {api_key}"
    if extra_headers:
        headers.update(extra_headers)

    req = urllib.request.Request(url, data=payload, headers=headers)

    try:
        resp = urllib.request.urlopen(req, timeout=timeout)
    except urllib.error.HTTPError as exc:
        body = ""
        try:
            body = exc.read().decode("utf-8", errors="replace")
        except OSError:
            pass
        msg = f"HTTP {exc.code} from {url}: {exc.reason}"
        if exc.code == 401:
            raise AuthError(msg, status=401, body=body) from exc
        if exc.code == 429:
            raise RateLimitError(msg, status=429, body=body) from exc
        if 500 <= exc.code < 600:
            raise ServerError(msg, status=exc.code, body=body) from exc
        raise APIError(msg, status=exc.code, body=body) from exc
    except urllib.error.URLError as exc:
        raise APIError(f"Transport error reaching {url}: {exc.reason}") from exc
    except OSError as exc:
        raise APIError(f"Socket error reaching {url}: {exc}") from exc

    raw = resp.read()
    try:
        data = json.loads(raw.decode("utf-8"))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise APIError(f"Non-JSON response from {url}",
                       body=raw[:500].decode("utf-8", errors="replace")) from exc

    # Some backends (OpenRouter, ollama) wrap errors in a 200 OK with
    # an "error" object instead of using HTTP status codes.
    if isinstance(data, dict) and "error" in data and "choices" not in data:
        err = data["error"]
        msg = err.get("message", str(err)) if isinstance(err, dict) else str(err)
        raise APIError(f"API error: {msg}",
                       body=json.dumps(err) if isinstance(err, dict) else str(err))

    choices = data.get("choices") or []
    text = ""
    if choices:
        msg_obj = choices[0].get("message") or {}
        text = msg_obj.get("content", "") or ""
    usage = data.get("usage") or {}
    return text, usage


def call_simple(
    base_url: str,
    api_key: Optional[str],
    model: str,
    system_prompt: str,
    user_prompt: str,
    **kwargs,
) -> tuple[str, dict]:
    """Convenience wrapper — system + user message style (matches
    consult.call_openrouter signature for easy mental mapping).

    Equivalent to call_chat_completion with a 2-element messages list.
    """
    return call_chat_completion(
        base_url=base_url, api_key=api_key, model=model,
        messages=[
            {"role": "system", "content": system_prompt},
            {"role": "user",   "content": user_prompt},
        ],
        **kwargs,
    )


__all__ = [
    "call_chat_completion",
    "call_simple",
    "APIError",
    "AuthError",
    "RateLimitError",
    "ServerError",
    "DEFAULT_TIMEOUT_SEC",
    "DEFAULT_MAX_TOKENS",
    "DEFAULT_TEMPERATURE",
]
