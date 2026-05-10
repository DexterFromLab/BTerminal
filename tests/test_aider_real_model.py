"""Pytest wrapper for tools/test_aider_real_model.sh (#17 / #89).

Source-level checks pin the contract between the bash runner, its two
Python helpers (argv_parity / stats_check), and AiderProvider.

Local helper smoke tests run against the host's repo (~10 ms each) —
they catch any rename in `bterminal.providers.aider` or signature
drift before anyone fires the slow ollama-bound run.

The opt-in real run gates on TWO env vars:
  - BTERMINAL_VM_TESTS=1 — same as the other VM runners
  - BTERMINAL_OLLAMA_AVAILABLE=1 — confirms the runner has a working
    ollama daemon (locally OR via the SSH alias)
Both must be set or the test skips. This double-gate keeps pytest fast
on machines without ollama + prevents accidental ~1-min runs in CI.
"""
from __future__ import annotations

import os
import subprocess
import sys
from pathlib import Path

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
SCRIPT = REPO_ROOT / "tools" / "test_aider_real_model.sh"
HELPER_DIR = REPO_ROOT / "tools" / "_vm_aider_checks"
PROVIDER_PY = REPO_ROOT / "bterminal" / "providers" / "aider.py"


# ─── Bash runner shape ─────────────────────────────────────────────────────


def test_runner_script_exists_and_executable():
    assert SCRIPT.is_file()
    assert SCRIPT.stat().st_mode & 0o111


