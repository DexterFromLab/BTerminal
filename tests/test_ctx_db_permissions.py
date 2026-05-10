"""Security: CTX DB file mode (chmod 600 for sensitive project info)
(#57 / #129, audit § 6.7 #30).

CTX DB at `~/.claude-context/context.db` stores:
  - Project rules (free-form prose users write to instruct AI)
  - Project contexts (shared keys/values)
  - Task IDs and descriptions (potentially sensitive, e.g.
    'fix CVE-2026-XXXX before release')
  - Session metadata (working directories)

On a multi-user box (rare for personal devs but common on
shared VMs / CI workers / teaching labs), default 0o644 perms
let other users read all of this. The fix: explicit chmod
0o600 on the file + 0o700 on its parent directory after
sqlite3 creates them.

Three decision branches:
  (a) Fresh init — no DB exists, ctx CLI creates it. Pin: end
      state has 0o600 file, 0o700 directory.
  (b) Existing DB un-chmod'd (e.g. seeded by an older BT
      version pre-#129) — next ctx CLI invocation tightens
      perms (idempotent fix-on-touch).
  (c) Restrictive umask=077 vs default 022 — both end with the
      same final mode (chmod is unconditional, not 'or'-applied).

Manual VM smoke (`ls -l ~/.claude-context/context.db` after
`ctx init`) is documented in tests/manual/README.md.
"""
from __future__ import annotations

import os
import stat
import subprocess
import sys
from pathlib import Path

import pytest


REPO_ROOT = Path(__file__).resolve().parent.parent
CTX_CLI = REPO_ROOT / "tools" / "ctx"
TASKS_CLI = REPO_ROOT / "tools" / "tasks"


def _run_ctx(args, home: Path) -> subprocess.CompletedProcess:
    """Invoke ctx CLI under isolated HOME — DB lives at
    home/.claude-context/context.db."""
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        [sys.executable, str(CTX_CLI)] + args,
        env=env,
        capture_output=True, text=True, timeout=10,
    )


def _run_tasks(args, home: Path) -> subprocess.CompletedProcess:
    env = {**os.environ, "HOME": str(home)}
    return subprocess.run(
        [sys.executable, str(TASKS_CLI)] + args,
        env=env,
        capture_output=True, text=True, timeout=10,
    )


def _file_mode_bits(path: Path) -> int:
    """Return the permission bits (rwx for u/g/o) — strip type
    bits so 0o600 vs S_IFREG|0o600 compare cleanly."""
    return path.stat().st_mode & 0o777


# ─── Branch (a): fresh init produces 0o600 file + 0o700 dir ────────────


def test_fresh_ctx_init_chmods_db_file_to_600(tmp_path):
    """Pin: first-ever ctx invocation creates DB with mode
    0o600. No group / world bits set."""
    result = _run_ctx(["get", "_smoke"], home=tmp_path)
    assert result.returncode == 0, result.stderr

    db_path = tmp_path / ".claude-context" / "context.db"
    assert db_path.exists()
    mode = _file_mode_bits(db_path)
    assert mode == 0o600, (
        f"DB mode = {oct(mode)} (expected 0o600). Group/world "
        f"bits: {oct(mode & 0o077)}"
    )


def test_fresh_ctx_init_chmods_parent_dir_to_700(tmp_path):
    """Pin: parent dir is 0o700. Without this, even a 0o600
    file is reachable to anyone who can readdir the parent."""
    _run_ctx(["get", "_smoke"], home=tmp_path)
    parent = tmp_path / ".claude-context"
    assert parent.exists() and parent.is_dir()
    mode = _file_mode_bits(parent)
    assert mode == 0o700, (
        f"parent dir mode = {oct(mode)} (expected 0o700)"
    )


def test_db_has_no_group_or_world_read_bits_set(tmp_path):
    """Headline #129 contract: `os.stat().st_mode & 0o077 == 0`.
    Neither group nor world has any bit (read/write/execute)."""
    _run_ctx(["get", "_smoke"], home=tmp_path)
    db_path = tmp_path / ".claude-context" / "context.db"
    mode = db_path.stat().st_mode & 0o077
    assert mode == 0, (
        f"DB has group/world bits set: {oct(mode)} — "
        f"sensitive project info readable by other users"
    )


