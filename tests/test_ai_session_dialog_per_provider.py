"""Dispatch matrix: AISessionDialog field visibility per provider
(#66 / #138, audit § 4 integration matrix).

For each provider, pin which schema fields the dialog renders
when the user picks that provider in the dropdown:

  Claude   → resume, skip_permissions, sudo (3 checkboxes)
  Copilot  → skip_permissions, plan_mode (cap-gated),
             allowed_tools (cap-gated), image_paste_template
  Aider    → [] (default empty schema)

Decision branches:
  (a) Capability-driven UI dispatch: plan_mode renders only
      when capabilities.plan_mode=True; allowed_tools only when
      granular_permissions=True; image_paste_template renders
      always for Copilot.
  (b) Provider switch in dropdown re-renders the schema
      container (no stale Claude widgets visible after switch
      to Copilot).
  (c) Claude has NO image_paste_template field — vision is
      native, no host-side hint template needed.
  (d) RESERVED: Aider model dropdown w/ qwen options (audit
      § 4 row) is NOT YET wired — pinned negatively so adding
      it is forced through this test file.

Manual VM smoke (open dialog for each provider type, observe
visible fields) — documented; the source-grep + capability-pin
matrix here covers the dispatch graph headlessly.
"""
from __future__ import annotations

from pathlib import Path

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)


REPO_ROOT = Path(__file__).resolve().parent.parent
PROVIDERS = ["claude", "copilot", "aider"]


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def reg():
    return ProviderRegistry(config=load_providers_config())


def _schema_keys(provider) -> set:
    """Helper: return set of config_keys in provider's dialog schema."""
    return {entry[0] for entry in provider.get_dialog_schema()}


def _schema_widget_types(provider) -> dict:
    """Helper: {key: widget_type} from provider's schema."""
    return {e[0]: e[1] for e in provider.get_dialog_schema()}


# ─── Claude schema ──────────────────────────────────────────────────


def test_claude_dialog_schema_has_skip_permissions(reg):
    """Cell (b): Claude shows skip_permissions checkbox."""
    keys = _schema_keys(reg.get("claude"))
    assert "skip_permissions" in keys
    types = _schema_widget_types(reg.get("claude"))
    assert types["skip_permissions"] == "checkbox"


def test_claude_dialog_schema_has_resume_checkbox(reg):
    """Cell: Claude exposes Resume checkbox (resume_flag=True)."""
    types = _schema_widget_types(reg.get("claude"))
    assert types.get("resume") == "checkbox"


def test_claude_dialog_schema_has_sudo_checkbox(reg):
    """Cell: Claude is sudo-aware (supports_sudo=True) — checkbox."""
    types = _schema_widget_types(reg.get("claude"))
    assert types.get("sudo") == "checkbox"


def test_claude_dialog_schema_does_NOT_show_image_paste_template(reg):
    """Cell (c)/(d): Claude has NO image_paste_template — vision
    is native (capabilities.image_paste_template_mode = builtin)."""
    keys = _schema_keys(reg.get("claude"))
    assert "image_paste_template" not in keys


def test_claude_dialog_schema_does_NOT_show_plan_mode(reg):
    """Cell: Claude doesn't have --plan (Copilot-only feature)."""
    keys = _schema_keys(reg.get("claude"))
    assert "plan_mode" not in keys


def test_claude_dialog_schema_does_NOT_show_allowed_tools(reg):
    """Cell: Claude doesn't surface granular_permissions
    in its dialog (capabilities.granular_permissions=False)."""
    keys = _schema_keys(reg.get("claude"))
    assert "allowed_tools" not in keys


# ─── Copilot schema ─────────────────────────────────────────────────


def test_copilot_dialog_schema_has_skip_permissions_yolo(reg):
    """Cell: Copilot's skip_permissions maps to --yolo / --allow-all."""
    types = _schema_widget_types(reg.get("copilot"))
    assert types.get("skip_permissions") == "checkbox"


def test_copilot_dialog_schema_has_plan_mode_when_capability_true(reg):
    """Cell (c): plan_mode toggle present iff capabilities.plan_mode."""
    cap = reg.get("copilot").capabilities.plan_mode
    keys = _schema_keys(reg.get("copilot"))
    if cap:
        assert "plan_mode" in keys
        types = _schema_widget_types(reg.get("copilot"))
        assert types["plan_mode"] == "checkbox"
    else:
        assert "plan_mode" not in keys


