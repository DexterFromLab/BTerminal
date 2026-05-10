"""E2E test for BUG#3 — Aider session doesn't receive active rules at spawn.

User report (manual QA, 2026-05-10): with active rules in the ctx
DB for a project, an aider tab spawned via BT does not have those
rules in its context. The user observed both 'context not loaded'
(BUG#2) and 'rules not working' (BUG#3) simultaneously.

Existing path (terminal_tab.py:_do_inject_rules) feeds the rules
block into the PTY via `feed_child` AFTER spawn — but only at the
periodic `inject_every` boundary (default 20 prompts). On a fresh
session there's no rule injection until the user has typed 20+
prompts, AND the regression suite caught
`test_aider_rules_inject_fires_after_inject_every_threshold` already
failing (rules pending cleared but no rules_inject event recorded).

Task #5 specifies the fix shape: rules must reach aider AT SPAWN
TIME via `--read <rules_file>` (or `--message`). This is
architecturally cleaner because:
  - aider treats --read content as readonly conventions in its
    system prompt — the LLM sees rules from prompt #1, not #20+
  - works identically when the user opens a tab and immediately asks
    a question (the PTY-feed approach misses this case)
  - inspectable via /proc/<pid>/cmdline for diagnostic / smoke tests

Fix sketch (for next session):
  1. At spawn, if `_resolve_ctx_project_name(project_dir)` resolves
     to a registered ctx project AND `ctx rules inject <name>`
     returns non-empty, BT writes the block to a per-session temp
     file (e.g. /tmp/_bt_aider_rules_<id>.md) and stores the path
     in `config["provider_options"]["rules_file"]`.
  2. `AiderProvider.build_argv` reads that key (when present) and
     appends `--read <path>` before the positional cwd arg.
  3. Tab close cleans up the temp file.

Both pieces are pinned below — layer 1 is the provider contract,
layer 2 is the spawn-time integration.
"""
from __future__ import annotations

import os
import shutil
import subprocess
import sys
import tempfile
from pathlib import Path
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent.parent

