"""Aider context file (AIDER.md) loading verification (#24 / #96).

Covers three claims from the auto-trigger plan:

  (a) Aider auto-loads AIDER.md from project_dir without BT having to
      pass an explicit --read flag. Pin: build_argv carries project_dir
      positionally so aider's cwd-based AIDER.md auto-discovery works.

  (b) When AIDER.md is missing but CLAUDE.md exists, BT auto-symlinks
      AIDER.md → CLAUDE.md via ensure_context_files_for_all_providers
      (#92). End-to-end: AIDER.md resolves to CLAUDE.md's content.

  (c) BT's intro_prompt (composed by _compute_intro_prompt_for_tab)
      reaches aider's context window. Verify the canonical header text
      'Project name in ctx/tasks: <name>' is present + the Aider
      long_label reaches the BTerminal header sentence (not stale
      'Claude' from a hardcoded default).

Manual VM smoke (project z CLAUDE.md → BT spawn aider → 'what is in
CLAUDE.md' → response includes content) is documented in
tests/manual/README.md (referenced via test_aider_real_model.sh from
#89). Headless tests below pin every observable component up to the
point of the actual qwen-coder dispatch.
"""
from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent

from bterminal.ctx.helpers import ensure_context_files_for_all_providers
from bterminal.providers import (
    ProviderRegistry,
    load_providers_config,
    reset_registry,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


# ─── (a) Aider auto-loads AIDER.md from cwd via positional argv ───────────


def test_aider_intro_prompt_mode_is_stdin_feed():
    """Aider's intro_prompt mode is 'stdin_feed' — BT injects the
    intro through the PTY after spawn, NOT through argv. This is
    different from Claude (positional) and Copilot (flag-based).

    Without this, build_argv would either swallow the intro
    (regression) or pass it as a positional aider treats as a
    project path (which would crash)."""
    reg = ProviderRegistry(config=load_providers_config())
    spec = reg.get("aider")._argv_spec
    assert spec.get("intro_prompt_mode") == "stdin_feed", (
        f"aider intro_prompt_mode drifted: {spec.get('intro_prompt_mode')!r}"
    )


def test_aider_build_argv_includes_project_dir_for_aider_md_discovery():
    """Aider's auto-discovery of AIDER.md depends on cwd containing
    the file. BT's spawn passes project_dir positionally, putting
    aider's cwd inside the project — that's how aider finds
    AIDER.md without a --read flag.

    If a refactor ever drops project_dir from argv (e.g. someone
    'simplifies' to rely purely on Popen cwd= kwarg), aider would
    have to be invoked with cwd=project_dir to keep auto-discovery
    working. Pin the positional argv contract so that path stays
    explicit."""
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    aider._binary_spec["binary"] = "/tmp/aider"  # noqa: SLF001
    argv = aider.build_argv(
        {"project_dir": "/tmp/myproj", "provider_options": {}},
        intro_prompt="hello",
    )
    # project_dir is the LAST argv element (positional)
    assert argv[-1] == "/tmp/myproj", (
        f"aider argv lost positional project_dir: {argv}"
    )
    # And intro_prompt MUST NOT be in argv (stdin_feed mode)
    assert "hello" not in argv, (
        f"aider argv carries intro (stdin_feed regression): {argv}"
    )


def test_aider_does_not_use_explicit_read_flag():
    """Aider relies on auto-discovery, not --read. Pin that BT
    doesn't sneak a --read flag in (would conflict with aider's own
    config search and could double-load)."""
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    aider._binary_spec["binary"] = "/tmp/aider"  # noqa: SLF001
    argv = aider.build_argv(
        {"project_dir": "/tmp/myproj", "provider_options": {}},
        intro_prompt="",
    )
    assert "--read" not in argv, (
        f"aider argv unexpectedly uses --read: {argv}"
    )


# ─── (b) AIDER.md missing + CLAUDE.md present → auto-symlink (#92) ───────


def test_missing_aider_md_gets_symlinked_to_claude_md(tmp_path):
    """Project has CLAUDE.md but no AIDER.md → ensure_context_files_
    for_all_providers creates AIDER.md as symlink → CLAUDE.md.
    Aider's auto-load then reads CLAUDE.md's content as if it were
    AIDER.md. Same content, no double-maintenance burden on the
    user."""
    claude_content = (
        "# myproj context\n\n"
        "Backend: Python 3.12 + FastAPI\n"
        "Frontend: React 19 + Vite\n"
        "Database: PostgreSQL 16\n"
    )
    (tmp_path / "CLAUDE.md").write_text(claude_content)
    assert not (tmp_path / "AIDER.md").exists()

    results = ensure_context_files_for_all_providers(tmp_path)
    assert results.get("AIDER.md") == "symlink"
    aider_md = tmp_path / "AIDER.md"
    assert aider_md.is_symlink()
    # End-to-end content roundtrip
    assert aider_md.read_text() == claude_content
    # And specifically: aider auto-loading AIDER.md sees the project
    # context that BT generated for Claude.
    assert "FastAPI" in aider_md.read_text()
    assert "PostgreSQL" in aider_md.read_text()


def test_existing_aider_md_left_alone_no_clobber(tmp_path):
    """If user has hand-authored AIDER.md (because they want
    Aider-specific instructions different from CLAUDE.md), the
    auto-symlink path leaves it untouched."""
    (tmp_path / "CLAUDE.md").write_text("# Claude-specific context")
    user_aider_content = (
        "# Aider-specific instructions\n\n"
        "When editing Python, prefer functional style.\n"
    )
    (tmp_path / "AIDER.md").write_text(user_aider_content)

    results = ensure_context_files_for_all_providers(tmp_path)
    assert results.get("AIDER.md") == "exists"
    # Content untouched — user's customization preserved
    assert (tmp_path / "AIDER.md").read_text() == user_aider_content
    assert not (tmp_path / "AIDER.md").is_symlink()


def test_no_claude_md_means_no_aider_md_either(tmp_path):
    """No CLAUDE.md to mirror → no AIDER.md materialized. Caller
    (BT spawn path) catches no_source and proceeds without context —
    aider runs but auto-discovery finds no AIDER.md, just like
    running aider directly without prior setup."""
    results = ensure_context_files_for_all_providers(tmp_path)
    assert results.get("AIDER.md") == "no_source"
    assert not (tmp_path / "AIDER.md").exists()


def test_aider_capability_context_file_equals_aider_md():
    """Pin the source-of-truth: aider's capabilities.context_file is
    'AIDER.md'. Without this, ensure_context_files_for_all_providers
    wouldn't know to mirror under that name, breaking flow (b)."""
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    assert aider.capabilities.context_file == "AIDER.md"


# ─── (c) intro_prompt computed reaches aider's context window ─────────────


def _seed_ctx_db_with_project(tmp_path: Path, project_name: str,
                                project_dir: str) -> Path:
    """Minimal CTX DB with one registered project so
    _resolve_ctx_project_name returns `project_name` for `project_dir`.
    Returns the patched CTX_DB path so tests can monkeypatch it."""
    ctx_db = tmp_path / "context.db"
    conn = sqlite3.connect(str(ctx_db))
    try:
        conn.executescript("""
            CREATE TABLE IF NOT EXISTS sessions (
                name TEXT PRIMARY KEY, description TEXT, work_dir TEXT,
                created_at TEXT DEFAULT (datetime('now'))
            );
            CREATE TABLE IF NOT EXISTS contexts (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                project TEXT NOT NULL, key TEXT NOT NULL, value TEXT NOT NULL,
                updated_at TEXT DEFAULT (datetime('now')),
                UNIQUE(project, key)
            );
        """)
        conn.execute(
            "INSERT INTO sessions (name, description, work_dir) "
            "VALUES (?, 'test project', ?)",
            (project_name, project_dir),
        )
        conn.commit()
    finally:
        conn.close()
    return ctx_db


def _stub_app_with_no_plugins():
    """Minimal `app` for _compute_intro_prompt_for_tab — needs
    `_plugins` (dict-like) + `sidecar_manifests` (dict-like). No
    plugins / no sidecars = bare intro prompt."""
    return SimpleNamespace(_plugins={}, sidecar_manifests={})


def test_compute_intro_prompt_uses_aider_long_label_in_header(tmp_path,
                                                                monkeypatch):
    """The intro header sentence reads 'You are working inside
    BTerminal — an SSH/<provider_label> terminal …'. For an Aider
    tab the label MUST be 'Aider' (the provider's display.long_label),
    not the 'Claude' fallback. Without this, intro tells aider it's
    Claude — confusing context for the model."""
    project_dir = tmp_path / "myaiderproj"
    project_dir.mkdir()
    (project_dir / "CLAUDE.md").write_text("# context")

    ctx_db = _seed_ctx_db_with_project(
        tmp_path, "myaiderproj", str(project_dir))
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))

    # Stub the deep deps of _build_intro_prompt — fetch_ctx, rules,
    # tools — they hit subprocess which would slow us. Bare strings
    # are enough since we're testing the header composition shape.
    cc = "bterminal.ui.dialogs.claude_code"
    monkeypatch.setattr(f"{cc}._fetch_ctx_output", lambda _p: "")
    monkeypatch.setattr(f"{cc}._fetch_rules_block", lambda _p: "")
    monkeypatch.setattr(f"{cc}._read_global_rules", lambda: [])
    monkeypatch.setattr(f"{cc}._tools_help", lambda _p: "(tools)")

    from bterminal.helpers import _compute_intro_prompt_for_tab
    tab = SimpleNamespace(
        ai_config={
            "provider": "aider",
            "name": "MyAiderSession",
            "project_dir": str(project_dir),
        },
        enabled_plugins=None,
    )
    out = _compute_intro_prompt_for_tab(_stub_app_with_no_plugins(), tab)

    assert "Aider" in out, (
        "intro_prompt header lost Aider provider_label — "
        f"first 200 chars: {out[:200]!r}"
    )


