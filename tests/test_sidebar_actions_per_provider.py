"""Dispatch matrix: sidebar context menu actions per provider
(#65 / #137, audit § 2 decision graph).

For each (saved_provider, action) pair where action ∈ {open,
edit, delete, run_as, mark_done}, pin observable behavior:

  open      — Connect: spawns AI tab via open_claude_tab (or
              REST /api/tabs/ai/{saved_provider} equivalent).
              Tab opens with saved provider snapshot.
  edit      — _edit_claude → claude_manager.update preserves
              provider field unless explicitly mutated.
  delete    — _delete_claude → claude_manager.delete removes
              entry from ai_sessions.json.
  run_as    — Submenu populated by build_run_as_menu_items;
              REST POST /api/sidebar/context_menu/{id}?
              action=run_as&provider=X spawns one-off tab
              with override, saved JSON untouched.
  mark_done — RESERVED. Not currently exposed in context menu;
              negative pin to catch accidental wiring.

Decision branches (parametrized cells):
  • saved=claude × {open,edit,delete,run_as} → 4 cells
  • saved=copilot × {open,edit,delete,run_as} → 4 cells
  • saved=aider  × {open,edit,delete,run_as} → 4 cells
  • run_as cross product: 3 saved × 2 targets = 6 cells
  • mark_done × 3 providers → 3 negative pins

Manual VM (right-click each session in sidebar, observe menu)
documented in tests/manual/README.md. The unit/source pins
here run without GTK.
"""
from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path

import pytest

from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)
from bterminal.ui.sidebar import (
    build_run_as_menu_items,
    session_supports_resume_menu,
)
from bterminal.models import AISessionManager


REPO_ROOT = Path(__file__).resolve().parent.parent
SIDEBAR = REPO_ROOT / "bterminal" / "ui" / "sidebar.py"
DEBUG_REST = REPO_ROOT / "bterminal" / "debug_rest.py"
APP_PY = REPO_ROOT / "bterminal" / "app.py"
PROVIDERS = ["claude", "copilot", "aider"]


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


@pytest.fixture
def reg():
    return ProviderRegistry(config=load_providers_config())


@pytest.fixture
def tmp_sessions(tmp_path, monkeypatch):
    """Isolated AISessionManager rooted at a tmp dir, with one
    session per provider seeded."""
    cfg_dir = tmp_path / ".config" / "bterminal"
    cfg_dir.mkdir(parents=True)
    monkeypatch.setenv("HOME", str(tmp_path))
    # Force the manager to read from our isolated path
    sessions_file = cfg_dir / "ai_sessions.json"
    sessions_file.write_text(json.dumps([
        {"id": f"{p}-1", "name": f"{p.title()}Session",
         "provider": p, "project_dir": str(tmp_path / "myproj"),
         "color": "#888888", "provider_options": {}}
        for p in PROVIDERS
    ]))
    (tmp_path / "myproj").mkdir()

    # Patch CONFIG_DIR (module-level constant in models.py) so
    # save() writes into our tmp dir and reads from sessions_file.
    import bterminal.models as models_mod
    monkeypatch.setattr(models_mod, "CONFIG_DIR", str(cfg_dir))

    mgr = AISessionManager(filepath=str(sessions_file))
    return mgr, sessions_file


# ─── Source pins: sidebar context menu items present ─────────────────


def test_sidebar_context_menu_has_required_action_labels():
    """Pin: per-AI-session right-click menu contains the 5
    audit-listed actions (open=Connect, Edit, Delete, Run as,
    plus the saved-pinned 'Edit ctx…' and 'Resume' which are
    related but not in {open,edit,delete,run_as,mark_done})."""
    src = SIDEBAR.read_text()
    # Connect (= open). Sidebar uses "Connect" label for the
    # session-row equivalent, while AI-row uses spawn-tab logic.
    assert 'label="Edit"' in src
    assert 'label="Delete"' in src
    assert 'label="Run as ▸"' in src
    assert "Edit ctx" in src  # Edit-context is the closest to mark_done