def test_copilot_dialog_schema_has_image_paste_template(reg):
    """Cell (d): Copilot exposes image_paste_template Entry."""
    keys = _schema_keys(reg.get("copilot"))
    assert "image_paste_template" in keys
    types = _schema_widget_types(reg.get("copilot"))
    assert types["image_paste_template"] == "text"


def test_copilot_dialog_schema_has_allowed_tools_when_granular(reg):
    """Cell: granular_permissions cap → allowed_tools textarea."""
    cap = reg.get("copilot").capabilities.granular_permissions
    keys = _schema_keys(reg.get("copilot"))
    if cap:
        assert "allowed_tools" in keys
        types = _schema_widget_types(reg.get("copilot"))
        assert types["allowed_tools"] == "textarea"
    else:
        assert "allowed_tools" not in keys


def test_copilot_dialog_schema_does_NOT_show_sudo(reg):
    """Cell: Copilot has supports_sudo=False — no sudo checkbox."""
    keys = _schema_keys(reg.get("copilot"))
    assert "sudo" not in keys


def test_copilot_image_paste_template_uses_default_as_placeholder(reg):
    """Pin: Copilot's image_paste_template entry uses the
    `_argv_spec.image_paste_template` as placeholder (#71). Lets
    user see what they're overriding before typing."""
    schema = reg.get("copilot").get_dialog_schema()
    ipt_entry = next(e for e in schema if e[0] == "image_paste_template")
    # 4-tuple: (key, type, label, placeholder)
    assert len(ipt_entry) >= 4
    placeholder = ipt_entry[3]
    assert isinstance(placeholder, str)
    # Default template contains {path} placeholder
    if placeholder:
        assert "{path}" in placeholder


# ─── Aider schema ──────────────────────────────────────────────────


def test_aider_dialog_schema_is_currently_empty(reg):
    """Cell (a) — RESERVED state: Aider has no provider-specific
    schema fields yet. Default base implementation returns [].

    Audit § 4 row planned: model dropdown w/ qwen2.5-coder
    options + local_endpoint_url override. Once implemented,
    this test will fire — forcing matrix update."""
    schema = reg.get("aider").get_dialog_schema()
    assert schema == [], (
        f"Aider schema gained fields without matrix update: {schema}"
    )


def test_aider_model_dropdown_NOT_YET_exposed_in_dialog(reg):
    """Negative pin (audit gap): model dropdown for Aider's qwen
    options is RESERVED. Once added, expected schema entry is
    `("model", "combo", "Model", ["qwen2.5-coder:0.5b", ...])`.
    Pin the absence — not the presence — until that lands."""
    keys = _schema_keys(reg.get("aider"))
    assert "model" not in keys


def test_aider_local_endpoint_url_NOT_YET_in_dialog(reg):
    """Negative pin (audit gap): per-session override of
    local_endpoint_url planned (#75) but not in schema yet.
    Pin absence — when added, expected entry:
    `("local_endpoint_url", "text", "Endpoint URL")`."""
    keys = _schema_keys(reg.get("aider"))
    assert "local_endpoint_url" not in keys


def test_aider_image_paste_template_NOT_YET_in_dialog(reg):
    """Negative pin: Aider has _argv_spec.image_paste_template
    set (#69, used at paste time) but no per-session override
    Entry in dialog. Pin absence — when added, follows Copilot
    precedent."""
    keys = _schema_keys(reg.get("aider"))
    assert "image_paste_template" not in keys


def test_aider_default_model_capability_value(reg):
    """Cell: capabilities.default_model is set even though no
    dialog widget surfaces it. Spawn argv falls back to this
    when session config doesn't carry an explicit model."""
    cap = reg.get("aider").capabilities.default_model
    assert cap == "openai/qwen2.5-coder:0.5b"


def test_aider_local_endpoint_url_capability_value(reg):
    """Cell: capabilities.local_endpoint_url set for Aider only
    (other 2 providers have None) — but no UI widget yet."""
    cap = reg.get("aider").capabilities.local_endpoint_url
    assert cap == "http://localhost:11434/v1"


