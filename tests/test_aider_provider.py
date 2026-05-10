"""Tests for AiderProvider (task #3 / #75 in audit doc).

Coverage:
  - construction from defaults.json without crashing
  - capability matrix matches audit § 9 plan
  - find_binary search path order + shutil.which fallback
  - build_argv variants (with/without intro_prompt, project_dir,
    resume, skip_permissions, model override, custom endpoint)
  - session_log_glob path template
  - parse_session_stats reads aider markdown chat history
  - registry integration: AiderProvider registered + instantiable
"""
from __future__ import annotations

import json
import stat
import sys
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.providers.aider import AiderProvider, _parse_num_with_suffix
from bterminal.providers.base import ProviderCapabilities, SessionStats


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def aider_config():
    return load_providers_config()["providers"]["aider"]


@pytest.fixture
def provider(aider_config):
    return AiderProvider(aider_config)


def _make_executable(path: Path):
    path.write_text("#!/bin/sh\necho fake aider\n")
    path.chmod(path.stat().st_mode
               | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ─── Construction + metadata ────────────────────────────────────────────────


def test_provider_metadata(provider):
    assert provider.name == "aider"
    assert provider.display.icon == "🦫"
    assert provider.display.short_label == "Aider"
    assert provider.display.long_label == "Aider"
    assert provider.display.color == "#fab387"
    assert provider.display.icon_path == "icons/aider.svg"


def test_capabilities_match_audit_plan(provider):
    """Audit § 9 capability matrix:
      intro_prompt=true, session_log=true, cost_in_log=false,
      rules_inject=true, task_auto_trigger=true, stats_bar=true,
      stats_bar_no_plan_usage=true, plan_mode=false, mcp_support=false.
    Plus #75-specific: local_endpoint_url set."""
    c = provider.capabilities
    assert isinstance(c, ProviderCapabilities)
    # Enabled
    assert c.intro_prompt is True
    assert c.resume_flag is True
    assert c.skip_permissions is True
    assert c.session_log is True
    assert c.rules_inject is True
    assert c.task_auto_trigger is True
    assert c.stats_bar is True
    assert c.stats_bar_no_plan_usage is True
    # Disabled
    assert c.continue_flag is False
    assert c.granular_permissions is False
    assert c.supports_sudo is False
    assert c.usage_api is False
    assert c.cost_in_log is False
    assert c.plan_mode is False
    assert c.autopilot is False
    assert c.mcp_support is False
    # New capability (#75)
    assert c.local_endpoint_url == "http://localhost:11434/v1"
    # Defaults
    assert c.default_model == "openai/qwen2.5-coder:0.5b"
    assert c.context_file == "AIDER.md"
    assert c.session_log_path == "{project_dir}/.aider.chat.history.md"


def test_default_model_targets_qwen_coder_0_5b(provider):
    """Audit § 8 Q3 decision — Qwen2.5-Coder 0.5B as the smallest
    model that fits VM-class hardware. Pinned in defaults so a fresh
    install actually has something runnable."""
    assert provider.capabilities.default_model == "openai/qwen2.5-coder:0.5b"


# ─── find_binary ────────────────────────────────────────────────────────────


def test_find_binary_returns_first_match(tmp_path, aider_config):
    fake = tmp_path / "aider"
    _make_executable(fake)
    cfg = json.loads(json.dumps(aider_config))
    cfg["binary"]["search_paths"] = [str(fake)]
    p = AiderProvider(cfg)
    assert p.find_binary() == str(fake)


def test_find_binary_walks_glob_patterns(tmp_path, aider_config):
    """Search_paths can include globs (e.g. pipx venvs path)."""
    venv_dir = tmp_path / "pipx" / "venvs" / "aider-chat" / "bin"
    venv_dir.mkdir(parents=True)
    fake = venv_dir / "aider"
    _make_executable(fake)
    cfg = json.loads(json.dumps(aider_config))
    cfg["binary"]["search_paths"] = [
        str(tmp_path / "pipx" / "venvs" / "*" / "bin" / "aider"),
    ]
    p = AiderProvider(cfg)
    assert p.find_binary() == str(fake)


def test_find_binary_falls_back_to_which(aider_config, monkeypatch):
    """No search_paths hits → shutil.which('aider') fallback."""
    cfg = json.loads(json.dumps(aider_config))
    cfg["binary"]["search_paths"] = ["/no/such/path"]
    monkeypatch.setattr(
        "bterminal.providers.aider.shutil.which",
        lambda c: "/usr/local/bin/aider" if c == "aider" else None,
    )
    p = AiderProvider(cfg)
    assert p.find_binary() == "/usr/local/bin/aider"


def test_find_binary_returns_none_when_missing(aider_config, monkeypatch):
    cfg = json.loads(json.dumps(aider_config))
    cfg["binary"]["search_paths"] = ["/no/such/path"]
    monkeypatch.setattr(
        "bterminal.providers.aider.shutil.which", lambda c: None,
    )
    assert AiderProvider(cfg).find_binary() is None


# ─── build_argv ─────────────────────────────────────────────────────────────


def _provider_with_fake_binary(tmp_path, cfg_dict):
    fake = tmp_path / "aider"
    _make_executable(fake)
    cfg = json.loads(json.dumps(cfg_dict))
    cfg["binary"]["search_paths"] = [str(fake)]
    return AiderProvider(cfg), str(fake)


def test_build_argv_returns_empty_when_binary_missing(aider_config, monkeypatch):
    cfg = json.loads(json.dumps(aider_config))
    cfg["binary"]["search_paths"] = ["/nope/aider"]
    monkeypatch.setattr(
        "bterminal.providers.aider.shutil.which", lambda c: None,
    )
    p = AiderProvider(cfg)
    assert p.build_argv({}, "") == []


def test_build_argv_default_uses_capability_model_and_endpoint(
    tmp_path, aider_config,
):
    """Bare config + no opts → defaults from capabilities flow into argv."""
    p, fake = _provider_with_fake_binary(tmp_path, aider_config)
    argv = p.build_argv({}, "")
    assert argv[0] == fake
    # --model openai/qwen2.5-coder:0.5b
    assert "--model" in argv
    m_idx = argv.index("--model")
    assert argv[m_idx + 1] == "openai/qwen2.5-coder:0.5b"
    # --openai-api-base http://localhost:11434/v1
    assert "--openai-api-base" in argv
    b_idx = argv.index("--openai-api-base")
    assert argv[b_idx + 1] == "http://localhost:11434/v1"
    # --openai-api-key dummy (Aider requires non-empty even for local)
    assert "--openai-api-key" in argv
    k_idx = argv.index("--openai-api-key")
    assert argv[k_idx + 1] == "dummy"
    # tui-safe flags
    assert "--no-stream" in argv
    assert "--no-show-model-warnings" in argv


def test_build_argv_no_intro_prompt_in_argv(tmp_path, aider_config):
    """Aider has no --message-init flag — intro_prompt MUST NOT land
    in argv. BT injects via PTY feed_child after spawn."""
    p, _ = _provider_with_fake_binary(tmp_path, aider_config)
    intro = "You are working in BTerminal. Read CLAUDE.md..."
    argv = p.build_argv({"project_dir": "/tmp/x"}, intro)
    assert intro not in argv
    # And no flag-shape variant either
    for s in argv:
        assert intro not in s


def test_build_argv_with_project_dir_appends_positional(tmp_path, aider_config):
    p, _ = _provider_with_fake_binary(tmp_path, aider_config)
    argv = p.build_argv({"project_dir": "/home/u/myproj"}, "")
    assert argv[-1] == "/home/u/myproj"


def test_build_argv_without_project_dir_no_positional(tmp_path, aider_config):
    """Aider falls back to spawn cwd when no positional given."""
    p, _ = _provider_with_fake_binary(tmp_path, aider_config)
    argv = p.build_argv({}, "")
    # Last argv element is some flag value — never a path-ish positional.
    # Specifically the last token must be one of the tui_safe flags.
    assert argv[-1] in ("--no-stream", "--no-show-model-warnings",
                         "dummy")  # a flag value


def test_build_argv_resume_adds_restore_history(tmp_path, aider_config):
    p, _ = _provider_with_fake_binary(tmp_path, aider_config)
    argv = p.build_argv({"resume": True}, "")
    assert "--restore-chat-history" in argv


def test_build_argv_skip_permissions_adds_yes_always(tmp_path, aider_config):
    p, _ = _provider_with_fake_binary(tmp_path, aider_config)
    argv = p.build_argv({"skip_permissions": True}, "")
    assert "--yes-always" in argv


def test_build_argv_model_override(tmp_path, aider_config):
    p, _ = _provider_with_fake_binary(tmp_path, aider_config)
    argv = p.build_argv({"model": "openai/llama3.1:8b"}, "")
    m_idx = argv.index("--model")
    assert argv[m_idx + 1] == "openai/llama3.1:8b"


def test_build_argv_custom_endpoint_override(tmp_path, aider_config):
    p, _ = _provider_with_fake_binary(tmp_path, aider_config)
    argv = p.build_argv(
        {"local_endpoint_url": "http://192.168.0.42:11434/v1"}, "",
    )
    b_idx = argv.index("--openai-api-base")
    assert argv[b_idx + 1] == "http://192.168.0.42:11434/v1"


def test_build_argv_provider_options_unwrap(tmp_path, aider_config):
    """R4.2 schema: opts can be nested under provider_options. Both
    flat (legacy) and nested (current) forms work — same as Claude/
    Copilot providers."""
    p, _ = _provider_with_fake_binary(tmp_path, aider_config)
    argv = p.build_argv(
        {"provider_options": {"resume": True, "skip_permissions": True}}, "",
    )
    assert "--restore-chat-history" in argv
    assert "--yes-always" in argv


# ─── session_log_glob ───────────────────────────────────────────────────────


def test_session_log_glob_uses_project_dir(provider):
    p = provider.session_log_glob("/home/u/myproj")
    assert p == "/home/u/myproj/.aider.chat.history.md"


def test_session_log_glob_strips_trailing_slash(provider):
    p = provider.session_log_glob("/tmp/proj/")
    assert p == "/tmp/proj/.aider.chat.history.md"


def test_session_log_glob_returns_none_for_empty_project_dir(provider):
    assert provider.session_log_glob("") is None
    assert provider.session_log_glob(None) is None


def test_session_log_glob_returns_none_when_capability_off(aider_config):
    cfg = json.loads(json.dumps(aider_config))
    cfg["capabilities"]["session_log"] = False
    p = AiderProvider(cfg)
    assert p.session_log_glob("/tmp/x") is None


# ─── parse_session_stats ────────────────────────────────────────────────────


def test_parse_session_stats_returns_zero_for_missing_log(provider):
    out = provider.parse_session_stats("/no/such/file.md")
    assert isinstance(out, SessionStats)
    assert out.input_tokens == 0
    assert out.output_tokens == 0
    assert out.response_count == 0


def test_parse_session_stats_grep_tokens_lines(provider, tmp_path):
    log = tmp_path / ".aider.chat.history.md"
    log.write_text(
        "#### Make it work\n"
        "I'll make it work.\n"
        "Tokens: 1.5k sent, 234 received.\n"
        "\n"
        "#### One more thing\n"
        "On it.\n"
        "Tokens: 12,345 sent, 5678 received.\n",
    )
    s = provider.parse_session_stats(str(log))
    # 1500 + 12345 = 13845 sent
    assert s.input_tokens == 13845
    # 234 + 5678 = 5912 received
    assert s.output_tokens == 5912


def test_parse_session_stats_counts_user_turns_as_responses(provider, tmp_path):
    log = tmp_path / ".aider.chat.history.md"
    log.write_text(
        "#### First ask\n"
        "Reply 1\n"
        "#### Second ask\n"
        "Reply 2\n"
        "#### Third ask\n"
        "Reply 3\n",
    )
    s = provider.parse_session_stats(str(log))
    assert s.response_count == 3


def test_parse_session_stats_picks_up_model_marker(provider, tmp_path):
    log = tmp_path / ".aider.chat.history.md"
    log.write_text(
        "Started session with --model openai/llama3.1:8b at 2026-05-07\n"
        "#### Hello\n"
        "Hi there.\n",
    )
    s = provider.parse_session_stats(str(log))
    assert s.model == "openai/llama3.1:8b"


def test_parse_session_stats_falls_back_to_default_model(provider, tmp_path):
    """Log without --model line → fall back to capability default."""
    log = tmp_path / ".aider.chat.history.md"
    log.write_text("#### Just a question\nReply\n")
    s = provider.parse_session_stats(str(log))
    assert s.model == "openai/qwen2.5-coder:0.5b"


def test_parse_session_stats_handles_unreadable_log(provider, tmp_path):
    log = tmp_path / "broken.md"
    log.write_bytes(b"\xff\xfe\x00\x00 bad encoding")
    s = provider.parse_session_stats(str(log))
    # Doesn't crash; encoding=replace handles it.
    assert isinstance(s, SessionStats)


# ─── _parse_num_with_suffix helper ─────────────────────────────────────────


@pytest.mark.parametrize("inp_n,inp_s,expected", [
    ("1.5", "k", 1500),
    ("12,345", "", 12345),
    ("2", "M", 2_000_000),
    ("100", "", 100),
    ("0.5", "k", 500),
    ("invalid", "k", 0),
    ("", "", 0),
])
def test_parse_num_with_suffix(inp_n, inp_s, expected):
    assert _parse_num_with_suffix(inp_n, inp_s) == expected


# ─── Idle detection ────────────────────────────────────────────────────────


def test_detect_idle_default_true(provider):
    """Aider has no ready marker — default idle=True trusts caller's
    debounce (BT _idle_check_tick already does ~10s VTE-quiet detection)."""
    assert provider.detect_idle(terminal=None, session_id=None) is True


# ─── Registry integration ──────────────────────────────────────────────────


def test_aider_registered_in_provider_classes():
    """_PROVIDER_CLASSES is the gating dict — without an entry the
    config slice is silently skipped at registry init."""
    from bterminal.providers import _PROVIDER_CLASSES
    assert "aider" in _PROVIDER_CLASSES
    assert _PROVIDER_CLASSES["aider"] is AiderProvider


def test_default_registry_instantiates_aider():
    """End-to-end: default config + auto-load → aider available."""
    reg = ProviderRegistry(config=load_providers_config())
    assert reg.has("aider")
    aider = reg.get("aider")
    assert isinstance(aider, AiderProvider)
    assert aider.display.icon == "🦫"


def test_registry_lists_three_providers_alphabetically():
    """all() returns alphabetical order — aider before claude before
    copilot. Sidebar / dialog dropdown depend on this."""
    reg = ProviderRegistry(config=load_providers_config())
    names = [p.name for p in reg.all()]
    assert names == ["aider", "claude", "copilot"]


def test_default_provider_is_still_claude():
    """Adding aider must NOT promote it to default (Claude users
    wouldn't expect the 3rd provider to take over)."""
    reg = ProviderRegistry(config=load_providers_config())
    assert reg.default_provider().name == "claude"


# ─── Image paste template (#69 integration) ────────────────────────────────


def test_aider_has_image_paste_template():
    """Audit § 8 + #69: Aider inherits Copilot-style hint because
    underlying model (Qwen-Coder via Ollama) doesn't auto-call Read
    on bare paths the way Claude does."""
    cfg = load_providers_config()["providers"]["aider"]
    template = cfg.get("argv", {}).get("image_paste_template")
    assert template is not None
    assert "{path}" in template
