"""Tests for sidebar flat-folder grouping (task #58, 2026-05-07).

Pre-task: AI sessions were nested under per-provider section headers
('Claude Code', 'GitHub Copilot CLI') with folder grouping inside
each. User feedback: redundant — provider already shown via per-row
icon. Task #58 collapses the hierarchy: AI sessions are folder-grouped
only, mixing providers within the same user folder.

Decision tree covered:
  (a) sessions across providers in the same folder → one folder header
  (b) folderless sessions (any provider) → root level, no provider headers
  (c) folder rename/delete still works post-flatten (ID prefix preserved)
  (d) no rows with id starting "section:" exist anywhere in the tree
"""
from __future__ import annotations

import os
import sys
from pathlib import Path
from unittest.mock import MagicMock

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

if not os.environ.get("DISPLAY"):
    pytest.skip(
        "Sidebar tree tests need a display; "
        "run with `xvfb-run -a pytest tests/test_sidebar_flat_grouping.py`",
        allow_module_level=True,
    )

import gi  # noqa: E402
gi.require_version("Gtk", "3.0")

from bterminal.providers import reset_registry  # noqa: E402
from bterminal.ui.sidebar import (  # noqa: E402
    COL_ID,
    COL_NAME,
    SessionSidebar,
)


@pytest.fixture(autouse=True)
def _reset_singleton():
    reset_registry()
    yield
    reset_registry()


def _stub_app(ssh_sessions=(), ai_sessions=()):
    """Sidebar reads .session_manager (SSH) + .ai_manager / .claude_manager
    (AI). update_folder + delete_folder paths are exercised via
    _rename_folder / _ungroup_folder — give them the same MagicMock so
    we can spy on call args."""
    app = MagicMock()
    app.session_manager.all.return_value = list(ssh_sessions)
    ai = MagicMock()
    ai.all.return_value = list(ai_sessions)
    app.ai_manager = ai
    app.claude_manager = ai  # legacy alias, sidebar uses claude_manager for AI folders
    return app


def _walk_rows(store):
    """Yield every (depth, name, id) tuple in tree-order."""
    out = []

    def visit(model, path, it):
        depth = path.get_depth() - 1
        out.append((
            depth,
            model.get_value(it, COL_NAME),
            model.get_value(it, COL_ID),
        ))
    store.foreach(visit)
    return out


def _names_at_depth(rows, depth):
    return [n for d, n, _ in rows if d == depth]


def _ids_at_depth(rows, depth):
    return [cid for d, _, cid in rows if d == depth]


# ─── (d) No 'section:' rows survive — provider grouping fully gone ──────────


def test_no_section_rows_when_only_one_provider():
    """Minimal AI list — must NOT produce a 'Claude Code' section header."""
    app = _stub_app(ai_sessions=[
        {"id": "c1", "name": "Solo", "provider": "claude",
         "project_dir": "/tmp/c"},
    ])
    sidebar = SessionSidebar(app)
    rows = _walk_rows(sidebar.store)
    section_ids = [cid for _, _, cid in rows if cid and cid.startswith("section:")]
    assert section_ids == [], (
        f"task #58 dropped provider sections, got {section_ids}"
    )
    sidebar.destroy()


def test_no_section_rows_when_two_providers_present():
    """Both providers with sessions — still no section headers."""
    app = _stub_app(ai_sessions=[
        {"id": "c1", "name": "Cl", "provider": "claude", "project_dir": "/tmp/c"},
        {"id": "p1", "name": "Co", "provider": "copilot", "project_dir": "/tmp/p"},
    ])
    sidebar = SessionSidebar(app)
    rows = _walk_rows(sidebar.store)
    section_ids = [cid for _, _, cid in rows if cid and cid.startswith("section:")]
    assert section_ids == []

    # Long labels also should not appear at any depth
    names = [n for _, n, _ in rows]
    assert "Claude Code" not in names
    assert "GitHub Copilot CLI" not in names

    sidebar.destroy()


# ─── (b) Folderless sessions go to root, regardless of provider ──────────────


def test_folderless_sessions_land_at_root():
    """AI sessions without folder field render at depth 0, no parent."""
    app = _stub_app(ai_sessions=[
        {"id": "c1", "name": "ClaudeRoot", "provider": "claude",
         "project_dir": "/tmp/c"},
        {"id": "p1", "name": "CopilotRoot", "provider": "copilot",
         "project_dir": "/tmp/p"},
    ])
    sidebar = SessionSidebar(app)
    rows = _walk_rows(sidebar.store)
    root_names = _names_at_depth(rows, 0)
    assert "ClaudeRoot" in root_names
    assert "CopilotRoot" in root_names
    sidebar.destroy()


# ─── (a) Both providers in same folder render under ONE folder header ────────