def test_parent_dir_has_no_group_or_world_bits_set(tmp_path):
    """Same contract for the parent dir — `& 0o077 == 0`."""
    _run_ctx(["get", "_smoke"], home=tmp_path)
    parent = tmp_path / ".claude-context"
    mode = parent.stat().st_mode & 0o077
    assert mode == 0, (
        f"parent dir has group/world bits: {oct(mode)}"
    )


# ─── Branch (b): existing DB un-chmod'd → fix-on-touch ─────────────────


def test_existing_db_with_loose_perms_gets_tightened_on_next_ctx_call(
        tmp_path):
    """Simulate pre-#129 state: seed DB with 0o644 from a
    different process. Next `ctx get` tightens to 0o600
    (idempotent fix)."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"

    # Seed via direct sqlite3 (mimics a pre-fix install)
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS sessions ("
        "name TEXT PRIMARY KEY, description TEXT, work_dir TEXT)"
    )
    conn.commit()
    conn.close()
    # Force loose perms
    os.chmod(db_path, 0o644)
    os.chmod(db_dir, 0o755)
    assert _file_mode_bits(db_path) == 0o644

    # Next ctx invocation should tighten
    _run_ctx(["get", "_smoke"], home=tmp_path)

    assert _file_mode_bits(db_path) == 0o600
    assert _file_mode_bits(db_dir) == 0o700


def test_loose_perms_with_world_writable_get_tightened(tmp_path):
    """Pathological: someone set 0o666 (world-writable). Pin
    that fix-on-touch closes this immediately."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"

    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.commit()
    conn.close()
    os.chmod(db_path, 0o666)
    os.chmod(db_dir, 0o777)

    _run_ctx(["get", "_smoke"], home=tmp_path)

    assert _file_mode_bits(db_path) == 0o600
    assert _file_mode_bits(db_dir) == 0o700


# ─── Branch (c): umask 077 vs 022 — same final state ───────────────────


def test_default_umask_022_yields_0o600(tmp_path):
    """Pin: even with permissive default umask=022 (which would
    create 0o644 for files), the explicit chmod tightens. Pin
    so the fix isn't accidentally reliant on the user's umask."""
    # We can't easily set umask from the parent process for the
    # subprocess since umask is per-process. But ctx's chmod is
    # unconditional (not "if loose, tighten"). So whatever the
    # umask, end state is 0o600.
    env = {**os.environ, "HOME": str(tmp_path)}
    # Spawn shell that sets umask=022 then runs ctx
    subprocess.run(
        ["bash", "-c",
         f"umask 022 && '{sys.executable}' '{CTX_CLI}' get _smoke"],
        env=env, capture_output=True, text=True, timeout=10,
    )

    db_path = tmp_path / ".claude-context" / "context.db"
    assert _file_mode_bits(db_path) == 0o600


def test_restrictive_umask_077_also_yields_0o600(tmp_path):
    """Mirror: with restrictive umask=077, ctx still chmods to
    0o600 (not e.g. 0o400). Pin: chmod uses fixed octal, not
    'apply current umask + tighten further'."""
    env = {**os.environ, "HOME": str(tmp_path)}
    subprocess.run(
        ["bash", "-c",
         f"umask 077 && '{sys.executable}' '{CTX_CLI}' get _smoke"],
        env=env, capture_output=True, text=True, timeout=10,
    )

    db_path = tmp_path / ".claude-context" / "context.db"
    assert _file_mode_bits(db_path) == 0o600


# ─── tasks CLI also uses the helper (parity) ──────────────────────────


def test_tasks_cli_first_call_creates_db_with_0o600(tmp_path):
    """`tasks` and `ctx` share DB_PATH. Either CLI may be the
    one that creates the file on first ever invocation. Pin
    `tasks` chmod's to 0o600 too."""
    result = _run_tasks(["list", "_smoke"], home=tmp_path)
    assert result.returncode == 0
    db_path = tmp_path / ".claude-context" / "context.db"
    assert db_path.exists()
    assert _file_mode_bits(db_path) == 0o600


