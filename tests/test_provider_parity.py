"""Provider parity matrix — Claude reference vs Aider equivalent (#19 / #91).

For each capability or behaviour Claude exhibits, assert that Aider
exhibits the same OBSERVABLE outcome — or, where it deliberately
diverges (different model dispatch, no usage API, no MCP), pin the
difference so future changes have to acknowledge it.

The matrix:

| Aspect               | Claude              | Aider                   |
|----------------------|---------------------|-------------------------|
| intro_prompt cap     | True                | True (parity)           |
| rules_inject cap     | True                | True (parity)           |
| task_auto_trigger    | True                | True (parity)           |
| stats_bar cap        | True                | True (parity, tokens-only) |
| session_log cap      | True                | True (parity, .md path) |
| skip_permissions     | True                | True (parity)           |
| build_argv shape     | non-empty + project | non-empty + project     |
| image_paste_template | null (native bytes) | hint with {path}        |
| should_inject_rules  | True for ai_config  | True for ai_config      |
| should_run_auto_...  | True for ai_config  | True for ai_config      |
| usage_api            | True                | False (local LLM)       |
| cost_in_log          | True                | False (off-process)     |
| mcp_support          | True                | False                   |

Each parity row has a Claude reference assertion + an Aider equivalent
assertion. Divergence rows are also pinned — silent divergence is the
exact bug class this test exists to catch.

Catches: provider dispatch divergence, capability gate regression on
either side, build_argv contract drift, image paste template
disappearing on Aider (which would silently break user image flow).
"""
from __future__ import annotations

import os
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

# Module-scoped registry — get_registry() is a singleton, so tests share
# the same instance and don't pay the JSON-load cost per test.
from bterminal.providers import get_registry  # noqa: E402
from bterminal.ui.terminal_tab import (  # noqa: E402
    should_inject_rules,
    should_run_auto_trigger,
)
from bterminal.ui.stats import (  # noqa: E402
    create_stats_reader_for_ai_config,
    stats_widget_options_for_ai_config,
)

REGISTRY = get_registry()
CLAUDE = REGISTRY.get("claude")
AIDER = REGISTRY.get("aider")


def _ai_cfg(provider: str, project_dir: str = "/tmp/parity-test") -> dict:
    """Minimal ai_config for the dispatch helpers."""
    return {
        "id": f"{provider}-1",
        "name": f"{provider}Session",
        "provider": provider,
        "project_dir": project_dir,
        "color": "#888888",
        "provider_options": {},
    }


# ─── Capability flag parity (canonical session machinery) ──────────────────


@pytest.mark.parametrize("cap_name", [
    "intro_prompt",
    "rules_inject",
    "task_auto_trigger",
    "stats_bar",
    "session_log",
    "skip_permissions",
    "resume_flag",
])
def test_capability_flag_parity(cap_name):
    """For every capability flag below, both providers must agree.
    The point of these tests is that any session-machinery feature
    Claude has, Aider must have too — otherwise BT's dispatch layer
    silently drops Aider out of the codepath."""
    claude_val = getattr(CLAUDE.capabilities, cap_name)
    aider_val = getattr(AIDER.capabilities, cap_name)
    assert claude_val is True, (
        f"Claude lost {cap_name!r} — broader session machinery "
        f"likely broken across all providers"
    )
    assert aider_val is True, (
        f"Aider lost {cap_name!r} — capability gate will silently "
        f"skip Aider tabs in BT's dispatch layer"
    )


@pytest.mark.parametrize("cap_name, claude_expected, aider_expected, why", [
    ("usage_api", True, False, "Aider uses local LLM, no remote usage endpoint"),
    ("cost_in_log", True, False, "Aider dispatches off-process via Ollama"),
    ("mcp_support", True, False, "Aider has no MCP server protocol"),
    ("supports_sudo", True, False, "Aider isn't a sudo-aware CLI"),
    ("continue_flag", True, False, "Aider has no --continue flag (only --restore)"),
    ("granular_permissions", False, False, "Neither granular today"),
    ("autopilot", False, False, "Neither has autopilot mode"),
    ("plan_mode", False, False, "Neither — only Copilot does"),
    ("stats_bar_no_plan_usage", False, True,
        "Aider runs in tokens-only mode (parity with Copilot)"),
])
def test_capability_flag_intentional_divergence(
        cap_name, claude_expected, aider_expected, why):
    """Pin the deliberate divergences. Without these tests, someone
    flipping a flag in defaults.json wouldn't realize they crossed a
    semantic boundary — these locks force the change to be explicit."""
    claude_val = getattr(CLAUDE.capabilities, cap_name)
    aider_val = getattr(AIDER.capabilities, cap_name)
    assert claude_val is claude_expected, (
        f"Claude.{cap_name} drifted to {claude_val} (expected "
        f"{claude_expected}). Reason locked: {why}"
    )
    assert aider_val is aider_expected, (
        f"Aider.{cap_name} drifted to {aider_val} (expected "
        f"{aider_expected}). Reason locked: {why}"
    )


