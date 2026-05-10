"""Tests for InstallerWizard (task #5 / #77 in audit doc).

Two tiers:
  1. Pure helpers (no GTK) — strip_ansi, parse_status_json_line,
     build_install_argv. Fast, deterministic.
  2. GTK widget tests pod xvfb-run — wizard construction, page nav,
     state machine, cancel mid-install. Skipped when no $DISPLAY,
     same pattern as test_ai_session_dialog_widgets.py.
"""
from __future__ import annotations

import json
import os
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))


# ─── Pure helpers ──────────────────────────────────────────────────────────

# Import these BEFORE the xvfb skip — pure helpers must be testable
# even without a display.
from bterminal.ui.installer_wizard import (
    build_install_argv,
    parse_status_json_line,
    strip_ansi,
)


# strip_ansi


def test_strip_ansi_removes_csi_sequences():
    inp = "\x1b[32m✓\x1b[0m installed"
    assert strip_ansi(inp) == "✓ installed"


def test_strip_ansi_removes_osc_window_title():
    """install.sh emits OSC sequences via mock — strip those too."""
    inp = "before\x1b]0;Window Title\x07after"
    assert strip_ansi(inp) == "beforeafter"


def test_strip_ansi_idempotent_on_clean_text():
    assert strip_ansi("plain text") == "plain text"
    assert strip_ansi("") == ""


def test_strip_ansi_handles_multiline():
    inp = "\x1b[31mFAIL\x1b[0m\n\x1b[32mOK\x1b[0m\n"
    assert strip_ansi(inp) == "FAIL\nOK\n"


# parse_status_json_line


def test_parse_status_json_returns_dict_for_valid_line():
    line = ('{"phase": "claude", "status": "installing", '
            '"progress": 15, "label": "Checking Claude"}')
    out = parse_status_json_line(line)
    assert out == {
        "phase": "claude", "status": "installing",
        "progress": 15, "label": "Checking Claude",
    }


def test_parse_status_json_strips_whitespace():
    line = '   {"phase":"x","status":"ok","progress":50,"label":""}\n'
    assert parse_status_json_line(line) is not None


def test_parse_status_json_returns_none_for_non_json():
    assert parse_status_json_line("[1/7] Checking runtime...") is None
    assert parse_status_json_line("  ✓ git") is None
    assert parse_status_json_line("") is None


def test_parse_status_json_returns_none_for_partial_json():
    """Schema gate: missing one of {phase, status, progress, label}
    → not a status line. Could be unrelated JSON output from some
    other tool."""
    assert parse_status_json_line('{"phase": "x"}') is None
    assert parse_status_json_line('{"status": "ok"}') is None
    assert parse_status_json_line(
        '{"phase":"x","status":"ok","progress":50}') is None


def test_parse_status_json_returns_none_for_invalid_json():
    assert parse_status_json_line("{bad json}") is None
    assert parse_status_json_line("{,}") is None


def test_parse_status_json_returns_none_for_array():
    """Top-level must be an object — arrays mean something else."""
    assert parse_status_json_line('[1, 2, 3]') is None


# build_install_argv


def test_build_install_argv_minimal():
    argv = build_install_argv("/path/to/repo", [])
    assert argv == [
        "bash", "/path/to/repo/install.sh",
        "--headless", "--status-json",
    ]


def test_build_install_argv_with_selected_csv():
    argv = build_install_argv("/repo", ["meld", "pandoc"])
    assert "--selected" in argv
    sel_idx = argv.index("--selected")
    assert argv[sel_idx + 1] == "meld,pandoc"


def test_build_install_argv_with_no_sudo():
    argv = build_install_argv("/repo", [], no_sudo=True)
    assert "--no-sudo" in argv


def test_build_install_argv_with_llama_token():
    """Llama opt-in shows up as a regular CSV entry — install.sh's
    selected_includes() handles 'llama'/'ollama' specially."""
    argv = build_install_argv("/repo", ["meld", "llama"])
    sel_idx = argv.index("--selected")
    assert argv[sel_idx + 1] == "meld,llama"


def test_build_install_argv_omits_selected_when_empty():
    """Empty selected list → no --selected flag → install.sh keeps
    legacy 'try every auto dep' behaviour. Required so users can
    rerun the wizard without picks and get the old behaviour."""
    argv = build_install_argv("/repo", [], no_sudo=True)
    assert "--selected" not in argv


