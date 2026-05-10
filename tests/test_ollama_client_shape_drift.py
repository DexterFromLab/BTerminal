"""Failure mode: Ollama API shape change (#35 / #107, audit § 6.1 #8).

Ollama's `:11434/api/tags` schema is not formally locked across
versions. BT's `parse_ollama_api_tags` must be defensive against:

  1. Wrapping object change — root key renames (`models` →
     `model_list`), wrapped to None, or replaced by an array root.
  2. Per-model field rename — `name` → `model`, `size` → `bytes`,
     `modified_at` removed.
  3. Version field added — extra fields in payload (forward-compat:
     ignore unknown).

The pinned contract: parser NEVER raises, ALWAYS returns a (possibly
empty) `list[OllamaModel]`. Caller (OptionsDialog) renders an empty
list as "no models pulled yet" — same UX as a fresh install.

Three decision branches mapped to the parser code:
  (a) Wrapping object change — `payload.get("models") or []`
      defends against missing/None root key.
  (b) Per-model field rename — `m.get("name") or m.get("model")`
      tries both common names; `m.get("size") or 0` defaults zero.
  (c) Version field added — extra dict keys ignored, no crash.

Manual VM smoke (`OLLAMA_VERSION=0.5.0 ollama list` then BT's
options dialog) is documented in tests/manual/README.md. Headless
tests below feed crafted payloads to the pure parser without any
ollama daemon.
"""
from __future__ import annotations

import pytest

from bterminal.ollama_client import (
    OllamaModel,
    parse_ollama_api_tags,
    parse_ollama_list_output,
)


# ─── Baseline: canonical v0.4+ shape works ───────────────────────────────


_CANONICAL_PAYLOAD = {
    "models": [
        {
            "name": "qwen2.5-coder:0.5b",
            "modified_at": "2026-05-07T10:30:00Z",
            "size": 397441024,
            "digest": "5a2cabcdef0123456789",
            "details": {"family": "qwen2"},
        },
        {
            "name": "llama3.1:8b",
            "modified_at": "2026-05-04T08:15:00Z",
            "size": 4_700_000_000,
            "digest": "a1b2c3d4e5f6",
        },
    ],
}


def test_canonical_shape_returns_two_models():
    """Sanity baseline — known-good payload parses correctly."""
    models = parse_ollama_api_tags(_CANONICAL_PAYLOAD)
    assert len(models) == 2
    assert models[0].name == "qwen2.5-coder:0.5b"
    assert models[0].size_gb == 0.4  # 397M ≈ 0.4 GB
    assert models[1].name == "llama3.1:8b"


# ─── (a) Wrapping object change ──────────────────────────────────────────


def test_models_key_missing_returns_empty_list():
    """Payload without the 'models' wrapper at all (renamed key,
    or schema dropped wrapping). `get("models") or []` defends."""
    out = parse_ollama_api_tags({})
    assert out == []


def test_models_key_set_to_none_returns_empty_list():
    """`{"models": null}` — explicit null payload (server bug, or
    'no models yet' representation in some versions). The `or []`
    fallback catches None."""
    out = parse_ollama_api_tags({"models": None})
    assert out == []


def test_renamed_root_key_returns_empty_list():
    """Future schema renames `models` → `model_list`. Parser sees
    no `models` key, returns []. Caller knows to surface 'unknown
    schema' rather than crashing."""
    out = parse_ollama_api_tags({
        "model_list": [{"name": "qwen2.5-coder:0.5b", "size": 100}]
    })
    assert out == []


def test_array_root_payload_returns_empty_list():
    """If a future schema flattens to `[{...}, {...}]` at root
    (no wrapper), our parser receives a list as `payload`. Calling
    `.get` on a list raises AttributeError — defensive contract
    must catch this. Today's parser raises here; we pin the
    actual current behavior so #107's fix has a clear baseline."""
    array_payload = [
        {"name": "qwen", "size": 100},
    ]
    # Current behavior: .get on list raises AttributeError. Pin
    # this so we know to add `if isinstance(payload, dict)` guard
    # in #107's fix.
    with pytest.raises(AttributeError):
        parse_ollama_api_tags(array_payload)  # type: ignore[arg-type]


def test_models_value_is_dict_not_list_returns_empty():
    """A future change might wrap each model in another dict
    keyed by name: `{"models": {"qwen": {...}}}`. Iterating a
    dict yields keys (strings), not dicts — `isinstance(m, dict)`
    check skips them. Verify."""
    out = parse_ollama_api_tags({
        "models": {"qwen2.5-coder:0.5b": {"size": 100}}
    })
    # Iterating dict gives keys ("qwen2.5-coder:0.5b") — strings,
    # filtered out by isinstance(m, dict)
    assert out == []


