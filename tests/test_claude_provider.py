"""Unit tests for bterminal.providers.claude.ClaudeProvider — T1.3.

Verifies 1:1 behavioral parity with the legacy code in:
    bterminal/helpers.py::_find_claude_path
    bterminal/ui/terminal_tab.py::spawn_claude (argv portion)
    bterminal/ui/stats.py::_SessionStatsReader / _fetch_claude_usage
"""
from __future__ import annotations

import json
import os
import stat

import pytest

from bterminal.providers import load_providers_config
from bterminal.providers.base import ProviderCapabilities, SessionStats
from bterminal.providers.claude import ClaudeProvider


# ─── Fixtures ────────────────────────────────────────────────────────────────

@pytest.fixture
def claude_config():
    return load_providers_config()["providers"]["claude"]


@pytest.fixture
def provider(claude_config):
    return ClaudeProvider(claude_config)


def _make_executable(path):
    """Touch + chmod +x so os.access(X_OK) returns True."""
    path.write_text("#!/bin/sh\necho fake claude\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ─── Construction ────────────────────────────────────────────────────────────

def test_provider_has_correct_metadata(provider):
    assert provider.name == "claude"
    assert provider.display.short_label == "Claude"
    assert provider.display.long_label == "Claude Code"
    assert isinstance(provider.capabilities, ProviderCapabilities)


def test_capabilities_match_defaults(provider):
    """T1.3 must preserve Claude's current behavior — these flags are
    the canonical baseline."""
    c = provider.capabilities
    assert c.intro_prompt is True
    assert c.resume_flag is True
    assert c.skip_permissions is True
    assert c.session_log is True
    assert c.usage_api is True
    assert c.rules_inject is True
    assert c.task_auto_trigger is True
    assert c.stats_bar is True


def test_pricing_loaded(provider):
    assert "claude-sonnet-4-6" in provider.pricing
    assert provider.pricing["claude-sonnet-4-6"]["input"] == 3.0


# ─── find_binary ─────────────────────────────────────────────────────────────

def test_find_binary_returns_first_match(tmp_path, claude_config):
    """search_paths order matters — first existing executable wins."""
    fake = tmp_path / "claude"
    _make_executable(fake)
    cfg = json.loads(json.dumps(claude_config))  # deep copy
    cfg["binary"]["search_paths"] = [str(fake), "/nonexistent/claude"]
    p = ClaudeProvider(cfg)
    assert p.find_binary() == str(fake)


def test_find_binary_skips_nonexecutable(tmp_path, claude_config, monkeypatch):
    fake = tmp_path / "claude"
    fake.write_text("not executable")
    cfg = json.loads(json.dumps(claude_config))
    cfg["binary"]["search_paths"] = [str(fake)]
    # Disable PATH fallback so the test isn't tricked by a system-installed
    # claude binary (e.g. ~/.npm-global/bin/claude on the dev machine).
    monkeypatch.setattr("bterminal.providers.claude.shutil.which",
                        lambda *a, **kw: None)
    p = ClaudeProvider(cfg)
    assert p.find_binary() is None


def test_find_binary_glob_resolves(tmp_path, claude_config):
    """Glob in search_paths (e.g. ~/.nvm/versions/node/*/bin/claude)."""
    nvm_v18 = tmp_path / "node-v18" / "bin"
    nvm_v20 = tmp_path / "node-v20" / "bin"
    nvm_v18.mkdir(parents=True)
    nvm_v20.mkdir(parents=True)
    _make_executable(nvm_v18 / "claude")
    _make_executable(nvm_v20 / "claude")
    cfg = json.loads(json.dumps(claude_config))
    cfg["binary"]["search_paths"] = [str(tmp_path / "node-v*" / "bin" / "claude")]
    p = ClaudeProvider(cfg)
    # reverse-sorted → v20 wins (newest)
    assert p.find_binary() == str(nvm_v20 / "claude")


# ─── build_argv ──────────────────────────────────────────────────────────────

def test_build_argv_minimal(tmp_path, claude_config, monkeypatch):
    fake = tmp_path / "claude"
    _make_executable(fake)
    cfg = json.loads(json.dumps(claude_config))
    cfg["binary"]["search_paths"] = [str(fake)]
    p = ClaudeProvider(cfg)
    assert p.build_argv({}, "") == [str(fake)]


def test_build_argv_with_resume(tmp_path, claude_config):
    fake = tmp_path / "claude"
    _make_executable(fake)
    cfg = json.loads(json.dumps(claude_config))
    cfg["binary"]["search_paths"] = [str(fake)]
    p = ClaudeProvider(cfg)
    argv = p.build_argv({"resume": True}, "")
    assert argv == [str(fake), "--resume"]


def test_build_argv_with_skip_permissions(tmp_path, claude_config):
    fake = tmp_path / "claude"
    _make_executable(fake)
    cfg = json.loads(json.dumps(claude_config))
    cfg["binary"]["search_paths"] = [str(fake)]
    p = ClaudeProvider(cfg)
    argv = p.build_argv({"skip_permissions": True}, "")
    assert argv == [str(fake), "--dangerously-skip-permissions"]


def test_build_argv_full_session_with_intro(tmp_path, claude_config):
    fake = tmp_path / "claude"
    _make_executable(fake)
    cfg = json.loads(json.dumps(claude_config))
    cfg["binary"]["search_paths"] = [str(fake)]
    p = ClaudeProvider(cfg)
    argv = p.build_argv(
        {"resume": True, "skip_permissions": True},
        "Hello Claude",
    )
    assert argv == [
        str(fake), "--resume", "--dangerously-skip-permissions",
        "Hello Claude",
    ]


def test_build_argv_reads_provider_options(tmp_path, claude_config):
    """R4.2 schema: flags live under provider_options. Legacy top-level
    still works (test above), but provider_options wins when present."""
    fake = tmp_path / "claude"
    _make_executable(fake)
    cfg = json.loads(json.dumps(claude_config))
    cfg["binary"]["search_paths"] = [str(fake)]
    p = ClaudeProvider(cfg)
    argv = p.build_argv(
        {"provider_options": {"resume": True, "skip_permissions": True}},
        "",
    )
    assert argv == [str(fake), "--resume", "--dangerously-skip-permissions"]


def test_build_argv_returns_empty_when_binary_missing(claude_config):
    cfg = json.loads(json.dumps(claude_config))
    cfg["binary"]["search_paths"] = ["/definitely/nowhere/claude"]
    p = ClaudeProvider(cfg)
    # Also clear PATH augmentation candidates
    assert p.build_argv({}, "Hello") == [] or \
           p.build_argv({}, "Hello")[0] != "/definitely/nowhere/claude"


# ─── Snapshot tests (T2.2 — argv shape for 5 typical configs) ──────────────
#
# Each snapshot is a (config, intro, expected_argv) tuple. expected_argv
# uses _BINARY as a placeholder that the test substitutes for the real
# tmp_path/claude binary at run time. Snapshots are intentionally
# declarative so a one-line review catches unintended changes — e.g. a
# refactor that drops --resume, swaps argv order, or doubles a flag.
#
# Pre-T2.2 the same scenarios were covered by individual tests
# (test_build_argv_*). Snapshots are an additional layer documenting
# THE EXACT shape, useful when porting to T2.3 (Copilot equivalents).

_BINARY = "<<binary>>"

_BUILD_ARGV_SNAPSHOTS = [
    pytest.param(
        {},
        "",
        [_BINARY],
        id="minimal_no_flags_no_intro",
    ),
    pytest.param(
        {"resume": True, "skip_permissions": True},
        "Hello Claude — please continue",
        [_BINARY, "--resume", "--dangerously-skip-permissions",
         "Hello Claude — please continue"],
        id="legacy_schema_resume_yolo_with_intro",
    ),
    pytest.param(
        {"continue": True},
        "Pick up where we left off",
        [_BINARY, "--continue", "Pick up where we left off"],
        id="continue_flag_with_intro",
    ),
    pytest.param(
        {"skip_permissions": True, "model": "claude-opus-4-7"},
        "",
        [_BINARY, "--dangerously-skip-permissions",
         "--model", "claude-opus-4-7"],
        id="model_override_no_intro",
    ),
    pytest.param(
        {"provider_options": {
            "resume": True,
            "skip_permissions": True,
            "model": "claude-sonnet-4-6",
        }},
        "Test prompt with 'quotes' and spaces",
        [_BINARY, "--resume", "--dangerously-skip-permissions",
         "--model", "claude-sonnet-4-6",
         "Test prompt with 'quotes' and spaces"],
        id="r4_2_schema_provider_options_full",
    ),
]


@pytest.mark.parametrize("config,intro,expected", _BUILD_ARGV_SNAPSHOTS)
def test_build_argv_snapshot(config, intro, expected, tmp_path, claude_config):
    """Golden snapshot: argv for 5 typical session configs.

    Each row asserts the exact argv list shape so the canonical Claude
    invocation is reviewable in one place. A future change that adds
    a flag or reorders existing ones must update this list.
    """
    fake = tmp_path / "claude"
    _make_executable(fake)
    cfg = json.loads(json.dumps(claude_config))
    cfg["binary"]["search_paths"] = [str(fake)]
    p = ClaudeProvider(cfg)

    # Substitute the placeholder for the real binary path
    expected_resolved = [str(fake) if x == _BINARY else x for x in expected]

    argv = p.build_argv(config, intro)
    assert argv == expected_resolved


def test_build_argv_intro_skipped_when_capability_disabled(tmp_path, claude_config):
    """Defensive: if capabilities.intro_prompt is somehow flipped off,
    the intro positional arg is dropped (per R4a.4)."""
    fake = tmp_path / "claude"
    _make_executable(fake)
    cfg = json.loads(json.dumps(claude_config))
    cfg["binary"]["search_paths"] = [str(fake)]
    cfg["capabilities"]["intro_prompt"] = False
    p = ClaudeProvider(cfg)
    argv = p.build_argv({}, "should be dropped")
    assert argv == [str(fake)]
    assert "should be dropped" not in argv


def test_build_argv_resume_takes_precedence_over_continue(tmp_path, claude_config):
    """When both flags are set (shouldn't happen normally), `resume`
    wins — mirrors the elif branch in ClaudeProvider.build_argv."""
    fake = tmp_path / "claude"
    _make_executable(fake)
    cfg = json.loads(json.dumps(claude_config))
    cfg["binary"]["search_paths"] = [str(fake)]
    p = ClaudeProvider(cfg)
    argv = p.build_argv({"resume": True, "continue": True}, "")
    assert "--resume" in argv
    assert "--continue" not in argv


# ─── session_log_glob ────────────────────────────────────────────────────────

def test_session_log_glob_sanitizes_cwd(provider):
    glob_path = provider.session_log_glob("/home/bartek/workspace/foo")
    # All non-alphanumeric become "-"; trailing slash stripped first.
    assert "-home-bartek-workspace-foo" in glob_path
    assert glob_path.endswith("*.jsonl")


def test_session_log_glob_strips_trailing_slash(provider):
    a = provider.session_log_glob("/foo/bar")
    b = provider.session_log_glob("/foo/bar/")
    assert a == b


def test_session_log_glob_returns_none_when_capability_off(claude_config):
    cfg = json.loads(json.dumps(claude_config))
    cfg["capabilities"]["session_log"] = False
    p = ClaudeProvider(cfg)
    assert p.session_log_glob("/anywhere") is None


# ─── parse_session_stats ─────────────────────────────────────────────────────

def _write_jsonl(path, events):
    path.write_text("\n".join(json.dumps(e) for e in events) + "\n")


def test_parse_session_stats_accumulates_tokens(tmp_path, provider):
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, [
        {"timestamp": "2026-05-06T10:00:00Z",
         "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                     "usage": {"input_tokens": 100, "output_tokens": 50,
                               "cache_read_input_tokens": 20,
                               "cache_creation_input_tokens": 30}}},
        {"timestamp": "2026-05-06T10:01:00Z",
         "message": {"role": "assistant", "model": "claude-sonnet-4-6",
                     "usage": {"input_tokens": 200, "output_tokens": 80}}},
    ])
    stats = provider.parse_session_stats(str(log))
    assert stats.input_tokens == 300
    assert stats.output_tokens == 130
    assert stats.cache_read_tokens == 20
    assert stats.cache_creation_tokens == 30
    assert stats.response_count == 2
    assert stats.model == "claude-sonnet-4-6"
    # Cost: (300*3 + 130*15 + 20*0.30 + 30*3.75)/1M = (900+1950+6+112.5)/1M
    expected_cost = (300 * 3.0 + 130 * 15.0 + 20 * 0.30 + 30 * 3.75) / 1_000_000
    assert stats.cost_usd == pytest.approx(expected_cost)


