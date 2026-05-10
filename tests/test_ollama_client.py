"""Tests for bterminal.ollama_client (task #7 / #79).

Pure parser tests + subprocess mocking — no real Ollama daemon
needed. Manual smoke is in the task description.
"""
from __future__ import annotations

import json
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bterminal import ollama_client as oc


# ─── parse_ollama_list_output ──────────────────────────────────────────────


def test_parse_list_handles_realistic_output():
    """Format observed on Ollama v0.4.7 — header + 2 model rows."""
    stdout = (
        "NAME                       ID              SIZE      MODIFIED\n"
        "qwen2.5-coder:0.5b         5a2c8a83b2e8    397 MB    2 hours ago\n"
        "llama3.1:8b                a1b2c3d4e5f6    4.7 GB    3 days ago\n"
    )
    out = oc.parse_ollama_list_output(stdout)
    assert len(out) == 2
    assert out[0].name == "qwen2.5-coder:0.5b"
    # 397 MB → ~0.39 GB (rounded to 2 decimals)
    assert 0.3 < out[0].size_gb < 0.5
    assert out[0].modified == "2 hours ago"
    assert out[1].name == "llama3.1:8b"
    assert out[1].size_gb == 4.7


def test_parse_list_returns_empty_for_blank_output():
    assert oc.parse_ollama_list_output("") == []
    assert oc.parse_ollama_list_output("\n\n") == []


def test_parse_list_returns_empty_for_header_only():
    """ollama list with no models prints header + nothing else."""
    stdout = "NAME    ID    SIZE    MODIFIED\n"
    assert oc.parse_ollama_list_output(stdout) == []


def test_parse_list_skips_unparseable_lines():
    """Defensive: garbage lines mixed with valid rows must not crash
    or wreck the rest. Older ollama versions sometimes append extra
    info lines."""
    stdout = (
        "NAME                       ID              SIZE      MODIFIED\n"
        "qwen2.5-coder:0.5b         5a2c            397 MB    2 hours ago\n"
        "garbage line without size info\n"
        "llama3.1:8b                a1b2            4.7 GB    1 day ago\n"
    )
    out = oc.parse_ollama_list_output(stdout)
    names = [m.name for m in out]
    assert names == ["qwen2.5-coder:0.5b", "llama3.1:8b"]


def test_parse_list_handles_kb_unit():
    stdout = (
        "NAME    ID    SIZE    MODIFIED\n"
        "tiny:1b    abc    100 kB    1 minute ago\n"
    )
    out = oc.parse_ollama_list_output(stdout)
    assert len(out) == 1
    assert out[0].size_gb < 0.001  # 100 kB = ~9.5e-5 GB


# ─── parse_ollama_api_tags ─────────────────────────────────────────────────


def test_parse_api_tags_extracts_models():
    payload = {
        "models": [
            {
                "name": "qwen2.5-coder:0.5b",
                "modified_at": "2026-05-07T13:42:18.123456789Z",
                "size": 397441024,
                "digest": "5a2c8a83b2e8abcdef",
                "details": {"family": "qwen"},
            },
        ],
    }
    out = oc.parse_ollama_api_tags(payload)
    assert len(out) == 1
    m = out[0]
    assert m.name == "qwen2.5-coder:0.5b"
    assert 0.3 < m.size_gb < 0.5
    # digest truncated to 12 chars
    assert m.digest == "5a2c8a83b2e8"
    # modified clipped to 19 chars (ISO ts down to seconds)
    assert m.modified == "2026-05-07T13:42:18"


def test_parse_api_tags_handles_empty_models_array():
    assert oc.parse_ollama_api_tags({"models": []}) == []
    assert oc.parse_ollama_api_tags({}) == []


def test_parse_api_tags_skips_entries_without_name():
    payload = {"models": [
        {"size": 100, "digest": "x"},  # no name
        {"name": "valid:tag", "size": 200},
    ]}
    out = oc.parse_ollama_api_tags(payload)
    assert len(out) == 1
    assert out[0].name == "valid:tag"


