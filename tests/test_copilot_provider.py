"""Unit tests for bterminal.providers.copilot.CopilotProvider — T1.4.

T1.4 is a SKELETON — most capabilities are off. These tests verify:
- The skeleton can be constructed from defaults.json without crashing.
- Capabilities baseline is mostly False (T1.4 starting point).
- find_binary works (delegates to configured search_paths).
- The deferred capabilities (session_log, intro_prompt, ...) gracefully
  no-op until later tasks (T2.3, T3.2, T4.1) flip them on.
"""
from __future__ import annotations

import json
import stat

import pytest

from bterminal.providers import load_providers_config
from bterminal.providers.base import ProviderCapabilities, SessionStats
from bterminal.providers.copilot import CopilotProvider


@pytest.fixture
def copilot_config():
    return load_providers_config()["providers"]["copilot"]


@pytest.fixture
def provider(copilot_config):
    return CopilotProvider(copilot_config)


def _make_executable(path):
    path.write_text("#!/bin/sh\necho fake copilot\n")
    path.chmod(path.stat().st_mode | stat.S_IXUSR | stat.S_IXGRP | stat.S_IXOTH)


# ─── Construction + metadata ─────────────────────────────────────────────────

def test_provider_metadata(provider):
    assert provider.name == "copilot"
    assert provider.display.short_label == "Copilot"
    assert provider.display.long_label == "GitHub Copilot CLI"
    assert provider.display.icon == "🤖"


def test_capabilities_baseline_after_t4_2(provider):
    """Baseline after T4.2: T2.3 (build_argv) + T3.5 (stats) + T3.7
    (rules_inject) + T4.1/T4.2 (task_auto_trigger via events.jsonl
    idle monitor) all True. Others flip in later tasks:
        T4.3: granular_permissions
        T4.4: plan_mode, autopilot
    """
    c = provider.capabilities
    assert isinstance(c, ProviderCapabilities)
    # Flipped True in T2.3
    assert c.intro_prompt is True
    assert c.resume_flag is True
    assert c.continue_flag is True
    assert c.skip_permissions is True
    # Flipped True in T3.5
    assert c.session_log is True
    assert c.cost_in_log is True
    assert c.stats_bar is True
    assert c.stats_bar_no_plan_usage is True  # always — Copilot has no plan API
    # Flipped True in T3.7
    assert c.rules_inject is True
    # Flipped True in T4.2 (events.jsonl idle detection from T4.1)
    assert c.task_auto_trigger is True
    # Flipped True in T4.3 (allowed_tools textarea)
    assert c.granular_permissions is True
    # Flipped True in T4.4 (plan mode toggle)
    assert c.plan_mode is True
    # Still disabled (later tasks)
    assert c.usage_api is False               # never (Copilot has no public API)
    assert c.autopilot is False               # T4.4 follow-up (autopilot loop)
    assert c.mcp_support is False             # later


def test_metadata_present_even_when_caps_off(provider):
    """Path templates / context_file / default_model stay populated
    so capabilities can flip True without re-editing defaults.json."""
    c = provider.capabilities
    assert c.session_log_path == "~/.copilot/session-state/{session_id}/events.jsonl"
    assert c.session_index_db_path == "~/.copilot/session-store.db"
    assert c.context_file == "AGENTS.md"
    assert c.context_file_cumulative is False
    assert c.default_model == "claude-sonnet-4-5"


# ─── find_binary (works today) ───────────────────────────────────────────────

def test_find_binary_returns_first_match(tmp_path, copilot_config):
    fake = tmp_path / "copilot"
    _make_executable(fake)
    cfg = json.loads(json.dumps(copilot_config))
    cfg["binary"]["search_paths"] = [str(fake)]
    p = CopilotProvider(cfg)
    assert p.find_binary() == str(fake)


def test_find_binary_returns_none_when_missing(tmp_path, copilot_config, monkeypatch):
    cfg = json.loads(json.dumps(copilot_config))
    cfg["binary"]["search_paths"] = [str(tmp_path / "nope")]
    monkeypatch.setattr("bterminal.providers.copilot.shutil.which",
                        lambda *a, **kw: None)
    p = CopilotProvider(cfg)
    assert p.find_binary() is None


# ─── build_argv (T2.3 — full implementation) ────────────────────────────────