def test_parse_session_stats_skips_malformed_lines(tmp_path, provider):
    log = tmp_path / "session.jsonl"
    log.write_text(
        "{not valid json\n"
        + json.dumps({"message": {"role": "assistant",
                                  "usage": {"input_tokens": 10}}}) + "\n"
        + "\n"  # blank line
        + "garbage\n"
    )
    stats = provider.parse_session_stats(str(log))
    assert stats.input_tokens == 10


def test_parse_session_stats_missing_file_returns_zero(provider):
    stats = provider.parse_session_stats("/nonexistent/session.jsonl")
    assert stats.input_tokens == 0
    assert stats.cost_usd == 0.0


def test_parse_session_stats_unknown_model_uses_default_pricing(tmp_path, provider):
    log = tmp_path / "session.jsonl"
    _write_jsonl(log, [
        {"message": {"role": "assistant", "model": "claude-future-99",
                     "usage": {"input_tokens": 1_000_000}}},
    ])
    stats = provider.parse_session_stats(str(log))
    # _DEFAULT_PRICE input rate = 3.0 → 1M tokens × 3.0 / 1M = 3.0
    assert stats.cost_usd == pytest.approx(3.0)


# ─── fetch_plan_usage ────────────────────────────────────────────────────────

def test_fetch_plan_usage_returns_none_when_creds_missing(tmp_path, claude_config):
    cfg = json.loads(json.dumps(claude_config))
    cfg["capabilities"]["oauth_creds_file"] = str(tmp_path / "nope.json")
    p = ClaudeProvider(cfg)
    assert p.fetch_plan_usage() is None