def test_parse_api_tags_handles_model_field_alias():
    """Some ollama versions use 'model' instead of 'name' in /api/tags."""
    payload = {"models": [{"model": "alt:tag", "size": 0}]}
    out = oc.parse_ollama_api_tags(payload)
    assert len(out) == 1
    assert out[0].name == "alt:tag"


# ─── _normalize_to_gb ──────────────────────────────────────────────────────


@pytest.mark.parametrize("num,unit,expected_min,expected_max", [
    (1.0, "GB", 0.99, 1.01),
    (1024, "MB", 0.99, 1.01),
    (1024 * 1024, "kB", 0.99, 1.01),
    (1024 ** 3, "B", 0.99, 1.01),
    (4.7, "GB", 4.69, 4.71),
])
def test_normalize_to_gb(num, unit, expected_min, expected_max):
    out = oc._normalize_to_gb(num, unit)
    assert expected_min <= out <= expected_max


# ─── is_daemon_running / is_cli_installed ──────────────────────────────────


def test_is_daemon_running_returns_false_on_connection_refused(monkeypatch):
    """No daemon → URLError, must return False (not raise)."""
    import urllib.error

    def boom(url, timeout=None):
        raise urllib.error.URLError("Connection refused")
    monkeypatch.setattr(oc.urllib.request, "urlopen", boom)
    assert oc.is_daemon_running() is False


def test_is_daemon_running_returns_true_on_200(monkeypatch):
    monkeypatch.setattr(oc.urllib.request, "urlopen",
                        lambda url, timeout=None: MagicMock())
    assert oc.is_daemon_running() is True


def test_is_cli_installed_via_shutil_which(monkeypatch):
    monkeypatch.setattr(oc.shutil, "which",
                        lambda c: "/usr/local/bin/ollama"
                        if c == "ollama" else None)
    assert oc.is_cli_installed() is True

    monkeypatch.setattr(oc.shutil, "which", lambda c: None)
    assert oc.is_cli_installed() is False


# ─── list_models composition ──────────────────────────────────────────────


def test_list_models_uses_http_when_available(monkeypatch):
    """Daemon running → HTTP path returns parsed list, no subprocess."""
    payload = {
        "models": [
            {"name": "qwen:0.5b", "size": 400000000, "modified_at": "2026"},
        ],
    }
    fake_resp = MagicMock()
    fake_resp.read.return_value = json.dumps(payload).encode()
    fake_resp.__enter__ = lambda self: self
    fake_resp.__exit__ = lambda self, *a: None
    monkeypatch.setattr(oc.urllib.request, "urlopen",
                        lambda url, timeout=None: fake_resp)

    # Subprocess should NEVER be called when HTTP works
    sp_called = {"n": 0}

    def sp(*a, **kw):
        sp_called["n"] += 1
        return MagicMock(returncode=0, stdout="")
    monkeypatch.setattr(oc.subprocess, "run", sp)

    out = oc.list_models()
    assert len(out) == 1
    assert out[0].name == "qwen:0.5b"
    assert sp_called["n"] == 0


def test_list_models_falls_back_to_cli_when_http_fails(monkeypatch):
    """No daemon (URLError) + CLI installed → run `ollama list`."""
    import urllib.error
    monkeypatch.setattr(oc.urllib.request, "urlopen",
                        lambda url, timeout=None: (_ for _ in ()).throw(
                            urllib.error.URLError("Connection refused")))
    monkeypatch.setattr(oc.shutil, "which",
                        lambda c: "/usr/bin/ollama" if c == "ollama" else None)
    fake_result = MagicMock(returncode=0, stdout=(
        "NAME    ID    SIZE    MODIFIED\n"
        "tiny:1b    a1    100 MB    1 minute ago\n"
    ))
    monkeypatch.setattr(oc.subprocess, "run",
                        lambda *a, **kw: fake_result)

    out = oc.list_models()
    assert len(out) == 1
    assert out[0].name == "tiny:1b"