def test_compute_intro_prompt_includes_project_name_marker(tmp_path,
                                                              monkeypatch):
    """The pinned phrase 'Project name in ctx/tasks: <name>' is what
    the auto-trigger plan asks to verify. It appears when there's no
    ctx_output (fresh project, no shared values yet) — pin that
    fallback path so the project name reaches the model regardless
    of ctx state."""
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    ctx_db = _seed_ctx_db_with_project(
        tmp_path, "myproj", str(project_dir))
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))

    cc = "bterminal.ui.dialogs.claude_code"
    # Empty ctx_output → fallback to 'Project name in ctx/tasks:' line
    monkeypatch.setattr(f"{cc}._fetch_ctx_output", lambda _p: "")
    monkeypatch.setattr(f"{cc}._fetch_rules_block", lambda _p: "")
    monkeypatch.setattr(f"{cc}._read_global_rules", lambda: [])
    monkeypatch.setattr(f"{cc}._tools_help", lambda _p: "(tools)")

    from bterminal.helpers import _compute_intro_prompt_for_tab
    tab = SimpleNamespace(
        ai_config={
            "provider": "aider",
            "name": "MyAiderSession",
            "project_dir": str(project_dir),
        },
        enabled_plugins=None,
    )
    out = _compute_intro_prompt_for_tab(_stub_app_with_no_plugins(), tab)
    assert "Project name in ctx/tasks: myproj" in out, (
        f"intro_prompt missing project_name marker. First 400 chars: "
        f"{out[:400]!r}"
    )