def test_build_install_argv_starts_with_bash_and_install_sh():
    """Spawned via bash explicitly so we don't depend on +x bit on
    a freshly-cloned tree (some tarball downloads strip exec)."""
    argv = build_install_argv("/some/clone", ["git-lfs"])
    assert argv[0] == "bash"
    assert argv[1].endswith("/install.sh")


# ─── GTK widget tests (xvfb-run skip) ──────────────────────────────────────


if not os.environ.get("DISPLAY"):
    pytest.skip(
        "InstallerWizard widget tests need a display; "
        "run with `xvfb-run -a pytest tests/test_installer_wizard.py`",
        allow_module_level=True,
    )


import gi  # noqa: E402
gi.require_version("Gtk", "3.0")
from gi.repository import Gtk  # noqa: E402

from bterminal.ui.installer_wizard import (  # noqa: E402
    InstallerWizard, _WIZARD_NEXT, _WIZARD_BACK,
)


@pytest.fixture
def wizard(tmp_path):
    # Stub repo_dir as the real BTerminal repo so populate_inventory
    # has DEPENDENCIES to read. We don't actually run install.sh
    # — only construction + nav state.
    w = InstallerWizard(parent=None, repo_dir=str(REPO_ROOT))
    yield w
    w.destroy()
    while Gtk.events_pending():
        Gtk.main_iteration_do(False)


# Construction


def test_wizard_constructs_with_5_pages(wizard):
    assert len(wizard.PAGES) == 5
    assert wizard.PAGES == (
        "welcome", "inventory", "picks", "progress", "summary",
    )


def test_wizard_starts_on_welcome_page(wizard):
    assert wizard._current_page == 0
    assert wizard.stack.get_visible_child_name() == "welcome"


def test_wizard_header_shows_step_count(wizard):
    """Header markup must include 'Step N of 5' so users know
    progress within the wizard."""
    text = wizard.lbl_header.get_text()
    assert "Step 1 of 5" in text


# Navigation buttons


def test_next_button_disabled_until_license_accepted(wizard):
    assert not wizard.btn_next.get_sensitive()
    wizard.chk_accept.set_active(True)
    assert wizard.btn_next.get_sensitive()


def test_back_button_hidden_on_first_page(wizard):
    assert not wizard.btn_back.get_visible()


def test_finish_button_hidden_until_summary_page(wizard):
    """'Open BTerminal' must only show on page 5 (summary)."""
    assert not wizard.btn_finish.get_visible()


def test_cancel_button_visible_on_progress_page(wizard):
    """User must be able to abort mid-install (page 4) — cancel
    triggers force_exit on the subprocess."""
    wizard._show_page(3)
    assert wizard.btn_cancel.get_visible()


def test_cancel_button_hidden_on_summary_page(wizard):
    """After install completes, only 'Open BTerminal' (or window
    close) makes sense — Cancel is meaningless."""
    wizard._show_page(4)
    assert not wizard.btn_cancel.get_visible()


# State machine


def test_show_page_advances_through_pages(wizard):
    wizard.chk_accept.set_active(True)  # unlock Next
    for idx in range(5):
        wizard._show_page(idx)
        assert wizard._current_page == idx
        assert wizard.stack.get_visible_child_name() == wizard.PAGES[idx]


def test_back_button_visibility_per_page(wizard):
    """Only pages 1 (inventory) and 2 (picks) show Back. Welcome
    has nowhere to go back; progress is locked during install;
    summary is final."""
    wizard.chk_accept.set_active(True)
    for idx in range(5):
        wizard._show_page(idx)
        if idx in (1, 2):
            assert wizard.btn_back.get_visible(), (
                f"Back should be visible on page {idx}")
        else:
            assert not wizard.btn_back.get_visible(), (
                f"Back should be HIDDEN on page {idx}")


def test_back_disabled_during_install(wizard):
    """Mid-install back-out is messy (subprocess running) — disable
    the button rather than supporting it."""
    wizard._show_page(3)
    assert not wizard.btn_back.get_sensitive() or not wizard.btn_back.get_visible()


# Inventory page