def _make_provider_with_fake_binary(tmp_path, copilot_config):
    fake = tmp_path / "copilot"
    _make_executable(fake)
    cfg = json.loads(json.dumps(copilot_config))
    cfg["binary"]["search_paths"] = [str(fake)]
    return CopilotProvider(cfg), str(fake)


def test_build_argv_returns_empty_when_binary_missing(copilot_config, monkeypatch):
    cfg = json.loads(json.dumps(copilot_config))
    cfg["binary"]["search_paths"] = ["/nope/copilot"]
    monkeypatch.setattr("bterminal.providers.copilot.shutil.which",
                        lambda *a, **kw: None)
    p = CopilotProvider(cfg)
    assert p.build_argv({}, "") == []


def test_argv_default_minimal(tmp_path, copilot_config):
    """Empty config + no intro → just binary + tui_safe flags."""
    p, fake = _make_provider_with_fake_binary(tmp_path, copilot_config)
    argv = p.build_argv({}, "")
    assert argv == [
        fake, "--no-mouse", "--plain-diff", "--no-color",
    ]


def test_argv_with_intro_uses_interactive_flag(tmp_path, copilot_config):
    """Default intro mode: -i (interactive — TUI starts with auto-prompt)."""
    p, fake = _make_provider_with_fake_binary(tmp_path, copilot_config)
    argv = p.build_argv({}, "Hello Copilot")
    assert argv == [
        fake, "--no-mouse", "--plain-diff", "--no-color",
        "-i", "Hello Copilot",
    ]


def test_argv_headless_uses_p_flag(tmp_path, copilot_config):
    """provider_options.headless=true → -p (one-shot, no TUI)."""
    p, fake = _make_provider_with_fake_binary(tmp_path, copilot_config)
    argv = p.build_argv(
        {"provider_options": {"headless": True}}, "Run linter")
    assert "-p" in argv
    assert "-i" not in argv
    # Intro follows the -p flag
    p_idx = argv.index("-p")
    assert argv[p_idx + 1] == "Run linter"


def test_argv_with_resume(tmp_path, copilot_config):
    """resume=True + capability → --resume in argv (no UUID pre-T4.5)."""
    p, fake = _make_provider_with_fake_binary(tmp_path, copilot_config)
    argv = p.build_argv({"resume": True}, "")
    assert "--resume" in argv
    assert "--continue" not in argv


def test_argv_with_continue(tmp_path, copilot_config):
    p, _ = _make_provider_with_fake_binary(tmp_path, copilot_config)
    argv = p.build_argv({"continue": True}, "")
    assert "--continue" in argv
    assert "--resume" not in argv


def test_argv_resume_takes_precedence_over_continue(tmp_path, copilot_config):
    p, _ = _make_provider_with_fake_binary(tmp_path, copilot_config)
    argv = p.build_argv({"resume": True, "continue": True}, "")
    assert "--resume" in argv
    assert "--continue" not in argv


def test_argv_with_yolo(tmp_path, copilot_config):
    """skip_permissions=True → --yolo (Copilot's equivalent of
    --dangerously-skip-permissions)."""
    p, _ = _make_provider_with_fake_binary(tmp_path, copilot_config)
    argv = p.build_argv({"skip_permissions": True}, "")
    assert "--yolo" in argv


def test_argv_with_model_override(tmp_path, copilot_config):
    p, _ = _make_provider_with_fake_binary(tmp_path, copilot_config)
    argv = p.build_argv({"model": "gpt-5"}, "")
    assert argv[-2:] == ["--model", "gpt-5"]


def test_argv_with_add_dir_for_project_dir(tmp_path, copilot_config):
    """project_dir → --add-dir <path>."""
    p, _ = _make_provider_with_fake_binary(tmp_path, copilot_config)
    argv = p.build_argv({"project_dir": "/home/me/myproj"}, "")
    add_dir_idx = argv.index("--add-dir")
    assert argv[add_dir_idx + 1] == "/home/me/myproj"


def test_argv_with_json_output(tmp_path, copilot_config):
    """json_output=True → --output-format json (used by T4.1 idle detection)."""
    p, _ = _make_provider_with_fake_binary(tmp_path, copilot_config)
    argv = p.build_argv(
        {"provider_options": {"json_output": True}}, "")
    assert "--output-format" in argv
    of_idx = argv.index("--output-format")
    assert argv[of_idx + 1] == "json"