# ─── Dispatch helper parity ────────────────────────────────────────────────


@pytest.mark.parametrize("provider", ["claude", "aider"])
def test_should_inject_rules_returns_true_for_both(provider):
    """T3.7 capability dispatch — both providers must opt into the
    periodic rules re-injection codepath. If either flips False, BT
    stops feeding rules through PTY and the user sees rule drift."""
    cfg = _ai_cfg(provider)
    assert should_inject_rules(cfg, REGISTRY) is True


@pytest.mark.parametrize("provider", ["claude", "aider"])
def test_should_run_auto_trigger_returns_true_for_both(provider):
    """T3.6 capability dispatch — both providers must opt into the
    [AUTO-TRIGGER] codepath. Aider must not silently drop tasks just
    because the gate didn't recognize it."""
    cfg = _ai_cfg(provider)
    assert should_run_auto_trigger(cfg, REGISTRY) is True


def test_dispatch_helpers_skip_unknown_provider():
    """Negative parity — both helpers must return False for an
    unregistered provider, not blow up. This is the contract that
    keeps SSH / local tabs out of AI codepaths."""
    cfg = _ai_cfg("future-cli-2030")
    assert should_inject_rules(cfg, REGISTRY) is False
    assert should_run_auto_trigger(cfg, REGISTRY) is False


# ─── build_argv shape parity ───────────────────────────────────────────────


def _stub_binary(provider, name):
    """Force find_binary() to resolve so build_argv emits a real
    argv even if the test runner doesn't have claude/aider on PATH."""
    provider._binary_spec["binary"] = f"/tmp/{name}"  # noqa: SLF001
    return provider


@pytest.mark.parametrize("provider_name", ["claude", "aider"])
def test_build_argv_emits_non_empty_list_starting_with_binary(provider_name):
    """Both providers must produce a non-empty argv whose first
    element is their binary path. Beyond that, argv shape differs
    intentionally — see the next two tests for per-provider specifics."""
    project_dir = "/tmp/parity-test-project"
    provider = REGISTRY.get(provider_name)
    _stub_binary(provider, provider_name)
    argv = provider.build_argv(
        {"project_dir": project_dir, "provider_options": {}},
        intro_prompt="hello",
    )
    assert argv, f"{provider_name} build_argv returned empty list"
    assert argv[0].endswith(provider_name), (
        f"{provider_name} argv doesn't lead with the binary: {argv}"
    )


def test_claude_build_argv_passes_project_dir_via_cwd_not_argv():
    """Claude argv carries the intro_prompt positionally + relies on
    BT's spawn cwd to set project context — project_dir is NOT in
    argv. Pin this so anyone tempted to 'fix' it understands the
    convention."""
    project_dir = "/tmp/parity-claude-cwd"
    _stub_binary(CLAUDE, "claude")
    argv = CLAUDE.build_argv(
        {"project_dir": project_dir, "provider_options": {}},
        intro_prompt="hello",
    )
    assert project_dir not in argv, (
        f"Claude argv unexpectedly carries project_dir: {argv}"
    )
    # Intro prompt IS in argv (positional)
    assert "hello" in argv


def test_aider_build_argv_appends_project_dir_positionally():
    """Aider argv ends with project_dir as a positional argument
    (aider's cwd detection wants it). Plus does NOT carry the
    intro_prompt in argv — Aider's mode is `stdin_feed`, BT injects
    intro after spawn via PTY."""
    project_dir = "/tmp/parity-aider-positional"
    _stub_binary(AIDER, "aider")
    argv = AIDER.build_argv(
        {"project_dir": project_dir, "provider_options": {}},
        intro_prompt="hello",
    )
    assert argv[-1] == project_dir, (
        f"Aider argv didn't end with project_dir: {argv}"
    )
    # Aider's stdin_feed mode means intro_prompt MUST NOT appear in argv
    assert "hello" not in argv, (
        f"Aider argv shouldn't carry intro_prompt (stdin_feed mode): {argv}"
    )


