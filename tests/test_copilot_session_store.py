"""Tests for Copilot SQLite session-store reader — T4.5.

Verifies the read-only client behaves correctly against synthetic
session-store.db fixtures (FTS5-enabled). Schema mirrors what
docs/cli-provider-abstraction-analysis.md describes — TODO(L2)
replace fixtures with a real Copilot session capture once a
subscription is available.
"""
from __future__ import annotations

import sqlite3

import pytest

from bterminal.providers.copilot_session_store import (
    CopilotSession,
    CopilotSessionStore,
    create_synthetic_session_store,
)


@pytest.fixture
def empty_db(tmp_path):
    """Empty session-store.db with valid schema, zero rows."""
    db = tmp_path / "session-store.db"
    create_synthetic_session_store(db, sessions=[])
    return db


@pytest.fixture
def populated_db(tmp_path):
    """Three sessions with varied last_active_at + searchable content."""
    db = tmp_path / "session-store.db"
    create_synthetic_session_store(db, sessions=[
        {
            "id": "uuid-newest",
            "summary": "Refactor user service auth flow",
            "repo": "myapp",
            "branch": "feat/auth-rewrite",
            "started_at": 1_700_000_000.0,
            "last_active_at": 1_700_010_000.0,
            "turn_count": 12,
            "content": "user clicked refactor authentication module",
        },
        {
            "id": "uuid-middle",
            "summary": "Fix flaky test in pytest suite",
            "repo": "myapp",
            "branch": "fix/flaky-test",
            "started_at": 1_700_000_000.0,
            "last_active_at": 1_700_005_000.0,
            "turn_count": 4,
            "content": "investigate intermittent pytest failure",
        },
        {
            "id": "uuid-oldest",
            "summary": "Add Slack notification integration",
            "repo": "myapp",
            "branch": "feat/slack",
            "started_at": 1_700_000_000.0,
            "last_active_at": 1_700_001_000.0,
            "turn_count": 8,
            "content": "implement slack webhook notifications",
        },
    ])
    return db


# ─── is_available ───────────────────────────────────────────────────────────

def test_is_available_returns_false_when_db_missing(tmp_path):
    store = CopilotSessionStore(str(tmp_path / "no-such.db"))
    assert store.is_available() is False


def test_is_available_returns_true_when_db_present(empty_db):
    store = CopilotSessionStore(str(empty_db))
    assert store.is_available() is True


# ─── list_sessions ──────────────────────────────────────────────────────────

def test_list_sessions_empty_when_db_missing(tmp_path):
    store = CopilotSessionStore(str(tmp_path / "no-such.db"))
    assert store.list_sessions() == []


def test_list_sessions_empty_when_no_rows(empty_db):
    store = CopilotSessionStore(str(empty_db))
    assert store.list_sessions() == []


def test_list_sessions_orders_by_last_active_desc(populated_db):
    """Newest activity first — matches typical session-picker UX."""
    store = CopilotSessionStore(str(populated_db))
    sessions = store.list_sessions()
    assert len(sessions) == 3
    ids = [s.id for s in sessions]
    assert ids == ["uuid-newest", "uuid-middle", "uuid-oldest"]


def test_list_sessions_returns_copilot_session_dataclass(populated_db):
    """Sanity that we return CopilotSession instances, not raw rows."""
    store = CopilotSessionStore(str(populated_db))
    s = store.list_sessions()[0]
    assert isinstance(s, CopilotSession)
    assert s.id == "uuid-newest"
    assert s.summary == "Refactor user service auth flow"
    assert s.repo == "myapp"
    assert s.branch == "feat/auth-rewrite"
    assert s.turn_count == 12


def test_list_sessions_respects_limit(populated_db):
    store = CopilotSessionStore(str(populated_db))
    assert len(store.list_sessions(limit=2)) == 2
    assert len(store.list_sessions(limit=1)) == 1


def test_list_sessions_returns_empty_when_table_missing(tmp_path):
    """Corrupt / future-version DB without `sessions` table → graceful []."""
    db = tmp_path / "session-store.db"
    conn = sqlite3.connect(str(db))
    conn.execute("CREATE TABLE wrong_name (x INTEGER)")
    conn.close()
    store = CopilotSessionStore(str(db))
    assert store.list_sessions() == []


