"""Pytest wrapper for tools/test_installer_wizard_vm.sh (#15 / #87).

Source-level checks pin the contract between the bash runner and the
real wizard widgets. If a wizard page header / button label / window
title drifts, these tests fail on the host (cheap) before anyone
fires the slow real-VM run (~10 min).

The opt-in real-VM run gates behind BTERMINAL_VM_TESTS=1, same as
#85 / #86's wrappers.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_installer_wizard_vm.sh"
WIZARD_PY = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
MAIN_PY = REPO_ROOT / "bterminal" / "__main__.py"


# ─── Bash runner shape ─────────────────────────────────────────────────────


def test_wizard_vm_script_exists_and_executable():
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111


def test_wizard_vm_script_bash_syntax_valid():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_wizard_vm_script_help_returns_zero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    for flag in ("--skip-llama", "--no-postflight"):
        assert flag in result.stdout, f"help missing flag: {flag}"


def test_wizard_vm_script_unknown_flag_exits_nonzero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--bogus"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0


# ─── Wizard contract pinned by source-level checks ─────────────────────────


def test_wizard_window_title_matches_runner_xdotool_searches():
    """The runner uses `xdotool search --name 'BTerminal Installer'` to
    activate the wizard window. That title must come from the GTK
    Dialog's set_title() / __init__ title= kwarg in installer_wizard.py."""
    runner = SCRIPT.read_text()
    wizard = WIZARD_PY.read_text()
    title_literal = "BTerminal Installer"
    assert title_literal in runner, (
        "runner doesn't reference the wizard title literal"
    )
    assert f'title="{title_literal}"' in wizard, (
        f"installer_wizard.py no longer sets title to {title_literal!r}"
    )


@pytest.mark.parametrize("idx, header_prefix", [
    (0, "Step 1 of 5: Welcome"),
    (1, "Step 2 of 5: System inventory"),
    (2, "Step 3 of 5: Pick what to install"),
    (3, "Step 4 of 5: Installing"),
    (4, "Step 5 of 5: Summary"),
])
def test_wizard_page_headers_match_runner_grep_targets(idx, header_prefix):
    """The runner greps the wizard's own log for these per-page
    headers as the 'are we on page N yet?' signal. Drift in either
    direction breaks navigation."""
    runner = SCRIPT.read_text()
    wizard = WIZARD_PY.read_text()
    assert header_prefix in runner, (
        f"runner no longer references page {idx + 1} header"
    )
    assert header_prefix in wizard, (
        f"installer_wizard.py no longer emits {header_prefix!r}"
    )


def test_wizard_runner_uses_xvfb_run_with_pinned_display():
    """xvfb-run -a + DISPLAY=:99 — pinning lets xdotool find the
    window unambiguously even if the VM has another X server."""
    text = SCRIPT.read_text()
    assert "xvfb-run" in text
    assert "DISPLAY=:99" in text


def test_wizard_runner_invokes_module_entrypoint():
    """The bash runner spawns `python3 -m bterminal --installer`. The
    --installer flag must be wired in __main__.py."""
    runner = SCRIPT.read_text()
    main = MAIN_PY.read_text()
    assert "python3 -m bterminal --installer" in runner
    assert "--installer" in main
    assert "_run_installer_wizard" in main


def test_wizard_runner_passes_repo_dir_env_for_chicken_egg():
    """Wizard runs PRE-BT-install — needs the cloned tree on PYTHONPATH
    + BTERMINAL_REPO_DIR env so installer_wizard.py imports & install.sh
    is invokable. The fix is documented in #6 / #78."""
    text = SCRIPT.read_text()
    assert "BTERMINAL_REPO_DIR" in text
    assert "PYTHONPATH=" in text


def test_wizard_runner_polls_for_phase_done_progress_100():
    """Page 4 advances by polling the wizard log for the install.sh
    --status-json terminal event. Same JSON markers as test_install_vm.sh
    phase B. The grep strings live inside ssh-quoted bash so the file
    has backslash-escaped inner quotes — match against the substring
    minus the leading quote so the assertion survives both forms."""
    text = SCRIPT.read_text()
    assert 'phase' in text and 'done' in text
    # Distinctive enough — only appears in install.sh's status_json output
    assert "progress\\\": 100" in text or '"progress": 100' in text