def test_inventory_page_populated_from_diagnostics(wizard, monkeypatch):
    from bterminal.diagnostics import DepStatus, DepSpec

    def fake_audit():
        return [
            DepStatus(spec=DepSpec("git", "git", "git", "required", ""),
                      present=True, path="/usr/bin/git", version="2.40"),
        ]

    monkeypatch.setattr(
        "bterminal.diagnostics.audit", fake_audit,
    )
    wizard._show_page(1)
    text = wizard.txt_inventory.get_buffer().props.text
    assert "git" in text


# Picks page


def test_picks_page_renders_checkbox_per_optional_dep(wizard):
    wizard._show_page(2)
    # Each non-required dep gets a checkbox
    from bterminal.diagnostics import DEPENDENCIES
    expected_optional_count = sum(
        1 for d in DEPENDENCIES if d.tier != "required")
    assert len(wizard._dep_checkboxes) == expected_optional_count


def test_picks_page_includes_llama_opt_in_checkbox(wizard):
    wizard._show_page(2)
    assert wizard._llama_check is not None
    # Default OFF — opt-in (curl|sh is sensitive)
    assert wizard._llama_check.get_active() is False


def test_gather_selected_deps_returns_ticked_only(wizard):
    wizard._show_page(2)
    # Untick everything first
    for chk in wizard._dep_checkboxes.values():
        chk.set_active(False)
    if wizard._llama_check:
        wizard._llama_check.set_active(False)
    # Tick exactly one
    if "meld" in wizard._dep_checkboxes:
        wizard._dep_checkboxes["meld"].set_active(True)
    selected = wizard._gather_selected_deps()
    if "meld" in wizard._dep_checkboxes:
        assert selected == ["meld"]
    else:
        # Some test envs lack meld in DEPENDENCIES — tick another
        # checkbox to verify the gather path
        first = next(iter(wizard._dep_checkboxes))
        wizard._dep_checkboxes[first].set_active(True)
        assert first in wizard._gather_selected_deps()


def test_gather_selected_deps_includes_llama_token_when_checked(wizard):
    wizard._show_page(2)
    for chk in wizard._dep_checkboxes.values():
        chk.set_active(False)
    wizard._llama_check.set_active(True)
    selected = wizard._gather_selected_deps()
    assert "llama" in selected


# Cancel mid-install


def test_cancel_install_force_exits_subprocess(wizard):
    """_cancel_install must call force_exit on the live subprocess
    to stop the install loop. We use a MagicMock to verify the
    method was called — actually spawning install.sh in a unit
    test is too slow + side-effecting."""
    fake_proc = MagicMock()
    wizard._install_proc = fake_proc
    wizard._cancel_install()
    assert wizard._cancelled is True
    fake_proc.force_exit.assert_called_once()
    assert wizard._install_proc is None


def test_cancel_install_no_op_when_proc_is_none(wizard):
    """Cancel after subprocess already finished must not crash."""
    wizard._install_proc = None
    wizard._cancel_install()
    assert wizard._cancelled is True


def test_handle_install_line_parses_status_and_updates_progress(wizard):
    """JSON status line → progress bar updates, label changes."""
    wizard._show_page(3)
    line = ('{"phase": "claude", "status": "installing", '
            '"progress": 50, "label": "Checking Claude"}')
    wizard._handle_install_line(line)
    # ProgressBar fraction = 0.5 (within float tolerance)
    assert abs(wizard.progress.get_fraction() - 0.5) < 0.01


def test_handle_install_line_appends_non_json_to_log(wizard):
    wizard._show_page(3)
    wizard._handle_install_line("[1/7] Checking runtime...")
    log_text = wizard.txt_log.get_buffer().props.text
    assert "Checking runtime" in log_text


def test_handle_install_line_strips_ansi_in_log(wizard):
    wizard._show_page(3)
    wizard._handle_install_line("\x1b[32m✓\x1b[0m git installed")
    log_text = wizard.txt_log.get_buffer().props.text
    assert "✓ git installed" in log_text
    assert "\x1b" not in log_text


def test_handle_install_line_marks_done_on_terminal_status(wizard):
    """Terminal status_json {phase: done, status: ok, progress: 100}
    sets _final_status_seen so the wait_async callback knows install
    succeeded vs was killed."""
    wizard._show_page(3)
    wizard._handle_install_line(
        '{"phase": "done", "status": "ok", "progress": 100, "label": "Done"}'
    )
    assert wizard._final_status_seen is True
