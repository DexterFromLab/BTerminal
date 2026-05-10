"""Race condition: InstallerWizard cancel during apt phase
(#39 / #111, audit § 6.2 #12).

When the user clicks Cancel mid-install (most catastrophically
during a long-running `sudo apt-get install` step), the wizard
must:

  1. Send SIGTERM (NOT SIGKILL) to the install.sh subprocess so
     install.sh's `trap '_on_interrupt' INT TERM` handler from
     #105 fires and rolls back from BACKUP_DIR.
  2. Schedule a SIGKILL fallback after 5 s in case rollback hangs
     (e.g. mid-`sudo` waiting for password input that won't come).
  3. Always advance to the summary page — pre-#111 only the
     success path advanced, leaving the cancel user stuck on the
     progress page.
  4. Render a 'Cancelled — partial install' banner on the summary
     page with guidance ('re-run; already-installed pieces are
     skipped').
  5. NOT release apt's dpkg lock manually — apt's own teardown
     handles that when the process group dies. Pin: wizard doesn't
     try to clean up dpkg.

Three decision branches:
  (a) Cancel during apt — SIGTERM hits install.sh which is awaiting
      `sudo apt-get install`. install.sh's INT/TERM trap fires,
      rollback runs, BACKUP_DIR restores. apt itself sees its
      child sudo die, abandons the install (dpkg's own atomicity
      handles partial state).
  (b) Cancel during pipx — install.sh isn't running pipx today
      (pipx not in install.sh per #106 finding), so this branch
      is N/A. Pinned in test for documentation.
  (c) Cancel during ollama curl — `curl … | sh` is wrapped in
      `if`, so SIGTERM kills curl, the `if` fails, install.sh
      `warn`s and continues (doesn't trigger ERR rollback).

Manual VM smoke (trigger wizard, on progress page send SIGTERM
via xdotool) is documented in tests/manual/README.md.
"""
from __future__ import annotations

import os
import re
import signal
import subprocess
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
WIZARD = REPO_ROOT / "bterminal" / "ui" / "installer_wizard.py"
INSTALL_SH = REPO_ROOT / "install.sh"


# ─── Source-grep: SIGTERM + SIGKILL fallback ─────────────────────────────


def test_cancel_install_uses_sigterm_for_graceful_shutdown():
    """Pin: `_cancel_install` sends SIGTERM (signal 15), NOT
    SIGKILL via force_exit. SIGTERM lets install.sh's INT/TERM
    trap fire → rollback runs."""
    src = WIZARD.read_text()
    fn_start = src.find("def _cancel_install")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "send_signal(15)" in body, (
        f"_cancel_install no longer uses SIGTERM(15) — install.sh "
        f"trap won't fire. Body: {body!r}"
    )


def test_cancel_install_includes_sigkill_fallback():
    """Pin: 5 s timeout fires force_exit() if SIGTERM rollback
    hangs. Without this, a stuck `sudo` (waiting for password)
    would leave the wizard hung indefinitely."""
    src = WIZARD.read_text()
    fn_start = src.find("def _cancel_install")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "GLib.timeout_add_seconds" in body, (
        "_cancel_install missing SIGKILL fallback timer"
    )
    assert "force_exit" in body, (
        "_cancel_install lost force_exit fallback"
    )
    # Specifically: 5 second timer
    assert "timeout_add_seconds(5" in body, (
        f"SIGKILL fallback timeout != 5s: {body[:500]!r}"
    )


def test_cancel_install_clears_subprocess_handle():
    """After SIGTERM, `_install_proc` is set to None so subsequent
    cancels don't re-fire signals against a dead process."""
    src = WIZARD.read_text()
    fn_start = src.find("def _cancel_install")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "self._install_proc = None" in body


def test_cancel_install_sets_cancelled_flag():
    """The `_cancelled` flag drives summary rendering. Without it
    set early, the summary would show 'Installation finished'
    even after a cancel."""
    src = WIZARD.read_text()
    fn_start = src.find("def _cancel_install")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "self._cancelled = True" in body


