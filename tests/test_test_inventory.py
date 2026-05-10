"""Pin the @pytest.mark.slow + tests/e2e/ inventory (#16 / #88).

Background: user reported (2026-05-07) that 'slow' tests felt like
they had vanished. Audit (#88) confirmed they're still collected by
the default `pytest tests/` path — `pytest.ini` registers the marker
but doesn't auto-skip via `addopts`.

This file is the host-side guard rail:
  - Exactly 3 files use @pytest.mark.slow. A 4th sneaks in → fail.
  - tests/e2e/ contains exactly 11 test_*.py files. Drift → fail.
  - pytest.ini doesn't add a global '-m "not slow"' to addopts (which
    would silently de-collect the slow tests on default invocation).
  - tools/test_all.sh exposes the documented modes (--slow-only, --e2e).
  - tests/manual/README.md's slow inventory table mentions every slow
    test by name — README rot detector.

These are all <10 ms checks: they live next to the unit suite, not in
the e2e layer. The point is to fail in pre-commit, not at release time.
"""
from __future__ import annotations

import re
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
TESTS = REPO_ROOT / "tests"
E2E = TESTS / "e2e"
RUNBOOK = TESTS / "manual" / "README.md"
TEST_ALL = REPO_ROOT / "tools" / "test_all.sh"
PYTEST_INI = REPO_ROOT / "pytest.ini"


# ─── Slow inventory (the canonical 3) ──────────────────────────────────────


EXPECTED_SLOW_FILES = {
    "test_exploration.py",
    "test_manifests.py",
    "test_idle_timeout.py",
}


_SLOW_DECORATOR_RE = re.compile(
    r"^@pytest\.mark\.slow\s*$\n^(?:@.*\s*$\n)*\s*def\s",
    re.MULTILINE,
)


def _collect_slow_files() -> set[str]:
    """Walk tests/ and return the set of file *names* with at least
    one real @pytest.mark.slow decorator. Match the decorator on its
    own line followed (possibly through more decorators) by a `def` —
    that filters out docstring mentions of the marker name."""
    out: set[str] = set()
    for path in TESTS.rglob("test_*.py"):
        try:
            text = path.read_text()
        except OSError:
            continue
        if _SLOW_DECORATOR_RE.search(text):
            out.add(path.name)
    return out


def test_slow_inventory_matches_expected_three():
    """If you see this fail with 'extra' — congrats, you added a 4th
    @slow test. Update EXPECTED_SLOW_FILES + the table in
    tests/manual/README.md, otherwise users will be surprised that
    `pytest tests/` got slower."""
    actual = _collect_slow_files()
    extra = actual - EXPECTED_SLOW_FILES
    missing = EXPECTED_SLOW_FILES - actual
    assert actual == EXPECTED_SLOW_FILES, (
        f"slow inventory drift:\n  extra:   {sorted(extra)}\n"
        f"  missing: {sorted(missing)}\n"
        f"Update EXPECTED_SLOW_FILES + tests/manual/README.md."
    )


@pytest.mark.parametrize("filename", sorted(EXPECTED_SLOW_FILES))
def test_each_slow_file_has_at_least_one_slow_decorator(filename):
    """Triple-redundant — catches the case where someone removes the
    decorator but forgets to update this list."""
    path = TESTS / filename
    assert path.is_file(), f"{filename} no longer exists in tests/"
    text = path.read_text()
    assert "@pytest.mark.slow" in text


# ─── e2e inventory ─────────────────────────────────────────────────────────


EXPECTED_E2E_TEST_FILES = {
    "test_cli_tools_smoke.py",
    "test_dual_provider_workflow.py",
    "test_feed_capture_foundation.py",
    "test_intro_prompt_structure.py",
    "test_per_tab_plugin_gating.py",
    "test_provider_switching.py",
    "test_sidebar_context_menu_rest.py",
    "test_smoke_battery.py",
    "test_tier1_acceptance.py",
    "test_tier2_acceptance.py",
    "test_tier3_acceptance.py",
}


def test_e2e_test_file_inventory_matches_expected():
    """tests/e2e/ ships with 11 test_*.py files. Adding/removing one
    without updating both this list and the README's e2e table is a
    drift bug."""
    actual = {p.name for p in E2E.glob("test_*.py")}
    extra = actual - EXPECTED_E2E_TEST_FILES
    missing = EXPECTED_E2E_TEST_FILES - actual
    assert actual == EXPECTED_E2E_TEST_FILES, (
        f"e2e inventory drift:\n  extra:   {sorted(extra)}\n"
        f"  missing: {sorted(missing)}\n"
        f"Update EXPECTED_E2E_TEST_FILES + tests/manual/README.md."
    )


def test_e2e_dir_has_init_and_readme():
    """Sanity — tests/e2e/ is a package + has its own README."""
    assert (E2E / "__init__.py").is_file()
    assert (E2E / "README.md").is_file()


