"""Failure mode: provider config file corrupted (#30 / #102,
audit § 6.1 #3).

When `~/.config/bterminal/providers.json` is corrupted (truncated
mid-write, missing keys, or contains an unknown provider entry),
ProviderRegistry MUST fail open:

  1. Truncated JSON → catch JSONDecodeError, log warning to stderr,
     fall back to bundled defaults.json — BT keeps working with
     vanilla provider config.
  2. Valid JSON but missing required capability key → cls(slice)
     raises during instantiation; ProviderRegistry catches, logs
     warning, skips that provider, others still work.
  3. Extra unknown provider in override (forward-compat with future
     versions) → no class registered; logs warning, skips. Doesn't
     crash on the unknown name.

Three decision branches from auto-trigger:
  (a) Total truncation (mid-array)
  (b) Valid JSON but missing required key
  (c) Extra unknown provider in override

The existing handler in load_providers_config catches
(OSError, json.JSONDecodeError) — these tests pin the contract for
all three failure shapes + verify warnings reach stderr.
"""
from __future__ import annotations

import io
import json
import sys
from pathlib import Path

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.providers.aider import AiderProvider


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


# ─── (a) Total truncation — JSONDecodeError fallback ─────────────────────


def test_truncated_mid_array_falls_back_to_defaults(tmp_path, capsys):
    """Half-written file: power-loss / OOM-kill mid-write left a
    partial JSON object. load_providers_config must catch the parse
    error and return the bundled defaults — BT loads with vanilla
    config + a warning on stderr."""
    user_path = tmp_path / "providers.json"
    # Realistic mid-write truncation: opening braces but no closing
    user_path.write_text('{"providers":{"aider":{"capa')

    config = load_providers_config(user_path=user_path)

    # Bundled defaults survived
    assert "providers" in config
    assert "claude" in config["providers"]
    assert "copilot" in config["providers"]
    assert "aider" in config["providers"]
    assert config["default_provider"] == "claude"

    # Warning on stderr — operator can debug
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "providers override" in captured.err
    assert str(user_path) in captured.err


def test_completely_empty_file_falls_back_to_defaults(tmp_path, capsys):
    """Edge: zero-byte file (atomic write that aborted before
    write()). Same JSONDecodeError path."""
    user_path = tmp_path / "providers.json"
    user_path.write_text("")
    config = load_providers_config(user_path=user_path)
    assert "providers" in config and "aider" in config["providers"]
    assert "WARN" in capsys.readouterr().err


def test_invalid_json_syntax_falls_back_to_defaults(tmp_path, capsys):
    """JSON-with-syntax-errors (someone hand-edited a comma)."""
    user_path = tmp_path / "providers.json"
    user_path.write_text('{"providers": {"aider": {"foo": "bar",}}}')  # trailing comma
    config = load_providers_config(user_path=user_path)
    assert "providers" in config
    assert "WARN" in capsys.readouterr().err


def test_truncated_file_does_not_raise():
    """The pinned contract from #102: load_providers_config must
    NEVER propagate JSONDecodeError to its caller. ProviderRegistry
    constructor relies on this — without the catch, BT crashes at
    startup."""
    # Synthesize a truncated user_path via tmp file
    import tempfile
    with tempfile.NamedTemporaryFile(mode="w", suffix=".json",
                                       delete=False) as f:
        f.write('{"providers":{"aid')
        path = Path(f.name)
    try:
        # This is the pin — must not raise
        config = load_providers_config(user_path=path)
        assert config is not None
    finally:
        path.unlink()


def test_registry_instantiates_with_corrupted_user_override(tmp_path,
                                                              capsys):
    """End-to-end: even with corrupted user_path, ProviderRegistry
    constructor produces a working registry with all 3 bundled
    providers."""
    user_path = tmp_path / "providers.json"
    user_path.write_text('{"providers":{"aider":{"capa')

    config = load_providers_config(user_path=user_path)
    registry = ProviderRegistry(config=config)
    # All 3 bundled providers usable
    assert registry.has("claude")
    assert registry.has("copilot")
    assert registry.has("aider")
    # Aider's full capability set survived the merge fallback
    aider = registry.get("aider")
    assert aider.capabilities.rules_inject is True
    assert aider.capabilities.context_file == "AIDER.md"


def test_load_providers_config_with_no_user_path_unchanged():
    """Regression: passing user_path=None (no override) must still
    return defaults. Pin so the fallback path doesn't accidentally
    require an override file."""
    config = load_providers_config(user_path=None)
    assert "providers" in config
    assert config["default_provider"] == "claude"


def test_load_providers_config_with_nonexistent_path_unchanged(tmp_path):
    """user_path points at a missing file → return defaults
    without printing a warning (this is the normal 'no override'
    state, not corruption)."""
    nonexistent = tmp_path / "never-exists.json"
    config = load_providers_config(user_path=nonexistent)
    assert "providers" in config