def test_models_value_is_string_returns_empty():
    """Pathological: server returned `{"models": "error"}` (string
    instead of list). Iteration over string gives chars — also
    filtered by isinstance(m, dict)."""
    out = parse_ollama_api_tags({"models": "no models pulled yet"})
    assert out == []


def test_models_value_is_int_raises_or_returns_empty():
    """Even more pathological — non-iterable in models slot. Pin
    actual behavior so #107's fix can decide whether to add an
    isinstance(value, list) guard."""
    # Current: `for m in non_iterable` raises TypeError. Pin.
    with pytest.raises(TypeError):
        parse_ollama_api_tags({"models": 42})  # type: ignore[dict-item]


# ─── (b) Per-model field rename ──────────────────────────────────────────


def test_per_model_name_key_uses_fallback_to_model():
    """Newer ollama versions use `model` instead of `name` in some
    contexts. Parser tries `m.get("name") or m.get("model")`."""
    out = parse_ollama_api_tags({
        "models": [
            {"model": "qwen2.5-coder:0.5b", "size": 100},
        ]
    })
    assert len(out) == 1
    assert out[0].name == "qwen2.5-coder:0.5b"


def test_per_model_no_name_or_model_skipped():
    """If neither `name` NOR `model` is present, parser skips that
    entry rather than emitting a nameless OllamaModel."""
    out = parse_ollama_api_tags({
        "models": [
            {"size": 100},  # no name/model
            {"name": "real-model", "size": 50},
        ]
    })
    # Only the named entry survives
    assert len(out) == 1
    assert out[0].name == "real-model"


def test_size_field_missing_defaults_to_zero():
    """Future schema removes `size` field — parser defaults to 0."""
    out = parse_ollama_api_tags({
        "models": [
            {"name": "minimal-model"},
        ]
    })
    assert len(out) == 1
    assert out[0].size_gb == 0.0


def test_size_field_set_to_none_defaults_to_zero():
    """Explicit None for size — `m.get("size") or 0` catches it."""
    out = parse_ollama_api_tags({
        "models": [
            {"name": "qwen", "size": None},
        ]
    })
    assert out[0].size_gb == 0.0


def test_modified_at_missing_returns_empty_string():
    """Future schema drops `modified_at`. Parser falls back to
    empty string — no exception."""
    out = parse_ollama_api_tags({
        "models": [
            {"name": "qwen", "size": 100},  # no modified_at
        ]
    })
    assert out[0].modified == ""


def test_modified_at_non_string_value_coerced():
    """Server returned a number for modified_at (e.g. unix epoch).
    `str()` coerces, [:19] truncates. No crash."""
    out = parse_ollama_api_tags({
        "models": [
            {"name": "qwen", "size": 100, "modified_at": 1714723200},
        ]
    })
    # Coerced to string, truncated to 19 chars (or shorter)
    assert isinstance(out[0].modified, str)


def test_digest_missing_returns_empty_string():
    """No digest field → empty string. Pin baseline."""
    out = parse_ollama_api_tags({
        "models": [{"name": "qwen", "size": 100}],
    })
    assert out[0].digest == ""


def test_digest_non_string_coerced_and_truncated():
    """Pin: digest coerced to str + truncated to 12 chars (sha
    prefix display convention)."""
    out = parse_ollama_api_tags({
        "models": [
            {"name": "qwen", "size": 100,
             "digest": "0123456789abcdef0123456789abcdef"},
        ]
    })
    assert out[0].digest == "0123456789ab"  # 12 chars
    assert len(out[0].digest) == 12


# ─── (c) Version field added: forward-compat (ignore unknown) ────────────


def test_version_field_added_to_payload_ignored():
    """Future schema adds top-level metadata keys. Parser only reads
    `models` — extra keys silently ignored."""
    out = parse_ollama_api_tags({
        "models": [{"name": "qwen", "size": 100}],
        "version": "0.6.0",
        "schema_version": 2,
        "build_info": {"commit": "abc123"},
    })
    assert len(out) == 1
    assert out[0].name == "qwen"


def test_extra_per_model_fields_ignored():
    """Per-model: future versions add `quantization`, `template`,
    `last_used_at`. Parser reads only the fields it needs;
    forward-compat clean."""
    out = parse_ollama_api_tags({
        "models": [
            {
                "name": "qwen2.5-coder:0.5b",
                "size": 397441024,
                "modified_at": "2026-05-07T10:30:00Z",
                "digest": "5a2cabcdef01",
                # Future fields — parser ignores all
                "quantization": "Q4_K_M",
                "template": "<|im_start|>...",
                "last_used_at": "2026-05-07T10:35:00Z",
                "context_window": 32768,
            }
        ]
    })
    assert len(out) == 1
    assert out[0].name == "qwen2.5-coder:0.5b"
    assert out[0].size_gb > 0