from bterminal.providers import (  # noqa: E402
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


def _aider_provider():
    cfg = load_providers_config()
    reg = ProviderRegistry(cfg)
    return reg.get("aider")


# ── Layer 1 — Provider contract: rules_file → --read in argv ─────────────


def test_build_argv_adds_read_for_rules_file_in_provider_options(tmp_path):
    """Pin: when caller pre-materializes rules to a file and sets
    `provider_options.rules_file` to that path, build_argv must
    surface it as `--read <path>`. This is the seam through which
    the spawn helper injects rules without coupling provider code
    to the ctx CLI."""
    rules_file = tmp_path / "_bt_aider_rules_test.md"
    rules_file.write_text(
        "# Active rules for project test_proj\n"
        "- ALWAYS use Polish in code comments\n"
    )

    provider = _aider_provider()
    with patch.object(provider, "find_binary", return_value="/usr/bin/aider"):
        argv = provider.build_argv(
            {
                "project_dir": str(tmp_path),
                "provider_options": {"rules_file": str(rules_file)},
            },
            intro_prompt="",
        )

    read_targets = [
        argv[i + 1] for i, t in enumerate(argv)
        if t == "--read" and i + 1 < len(argv)
    ]
    assert read_targets, (
        f"build_argv did NOT include --read for rules_file. "
        f"argv: {argv}"
    )
    assert str(rules_file) in read_targets, (
        f"--read targets {read_targets} don't include rules file "
        f"{rules_file}"
    )


def test_build_argv_no_read_when_rules_file_absent_in_options(tmp_path):
    """Negative: no rules_file in provider_options → no --read for
    rules. (Note: --read for AIDER.md from BUG#2 is a separate
    concern; this test only checks rules-related --read.)"""
    provider = _aider_provider()
    with patch.object(provider, "find_binary", return_value="/usr/bin/aider"):
        argv = provider.build_argv(
            {
                "project_dir": str(tmp_path),
                "provider_options": {},
            },
            intro_prompt="",
        )

    # If --read appears, it must NOT point at a path containing
    # 'rules' or a temp file path. AIDER.md/CLAUDE.md auto-attach
    # (BUG#2 fix) is OK and orthogonal.
    read_targets = [
        argv[i + 1] for i, t in enumerate(argv)
        if t == "--read" and i + 1 < len(argv)
    ]
    rules_targets = [p for p in read_targets if "rules" in p.lower()]
    assert not rules_targets, (
        f"--read referenced a rules-like file but rules_file wasn't "
        f"set in options. Spurious targets: {rules_targets}"
    )


# ── Layer 2 — Spawn integration: ctx → rules_file → build_argv ──────────


def test_spawn_pipeline_materializes_rules_and_passes_to_argv(tmp_path, monkeypatch):
    """Pin: `_build_spawn_script` (the seam called by
    `spawn_ai_cli`) must recognise that the project has active ctx
    rules and write them to a temp file before calling build_argv.

    The test mocks `ctx rules inject <project>` to return a known
    block, then asserts the bash script:
      a) contains `--read /some/path` for a temp rules file
      b) the temp file's content matches the mocked block
    """
    from bterminal.ui.terminal_tab import TerminalTab

    rules_block = (
        "════════════════════════════════════════════════════\n"
        "PRZYPOMNIENIE REGUŁ [test_proj]\n"
        "════════════════════════════════════════════════════\n"
        "• ALWAYS use Polish in code comments\n"
    )

    project_dir = tmp_path / "test_proj"
    project_dir.mkdir()

    # Mock subprocess.run for `ctx rules inject` so the spawn helper
    # gets a non-empty block. Other subprocess.run calls fall through
    # to the real one.
    real_run = subprocess.run

    def fake_run(argv, *args, **kwargs):
        if argv[:3] == ["ctx", "rules", "inject"]:
            return subprocess.CompletedProcess(
                argv, 0, stdout=rules_block, stderr="",
            )
        return real_run(argv, *args, **kwargs)

    monkeypatch.setattr(subprocess, "run", fake_run)

    # Mock _resolve_ctx_project_name to return a known name (avoids
    # ctx DB dependency)
    import bterminal.ctx.helpers as ctx_helpers
    monkeypatch.setattr(
        ctx_helpers, "_resolve_ctx_project_name",
        lambda d: "test_proj",
    )

    provider = _aider_provider()
    with patch.object(provider, "find_binary", return_value="/usr/bin/aider"):
        config = {
            "provider": "aider",
            "project_dir": str(project_dir),
            "provider_options": {},
        }
        # Static method — no instance needed
        script = TerminalTab._build_spawn_script(provider, config, "")

    # Assertion 1: script contains --read pointing at a file
    assert "--read" in script, (
        f"_build_spawn_script must inject --read for active rules "
        f"when ctx project resolves and has rules. Got script:\n"
        f"{script[:500]}"
    )
    # Extract --read target from the bash command
    import shlex
    tokens = shlex.split(script.split("\n")[0])
    read_idx = tokens.index("--read")
    rules_path = tokens[read_idx + 1]
    assert os.path.isfile(rules_path), (
        f"--read target {rules_path} must be a real file written "
        f"before spawn"
    )
    # Assertion 2: file content includes the mocked rules block
    content = Path(rules_path).read_text()
    assert "PRZYPOMNIENIE REGUŁ" in content, (
        f"rules file content doesn't include the ctx-injected block. "
        f"Got:\n{content[:300]}"
    )
    assert "Polish in code comments" in content


# ── Behavioural VM test — /proc cmdline inspection ───────────────────────


@pytest.mark.skipif(
    not (os.environ.get("BTERMINAL_E2E_VM") == "1"
         and Path("/tmp/aider_ctx_test").is_dir()),
    reason="VM-only behavioural test (set BTERMINAL_E2E_VM=1)",
)
def test_real_aider_cmdline_contains_read_for_rules_file():
    """Behavioural pin: spawn aider via BT REST in a project with
    active ctx rules → inspect /proc/<aider_pid>/cmdline → assert
    `--read <path>` present and the path's content matches
    `ctx rules inject <project>` output.

    This test is gated on `BTERMINAL_E2E_VM=1` because it requires
    a live BT process + ctx DB seeded with rules. Setup procedure
    is documented in `smoke-logs/bug3-aider-rules-spawn/EVIDENCE.md`."""
    pids = subprocess.run(
        ["pgrep", "-af", "aider"],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip().splitlines()
    aider_pids = [
        line.split()[0] for line in pids
        if "aider" in line and "test" not in line
    ]
    assert aider_pids, "no live aider process — spawn one before running"

    pid = aider_pids[0]
    cmdline = Path(f"/proc/{pid}/cmdline").read_bytes().decode().split("\x00")
    assert "--read" in cmdline, (
        f"aider /proc/{pid}/cmdline lacks --read flag. "
        f"cmdline: {cmdline}"
    )
    read_idx = cmdline.index("--read")
    target = cmdline[read_idx + 1]
    content = Path(target).read_text()
    # The rules file must contain content that matches ctx output.
    expected = subprocess.run(
        ["ctx", "rules", "inject", "aider_bug3_test"],
        capture_output=True, text=True, timeout=5,
    ).stdout.strip()
    assert expected and expected in content, (
        f"rules file at {target} doesn't match `ctx rules inject` "
        f"output. Expected snippet:\n{expected[:200]}\n"
        f"Got:\n{content[:300]}"
    )