@pytest.mark.parametrize("provider_name", ["claude", "aider"])
def test_build_argv_returns_empty_when_binary_missing(provider_name):
    """Both providers must fail-soft when their binary isn't installed.
    Caller (BT spawn path) handles empty argv as 'show install hint'."""
    provider = REGISTRY.get(provider_name)
    # Force find_binary() to return None by patching the spec
    with patch.object(provider, "find_binary", return_value=None):
        argv = provider.build_argv(
            {"project_dir": "/tmp/no-binary", "provider_options": {}},
            intro_prompt="",
        )
    assert argv == [], f"{provider_name} should return [] when binary missing"


# ─── session_log_glob parity ───────────────────────────────────────────────


@pytest.mark.parametrize("provider_name, expected_suffix", [
    ("claude", ".jsonl"),
    ("aider",  ".aider.chat.history.md"),
])
def test_session_log_glob_resolves_to_expected_suffix(
        provider_name, expected_suffix):
    """Both providers must produce a non-None session log path when
    given a project_dir. The format differs (Claude JSONL vs Aider MD)
    but the discoverability is the same observable: BT's session log
    panel can show it."""
    provider = REGISTRY.get(provider_name)
    log = provider.session_log_glob("/tmp/parity-test-project")
    assert log is not None, f"{provider_name} session_log_glob returned None"
    assert log.endswith(expected_suffix), (
        f"{provider_name} session log path didn't end with "
        f"{expected_suffix}: {log}"
    )


def test_aider_session_log_glob_returns_none_without_project_dir():
    """Aider returns None for empty project_dir — its template needs
    a real cwd to resolve to a meaningful path. Claude's
    session_log_glob doesn't share this defensive check (it generates
    a dummy `~/.claude/projects//*.jsonl` pattern) — that's a real
    contract divergence pinned by the next test."""
    assert AIDER.session_log_glob("") is None


def test_claude_session_log_glob_does_not_validate_project_dir():
    """Pin: Claude's session_log_glob accepts an empty project_dir
    and substitutes into its template, producing a useless glob like
    `~/.claude/projects//*.jsonl`. That's the current contract — if
    Claude tightens up to match Aider's defensive None-return, this
    test must fail explicitly so the parity table gets re-balanced."""
    out = CLAUDE.session_log_glob("")
    assert out is not None, (
        "Claude tightened session_log_glob to validate project_dir — "
        "lift Aider's parity by removing this test, and update the "
        "previous test to assert both return None"
    )
    # Sanity: it really is the dummy-pattern case
    assert ".claude/projects" in out


# ─── image_paste_template parity (Aider matches Copilot, not Claude) ──────


def test_image_paste_template_aider_has_hint_with_path_placeholder():
    """Aider's argv spec includes an image_paste_template — when the
    user pastes an image, BT wraps the saved path with the template
    string before feeding to the CLI. The placeholder {path} is what
    BT substitutes at paste time."""
    template = AIDER._argv_spec.get("image_paste_template")  # noqa: SLF001
    assert template, "Aider lost image_paste_template — image paste broken"
    assert "{path}" in template, (
        f"Aider image_paste_template missing {{path}} placeholder: {template!r}"
    )
    # Hint text must be substantive (not just '{path}' bare) — the
    # whole point is to nudge the model toward 'describe what you see'
    # before editing code.
    assert len(template) > 30, (
        f"Aider image_paste_template too short to be useful: {template!r}"
    )


def test_image_paste_template_copilot_has_same_pattern_as_aider():
    """Parity — Copilot wraps with hint just like Aider. Claude
    intentionally has null (uses Anthropic's native multimodal API
    bytes path, not template substitution) — that's the deliberate
    divergence."""
    copilot = REGISTRY.get("copilot")
    aider_tpl = AIDER._argv_spec.get("image_paste_template")  # noqa: SLF001
    copilot_tpl = copilot._argv_spec.get("image_paste_template")  # noqa: SLF001
    assert copilot_tpl and "{path}" in copilot_tpl
    assert aider_tpl and "{path}" in aider_tpl