# ─── Source-grep: summary page renders cancelled state ──────────────────


def test_populate_summary_branches_on_cancelled_flag():
    """Pin: `_populate_summary` checks `self._cancelled` and
    renders distinct banner. Pre-#111 the page always said
    'Installation finished'."""
    src = WIZARD.read_text()
    fn_start = src.find("def _populate_summary")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "if self._cancelled:" in body
    # Cancelled banner mentions partial install
    assert "Cancelled" in body
    assert "partial install" in body or "partial" in body.lower()


def test_summary_cancelled_banner_includes_resume_guidance():
    """User-facing message tells them to re-run + that already-
    installed pieces are skipped. Without it, they're left
    wondering whether to start over from scratch."""
    src = WIZARD.read_text()
    fn_start = src.find("def _populate_summary")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    body_lower = body.lower()
    assert "re-run" in body_lower or "rerun" in body_lower
    assert "skip" in body_lower or "already" in body_lower


def test_summary_success_banner_unchanged():
    """Negative parity: success path STILL shows 'Installation
    finished'. Pin so a refactor that only sets the cancelled
    branch doesn't accidentally drop the success message."""
    src = WIZARD.read_text()
    fn_start = src.find("def _populate_summary")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "Installation finished" in body


# ─── Source-grep: progress→summary transition fires on BOTH paths ────────


def test_progress_to_summary_transition_fires_on_cancel_too():
    """Pre-#111 the transition was inside `if not self._cancelled:`
    — cancelled users got stuck on progress page. Pin: the
    transition fires unconditionally (so cancelled users see the
    summary)."""
    src = WIZARD.read_text()
    on_done_idx = src.find("def _on_install_done")
    next_def = src.find("\n    def ", on_done_idx + 1)
    body = src[on_done_idx:next_def]
    # Should reach _show_page(4) regardless of _cancelled
    assert "_show_page(4)" in body
    # And the wrapping `if not self._cancelled:` should be gone
    # (or at least the show_page must NOT be inside it)
    show_idx = body.find("_show_page(4)")
    preceding = body[:show_idx]
    # Last 'if' keyword before show_page(4) should NOT gate on _cancelled
    last_if = preceding.rfind("if ")
    if last_if > 0:
        if_to_show = preceding[last_if:]
        assert "not self._cancelled" not in if_to_show, (
            f"_show_page(4) still gated by `if not self._cancelled`: "
            f"{if_to_show[:200]!r}"
        )


# ─── install.sh side: trap fires on SIGTERM (regression from #105) ──────


def test_install_sh_trap_int_term_still_present():
    """Cross-check: the wizard's SIGTERM strategy assumes
    install.sh's INT/TERM trap is in place (added in #105). Pin
    here so a refactor that drops the trap silently breaks the
    cancel flow."""
    src = INSTALL_SH.read_text()
    assert "trap '_on_interrupt' INT TERM" in src
    # And the handler emits the rollback marker
    assert "BTERMINAL_INTERRUPT_ROLLBACK_OK" in src


