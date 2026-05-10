"""Failure mode: install.sh interrupted by SIGINT mid-execution
(#33 / #105, audit § 6.1 #6).

When the user hits Ctrl-C during `bash install.sh`, the script must:
  1. Catch SIGINT (and SIGTERM for completeness) — NOT just rely on
     the ERR trap (which only fires on `set -e` failures, not on
     signal-driven exits).
  2. Restore from BACKUP_DIR if one was already created
     (post-phase-2.7), emitting `BTERMINAL_INTERRUPT_ROLLBACK_OK`.
  3. Exit gracefully if no backup exists yet (early-phase
     interruption), emitting `BTERMINAL_INTERRUPT_NO_BACKUP` with
     a "run again to retry" hint.
  4. Exit code 130 (128 + SIGINT) — distinguishes user-interrupt
     from a real install failure.

Three decision branches:
  (a) SIGINT phase 1 (pre-backup): no backup yet → no rollback
      possible, user-friendly "interrupted before backup" message.
  (b) SIGINT phase 5 (mid-files): BACKUP_DIR populated, rollback
      restores prior state cleanly.
  (c) SIGINT after success: trap doesn't fire (script already
      exited 0); pin that the success path is unreachable from the
      interrupt handler.

Pre-#105 baseline: `install.sh` had only `trap '_on_error' ERR` —
SIGINT propagated as default bash behavior (immediate exit 130 with
no rollback). #105 added `trap '_on_interrupt' INT TERM` paired with
a dedicated `_on_interrupt()` handler.

Manual VM smoke (`bash install.sh & sleep 1 && kill -INT %1`) is
documented in tests/manual/README.md. Headless tests below validate:
  - source-grep for the new traps
  - bash subprocess test that fires SIGINT mid-script + checks output
  - decision branch markers reach stderr
"""
from __future__ import annotations

import os
import signal
import subprocess
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


# ─── Source-level: trap declarations ─────────────────────────────────────


def test_install_sh_has_int_trap_in_addition_to_err():
    """The pre-#105 install.sh only trapped ERR. Pin that the new
    INT/TERM trap is in place."""
    src = INSTALL_SH.read_text()
    assert "trap '_on_error' ERR" in src, (
        "ERR trap missing — pre-existing rollback path broken"
    )
    assert "trap '_on_interrupt' INT TERM" in src, (
        "INT/TERM trap missing — Ctrl-C still leaves partial state"
    )


def test_on_interrupt_handler_defined():
    """The handler function exists and has a body."""
    src = INSTALL_SH.read_text()
    assert "_on_interrupt() {" in src
    fn_start = src.find("_on_interrupt() {")
    fn_end = src.find("\n}\n", fn_start)
    body = src[fn_start:fn_end + 2]
    # Has both branches (with/without BACKUP_DIR)
    assert 'BACKUP_DIR' in body
    assert 'rm -rf "$BACKUP_DIR"' in body, (
        "_on_interrupt doesn't clean up BACKUP_DIR after restore"
    )


def test_on_interrupt_emits_distinct_marker_from_on_error():
    """ERR trap and INT trap emit different stderr markers so the
    GUI installer (or scripted observers) can distinguish error
    rollback from user-interrupt rollback."""
    src = INSTALL_SH.read_text()
    # ERR markers (pre-existing)
    assert "BTERMINAL_ROLLBACK_OK" in src  # success rollback after ERR
    assert "BTERMINAL_FRESH_INSTALL_FAILED" in src
    # INT markers (new for #105)
    assert "BTERMINAL_INTERRUPT_ROLLBACK_OK" in src
    assert "BTERMINAL_INTERRUPT_NO_BACKUP" in src


def test_on_interrupt_exits_with_130_signal_code():
    """Exit code 130 = 128 + SIGINT(2). Convention for shell scripts
    interrupted by Ctrl-C. Pin so a future refactor doesn't change
    to a generic exit 1, breaking the user-vs-error distinction."""
    src = INSTALL_SH.read_text()
    fn_start = src.find("_on_interrupt() {")
    fn_end = src.find("\n}\n", fn_start)
    body = src[fn_start:fn_end + 2]
    assert "exit 130" in body, (
        f"_on_interrupt should exit 130 (128+SIGINT); body: {body[:300]!r}"
    )


def test_on_interrupt_restores_backup_files_when_present():
    """The restore branch loops over BTERMINAL_FILES and copies each
    one back. Same logic as _on_error — pin parity so a refactor
    that touches one path doesn't drift from the other."""
    src = INSTALL_SH.read_text()
    fn_start = src.find("_on_interrupt() {")
    fn_end = src.find("\n}\n", fn_start)
    body = src[fn_start:fn_end + 2]
    assert "for f in" in body
    assert 'BTERMINAL_FILES' in body
    assert 'cp -f "$BACKUP_DIR/$f"' in body


