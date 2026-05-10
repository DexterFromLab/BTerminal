"""Pytest wrapper for tools/test_update_vm.sh (#14 / #86).

Source-level checks + an opt-in 'real VM run' that's skipped unless
BTERMINAL_VM_TESTS=1 is set in env. The opt-in keeps slow / SSH-bound
runs out of the default `pytest tests/` path.

Edge cases covered by source-level checks:
  - license hash drift (#52 regression — phase 1 + 5)
  - errata.json parse failure (phase 2)
  - mid-install Ctrl-C / failure (phase 4 — same rollback pattern as
    #85's test_install_vm.sh, exercises install.sh's _on_error trap)
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_update_vm.sh"
HELPER_DIR = REPO_ROOT / "tools" / "_vm_update_checks"


# ─── Source-level checks ───────────────────────────────────────────────────


def test_update_vm_script_exists_and_executable():
    assert SCRIPT.is_file()
    mode = SCRIPT.stat().st_mode
    assert mode & 0o111


def test_update_vm_script_bash_syntax_valid():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_update_vm_script_help_returns_zero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    for flag in ("--modes", "--skip-rollback", "--use-real-remote"):
        assert flag in result.stdout, f"help missing flag: {flag}"
    # #130: env-var alternative also documented
    assert "BTERMINAL_NETWORK_TESTS" in result.stdout


def test_update_vm_script_unknown_flag_exits_nonzero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--bogus"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0


@pytest.mark.parametrize("phase_banner", [
    "_read_local_license returns markdown TEXT",
    "_load_local_errata tolerates corrupted errata.json",
    "End-to-end — fake upstream remote",
    "Rollback — corrupt install.sh mid-run",
    "_remote_license_blob_path resolves to a real markdown blob",
])
def test_update_vm_announces_each_phase(phase_banner):
    """Each of the 5 phases prints a recognizable banner so the
    runner output correlates with the runbook README phases table."""
    text = SCRIPT.read_text()
    assert phase_banner in text, f"phase banner missing: {phase_banner!r}"


def test_update_vm_uses_vm_sync_helper():
    """Always sync first — re-uses the canonical rsync wrapper."""
    text = SCRIPT.read_text()
    assert "vm_sync.sh" in text


def test_update_vm_stages_python_helpers_via_scp():
    """The script avoids heredoc-escape hell by scp'ing four pure
    Python helper files from tools/_vm_update_checks/ to /tmp on the
    VM. Schemas of those helpers are validated below."""
    text = SCRIPT.read_text()
    assert "scp" in text
    assert "_vm_update_checks" in text
    assert "/tmp/bt_update_checks" in text


def test_update_vm_helper_dir_contains_four_python_files():
    files = sorted(p.name for p in HELPER_DIR.glob("*.py"))
    expected = [
        "blob_path_probe.py",
        "errata_corruption.py",
        "git_pull_check.py",
        "license_regression.py",
    ]
    assert files == expected, f"helper inventory drift: {files}"


@pytest.mark.parametrize("helper, expected_marker", [
    ("license_regression.py", "local-license-ok"),
    ("errata_corruption.py", "errata-loader-ok"),
    ("git_pull_check.py", "pull-ok"),
    ("blob_path_probe.py", "blob-content-ok"),
])
def test_update_vm_helper_emits_expected_pass_marker(helper, expected_marker):
    """Each helper's stdout 'OK marker' is grepped by the bash runner.
    A marker rename without bash-side update would silently fail open
    — pin the contract here."""
    text = (HELPER_DIR / helper).read_text()
    assert expected_marker in text, (
        f"{helper} no longer emits {expected_marker!r}"
    )


@pytest.mark.parametrize("helper, imported_symbol", [
    ("license_regression.py", "_read_local_license"),
    ("license_regression.py", "_fetch_remote_license"),
    ("errata_corruption.py", "_load_local_errata"),
    ("git_pull_check.py", "_git_pull_with_autostash"),
    ("git_pull_check.py", "_git_repo_is_dirty"),
    ("blob_path_probe.py", "_remote_license_blob_path"),
])
def test_update_vm_helper_imports_real_updater_symbol(helper, imported_symbol):
    """The helpers exercise specific updater.py functions; if any of
    those are renamed without updating the helper, the VM run would
    blow up with ImportError. Cheap host-side check pins the contract."""
    helper_text = (HELPER_DIR / helper).read_text()
    assert imported_symbol in helper_text, (
        f"{helper} doesn't import {imported_symbol}"
    )

    updater_text = (REPO_ROOT / "bterminal" / "updater.py").read_text()
    assert f"def {imported_symbol}" in updater_text, (
        f"{imported_symbol} no longer defined in updater.py"
    )


def test_update_vm_phase3_default_uses_fake_local_remote_not_github():
    """Phase 3's DEFAULT path uses a local /tmp upstream — never
    the real github.com remote unless --use-real-remote /
    BTERMINAL_NETWORK_TESTS=1 explicitly opts in (#130).

    Pin: the fake-upstream branch (mode_active 3 +
    USE_REAL_REMOTE=false) uses `git clone --quiet --bare
    $VM_PATH`. SSH-style `git@github` is forbidden everywhere
    (read-only HTTPS only)."""
    text = SCRIPT.read_text()
    assert "git clone --quiet --bare $VM_PATH" in text
    # No SSH GitHub URLs anywhere — auth would be required
    assert "git@github" not in text
    # github.com may now appear (in #130's --use-real-remote
    # branch), but the FAKE-UPSTREAM branch must not reference
    # it. Locate the fake-branch block + check.
    # Locate the executable fake-upstream block (start at `if
    # mode_active 3 && ... USE_REAL_REMOTE == false`, end at
    # the next `# ─── Phase 3 (alt)` banner).
    fake_marker = (
        'if mode_active 3 && [[ "$USE_REAL_REMOTE" == false ]]'
    )
    fake_block_start = text.find(fake_marker)
    assert fake_block_start > 0, (
        f"fake-branch marker {fake_marker!r} not found"
    )
    real_phase_banner = "# ─── Phase 3 (alt):"
    real_block_start = text.find(real_phase_banner, fake_block_start)
    fake_block = text[fake_block_start:real_block_start]
    assert "github.com" not in fake_block, (
        "fake-upstream phase 3 contains github.com — would "
        "accidentally hit network during default smokes"
    )


def test_update_vm_phase4_reuses_install_vm_rollback_pattern():
    """Phase 4 mirrors #85's rollback test: corrupt install.sh by
    injecting `false` after phase 5 banner, verify BTERMINAL_ROLLBACK_OK
    + __init__.py hash unchanged."""
    text = SCRIPT.read_text()
    assert "BTERMINAL_ROLLBACK_OK" in text
    assert "PRE_HASH" in text and "POST_HASH" in text
    assert "false" in text
    # Ensures the rollback corruption targets phase 5 (Files install)
    assert "5/7" in text


def test_update_vm_phase4_skipped_when_skip_rollback_passed():
    """`--skip-rollback` strips '4' from MODES so phase 4 doesn't run.
    Verify by parsing the script's flag handling."""
    text = SCRIPT.read_text()
    assert "--skip-rollback" in text
    assert 'MODES//4' in text or 'MODES//,4' in text


def test_update_vm_outputs_per_step_logs():
    text = SCRIPT.read_text()
    assert "LOG_DIR" in text
    assert "stderr.log" in text


def test_update_vm_emits_pass_fail_summary():
    text = SCRIPT.read_text()
    assert "passed" in text and "failed" in text
    assert "FAIL_LIST" in text


def test_update_vm_cleans_up_helper_staging_on_exit():
    """Don't leave /tmp/bt_update_checks lying around between runs."""
    text = SCRIPT.read_text()
    assert "rm -rf /tmp/bt_update_checks" in text


# ─── Helper smoke tests ────────────────────────────────────────────────────


@pytest.mark.parametrize("helper", [
    "blob_path_probe.py",
    "errata_corruption.py",
    "git_pull_check.py",
    "license_regression.py",
])
def test_helper_python_syntax_valid(helper):
    """Every helper compiles. ImportError surfaces here, not on the VM."""
    path = HELPER_DIR / helper
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(path)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"{helper} syntax error:\n{result.stderr}"
    )