def test_fetch_plan_usage_returns_none_when_capability_off(claude_config):
    cfg = json.loads(json.dumps(claude_config))
    cfg["capabilities"]["usage_api"] = False
    p = ClaudeProvider(cfg)
    assert p.fetch_plan_usage() is None


def test_fetch_plan_usage_returns_none_when_token_expired(tmp_path, claude_config):
    creds = tmp_path / "credentials.json"
    creds.write_text(json.dumps({
        "claudeAiOauth": {
            "accessToken": "expired-token",
            "expiresAt": 1,  # 1970 → expired
        }
    }))
    cfg = json.loads(json.dumps(claude_config))
    cfg["capabilities"]["oauth_creds_file"] = str(creds)
    p = ClaudeProvider(cfg)
    assert p.fetch_plan_usage() is None


# ─── detect_idle / dialog_schema (defaults) ─────────────────────────────────

def test_detect_idle_returns_true(provider):
    """Default — no live tail-f yet (post-MVP enhancement)."""
    assert provider.detect_idle(terminal=None, session_id=None) is True


def test_dialog_schema_returns_claude_specific_fields(provider):
    """T2.6: ClaudeProvider declares 3 checkboxes — resume, skip_permissions, sudo."""
    schema = provider.get_dialog_schema()
    assert len(schema) == 3
    keys = [entry[0] for entry in schema]
    assert keys == ["resume", "skip_permissions", "sudo"]
    assert all(entry[1] == "checkbox" for entry in schema)