# ─── Cross-provider matrix: schema disjoint properties ─────────────


@pytest.mark.parametrize("provider", PROVIDERS)
def test_schema_returns_list_of_tuples(provider, reg):
    """Pin: every provider's get_dialog_schema returns a list
    of 3+-tuples (key, widget_type, label, [...extras])."""
    schema = reg.get(provider).get_dialog_schema()
    assert isinstance(schema, list)
    for entry in schema:
        assert isinstance(entry, tuple)
        assert len(entry) >= 3
        key, wtype, label = entry[0], entry[1], entry[2]
        assert isinstance(key, str) and key
        assert wtype in ("checkbox", "combo", "text", "textarea")
        assert isinstance(label, str) and label


@pytest.mark.parametrize("provider", PROVIDERS)
def test_schema_keys_are_unique(provider, reg):
    """Pin: no provider re-uses the same key — would shadow
    one widget with another at render time."""
    schema = reg.get(provider).get_dialog_schema()
    keys = [e[0] for e in schema]
    assert len(keys) == len(set(keys))


@pytest.mark.parametrize("provider", PROVIDERS)
def test_schema_keys_are_subset_of_provider_option_keys(provider, reg):
    """Pin: every schema key MUST appear in the canonical
    `_PROVIDER_OPTION_KEYS` list (so the dialog's get_data()
    correctly routes them under provider_options sub-dict
    rather than top-level)."""
    from bterminal.ui.dialogs.ai_session import _PROVIDER_OPTION_KEYS
    schema_keys = _schema_keys(reg.get(provider))
    for key in schema_keys:
        assert key in _PROVIDER_OPTION_KEYS, (
            f"{provider} schema key {key!r} missing from "
            f"_PROVIDER_OPTION_KEYS — get_data() will leave it "
            f"top-level (not under provider_options)"
        )


# ─── Capability-gated UI rendering: schema branches per cap ────────


@pytest.mark.parametrize("provider, cap_name, expected_key", [
    ("copilot", "plan_mode", "plan_mode"),
    ("copilot", "granular_permissions", "allowed_tools"),
    ("claude",  "supports_sudo", "sudo"),
    ("claude",  "resume_flag", "resume"),
])
def test_capability_gates_schema_field(
        provider, cap_name, expected_key, reg):
    """Combined cell: when capability flag is True, schema
    contains the corresponding key. Pin per known cap→field
    mapping. 4 (provider, cap, field) cells."""
    cap = getattr(reg.get(provider).capabilities, cap_name)
    keys = _schema_keys(reg.get(provider))
    if cap:
        assert expected_key in keys, (
            f"{provider}.{cap_name}=True but {expected_key!r} "
            f"missing from schema"
        )
    else:
        assert expected_key not in keys, (
            f"{provider}.{cap_name}=False but {expected_key!r} "
            f"present in schema"
        )


# ─── Source pin: AISessionDialog re-renders schema on switch ───────


def test_dialog_re_renders_schema_on_provider_change():
    """Pin: `_on_provider_changed` calls
    `_render_schema_for_current_provider` — without re-render,
    switching from Claude to Copilot would leave Claude widgets
    visible."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs" /
           "ai_session.py").read_text()
    handler_idx = src.find("def _on_provider_changed")
    body_end = src.find("\n    def ", handler_idx)
    body = src[handler_idx:body_end]
    assert "_render_schema_for_current_provider" in body


def test_dialog_preserves_widget_values_across_provider_switch():
    """Pin (#59 fix): `_render_schema_for_current_provider`
    snapshots prior_values + merges into _session_data BEFORE
    clearing widgets. Without this, switching Claude→Copilot→
    Claude would reset Claude-only fields to defaults."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs" /
           "ai_session.py").read_text()
    fn_idx = src.find("def _render_schema_for_current_provider")
    body_end = src.find("\n    def ", fn_idx)
    body = src[fn_idx:body_end]
    assert "_collect_current_schema_values" in body
    assert "_session_data.update(prior_values)" in body


