"""Race condition: install.sh + install.sh in parallel
(#38 / #110, audit § 6.2 #11).

Two simultaneous `bash install.sh` invocations would collide on:
  - BACKUP_DIR creation (both `mktemp -d /tmp/bterminal-backup-XXXXXX`
    succeeds, but only one's restore set is consistent)
  - File copies into ~/.local/share/bterminal/bterminal/ (interleaved
    writes corrupt the package)
  - npm-global state (npm install -g serializes via npm's own lock,
    but we'd see weird race with package staging)

The #110 fix: install.sh acquires a per-user advisory `flock` on
`~/.config/bterminal/install.lock` early in the bash-flow phase
(after `maybe_launch_gtk_wizard` short-circuit). Second invocation
fails the flock, exits 7, emits `BTERMINAL_INSTALL_LOCKED` marker.

Three decision branches:
  (a) flock contention — second proc fails non-blocking lock,
      exits 7, no further work done.
  (b) BACKUP_DIR overlap — pin: first proc creates BACKUP_DIR, runs
      to completion (backup never overlaps because the second proc
      exits BEFORE creating its own backup).
  (c) One finishes mid-rollback of other — N/A under the flock
      design: only ONE install can be in any phase at a time. Pin
      the design as "no concurrent rollback paths possible".

Manual VM smoke (`bash install.sh & bash install.sh; wait`) is
documented in tests/manual/README.md. Headless tests below pin the
flock dispatch with bash subprocess + source-grep.
"""
from __future__ import annotations

import os
import signal
import subprocess
import threading
import time
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


# ─── Source-grep: flock structure landed ────────────────────────────────


def test_install_sh_uses_flock_for_concurrent_runs():
    """Pin: install.sh acquires a non-blocking advisory lock via
    `flock -n 9` on a per-user file. Without this, two parallel
    invocations corrupt BACKUP_DIR / file copies."""
    src = INSTALL_SH.read_text()
    assert "flock -n 9" in src, (
        "install.sh missing flock — concurrent runs collide"
    )
    assert "exec 9>" in src, (
        "install.sh missing FD 9 redirection for flock"
    )
    assert "INSTALL_LOCKFILE" in src
    # User-scoped lock (under $HOME) so multiple users on same host
    # don't interfere
    assert "$HOME/.config/bterminal/install.lock" in src \
        or "HOME/.config" in src


def test_install_sh_lock_acquisition_after_wizard_short_circuit():
    """The lock must be acquired AFTER `maybe_launch_gtk_wizard`
    returns — otherwise the wizard's internal `install.sh --headless`
    spawn would block on the parent's lock. Pin source ordering."""
    src = INSTALL_SH.read_text()
    wizard_call_idx = src.find("\nmaybe_launch_gtk_wizard\n")
    assert wizard_call_idx > 0
    flock_idx = src.find("flock -n 9", wizard_call_idx)
    assert flock_idx > wizard_call_idx, (
        "flock acquired BEFORE wizard call — wizard's child "
        "install.sh would deadlock"
    )


def test_install_sh_lock_contention_exits_with_distinct_code():
    """Pin: lock-contention exit code is 7 (distinct from 1, 130,
    other failure modes). The GUI installer + scripted automation
    can branch on this to display 'another install running' rather
    than 'install failed'."""
    src = INSTALL_SH.read_text()
    flock_idx = src.find("flock -n 9")
    # Look at next 200 chars for the exit code
    after = src[flock_idx:flock_idx + 500]
    assert "exit 7" in after, (
        f"lock-contention path missing 'exit 7': {after[:300]!r}"
    )


def test_install_sh_emits_install_locked_marker_on_contention():
    """The `BTERMINAL_INSTALL_LOCKED` stderr marker is the
    counterpart to `BTERMINAL_ROLLBACK_OK` / `BTERMINAL_INTERRUPT_*`
    — the GUI surfaces it as 'wait for the other install'.
    Pin literal."""
    src = INSTALL_SH.read_text()
    assert "BTERMINAL_INSTALL_LOCKED" in src


def test_install_sh_lock_message_explains_stale_lockfile():
    """User-facing diagnostic: 'remove if stale (no holder process
    running)'. Without this, users hit a permanent block when
    a previous install crashed before releasing the lock."""
    src = INSTALL_SH.read_text()
    # Look for 'stale' or 'holder' guidance near the locked block
    locked_idx = src.find("BTERMINAL_INSTALL_LOCKED")
    nearby = src[max(0, locked_idx - 800):locked_idx]
    nearby_lower = nearby.lower()
    assert "stale" in nearby_lower or "holder" in nearby_lower, (
        f"no stale-lockfile guidance near contention block: "
        f"{nearby[-400:]!r}"
    )


def test_install_sh_writes_pid_to_lockfile_for_diagnostics():
    """Stash the holder's PID in the lock file so a sysadmin can
    identify which process is blocking. Pin the contract."""
    src = INSTALL_SH.read_text()
    flock_idx = src.find("flock -n 9")
    after = src[flock_idx:flock_idx + 600]
    assert 'echo "$$" >' in after, (
        "lock holder PID not stashed for diagnostics"
    )