def test_compute_intro_prompt_with_ctx_output_includes_project_context_label(
        tmp_path, monkeypatch):
    """When ctx_output IS populated (project has shared context), the
    intro switches from 'Project name in ctx/tasks' to 'Project
    context (<name>):' followed by the dump. Both branches surface
    the project name to aider — pin both."""
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    ctx_db = _seed_ctx_db_with_project(
        tmp_path, "myproj", str(project_dir))
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))

    cc = "bterminal.ui.dialogs.claude_code"
    monkeypatch.setattr(f"{cc}._fetch_ctx_output",
                        lambda _p: "shared.api: https://api.example.com")
    monkeypatch.setattr(f"{cc}._fetch_rules_block", lambda _p: "")
    monkeypatch.setattr(f"{cc}._read_global_rules", lambda: [])
    monkeypatch.setattr(f"{cc}._tools_help", lambda _p: "(tools)")

    from bterminal.helpers import _compute_intro_prompt_for_tab
    tab = SimpleNamespace(
        ai_config={
            "provider": "aider",
            "name": "MyAiderSession",
            "project_dir": str(project_dir),
        },
        enabled_plugins=None,
    )
    out = _compute_intro_prompt_for_tab(_stub_app_with_no_plugins(), tab)
    assert "Project context (myproj):" in out, (
        f"intro_prompt missing 'Project context (myproj):' label. "
        f"Got: {out[:400]!r}"
    )
    assert "shared.api" in out, "ctx_output not embedded into intro"


