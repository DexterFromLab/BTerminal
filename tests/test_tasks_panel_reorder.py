"""TDD: GTK TaskListPanel — reorder Up/Down buttons (written BEFORE implementation).

All tests here should FAIL until:
  - TaskListPanel gains ↑ and ↓ buttons in its button bar
  - A `_on_reorder_task(direction)` method (or equivalent) is added
  - The handler reads the selected task_id, finds its position-neighbour in
    the DB and swaps their positions (same logic as `tasks reorder` CLI)

Test tiers:
  (A) Source-level — no GTK import needed; check method/button presence in
      source text. Fast, always run.
  (B) DB-level — patch CTX_DB to a tmp db, call the handler directly without
      a real widget tree; verifies position-swap logic.
  (C) Widget-level — require DISPLAY; instantiate real TaskListPanel, walk
      widget tree to assert ↑/↓ buttons exist and are wired.
"""
from __future__ import annotations

import os
import sqlite3
import sys
from pathlib import Path
from unittest.mock import MagicMock, patch

import pytest

REPO_ROOT = Path(__file__).resolve().parent.parent
PANEL_SRC = REPO_ROOT / "bterminal" / "ui" / "panels" / "tasks.py"

if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

_HAS_DISPLAY = bool(os.environ.get("DISPLAY"))


# ─── Helpers ────────────────────────────────────────────────────────────


def _make_db(db_path: Path, project: str = "myproj",
             tasks: list[tuple] | None = None) -> None:
    """Seed a minimal CTX DB with position column + optional tasks.

    Also inserts the project into the sessions table so TaskListPanel's
    project combo (which reads from sessions) can find it.
    """
    conn = sqlite3.connect(str(db_path))
    conn.executescript("""
        CREATE TABLE tasks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            project TEXT NOT NULL,
            task_id TEXT NOT NULL,
            description TEXT NOT NULL,
            status TEXT DEFAULT 'open',
            position INTEGER,
            created_at TEXT DEFAULT (datetime('now')),
            updated_at TEXT DEFAULT (datetime('now')),
            UNIQUE(project, task_id)
        );
        CREATE TABLE task_config (project TEXT PRIMARY KEY, autorun INTEGER DEFAULT 0);
        CREATE TABLE task_claims (
            project TEXT NOT NULL, task_id TEXT NOT NULL, session_id TEXT NOT NULL,
            PRIMARY KEY (project, task_id, session_id)
        );
        CREATE TABLE sessions (name TEXT PRIMARY KEY, description TEXT, work_dir TEXT);
        CREATE TABLE contexts (
            id INTEGER PRIMARY KEY AUTOINCREMENT, project TEXT NOT NULL,
            key TEXT NOT NULL, value TEXT NOT NULL, UNIQUE(project, key)
        );
        CREATE TABLE shared (key TEXT PRIMARY KEY, value TEXT NOT NULL);
    """)
    # Register project in sessions so the panel's project combo populates it
    conn.execute(
        "INSERT OR IGNORE INTO sessions (name) VALUES (?)", (project,)
    )
    if tasks:
        for pos, (task_id, desc, status) in enumerate(tasks, start=1):
            conn.execute(
                "INSERT INTO tasks (project, task_id, description, status, position) "
                "VALUES (?, ?, ?, ?, ?)",
                (project, task_id, desc, status, pos),
            )
    conn.commit()
    conn.close()


def _get_order(db_path: Path, project: str) -> list[str]:
    conn = sqlite3.connect(str(db_path))
    rows = conn.execute(
        "SELECT task_id FROM tasks WHERE project = ? ORDER BY position NULLS LAST, task_id",
        (project,),
    ).fetchall()
    conn.close()
    return [r[0] for r in rows]


# ═══════════════════════════════════════════════════════════════════════
# Tier A: Source-level checks (no GTK needed)
# ═══════════════════════════════════════════════════════════════════════


def test_panel_source_has_reorder_method():
    """TaskListPanel source must define a reorder handler method."""
    src = PANEL_SRC.read_text()
    assert "_on_reorder_task" in src, (
        "TaskListPanel missing '_on_reorder_task' method — "
        "add it to handle Up/Down button clicks"
    )