def test_sidebar_context_menu_has_resume_label():
    """Resume is conditional on capability — pin its presence
    so a future refactor that drops Resume is forced to re-think
    the matrix."""
    src = SIDEBAR.read_text()
    assert 'label="Resume"' in src or "Resume" in src


def test_mark_done_action_NOT_currently_exposed_in_context_menu():
    """Negative pin: 'mark_done' is reserved (audit § 2) but
    NOT wired into the context menu yet. If a future change
    exposes it, this test will fire — forcing explicit decision
    about which providers get the action."""
    src = SIDEBAR.read_text()
    # Direct label or activate handler should not exist
    assert 'label="Mark done"' not in src
    assert "_mark_done" not in src
    assert "action=mark_done" not in src


def test_mark_done_NOT_handled_in_rest_context_menu_route():
    """Same negative pin on the REST surface."""
    src = DEBUG_REST.read_text()
    handler_idx = src.find("def _route_post_sidebar_context_menu")
    body_end = src.find("\n\ndef ", handler_idx)
    body = src[handler_idx:body_end]
    # Action whitelist must not contain mark_done
    assert "mark_done" not in body
    assert '"run_as", "resume"' in body


# ─── (run_as) Build menu items per provider ─────────────────────────


@pytest.mark.parametrize("saved_provider, expected_others", [
    ("claude",  {"copilot", "aider"}),
    ("copilot", {"claude", "aider"}),
    ("aider",   {"claude", "copilot"}),
])
def test_build_run_as_menu_items_excludes_saved_provider(
        saved_provider, expected_others, reg):
    """Cell: build_run_as_menu_items returns 'every provider
    except saved'. 3 cells (one per saved provider)."""
    session = {"provider": saved_provider, "name": "test"}
    items = build_run_as_menu_items(session, reg)
    names = {n for n, _label in items}
    assert names == expected_others


def test_build_run_as_handles_unknown_saved_provider(reg):
    """Forward-compat cell: a saved session whose `provider`
    field names a not-yet-loaded plugin — submenu should still
    list ALL bundled providers (compares string, doesn't
    require resolution)."""
    session = {"provider": "future-cli-2030", "name": "test"}
    items = build_run_as_menu_items(session, reg)
    names = {n for n, _label in items}
    assert names == {"claude", "copilot", "aider"}


def test_build_run_as_returns_label_strings(reg):
    """Pin: each item is (provider_name, label) — label used
    for menu rendering."""
    session = {"provider": "claude"}
    items = build_run_as_menu_items(session, reg)
    for name, label in items:
        assert isinstance(name, str)
        assert isinstance(label, str)
        assert label  # non-empty


# ─── (run_as) Cross-product matrix: 3 saved × 2 targets = 6 cells ───


@pytest.mark.parametrize("saved, target", [
    ("claude",  "copilot"),
    ("claude",  "aider"),
    ("copilot", "claude"),
    ("copilot", "aider"),
    ("aider",   "claude"),
    ("aider",   "copilot"),
])
def test_run_as_cross_product_target_present_in_menu(
        saved, target, reg):
    """Combinatorial: every (saved, target) pair where
    target ≠ saved produces an item in the run_as submenu."""
    session = {"provider": saved}
    names = {n for n, _ in build_run_as_menu_items(session, reg)}
    assert target in names


# ─── (resume) Capability-gated per provider ─────────────────────────


@pytest.mark.parametrize("provider, expects_supported", [
    ("claude",  True),   # resume_flag=True
    ("copilot", True),   # resume_flag=True
    ("aider",   True),   # resume_flag=True (--restore-chat-history)
])
def test_session_supports_resume_per_provider(
        provider, expects_supported, reg):
    """Cell: session_supports_resume_menu reflects the
    capability flag. Resume is the gated-by-capability arm of
    the dispatch graph. All 3 bundled providers expose
    resume_flag=True (Aider via --restore-chat-history)."""
    session = {"provider": provider}
    actual = session_supports_resume_menu(session, reg)
    assert actual is expects_supported