def test_compute_intro_prompt_appends_custom_prompt_for_aider(tmp_path,
                                                                 monkeypatch):
    """If ai_config.prompt is set (user customized in dialog), it gets
    appended after the standard header. Aider parity with Claude."""
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    ctx_db = _seed_ctx_db_with_project(
        tmp_path, "myproj", str(project_dir))
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))

    cc = "bterminal.ui.dialogs.claude_code"
    monkeypatch.setattr(f"{cc}._fetch_ctx_output", lambda _p: "")
    monkeypatch.setattr(f"{cc}._fetch_rules_block", lambda _p: "")
    monkeypatch.setattr(f"{cc}._read_global_rules", lambda: [])
    monkeypatch.setattr(f"{cc}._tools_help", lambda _p: "(tools)")

    from bterminal.helpers import _compute_intro_prompt_for_tab
    tab = SimpleNamespace(
        ai_config={
            "provider": "aider",
            "name": "MyAiderSession",
            "project_dir": str(project_dir),
            "prompt": "TEST CUSTOM AIDER PROMPT",
        },
        enabled_plugins=None,
    )
    out = _compute_intro_prompt_for_tab(_stub_app_with_no_plugins(), tab)
    assert "TEST CUSTOM AIDER PROMPT" in out


# ─── End-to-end glue: AIDER.md mirror + intro_prompt parity ───────────────


def test_full_flow_aider_md_mirror_and_intro_prompt_carry_same_project(
        tmp_path, monkeypatch):
    """End-to-end: a project_dir with CLAUDE.md + ctx-registered
    project → BT auto-creates AIDER.md (#92) AND
    _compute_intro_prompt_for_tab returns text mentioning the
    project name. Both ends of the context delivery pipeline reach
    Aider:
      - aider auto-loads AIDER.md → sees CLAUDE.md content via symlink
      - BT feeds intro_prompt via stdin_feed → aider sees ctx state

    Catches: any single-side regression where one path works but
    the other silently drops the project context."""
    project_dir = tmp_path / "myproj"
    project_dir.mkdir()
    claude_md = project_dir / "CLAUDE.md"
    claude_md.write_text(
        "# myproj\n\n"
        "Goal: build a multi-tenant SaaS for accountants.\n"
    )

    # Step 1: simulate ctx wizard finalize → mirror context files
    ensure_context_files_for_all_providers(project_dir)
    aider_md = project_dir / "AIDER.md"
    assert aider_md.is_symlink()
    assert "multi-tenant SaaS" in aider_md.read_text()

    # Step 2: simulate BT spawning aider → compute intro_prompt
    ctx_db = _seed_ctx_db_with_project(
        tmp_path, "myproj", str(project_dir))
    monkeypatch.setattr("bterminal.helpers.CTX_DB", str(ctx_db))
    monkeypatch.setattr("bterminal.ctx.helpers.CTX_DB", str(ctx_db))
    cc = "bterminal.ui.dialogs.claude_code"
    monkeypatch.setattr(f"{cc}._fetch_ctx_output", lambda _p: "")
    monkeypatch.setattr(f"{cc}._fetch_rules_block", lambda _p: "")
    monkeypatch.setattr(f"{cc}._read_global_rules", lambda: [])
    monkeypatch.setattr(f"{cc}._tools_help", lambda _p: "(tools)")

    from bterminal.helpers import _compute_intro_prompt_for_tab
    tab = SimpleNamespace(
        ai_config={
            "provider": "aider",
            "name": "MyAiderSession",
            "project_dir": str(project_dir),
        },
        enabled_plugins=None,
    )
    out = _compute_intro_prompt_for_tab(_stub_app_with_no_plugins(), tab)

    # intro_prompt names the project — confirms ctx side of pipeline
    assert "myproj" in out
    # And uses the Aider label (not stale 'Claude')
    assert "Aider" in out


# ─── Capability dispatch: context_file_cumulative semantics ──────────────


def test_aider_context_file_cumulative_is_false():
    """Aider's context_file_cumulative=False means BT replaces the
    AIDER.md mirror on each ctx wizard run rather than appending.
    Pin so it doesn't drift to True (which would conflict with the
    symlink-based mirror — appending to a symlink target would
    silently mutate CLAUDE.md)."""
    reg = ProviderRegistry(config=load_providers_config())
    aider = reg.get("aider")
    assert aider.capabilities.context_file_cumulative is False