def test_panel_source_has_up_button():
    """Source must reference an Up reorder button (↑ or label containing 'up')."""
    src = PANEL_SRC.read_text()
    has_arrow = "↑" in src
    has_label = "up" in src.lower() and ("btn_up" in src or "reorder" in src.lower())
    assert has_arrow or has_label, (
        "TaskListPanel source has no ↑ button for reorder — "
        "add btn_up connected to _on_reorder_task('up')"
    )


def test_panel_source_has_down_button():
    """Source must reference a Down reorder button (↓ or label containing 'down')."""
    src = PANEL_SRC.read_text()
    has_arrow = "↓" in src
    has_label = "down" in src.lower() and ("btn_down" in src or "reorder" in src.lower())
    assert has_arrow or has_label, (
        "TaskListPanel source has no ↓ button for reorder — "
        "add btn_down connected to _on_reorder_task('down')"
    )


def test_panel_source_reorder_calls_load_tasks():
    """After swapping positions, the handler must refresh the view via _load_tasks."""
    src = PANEL_SRC.read_text()
    reorder_idx = src.find("def _on_reorder_task")
    assert reorder_idx != -1, "_on_reorder_task not found in source"
    next_def = src.find("\n    def ", reorder_idx + 1)
    body = src[reorder_idx: next_def if next_def != -1 else len(src)]
    assert "_load_tasks" in body, (
        "_on_reorder_task must call self._load_tasks() after the swap"
    )


def test_panel_source_reorder_uses_parameterized_sql():
    """Reorder handler must use ? placeholders, not f-string SQL."""
    src = PANEL_SRC.read_text()
    reorder_idx = src.find("def _on_reorder_task")
    assert reorder_idx != -1, "_on_reorder_task not found"
    next_def = src.find("\n    def ", reorder_idx + 1)
    body = src[reorder_idx: next_def if next_def != -1 else len(src)]

    assert 'f"UPDATE' not in body, "f-string SQL in _on_reorder_task — injection risk"
    assert "f'UPDATE" not in body, "f-string SQL in _on_reorder_task — injection risk"
    assert "WHERE project = ? AND task_id = ?" in body, (
        "_on_reorder_task must use parameterized WHERE clause"
    )


# ═══════════════════════════════════════════════════════════════════════
# Tier B: DB-level logic tests (no GTK needed — patch CTX_DB + mock selection)
# ═══════════════════════════════════════════════════════════════════════


def _make_panel_with_db(db_path: Path):
    """Import TaskListPanel with CTX_DB patched to db_path.
    Requires DISPLAY; caller must skip otherwise.
    Returns (panel, app_mock).
    """
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk  # noqa: F401

    app = MagicMock()
    app.notebook.get_n_pages.return_value = 0

    with patch("bterminal.ui.panels.tasks.CTX_DB", str(db_path)):
        from bterminal.ui.panels.tasks import TaskListPanel
        panel = TaskListPanel(app)

    return panel, app


@pytest.mark.skipif(not _HAS_DISPLAY, reason="needs DISPLAY for GTK")
def test_reorder_up_swaps_positions_in_db(tmp_path):
    """Clicking ↑ on task '2' swaps its position with task '1' in DB."""
    db_path = tmp_path / ".claude-context" / "context.db"
    db_path.parent.mkdir()
    _make_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
    ])

    with patch("bterminal.ui.panels.tasks.CTX_DB", str(db_path)):
        from bterminal.ui.panels.tasks import TaskListPanel
        app = MagicMock()
        app.notebook.get_n_pages.return_value = 0
        panel = TaskListPanel(app)

        # Select task '2' in the TreeView
        for i, row in enumerate(panel.store):
            if row[1] == "2":
                panel.tree.get_selection().select_iter(panel.store.get_iter(str(i)))
                break

        panel._on_reorder_task("up")

    order = _get_order(db_path, "myproj")
    assert order.index("2") < order.index("1"), (
        f"After moving '2' up, expected '2' before '1', got order: {order}"
    )


