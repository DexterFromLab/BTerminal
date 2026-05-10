"""Tests for memory_wizard provider dispatch — T3.8.

memory_wizard now supports `--provider claude|copilot` plus auto-detect
from `~/.config/bterminal/ai_sessions.json`. Pure helpers exposed at
module level (`_detect_provider_from_sessions`, `_resolve_provider`,
`_build_ai_ask_argv`) let us test the dispatch logic without invoking
either CLI.
"""
from __future__ import annotations

import importlib.util
import json
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).parent.parent
SCRIPT_PATH = REPO_ROOT / "tools" / "memory_wizard"


@pytest.fixture(scope="module")
def mw():
    """Import tools/memory_wizard as a module. The script has no `.py`
    extension so spec_from_file_location's default loader rejects it;
    we use SourceFileLoader explicitly. Wrapped in a try/finally to
    clean up sys.modules even if exec_module raises."""
    from importlib.machinery import SourceFileLoader

    loader = SourceFileLoader(
        "memory_wizard_under_test", str(SCRIPT_PATH),
    )
    spec = importlib.util.spec_from_loader(
        "memory_wizard_under_test", loader,
    )
    mod = importlib.util.module_from_spec(spec)
    sys.modules["memory_wizard_under_test"] = mod
    try:
        loader.exec_module(mod)
        yield mod
    finally:
        sys.modules.pop("memory_wizard_under_test", None)


# ─── _detect_provider_from_sessions ─────────────────────────────────────────

def test_detect_returns_none_when_file_missing(mw, tmp_path):
    assert mw._detect_provider_from_sessions(
        "myproj", "/tmp/myproj", tmp_path / "no-such.json",
    ) is None


def test_detect_returns_none_when_file_malformed(mw, tmp_path):
    bad = tmp_path / "ai_sessions.json"
    bad.write_text("{not valid json")
    assert mw._detect_provider_from_sessions("x", "/tmp", bad) is None


def test_detect_returns_none_when_file_not_a_list(mw, tmp_path):
    bad = tmp_path / "ai_sessions.json"
    bad.write_text(json.dumps({"some": "object"}))
    assert mw._detect_provider_from_sessions("x", "/tmp", bad) is None


def test_detect_match_by_name_returns_claude(mw, tmp_path):
    f = tmp_path / "ai_sessions.json"
    f.write_text(json.dumps([
        {"name": "myproj", "provider": "claude", "project_dir": "/tmp/a"},
        {"name": "other", "provider": "copilot", "project_dir": "/tmp/b"},
    ]))
    assert mw._detect_provider_from_sessions("myproj", None, f) == "claude"


def test_detect_match_by_name_returns_copilot(mw, tmp_path):
    f = tmp_path / "ai_sessions.json"
    f.write_text(json.dumps([
        {"name": "myproj", "provider": "copilot", "project_dir": "/tmp/a"},
    ]))
    assert mw._detect_provider_from_sessions("myproj", None, f) == "copilot"


def test_detect_match_by_project_dir(mw, tmp_path):
    """When `name` doesn't match but `project_dir` does, still return
    the session's provider — useful when the project alias differs
    from the session display name."""
    f = tmp_path / "ai_sessions.json"
    f.write_text(json.dumps([
        {"name": "DisplayLabel", "provider": "copilot",
         "project_dir": "/home/me/myproj"},
    ]))
    got = mw._detect_provider_from_sessions(
        "myproj", "/home/me/myproj", f,
    )
    assert got == "copilot"


def test_detect_no_match_returns_none(mw, tmp_path):
    f = tmp_path / "ai_sessions.json"
    f.write_text(json.dumps([
        {"name": "alpha", "provider": "claude", "project_dir": "/tmp/a"},
    ]))
    assert mw._detect_provider_from_sessions(
        "missing", "/tmp/missing", f,
    ) is None


def test_detect_unsupported_provider_returns_none(mw, tmp_path):
    """Future-version session names a provider this build doesn't know."""
    f = tmp_path / "ai_sessions.json"
    f.write_text(json.dumps([
        {"name": "x", "provider": "aider", "project_dir": "/tmp/x"},
    ]))
    assert mw._detect_provider_from_sessions("x", None, f) is None


def test_detect_skips_non_dict_entries(mw, tmp_path):
    """Defensive: malformed entries (string, list) are skipped."""
    f = tmp_path / "ai_sessions.json"
    f.write_text(json.dumps([
        "garbage",
        ["also garbage"],
        {"name": "x", "provider": "claude", "project_dir": "/tmp"},
    ]))
    assert mw._detect_provider_from_sessions("x", None, f) == "claude"


# ─── _resolve_provider — priority order ────────────────────────────────────

def test_resolve_explicit_flag_wins(mw, tmp_path):
    """--provider copilot beats whatever ai_sessions.json says."""
    f = tmp_path / "ai_sessions.json"
    f.write_text(json.dumps([
        {"name": "x", "provider": "claude", "project_dir": "/tmp/x"},
    ]))
    got = mw._resolve_provider("x", "/tmp/x", "copilot",
                                ai_sessions_path=f)
    assert got == "copilot"


def test_resolve_invalid_explicit_falls_back_to_detect(mw, tmp_path):
    """If --provider was somehow set to a bogus value (shouldn't happen
    because main() validates), _resolve_provider gracefully falls back
    to auto-detect."""
    f = tmp_path / "ai_sessions.json"
    f.write_text(json.dumps([
        {"name": "x", "provider": "copilot", "project_dir": "/tmp/x"},
    ]))
    got = mw._resolve_provider("x", "/tmp/x", "totally-unsupported",
                                ai_sessions_path=f)
    assert got == "copilot"


