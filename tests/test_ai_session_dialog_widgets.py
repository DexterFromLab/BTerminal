"""Widget-level tests for AISessionDialog (task #55, 2026-05-07).

Decision-tree coverage of the create + edit flow that pure-helper
tests in test_ai_session_dialog.py cannot reach: action area Cancel/OK
visibility, schema rebuild on provider switch, edit roundtrip with
provider-specific options, intrinsic vs window size sanity.

Skipped when no $DISPLAY is set — run with `xvfb-run -a pytest
tests/test_ai_session_dialog_widgets.py` (a real X server also works).
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if not os.environ.get("DISPLAY"):
    pytest.skip(
        "AISessionDialog widget tests need a display; "
        "run with `xvfb-run -a pytest tests/test_ai_session_dialog_widgets.py`",
        allow_module_level=True,
    )

import gi  # noqa: E402

gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from bterminal.providers import reset_registry  # noqa: E402
from bterminal.ui.dialogs.ai_session import AISessionDialog  # noqa: E402


# ─── Fixtures ────────────────────────────────────────────────────────────────


@pytest.fixture(autouse=True)
def _reset_singleton():
    """Each test gets a fresh provider registry — defaults.json wins."""
    reset_registry()
    yield
    reset_registry()


def _stub_parent(existing_sessions=()):
    """Minimal parent that satisfies ClaudeCodeDialog.__init__ contract.

    ClaudeCodeDialog reads parent.claude_manager.all() (folder combo
    population) and parent._plugins (per-tab plugin checkbox list).
    Both are tolerated as missing/empty — we wire the bare minimum.
    """
    parent = Gtk.Window()
    mgr = MagicMock()
    mgr.all.return_value = list(existing_sessions)
    parent.claude_manager = mgr
    parent._plugins = {}  # no GTK plugins registered
    parent.sidecar_manifests = {}  # _list_available_plugins iterates this
    return parent


def _new_dialog(parent=None, session=None):
    parent = parent or _stub_parent()
    dlg = AISessionDialog(parent, session=session)
    # Realize so we can ask the toplevel for its preferred size without
    # actually running the main loop.
    dlg.show_all()
    return dlg, parent


def _close(dlg, parent):
    dlg.destroy()
    parent.destroy()
    # Pump any pending GTK events so the destroy hooks fire deterministically.
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


def _action_buttons(dlg) -> list[Gtk.Widget]:
    """All buttons in the dialog's action area (Gtk.Dialog rolls them
    into a HeaderBar or HButtonBox depending on theme — we walk both)."""
    out: list[Gtk.Widget] = []
    action_area = dlg.get_action_area()
    if action_area is not None:
        out.extend(action_area.get_children())
    header = dlg.get_header_bar()
    if header is not None:
        for w in header.get_children():
            if isinstance(w, Gtk.Button):
                out.append(w)
    return out


def _response_for(dlg, button) -> int:
    return dlg.get_response_for_widget(button)


# ─── (a) New session — both action buttons visible+sensitive ─────────────────


def test_new_session_dialog_has_visible_save_and_cancel():
    """Bug #53 (2026-05-07): user reported no Save button in the dialog
    when creating a new AI session. Pre-fix the inherited Cancel/OK
    pair existed in the widget tree but were pushed off-screen by the
    dialog's autosize + tall content. We assert action buttons are
    visible (not just present) — `is_visible()` returns the realized
    state for an already-show_all'd dialog."""
    dlg, parent = _new_dialog()
    try:
        buttons = _action_buttons(dlg)
        responses = {_response_for(dlg, b) for b in buttons}
        assert Gtk.ResponseType.OK in responses, (
            f"Save (OK) button missing — only got responses: {responses}"
        )
        assert Gtk.ResponseType.CANCEL in responses

        for b in buttons:
            if _response_for(dlg, b) == Gtk.ResponseType.OK:
                assert b.is_visible(), "Save button is in tree but not visible"
                assert b.get_sensitive(), "Save button must be clickable on open"
    finally:
        _close(dlg, parent)


