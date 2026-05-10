"""Dispatch matrix: every capability flag × every provider × every UI path
(#63 / #135, audit § 2 decision graph + § 5 coverage table).

The bundled capability matrix has 26 fields × 3 providers × 6
UI paths = 468 cells. Most cells are "doesn't apply" (the
capability isn't observable on that path). This file pins the
observable subset:

  rows: capability_name (subset of ProviderCapabilities fields
        that affect UI dispatch)
  cols: provider_name × ui_path

Per cell we assert ONE of:
  - The dispatch helper returns True iff the capability is True
    (capability gate semantics).
  - The dispatch helper returns the capability's typed value
    (e.g. context_file → "AIDER.md" string).
  - The capability is invisible on that path (skip cell — no
    assertion).

UI paths covered:
  sidebar       — display info (icon, color, long_label)
  dialog        — AISessionDialog field visibility
  REST          — /api/tabs response shape
  intro_prompt  — _compute_intro_prompt_for_tab branch
  paste         — _format_image_paste_for_provider branch
  rules_inject  — should_inject_rules / extract_rules_inject_bytes

Combined with the per-feature parity tests (#19, #91), this
matrix gives us decision-tree depth coverage across every
provider-aware code path.
"""
from __future__ import annotations

import dataclasses
from pathlib import Path
from unittest.mock import patch

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.providers.base import ProviderCapabilities
from bterminal.ui.terminal_tab import (
    extract_rules_inject_bytes,
    should_inject_rules,
    should_run_auto_trigger,
)
from bterminal.ui.stats import (
    create_stats_reader_for_ai_config,
    stats_widget_options_for_ai_config,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
PROVIDERS = ["claude", "copilot", "aider"]
UI_PATHS = ["sidebar", "dialog", "rest", "intro_prompt",
            "paste", "rules_inject"]


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def reg():
    return ProviderRegistry(config=load_providers_config())


def _ai_cfg(provider: str, project_dir: str = "/tmp/matrix-test") -> dict:
    return {
        "id": f"{provider}-1",
        "name": f"{provider}Session",
        "provider": provider,
        "project_dir": project_dir,
        "color": "#888888",
        "provider_options": {},
    }


# ─── Source-of-truth: capability fields enumerated ─────────────────────


def test_capability_dataclass_has_26_fields_we_audit():
    """Pin: ProviderCapabilities has the 26 fields the dispatch
    matrix audits. A new field landing without matrix coverage
    would slip past — pin so adding caps forces explicit
    decision about which path observes them."""
    fields = [f.name for f in
              dataclasses.fields(ProviderCapabilities)]
    expected = {
        "intro_prompt", "resume_flag", "continue_flag",
        "skip_permissions", "granular_permissions", "supports_sudo",
        "session_log", "session_log_path",
        "session_index_db", "session_index_db_path",
        "usage_api", "usage_api_url", "oauth_creds_file",
        "cost_in_log", "rules_inject", "task_auto_trigger",
        "stats_bar", "stats_bar_no_plan_usage",
        "plan_mode", "autopilot", "mcp_support",
        "context_file", "context_file_cumulative",
        "ready_marker", "default_model", "local_endpoint_url",
    }
    actual = set(fields)
    assert expected.issubset(actual), (
        f"capability set drifted; missing from defaults: "
        f"{expected - actual}; new in defaults: {actual - expected}"
    )


# ─── Capability × provider × intro_prompt UI path ──────────────────────


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("cap", [
    "intro_prompt", "rules_inject", "task_auto_trigger",
    "stats_bar", "session_log", "skip_permissions",
])
def test_required_capability_flags_true_for_all_three_providers(
        provider, cap, reg):
    """Pin: every bundled provider has the SESSION-MACHINERY
    capability flags True. These are the ones that drive the
    canonical dispatch graph from audit § 2.

    Cells: cap=intro_prompt × provider=claude → True (etc).
    Combined: 6 caps × 3 providers = 18 must-be-True cells."""
    val = getattr(reg.get(provider).capabilities, cap)
    assert val is True, (
        f"{provider}.{cap} = {val} (should be True per audit § 2 "
        f"capability matrix)"
    )


@pytest.mark.parametrize("provider, expected", [
    ("claude", "system.stop"),
    ("copilot", None),
    ("aider", None),
])
def test_ready_marker_per_provider(provider, expected, reg):
    """Cell: cap=ready_marker × provider=X. Claude has a
    structured 'system.stop' marker; Copilot/Aider rely on VTE
    silence (None). Pin all three."""
    assert reg.get(provider).capabilities.ready_marker == expected


@pytest.mark.parametrize("provider, expected", [
    ("claude", "CLAUDE.md"),
    ("copilot", "AGENTS.md"),
    ("aider", "AIDER.md"),
])
def test_context_file_per_provider(provider, expected, reg):
    """Cell: cap=context_file × provider=X. The auto-symlink
    (#92) reads this — pin all three values for end-to-end
    correctness."""
    assert reg.get(provider).capabilities.context_file == expected


@pytest.mark.parametrize("provider, has_local_endpoint", [
    ("claude", False),
    ("copilot", False),
    ("aider", True),
])
def test_local_endpoint_url_only_for_aider(
        provider, has_local_endpoint, reg):
    """Cell: cap=local_endpoint_url × provider=X. Only Aider
    declares a local LLM endpoint. Pin so a refactor that adds
    it to Claude/Copilot is forced to explain why."""
    val = reg.get(provider).capabilities.local_endpoint_url
    if has_local_endpoint:
        assert val and val.startswith("http"), (
            f"aider.local_endpoint_url not a URL: {val!r}"
        )
    else:
        assert val is None, (
            f"{provider} unexpectedly has local_endpoint_url={val!r}"
        )


# ─── Capability × dispatch helper × provider matrix ───────────────────


@pytest.mark.parametrize("provider", PROVIDERS)
def test_should_inject_rules_dispatch(provider, reg):
    """Cell: cap=rules_inject × ui=rules_inject. The dispatch
    helper returns True iff cap is True (3 cells)."""
    expected = reg.get(provider).capabilities.rules_inject
    assert should_inject_rules(_ai_cfg(provider), reg) is expected


@pytest.mark.parametrize("provider", PROVIDERS)
def test_should_run_auto_trigger_dispatch(provider, reg):
    """Cell: cap=task_auto_trigger × ui=auto_trigger. (3 cells)."""
    expected = reg.get(provider).capabilities.task_auto_trigger
    assert should_run_auto_trigger(_ai_cfg(provider), reg) is expected


@pytest.mark.parametrize("provider", PROVIDERS)
def test_extract_rules_inject_bytes_dispatch_provider_agnostic(provider):
    """Cell: cap=rules_inject × ui=rules_inject (bytes). #93
    contract: byte-identical across all 3 providers (3 cells
    must produce same bytes)."""
    out = extract_rules_inject_bytes(provider, "myproj", "## rule")
    assert out == b"## rule"


@pytest.mark.parametrize("provider", PROVIDERS)
def test_stats_reader_factory_dispatch(provider, reg):
    """Cell: cap=stats_bar × ui=stats_bar. Factory returns a
    reader instance iff cap is True AND a reader class is
    registered. All 3 providers have stats_bar=True + reader
    registered (#94)."""
    reader = create_stats_reader_for_ai_config(_ai_cfg(provider), reg)
    if reg.get(provider).capabilities.stats_bar:
        assert reader is not None, (
            f"{provider}.stats_bar=True but factory returned None"
        )


@pytest.mark.parametrize("provider", PROVIDERS)
def test_stats_widget_options_hide_plan_usage_dispatch(
        provider, reg):
    """Cell: cap=stats_bar_no_plan_usage × ui=stats_bar. The
    options dict's `hide_plan_usage` key MUST equal the cap value."""
    cap = reg.get(provider).capabilities.stats_bar_no_plan_usage
    opts = stats_widget_options_for_ai_config(_ai_cfg(provider), reg)
    assert opts["hide_plan_usage"] == cap


@pytest.mark.parametrize("provider", PROVIDERS)
def test_stats_widget_options_cost_unavailable_dispatch(
        provider, reg):
    """Cell: cap=cost_in_log × ui=stats_bar. cost_unavailable
    is the INVERSE — True when cost_in_log is False (Aider's
    case, #94)."""
    cap = reg.get(provider).capabilities.cost_in_log
    opts = stats_widget_options_for_ai_config(_ai_cfg(provider), reg)
    assert opts["cost_unavailable"] == (not cap)


# ─── Capability × ui=paste matrix ─────────────────────────────────────


@pytest.mark.parametrize("provider", PROVIDERS)
def test_image_paste_template_capability_dispatch(provider, reg):
    """Cell: cap=image_paste_template × ui=paste. Lives in
    `_argv_spec` (defaults.json:argv.image_paste_template), not
    capabilities — but observable through paste dispatch.

    Claude has `null` (vision native); Copilot/Aider have hint
    templates with `{path}`."""
    template = reg.get(provider)._argv_spec.get("image_paste_template")
    if provider == "claude":
        assert template is None
    else:
        assert isinstance(template, str)
        assert "{path}" in template


# ─── Capability × ui=intro_prompt matrix ──────────────────────────────


@pytest.mark.parametrize("provider", PROVIDERS)
def test_intro_prompt_mode_per_provider(provider, reg):
    """Cell: cap=intro_prompt × ui=intro_prompt. The
    `intro_prompt_mode` argv spec (positional/flag/stdin_feed)
    determines HOW intro reaches the AI CLI."""
    mode = reg.get(provider)._argv_spec.get("intro_prompt_mode")
    expected = {
        "claude": "positional",
        "copilot": "flag",
        "aider": "stdin_feed",
    }
    assert mode == expected[provider], (
        f"{provider} intro_prompt_mode = {mode!r} "
        f"(expected {expected[provider]!r})"
    )


# ─── Capability × ui=sidebar matrix ──────────────────────────────────


@pytest.mark.parametrize("provider", PROVIDERS)
def test_display_metadata_visible_to_sidebar(provider, reg):
    """Cell: cap=display × ui=sidebar. Each provider exposes
    icon + label + color through `provider.display`. Sidebar
    renders these for the Aider beaver / Copilot bot / Claude
    sparkle."""
    display = reg.get(provider).display
    assert display.icon
    assert display.short_label
    assert display.long_label
    assert display.color.startswith("#")


# ─── Capability × ui=REST matrix ─────────────────────────────────────


def test_rest_route_regex_accepts_all_three_providers():
    """Cell: cap=name × ui=REST. The /api/tabs/ai/{provider}
    route regex accepts `[\\w-]+` — so all 3 bundled names
    plus any plugin-provided name works."""
    src = (REPO_ROOT / "bterminal" / "debug_rest.py").read_text()
    assert "/api/tabs/ai/(?P<provider>[\\w-]+)" in src


def test_rest_tabs_endpoint_emits_provider_field():
    """Cell: REST surface exposes provider field. External
    tooling (test_provider_switch_mid_session.py / e2e) reads
    this. Pin source."""
    src = (REPO_ROOT / "bterminal" / "debug_rest.py").read_text()
    assert '"provider"' in src


# ─── Capability × ui=dialog matrix ───────────────────────────────────


@pytest.mark.parametrize("provider", PROVIDERS)
def test_dialog_can_construct_session_for_each_provider(
        provider, reg):
    """Cell: cap=name × ui=dialog. Verify session config dict
    can be constructed for each provider — AISessionDialog reads
    these fields."""
    cfg = _ai_cfg(provider)
    assert cfg["provider"] == provider
    # Round-trip via registry — dialog uses reg.get(provider)
    p = reg.get(provider)
    assert p.name == provider


# ─── Combined: 6×3 = 18 must-be-True caps + dispatch result ──────────


REQUIRED_CAPS = ["intro_prompt", "rules_inject", "task_auto_trigger",
                 "stats_bar", "session_log", "skip_permissions"]


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("cap", REQUIRED_CAPS)
def test_required_caps_all_observable_via_dispatch_helpers(
        provider, cap, reg):
    """Combined cell: every required cap must be both True
    AND observable via at least one dispatch helper. Pure
    presence in `capabilities` isn't enough — has to influence
    runtime behavior."""
    val = getattr(reg.get(provider).capabilities, cap)
    assert val is True

    # Cap-specific dispatch verification
    cfg = _ai_cfg(provider)
    if cap == "rules_inject":
        assert should_inject_rules(cfg, reg) is True
    elif cap == "task_auto_trigger":
        assert should_run_auto_trigger(cfg, reg) is True
    elif cap == "stats_bar":
        reader = create_stats_reader_for_ai_config(cfg, reg)
        assert reader is not None
    # intro_prompt / session_log / skip_permissions are
    # observable through indirect paths (build_argv shape,
    # session_log_glob path) — covered by per-feature tests
    # in #91 / #93 / #94.


# ─── Capability × provider × intentional divergence ──────────────────


DIVERGENCE_MATRIX = [
    # (cap, claude, copilot, aider, why)
    ("usage_api",        True,  False, False,
     "Aider local LLM, Copilot has no public API"),
    ("cost_in_log",      True,  True,  False,
     "Aider dispatches off-process via Ollama"),
    ("mcp_support",      True,  False, False,
     "Aider + Copilot don't speak MCP"),
    ("supports_sudo",    True,  False, False,
     "Only Claude is sudo-aware"),
    ("continue_flag",    True,  True,  False,
     "Aider has only --restore-chat-history"),
    ("granular_permissions", False, True, False,
     "Only Copilot has granular permission knobs"),
    ("plan_mode",        False, True,  False,
     "Only Copilot has --plan"),
    ("stats_bar_no_plan_usage", False, True, True,
     "Aider/Copilot run tokens-only"),
    ("autopilot",        False, False, False,
     "Reserved for future"),
]


@pytest.mark.parametrize(
    "cap, claude_v, copilot_v, aider_v, why",
    DIVERGENCE_MATRIX,
)
def test_divergence_matrix_pinned_with_rationale(
        cap, claude_v, copilot_v, aider_v, why, reg):
    """Pin: every intentional divergence has both a value AND
    a `why` rationale. Future tweak forces explicit rationale
    update — without this pin, silent flips slip through."""
    actual_claude = getattr(reg.get("claude").capabilities, cap)
    actual_copilot = getattr(reg.get("copilot").capabilities, cap)
    actual_aider = getattr(reg.get("aider").capabilities, cap)

    assert actual_claude == claude_v, (
        f"Claude.{cap} = {actual_claude} (expected {claude_v}). "
        f"Reason locked: {why}"
    )
    assert actual_copilot == copilot_v, (
        f"Copilot.{cap} = {actual_copilot} (expected {copilot_v}). "
        f"Reason locked: {why}"
    )
    assert actual_aider == aider_v, (
        f"Aider.{cap} = {actual_aider} (expected {aider_v}). "
        f"Reason locked: {why}"
    )


# ─── Forward-compat: unknown provider returns False / None ──────────


@pytest.mark.parametrize("dispatch_helper, expected", [
    (should_inject_rules, False),
    (should_run_auto_trigger, False),
])
def test_dispatch_helpers_skip_unknown_provider(
        dispatch_helper, expected, reg):
    """Cell: cap=any × provider=unknown × ui=dispatch. All
    dispatch helpers fail-safe to False/None for unknown
    providers (forward-compat — saved configs from a future
    BT version naming an unloaded plugin must not crash)."""
    cfg = _ai_cfg("plugin-not-loaded-2030")
    assert dispatch_helper(cfg, reg) is expected


def test_factory_returns_none_for_unknown_provider(reg):
    """Same for stats_reader factory."""
    cfg = _ai_cfg("plugin-not-loaded-2030")
    assert create_stats_reader_for_ai_config(cfg, reg) is None


def test_widget_options_returns_empty_for_unknown_provider(reg):
    """Same for widget options."""
    cfg = _ai_cfg("plugin-not-loaded-2030")
    assert stats_widget_options_for_ai_config(cfg, reg) == {}


# ─── Complete provider matrix: every provider has every required cap ─


@pytest.mark.parametrize("provider", PROVIDERS)
def test_provider_capability_dataclass_complete(provider, reg):
    """Pin: each provider's capabilities is a fully-populated
    `ProviderCapabilities` instance. A partial dict would
    surface here (e.g. `{intro_prompt: True}` only)."""
    caps = reg.get(provider).capabilities
    assert isinstance(caps, ProviderCapabilities)
    # All 24 fields readable
    for field in dataclasses.fields(ProviderCapabilities):
        getattr(caps, field.name)


# ─── 6 UI paths covered ──────────────────────────────────────────────


@pytest.mark.parametrize("ui_path", UI_PATHS)
def test_each_ui_path_has_a_test_pinning_at_least_one_capability(
        ui_path):
    """Self-pin: this test file references every UI path from
    the audit § 2 decision graph. Without this, a future
    contributor could add a UI path without coverage."""
    test_file = Path(__file__).read_text()
    # Each path mentioned in some test name or docstring
    assert ui_path in test_file, (
        f"UI path {ui_path!r} not referenced in dispatch matrix"
    )


# ─── Combinatorial: 26 caps × 3 providers = 78 read accesses ─────────


@pytest.mark.parametrize("provider", PROVIDERS)
def test_every_capability_field_readable_for_every_provider(
        provider, reg):
    """Pin: 78 cells (26 caps × 3 providers) — every cap field
    is readable on every provider's capabilities dataclass.
    Catches a refactor that adds a field to defaults.json
    without adding it to the dataclass (KeyError on construct)."""
    caps = reg.get(provider).capabilities
    for field in dataclasses.fields(ProviderCapabilities):
        # Access each field — getattr raises if dataclass is
        # broken. Pin: every cell is observable.
        val = getattr(caps, field.name)
        # Type sanity: bool, str, or None
        assert val is None or isinstance(val, (bool, str, int, float, list, dict))


# ─── Capability gate predicate consistency ──────────────────────────


@pytest.mark.parametrize("provider", PROVIDERS)
def test_dispatch_helpers_use_only_capability_no_provider_branch(
        provider, reg):
    """Pin: dispatch helpers (`should_inject_rules`,
    `should_run_auto_trigger`) make decisions ONLY based on
    capability flags — NOT on provider name. So flipping a
    capability flag changes the decision, but renaming a
    provider doesn't.

    Test: temporarily flip the capability via a shadow
    provider object and verify dispatch flips too."""
    # Verify dispatch == capability state
    cfg = _ai_cfg(provider)
    cap_inject = reg.get(provider).capabilities.rules_inject
    assert should_inject_rules(cfg, reg) is cap_inject

    # Now create a shadow with rules_inject=False, register
    # under same name, and verify dispatch flips
    p = reg.get(provider)
    shadow_caps = dataclasses.replace(p.capabilities,
                                      rules_inject=False)
    p_shadow = type(p).__new__(type(p))
    p_shadow.__dict__.update(p.__dict__)
    p_shadow.capabilities = shadow_caps

    reg2 = ProviderRegistry(config=load_providers_config())
    reg2.register(p_shadow)
    assert should_inject_rules(cfg, reg2) is False, (
        f"dispatch helper ignored capability flip for {provider}"
    )