def test_install_sh_handles_sigterm_during_long_running_apt(tmp_path):
    """End-to-end: spawn install.sh-style stub that sleeps inside
    a long simulated `apt-get install`, send SIGTERM, verify the
    INT/TERM trap fires and the BACKUP_DIR restore loop runs.

    Mirrors what wizard's SIGTERM does when user cancels during
    apt phase."""
    src = INSTALL_SH.read_text()
    on_interrupt_start = src.find("# #105")
    trap_end = src.find("trap '_on_interrupt' INT TERM\n",
                          on_interrupt_start)
    snippet = src[on_interrupt_start:trap_end + len(
        "trap '_on_interrupt' INT TERM\n")]

    # Pre-populate BACKUP_DIR so rollback fires the success branch
    backup_dir = tmp_path / "backup"
    backup_dir.mkdir()
    (backup_dir / "ctx").write_text("# orig ctx\n")
    install_dir = tmp_path / "install"
    install_dir.mkdir()
    (install_dir / "ctx").write_text("# current ctx\n")

    test_script = tmp_path / "apt_phase_repro.sh"
    test_script.write_text(
        "#!/bin/bash\n"
        "set -uo pipefail\n"
        f'BACKUP_DIR="{backup_dir}"\n'
        f'INSTALL_DIR="{install_dir}"\n'
        'BTERMINAL_FILES=(ctx consult tasks claude_log memory_wizard)\n'
        f"{snippet}\n"
        # Simulate long apt-get install (5 s sleep)
        "echo 'apt-get install -y meld'\n"
        "sleep 5\n"
    )
    test_script.chmod(0o755)

    log_path = tmp_path / "log.txt"
    log_handle = open(log_path, "w")
    proc = subprocess.Popen(
        ["bash", str(test_script)],
        stdout=log_handle, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        time.sleep(0.5)  # let traps arm + enter sleep
        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=10)
    finally:
        log_handle.close()

    log = log_path.read_text()
    # Trap fired, rollback emitted, exit 130
    assert "BTERMINAL_INTERRUPT_ROLLBACK_OK" in log, (
        f"INT/TERM trap didn't fire on SIGTERM: {log[-500:]!r}"
    )
    assert rc == 130
    # File restored from backup
    assert "orig ctx" in (install_dir / "ctx").read_text()


# ─── (b) Cancel during pipx: N/A — install.sh has no pipx ───────────────


def test_install_sh_does_not_invoke_pipx_anywhere():
    """Auto-trigger plan punkt (b) — cancel during pipx — is N/A
    because install.sh has no pipx invocation. Pin so a refactor
    that adds pipx doesn't accidentally pick up the cancel path
    without auditing how SIGTERM affects pipx (pipx writes to a
    venv; pip mid-install can leave half-staged packages)."""
    src = INSTALL_SH.read_text()
    assert "pipx install" not in src, (
        "install.sh now uses pipx — cancel-during-pipx path needs "
        "audit (pipx may need explicit cleanup, unlike apt's atomic "
        "dpkg state)"
    )


# ─── (c) Cancel during ollama curl: graceful warn, no rollback ──────────


def test_ollama_curl_pipe_sh_handled_in_if_block():
    """`curl ollama.com/install.sh | sh` is inside `if`. SIGTERM
    kills curl, the `if` fails (curl exits non-zero), install.sh
    `warn`s and continues. NO ERR rollback fires for ollama.

    Pin: cancel during ollama curl leaves the rest of the install
    in 'partial' state (BT files installed, but ollama missing)."""
    src = INSTALL_SH.read_text()
    curl_idx = src.find("curl -fsSL https://ollama.com/install.sh")
    assert curl_idx > 0
    # Walk backwards to find `if` keyword
    preceding = src[max(0, curl_idx - 300):curl_idx]
    assert "if " in preceding, (
        "ollama curl not in `if` block — SIGTERM during ollama "
        "would now fatally fail the install"
    )


def test_ollama_install_failure_warns_only():
    """Verify ollama failure path uses `warn` (yellow ⚠), not
    `fail` (red ✗) — same contract pinned by #106 disk_full
    tests. Cancel during ollama is logically equivalent to ENOSPC
    during ollama: install.sh continues."""
    src = INSTALL_SH.read_text()
    ollama_idx = src.find("Ollama install failed")
    assert ollama_idx > 0
    line_start = src.rfind("\n", 0, ollama_idx) + 1
    line_end = src.find("\n", ollama_idx)
    line = src[line_start:line_end]
    assert "warn" in line.lower()


# ─── No zombie dpkg lock: apt's own atomicity handles it ────────────────