def test_tasks_cli_tightens_loose_db_on_call(tmp_path):
    """Same fix-on-touch behaviour from `tasks` side."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir()
    db_path = db_dir / "context.db"
    import sqlite3
    conn = sqlite3.connect(str(db_path))
    conn.execute(
        "CREATE TABLE IF NOT EXISTS task_config "
        "(project TEXT PRIMARY KEY, autorun INTEGER)")
    conn.commit()
    conn.close()
    os.chmod(db_path, 0o644)
    os.chmod(db_dir, 0o755)

    _run_tasks(["list", "_smoke"], home=tmp_path)

    assert _file_mode_bits(db_path) == 0o600
    assert _file_mode_bits(db_dir) == 0o700


# ─── Source-grep: chmod helper present + invoked from get_db ──────────


def test_ctx_cli_has_secure_perms_helper():
    """Pin: `_ensure_secure_perms` helper defined in `tools/ctx`,
    using `os.chmod(path, 0o600)` and `os.chmod(parent, 0o700)`."""
    src = CTX_CLI.read_text()
    assert "def _ensure_secure_perms" in src
    fn_idx = src.find("def _ensure_secure_perms")
    next_def = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_def]
    assert "0o600" in body
    assert "0o700" in body
    assert "os.chmod(" in body


def test_ctx_get_db_calls_secure_perms():
    """Pin: `get_db()` calls `_ensure_secure_perms(DB_PATH)`
    after sqlite has created the file. Without this, the helper
    is dead code."""
    src = CTX_CLI.read_text()
    fn_idx = src.find("def get_db")
    next_def = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_def]
    assert "_ensure_secure_perms(DB_PATH)" in body


def test_tasks_cli_has_secure_perms_helper():
    """Same defense in tasks CLI — duplicate helper (different
    file but identical logic)."""
    src = TASKS_CLI.read_text()
    assert "def _ensure_secure_perms" in src
    assert "0o600" in src
    assert "0o700" in src


def test_tasks_get_db_calls_secure_perms():
    src = TASKS_CLI.read_text()
    fn_idx = src.find("def get_db")
    next_def = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_def]
    assert "_ensure_secure_perms(DB_PATH)" in body


# ─── Defensive: best-effort wrapping ─────────────────────────────────


def test_secure_perms_swallows_oserror_on_readonly_fs(tmp_path):
    """Pin: `_ensure_secure_perms` wraps `os.chmod` in
    try/except OSError. If user mounted ~/.claude-context on a
    read-only filesystem (rare but possible), the helper logs
    nothing and returns silently — DB still works for queries
    even if perms can't be tightened."""
    src = CTX_CLI.read_text()
    fn_idx = src.find("def _ensure_secure_perms")
    next_def = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_def]
    # try/except OSError around os.chmod
    assert "try:" in body
    assert "except OSError:" in body
    # Best-effort comment / docstring
    assert "best-effort" in body.lower() or "OSError" in body


def test_secure_perms_handles_missing_file_gracefully():
    """Pin: helper checks `path.exists()` before chmod'ing the
    file. If the DB doesn't exist (called pre-creation), no
    exception."""
    src = CTX_CLI.read_text()
    fn_idx = src.find("def _ensure_secure_perms")
    next_def = src.find("\ndef ", fn_idx + 1)
    body = src[fn_idx:next_def]
    assert "path.exists()" in body or "isfile" in body


# ─── Idempotency: repeated calls don't break ─────────────────────────


def test_repeated_ctx_calls_keep_perms_at_0o600(tmp_path):
    """Pin: idempotent. 5 ctx invocations don't toggle perms or
    drift. Each call ends at 0o600."""
    db_path = tmp_path / ".claude-context" / "context.db"
    for _ in range(5):
        _run_ctx(["get", "_smoke"], home=tmp_path)
        assert _file_mode_bits(db_path) == 0o600