def test_on_interrupt_provides_resume_hint_when_no_backup():
    """The 'no backup' branch tells the user 'run again to retry'.
    Pin so the message stays — without it, users see only an exit
    code and no guidance."""
    src = INSTALL_SH.read_text()
    fn_start = src.find("_on_interrupt() {")
    fn_end = src.find("\n}\n", fn_start)
    body = src[fn_start:fn_end + 2]
    # "run again" or "retry" — either phrasing is OK, just need
    # SOME guidance string
    body_lower = body.lower()
    assert "run" in body_lower and "again" in body_lower, (
        f"_on_interrupt no-backup branch missing user guidance: {body!r}"
    )


def test_install_sh_bash_syntax_still_valid():
    """`bash -n` parses the patched script — guards against a typo
    in the trap declaration that would silently break the script
    on every run."""
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"install.sh syntax broken after #105 patch:\n{result.stderr}"
    )


# ─── (a) SIGINT phase 1 (pre-backup): no rollback, friendly exit ─────────


def _spawn_install_with_delay(tmp_path: Path, delay_seconds: float):
    """Helper: copy install.sh to tmp_path with a `sleep N` injected
    near the top, then spawn it as subprocess. Returns the Popen so
    the caller can SIGINT it. Output captured into tmp_path/install.log.

    The injected sleep gives the test deterministic timing — without
    it, install.sh races through and exits before SIGINT lands."""
    patched = tmp_path / "install_test.sh"
    src = INSTALL_SH.read_text()
    # Inject sleep right after the trap declarations so SIGINT
    # arrives while traps are armed but before any real work
    sentinel = "trap '_on_interrupt' INT TERM"
    assert sentinel in src
    patched_src = src.replace(
        sentinel,
        f"{sentinel}\n# TEST_HOOK: sleep so SIGINT lands here, then\n"
        f"# exit cleanly so we never reach the rest of install.sh\n"
        f"# (which would fail on missing apt deps in the test env).\n"
        f"sleep {delay_seconds}\nexit 0\n",
    )
    patched.write_text(patched_src)
    patched.chmod(0o755)

    log_path = tmp_path / "install.log"
    log_handle = open(log_path, "w")
    # --headless short-circuits maybe_launch_gtk_wizard at line 114
    # of install.sh — without it, the function tries to invoke
    # `python3 -m bterminal --installer` against a non-repo cwd,
    # printing irrelevant errors that mask our SIGINT signal.
    # Empty DISPLAY/WAYLAND_DISPLAY further guarantees the wizard
    # branch is skipped.
    env = {k: v for k, v in os.environ.items()
           if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env["HOME"] = str(tmp_path)
    env["INSTALL_DIR"] = str(tmp_path / "fake-install")
    proc = subprocess.Popen(
        ["bash", str(patched), "--headless"],
        cwd=str(tmp_path),
        stdout=log_handle, stderr=subprocess.STDOUT,
        env=env,
        start_new_session=True,
    )
    return proc, log_path, log_handle


def test_sigint_before_backup_emits_no_backup_marker(tmp_path):
    """(a) SIGINT phase 1: BACKUP_DIR is empty string at script
    start. Sending SIGINT before the backup phase runs → handler
    falls through the no-backup branch → emits
    BTERMINAL_INTERRUPT_NO_BACKUP."""
    proc, log_path, log_handle = _spawn_install_with_delay(
        tmp_path, delay_seconds=5.0)
    try:
        time.sleep(0.5)  # let bash arm traps + enter sleep
        proc.send_signal(signal.SIGINT)
        rc = proc.wait(timeout=10)
    finally:
        log_handle.close()

    log = log_path.read_text()
    assert "BTERMINAL_INTERRUPT_NO_BACKUP" in log, (
        f"no-backup marker missing from output:\n{log[-1000:]}"
    )
    # Exit 130 = 128 + SIGINT(2)
    assert rc == 130, f"expected exit 130, got {rc}"


def test_sigint_before_backup_does_not_emit_rollback_ok_marker(tmp_path):
    """Negative pin: ROLLBACK_OK marker is reserved for the
    post-backup branch. Pre-backup interrupt → only NO_BACKUP
    fires."""
    proc, log_path, log_handle = _spawn_install_with_delay(
        tmp_path, delay_seconds=5.0)
    try:
        time.sleep(0.5)
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=10)
    finally:
        log_handle.close()

    log = log_path.read_text()
    assert "BTERMINAL_INTERRUPT_ROLLBACK_OK" not in log, (
        "ROLLBACK_OK fired without a backup — handler logic broken"
    )