def test_new_session_dialog_default_response_is_save():
    dlg, parent = _new_dialog()
    try:
        # Pressing Enter inside an entry triggers the default response.
        # Must be OK so the user can submit by keyboard.
        for b in _action_buttons(dlg):
            if _response_for(dlg, b) == Gtk.ResponseType.OK:
                assert b.has_default(), (
                    "Save button must be the default response so Enter "
                    "submits the form"
                )
                break
        else:
            pytest.fail("no OK button found")
    finally:
        _close(dlg, parent)


# ─── (h) Action area always reachable regardless of schema size ──────────────


def test_dialog_natural_height_does_not_exceed_realistic_screen():
    """Regression for bug #53: schema container + custom prompt textview +
    plugin checkboxes pushed action area below 768px on the user's
    display. We assert the dialog's NATURAL height stays within a
    common-laptop screen (768px), so action buttons render on-screen."""
    dlg, parent = _new_dialog()
    try:
        # Gtk.Widget.get_preferred_size returns (minimum, natural) — we
        # compare against 768px (typical 1366×768 laptop). Allow 4px
        # slack for window-manager decorations.
        _, natural = dlg.get_preferred_size()
        assert natural.height <= 768 - 4, (
            f"Dialog natural height {natural.height}px overflows 768px "
            f"laptop screen — Save button falls below the fold. "
            f"Either constrain default_size or wrap content in a "
            f"GtkScrolledWindow."
        )
    finally:
        _close(dlg, parent)


def test_dialog_default_size_height_is_bounded():
    """Even with all schema fields rendered, the *initial* window size
    must be bounded — set_default_size(460, -1) lets GTK auto-fit
    which on tall content explodes vertically. Cap at ≤640px so the
    action area stays in the viewport."""
    dlg, parent = _new_dialog()
    try:
        w, h = dlg.get_size()
        # If h == -1 here (theoretically impossible after show_all,
        # belt-and-suspenders) we'd fail loudly.
        assert h > 0, "show_all must give the window a concrete height"
        assert h <= 640, (
            f"Initial dialog height {h}px > 640px — likely auto-fit "
            f"explosion. Set_default_size(width, ≤640)."
        )
    finally:
        _close(dlg, parent)


# ─── (b) Existing claude session — fields populated ──────────────────────────


def test_edit_existing_claude_session_populates_fields():
    sess = {
        "name": "MyClaude", "provider": "claude",
        "project_dir": "/tmp/mc", "color": "#89b4fa",
        "folder": "Work",
        "provider_options": {
            "resume": True, "skip_permissions": True, "sudo": False,
        },
    }
    dlg, parent = _new_dialog(session=sess)
    try:
        assert dlg.entry_name.get_text() == "MyClaude"
        assert dlg.entry_project_dir.get_text() == "/tmp/mc"
        # provider dropdown set to claude (initial provider)
        assert dlg.get_active_provider_name() == "claude"

        data = dlg.get_data()
        assert data["provider"] == "claude"
        opts = data.get("provider_options", {})
        assert opts.get("resume") is True
        assert opts.get("skip_permissions") is True
    finally:
        _close(dlg, parent)


# ─── (c) Existing copilot session ────────────────────────────────────────────


def test_edit_existing_copilot_session_uses_copilot_schema():
    sess = {
        "name": "MyCop", "provider": "copilot",
        "project_dir": "/tmp/mco", "color": "#a6e3a1",
        "provider_options": {
            "skip_permissions": True, "plan_mode": False,
            "allowed_tools": "shell(rm)\nshell(curl)",
        },
    }
    dlg, parent = _new_dialog(session=sess)
    try:
        assert dlg.get_active_provider_name() == "copilot"

        data = dlg.get_data()
        assert data["provider"] == "copilot"
        opts = data.get("provider_options", {})
        # T4.3/T4.4 fields preserved through the edit roundtrip
        assert opts.get("skip_permissions") is True
        assert opts.get("plan_mode") is False
        assert "shell(rm)" in opts.get("allowed_tools", "")
    finally:
        _close(dlg, parent)


# ─── (d) Provider switch claude→copilot mid-edit preserves shared keys ───────