# ─── Pathological inputs: parser stays alive ────────────────────────────


def test_empty_models_list_returns_empty():
    """`{"models": []}` is the canonical 'no models pulled' state."""
    out = parse_ollama_api_tags({"models": []})
    assert out == []


def test_models_with_mixed_valid_invalid_entries():
    """Some entries dict, some string, some None. Parser keeps
    only the valid dicts with names."""
    out = parse_ollama_api_tags({
        "models": [
            {"name": "valid-1", "size": 100},
            "garbage-string",
            None,
            {"size": 50},  # missing name
            42,
            {"model": "valid-2", "size": 200},
        ]
    })
    assert len(out) == 2
    names = {m.name for m in out}
    assert names == {"valid-1", "valid-2"}


def test_deeply_nested_size_handled():
    """If a future schema nests size in `details.size_bytes`, our
    parser misses it — but doesn't crash. Pin actual behavior so
    #107's fix can decide whether to crawl deeper."""
    out = parse_ollama_api_tags({
        "models": [
            {"name": "deep", "details": {"size_bytes": 1024 * 1024}},
        ]
    })
    # Parser only looks at top-level `size`, defaults to 0
    assert out[0].size_gb == 0.0


def test_unicode_name_survives():
    """Sanity — non-ASCII model name (unlikely but defensible)
    round-trips."""
    out = parse_ollama_api_tags({
        "models": [{"name": "qwen-prèmium-é:0.5b", "size": 100}]
    })
    assert out[0].name == "qwen-prèmium-é:0.5b"


# ─── parse_ollama_list_output (CLI fallback) — same robustness ──────────


def test_cli_parser_handles_empty_stdout():
    out = parse_ollama_list_output("")
    assert out == []


def test_cli_parser_handles_header_only():
    """If `ollama list` returns just the header (no models)."""
    out = parse_ollama_list_output("NAME    ID    SIZE    MODIFIED\n")
    assert out == []


def test_cli_parser_skips_unparseable_lines():
    """Future format change introduces a different column layout
    — lines that don't match the regex are skipped, not crashed
    on."""
    stdout = (
        "NAME                  ID         SIZE     MODIFIED\n"
        "qwen2.5-coder:0.5b    5a2c       397 MB   2 hours ago\n"
        "garbage-line-no-columns\n"
        "another-bad   line   without size\n"
        "llama3.1:8b           a1b2       4.7 GB   3 days ago\n"
    )
    out = parse_ollama_list_output(stdout)
    # Only the well-formed lines survive
    assert len(out) == 2
    assert {m.name for m in out} == {"qwen2.5-coder:0.5b", "llama3.1:8b"}


def test_cli_parser_handles_unusual_size_units():
    """Test unit normalization paths (MB / GB / kB / KB / B)."""
    stdout = (
        "NAME            ID    SIZE      MODIFIED\n"
        "small-model     a1    512 MB    1 day ago\n"
        "tiny-model      b2    100 kB    2 days ago\n"
    )
    out = parse_ollama_list_output(stdout)
    assert len(out) == 2
    sizes = {m.name: m.size_gb for m in out}
    # 512 MB → 0.5 GB (round to 2 decimal in MB branch)
    assert sizes["small-model"] == 0.5
    # 100 kB → 100/1024^2 GB ≈ 0.0001 (kB branch rounds to 4 decimals)
    # Pin actual kB-precision behavior so a refactor noticing the
    # tiny non-zero value doesn't accidentally raise the kB rounding
    # without checking display impact.
    assert 0.0 < sizes["tiny-model"] < 0.001


# ─── Cross-cutting: parser purity ───────────────────────────────────────


def test_parser_does_not_mutate_input():
    """Both parsers must be pure — input dict/string unchanged
    after call. Without this, caller can't safely reuse the same
    payload for multiple display modes."""
    payload = {"models": [{"name": "qwen", "size": 100}]}
    payload_copy = dict(payload)
    payload_copy["models"] = list(payload["models"])
    payload_copy["models"][0] = dict(payload["models"][0])

    parse_ollama_api_tags(payload)

    assert payload == payload_copy, (
        "parser mutated input dict — calls are no longer idempotent"
    )


def test_parser_returns_typed_dataclass():
    """Output is `list[OllamaModel]`, not raw dicts. Pin so a
    refactor that drops the dataclass wrapper breaks downstream
    consumers."""
    out = parse_ollama_api_tags(_CANONICAL_PAYLOAD)
    for m in out:
        assert isinstance(m, OllamaModel)
        assert hasattr(m, "name")
        assert hasattr(m, "size_gb")
        assert hasattr(m, "modified")
        assert hasattr(m, "digest")