def test_list_models_returns_empty_when_neither_path_works(monkeypatch):
    """No daemon + no CLI → empty list, no crash."""
    import urllib.error
    monkeypatch.setattr(oc.urllib.request, "urlopen",
                        lambda url, timeout=None: (_ for _ in ()).throw(
                            urllib.error.URLError("nope")))
    monkeypatch.setattr(oc.shutil, "which", lambda c: None)
    assert oc.list_models() == []


# ─── delete_model / pull_model ─────────────────────────────────────────────


def test_delete_model_returns_false_when_cli_missing(monkeypatch):
    monkeypatch.setattr(oc.shutil, "which", lambda c: None)
    ok, msg = oc.delete_model("qwen:0.5b")
    assert ok is False
    assert "not installed" in msg.lower()


def test_delete_model_handles_nonzero_exit(monkeypatch):
    monkeypatch.setattr(oc.shutil, "which",
                        lambda c: "/usr/bin/ollama")
    fake = MagicMock(returncode=1, stderr="model not found\n", stdout="")
    monkeypatch.setattr(oc.subprocess, "run", lambda *a, **kw: fake)
    ok, msg = oc.delete_model("nonexistent:1b")
    assert ok is False
    assert "model not found" in msg


def test_delete_model_success_path(monkeypatch):
    monkeypatch.setattr(oc.shutil, "which",
                        lambda c: "/usr/bin/ollama")
    fake = MagicMock(returncode=0, stdout="deleted qwen:0.5b\n", stderr="")
    monkeypatch.setattr(oc.subprocess, "run", lambda *a, **kw: fake)
    ok, msg = oc.delete_model("qwen:0.5b")
    assert ok is True
    assert "deleted" in msg


def test_pull_model_returns_false_when_cli_missing(monkeypatch):
    monkeypatch.setattr(oc.shutil, "which", lambda c: None)
    ok, msg = oc.pull_model("qwen:0.5b")
    assert ok is False
    assert "not installed" in msg.lower()


# ─── default_local_model_for_provider option roundtrip + #75 wiring ───────


def test_default_options_includes_local_model_mapping():
    from bterminal.config import _OPTIONS_DEFAULTS
    assert "default_local_model_for_provider" in _OPTIONS_DEFAULTS
    assert _OPTIONS_DEFAULTS["default_local_model_for_provider"] == {}


def test_aider_build_argv_uses_user_default_when_set(tmp_path, monkeypatch):
    """User mapped Aider → custom model in OptionsDialog → argv uses it
    when session opts has no explicit override."""
    import json as _json
    import stat
    from bterminal.providers import load_providers_config
    from bterminal.providers.aider import AiderProvider

    fake = tmp_path / "aider"
    fake.write_text("#!/bin/sh\necho fake\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    cfg = _json.loads(_json.dumps(
        load_providers_config()["providers"]["aider"]))
    cfg["binary"]["search_paths"] = [str(fake)]
    p = AiderProvider(cfg)

    from bterminal import config
    monkeypatch.setitem(
        config._OPTIONS,
        "default_local_model_for_provider",
        {"aider": "openai/llama3.1:8b"},
    )

    argv = p.build_argv({}, "")
    m_idx = argv.index("--model")
    assert argv[m_idx + 1] == "openai/llama3.1:8b"


def test_aider_session_override_wins_over_global_default(tmp_path, monkeypatch):
    """If session has its own model field, it takes precedence over
    the global mapping."""
    import json as _json
    import stat
    from bterminal.providers import load_providers_config
    from bterminal.providers.aider import AiderProvider

    fake = tmp_path / "aider"
    fake.write_text("#!/bin/sh\n")
    fake.chmod(fake.stat().st_mode | stat.S_IXUSR)
    cfg = _json.loads(_json.dumps(
        load_providers_config()["providers"]["aider"]))
    cfg["binary"]["search_paths"] = [str(fake)]
    p = AiderProvider(cfg)

    from bterminal import config
    monkeypatch.setitem(
        config._OPTIONS,
        "default_local_model_for_provider",
        {"aider": "openai/llama3.1:8b"},  # global
    )
    argv = p.build_argv({"model": "openai/qwen:14b"}, "")  # session
    m_idx = argv.index("--model")
    assert argv[m_idx + 1] == "openai/qwen:14b"