def test_provider_switch_preserves_shared_skip_permissions():
    sess = {
        "name": "x", "provider": "claude", "project_dir": "/tmp/x",
        "provider_options": {"skip_permissions": True, "resume": False,
                             "sudo": False},
    }
    dlg, parent = _new_dialog(session=sess)
    try:
        # Programmatically flip the dropdown (simulates user's combo click).
        items = dlg._provider_combo_items
        copilot_idx = next(i for i, (n, _) in enumerate(items) if n == "copilot")
        dlg.provider_combo.set_active(copilot_idx)

        # _on_provider_changed fires synchronously on set_active.
        assert dlg.get_active_provider_name() == "copilot"

        # Schema rebuilt — skip_permissions widget should still be
        # checked because Copilot also has skip_permissions in schema
        # and we copy prior values across.
        skip_w = dlg._schema_widgets.get("skip_permissions")
        assert skip_w is not None, "Copilot schema must include skip_permissions"
        assert skip_w["widget"].get_active() is True, (
            "skip_permissions value lost across provider switch"
        )
    finally:
        _close(dlg, parent)


# ─── (e) Provider switch back roundtrip ──────────────────────────────────────


def test_provider_switch_roundtrip_preserves_user_values():
    """User clicks claude→copilot→claude. Skip_permissions value entered
    while the Claude schema was rendered must still be there at the end."""
    dlg, parent = _new_dialog()
    try:
        items = dlg._provider_combo_items
        claude_idx = next(i for i, (n, _) in enumerate(items) if n == "claude")
        copilot_idx = next(i for i, (n, _) in enumerate(items) if n == "copilot")

        # Start on claude (default), set skip_permissions=True
        dlg.provider_combo.set_active(claude_idx)
        skip_w = dlg._schema_widgets["skip_permissions"]
        skip_w["widget"].set_active(True)

        # Flip to copilot — skip_permissions carries over (shared key)
        dlg.provider_combo.set_active(copilot_idx)
        assert dlg._schema_widgets["skip_permissions"]["widget"].get_active() is True

        # Flip back to claude — value still there
        dlg.provider_combo.set_active(claude_idx)
        assert dlg._schema_widgets["skip_permissions"]["widget"].get_active() is True
    finally:
        _close(dlg, parent)


# ─── Task #59: provider-specific edits survive roundtrip ────────────────────
#
# Pre-fix bug: prior_values was a local var inside
# _render_schema_for_current_provider. Claude's `resume`/`sudo` widgets
# weren't in the Copilot schema, so their user-edited values lived only
# in `prior_values` and dropped on the floor when the function returned.
# A subsequent claude→copilot→claude roundtrip would re-render
# resume/sudo from `_session_data` (the constructor snapshot), losing
# any edits the user made.


def test_claude_resume_edit_survives_roundtrip_to_copilot_and_back():
    """User edits Claude resume=True (constructor saw resume=False),
    switches to Copilot, then back. The edited value must persist."""
    sess = {
        "name": "x", "provider": "claude", "project_dir": "/tmp/x",
        "provider_options": {
            "resume": False, "skip_permissions": True, "sudo": False,
        },
    }
    dlg, parent = _new_dialog(session=sess)
    try:
        items = dlg._provider_combo_items
        claude_idx = next(i for i, (n, _) in enumerate(items) if n == "claude")
        copilot_idx = next(i for i, (n, _) in enumerate(items) if n == "copilot")

        # Edit resume on the Claude schema
        dlg._schema_widgets["resume"]["widget"].set_active(True)

        dlg.provider_combo.set_active(copilot_idx)
        assert "resume" not in dlg._schema_widgets, (
            "Copilot schema doesn't expose resume — sanity check"
        )

        dlg.provider_combo.set_active(claude_idx)
        assert dlg._schema_widgets["resume"]["widget"].get_active() is True, (
            "task #59 expects user's resume=True edit to survive a "
            "claude→copilot→claude roundtrip"
        )
    finally:
        _close(dlg, parent)