def test_wizard_runner_kills_orphans_before_spawning():
    """Without cleanup hook a prior aborted run would leak Xvfb + the
    wizard, and the next run's xdotool would target the stale window."""
    text = SCRIPT.read_text()
    assert "pkill" in text
    assert "Xvfb" in text


def test_wizard_runner_postflight_checks_aider_binary():
    """Post-install verifies aider exists — Aider provider's find_binary
    walks ~/.local/bin/aider. Without this the install would 'succeed'
    but Aider sessions wouldn't spawn."""
    text = SCRIPT.read_text()
    assert "~/.local/bin/aider" in text
    assert "aider" in text


def test_wizard_runner_postflight_pings_ollama_api():
    """Soft-PASSing ollama API ping — daemon may need user systemd
    session to auto-start, which xvfb-run-only VMs lack. Recorded as
    documented quirk in tests/manual/README.md."""
    text = SCRIPT.read_text()
    assert "11434" in text
    assert "/api/tags" in text


def test_wizard_runner_emits_pass_fail_summary():
    text = SCRIPT.read_text()
    assert "Total:" in text
    assert "passed" in text and "failed" in text
    assert "FAIL_LIST" in text


def test_wizard_runner_handles_skip_llama_branch():
    """--skip-llama omits the second checkbox tick on page 3 +
    skips the post-flight ollama checks. Both branches must exist."""
    text = SCRIPT.read_text()
    assert "SKIP_LLAMA" in text
    # Two branches: one keystroke seq with llama tick, one without
    assert text.count("PICKS_KEYSTROKES") >= 2


# ─── Wizard widget-layout assumptions (catches GTK refactors) ──────────────


def test_wizard_action_buttons_layout_unchanged():
    """The runner's Tab counts assume action area order:
    Cancel → Back → Next → Finish. If the order changes (e.g. someone
    adds a 'Help' button) the keystroke sequences will hit the wrong
    target."""
    text = WIZARD_PY.read_text()
    cancel_pos = text.find("self.btn_cancel = self.add_button(")
    back_pos   = text.find("self.btn_back = self.add_button(")
    next_pos   = text.find("self.btn_next = self.add_button(")
    finish_pos = text.find("self.btn_finish = self.add_button(")

    for name, pos in [("btn_cancel", cancel_pos), ("btn_back", back_pos),
                       ("btn_next", next_pos), ("btn_finish", finish_pos)]:
        assert pos > 0, f"installer_wizard.py no longer creates {name}"

    # Order: cancel < back < next < finish
    assert cancel_pos < back_pos < next_pos < finish_pos, (
        "action button creation order changed — runner Tab counts "
        "would target the wrong button"
    )


# ─── #131 — GTK theme resilience guards ────────────────────────────────


def test_wizard_runner_has_advance_with_fallback_helper():
    """Pin: the runner has `_advance_with_fallback` that retries
    with F10-menu navigation when Tab counts miss the target.
    Mitigates non-default GTK themes (HighContrast, custom)
    that may shift action-area button ordering."""
    text = SCRIPT.read_text()
    assert "_advance_with_fallback" in text, (
        "tools/test_installer_wizard_vm.sh missing the F10 "
        "fallback helper for theme-resilient nav"
    )


def test_wizard_runner_uses_f10_menu_chord_in_fallback():
    """Pin: F10 keystroke present (GTK convention for entering
    menu/action-area chord). xdotool can't address widgets by
    name so this is the keyboard-driven escape hatch."""
    text = SCRIPT.read_text()
    assert "F10" in text, (
        "no F10 keystroke in runner — fallback for non-default "
        "GTK themes missing"
    )


def test_wizard_runner_acknowledges_gtk_theme_env_override():
    """Pin: runner detects `GTK_THEME` env var and announces
    fallback armed. Without this, a maintainer running under
    `GTK_THEME=HighContrast` would see no diagnostic when Tab
    counts miss."""
    text = SCRIPT.read_text()
    assert "GTK_THEME" in text
    # Diagnostic message
    assert "non-default theme" in text or \
        "fallback xdotool sequences armed" in text


def test_fallback_helper_takes_primary_and_fallback_keystroke_args():
    """Pin: the helper signature accepts `<primary-keys>` AND
    `[fallback-keys]` so different pages can supply their own
    fallback sequences. Default fallback is `F10 Return` (open
    menu, activate default action)."""
    text = SCRIPT.read_text()
    helper_idx = text.find("_advance_with_fallback() {")
    assert helper_idx > 0
    fn_end = text.find("\n}\n", helper_idx)
    body = text[helper_idx:fn_end + 2]

    # 4 positional args
    assert 'name="$1"' in body
    assert 'expected_header="$2"' in body
    assert 'primary_keys="$3"' in body
    assert 'fallback_keys="${4:-F10 Return}"' in body