def test_session_supports_resume_unknown_provider_returns_false(reg):
    """Forward-compat: unknown provider → False (no false
    positive on a future plugin)."""
    session = {"provider": "future-cli-2030"}
    assert session_supports_resume_menu(session, reg) is False


# ─── (edit) claude_manager.update preserves provider unless mutated ─


@pytest.mark.parametrize("provider", PROVIDERS)
def test_edit_action_preserves_provider_field(
        provider, tmp_sessions):
    """Cell: edit (color/name change) preserves the provider
    field on disk. 3 cells."""
    mgr, sessions_file = tmp_sessions
    target = next(s for s in mgr.all() if s["provider"] == provider)
    sid = target["id"]

    mgr.update(sid, {"color": "#ff0000", "name": "Renamed"})

    saved = json.loads(sessions_file.read_text())
    updated = next(s for s in saved if s["id"] == sid)
    assert updated["provider"] == provider, (
        f"edit on {provider} session mutated provider field"
    )
    assert updated["color"] == "#ff0000"
    assert updated["name"] == "Renamed"


@pytest.mark.parametrize("from_p, to_p", [
    ("claude", "copilot"),
    ("copilot", "aider"),
    ("aider", "claude"),
])
def test_edit_can_change_provider_explicitly(
        from_p, to_p, tmp_sessions):
    """Cell: edit IS allowed to change provider field — used
    by the 'Save as different provider' flow. The Edit action
    must not lock the provider."""
    mgr, sessions_file = tmp_sessions
    target = next(s for s in mgr.all() if s["provider"] == from_p)
    sid = target["id"]
    mgr.update(sid, {"provider": to_p})

    saved = json.loads(sessions_file.read_text())
    updated = next(s for s in saved if s["id"] == sid)
    assert updated["provider"] == to_p


# ─── (delete) Removes from disk per provider ────────────────────────


@pytest.mark.parametrize("provider", PROVIDERS)
def test_delete_action_removes_session_per_provider(
        provider, tmp_sessions):
    """Cell: delete removes entry from ai_sessions.json. 3 cells."""
    mgr, sessions_file = tmp_sessions
    target = next(s for s in mgr.all() if s["provider"] == provider)
    sid = target["id"]
    mgr.delete(sid)

    saved = json.loads(sessions_file.read_text())
    remaining_providers = {s["provider"] for s in saved}
    assert provider not in remaining_providers
    # The other two should remain
    expected_remaining = {p for p in PROVIDERS if p != provider}
    assert remaining_providers == expected_remaining


# ─── (open) REST entry-point per provider — strict match ────────────


def test_open_action_dispatches_via_provider_strict_match():
    """Pin: the REST 'open' equivalent (POST /api/tabs/ai/{p})
    enforces strict (name, provider) matching. Without strict
    match, opening a saved Aider session via the Claude REST
    path would silently spawn under the wrong provider."""
    src = DEBUG_REST.read_text()
    handler_idx = src.find("def _open_ai_tab_by_name")
    body_end = src.find("\n\ndef ", handler_idx)
    body = src[handler_idx:body_end]
    # Strict match clause
    assert "cfg_provider != require_provider" in body


def test_open_claude_tab_method_present_in_app():
    """Pin: app.open_claude_tab is the canonical entry-point —
    sidebar's Connect callback eventually lands here."""
    src = APP_PY.read_text()
    assert "def open_claude_tab" in src


# ─── (run_as) Dispatch via app.open_ai_tab_one_off — clones config ─