def test_resolve_auto_detect_from_sessions(mw, tmp_path):
    """No --provider flag, ai_sessions.json picks the answer."""
    f = tmp_path / "ai_sessions.json"
    f.write_text(json.dumps([
        {"name": "x", "provider": "copilot", "project_dir": "/tmp/x"},
    ]))
    got = mw._resolve_provider("x", "/tmp/x", None, ai_sessions_path=f)
    assert got == "copilot"


def test_resolve_falls_back_to_claude_when_no_match(mw, tmp_path):
    """No flag, no matching session → legacy default 'claude'."""
    f = tmp_path / "ai_sessions.json"
    f.write_text(json.dumps([]))
    got = mw._resolve_provider("missing", None, None, ai_sessions_path=f)
    assert got == "claude"


def test_resolve_no_sessions_file_falls_back_to_claude(mw, tmp_path):
    """Fresh install (no ai_sessions.json yet) → claude default."""
    got = mw._resolve_provider(
        "x", "/tmp/x", None, ai_sessions_path=tmp_path / "missing.json",
    )
    assert got == "claude"


# ─── _build_ai_ask_argv — provider-specific invocation ──────────────────────

def test_build_argv_claude_uses_print_and_stdin(mw):
    argv, stdin = mw._build_ai_ask_argv("claude", "How do I do X?")
    assert argv[0] == "claude"
    assert "--print" in argv
    assert "--output-format" in argv
    # Claude reads prompt via stdin
    assert stdin == "How do I do X?"


def test_build_argv_copilot_uses_p_flag_no_stdin(mw):
    argv, stdin = mw._build_ai_ask_argv("copilot", "How do I do X?")
    assert argv[0] == "copilot"
    # Copilot takes prompt as -p TEXT
    assert "-p" in argv
    p_idx = argv.index("-p")
    assert argv[p_idx + 1] == "How do I do X?"
    # TUI-safe flags so output is plain text (--no-banner removed
    # 2026-05-07 — real copilot v1.0.43 has only opt-in `--banner`)
    assert "--no-color" in argv
    # Copilot doesn't need stdin
    assert stdin is None


def test_build_argv_unknown_provider_falls_back_to_claude(mw):
    """Defensive: unsupported provider name → claude argv (legacy)."""
    argv, stdin = mw._build_ai_ask_argv("aider", "test")
    assert argv[0] == "claude"
    assert stdin == "test"


# ─── Module-level state for legacy _claude_ask ──────────────────────────────

def test_claude_ask_delegates_to_ai_ask(mw, monkeypatch):
    """Setting _ACTIVE_PROVIDER routes _claude_ask through the chosen
    provider's CLI. We patch _ai_ask to inspect the dispatch."""
    captured = {}

    def fake(prompt, provider="claude"):
        captured["prompt"] = prompt
        captured["provider"] = provider
        return "MOCK_REPLY"

    monkeypatch.setattr(mw, "_ai_ask", fake)
    monkeypatch.setattr(mw, "_ACTIVE_PROVIDER", "copilot")

    reply = mw._claude_ask("hello")
    assert reply == "MOCK_REPLY"
    assert captured["prompt"] == "hello"
    assert captured["provider"] == "copilot"


def test_claude_ask_with_default_active_provider(mw, monkeypatch):
    """Without anyone setting _ACTIVE_PROVIDER, it defaults to 'claude'."""
    captured = {}

    def fake(prompt, provider="claude"):
        captured["provider"] = provider
        return "OK"

    monkeypatch.setattr(mw, "_ai_ask", fake)
    monkeypatch.setattr(mw, "_ACTIVE_PROVIDER", "claude")

    mw._claude_ask("hi")
    assert captured["provider"] == "claude"


# ─── _ai_ask error handling ─────────────────────────────────────────────────

def test_ai_ask_returns_error_when_binary_missing(mw, monkeypatch):
    def fake_run(*args, **kwargs):
        raise FileNotFoundError("no copilot here")

    monkeypatch.setattr(mw.subprocess, "run", fake_run)
    out = mw._ai_ask("test", provider="copilot")
    assert "[Error:" in out
    assert "copilot" in out


def test_ai_ask_returns_error_on_timeout(mw, monkeypatch):
    def fake_run(*args, **kwargs):
        raise mw.subprocess.TimeoutExpired(cmd="claude", timeout=120)

    monkeypatch.setattr(mw.subprocess, "run", fake_run)
    out = mw._ai_ask("test", provider="claude")
    assert "timed out" in out.lower()


def test_ai_ask_returns_stdout_on_success(mw, monkeypatch):
    class _Result:
        stdout = "MOCK CLAUDE OUTPUT\n"
        stderr = ""

    def fake_run(*args, **kwargs):
        return _Result()

    monkeypatch.setattr(mw.subprocess, "run", fake_run)
    out = mw._ai_ask("test", provider="claude")
    assert out == "MOCK CLAUDE OUTPUT"


def test_ai_ask_returns_stderr_when_stdout_empty(mw, monkeypatch):
    class _Result:
        stdout = ""
        stderr = "Authentication failed"

    def fake_run(*args, **kwargs):
        return _Result()

    monkeypatch.setattr(mw.subprocess, "run", fake_run)
    out = mw._ai_ask("test", provider="claude")
    assert "[Error:" in out
    assert "Authentication failed" in out