# ─── (b) Valid JSON but missing required key — registry skips ────────────


def test_valid_json_missing_capability_key_skips_provider(tmp_path,
                                                            capsys):
    """Override declares an aider entry but is missing the required
    `capabilities` key — AiderProvider.__init__ raises KeyError on
    `config["capabilities"]`. Registry catches, logs warning, skips
    aider; claude + copilot stay registered."""
    # Defaults have everything; override REPLACES aider with a half-
    # baked entry. _deep_merge will merge the dicts, but if override
    # explicitly sets aider to a top-level value lacking capabilities,
    # the merge result is invalid for AiderProvider.
    #
    # _deep_merge merges nested dicts, so we need to break aider's
    # 'capabilities' key by nullifying it in the override.
    user_path = tmp_path / "providers.json"
    # A sneaky invalid override: forces capabilities → null on merge
    user_path.write_text(json.dumps({
        "providers": {
            "aider": {
                # Replacing the whole entry would still merge with
                # defaults; instead, override signals that 'capabilities'
                # is None — this fails AiderProvider's init which calls
                # ProviderCapabilities(**config["capabilities"]).
                "capabilities": None,
            }
        }
    }))

    config = load_providers_config(user_path=user_path)
    # Construct registry — aider should fail to instantiate but
    # claude/copilot OK.
    registry = ProviderRegistry(config=config)
    captured = capsys.readouterr()
    # claude + copilot still registered
    assert registry.has("claude")
    assert registry.has("copilot")
    # aider gracefully skipped
    assert not registry.has("aider"), (
        "aider should have been skipped due to invalid capabilities"
    )
    # Warning logged to stderr
    assert "failed to instantiate provider 'aider'" in captured.err


def test_partial_capability_dict_skips_provider(tmp_path, capsys):
    """Override sets aider.capabilities to a dict missing required
    field — AiderProvider's ProviderCapabilities(**dict) succeeds
    only if all required fields are present (or defaultable).

    For the dataclass, missing fields fall back to defaults — so
    actually a partial cap dict CAN still construct. We construct a
    malformed dict that DOES break: pass an unknown key, which
    raises TypeError on dataclass __init__."""
    user_path = tmp_path / "providers.json"
    user_path.write_text(json.dumps({
        "providers": {
            "aider": {
                "capabilities": {
                    "this_is_not_a_real_capability_field": True,
                }
            }
        }
    }))
    config = load_providers_config(user_path=user_path)
    registry = ProviderRegistry(config=config)
    # claude/copilot survive
    assert registry.has("claude")
    assert registry.has("copilot")
    # aider was skipped
    assert not registry.has("aider")
    # Warning logged
    captured = capsys.readouterr()
    assert "failed to instantiate provider 'aider'" in captured.err


# ─── (c) Extra unknown provider in override — forward-compat skip ────────


def test_unknown_provider_name_in_override_skipped_gracefully(tmp_path,
                                                                capsys):
    """A future BTerminal version might ship a 4th provider called
    'gemini'; users running an older build with a settings file
    from the newer build see the unknown name. Registry must skip
    it (log warning), keep the 3 bundled providers usable."""
    user_path = tmp_path / "providers.json"
    user_path.write_text(json.dumps({
        "providers": {
            "gemini-future": {
                "display": {"icon": "✨", "short_label": "Gemini",
                             "long_label": "Gemini", "color": "#888"},
                "binary": {"search_paths": [], "argv_prefix": []},
                "argv": {},
                "capabilities": {"intro_prompt": True},
            }
        }
    }))
    config = load_providers_config(user_path=user_path)
    registry = ProviderRegistry(config=config)
    # 3 bundled providers all OK
    for name in ("claude", "copilot", "aider"):
        assert registry.has(name)
    # Unknown one skipped
    assert not registry.has("gemini-future")
    captured = capsys.readouterr()
    assert "provider 'gemini-future'" in captured.err
    assert "no class registered" in captured.err


def test_unknown_provider_does_not_break_default_provider_resolution(
        tmp_path, capsys):
    """The default_provider field can name an unknown one. We need
    a graceful fallback — caller can detect via has() before get()."""
    user_path = tmp_path / "providers.json"
    user_path.write_text(json.dumps({
        "default_provider": "gemini-future",  # unknown
        "providers": {
            "gemini-future": {
                "display": {"icon": "✨", "short_label": "G",
                             "long_label": "G", "color": "#000"},
                "binary": {}, "argv": {},
                "capabilities": {},
            }
        }
    }))
    config = load_providers_config(user_path=user_path)
    registry = ProviderRegistry(config=config)
    # Claude/Copilot/Aider OK
    assert registry.has("claude")
    # Default points at unknown
    assert registry._default_name == "gemini-future"  # noqa: SLF001
    # Caller MUST check has() before get() — get raises KeyError
    with pytest.raises(KeyError):
        registry.get("gemini-future")