def test_image_paste_template_claude_intentionally_null():
    """Pin Claude's null — image paste for Claude uses Anthropic SDK's
    native multimodal bytes. Flipping this to a template would change
    user behavior, so force the change to be explicit."""
    template = CLAUDE._argv_spec.get("image_paste_template")  # noqa: SLF001
    assert template is None, (
        f"Claude image_paste_template should stay null (native API "
        f"bytes path) but became: {template!r}"
    )


# ─── stats_bar dispatch parity (currently divergent — pin the gap) ─────────


def test_stats_bar_options_parity_aider_hides_plan_usage():
    """Both Aider and Copilot run in tokens-only mode (no plan_usage
    gauge), Claude runs full mode. Pin all three so silent flips
    don't slip through."""
    claude_opts = stats_widget_options_for_ai_config(
        _ai_cfg("claude"), REGISTRY)
    aider_opts = stats_widget_options_for_ai_config(
        _ai_cfg("aider"), REGISTRY)
    copilot_opts = stats_widget_options_for_ai_config(
        _ai_cfg("copilot"), REGISTRY)

    assert claude_opts.get("hide_plan_usage") is False, (
        f"Claude widget options drifted: {claude_opts}"
    )
    assert aider_opts.get("hide_plan_usage") is True, (
        f"Aider widget options should hide plan_usage: {aider_opts}"
    )
    assert copilot_opts.get("hide_plan_usage") is True, (
        f"Copilot widget options should hide plan_usage: {copilot_opts}"
    )


def test_stats_reader_factory_returns_reader_for_all_three_providers():
    """Post-#94: all three bundled providers have a reader class
    registered in _READER_CLASSES, so the factory returns a non-None
    instance for each. Was a known gap before #94 — fixed by the
    AiderStatsReader landing."""
    aider_reader = create_stats_reader_for_ai_config(
        _ai_cfg("aider"), REGISTRY)
    claude_reader = create_stats_reader_for_ai_config(
        _ai_cfg("claude"), REGISTRY)
    copilot_reader = create_stats_reader_for_ai_config(
        _ai_cfg("copilot"), REGISTRY)

    assert claude_reader is not None
    assert copilot_reader is not None
    assert aider_reader is not None, (
        "Aider reader regressed to None — #94 fix dropped? "
        "Check bterminal/ui/stats/__init__.py:_READER_CLASSES"
    )
    # Sanity: each is bound to the right project_dir
    assert aider_reader.project_dir == "/tmp/parity-test"


# ─── Local-LLM-only divergence (sanity) ───────────────────────────────────


def test_aider_alone_has_local_endpoint_url():
    """Aider is BT's first local-LLM provider — it must declare
    local_endpoint_url so BT knows to manage the Ollama daemon
    lifecycle. Claude/Copilot don't (they hit remote APIs). Pin this
    so a future provider that adds local_endpoint_url documents what
    it means."""
    assert AIDER.capabilities.local_endpoint_url, (
        "Aider lost local_endpoint_url — BT can no longer route paste "
        "/ stats / model picks toward Ollama"
    )
    assert CLAUDE.capabilities.local_endpoint_url is None, (
        "Claude shouldn't have local_endpoint_url"
    )
    copilot = REGISTRY.get("copilot")
    assert copilot.capabilities.local_endpoint_url is None, (
        "Copilot shouldn't have local_endpoint_url"
    )


# ─── Display parity (icon + color shape) ──────────────────────────────────


@pytest.mark.parametrize("provider_name", ["claude", "aider", "copilot"])
def test_display_metadata_complete(provider_name):
    """Each provider's display block has icon + label + color so the
    sidebar / dialogs render uniformly. Missing field → broken UI."""
    import json
    defaults_path = REPO_ROOT / "bterminal" / "providers" / "defaults.json"
    cfg = json.loads(defaults_path.read_text())["providers"][provider_name]
    display = cfg.get("display", {})
    for field in ("icon", "short_label", "long_label", "color"):
        assert display.get(field), (
            f"{provider_name}.display.{field} missing — UI fallback "
            f"will look inconsistent"
        )
    # Color must be a hex string — sidebar emits CSS for dot indicators
    assert display["color"].startswith("#"), (
        f"{provider_name} color isn't hex: {display['color']!r}"
    )