def test_sigint_before_backup_includes_run_again_hint(tmp_path):
    """User-facing message tells them what to do next."""
    proc, log_path, log_handle = _spawn_install_with_delay(
        tmp_path, delay_seconds=5.0)
    try:
        time.sleep(0.5)
        proc.send_signal(signal.SIGINT)
        proc.wait(timeout=10)
    finally:
        log_handle.close()

    log = log_path.read_text().lower()
    assert ("run" in log and "again" in log) or "retry" in log, (
        f"no resume guidance in interrupt output: {log[-500:]!r}"
    )


# ─── (b) SIGINT phase 5 (mid-files, BACKUP_DIR populated): rollback ──────


def test_sigint_with_populated_backup_dir_triggers_rollback(tmp_path):
    """(b) SIGINT after BACKUP_DIR populated: handler restores files
    + emits BTERMINAL_INTERRUPT_ROLLBACK_OK.

    Test simulates 'mid-files' state by setting BACKUP_DIR to a real
    populated dir BEFORE the script enters the test sleep. Without
    monkey-patching the install.sh source we'd need to actually run
    it through phase 2.7 — too fragile for a headless test. So we
    inject a script that exports BACKUP_DIR + creates the dir and
    THEN waits for SIGINT, then sources the handler."""
    # Build a minimal script that mimics the 'after backup, before
    # real work' state
    backup_dir = tmp_path / "fake-backup"
    backup_dir.mkdir()
    # Pre-populate with one BTERMINAL_FILES entry to verify restore
    (backup_dir / "ctx").write_text("#!/bin/bash\n# original ctx\n")

    # Reuse the real _on_interrupt by sourcing the relevant section
    # of install.sh into a stub. Extract just the helper definition +
    # trap line so we don't need to run the whole installer.
    src = INSTALL_SH.read_text()
    on_interrupt_start = src.find("# #105")
    trap_end = src.find("trap '_on_interrupt' INT TERM\n",
                          on_interrupt_start)
    snippet = src[on_interrupt_start:trap_end + len(
        "trap '_on_interrupt' INT TERM\n")]
    assert "_on_interrupt()" in snippet

    install_dir = tmp_path / "install"
    install_dir.mkdir()
    # Pre-existing file that gets restored from backup
    (install_dir / "ctx").write_text("#!/bin/bash\n# CURRENT ctx\n")

    test_script = tmp_path / "interrupt_test.sh"
    test_script.write_text(
        "#!/bin/bash\n"
        "set -uo pipefail\n"
        f'BACKUP_DIR="{backup_dir}"\n'
        f'INSTALL_DIR="{install_dir}"\n'
        'BTERMINAL_FILES=(ctx consult tasks claude_log memory_wizard)\n'
        f"{snippet}\n"
        "sleep 5\n"
    )
    test_script.chmod(0o755)

    log_path = tmp_path / "test.log"
    log_handle = open(log_path, "w")
    proc = subprocess.Popen(
        ["bash", str(test_script)],
        stdout=log_handle, stderr=subprocess.STDOUT,
        start_new_session=True,
    )
    try:
        time.sleep(0.5)
        proc.send_signal(signal.SIGINT)
        rc = proc.wait(timeout=10)
    finally:
        log_handle.close()

    log = log_path.read_text()
    # Rollback marker fired
    assert "BTERMINAL_INTERRUPT_ROLLBACK_OK" in log, (
        f"rollback marker missing from output:\n{log[-1000:]}"
    )
    # Restored from backup — content matches backup, not the
    # 'current' pre-test state
    restored = (install_dir / "ctx").read_text()
    assert "original ctx" in restored, (
        f"ctx not restored from backup: {restored!r}"
    )
    # BACKUP_DIR cleaned up after restore
    assert not backup_dir.exists()
    # Exit 130
    assert rc == 130


# ─── (c) SIGINT after success: handler doesn't fire ──────────────────────


def test_install_sh_clears_backup_after_success_path():
    """Pin: the script clears BACKUP_DIR at the end of a successful
    run (line 803-ish). After that line, an INT signal would still
    fire _on_interrupt but it would hit the no-backup branch
    (because BACKUP_DIR was already rm'd)."""
    src = INSTALL_SH.read_text()
    # The cleanup line near the end of install.sh
    assert 'rm -rf "$BACKUP_DIR"' in src
    # Sanity — there's a final cleanup independent of the traps
    final_cleanup = src.find('[[ -n "$BACKUP_DIR" && -d "$BACKUP_DIR" ]] '
                              '&& rm -rf "$BACKUP_DIR"')
    assert final_cleanup > 0, (
        "install.sh missing final BACKUP_DIR cleanup — successful "
        "runs leave /tmp/bterminal-backup-* lying around"
    )