def test_argv_full_session_with_provider_options(tmp_path, copilot_config):
    """R4.2 schema: complete session config with all flags."""
    p, fake = _make_provider_with_fake_binary(tmp_path, copilot_config)
    argv = p.build_argv(
        {
            "project_dir": "/tmp/project",
            "provider_options": {
                "resume": True,
                "skip_permissions": True,
                "model": "claude-sonnet-4-5",
                "json_output": True,
            },
        },
        "Continue the refactor",
    )
    assert argv == [
        fake,
        "--no-mouse", "--plain-diff", "--no-color",
        "--resume", "--yolo",
        "--model", "claude-sonnet-4-5",
        "--add-dir", "/tmp/project",
        "--output-format", "json",
        "-i", "Continue the refactor",
    ]


def test_argv_capability_disabled_drops_flag(tmp_path, copilot_config):
    """If a capability is False, the corresponding flag is suppressed
    even when config requests it. Forward-compat for future-version
    configs that disable a feature."""
    cfg = json.loads(json.dumps(copilot_config))
    fake = tmp_path / "copilot"
    _make_executable(fake)
    cfg["binary"]["search_paths"] = [str(fake)]
    cfg["capabilities"]["resume_flag"] = False
    cfg["capabilities"]["skip_permissions"] = False
    p = CopilotProvider(cfg)
    argv = p.build_argv({"resume": True, "skip_permissions": True}, "")
    assert "--resume" not in argv
    assert "--yolo" not in argv


def test_argv_intro_dropped_when_capability_disabled(tmp_path, copilot_config):
    cfg = json.loads(json.dumps(copilot_config))
    fake = tmp_path / "copilot"
    _make_executable(fake)
    cfg["binary"]["search_paths"] = [str(fake)]
    cfg["capabilities"]["intro_prompt"] = False
    p = CopilotProvider(cfg)
    argv = p.build_argv({}, "should be dropped")
    assert "should be dropped" not in argv
    assert "-i" not in argv
    assert "-p" not in argv


# ─── Deferred behaviors gracefully no-op ─────────────────────────────────────

def test_session_log_glob_works_after_t3_5(provider):
    """T3.5: session_log capability flipped to True; glob returns the
    events.jsonl path template (used by CopilotStatsReader)."""
    glob_path = provider.session_log_glob("/any/project")
    assert glob_path is not None
    assert glob_path.endswith("events.jsonl")
    assert "session-state" in glob_path


def test_session_log_glob_returns_none_when_capability_disabled(copilot_config):
    """Defensive: if a future config flips session_log back to False,
    the glob method gracefully returns None (so the widget skips)."""
    cfg = json.loads(json.dumps(copilot_config))
    cfg["capabilities"]["session_log"] = False
    p = CopilotProvider(cfg)
    assert p.session_log_glob("/any/project") is None


def test_parse_session_stats_returns_empty(provider, tmp_path):
    """T1.4: empty stats. T3.2 implements real events.jsonl parser."""
    log = tmp_path / "events.jsonl"
    log.write_text('{"type": "tool.execution_complete"}\n')
    stats = provider.parse_session_stats(str(log))
    assert isinstance(stats, SessionStats)
    assert stats.input_tokens == 0
    assert stats.cost_usd == 0.0


def test_fetch_plan_usage_always_none(provider):
    """Copilot has no public usage API → always None."""
    assert provider.fetch_plan_usage() is None


def test_detect_idle_default_true(provider, tmp_path, monkeypatch):
    """Without session_id and no events.jsonl on disk, detect_idle
    returns True (nothing in flight). 2026-05-07: this test used to
    rely on the real $HOME being empty, but now that real copilot may
    populate ~/.copilot/session-state/ at install time we point HOME
    at an isolated tmpdir to keep the test deterministic across hosts."""
    monkeypatch.setenv("HOME", str(tmp_path))
    assert provider.detect_idle(terminal=None, session_id=None) is True


def test_dialog_schema_returns_copilot_specific_fields(provider):
    """T2.6 + T4.3 + #54: CopilotProvider returns skip_permissions
    checkbox + plan_mode (T4.4) + allowed_tools textarea (gated on
    granular_permissions). Task #54 (2026-05-07) removed the model
    combo — model selection happens at runtime via the native /model
    slash command, not in the BT dialog."""
    schema = provider.get_dialog_schema()
    keys = [entry[0] for entry in schema]
    types = [entry[1] for entry in schema]
    assert keys == ["skip_permissions", "plan_mode", "allowed_tools", "image_paste_template"]
    assert types == ["checkbox", "checkbox", "textarea", "text"]