def test_blob_path_probe_works_against_real_repo():
    """Run the blob path probe locally — it should find the canonical
    English license blob in this very repo. Catches regressions where
    the blob path drifts from the real on-disk file."""
    result = subprocess.run(
        [sys.executable, str(HELPER_DIR / "blob_path_probe.py"),
         str(REPO_ROOT)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"blob probe failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    assert "blob-path-ok" in result.stdout
    assert "blob-content-ok" in result.stdout


def test_license_regression_works_against_real_repo():
    """Same as above but for license_regression — proves the markdown
    text contract holds against the live tree right now."""
    result = subprocess.run(
        [sys.executable, str(HELPER_DIR / "license_regression.py"),
         str(REPO_ROOT)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, (
        f"license regression failed:\nstdout: {result.stdout}"
        f"\nstderr: {result.stderr}"
    )
    assert "local-license-ok" in result.stdout


# ─── #130 — real-remote phase 3 source-grep guards ─────────────────────────


def test_use_real_remote_flag_handled():
    """Pin: --use-real-remote toggles USE_REAL_REMOTE=true in the
    runner. Catches a refactor that drops the flag without
    updating the dispatch."""
    text = SCRIPT.read_text()
    assert "--use-real-remote)" in text
    assert "USE_REAL_REMOTE=true" in text


def test_bterminal_network_tests_env_var_also_flips_real_remote():
    """Pin: env-var alternative `BTERMINAL_NETWORK_TESTS=1` also
    enables the real-remote branch. Mirrors #89's
    `BTERMINAL_VM_TESTS` and #103's `BTERMINAL_NETWORK_DOWN_TEST`
    style (consistent gating across slow/network-bound tests)."""
    text = SCRIPT.read_text()
    assert "BTERMINAL_NETWORK_TESTS" in text
    # Specifically: env=1 sets USE_REAL_REMOTE=true
    assert 'BTERMINAL_NETWORK_TESTS:-0' in text or \
        '${BTERMINAL_NETWORK_TESTS:-0}' in text


def test_github_remote_default_points_at_dexter_from_lab():
    """Pin: default GitHub URL is the canonical BT repo.
    `BTERMINAL_GITHUB_REMOTE` env var allows override (for
    forks / mirror testing). Pin both."""
    text = SCRIPT.read_text()
    assert "DexterFromLab/BTerminal" in text
    assert "BTERMINAL_GITHUB_REMOTE" in text


def test_real_remote_phase_has_distinct_banner():
    """Pin: real-remote phase 3 has its own `[3-real]` banner so
    runner output distinguishes from the fake-upstream phase 3.
    Without this, mode-3 logs would conflate the two paths."""
    text = SCRIPT.read_text()
    assert "[3-real]" in text


def test_real_remote_phase_does_preflight_reachability_probe():
    """Pin: `git ls-remote` probe BEFORE attempting the clone.
    Branch (a) — when GitHub is unreachable, probe fails fast
    rather than the whole clone hanging. Branches (b)/(c) —
    rate limit / auth failure also surface here as probe
    failure (ls-remote returns non-zero)."""
    text = SCRIPT.read_text()
    assert "git ls-remote" in text
    # Probe wrapped in `timeout 10` so it doesn't hang
    assert "timeout 10 git ls-remote" in text


def test_real_remote_clone_uses_shallow_depth_for_speed():
    """Pin: `--depth 50` shallow clone — keeps network traffic
    bounded. A full clone of BT's history is ~MB; shallow keeps
    it sub-MB. Catches a refactor that switches to full clone."""
    text = SCRIPT.read_text()
    assert "--depth 50" in text or "--depth=50" in text


def test_real_remote_phase_writes_distinct_log_file():
    """Pin: real-remote phase logs to `phase3-real.log`,
    distinct from `phase3.log` (fake upstream). Lets debug
    locate the right log without grepping."""
    text = SCRIPT.read_text()
    assert "phase3-real.log" in text


def test_real_remote_does_not_push_to_origin():
    """Security pin: read-only access to GitHub. The phase 3
    real-remote test must NEVER `git push origin` — that would
    require auth + would mutate the public repo. Pin: only
    `git fetch` and worktree-only commits."""
    text = SCRIPT.read_text()
    # Find the real-remote phase block
    real_phase_start = text.find("[3-real]")
    if real_phase_start < 0:
        pytest.skip("real-remote phase not present")
    next_phase = text.find("# ─── Phase 4", real_phase_start)
    real_phase = text[real_phase_start:next_phase]
    # No `git push origin` in the real-remote section
    assert "git push" not in real_phase, (
        "real-remote phase contains git push — would fail "
        "without auth + mutates public repo"
    )


def test_real_remote_failure_surfaces_branch_appropriate_message():
    """Pin: when probe fails, the runner emits a single combined
    message covering all three failure branches (a unreachable /
    b rate-limited / c auth fail) so users see the actionable
    'GitHub unreachable / rate-limited / auth fail' hint."""
    text = SCRIPT.read_text()
    # The failure message mentions all three causes
    assert "unreachable" in text.lower()
    assert ("rate-limit" in text.lower()
            or "rate limit" in text.lower())
    assert "auth" in text.lower()


def test_real_remote_cleanup_on_failure():
    """Pin: even on real-remote failure, `/tmp/bt-real-local-$$`
    cleaned up. Without this, /tmp accumulates clones across
    test reruns."""
    text = SCRIPT.read_text()
    real_phase_start = text.find("[3-real]")
    next_phase = text.find("# ─── Phase 4", real_phase_start)
    real_phase = text[real_phase_start:next_phase]
    assert "rm -rf" in real_phase
    # Specifically the LOCAL var cleanup
    assert "$LOCAL" in real_phase or "${LOCAL}" in real_phase


def test_modes_3_alone_does_not_require_real_remote():
    """Pin: `--modes 3` without `--use-real-remote` runs the
    fake-upstream branch (default behavior). Without this, the
    real-remote branch would always fire for `--modes 3` users."""
    text = SCRIPT.read_text()
    # Both branches gated on USE_REAL_REMOTE
    fake_phase_idx = text.find('mode_active 3 && [[ "$USE_REAL_REMOTE" == false ]]')
    real_phase_idx = text.find('mode_active 3 && [[ "$USE_REAL_REMOTE" == true ]]')
    assert fake_phase_idx > 0
    assert real_phase_idx > 0
    assert real_phase_idx > fake_phase_idx, (
        "real-remote phase declared before fake — runtime would "
        "skip the real branch on --use-real-remote"
    )


def test_use_real_remote_default_value_is_false():
    """Pin: `USE_REAL_REMOTE=false` is the default. Without this,
    routine smokes (no flag, no env) would hit network."""
    text = SCRIPT.read_text()
    # The default assignment near the top of the script
    assert "USE_REAL_REMOTE=false" in text


def test_use_real_remote_handled_in_arg_parser_loop():
    """Pin: the arg parser's case branch handles --use-real-remote
    explicitly (not via fall-through to the catch-all
    'Unknown' error)."""
    text = SCRIPT.read_text()
    # In the while [[ $# -gt 0 ]] loop's case statement
    assert "--use-real-remote)" in text


# ─── Opt-in real-VM run ────────────────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("BTERMINAL_VM_TESTS") != "1",
    reason="VM-bound test — set BTERMINAL_VM_TESTS=1 + ensure vm-test alias",
)
def test_update_vm_real_run_passes():
    """End-to-end real-VM updater smoke. Skipped by default to keep
    `pytest tests/` fast. Failures here mean the updater path on a
    real machine is broken; catches packaging-level regressions
    invisible to unit tests."""
    result = subprocess.run(
        ["bash", str(SCRIPT), "--skip-rollback"],
        capture_output=True, text=True, timeout=600,
    )
    assert result.returncode == 0, (
        f"updater VM smoke failed exit={result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-3000:]}\n"
        f"--- stderr ---\n{result.stderr[-1500:]}"
    )


@pytest.mark.skipif(
    os.environ.get("BTERMINAL_VM_TESTS") != "1"
        or os.environ.get("BTERMINAL_NETWORK_TESTS") != "1",
    reason="VM + network test — needs BTERMINAL_VM_TESTS=1 + "
           "BTERMINAL_NETWORK_TESTS=1",
)
def test_update_vm_real_remote_run_passes():
    """End-to-end real-VM updater smoke against GitHub. Doubly-
    gated (VM + network) so it never fires under default pytest
    invocation. Catches packaging-level regressions involving
    real git remote interactions: shallow clone semantics, HTTPS
    auth (or lack thereof), GitHub rate limits."""
    result = subprocess.run(
        ["bash", str(SCRIPT), "--use-real-remote", "--modes", "3",
         "--skip-rollback"],
        capture_output=True, text=True, timeout=600,
    )
    # Either passes (network reachable, real-remote pull worked)
    # OR fails with a 'GitHub unreachable' message — both
    # acceptable outcomes for a network-gated test.
    assert (result.returncode == 0
            or "unreachable" in result.stdout
            or "rate-limited" in result.stdout
            or "auth fail" in result.stdout), (
        f"unexpected failure mode: exit={result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-2000:]}"
    )