def test_dialog_hides_legacy_static_claude_checkboxes():
    """Pin: parent's hardcoded chk_sudo / chk_resume /
    chk_skip_perms are hidden — schema-driven widgets are the
    SOLE source of truth."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs" /
           "ai_session.py").read_text()
    fn_idx = src.find("def _hide_legacy_provider_widgets")
    body_end = src.find("\n    def ", fn_idx)
    body = src[fn_idx:body_end]
    assert "chk_sudo" in body
    assert "chk_resume" in body
    assert "chk_skip_perms" in body
    assert "set_visible(False)" in body


# ─── get_data() routes schema-driven values under provider_options ─


def test_get_data_drops_parent_static_keys_before_schema_read():
    """Pin: get_data() pops every _PROVIDER_OPTION_KEYS from
    the parent's base dict BEFORE reading schema widgets, so
    the schema is the source of truth even when the parent
    static checkboxes still emit values."""
    src = (REPO_ROOT / "bterminal" / "ui" / "dialogs" /
           "ai_session.py").read_text()
    fn_idx = src.find("    def get_data(self) -> dict:")
    # get_data is the last method in the class — slice to next
    # top-level construct (`__all__` or end of file)
    body_end = src.find("\n__all__", fn_idx)
    if body_end < 0:
        body_end = len(src)
    body = src[fn_idx:body_end]
    assert "for key in _PROVIDER_OPTION_KEYS:" in body
    assert "base.pop(key, None)" in body
    assert "_collect_current_schema_values()" in body
    assert "_split_provider_options_from_data" in body


def test_split_provider_options_routes_schema_keys_to_subdict():
    """Cell-level pin: every key the schema can produce ends
    up under `provider_options` after get_data(), not
    top-level. Tests _split_provider_options_from_data
    directly — pure helper, no GTK."""
    from bterminal.ui.dialogs.ai_session import (
        _split_provider_options_from_data,
        _PROVIDER_OPTION_KEYS,
    )
    flat = {
        "name": "test", "color": "#000",
        "skip_permissions": True, "plan_mode": False,
        "allowed_tools": "shell", "image_paste_template": "x",
        "model": "qwen", "sudo": True, "resume": True,
    }
    result = _split_provider_options_from_data(flat)
    # Top-level: only name, color
    assert set(result.keys()) - {"provider_options"} == {"name", "color"}
    # All schema-routed keys under provider_options
    opts = result["provider_options"]
    for key in ("skip_permissions", "plan_mode", "allowed_tools",
                "image_paste_template", "model", "sudo", "resume"):
        assert key in opts
        assert key in _PROVIDER_OPTION_KEYS


# ─── Self-pin: every (provider, audit-listed field) cell tested ────


AUDIT_DIALOG_MATRIX = [
    # (provider, field_key, expected_present)
    # — Claude column
    ("claude",  "resume",                True),
    ("claude",  "skip_permissions",      True),
    ("claude",  "sudo",                  True),
    ("claude",  "plan_mode",             False),
    ("claude",  "allowed_tools",         False),
    ("claude",  "image_paste_template",  False),
    ("claude",  "model",                 False),
    # — Copilot column
    ("copilot", "skip_permissions",      True),
    ("copilot", "plan_mode",             True),
    ("copilot", "allowed_tools",         True),
    ("copilot", "image_paste_template",  True),
    ("copilot", "sudo",                  False),
    ("copilot", "resume",                False),
    ("copilot", "model",                 False),
    # — Aider column (RESERVED — currently all False)
    ("aider",   "skip_permissions",      False),
    ("aider",   "sudo",                  False),
    ("aider",   "resume",                False),
    ("aider",   "plan_mode",             False),
    ("aider",   "model",                 False),  # audit gap
    ("aider",   "image_paste_template",  False),  # audit gap
    ("aider",   "local_endpoint_url",    False),  # audit gap
]


@pytest.mark.parametrize("provider, field, expected", AUDIT_DIALOG_MATRIX)
def test_audit_dialog_field_visibility_matrix(
        provider, field, expected, reg):
    """Combined matrix: 21 cells (3 providers × 7 fields).
    Every audit-listed field is pinned to its expected
    visibility state per provider — covers happy paths AND
    audit gaps (Aider's RESERVED rows)."""
    keys = _schema_keys(reg.get(provider))
    actual = field in keys
    assert actual is expected, (
        f"{provider}.{field}: expected_visible={expected} "
        f"actual_in_schema={actual}"
    )