def test_user_manually_loosens_perms_then_ctx_tightens_again(tmp_path):
    """Pin: even if user manually does `chmod 644
    ~/.claude-context/context.db` (e.g. trying to share it via
    `cp` to another user), the next ctx call tightens it back.
    Pin so the user's escape hatch isn't a permanent leak."""
    _run_ctx(["get", "_smoke"], home=tmp_path)
    db_path = tmp_path / ".claude-context" / "context.db"
    assert _file_mode_bits(db_path) == 0o600

    # User manually loosens
    os.chmod(db_path, 0o644)
    assert _file_mode_bits(db_path) == 0o644

    # Next ctx call tightens
    _run_ctx(["get", "_smoke"], home=tmp_path)
    assert _file_mode_bits(db_path) == 0o600


# ─── Complementary: SQLite WAL files (if present) also under 0o700 dir ─


def test_sqlite_wal_journal_files_inside_secure_dir(tmp_path):
    """sqlite WAL mode creates `context.db-wal` and
    `context.db-shm` sidecar files. They inherit umask but
    live INSIDE the 0o700 dir we tightened — so they're
    inaccessible to other users via the dir guard alone.

    Pin: WAL sidecar files exist under the secure parent."""
    _run_ctx(["set", "_smoke", "k", "v"], home=tmp_path)

    db_dir = tmp_path / ".claude-context"
    # WAL files may or may not exist at this point — sqlite
    # creates them lazily. Just verify the parent dir is 0o700
    # so even if they exist, they're behind the dir guard.
    assert _file_mode_bits(db_dir) == 0o700


# ─── Cross: DB_PATH is per-user (not shared) ──────────────────────────


def test_ctx_db_path_uses_user_home(tmp_path):
    """Pin: DB_PATH is `Path.home() / ".claude-context" /
    "context.db"`. NOT under /tmp, /var, or system-wide path
    that would be world-readable by default."""
    src = CTX_CLI.read_text()
    assert "Path.home()" in src
    assert ".claude-context" in src
    assert 'Path("/tmp' not in src
    assert 'Path("/var' not in src
    assert "Path('/opt" not in src


# ─── Cross-cutting: write+read smoke after secure perms ──────────────


def test_set_then_get_works_after_chmod(tmp_path):
    """Pin: after chmod 0o600, ctx can still read+write. The
    perm change doesn't break sqlite's own access (the calling
    user's UID retains rw)."""
    result = _run_ctx(
        ["set", "_smoke", "answer", "42"], home=tmp_path)
    assert result.returncode == 0

    result = _run_ctx(["get", "_smoke"], home=tmp_path)
    assert result.returncode == 0
    assert "42" in result.stdout


def test_two_clis_share_secure_db(tmp_path):
    """Pin: ctx and tasks both honor the same DB at 0o600.
    First creates → 0o600. Second invocation (different CLI)
    can still read+write."""
    _run_ctx(["set", "_smoke", "k", "v"], home=tmp_path)
    db_path = tmp_path / ".claude-context" / "context.db"
    assert _file_mode_bits(db_path) == 0o600

    # tasks CLI also writes to the same DB
    result = _run_tasks(
        ["add", "_smoke", "an injected task"], home=tmp_path)
    assert result.returncode == 0
    # Still 0o600 after tasks invocation
    assert _file_mode_bits(db_path) == 0o600


# ─── Migration: old umask=022 + 0o644 install gets fixed automatically ─


def test_pre_129_install_auto_migrates_to_0o600(tmp_path):
    """Migration scenario: pre-#129 BT created DB with 0o644.
    After updating BT, the next ctx CLI invocation fixes it
    silently. Pin: NO error message, NO user intervention
    required, file mode tightens automatically."""
    db_dir = tmp_path / ".claude-context"
    db_dir.mkdir(mode=0o755)
    db_path = db_dir / "context.db"
    # Pre-#129 state
    import sqlite3
    sqlite3.connect(str(db_path)).close()
    os.chmod(db_path, 0o644)

    # Simulating BT update — user runs ctx
    result = _run_ctx(["get", "_smoke"], home=tmp_path)

    # Silent fix
    assert "chmod" not in result.stderr.lower()
    assert "permission" not in result.stderr.lower()
    # Mode tightened
    assert _file_mode_bits(db_path) == 0o600
    assert _file_mode_bits(db_dir) == 0o700