def test_after_clean_exit_no_interrupt_marker():
    """After install.sh exits 0, there's no `BTERMINAL_INTERRUPT_*`
    in stdout/stderr. SIGINT after script exit goes to the parent
    shell, not the dead install.sh process."""
    # Source-grep negative — no pre-exit emission of interrupt markers
    src = INSTALL_SH.read_text()
    # Both interrupt markers ONLY emitted from inside _on_interrupt
    fn_start = src.find("_on_interrupt() {")
    fn_end = src.find("\n}\n", fn_start)
    interrupt_body = src[fn_start:fn_end]
    # Both markers appear within the interrupt handler body
    assert src.count("BTERMINAL_INTERRUPT_ROLLBACK_OK") == 1
    assert src.count("BTERMINAL_INTERRUPT_NO_BACKUP") == 1
    assert "BTERMINAL_INTERRUPT_ROLLBACK_OK" in interrupt_body
    assert "BTERMINAL_INTERRUPT_NO_BACKUP" in interrupt_body


# ─── Cross-cutting: TERM signal path (not just INT) ──────────────────────


def test_sigterm_before_backup_also_triggers_handler(tmp_path):
    """SIGTERM (kill -TERM, used by systemd / gnome-terminal close)
    must hit the same handler as SIGINT. Pin parity so a daemon-
    managed install (rare but possible) gets the same rollback."""
    proc, log_path, log_handle = _spawn_install_with_delay(
        tmp_path, delay_seconds=5.0)
    try:
        time.sleep(0.5)
        proc.send_signal(signal.SIGTERM)
        rc = proc.wait(timeout=10)
    finally:
        log_handle.close()

    log = log_path.read_text()
    assert "BTERMINAL_INTERRUPT_NO_BACKUP" in log, (
        f"SIGTERM didn't trigger interrupt handler:\n{log[-500:]}"
    )
    # SIGTERM also exits 130 in our handler (uniform)
    assert rc == 130


def test_sigkill_does_not_trigger_handler(tmp_path):
    """Negative parity: SIGKILL is uncatchable — handler doesn't
    fire. The script dies abruptly with no rollback. This is bash
    semantics, not a bug; pin so users understand the limit."""
    proc, log_path, log_handle = _spawn_install_with_delay(
        tmp_path, delay_seconds=5.0)
    try:
        time.sleep(0.5)
        proc.send_signal(signal.SIGKILL)
        rc = proc.wait(timeout=10)
    finally:
        log_handle.close()

    log = log_path.read_text()
    # No interrupt marker — handler couldn't fire
    assert "BTERMINAL_INTERRUPT_ROLLBACK_OK" not in log
    assert "BTERMINAL_INTERRUPT_NO_BACKUP" not in log
    # Exit by signal — bash reports SIGKILL as -9 or 137 depending
    # on how Popen reads the status
    assert rc in (-9, 137, 9), f"unexpected SIGKILL exit: {rc}"


# ─── ERR trap still works (no regression from #105 patch) ────────────────


def test_err_trap_still_fires_on_set_e_failure(tmp_path):
    """Regression guard: adding INT/TERM trap shouldn't break the
    pre-existing ERR trap. Spawn a stub install.sh that triggers
    `false` (which set -e converts to ERR) and verify
    BTERMINAL_FRESH_INSTALL_FAILED still emits."""
    src = INSTALL_SH.read_text()
    # Build minimal repro — extract _on_error + traps + a synthetic
    # `false` to trigger ERR
    on_error_start = src.find("_on_error() {")
    err_trap_end = src.find("trap '_on_interrupt' INT TERM\n",
                             on_error_start)
    snippet = src[on_error_start:err_trap_end + len(
        "trap '_on_interrupt' INT TERM\n")]

    test_script = tmp_path / "err_test.sh"
    test_script.write_text(
        "#!/bin/bash\n"
        "set -e\n"
        'BACKUP_DIR=""\n'
        'INSTALL_DIR=""\n'
        'BTERMINAL_FILES=(ctx)\n'
        f"{snippet}\n"
        "false\n"  # trigger ERR
    )
    test_script.chmod(0o755)

    result = subprocess.run(
        ["bash", str(test_script)],
        capture_output=True, text=True, timeout=10,
    )
    # ERR fires, no backup → FRESH_INSTALL_FAILED marker
    assert "BTERMINAL_FRESH_INSTALL_FAILED" in result.stderr, (
        f"ERR trap regression — marker missing from stderr:\n"
        f"{result.stderr}"
    )
    # Exit non-zero (specifically 1 from `false`)
    assert result.returncode != 0