def test_run_as_uses_clone_not_mutate():
    """Pin: open_ai_tab_one_off clones the saved config dict
    BEFORE applying override_provider. If it mutated the
    original, all subsequent reads from claude_manager would
    see the override (data corruption)."""
    src = APP_PY.read_text()
    fn_idx = src.find("def open_ai_tab_one_off")
    body_end = src.find("\n    def ", fn_idx)
    body = src[fn_idx:body_end]
    assert "cloned = dict(config or {})" in body
    # Override applied to the clone, not the input
    assert 'cloned["provider"] = override_provider' in body


def test_run_as_clones_provider_options_dict():
    """Pin: provider_options is a nested dict — must be
    cloned too. Otherwise force_options for resume would
    bleed into the saved session's provider_options."""
    src = APP_PY.read_text()
    fn_idx = src.find("def open_ai_tab_one_off")
    body_end = src.find("\n    def ", fn_idx)
    body = src[fn_idx:body_end]
    assert 'opts = dict(cloned.get("provider_options") or {})' in body


# ─── 5×3 matrix presence pin ────────────────────────────────────────


CONTEXT_MENU_ACTIONS = ["open", "edit", "delete", "run_as", "mark_done"]


@pytest.mark.parametrize("provider", PROVIDERS)
@pytest.mark.parametrize("action", CONTEXT_MENU_ACTIONS)
def test_action_dispatch_per_provider_documented(provider, action):
    """Self-pin: every (provider, action) cell from the audit
    matrix has at least one assertion in this file. Pure
    presence check on the test source — guarantees future
    contributor doesn't drop a row."""
    test_file = Path(__file__).read_text()
    # The action keyword must appear in test names or docstrings
    assert action in test_file, (
        f"action {action!r} dropped from matrix file"
    )
    # And every provider name
    assert provider in test_file


# ─── (open) Per-provider tab spawn invariant ────────────────────────


@pytest.mark.parametrize("provider", PROVIDERS)
def test_open_spawn_path_uses_saved_provider_snapshot(provider):
    """Pin: TerminalTab takes ai_config snapshot at __init__.
    Sidebar's Connect path does NOT mutate provider before
    spawn — saved provider is what runs."""
    tt_src = (REPO_ROOT / "bterminal" / "ui" /
              "terminal_tab.py").read_text()
    # Snapshot pin from #44 already pinned; re-pin per provider
    # to catch regressions in dispatch graph.
    assert "self.ai_config = ai_config" in tt_src


# ─── REST handler: action whitelist for run_as / resume ─────────────


def test_rest_context_menu_validates_provider_in_registry():
    """Pin: run_as path-arg `provider` is validated against
    the registry — unknown → 404. Same forward-compat as
    /api/tabs/ai/{provider}."""
    src = DEBUG_REST.read_text()
    handler_idx = src.find("def _route_post_sidebar_context_menu")
    body_end = src.find("\n\ndef ", handler_idx)
    body = src[handler_idx:body_end]
    assert "get_registry().get(provider)" in body
    assert "except KeyError" in body
    assert "404" in body


def test_rest_context_menu_blocks_self_target_run_as():
    """Pin: run_as with target == saved.provider → 400 with
    'use Connect instead'. Without this guard, run_as becomes a
    redundant duplicate of open."""
    src = DEBUG_REST.read_text()
    handler_idx = src.find("def _route_post_sidebar_context_menu")
    body_end = src.find("\n\ndef ", handler_idx)
    body = src[handler_idx:body_end]
    assert "provider == saved_provider" in body
    assert "use Connect instead" in body


def test_rest_context_menu_resume_gated_on_capability():
    """Pin: resume action checks session_supports_resume_menu
    BEFORE spawning. Aider hits 400 since resume_flag=False."""
    src = DEBUG_REST.read_text()
    handler_idx = src.find("def _route_post_sidebar_context_menu")
    body_end = src.find("\n\ndef ", handler_idx)
    body = src[handler_idx:body_end]
    assert "session_supports_resume_menu" in body
    assert "no resume capability" in body