def test_fallback_helper_retries_only_when_primary_fails():
    """Pin: fallback fires ONLY when primary attempt's expected
    page header isn't reached. Avoids running fallback as
    primary on default themes (where it would unnecessarily
    open the menu)."""
    text = SCRIPT.read_text()
    helper_idx = text.find("_advance_with_fallback() {")
    fn_end = text.find("\n}\n", helper_idx)
    body = text[helper_idx:fn_end + 2]

    # Helper greps for header BEFORE deciding to fallback
    grep_idx = body.find("grep -F '${expected_header}'")
    assert grep_idx > 0
    # Fallback xdotool keys come AFTER the grep gate
    fallback_idx = body.find("fallback_keys")
    primary_xdotool = body.find("primary_keys")
    assert primary_xdotool > 0
    assert fallback_idx > primary_xdotool, (
        "fallback declared before primary — runner would fire "
        "fallback even when primary keys would have worked"
    )


def test_fallback_helper_logs_to_distinct_file():
    """Pin: primary keys → `<name>-keys.log`, fallback →
    `<name>-fallback.log`. Distinct filenames so debug can tell
    which path was taken without grepping."""
    text = SCRIPT.read_text()
    helper_idx = text.find("_advance_with_fallback() {")
    fn_end = text.find("\n}\n", helper_idx)
    body = text[helper_idx:fn_end + 2]
    assert "${name}-keys.log" in body
    assert "${name}-fallback.log" in body


def test_fallback_helper_sleeps_between_keystroke_and_verify():
    """Pin: `sleep 1` between xdotool send + grep verify — gives
    GTK time to render the new page. Without this, the verify
    grep races the page transition and falsely reports failure."""
    text = SCRIPT.read_text()
    helper_idx = text.find("_advance_with_fallback() {")
    fn_end = text.find("\n}\n", helper_idx)
    body = text[helper_idx:fn_end + 2]
    # At least 2 sleep 1 (one after primary, one after fallback)
    assert body.count("sleep 1") >= 2


def test_button_layout_test_documents_help_button_concern():
    """Pin: the existing `test_wizard_action_buttons_layout_unchanged`
    test mentions the 'Help button' scenario in its docstring —
    that's the canonical custom-theme case (a) Adwaita /
    (b) HighContrast / (c) custom-with-Help. Without the doc,
    a maintainer might miss why the order check matters."""
    text = WIZARD_PY.read_text() if False else None
    test_text = (REPO_ROOT / "tests"
                 / "test_installer_wizard_vm.py").read_text()
    fn_idx = test_text.find(
        "def test_wizard_action_buttons_layout_unchanged")
    next_def = test_text.find("\n\ndef ", fn_idx + 1)
    body = test_text[fn_idx:next_def]
    assert "'Help' button" in body or "Help button" in body, (
        "button-order test no longer documents the Help-button "
        "custom-theme regression case"
    )


def test_wizard_license_checkbox_label_unchanged():
    """The license checkbox label is what the runner relies on (Space
    on a Tab-focused checkbox toggles it). If the label or its widget
    type changes, the test would falsely report 'license accepted'."""
    text = WIZARD_PY.read_text()
    assert 'I have read and accept the license terms.' in text
    assert "Gtk.CheckButton" in text


# ─── Opt-in real-VM run ────────────────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("BTERMINAL_VM_TESTS") != "1",
    reason="VM-bound + needs xvfb + xdotool — set BTERMINAL_VM_TESTS=1",
)
def test_wizard_vm_real_run_passes():
    """End-to-end real-VM wizard E2E. Skipped by default. Failures here
    mean GUI installer flow on a clean machine is broken — catches:
      - --installer entrypoint regressions
      - status_json contract drift
      - install.sh --headless --selected llama failing in Xvfb env
      - aider not installed despite llama opt-in
    Runs ~10 min on a typical VM."""
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True, text=True, timeout=900,
    )
    assert result.returncode == 0, (
        f"wizard VM E2E failed exit={result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-3000:]}\n"
        f"--- stderr ---\n{result.stderr[-1500:]}"
    )
