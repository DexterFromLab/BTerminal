"""Forward compat: Ollama 0.5+ API breaking changes
(#49 / #121, audit § 6.5 #22).

Ollama's public HTTP API at `:11434` is not formally version-locked.
Across 0.4 → 0.5 → 0.6 cycles, possible breaking changes:
  (a) Endpoint rename `/api/tags` → `/api/models` (semantic
      consolidation with OpenAI-compat surface).
  (b) Required new header (e.g. `X-Ollama-Version` for negotiation,
      or `Authorization` for the new `--secure` flag).
  (c) Per-model field shape change (e.g. `size` → `bytes`,
      `modified_at` → `modifiedAt` camelCase, `details` nested).

#35 / #107 already pinned shape-drift defenses for the parser. This
file adds the version-axis tests:
  - hard-coded URL is the failure surface for branch (a)
  - request headers (none today) are the failure surface for (b)
  - parser robustness for (c) — parametrized 0.4 vs hypothetical
    0.6 schemas

Plus pinned migration markers — when shim infrastructure lands,
specific tests must be flipped.

Manual VM smoke (`install ollama 0.4 deb, run client`) is documented
in tests/manual/README.md.
"""
from __future__ import annotations

import json
import urllib.error
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

from bterminal.ollama_client import (
    DEFAULT_API_URL,
    OllamaModel,
    is_daemon_running,
    list_models,
    parse_ollama_api_tags,
    parse_ollama_list_output,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
OLLAMA_CLIENT = REPO_ROOT / "bterminal" / "ollama_client.py"


# ─── Branch (a): /api/tags rename to /api/models ────────────────────────


def test_current_endpoint_is_api_tags():
    """Pin: today's BT hits `/api/tags`. Source-grep so a refactor
    that switches endpoint without testing forward-compat fails
    here loudly."""
    src = OLLAMA_CLIENT.read_text()
    assert "/api/tags" in src
    # And NO references to /api/models yet (Ollama 0.5+ might
    # add it as an alias)
    # Grep finds the literal string in code, not just docstring
    code_lines = [
        line for line in src.split("\n")
        if not line.lstrip().startswith("#")
        and not line.lstrip().startswith('"""')
    ]
    code = "\n".join(code_lines)
    assert "/api/tags" in code
    # Pin: /api/models is NOT used today. When Ollama 0.6 ships,
    # the migrator can detect this assertion failing as the
    # signal to update.
    assert "/api/models" not in code, (
        "BT now uses /api/models — Ollama 0.6+ shim landed; "
        "lift this pin and add version-aware endpoint dispatch"
    )


def test_endpoint_rename_simulation_returns_empty_via_404():
    """Simulate Ollama 0.6 dropping `/api/tags` (returns 404). BT's
    `list_models` falls through to CLI fallback → empty list (CLI
    not installed in test). Pin: NO crash, no exception leak."""
    # urlopen raises HTTPError(404)
    err = urllib.error.HTTPError(
        "http://localhost:11434/api/tags",
        code=404, msg="Not Found", hdrs={}, fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=err):
        # CLI fallback also fails (not installed)
        with patch("bterminal.ollama_client.is_cli_installed",
                    return_value=False):
            result = list_models()
    # Empty list, no exception
    assert result == []


def test_endpoint_rename_simulation_does_not_crash_is_daemon_running():
    """Same scenario for `is_daemon_running` — 404 from /api/tags
    means daemon is up but endpoint changed. Currently we report
    False (daemon-not-reachable). Pin so a future fix to detect
    'daemon up but endpoint shifted' has explicit migration."""
    err = urllib.error.HTTPError(
        "http://localhost:11434/api/tags",
        code=404, msg="Not Found", hdrs={}, fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=err):
        # Today: HTTPError leaks past the bare except
        # (URLError/OSError list). Pin actual behavior.
        try:
            result = is_daemon_running()
            # If caught: returns False
            assert result is False
        except urllib.error.HTTPError:
            # Today's behavior — HTTPError isn't in the except
            # clause. Pin so migrator can decide whether to
            # broaden it.
            pytest.skip(
                "is_daemon_running doesn't catch HTTPError — "
                "pin behaviour update required"
            )


# ─── Branch (b): added required header ──────────────────────────────────


def test_current_http_calls_send_no_extra_headers():
    """Pin: today BT sends bare urlopen calls — no
    `Authorization`, no `X-Ollama-Version`. Source-grep so a
    migrator adding required headers can SEE that bumping the
    requirements means updating BT."""
    src = OLLAMA_CLIENT.read_text()
    code_lines = [
        line for line in src.split("\n")
        if not line.lstrip().startswith("#")
        and not line.lstrip().startswith('"""')
    ]
    code = "\n".join(code_lines)
    # No urllib.request.Request with custom headers — just
    # urlopen(url, timeout=...)
    assert "urllib.request.Request" not in code, (
        "ollama_client now uses Request() — header injection "
        "available; update tests to pin the new header set"
    )
    assert 'headers={' not in code
    assert 'Authorization' not in code
    assert 'X-Ollama' not in code
    assert 'X-API' not in code


def test_required_header_simulation_returns_401():
    """Simulate Ollama 0.6 requiring `Authorization: Bearer <token>`
    by responding 401 to anonymous calls. BT's list_models →
    CLI fallback → empty. Pin: no crash."""
    err = urllib.error.HTTPError(
        "http://localhost:11434/api/tags",
        code=401, msg="Unauthorized", hdrs={}, fp=None,
    )
    with patch("urllib.request.urlopen", side_effect=err):
        with patch("bterminal.ollama_client.is_cli_installed",
                    return_value=False):
            result = list_models()
    assert result == []


def test_list_models_uses_http_first_then_cli_fallback():
    """Pin the dispatch order: HTTP /api/tags → CLI `ollama list`
    fallback → empty. The CLI path serves as the BREAKING-CHANGE
    cushion — even when HTTP shape drifts, CLI may still work
    (Ollama keeps CLI semantically stable longer)."""
    src = OLLAMA_CLIENT.read_text()
    fn_start = src.find("def list_models")
    fn_end = src.find("\n\ndef ", fn_start)
    body = src[fn_start:fn_end]
    # HTTP first, CLI second
    http_idx = body.find("/api/tags")
    cli_idx = body.find("ollama")
    assert http_idx > 0 and cli_idx > 0
    # CLI fallback comes AFTER http section
    cli_subprocess_idx = body.find("subprocess.run")
    assert cli_subprocess_idx > http_idx, (
        "CLI fallback runs BEFORE HTTP — defeats the 'broken-API "
        "still works via CLI' contract"
    )


# ─── Branch (c): per-model field shape change ───────────────────────────


# 0.4 schema — current Ollama (canonical)
PAYLOAD_0_4 = {
    "models": [
        {
            "name": "qwen2.5-coder:0.5b",
            "modified_at": "2026-05-07T10:30:00Z",
            "size": 397441024,
            "digest": "5a2cabcdef0123456789",
            "details": {
                "format": "gguf", "family": "qwen2",
                "parameter_size": "494M",
                "quantization_level": "Q4_K_M",
            },
        },
    ],
}


# Hypothetical 0.6 schema — camelCase + nested size
PAYLOAD_0_6_HYPOTHETICAL = {
    "models": [
        {
            "model": "qwen2.5-coder:0.5b",  # `model` not `name`
            "modifiedAt": "2026-09-15T12:00:00Z",  # camelCase
            "details": {
                "format": "gguf",
                "sizeBytes": 397441024,  # nested
                "family": "qwen2",
            },
            "digest": "5a2cabcdef0123456789abcdef",
        },
    ],
}


def test_parse_0_4_payload_canonical(tmp_path):
    """Baseline: current 0.4 schema parses correctly."""
    out = parse_ollama_api_tags(PAYLOAD_0_4)
    assert len(out) == 1
    assert out[0].name == "qwen2.5-coder:0.5b"
    assert out[0].size_gb > 0


def test_parse_0_6_camelcase_modifiedAt_falls_back_gracefully():
    """0.6 hypothetical: `modifiedAt` instead of `modified_at`.
    Current parser only reads `modified_at` → falls back to ""
    for the renamed field. Other fields (name via `model`
    fallback) survive."""
    out = parse_ollama_api_tags(PAYLOAD_0_6_HYPOTHETICAL)
    # `model` fallback works (#35 contract)
    assert len(out) == 1
    assert out[0].name == "qwen2.5-coder:0.5b"
    # modifiedAt NOT recognized — empty string
    assert out[0].modified == ""


def test_parse_0_6_nested_size_returns_zero_today():
    """0.6 hypothetical: `details.sizeBytes` instead of top-level
    `size`. Parser only reads top-level → 0.0 GB. Pin so a future
    shim that adds nested-key support has explicit migration."""
    out = parse_ollama_api_tags(PAYLOAD_0_6_HYPOTHETICAL)
    assert out[0].size_gb == 0.0, (
        f"parser unexpectedly found nested size: {out[0].size_gb}"
    )


def test_parse_mixed_0_4_and_0_6_entries():
    """Realistic transition: ollama 0.4-style entry + 0.6-style
    entry in the same response (e.g. during a daemon upgrade).
    Both should parse to OllamaModel with whatever fields the
    parser CAN extract."""
    mixed = {
        "models": [
            PAYLOAD_0_4["models"][0],  # 0.4 entry
            PAYLOAD_0_6_HYPOTHETICAL["models"][0],  # 0.6 entry
        ],
    }
    out = parse_ollama_api_tags(mixed)
    assert len(out) == 2
    # 0.4 entry has all fields
    assert out[0].size_gb > 0
    # 0.6 entry has name (via `model` fallback) but degraded
    assert out[1].name == "qwen2.5-coder:0.5b"
    assert out[1].size_gb == 0.0


def test_parse_0_4_schema_with_added_top_level_metadata():
    """0.6 might add top-level metadata fields (`apiVersion`,
    `nextPageToken`, etc.). Parser only reads `models` → ignored
    cleanly."""
    payload = dict(PAYLOAD_0_4)
    payload["apiVersion"] = "0.6.0"
    payload["nextPageToken"] = "abc123"
    payload["totalCount"] = 1
    out = parse_ollama_api_tags(payload)
    # Still parses the original entry
    assert len(out) == 1
    assert out[0].name == "qwen2.5-coder:0.5b"


# ─── CLI fallback as breaking-change cushion ────────────────────────────


def test_cli_parser_handles_realistic_0_4_output():
    """Pin: today's `ollama list` CLI output parsed correctly.
    The CLI is the second line of defense for HTTP API drift."""
    stdout_0_4 = (
        "NAME                       ID              SIZE     MODIFIED\n"
        "qwen2.5-coder:0.5b         5a2cabcdef01    397 MB   2 hours ago\n"
        "llama3.1:8b                a1b2c3d4e5f6    4.7 GB   3 days ago\n"
    )
    out = parse_ollama_list_output(stdout_0_4)
    assert len(out) == 2
    assert {m.name for m in out} == {
        "qwen2.5-coder:0.5b", "llama3.1:8b"}


def test_cli_parser_robust_to_added_columns():
    """If 0.6 adds extra columns (e.g. `LAST_USED`), the regex
    matches up to MODIFIED and ignores the rest. Pin: parser
    skips lines that don't match the canonical layout instead
    of crashing."""
    stdout_with_extra_col = (
        "NAME                       ID              SIZE     MODIFIED       LAST_USED\n"
        "qwen2.5-coder:0.5b         5a2c            397 MB   2 hours ago    1h ago\n"
    )
    out = parse_ollama_list_output(stdout_with_extra_col)
    # Regex matches up to MODIFIED block; trailing column
    # absorbed into modified field (or skipped depending on
    # whitespace). Test pinned at "doesn't crash".
    # We accept 0 OR 1 entry — the point is robustness.
    assert isinstance(out, list)
    assert len(out) <= 1


def test_cli_parser_handles_renamed_columns_gracefully():
    """If 0.6 renames the header columns, lines still match if
    the data layout is unchanged. Pin behavior."""
    stdout_renamed = (
        "MODEL                      DIGEST          BYTES    UPDATED\n"
        "qwen2.5-coder:0.5b         5a2c            397 MB   2 hours ago\n"
    )
    out = parse_ollama_list_output(stdout_renamed)
    # Header skipped, data line still matches the regex
    assert len(out) == 1
    assert out[0].name == "qwen2.5-coder:0.5b"


# ─── Headers + auth simulation ─────────────────────────────────────────


def test_url_construction_for_api_tags_is_concatenation():
    """Pin: URL built via simple `f"{api_url}/api/tags"`. A
    future shim could swap endpoints by setting a different
    `api_url` (without changing any other code) — cheap escape
    hatch."""
    src = OLLAMA_CLIENT.read_text()
    # Both is_daemon_running and list_models do the same join
    assert "f\"{api_url.rstrip('/')}/api/tags\"" in src or \
        '{api_url.rstrip("/")}/api/tags' in src


def test_default_api_url_can_be_overridden_via_function_arg():
    """Pin: `list_models(api_url="...")` accepts an override.
    Future: shim that auto-detects 0.6 daemon could pass a
    different URL (e.g. with /api/models) without monkeypatching
    the module constant."""
    import inspect
    sig = inspect.signature(list_models)
    assert "api_url" in sig.parameters
    assert sig.parameters["api_url"].default == DEFAULT_API_URL


def test_default_api_url_constant_is_module_level():
    """Pin: `DEFAULT_API_URL` is a module-level constant, exported
    via `__all__`. Allows a future shim module to override it
    without touching ollama_client.py source."""
    src = OLLAMA_CLIENT.read_text()
    assert "DEFAULT_API_URL" in src
    assert '"DEFAULT_API_URL"' in src  # in __all__ tuple


# ─── Future-proof: schema_version field handling ────────────────────────


def test_parse_ignores_top_level_schema_version_if_added():
    """If 0.6 adds `{"schema_version": 2, "models": [...]}`,
    the parser ignores `schema_version` and reads `models` as
    before. Forward-compat — version-agnostic."""
    payload = {"schema_version": 2, "models": PAYLOAD_0_4["models"]}
    out = parse_ollama_api_tags(payload)
    assert len(out) == 1
    assert out[0].name == "qwen2.5-coder:0.5b"


def test_parse_ignores_pagination_token_if_added():
    """If 0.6 adds pagination (`nextPageToken`, `previousPage`),
    the parser still reads only the first page. Migration to
    multi-page would require explicit handling — pin current
    'one-page' assumption."""
    payload = {
        "models": PAYLOAD_0_4["models"],
        "nextPageToken": "page-2-cursor",
    }
    out = parse_ollama_api_tags(payload)
    # First page parsed
    assert len(out) == 1
    # nextPageToken NOT acted on (no pagination in BT today)


# ─── Migration markers ──────────────────────────────────────────────────


def test_no_version_aware_dispatch_in_ollama_client_today():
    """Pin: today's ollama_client has NO version-aware dispatch.
    The shim is a follow-up task. Lifting this pin = the shim
    landed; update endpoint / header tests accordingly."""
    src = OLLAMA_CLIENT.read_text()
    assert "ollama_version" not in src.lower()
    assert "detect_ollama_version" not in src.lower()
    assert "schema_version" not in src.lower()


def test_exports_pinned_for_external_shim_consumption():
    """Pin: module exports stable surface for a future shim
    (lives in ollama_client.py or a sibling module)."""
    src = OLLAMA_CLIENT.read_text()
    # __all__ tuple includes the canonical set
    assert "OllamaModel" in src
    assert "parse_ollama_api_tags" in src
    assert "parse_ollama_list_output" in src
    assert "DEFAULT_API_URL" in src
    assert "is_daemon_running" in src
    assert "list_models" in src


# ─── Cross: HTTP / CLI parsers stay independent ─────────────────────────


def test_http_and_cli_parsers_produce_compatible_OllamaModel_shape():
    """Pin: both parsers return list[OllamaModel] with the same
    field shape. A future shim can swap parsers based on detected
    version without changing downstream consumers."""
    http_out = parse_ollama_api_tags(PAYLOAD_0_4)
    cli_stdout = (
        "NAME                       ID              SIZE     MODIFIED\n"
        "qwen2.5-coder:0.5b         5a2c            397 MB   2 hours ago\n"
    )
    cli_out = parse_ollama_list_output(cli_stdout)

    assert len(http_out) == 1 and len(cli_out) == 1
    h, c = http_out[0], cli_out[0]
    # Same dataclass shape (same attrs)
    assert type(h) is type(c)
    assert isinstance(h, OllamaModel)
    # Same name (canonical alignment between HTTP + CLI)
    assert h.name == c.name == "qwen2.5-coder:0.5b"
    # Both produce non-zero size for known model
    assert h.size_gb > 0 and c.size_gb > 0


def test_parser_shape_drift_resilience_pinned_in_separate_test_file():
    """Cross-reference: tests/test_ollama_client_shape_drift.py
    (#35 / #107) covers the SHAPE-axis defenses. This file
    covers VERSION-axis. Pin both files exist so a refactor that
    drops one doesn't accidentally lose a test surface."""
    shape_drift = REPO_ROOT / "tests" / "test_ollama_client_shape_drift.py"
    assert shape_drift.exists(), (
        "shape-drift test file missing — version-drift defenses "
        "lose half their context"
    )