@pytest.mark.skipif(not _HAS_DISPLAY, reason="needs DISPLAY for GTK")
def test_reorder_down_swaps_positions_in_db(tmp_path):
    """Clicking ↓ on task '1' swaps its position with task '2' in DB."""
    db_path = tmp_path / ".claude-context" / "context.db"
    db_path.parent.mkdir()
    _make_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
        ("3", "gamma", "open"),
    ])

    with patch("bterminal.ui.panels.tasks.CTX_DB", str(db_path)):
        from bterminal.ui.panels.tasks import TaskListPanel
        app = MagicMock()
        app.notebook.get_n_pages.return_value = 0
        panel = TaskListPanel(app)

        for i, row in enumerate(panel.store):
            if row[1] == "1":
                panel.tree.get_selection().select_iter(panel.store.get_iter(str(i)))
                break

        panel._on_reorder_task("down")

    order = _get_order(db_path, "myproj")
    assert order.index("2") < order.index("1"), (
        f"After moving '1' down, expected '2' before '1', got order: {order}"
    )


@pytest.mark.skipif(not _HAS_DISPLAY, reason="needs DISPLAY for GTK")
def test_reorder_up_on_first_task_is_noop(tmp_path):
    """Moving the first task up does not change DB order."""
    db_path = tmp_path / ".claude-context" / "context.db"
    db_path.parent.mkdir()
    _make_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
    ])

    original = _get_order(db_path, "myproj")

    with patch("bterminal.ui.panels.tasks.CTX_DB", str(db_path)):
        from bterminal.ui.panels.tasks import TaskListPanel
        app = MagicMock()
        app.notebook.get_n_pages.return_value = 0
        panel = TaskListPanel(app)

        for i, row in enumerate(panel.store):
            if row[1] == original[0]:
                panel.tree.get_selection().select_iter(panel.store.get_iter(str(i)))
                break

        panel._on_reorder_task("up")

    assert _get_order(db_path, "myproj") == original


@pytest.mark.skipif(not _HAS_DISPLAY, reason="needs DISPLAY for GTK")
def test_reorder_down_on_last_task_is_noop(tmp_path):
    """Moving the last task down does not change DB order."""
    db_path = tmp_path / ".claude-context" / "context.db"
    db_path.parent.mkdir()
    _make_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
    ])

    original = _get_order(db_path, "myproj")

    with patch("bterminal.ui.panels.tasks.CTX_DB", str(db_path)):
        from bterminal.ui.panels.tasks import TaskListPanel
        app = MagicMock()
        app.notebook.get_n_pages.return_value = 0
        panel = TaskListPanel(app)

        for i, row in enumerate(panel.store):
            if row[1] == original[-1]:
                panel.tree.get_selection().select_iter(panel.store.get_iter(str(i)))
                break

        panel._on_reorder_task("down")

    assert _get_order(db_path, "myproj") == original


@pytest.mark.skipif(not _HAS_DISPLAY, reason="needs DISPLAY for GTK")
def test_reorder_with_no_selection_is_noop(tmp_path):
    """Calling reorder with no row selected does nothing (no crash, no DB change)."""
    db_path = tmp_path / ".claude-context" / "context.db"
    db_path.parent.mkdir()
    _make_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "open"),
    ])

    original = _get_order(db_path, "myproj")

    with patch("bterminal.ui.panels.tasks.CTX_DB", str(db_path)):
        from bterminal.ui.panels.tasks import TaskListPanel
        app = MagicMock()
        app.notebook.get_n_pages.return_value = 0
        panel = TaskListPanel(app)
        panel.tree.get_selection().unselect_all()
        panel._on_reorder_task("up")

    assert _get_order(db_path, "myproj") == original


@pytest.mark.skipif(not _HAS_DISPLAY, reason="needs DISPLAY for GTK")
def test_reorder_skips_separator_row(tmp_path):
    """Selecting a separator row (task_id='') and calling reorder must be a no-op."""
    db_path = tmp_path / ".claude-context" / "context.db"
    db_path.parent.mkdir()
    _make_db(db_path, tasks=[
        ("1", "alpha", "open"),
        ("2", "beta", "done"),
    ])

    original = _get_order(db_path, "myproj")

    with patch("bterminal.ui.panels.tasks.CTX_DB", str(db_path)):
        from bterminal.ui.panels.tasks import TaskListPanel
        app = MagicMock()
        app.notebook.get_n_pages.return_value = 0
        panel = TaskListPanel(app)

        for i, row in enumerate(panel.store):
            if row[4]:  # is_separator column
                panel.tree.get_selection().select_iter(panel.store.get_iter(str(i)))
                break

        panel._on_reorder_task("down")

    assert _get_order(db_path, "myproj") == original


