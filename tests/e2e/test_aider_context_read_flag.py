"""E2E test for BUG#2 — Aider session doesn't load AIDER.md/CLAUDE.md
context when project_dir contains them.

User report (manual QA, 2026-05-10): aider tab opened via BT shows
    Aider v0.86.2
    Model: openai/qwen2.5-coder:0.5b with whole edit format
    Git repo: .git with 0 files
    Repo-map: using 1024 tokens, auto refresh

— with NO line confirming AIDER.md was loaded. The user observed that
context wasn't reaching aider's session. Manual reproduction on VM
confirmed the cause.

Direct VM evidence captured 2026-05-10 (smoke-logs/bug2-aider-context/):
- aider_WITHOUT_read_BUG.txt: spawn aider in /tmp/aider_ctx_test
  (which contains AIDER.md), no --read flag → banner ends with
  "Repo-map: using 1024 tokens" — no AIDER.md mention.
- aider_WITH_read_FIXED.txt: same dir + AIDER.md, but with
  --read /tmp/aider_ctx_test/AIDER.md → banner now ends with
  "Added AIDER.md to the chat (read-only)." ← proof of fix shape.

Root cause: `bterminal/providers/aider.py:build_argv` passes
project_dir as a positional arg only. `tests/test_aider_context_file.py`
docstring claims "aider auto-loads AIDER.md from cwd" but **aider
0.86.2 does NOT auto-load AIDER.md** — it requires --read or --file
argv.

Fix shape (for the implementation task):
- In `build_argv`, when `project_dir` is set, check for AIDER.md
  (preferred) or CLAUDE.md (fallback) at that path. If present,
  append `--read <path>` to argv before the positional cwd arg.
- The dialogs.py wizard already creates AIDER.md (mirror of CLAUDE.md
  via task #113's bootstrap_provider_context_files). After this fix,
  every aider tab spawned with a context-bearing project_dir will
  show "Added AIDER.md to the chat (read-only)" in its banner.
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from types import SimpleNamespace
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


# ── Static / structural: build_argv must add --read for AIDER.md ─────────


def _aider_provider():
    cfg = load_providers_config()
    reg = ProviderRegistry(cfg)
    return reg.get("aider")


def test_build_argv_adds_read_flag_when_AIDER_md_in_project_dir(tmp_path):
    """Pin: aider 0.86.2 does NOT auto-discover AIDER.md from cwd
    (verified empirically on VM). BT must inject `--read AIDER.md`
    explicitly when the file exists in project_dir."""
    project_dir = tmp_path / "proj"
    project_dir.mkdir()
    aider_md = project_dir / "AIDER.md"
    aider_md.write_text(
        "# Test conventions\nMarker: ELEPHANT-CASTLE\n"
    )

    provider = _aider_provider()
    with patch.object(provider, "find_binary", return_value="/usr/bin/aider"):
        argv = provider.build_argv(
            {"project_dir": str(project_dir)},
            intro_prompt="",
        )

    # Find any --read occurrence pointing at our AIDER.md
    read_paths = []
    for i, tok in enumerate(argv):
        if tok == "--read" and i + 1 < len(argv):
            read_paths.append(argv[i + 1])

    assert read_paths, (
        f"build_argv produced no --read flag, but project_dir has "
        f"AIDER.md. argv was: {argv}\n"
        f"Without --read, aider 0.86.2 won't load the conventions file "
        f"(banner shows 'Repo-map: using 1024 tokens' but no "
        f"'Added AIDER.md to the chat (read-only).' line)."
    )
    assert any(str(aider_md) in p for p in read_paths), (
        f"--read present but doesn't reference AIDER.md path. "
        f"Got --read targets: {read_paths}, expected one of them to "
        f"end with {aider_md.name}"
    )


def test_build_argv_falls_back_to_CLAUDE_md_when_no_AIDER_md(tmp_path):
    """When only CLAUDE.md exists in project_dir, BT should still
    inject --read for it. The mirroring (task #113's
    bootstrap_provider_context_files) means most projects will have
    AIDER.md too, but not all — fallback path matters."""
    project_dir = tmp_path / "proj_claude_only"
    project_dir.mkdir()
    claude_md = project_dir / "CLAUDE.md"
    claude_md.write_text("# CLAUDE.md fallback\n")

    provider = _aider_provider()
    with patch.object(provider, "find_binary", return_value="/usr/bin/aider"):
        argv = provider.build_argv(
            {"project_dir": str(project_dir)},
            intro_prompt="",
        )

    read_args = [argv[i + 1] for i, t in enumerate(argv)
                 if t == "--read" and i + 1 < len(argv)]
    assert read_args, (
        f"no --read flag with CLAUDE.md present in project_dir. "
        f"argv: {argv}"
    )
    assert any("CLAUDE.md" in p for p in read_args), (
        f"--read should target CLAUDE.md as fallback, got: {read_args}"
    )


def test_build_argv_no_read_when_project_dir_has_no_context_files(tmp_path):
    """Negative: empty project_dir → no --read flag (don't pass
    --read /nonexistent/AIDER.md or aider will error out at startup)."""
    project_dir = tmp_path / "empty_proj"
    project_dir.mkdir()

    provider = _aider_provider()
    with patch.object(provider, "find_binary", return_value="/usr/bin/aider"):
        argv = provider.build_argv(
            {"project_dir": str(project_dir)},
            intro_prompt="",
        )

    assert "--read" not in argv, (
        f"--read should not appear when project_dir has neither "
        f"AIDER.md nor CLAUDE.md. argv: {argv}"
    )


def test_build_argv_no_read_when_project_dir_absent():
    """Negative: missing project_dir → still no --read."""
    provider = _aider_provider()
    with patch.object(provider, "find_binary", return_value="/usr/bin/aider"):
        argv = provider.build_argv({}, intro_prompt="")

    assert "--read" not in argv


# ── Behavioural: real aider on VM with/without --read confirms shape ────


@pytest.mark.skipif(
    not shutil.which("aider")
    and not (Path.home() / ".local" / "bin" / "aider").is_file(),
    reason="real aider binary not available",
)
def test_real_aider_banner_includes_added_AIDER_md_with_read_flag(tmp_path):
    """Behavioural pin: confirm the fix shape works against real
    aider 0.86.2. Spawn aider with `--read AIDER.md` and verify the
    banner contains 'Added AIDER.md to the chat (read-only)'.

    Without this line, aider isn't actually reading the file (the
    visible bug). With it, the file is loaded as readonly conventions.

    Note: this test does NOT assert what BT does — it asserts what
    aider DOES when given --read. So even before the BT fix, this
    test should pass (it pins aider's behaviour, not BT's). The
    static tests above pin BT's behaviour."""
    aider_bin = shutil.which("aider") or str(
        Path.home() / ".local" / "bin" / "aider"
    )
    if not Path(aider_bin).is_file():
        pytest.skip(f"aider binary not found at {aider_bin}")

    project_dir = tmp_path / "aider_real_ctx"
    project_dir.mkdir()
    aider_md = project_dir / "AIDER.md"
    aider_md.write_text(
        "# Test\nMarker: ELEPHANT-CASTLE-9527\n"
    )

    # Spawn aider with --read; --message "exit" makes it leave after
    # banner; --yes-always auto-confirms the "create git repo?" prompt.
    result = subprocess.run(
        [aider_bin,
         "--no-stream", "--no-show-model-warnings", "--no-fancy-input",
         "--yes-always",
         "--read", str(aider_md),
         "--model", "openai/qwen2.5-coder:0.5b",
         "--openai-api-base", "http://localhost:11434/v1",
         "--openai-api-key", "dummy",
         "--message", "exit"],
        cwd=str(project_dir),
        capture_output=True, text=True, timeout=45,
    )
    out = result.stdout + result.stderr

    # Acceptance from task description: 'Repo-map: using' line + the
    # 'Added AIDER.md' line must be present.
    assert re.search(r"Repo-map: using \d+ tokens", out), (
        f"aider banner missing 'Repo-map: using …' line. Output:\n{out[:500]}"
    )
    assert "Added AIDER.md to the chat (read-only)" in out, (
        f"aider with --read did NOT confirm AIDER.md load. This means "
        f"either aider's contract changed (banner text drifted) or "
        f"the spawn went wrong. Output:\n{out[:500]}"
    )
    # And the inverse: '0 files' should appear in repo-map line —
    # which is fine because AIDER.md is read-only context, not a
    # repo-tracked file. (User's bug isn't 0-files; it's no
    # AIDER.md mention at all.)