# ─── search ─────────────────────────────────────────────────────────────────

def test_search_uses_fts5_index(populated_db):
    """Search 'refactor' matches uuid-newest's content row."""
    store = CopilotSessionStore(str(populated_db))
    results = store.search("refactor")
    ids = [s.id for s in results]
    assert "uuid-newest" in ids


def test_search_returns_empty_for_no_match(populated_db):
    store = CopilotSessionStore(str(populated_db))
    assert store.search("totally-nonexistent-keyword") == []


def test_search_returns_empty_for_blank_query(populated_db):
    store = CopilotSessionStore(str(populated_db))
    assert store.search("") == []
    assert store.search("   ") == []


def test_search_returns_empty_when_db_missing(tmp_path):
    store = CopilotSessionStore(str(tmp_path / "no-such.db"))
    assert store.search("refactor") == []


def test_search_returns_empty_on_invalid_fts5_syntax(populated_db):
    """Pathological FTS5 queries (unmatched quotes etc.) → graceful []."""
    store = CopilotSessionStore(str(populated_db))
    # FTS5 can't parse this — must not crash
    assert store.search('NEAR((') == []


def test_search_finds_multiple_matches(populated_db):
    """`pytest` matches uuid-middle's content; sanity that join works."""
    store = CopilotSessionStore(str(populated_db))
    results = store.search("pytest")
    assert len(results) >= 1
    assert any(s.id == "uuid-middle" for s in results)


# ─── get_session ────────────────────────────────────────────────────────────

def test_get_session_by_uuid(populated_db):
    store = CopilotSessionStore(str(populated_db))
    s = store.get_session("uuid-middle")
    assert s is not None
    assert s.id == "uuid-middle"
    assert s.summary == "Fix flaky test in pytest suite"


def test_get_session_returns_none_for_missing(populated_db):
    store = CopilotSessionStore(str(populated_db))
    assert store.get_session("does-not-exist") is None


def test_get_session_returns_none_when_db_missing(tmp_path):
    store = CopilotSessionStore(str(tmp_path / "no-such.db"))
    assert store.get_session("any") is None


# ─── Read-only protection ───────────────────────────────────────────────────

def test_writes_through_store_connection_raise(populated_db):
    """The store opens with mode=ro — any INSERT/UPDATE/DELETE raises.

    We can't easily call a write through the public API (none expose
    one), so we manually inspect the connection mode by attempting a
    write through `_open()`. This proves the readonly URI flag took
    effect."""
    store = CopilotSessionStore(str(populated_db))
    with store._open() as conn:
        with pytest.raises(sqlite3.OperationalError):
            conn.execute(
                "INSERT INTO sessions (id, summary) VALUES (?, ?)",
                ("attempt", "should fail"),
            )


# ─── create_synthetic_session_store helper ──────────────────────────────────

def test_synthetic_helper_creates_valid_schema(tmp_path):
    db = tmp_path / "session-store.db"
    create_synthetic_session_store(db, sessions=[
        {"id": "u1", "summary": "x", "started_at": 1.0,
         "last_active_at": 2.0, "turn_count": 3},
    ])
    # Confirm the table layout matches what the reader queries
    conn = sqlite3.connect(str(db))
    try:
        cols = [
            row[1] for row in
            conn.execute("PRAGMA table_info(sessions)").fetchall()
        ]
        assert "id" in cols
        assert "summary" in cols
        assert "last_active_at" in cols
        # FTS5 virtual table created
        ft_cols = [
            row[1] for row in
            conn.execute("PRAGMA table_info(search_index)").fetchall()
        ]
        assert "content" in ft_cols
    finally:
        conn.close()


def test_synthetic_helper_overwrites_existing_file(tmp_path):
    db = tmp_path / "session-store.db"
    create_synthetic_session_store(db, sessions=[{"id": "first"}])
    create_synthetic_session_store(db, sessions=[{"id": "second"}])
    store = CopilotSessionStore(str(db))
    ids = [s.id for s in store.list_sessions()]
    assert ids == ["second"]


# ─── Module exports ─────────────────────────────────────────────────────────

def test_exported_symbols():
    from bterminal.providers import copilot_session_store as mod
    assert "CopilotSession" in mod.__all__
    assert "CopilotSessionStore" in mod.__all__
    assert "create_synthetic_session_store" in mod.__all__