def test_wizard_does_not_manually_kill_apt_or_dpkg():
    """Pin: the wizard sends SIGTERM to install.sh ONLY. It does
    NOT directly kill apt / dpkg / sudo. Apt's own atomicity
    handles partial state when its parent (sudo, install.sh's
    invocation) dies. Manual cleanup would create more problems.

    Source-grep skips docstring lines (which legitimately mention
    apt-get explaining why we DON'T touch it)."""
    src = WIZARD.read_text()
    fn_start = src.find("def _cancel_install")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]

    # Strip docstring + comment lines so we only grep executable code
    code_lines = []
    in_docstring = False
    for line in body.split("\n"):
        stripped = line.lstrip()
        # Crude docstring detection: triple-quote opens/closes
        if '"""' in stripped:
            # Toggle in/out of docstring (handles single-line and
            # multi-line forms)
            if stripped.count('"""') == 2:
                continue  # single-line docstring
            in_docstring = not in_docstring
            continue
        if in_docstring:
            continue
        if stripped.startswith("#"):
            continue
        code_lines.append(line)
    code = "\n".join(code_lines)

    forbidden = ["apt-get", "dpkg", "/var/lib/dpkg",
                 "killall apt", "rm /var/lib/dpkg/lock"]
    for pat in forbidden:
        assert pat not in code, (
            f"_cancel_install code manually touches apt/dpkg: "
            f"{pat!r} — apt's own teardown is sufficient"
        )


# ─── Pin: wizard cancel idempotency ─────────────────────────────────────


def test_cancel_install_safe_to_call_twice():
    """The cancel flow may fire twice: once from user clicking
    Cancel, once from `run_and_install`'s finally block. Pin
    that both calls are no-ops after the first (proc handle is
    cleared)."""
    src = WIZARD.read_text()
    fn_start = src.find("def _cancel_install")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    # Guard: only signal if proc is not None
    assert "if self._install_proc is not None:" in body


def test_run_and_install_calls_cancel_on_close_event():
    """The wizard's outer try/finally guarantees cancel runs even
    on unusual close paths (X close button, escape key). Pin
    finally clause."""
    src = WIZARD.read_text()
    fn_start = src.find("def run_and_install")
    fn_end = src.find("\n    def ", fn_start + 1)
    body = src[fn_start:fn_end]
    assert "finally:" in body
    finally_idx = body.find("finally:")
    after_finally = body[finally_idx:]
    assert "self._cancel_install()" in after_finally, (
        "run_and_install lacks finally cleanup — close events "
        "could leak install.sh subprocess"
    )


# ─── Subsequent re-run completes (idempotency of install.sh) ────────────


def test_install_sh_idempotent_for_re_run_after_cancel(tmp_path):
    """Cancel mid-install + re-run scenario: install.sh detects
    already-installed pieces (npm packages, pipx venvs, apt deb's)
    and skips them. Pin via source-grep that install.sh has 'already
    up to date' / 'already installed' style guards."""
    src = INSTALL_SH.read_text()
    # Look for idempotency markers — at least npm + apt branches
    # have 'already installed' style guards
    idempotency_keywords = ["Already up to date",
                              "already installed",
                              "Already installed",
                              "already up to date",
                              "version found"]
    found = [kw for kw in idempotency_keywords if kw in src]
    assert found, (
        "install.sh has no idempotency keywords — re-run after "
        "cancel may re-do every step (slow but not broken)"
    )


# ─── Cross-cutting: wizard syntax still parses ──────────────────────────


def test_wizard_module_imports_cleanly():
    """Sanity — the patched wizard module is importable. Catches
    a typo in the SIGTERM/timer addition that would crash on
    import."""
    result = subprocess.run(
        ["python3", "-c",
         "import bterminal.ui.installer_wizard as m; "
         "assert hasattr(m, 'InstallerWizard')"],
        capture_output=True, text=True, timeout=10,
        cwd=str(REPO_ROOT),
    )
    assert result.returncode == 0, (
        f"wizard import failed:\nstdout: {result.stdout}\n"
        f"stderr: {result.stderr}"
    )