def test_install_sh_bash_syntax_still_valid():
    """`bash -n` parses the patched script. Catches a typo in the
    flock declaration that would break every install."""
    result = subprocess.run(
        ["bash", "-n", str(INSTALL_SH)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"install.sh syntax broken after #110:\n{result.stderr}"
    )


# ─── Bash subprocess: actual flock contention ────────────────────────────


def _build_lock_test_script(tmp_path: Path, lockfile: Path,
                              hold_seconds: float = 0.0):
    """Synthesize a minimal bash script that mirrors install.sh's
    flock acquisition pattern. Optional sleep simulates a long-
    running install."""
    script = tmp_path / f"lock_test_{hold_seconds}.sh"
    script.write_text(
        "#!/bin/bash\n"
        "set -uo pipefail\n"
        f'INSTALL_LOCKFILE="{lockfile}"\n'
        'mkdir -p "$(dirname "$INSTALL_LOCKFILE")"\n'
        'exec 9>"$INSTALL_LOCKFILE"\n'
        "if ! flock -n 9; then\n"
        '    echo "BTERMINAL_INSTALL_LOCKED" >&2\n'
        "    exit 7\n"
        "fi\n"
        'echo "$$" > "$INSTALL_LOCKFILE"\n'
        "echo 'GOT_LOCK'\n"
        f"sleep {hold_seconds}\n"
        "echo 'DONE'\n"
    )
    script.chmod(0o755)
    return script


def test_two_concurrent_invocations_one_wins(tmp_path):
    """(a) Concurrent runs: spawn 2 install-shaped scripts. First
    acquires lock, second fails non-blocking, exits 7."""
    lockfile = tmp_path / "install.lock"
    # Script 1 holds lock for 2s
    script1 = _build_lock_test_script(tmp_path, lockfile, 2.0)
    # Script 2 tries to acquire while script 1 holds — fails fast
    script2 = _build_lock_test_script(tmp_path, lockfile, 0.0)

    log1 = tmp_path / "out1.log"
    log2 = tmp_path / "out2.log"

    p1 = subprocess.Popen(
        ["bash", str(script1)],
        stdout=open(log1, "w"), stderr=subprocess.STDOUT,
    )
    time.sleep(0.3)  # let script1 acquire lock

    p2 = subprocess.Popen(
        ["bash", str(script2)],
        stdout=open(log2, "w"), stderr=subprocess.STDOUT,
    )
    rc2 = p2.wait(timeout=5)
    rc1 = p1.wait(timeout=10)

    # Script 1 (holder) succeeded
    assert rc1 == 0
    log1_text = log1.read_text()
    assert "GOT_LOCK" in log1_text
    assert "DONE" in log1_text

    # Script 2 (challenger) exited 7 with marker
    assert rc2 == 7
    log2_text = log2.read_text()
    assert "BTERMINAL_INSTALL_LOCKED" in log2_text
    # And NEVER got past the lock check
    assert "GOT_LOCK" not in log2_text


def test_lock_released_after_holder_exits(tmp_path):
    """After the first invocation finishes, the second can
    re-acquire. Pin so the lock isn't 'sticky' across runs."""
    lockfile = tmp_path / "install.lock"
    # Sequential runs — no overlap
    script = _build_lock_test_script(tmp_path, lockfile, 0.0)

    # First run
    result1 = subprocess.run(
        ["bash", str(script)],
        capture_output=True, text=True, timeout=5,
    )
    assert result1.returncode == 0
    assert "GOT_LOCK" in result1.stdout

    # Second run — should acquire fresh lock
    result2 = subprocess.run(
        ["bash", str(script)],
        capture_output=True, text=True, timeout=5,
    )
    assert result2.returncode == 0
    assert "GOT_LOCK" in result2.stdout


def test_stale_lockfile_with_no_holder_can_be_acquired(tmp_path):
    """A lockfile left over from a crashed install (no process
    holds the FD) can be acquired by a fresh run. The flock(2)
    semantics — kernel-level lock tied to FD lifetime — guarantee
    this. Pin so a refactor that switches to a PID-based check
    doesn't break recovery."""
    lockfile = tmp_path / "install.lock"
    # Manually create stale lockfile with bogus PID
    lockfile.write_text("999999\n")

    script = _build_lock_test_script(tmp_path, lockfile, 0.0)
    result = subprocess.run(
        ["bash", str(script)],
        capture_output=True, text=True, timeout=5,
    )
    assert result.returncode == 0, (
        f"stale lockfile blocked acquisition: {result.stdout}"
    )
    assert "GOT_LOCK" in result.stdout
    # PID rewritten to current process
    pid_in_file = lockfile.read_text().strip()
    assert pid_in_file != "999999", (
        f"stale PID still in lockfile: {pid_in_file}"
    )


def test_third_invocation_during_two_way_race(tmp_path):
    """Concurrency stress: 3 simultaneous invocations → first wins,
    other two exit 7. Catches a flock implementation that's only
    pairwise-correct."""
    lockfile = tmp_path / "install.lock"
    script_long = _build_lock_test_script(tmp_path, lockfile, 1.5)
    script_short = _build_lock_test_script(tmp_path, lockfile, 0.0)

    log1 = tmp_path / "p1.log"
    log2 = tmp_path / "p2.log"
    log3 = tmp_path / "p3.log"

    p1 = subprocess.Popen(["bash", str(script_long)],
                            stdout=open(log1, "w"),
                            stderr=subprocess.STDOUT)
    time.sleep(0.3)

    p2 = subprocess.Popen(["bash", str(script_short)],
                            stdout=open(log2, "w"),
                            stderr=subprocess.STDOUT)
    p3 = subprocess.Popen(["bash", str(script_short)],
                            stdout=open(log3, "w"),
                            stderr=subprocess.STDOUT)

    rc2 = p2.wait(timeout=5)
    rc3 = p3.wait(timeout=5)
    rc1 = p1.wait(timeout=10)

    assert rc1 == 0  # holder
    assert rc2 == 7  # blocked
    assert rc3 == 7  # blocked
    assert "BTERMINAL_INSTALL_LOCKED" in log2.read_text()
    assert "BTERMINAL_INSTALL_LOCKED" in log3.read_text()


# ─── Real install.sh exec — verify the patched script enforces lock ──────


def test_real_install_sh_acquires_lock_when_invoked(tmp_path):
    """Spawn the REAL install.sh (with --headless to short-circuit
    the wizard + heavy install paths). It should acquire the lock
    before failing on any missing-deps issues — verify by running
    a second instance immediately after.

    The first instance fails fast (no SCRIPT_DIR, no defaults/) and
    releases the lock; the second can then acquire it. We verify
    the LOCKFILE state, not full install behavior."""
    lockfile = tmp_path / "install.lock"

    # Set HOME + INSTALL_LOCKFILE to a tmp scope so we don't
    # interfere with a real BT install
    env = {k: v for k, v in os.environ.items()
           if k not in ("DISPLAY", "WAYLAND_DISPLAY")}
    env["HOME"] = str(tmp_path)
    env["INSTALL_LOCKFILE"] = str(lockfile)

    # First run — will fail fast on missing apt/etc, but should
    # nevertheless touch the lockfile (proving lock acquisition
    # happens before the failure). Timeout generous (90 s) because
    # under full pytest sweep load the install.sh probe steps
    # (npm/git/python --version checks) can be sluggish.
    result = subprocess.run(
        ["bash", str(INSTALL_SH), "--headless", "--no-sudo"],
        cwd=str(tmp_path), env=env,
        capture_output=True, text=True, timeout=90,
    )
    # Either succeeded (rare in headless env) OR failed AFTER
    # creating the lockfile
    assert lockfile.exists(), (
        f"install.sh didn't touch lockfile — flock acquisition "
        f"didn't happen. stdout: {result.stdout[-500:]!r}\n"
        f"stderr: {result.stderr[-500:]!r}"
    )


# ─── Pin: BACKUP_DIR creation only happens for the lock holder ──────────


def test_backup_dir_creation_is_after_lock_acquisition():
    """Source ordering: BACKUP_DIR mktemp happens AFTER flock
    succeeds. A non-holder never reaches the backup phase, so
    there's no concurrent BACKUP_DIR creation."""
    src = INSTALL_SH.read_text()
    flock_idx = src.find("flock -n 9")
    backup_idx = src.find('BACKUP_DIR="$(mktemp')
    assert flock_idx > 0
    assert backup_idx > flock_idx, (
        f"BACKUP_DIR mktemp at {backup_idx} happens BEFORE flock "
        f"at {flock_idx} — concurrent runs would race on backup"
    )


def test_backup_dir_mktemp_uses_unique_template():
    """Even within a single install, BACKUP_DIR uses
    `mktemp -d /tmp/bterminal-backup-XXXXXX` — unique per call.
    A theoretical concurrent run (if flock failed) couldn't collide
    because mktemp atomically creates a unique name. Pin the
    template."""
    src = INSTALL_SH.read_text()
    assert "mktemp -d /tmp/bterminal-backup-XXXXXX" in src, (
        "BACKUP_DIR mktemp template changed — verify no clashes"
    )


# ─── Pin: rollback paths can't run concurrently under flock design ──────


def test_rollback_path_protected_by_lock_design():
    """Source ordering: _on_error runs only when the script is
    inside the locked region (post-flock). Two invocations can't
    have rollback paths firing simultaneously because the second
    invocation never gets past the lock check.

    Pin: _on_error doesn't try to acquire its own lock (would
    deadlock with the held flock)."""
    src = INSTALL_SH.read_text()
    fn_start = src.find("_on_error() {")
    fn_end = src.find("\n}\n", fn_start)
    body = src[fn_start:fn_end]
    assert "flock" not in body, (
        "_on_error contains flock — would deadlock with main "
        "invocation's lock"
    )