def test_claude_sudo_edit_survives_roundtrip_to_copilot_and_back():
    """Same scenario as resume but for sudo — both are Claude-only."""
    sess = {
        "name": "x", "provider": "claude", "project_dir": "/tmp/x",
        "provider_options": {
            "resume": False, "skip_permissions": True, "sudo": False,
        },
    }
    dlg, parent = _new_dialog(session=sess)
    try:
        items = dlg._provider_combo_items
        claude_idx = next(i for i, (n, _) in enumerate(items) if n == "claude")
        copilot_idx = next(i for i, (n, _) in enumerate(items) if n == "copilot")

        dlg._schema_widgets["sudo"]["widget"].set_active(True)

        dlg.provider_combo.set_active(copilot_idx)
        dlg.provider_combo.set_active(claude_idx)
        assert dlg._schema_widgets["sudo"]["widget"].get_active() is True


    finally:
        _close(dlg, parent)


def test_copilot_plan_mode_edit_survives_roundtrip_to_claude_and_back():
    """Mirror of the Claude case: plan_mode and allowed_tools live only
    in the Copilot schema. User edits must survive copilot→claude→copilot."""
    sess = {
        "name": "y", "provider": "copilot", "project_dir": "/tmp/y",
        "provider_options": {
            "skip_permissions": True, "plan_mode": False,
            "allowed_tools": "",
        },
    }
    dlg, parent = _new_dialog(session=sess)
    try:
        items = dlg._provider_combo_items
        claude_idx = next(i for i, (n, _) in enumerate(items) if n == "claude")
        copilot_idx = next(i for i, (n, _) in enumerate(items) if n == "copilot")

        dlg._schema_widgets["plan_mode"]["widget"].set_active(True)

        dlg.provider_combo.set_active(claude_idx)
        assert "plan_mode" not in dlg._schema_widgets

        dlg.provider_combo.set_active(copilot_idx)
        assert dlg._schema_widgets["plan_mode"]["widget"].get_active() is True


    finally:
        _close(dlg, parent)


def test_copilot_allowed_tools_edit_survives_roundtrip_to_claude_and_back():
    sess = {
        "name": "z", "provider": "copilot", "project_dir": "/tmp/z",
        "provider_options": {
            "skip_permissions": True, "plan_mode": False,
            "allowed_tools": "",
        },
    }
    dlg, parent = _new_dialog(session=sess)
    try:
        items = dlg._provider_combo_items
        claude_idx = next(i for i, (n, _) in enumerate(items) if n == "claude")
        copilot_idx = next(i for i, (n, _) in enumerate(items) if n == "copilot")

        # Edit allowed_tools on Copilot schema
        buf = dlg._schema_widgets["allowed_tools"].get("buffer") \
              or dlg._schema_widgets["allowed_tools"]["widget"].get_buffer()
        buf.set_text("shell(rm)\nshell(curl)")

        dlg.provider_combo.set_active(claude_idx)
        dlg.provider_combo.set_active(copilot_idx)

        buf2 = dlg._schema_widgets["allowed_tools"].get("buffer") \
               or dlg._schema_widgets["allowed_tools"]["widget"].get_buffer()
        start, end = buf2.get_bounds()
        text = buf2.get_text(start, end, False)
        assert "shell(rm)" in text, (
            f"allowed_tools edit lost across roundtrip; got {text!r}"
        )
        assert "shell(curl)" in text


    finally:
        _close(dlg, parent)


def test_get_data_after_provider_switch_returns_target_provider_options_only():
    """Saving a session AFTER switching Claude→Copilot must produce a
    Copilot config — the dropped Claude keys (resume, sudo) must not
    leak into provider_options even though they live in _session_data."""
    sess = {
        "name": "x", "provider": "claude", "project_dir": "/tmp/x",
        "provider_options": {
            "resume": True, "skip_permissions": True, "sudo": True,
        },
    }
    dlg, parent = _new_dialog(session=sess)
    try:
        items = dlg._provider_combo_items
        copilot_idx = next(i for i, (n, _) in enumerate(items) if n == "copilot")
        dlg.provider_combo.set_active(copilot_idx)

        data = dlg.get_data()
        assert data["provider"] == "copilot"
        opts = data["provider_options"]
        # Claude-only keys must NOT leak into a Copilot save, even if
        # _session_data still has them after task #59's merge.
        assert "resume" not in opts, (
            f"Claude resume leaked into Copilot save: {opts}"
        )
        assert "sudo" not in opts, (
            f"Claude sudo leaked into Copilot save: {opts}"
        )
        # Shared key transferred properly
        assert opts.get("skip_permissions") is True


    finally:
        _close(dlg, parent)