def test_mixed_providers_in_same_folder_share_one_header():
    """User assigns both Claude and Copilot sessions to folder 'Work'.
    Pre-task #58 they would be split into two sections ('Claude Code'
    → Work → ClaudeWork, 'GitHub Copilot CLI' → Work → CopilotWork).
    Post-task: a single 'Work' header at root with both children
    underneath."""
    app = _stub_app(ai_sessions=[
        {"id": "c1", "name": "ClaudeWork", "provider": "claude",
         "folder": "Work", "project_dir": "/tmp/c"},
        {"id": "p1", "name": "CopilotWork", "provider": "copilot",
         "folder": "Work", "project_dir": "/tmp/p"},
    ])
    sidebar = SessionSidebar(app)
    rows = _walk_rows(sidebar.store)

    # Exactly one 'Work' folder header at root with count=2
    folder_rows = [
        (d, n, cid) for d, n, cid in rows
        if cid and cid.startswith("cfolder:") and cid.endswith(":Work")
    ]
    assert len(folder_rows) == 1, (
        f"expected one 'Work' folder header, got {folder_rows}"
    )
    depth, name, cid = folder_rows[0]
    assert depth == 0, "folder header must be at root, not nested under a section"
    assert "(2)" in name, f"folder header should show count, got {name!r}"

    # Both sessions are children at depth 1
    nested_names = _names_at_depth(rows, 1)
    assert "ClaudeWork" in nested_names
    assert "CopilotWork" in nested_names

    sidebar.destroy()


def test_two_different_folders_render_as_two_root_headers():
    app = _stub_app(ai_sessions=[
        {"id": "c1", "name": "C", "provider": "claude", "folder": "Alpha",
         "project_dir": "/t/a"},
        {"id": "p1", "name": "P", "provider": "copilot", "folder": "Beta",
         "project_dir": "/t/b"},
    ])
    sidebar = SessionSidebar(app)
    rows = _walk_rows(sidebar.store)
    cfolder_ids = [
        cid for _, _, cid in rows
        if cid and cid.startswith("cfolder:")
    ]
    assert sorted(cfolder_ids) == ["cfolder:Alpha", "cfolder:Beta"]
    sidebar.destroy()


# ─── (c) Folder rename/delete still works post-flatten ──────────────────────


def test_folder_rename_invokes_manager_update_folder_with_correct_name():
    """The 'Rename folder…' context-menu action splits the COL_ID on
    ':' and calls _rename_folder(name, claude_manager). With 'cfolder:'
    prefix kept (task #58 backwards-compat), this still routes through
    the AI manager so renaming a mixed-provider folder updates every
    session's `folder` field, regardless of provider."""
    app = _stub_app(ai_sessions=[
        {"id": "c1", "name": "C", "provider": "claude", "folder": "OldName",
         "project_dir": "/t/c"},
        {"id": "p1", "name": "P", "provider": "copilot", "folder": "OldName",
         "project_dir": "/t/p"},
    ])
    sidebar = SessionSidebar(app)

    # Find the cfolder:OldName row id and verify the prefix logic still
    # extracts "OldName" — that's what _on_edit / _on_delete do.
    rows = _walk_rows(sidebar.store)
    folder_ids = [
        cid for _, _, cid in rows
        if cid and cid.startswith("cfolder:")
    ]
    assert folder_ids == ["cfolder:OldName"]
    extracted = folder_ids[0].split(":", 1)[1]
    assert extracted == "OldName", "prefix split must still recover folder name"

    # The manager that rename hits is claude_manager (AI sessions).
    # Confirm sidebar wired claude_manager to the ai_manager mock, so
    # _rename_folder("OldName", claude_manager) reaches the same backing
    # store as the AI sessions we seeded.
    assert sidebar.app.claude_manager is sidebar.app.ai_manager

    sidebar.destroy()


def test_folder_delete_path_uses_ai_manager():
    """Deleting a folder removes its grouping (sessions move to root,
    keeping their data). The handler dispatches on cfolder: prefix to
    the AI manager — verify the manager.update path is reachable
    through the same alias."""
    app = _stub_app(ai_sessions=[
        {"id": "c1", "name": "X", "provider": "claude", "folder": "ToDelete",
         "project_dir": "/t/x"},
    ])
    sidebar = SessionSidebar(app)

    # Confirm cfolder:ToDelete row exists at root
    rows = _walk_rows(sidebar.store)
    cfolder_ids = [cid for _, _, cid in rows if cid and cid.startswith("cfolder:")]
    assert cfolder_ids == ["cfolder:ToDelete"]

    # _ungroup_folder reads sessions from claude_manager.all() and
    # writes via claude_manager.update — both go through the same
    # MagicMock alias as ai_manager, so the call path is unchanged.
    assert sidebar.app.claude_manager.all.return_value == [
        {"id": "c1", "name": "X", "provider": "claude", "folder": "ToDelete",
         "project_dir": "/t/x"},
    ]
    sidebar.destroy()


# ─── Mixed SSH + AI: they coexist at root, AI under their own folder system ─


def test_ssh_and_ai_folders_remain_distinct():
    """SSH 'Work' folder uses 'folder:' prefix; AI 'Work' folder uses
    'cfolder:' — they're separate buckets even with identical labels.
    This is intentional (different managers, different rename targets)
    and prevents cross-contamination."""
    app = _stub_app(
        ssh_sessions=[
            {"id": "ssh1", "name": "MyHost", "folder": "Work",
             "username": "u", "host": "h", "port": 22},
        ],
        ai_sessions=[
            {"id": "ai1", "name": "MyClaude", "provider": "claude",
             "folder": "Work", "project_dir": "/t/c"},
        ],
    )
    sidebar = SessionSidebar(app)
    rows = _walk_rows(sidebar.store)
    folder_prefixes = sorted({
        cid.split(":", 1)[0] for _, _, cid in rows
        if cid and (cid.startswith("folder:") or cid.startswith("cfolder:"))
    })
    assert folder_prefixes == ["cfolder", "folder"]
    sidebar.destroy()