def test_runner_script_bash_syntax_valid():
    result = subprocess.run(
        ["bash", "-n", str(SCRIPT)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_runner_script_help_returns_zero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--help"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0
    for flag in ("--no-pull", "--keep-project", "--large-project"):
        assert flag in result.stdout, f"help missing {flag}"


def test_runner_script_unknown_flag_exits_nonzero():
    result = subprocess.run(
        ["bash", str(SCRIPT), "--bogus"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0


# ─── Phase coverage ────────────────────────────────────────────────────────


@pytest.mark.parametrize("phase_banner", [
    "[0] Preflight",
    "[1] Setting up clean test project",
    "[2] Computing BT-style argv from AiderProvider.build_argv",
    "[3] Spawning aider with --message",
    "[4] Asserting 'PONG' in aider stdout",
    "[5] Asserting .aider.chat.history.md was created",
    "[6] Verifying AiderProvider.parse_session_stats reads the log",
])
def test_runner_announces_each_phase(phase_banner):
    """Every documented phase prints a recognizable banner."""
    text = SCRIPT.read_text()
    assert phase_banner in text, f"phase banner missing: {phase_banner!r}"


def test_runner_uses_qwen_05b_model():
    """The audit + recommend_models suggest qwen2.5-coder:0.5b as the
    smallest viable tester model. Drift from that breaks docs +
    DEFAULT_MODEL invariant in providers/aider.py."""
    text = SCRIPT.read_text()
    assert "qwen2.5-coder:0.5b" in text


def test_runner_uses_deterministic_prompt():
    """The PONG prompt is the entire phase 4 assertion. Changing it
    here without grepping for 'pong' downstream would break detection."""
    text = SCRIPT.read_text()
    assert "Reply with exactly the word PONG and nothing else." in text
    # Phase 4 asserts case-insensitive grep
    assert "grep -qi 'pong'" in text


def test_runner_pulls_model_via_ollama():
    text = SCRIPT.read_text()
    assert "ollama pull" in text
    assert "ollama list" in text


def test_runner_requires_aider_binary_present():
    """Phase 0 must hard-fail if aider isn't installed — otherwise
    phase 3 would emit a confusing 'command not found' instead of a
    clean error message."""
    text = SCRIPT.read_text()
    assert "command -v aider" in text
    assert "Aborting: aider is required." in text


def test_runner_recovers_from_dead_ollama_daemon():
    """If :11434 isn't responding, runner should attempt
    `nohup ollama serve` rather than aborting — VMs without a D-Bus
    user session can't systemctl-start ollama."""
    text = SCRIPT.read_text()
    assert "nohup ollama serve" in text


def test_runner_uses_timeout_around_aider_spawn():
    """qwen-0.5b is fast but not deterministic — without a timeout a
    hung dispatch would block the runner indefinitely. Post-#132,
    the timeout is parameterized via $SPAWN_TIMEOUT (90s default,
    180s for --large-project)."""
    text = SCRIPT.read_text()
    # Either the parameterized form OR the explicit fallback
    assert ("timeout ${SPAWN_TIMEOUT}s" in text
            or "timeout 90s" in text)
    # And both default values pinned
    assert "SPAWN_TIMEOUT=90" in text


def test_runner_creates_git_repo_in_test_project():
    """Aider refuses to edit untracked dirs by default — without
    `git init` the spawn would prompt interactively + the runner
    would hang."""
    text = SCRIPT.read_text()
    assert "git init" in text
    # Initial empty commit + main.py commit so aider has something
    assert "main.py" in text


# ─── Helper Python files ───────────────────────────────────────────────────


def test_helper_dir_contains_two_python_files():
    files = sorted(p.name for p in HELPER_DIR.glob("*.py"))
    assert files == ["argv_parity.py", "stats_check.py"], (
        f"unexpected helper inventory: {files}"
    )


@pytest.mark.parametrize("helper", [
    "argv_parity.py",
    "stats_check.py",
])
def test_helper_python_syntax_valid(helper):
    result = subprocess.run(
        [sys.executable, "-m", "py_compile", str(HELPER_DIR / helper)],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode == 0, result.stderr


def test_argv_parity_imports_real_provider_class():
    """argv_parity.py instantiates AiderProvider — if the class is
    renamed the helper would crash on the VM, blowing up phase 2."""
    text = (HELPER_DIR / "argv_parity.py").read_text()
    assert "from bterminal.providers.aider import AiderProvider" in text
    assert "build_argv" in text

    # And the provider still exposes both AiderProvider + build_argv
    prov = PROVIDER_PY.read_text()
    assert "class AiderProvider" in prov
    assert "def build_argv" in prov


def test_stats_check_imports_real_provider_class():
    text = (HELPER_DIR / "stats_check.py").read_text()
    assert "from bterminal.providers.aider import AiderProvider" in text
    for symbol in ("session_log_glob", "parse_session_stats"):
        assert symbol in text, f"stats_check no longer calls {symbol}"

    prov = PROVIDER_PY.read_text()
    for symbol in ("session_log_glob", "parse_session_stats"):
        assert f"def {symbol}" in prov, (
            f"AiderProvider no longer defines {symbol}"
        )


def test_stats_check_emits_required_markers():
    """The bash runner greps for 'stats-check-ok' as the pass signal.
    A marker rename here would make phase 6 silently fail-open."""
    text = (HELPER_DIR / "stats_check.py").read_text()
    assert "stats-check-ok" in text
    # response_count + model lines are also grepped for visibility
    assert "response-count=" in text
    assert "model=" in text


# ─── Helper smoke tests against host repo ──────────────────────────────────


def test_argv_parity_helper_runs_against_real_provider():
    """Compute build_argv against the host's AiderProvider — proves
    the helper produces a valid shell-quoted argv. Runs even when
    aider isn't installed (helper falls back to shutil.which path).
    The helper MAY exit 2 if aider is genuinely missing — accept that
    as a soft pass since the bash runner phase 0 catches it earlier."""
    result = subprocess.run(
        [sys.executable, str(HELPER_DIR / "argv_parity.py"),
         str(REPO_ROOT), "/tmp/test-proj"],
        capture_output=True, text=True, timeout=10,
    )
    if result.returncode == 2 and "aider-binary-missing" in result.stderr:
        pytest.skip("aider not installed locally — bash runner will catch")
    assert result.returncode == 0, (
        f"argv_parity failed:\nstdout: {result.stdout}\nstderr: {result.stderr}"
    )
    # Output is shell-quoted argv — must contain the canonical flags
    assert "--model" in result.stdout
    assert "openai/qwen2.5-coder:0.5b" in result.stdout
    assert "--openai-api-base" in result.stdout
    assert "http://localhost:11434/v1" in result.stdout
    assert "--no-stream" in result.stdout
    assert "/tmp/test-proj" in result.stdout


def test_stats_check_helper_handles_missing_log_gracefully():
    """When the chat history doesn't exist, stats_check should fail
    fast with a clear assertion (not a NoneType crash). This covers
    the case where phase 3 spawn fails before the log is written."""
    result = subprocess.run(
        [sys.executable, str(HELPER_DIR / "stats_check.py"),
         str(REPO_ROOT), "/tmp/nonexistent-aider-project"],
        capture_output=True, text=True, timeout=10,
    )
    assert result.returncode != 0
    # Either AssertionError OR FileNotFoundError — both are clean failures
    combined = result.stdout + result.stderr
    assert "chat history not at expected path" in combined \
        or "session_log_glob returned None" in combined


# ─── #132 — large-project mode source-grep guards ─────────────────────────


def test_runner_supports_large_project_flag():
    """Pin: `--large-project` flag toggles `LARGE_PROJECT=true`
    in the runner. Without it, the synthesized 100k LOC repo
    setup never fires."""
    text = SCRIPT.read_text()
    assert "--large-project)" in text
    assert "LARGE_PROJECT=true" in text


def test_large_project_default_is_false():
    """Pin: `LARGE_PROJECT=false` is the default. Routine smokes
    don't pay the ~30s synthesized-repo setup cost."""
    text = SCRIPT.read_text()
    assert "LARGE_PROJECT=false" in text


def test_large_project_synthesizes_1000_source_files():
    """Pin: ~100k LOC = 1000 files × 100 lines each. Catches a
    refactor that drops the file count below 1000 (which would
    no longer stress aider's repo-map indexing)."""
    text = SCRIPT.read_text()
    # Loop 1..1000 over src/mod_$i.py
    assert "for i in \\$(seq 1 1000)" in text
    assert "src/mod_\\$i.py" in text or "src/mod_$i.py" in text


def test_large_project_creates_node_modules_for_gitignore_test():
    """Pin sub-mode (b): node_modules/ created with 100 packages
    + listed in .gitignore. Verifies aider respects gitignore
    when scanning the repo."""
    text = SCRIPT.read_text()
    assert "node_modules/" in text
    assert "echo 'node_modules/' > .gitignore" in text
    # 100 fake packages
    assert "for i in \\$(seq 1 100)" in text
    assert "node_modules/pkg-\\$i" in text


def test_large_project_creates_decoy_aider_md_for_discovery_test():
    """Pin sub-mode (c): root AIDER.md + nested decoy AIDER.md.
    The decoy verifies aider's cwd-only auto-discovery — when
    spawned in $PROJECT_REL, it picks the root AIDER.md, not
    the deep sibling at src/sub/deep/path/AIDER.md."""
    text = SCRIPT.read_text()
    assert "src/sub/deep/path" in text
    # Both AIDER.md files referenced
    assert "Root AIDER.md" in text
    assert "DECOY AIDER.md" in text


def test_large_project_phase_1_5_verifies_three_sub_modes():
    """Pin: phase 1.5 has explicit `(a)` `(b)` `(c)` sub-mode
    checks before spawning aider. Catches a refactor that drops
    one of the verifications."""
    text = SCRIPT.read_text()
    assert "phase 1.5 (a)" in text
    assert "phase 1.5 (b)" in text
    assert "phase 1.5 (c)" in text


def test_large_project_phase_1_5_runs_only_when_flag_active():
    """Pin: the phase-1.5 block is gated by
    `[[ "$LARGE_PROJECT" == true ]]` so non-large smokes don't
    waste time on irrelevant invariants."""
    text = SCRIPT.read_text()
    # Locate the [1.5] block
    block_idx = text.find('[1.5] Verifying large-project')
    assert block_idx > 0
    # Walk backwards to find the gating `if`
    preceding = text[:block_idx]
    last_if = preceding.rfind("if ")
    gating_block = preceding[last_if:block_idx]
    assert 'LARGE_PROJECT' in gating_block, (
        "[1.5] phase not gated by LARGE_PROJECT — would fire "
        "on regular smoke runs"
    )


def test_large_project_extends_aider_spawn_timeout_to_180s():
    """Pin: large-project spawn timeout is 180s (vs 90s for
    normal). Aider's repo-map indexing on 100k LOC takes longer
    than the small-project case."""
    text = SCRIPT.read_text()
    assert "SPAWN_TIMEOUT=180" in text
    # And the default 90 is still there for non-large mode
    assert "SPAWN_TIMEOUT=90" in text


def test_aider_spawn_phase_records_duration():
    """Pin: phase 3 records wall-clock duration via $SPAWN_START
    + date-diff. Lets users see startup time in test output —
    audit's '#132 startup time' headline metric."""
    text = SCRIPT.read_text()
    assert "SPAWN_START=$(date +%s)" in text
    assert "SPAWN_DURATION=" in text


def test_large_project_announces_startup_time_in_pass_message():
    """Pin: when --large-project, the phase 3 success line
    explicitly surfaces `startup time:` — without it, users
    couldn't see the metric without grepping logs."""
    text = SCRIPT.read_text()
    assert "large-project startup time:" in text


def test_node_modules_gitignore_check_uses_git_ls_files():
    """Pin: sub-mode (b) check uses `git ls-files node_modules/`
    + grep -q to verify gitignore actually skipped them.
    Without this, .gitignore could be present but ineffective
    (e.g. files added before gitignore was created)."""
    text = SCRIPT.read_text()
    submode_idx = text.find("submode-b")
    assert submode_idx > 0
    next_section = text.find("submode-c", submode_idx)
    submode_b_block = text[submode_idx:next_section]
    assert "git ls-files node_modules/" in submode_b_block
    assert "! git ls-files" in submode_b_block, (
        "sub-mode (b) doesn't NEGATE the git ls-files check — "
        "would pass even when node_modules WERE tracked"
    )


def test_decoy_aider_md_check_compares_content_distinct():
    """Pin: sub-mode (c) verifies the decoy file's content
    differs from the root's. If they're identical, the test
    can't distinguish which one aider picks.

    The shell snippet uses `test "$ROOT_AIDER" != "$DEEP_AIDER"`
    nested inside ssh-quoted bash, which double-escapes the
    quotes + dollar signs. Match on the loose substring
    pattern + presence of `!=` to avoid escape brittleness."""
    text = SCRIPT.read_text()
    submode_idx = text.find("submode-c")
    next_section = text.find("# ─── Phase 2", submode_idx)
    submode_c_block = text[submode_idx:next_section]
    assert "ROOT_AIDER" in submode_c_block
    assert "DEEP_AIDER" in submode_c_block
    # Inequality test — both vars compared
    assert "ROOT_AIDER" in submode_c_block
    assert "!=" in submode_c_block, (
        "sub-mode (c) doesn't compare ROOT vs DEEP — both files "
        "could be identical and test still passes"
    )


# ─── Opt-in real-VM run ────────────────────────────────────────────────────


@pytest.mark.skipif(
    os.environ.get("BTERMINAL_VM_TESTS") != "1"
        or os.environ.get("BTERMINAL_OLLAMA_AVAILABLE") != "1",
    reason="needs BTERMINAL_VM_TESTS=1 + BTERMINAL_OLLAMA_AVAILABLE=1",
)
def test_aider_real_model_smoke_passes():
    """End-to-end ollama+qwen+aider on the VM. ~1 min runtime. Catches:
      - model dispatch broken
      - OpenAI-compat endpoint shifted under us
      - --no-stream regression in newer aider
      - parse_session_stats regex breaks on real-world markdown
    Skipped by default (double-gated by env vars)."""
    result = subprocess.run(
        ["bash", str(SCRIPT)],
        capture_output=True, text=True, timeout=300,
    )
    assert result.returncode == 0, (
        f"aider real-model E2E failed exit={result.returncode}\n"
        f"--- stdout ---\n{result.stdout[-3000:]}\n"
        f"--- stderr ---\n{result.stderr[-1500:]}"
    )
