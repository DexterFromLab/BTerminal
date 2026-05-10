"""Pin tests for #112 — _on_interrupt rmdir's empty $INSTALL_DIR
when SIGTERM fires before backup creation.

Phase [1/7] of install.sh runs `mkdir -p "$INSTALL_DIR"` up-front,
which means an interrupt anywhere between [1/7] and [5/7] (file copy)
leaves a useless empty `~/.local/share/bterminal/` directory on disk
even when the no-backup branch fires (BTERMINAL_INTERRUPT_NO_BACKUP).

Acceptance: after the trap fires with BACKUP_DIR empty, `ls $INSTALL_DIR`
should return "absent" — not "empty".

The cleanup uses `rmdir` (not `rm -rf`) so a real partial install with
some files already copied is never destroyed: rmdir refuses non-empty.
"""
import os
import re
import subprocess
import textwrap
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


# ── Static structural guards ────────────────────────────────────────────


def _slice_function(src: str, header: str) -> str:
    idx = src.find(header)
    assert idx >= 0, f"function header not found: {header!r}"
    end = src.find("\n}\n", idx)
    assert end > idx
    return src[idx:end]


def test_install_sh_present():
    assert INSTALL_SH.is_file()


def test_on_interrupt_calls_rmdir_in_no_backup_branch():
    """Pin: the no-backup branch of _on_interrupt() must rmdir
    $INSTALL_DIR (only when empty)."""
    src = INSTALL_SH.read_text()
    body = _slice_function(src, "_on_interrupt() {")
    # Must reference the marker that signals no-backup branch entry
    assert "BTERMINAL_INTERRUPT_NO_BACKUP" in body
    # Must call rmdir on $INSTALL_DIR — non-recursive so user data safe
    assert re.search(r'rmdir\s+"\$INSTALL_DIR"', body), (
        "expected `rmdir \"$INSTALL_DIR\"` in _on_interrupt"
    )
    # Must NOT use rm -rf on INSTALL_DIR in the interrupt path —
    # that would destroy partial-install user files.
    assert not re.search(r'rm\s+-rf\s+"\$INSTALL_DIR"', body), (
        "_on_interrupt must use rmdir (empty-only), not rm -rf"
    )


def test_on_interrupt_walks_empty_subdirs_first():
    """Pin: rmdir of $INSTALL_DIR fails if any empty subdir lingers
    (extensions/, locale/...). Pre-walk with `find -type d -empty -delete`
    so the parent rmdir succeeds when the leaf is empty too."""
    src = INSTALL_SH.read_text()
    body = _slice_function(src, "_on_interrupt() {")
    assert re.search(
        r'find\s+"\$INSTALL_DIR"\s+-mindepth\s+1\s+-type\s+d\s+-empty\s+-delete',
        body,
    ), "expected `find $INSTALL_DIR -mindepth 1 -type d -empty -delete`"


def test_on_interrupt_emits_cleanup_marker():
    """Pin: the GUI wizard parses BTERMINAL_INTERRUPT_* markers from
    stderr to render a status. Add a CLEANED_INSTALL_DIR marker so
    the wizard can confirm cleanup succeeded."""
    src = INSTALL_SH.read_text()
    body = _slice_function(src, "_on_interrupt() {")
    assert "BTERMINAL_INTERRUPT_CLEANED_INSTALL_DIR" in body


def test_no_backup_branch_guards_install_dir_var():
    """Pin: rmdir must be guarded by `[[ -n "$INSTALL_DIR" ]]` — without
    it, an unset INSTALL_DIR would attempt `rmdir ""` (harmless) but the
    pre-`find` walk would expand to `find  -mindepth 1 ...` which lists
    EVERYTHING under cwd. Defensive guard required."""
    src = INSTALL_SH.read_text()
    body = _slice_function(src, "_on_interrupt() {")
    # The cleanup block needs an [[ -n "$INSTALL_DIR" ... ]] guard
    assert re.search(
        r'\[\[\s+-n\s+"\$INSTALL_DIR"\s+&&\s+-d\s+"\$INSTALL_DIR"\s+\]\]',
        body,
    ), "expected combined -n + -d guard on INSTALL_DIR"


# ── Behavioural test: extract + execute _on_interrupt in isolation ─────


_HARNESS = textwrap.dedent(
    """\
    #!/bin/bash
    set +e
    # Stubs for log_line / status_json / printf-coloring helpers used
    # inside _on_interrupt. We only care about the rmdir side effect.
    log_line() { :; }
    status_json() { :; }

    # Inject overrides BEFORE sourcing install.sh's function definitions
    INSTALL_DIR="{install_dir}"
    BACKUP_DIR=""
    BTERMINAL_FILES=()

    # Extract just the _on_interrupt body from install.sh and define it
    # in this shell. We can't `source install.sh` because it would run
    # the install logic; we only want the trap function.
    eval "$(awk '/^_on_interrupt\\(\\) \\{$/,/^\\}$/' "{install_sh}")"

    # Disable the exit so we can inspect post-state.
    _on_interrupt() {{
        $(declare -f _on_interrupt | sed -n '/{{$/,/^}}$/p' \\
            | sed '1d;$d;s/^    //' | sed 's/exit 130/return 0/')
    }}
    # The above is a no-op fallback; rely on the eval above instead.
    """
)