@pytest.mark.skipif(
    not shutil.which("aider")
    and not (Path.home() / ".local" / "bin" / "aider").is_file(),
    reason="real aider binary not available",
)
def test_real_aider_banner_LACKS_added_AIDER_md_without_read_flag(tmp_path):
    """Negative behavioural pin: confirm the BUG today. Without
    --read, aider 0.86.2 does NOT print 'Added AIDER.md' even though
    AIDER.md is in cwd. This is what makes BUG#2 a real bug —
    aider doesn't auto-discover the conventions file the way some
    older versions or docs suggest."""
    aider_bin = shutil.which("aider") or str(
        Path.home() / ".local" / "bin" / "aider"
    )
    if not Path(aider_bin).is_file():
        pytest.skip(f"aider binary not found at {aider_bin}")

    project_dir = tmp_path / "aider_real_ctx_no_read"
    project_dir.mkdir()
    (project_dir / "AIDER.md").write_text("# Test\n")

    result = subprocess.run(
        [aider_bin,
         "--no-stream", "--no-show-model-warnings", "--no-fancy-input",
         "--yes-always",
         "--model", "openai/qwen2.5-coder:0.5b",
         "--openai-api-base", "http://localhost:11434/v1",
         "--openai-api-key", "dummy",
         "--message", "exit"],
        cwd=str(project_dir),
        capture_output=True, text=True, timeout=45,
    )
    out = result.stdout + result.stderr

    assert re.search(r"Repo-map: using \d+ tokens", out), (
        "banner missing repo-map line — aider may not have started"
    )
    assert "Added AIDER.md" not in out, (
        f"AIDER.md auto-discovery DID work without --read on this "
        f"aider version — that contradicts the bug premise. If this "
        f"assertion fails, BUG#2 may be obsolete or aider behaviour "
        f"changed. Output:\n{out[:500]}"
    )