# ─── pytest.ini contract ───────────────────────────────────────────────────


def test_pytest_ini_registers_slow_marker():
    """The marker must be declared so 'pytest -m slow' works without
    PytestUnknownMarkWarning."""
    text = PYTEST_INI.read_text()
    assert re.search(r"^\s*slow:\s", text, re.M), (
        "pytest.ini no longer declares the 'slow' marker"
    )


def test_pytest_ini_does_not_globally_skip_slow():
    """If addopts contains '-m "not slow"' or similar, the user's
    feedback ('slow tests perceived as missing') would be technically
    accurate — slow tests would silently de-collect from `pytest tests/`.
    Guard against that ever shipping."""
    text = PYTEST_INI.read_text()
    addopts_match = re.search(
        r"^\s*addopts\s*=\s*(.+)$", text, re.M)
    if addopts_match:
        opts = addopts_match.group(1)
        assert "not slow" not in opts, (
            "pytest.ini addopts auto-skips slow tests — this is the "
            "exact regression #88 was filed for. Remove the -m exclusion."
        )


# ─── tools/test_all.sh contract ────────────────────────────────────────────


@pytest.mark.parametrize("mode", [
    "--fast", "--slow", "--slow-only", "--e2e", "--quick",
    "--layer", "--watch",
])
def test_test_all_sh_supports_mode(mode):
    """Every documented mode in the README has a real case branch
    (or option detection) in test_all.sh. Drift between docs and
    implementation will silently fail the user with 'unknown mode'."""
    text = TEST_ALL.read_text()
    assert mode in text, f"test_all.sh no longer references {mode!r}"


def test_test_all_sh_slow_only_runs_only_slow_marker():
    """--slow-only must invoke pytest with `-m slow` (NOT --slow which
    is a different flag, and NOT bare pytest which would run everything)."""
    text = TEST_ALL.read_text()
    # Match the case branch body
    m = re.search(r"--slow-only\)\s*\n((?:.+\n){1,6})", text)
    assert m, "test_all.sh missing --slow-only case branch"
    body = m.group(1)
    assert "-m slow" in body, (
        "--slow-only doesn't actually filter to the slow marker"
    )


def test_test_all_sh_e2e_targets_e2e_dir():
    """--e2e must invoke pytest against tests/e2e/ specifically (not
    rely on auto-discovery from cwd)."""
    text = TEST_ALL.read_text()
    m = re.search(r"--e2e\)\s*\n((?:.+\n){1,6})", text)
    assert m, "test_all.sh missing --e2e case branch"
    body = m.group(1)
    assert "tests/e2e/" in body


# ─── README rot detector ───────────────────────────────────────────────────


def test_readme_lists_every_slow_test_by_name():
    """The slow inventory table in tests/manual/README.md must mention
    each of the 3 slow test files. Catches the case where someone adds
    a new @slow without updating the README."""
    text = RUNBOOK.read_text()
    for filename in sorted(EXPECTED_SLOW_FILES):
        # Strip 'test_' prefix and '.py' so the README can use prose
        # form like 'random-walk explorer' — we just need *some*
        # reference. Match on the bare module name: 'test_exploration'.
        stem = filename.replace(".py", "")
        assert stem in text, (
            f"runbook missing reference to {stem!r} in slow inventory"
        )


def test_readme_lists_every_e2e_test_by_name():
    """Same for the e2e inventory table."""
    text = RUNBOOK.read_text()
    for filename in sorted(EXPECTED_E2E_TEST_FILES):
        assert filename in text, (
            f"runbook missing reference to {filename!r} in e2e table"
        )


def test_readme_documents_all_run_modes():
    """The 'Pytest run modes' table must cover the 5 canonical
    invocations users are likely to type."""
    text = RUNBOOK.read_text()
    for invocation in [
        "pytest tests/",
        "pytest -m slow",
        "pytest tests/e2e/",
        "test_all.sh --slow-only",
        "test_all.sh --e2e",
    ]:
        assert invocation in text, (
            f"runbook missing run-mode entry: {invocation!r}"
        )


def test_readme_has_ci_matrix_table():
    """User asked: 'CI matrix doc — slow + e2e enabled on release
    branch only?'. The runbook now answers that with a Branch/Mode/Why
    table."""
    text = RUNBOOK.read_text()
    assert "CI matrix recommendation" in text
    # At minimum: feature branch + master + release row
    assert "feature/" in text or "feature/*" in text
    assert "release/" in text or "release/*" in text
    assert "nightly" in text.lower()


def test_readme_documents_bterminal_process_fixture():
    """The fixture in tests/conftest.py is the lynchpin of the e2e
    layer — runbook owes new contributors a quick reference so they
    don't have to read 200 lines of conftest before writing their
    first e2e test."""
    text = RUNBOOK.read_text()
    assert "bterminal_process" in text
    assert "conftest.py" in text
