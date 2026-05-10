"""Pin tests for #111 — install.sh do_fix + validate_npm_cli must
neutralize cwd before any subprocess fork.

Background: when the GUI installer wizard spawns `install.sh --fix`
with cwd=$INSTALL_DIR, a later phase that renames INSTALL_DIR aside
(BACKUP_DIR rotation) leaves the script's cwd dangling. The next
external probe (readlink, head, the AI binary's --version call)
fork()s and prints "current working directory was deleted" — which
masquerades as a Repair FAILED in the wizard summary even when the
in-place fix (launcher symlink restore) actually worked.

Fix: cd to /tmp at the top of validate_npm_cli (both forward + real
definitions) and at do_fix entry. cd is a bash builtin so it works
regardless of whether the original cwd still exists.
"""
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parent.parent
INSTALL_SH = REPO_ROOT / "install.sh"


def _slice_function(src: str, header: str) -> str:
    """Return the body of the next bash function with this header."""
    idx = src.find(header)
    assert idx >= 0, f"function header not found: {header!r}"
    # find next closing top-level `}` (function ends with `^}` line)
    end = src.find("\n}\n", idx)
    assert end > idx, f"function end not found for {header!r}"
    return src[idx:end]


def test_install_sh_present():
    assert INSTALL_SH.is_file()


def test_validate_npm_cli_forward_decl_changes_cwd_first():
    """Pin: forward declaration of validate_npm_cli (used by do_fix)
    cd's to /tmp before touching $1 or any external command."""
    src = INSTALL_SH.read_text()
    # Both definitions share the same header; the FIRST occurrence is
    # the forward decl (≈ line 310), the second is the canonical one.
    first_idx = src.find("validate_npm_cli() {")
    assert first_idx > 0
    body = _slice_function(src[first_idx:], "validate_npm_cli() {")
    # First non-comment, non-empty statement must be the cd guard.
    statements = [
        ln.strip() for ln in body.splitlines()[1:]
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert statements, "function body unexpectedly empty"
    assert statements[0].startswith("cd /tmp"), (
        f"expected first statement to be 'cd /tmp ...', "
        f"got: {statements[0]!r}"
    )
    # Must have the || cd / fallback (in case /tmp itself is unavailable)
    assert "cd /" in statements[0]


def test_validate_npm_cli_real_decl_also_changes_cwd():
    """Pin: the canonical (later) definition also cd's to /tmp.
    Required because the install path runs the second definition
    AFTER do_fix returns and falls through to install."""
    src = INSTALL_SH.read_text()
    first_idx = src.find("validate_npm_cli() {")
    second_idx = src.find("validate_npm_cli() {", first_idx + 1)
    assert second_idx > first_idx, (
        "canonical (second) validate_npm_cli definition not found"
    )
    body = _slice_function(src[second_idx:], "validate_npm_cli() {")
    statements = [
        ln.strip() for ln in body.splitlines()[1:]
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert statements[0].startswith("cd /tmp"), (
        f"expected first statement to be 'cd /tmp ...', "
        f"got: {statements[0]!r}"
    )


def test_do_fix_changes_cwd_before_first_subprocess():
    """Pin: do_fix() top-of-function cd's to /tmp so the WHOLE repair
    flow inherits a stable cwd, not just the validate_npm_cli call."""
    src = INSTALL_SH.read_text()
    body = _slice_function(src, "do_fix() {")
    statements = [
        ln.strip() for ln in body.splitlines()[1:]
        if ln.strip() and not ln.strip().startswith("#")
    ]
    assert statements, "do_fix body unexpectedly empty"
    assert statements[0].startswith("cd /tmp"), (
        f"do_fix's first statement must be cd /tmp, got: {statements[0]!r}"
    )


def test_cwd_neutralization_uses_silent_fallback():
    """Pin: `cd /tmp 2>/dev/null || cd /` pattern — silent because the
    error message itself is what we're trying to suppress, and the
    fallback to / is for hardened environments without /tmp."""
    src = INSTALL_SH.read_text()
    # Pattern must appear at least 3× — once per fix site.
    occurrences = src.count("cd /tmp 2>/dev/null || cd /")
    assert occurrences >= 3, (
        f"expected pattern in 3+ places (validate_npm_cli ×2, do_fix ×1), "
        f"got {occurrences}"
    )