# ═══════════════════════════════════════════════════════════════════════
# Tier C: Widget-tree checks (needs DISPLAY)
# ═══════════════════════════════════════════════════════════════════════


def _collect_buttons(widget, out=None):
    """Recursively collect all Gtk.Button labels in a widget tree."""
    if out is None:
        out = []
    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk
    if isinstance(widget, Gtk.Button):
        lbl = widget.get_label() or ""
        out.append(lbl)
    if hasattr(widget, "get_children"):
        for child in widget.get_children():
            _collect_buttons(child, out)
    return out


@pytest.mark.skipif(not _HAS_DISPLAY, reason="needs DISPLAY for GTK")
def test_panel_has_up_button_in_widget_tree(tmp_path):
    """Real widget tree must contain a button with ↑ label."""
    db_path = tmp_path / ".claude-context" / "context.db"
    db_path.parent.mkdir()
    _make_db(db_path)

    import gi
    gi.require_version("Gtk", "3.0")
    with patch("bterminal.ui.panels.tasks.CTX_DB", str(db_path)):
        from bterminal.ui.panels.tasks import TaskListPanel
        app = MagicMock()
        app.notebook.get_n_pages.return_value = 0
        panel = TaskListPanel(app)

    labels = _collect_buttons(panel)
    assert any("↑" in lbl for lbl in labels), (
        f"No ↑ button found in TaskListPanel widget tree. Button labels: {labels}"
    )


@pytest.mark.skipif(not _HAS_DISPLAY, reason="needs DISPLAY for GTK")
def test_panel_has_down_button_in_widget_tree(tmp_path):
    """Real widget tree must contain a button with ↓ label."""
    db_path = tmp_path / ".claude-context" / "context.db"
    db_path.parent.mkdir()
    _make_db(db_path)

    import gi
    gi.require_version("Gtk", "3.0")
    with patch("bterminal.ui.panels.tasks.CTX_DB", str(db_path)):
        from bterminal.ui.panels.tasks import TaskListPanel
        app = MagicMock()
        app.notebook.get_n_pages.return_value = 0
        panel = TaskListPanel(app)

    labels = _collect_buttons(panel)
    assert any("↓" in lbl for lbl in labels), (
        f"No ↓ button found in TaskListPanel widget tree. Button labels: {labels}"
    )


@pytest.mark.skipif(not _HAS_DISPLAY, reason="needs DISPLAY for GTK")
def test_panel_up_down_buttons_have_tooltips(tmp_path):
    """↑ and ↓ buttons should have tooltips explaining reorder."""
    db_path = tmp_path / ".claude-context" / "context.db"
    db_path.parent.mkdir()
    _make_db(db_path)

    import gi
    gi.require_version("Gtk", "3.0")
    from gi.repository import Gtk

    with patch("bterminal.ui.panels.tasks.CTX_DB", str(db_path)):
        from bterminal.ui.panels.tasks import TaskListPanel
        app = MagicMock()
        app.notebook.get_n_pages.return_value = 0
        panel = TaskListPanel(app)

    def collect_buttons_with_tooltips(widget, out=None):
        if out is None:
            out = []
        if isinstance(widget, Gtk.Button):
            lbl = widget.get_label() or ""
            tip = widget.get_tooltip_text() or ""
            out.append((lbl, tip))
        if hasattr(widget, "get_children"):
            for child in widget.get_children():
                collect_buttons_with_tooltips(child, out)
        return out

    buttons = collect_buttons_with_tooltips(panel)
    up_btn = next(
        (t for lbl, t in buttons if "↑" in lbl), None
    )
    down_btn = next(
        (t for lbl, t in buttons if "↓" in lbl), None
    )
    assert up_btn is not None, "No ↑ button with tooltip found"
    assert down_btn is not None, "No ↓ button with tooltip found"