# ─── (f) get_data() returns canonical R4.2 shape for both providers ──────────


def test_get_data_for_new_claude_session_canonical_r42_shape():
    dlg, parent = _new_dialog()
    try:
        dlg.entry_name.set_text("Brand New")
        dlg.entry_project_dir.set_text("/tmp/new")

        data = dlg.get_data()
        assert data["name"] == "Brand New"
        assert data["project_dir"] == "/tmp/new"
        assert data["provider"] == "claude"
        # Provider-specific options nested, not flat
        assert "provider_options" in data
        # No leftover legacy flat keys
        for legacy in ("resume", "skip_permissions", "sudo"):
            assert legacy not in data, (
                f"flat '{legacy}' leaked into top-level — must live in "
                f"provider_options (R4.2 schema)"
            )
    finally:
        _close(dlg, parent)


def test_get_data_for_new_copilot_session_canonical_r42_shape():
    dlg, parent = _new_dialog()
    try:
        # Switch to copilot first
        items = dlg._provider_combo_items
        copilot_idx = next(i for i, (n, _) in enumerate(items) if n == "copilot")
        dlg.provider_combo.set_active(copilot_idx)

        dlg.entry_name.set_text("Cop New")
        dlg.entry_project_dir.set_text("/tmp/cop")

        data = dlg.get_data()
        assert data["provider"] == "copilot"
        assert data["name"] == "Cop New"
        assert "provider_options" in data
        # Claude-only keys must not leak into the saved Copilot session
        opts = data["provider_options"]
        assert "resume" not in opts, "Copilot session must not carry --resume"
        assert "sudo" not in opts, "Copilot session has no sudo flag"
    finally:
        _close(dlg, parent)


# ─── (g) Empty project_dir state ─────────────────────────────────────────────


def test_new_session_starts_with_empty_project_dir():
    dlg, parent = _new_dialog()
    try:
        assert dlg.entry_project_dir.get_text() == ""
        # The dialog still renders without crashing — no precondition
        # on project_dir at construction time. (Validation, if any,
        # happens at OK click — out of scope here.)
    finally:
        _close(dlg, parent)


# ─── (i) Edit path doesn't lose existing copilot allowed_tools/plan_mode ─────


def test_edit_copilot_preserves_allowed_tools_and_plan_mode_on_save():
    """Open an existing Copilot session with all four T4.3/T4.4 fields,
    don't modify anything, save. The returned data must round-trip
    every value losslessly — regression for cases where empty schema
    widgets overwrite saved options."""
    sess = {
        "name": "Cop", "provider": "copilot", "project_dir": "/tmp/cop",
        "provider_options": {
            "skip_permissions": True,
            "plan_mode": True,
            "allowed_tools": "shell(rm)\nMy-MCP-Server",
        },
    }
    dlg, parent = _new_dialog(session=sess)
    try:
        data = dlg.get_data()
        opts = data["provider_options"]
        assert opts.get("skip_permissions") is True
        assert opts.get("plan_mode") is True
        assert "shell(rm)" in opts.get("allowed_tools", "")
        assert "My-MCP-Server" in opts.get("allowed_tools", "")
    finally:
        _close(dlg, parent)