def test_multiple_unknown_providers_all_skipped(tmp_path, capsys):
    """Override piles on three unknown providers. Each gets its own
    warning. Bundled trio still complete."""
    user_path = tmp_path / "providers.json"
    user_path.write_text(json.dumps({
        "providers": {
            "gemini-future": {"capabilities": {}},
            "llama-direct": {"capabilities": {}},
            "ollama-native": {"capabilities": {}},
        }
    }))
    config = load_providers_config(user_path=user_path)
    registry = ProviderRegistry(config=config)
    captured = capsys.readouterr()
    for unknown in ("gemini-future", "llama-direct", "ollama-native"):
        assert not registry.has(unknown)
        assert unknown in captured.err
    # Bundled trio
    for known in ("claude", "copilot", "aider"):
        assert registry.has(known)


# ─── Override that disables a provider via partial merge ─────────────────


def test_user_override_can_modify_existing_capability(tmp_path):
    """Sanity / complement to corruption tests: a VALID override
    that flips a capability flag works as expected. Without this,
    the test suite has no positive case for the merge mechanism."""
    user_path = tmp_path / "providers.json"
    user_path.write_text(json.dumps({
        "providers": {
            "aider": {
                "capabilities": {"rules_inject": False}
            }
        }
    }))
    config = load_providers_config(user_path=user_path)
    aider_cap = config["providers"]["aider"]["capabilities"]
    # Override applied
    assert aider_cap["rules_inject"] is False
    # Other capabilities preserved (deep merge)
    assert aider_cap["context_file"] == "AIDER.md"
    assert aider_cap["task_auto_trigger"] is True


# ─── Cross-cutting: warnings always reach stderr (not stdout) ────────────


def test_corruption_warnings_go_to_stderr_not_stdout(tmp_path, capsys):
    """Pin: warnings about config corruption land on stderr so they
    don't pollute pipelines that consume BT stdout. Important for
    the debug-REST + scripted automation flows."""
    user_path = tmp_path / "providers.json"
    user_path.write_text("{not json")  # forces JSONDecodeError
    load_providers_config(user_path=user_path)
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "WARN" not in captured.out  # NOT on stdout


def test_load_providers_config_handles_oserror(monkeypatch, capsys):
    """Edge: file exists but read raises OSError (permission denied,
    EBADF). Same fallback path."""
    # Use a real path that we then make unreadable via monkeypatch
    fake_path = Path("/tmp/test-fake-providers-path.json")

    def _raise_oserror(*a, **kw):
        raise OSError("permission denied")

    # Force open() to raise on the override path. Need to be careful
    # to only intercept the override call, not the defaults read.
    real_open = open
    defaults_path = Path(__file__).resolve().parent.parent \
        / "bterminal" / "providers" / "defaults.json"

    def _selective_open(path, *args, **kwargs):
        if str(path) == str(fake_path):
            raise OSError("permission denied")
        return real_open(path, *args, **kwargs)

    # Patch only this module's `open`
    import builtins
    monkeypatch.setattr(builtins, "open", _selective_open)
    # Pretend file exists so we go down the open() path
    monkeypatch.setattr(Path, "exists", lambda self: True)

    config = load_providers_config(user_path=fake_path)
    assert "providers" in config
    captured = capsys.readouterr()
    assert "WARN" in captured.err
    assert "permission denied" in captured.err


# ─── End-to-end: full registry boot through corruption → recovery ────────


def test_registry_singleton_recovers_after_user_fixes_file(
        tmp_path, capsys):
    """Lifecycle: BT starts with corrupted file → falls back to
    defaults → user fixes file → next boot picks up the fix.
    Pinned via two independent ProviderRegistry constructions, the
    second after a config rewrite."""
    user_path = tmp_path / "providers.json"

    # Phase 1: corrupted at boot
    user_path.write_text('{"providers":{"aider":{"capa')
    config1 = load_providers_config(user_path=user_path)
    reg1 = ProviderRegistry(config=config1)
    assert reg1.has("aider")
    # Default values (no override applied due to corruption)
    assert reg1.get("aider").capabilities.rules_inject is True

    # Capture and clear corruption warning
    capsys.readouterr()

    # Phase 2: user repairs file
    user_path.write_text(json.dumps({
        "providers": {
            "aider": {
                "capabilities": {"task_auto_trigger": False}
            }
        }
    }))
    config2 = load_providers_config(user_path=user_path)
    reg2 = ProviderRegistry(config=config2)
    assert reg2.has("aider")
    # Override now applied
    assert reg2.get("aider").capabilities.task_auto_trigger is False
    # rules_inject still True (deep merge preserved untouched fields)
    assert reg2.get("aider").capabilities.rules_inject is True
    # No new corruption warning
    assert "WARN" not in capsys.readouterr().err