def _build_extracted_trap_runner(tmp_path: Path) -> Path:
    """Write a bash script that extracts _on_interrupt and calls it
    with INSTALL_DIR pointed at a tmp dir, BACKUP_DIR empty.
    Returns the path to the script."""
    script = tmp_path / "trap_runner.sh"
    script.write_text(textwrap.dedent(f"""\
        #!/bin/bash
        # Stubs (functions invoked inside _on_interrupt that aren't
        # defined when we extract just the one function).
        log_line() {{ :; }}
        status_json() {{ :; }}

        # Override the targets the trap operates on.
        INSTALL_DIR="$1"
        BACKUP_DIR=""
        BTERMINAL_FILES=()

        # Extract _on_interrupt body from install.sh — between the
        # `_on_interrupt() {{` line and the next standalone `}}` line.
        eval "$(awk '/^_on_interrupt\\(\\) \\{{$/,/^\\}}$/' '{INSTALL_SH}')"

        # Replace `exit 130` with `return 130` so the script can
        # continue and we can inspect the result.
        eval "$(declare -f _on_interrupt | sed 's/exit 130/return 130/')"

        _on_interrupt
        echo "exit_rc=$?"
    """))
    script.chmod(0o755)
    return script


def test_behavioural_rmdir_empty_install_dir(tmp_path):
    """End-to-end: simulate the no-backup interrupt path with an
    empty INSTALL_DIR. After the trap, the dir must be GONE
    (not just empty)."""
    install_dir = tmp_path / "fake_local_share_bterminal"
    install_dir.mkdir()
    assert install_dir.exists()

    runner = _build_extracted_trap_runner(tmp_path)
    proc = subprocess.run(
        ["bash", str(runner), str(install_dir)],
        capture_output=True, text=True, timeout=10,
    )
    # rmdir succeeded → dir gone
    assert not install_dir.exists(), (
        f"INSTALL_DIR still exists after _on_interrupt; "
        f"stdout={proc.stdout!r} stderr={proc.stderr!r}"
    )
    # Marker emitted on stderr
    assert "BTERMINAL_INTERRUPT_CLEANED_INSTALL_DIR" in proc.stderr
    assert "BTERMINAL_INTERRUPT_NO_BACKUP" in proc.stderr


def test_behavioural_rmdir_skips_non_empty(tmp_path):
    """Safety: if INSTALL_DIR contains files (real partial install),
    rmdir must FAIL silently and leave them in place. We must NEVER
    rm -rf the user's partial state."""
    install_dir = tmp_path / "fake_partial_install"
    install_dir.mkdir()
    canary = install_dir / "user_data.txt"
    canary.write_text("important")

    runner = _build_extracted_trap_runner(tmp_path)
    proc = subprocess.run(
        ["bash", str(runner), str(install_dir)],
        capture_output=True, text=True, timeout=10,
    )
    # Dir + canary still present
    assert install_dir.exists()
    assert canary.exists() and canary.read_text() == "important"
    # CLEANED marker MUST NOT appear when rmdir failed
    assert "BTERMINAL_INTERRUPT_CLEANED_INSTALL_DIR" not in proc.stderr
    # NO_BACKUP marker still emitted (the trap path was taken)
    assert "BTERMINAL_INTERRUPT_NO_BACKUP" in proc.stderr


def test_behavioural_rmdir_handles_empty_subdirs(tmp_path):
    """Phase [5/7] step 2 mkdir's $INSTALL_DIR/extensions/. Interrupt
    after that but before any file copy → INSTALL_DIR has one empty
    subdir, parent dir is non-empty (because of the subdir). The
    `find -type d -empty -delete` pre-walk must clear the leaf so
    the parent rmdir then succeeds."""
    install_dir = tmp_path / "fake_with_empty_subdir"
    install_dir.mkdir()
    (install_dir / "extensions").mkdir()
    (install_dir / "locale").mkdir()
    # Nested empty subdir to verify recursive walk
    (install_dir / "locale" / "en_US").mkdir()

    runner = _build_extracted_trap_runner(tmp_path)
    proc = subprocess.run(
        ["bash", str(runner), str(install_dir)],
        capture_output=True, text=True, timeout=10,
    )
    assert not install_dir.exists(), (
        f"INSTALL_DIR with empty subdirs should be cleaned. "
        f"stderr={proc.stderr!r}"
    )
    assert "BTERMINAL_INTERRUPT_CLEANED_INSTALL_DIR" in proc.stderr