def test_edit_with_unknown_provider_falls_back_to_default():
    """Forward-compat: a session config naming a future/unknown provider
    must not crash the dialog. Pick the registry default and proceed."""
    sess = {
        "name": "X", "provider": "unknown-future-cli",
        "project_dir": "/tmp/x",
    }
    # AISessionDialog reads registry.default_provider() when
    # initial_provider isn't a real name; we just check it doesn't raise.
    dlg, parent = _new_dialog(session=sess)
    try:
        active = dlg.get_active_provider_name()
        # Fall-through: combo defaults to whatever's at index 0 (claude)
        # — anything other than crash is acceptable.
        # task #3 / #75: aider added as bundled 3rd provider
        assert active in ("aider", "claude", "copilot")
    finally:
        _close(dlg, parent)


# ─── New session — plugins unchecked by default ─────────────────────────────
#
# 2026-05-13: user requested that newly created sessions have ALL plugins
# DISABLED by default. Pre-fix the dialog ticked each plugin whose
# `default_in_session` was True (i.e. nearly all of them). The fix in
# claude_code.py replaces that branch with `chk.set_active(False)` when
# no saved selection exists. These tests pin the new behavior.


def _parent_with_stub_plugins():
    """Parent stubbed with two GTK plugins and one sidecar manifest, all
    with `default_in_session=True` — pre-fix this would tick all three."""
    parent = _stub_parent()
    parent._plugins = {
        "plugin_a": SimpleNamespace(
            name="plugin_a", title="Plugin A", default_in_session=True,
        ),
        "plugin_b": SimpleNamespace(
            name="plugin_b", title="Plugin B", default_in_session=True,
        ),
    }
    parent.sidecar_manifests = {
        "sidecar_x": SimpleNamespace(
            name="sidecar_x", title="Sidecar X", default_in_session=True,
        ),
    }
    return parent


def test_new_session_has_all_plugin_checkboxes_unchecked():
    """A brand-new session (session=None) must start with every plugin
    checkbox UNCHECKED. Earlier behavior: ticked whenever the plugin's
    own `default_in_session` flag was True — surprising for the user who
    expected an opt-in model."""
    parent = _parent_with_stub_plugins()
    dlg, parent = _new_dialog(parent=parent, session=None)
    try:
        assert dlg._plugin_checks, "stub plugins must surface as checkboxes"
        for name, chk in dlg._plugin_checks.items():
            assert chk.get_active() is False, (
                f"plugin {name!r} is ticked on a new-session dialog; "
                f"expected opt-in default (all unchecked)"
            )

        # Save → enabled_plugins is an empty list, persisting the
        # user's untouched "no plugins" choice into ai_sessions.json.
        data = dlg.get_data()
        assert data.get("enabled_plugins") == []
    finally:
        _close(dlg, parent)


def test_edit_session_with_saved_plugins_respects_selection():
    """Edit-mode regression guard: an existing session that picked
    `plugin_a` must still display plugin_a ticked + the rest unticked.
    The opt-in default only applies to brand-new sessions."""
    parent = _parent_with_stub_plugins()
    sess = {
        "name": "EditMe", "provider": "claude",
        "project_dir": "/tmp/e",
        "enabled_plugins": ["plugin_a"],
    }
    dlg, parent = _new_dialog(parent=parent, session=sess)
    try:
        checks = dlg._plugin_checks
        assert checks["plugin_a"].get_active() is True
        assert checks["plugin_b"].get_active() is False
        assert checks["sidecar_x"].get_active() is False
    finally:
        _close(dlg, parent)


def test_edit_session_with_empty_enabled_plugins_keeps_all_unchecked():
    """Edge case: a session saved after the fix has `enabled_plugins=[]`.
    Re-opening it must NOT re-tick any plugin (which would happen if the
    code mistakenly treated [] as 'no preference' and fell through to
    the new-session default — except now that default is also False, so
    this is doubly safe). Pin the explicit-empty semantics."""
    parent = _parent_with_stub_plugins()
    sess = {
        "name": "Empty", "provider": "claude",
        "project_dir": "/tmp/e",
        "enabled_plugins": [],
    }
    dlg, parent = _new_dialog(parent=parent, session=sess)
    try:
        for name, chk in dlg._plugin_checks.items():
            assert chk.get_active() is False, (
                f"plugin {name!r} ticked on a session that explicitly "
                f"saved enabled_plugins=[]"
            )
    finally:
        _close(dlg, parent)
