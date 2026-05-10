"""Copilot SQLite session-store reader — T4.5 (optional).

GitHub Copilot CLI persists rich session metadata in
`~/.copilot/session-store.db` — a SQLite database with a `sessions`
table plus an FTS5 virtual table (`search_index`) for full-text
search across user prompts and assistant replies. This module
exposes a read-only client BTerminal uses to:

  - List recent Copilot sessions (id + auto-summary + repo + branch).
  - Run FTS5 search across session content.
  - Fetch one session's detail to feed into "Resume in BTerminal"
    (spawn a tab with `copilot --resume <uuid>`).

All connections open in `mode=ro` URI mode — BTerminal must never
mutate Copilot's state.

TODO(L2): once a Copilot subscription is available, capture a real
session-store.db, diff its schema against the queries below, and
adjust column names / table layout. The schema here mirrors what
docs/cli-provider-abstraction-analysis.md and jonmagic's blog post
describe (sessions / turns / checkpoints / session_files /
session_refs / search_index FTS5).
"""
from __future__ import annotations

import os
import sqlite3
from dataclasses import dataclass
from typing import Optional


_DEFAULT_DB_PATH = os.path.expanduser("~/.copilot/session-store.db")


@dataclass
class CopilotSession:
    """Subset of session-store.db columns relevant to BTerminal's UI.

    Other columns (workspace, args, …) load on-demand via get_session().
    The list / search views show only these basics so a 100-row
    picker stays compact.
    """

    id: str               # session UUID — used in `copilot --resume <id>`
    summary: str          # auto-generated session summary
    repo: str             # repo name (basename of cwd)
    branch: str           # git branch at session start
    started_at: float     # epoch seconds
    last_active_at: Optional[float] = None
    turn_count: int = 0


class CopilotSessionStore:
    """Read-only client for `~/.copilot/session-store.db`.

    Constructor accepts an explicit path (used by tests with synthetic
    fixtures). Production callers omit the arg and get the default
    user-level path.
    """

    def __init__(self, db_path: Optional[str] = None):
        self.db_path = os.path.expanduser(db_path or _DEFAULT_DB_PATH)

    def is_available(self) -> bool:
        """True iff the DB file is present. Doesn't open a connection."""
        return os.path.isfile(self.db_path)

    def _open(self) -> sqlite3.Connection:
        """Open in URI mode=ro so writes raise OperationalError."""
        uri = f"file:{self.db_path}?mode=ro"
        conn = sqlite3.connect(uri, uri=True, timeout=2.0)
        conn.row_factory = sqlite3.Row
        return conn

    def list_sessions(self, limit: int = 50) -> list[CopilotSession]:
        """Recent sessions ordered by last_active_at DESC.

        Returns [] when:
          - DB file doesn't exist (fresh install).
          - `sessions` table doesn't exist (corrupt / future schema).
          - Any sqlite3.Error during query.
        """
        if not self.is_available():
            return []
        try:
            with self._open() as conn:
                cursor = conn.execute(
                    "SELECT id, summary, repo, branch, "
                    "       started_at, last_active_at, turn_count "
                    "FROM sessions "
                    "ORDER BY COALESCE(last_active_at, 0) DESC "
                    "LIMIT ?",
                    (limit,),
                )
                return [self._row_to_session(row)
                        for row in cursor.fetchall()]
        except sqlite3.Error:
            return []

    def search(self, query: str, limit: int = 50) -> list[CopilotSession]:
        """FTS5 full-text search across the `search_index` virtual table.

        Joins matching session_id back into `sessions` so the picker
        receives the same `CopilotSession` shape as list_sessions().
        Returns [] on empty / blank query, missing table, or invalid
        FTS5 syntax (we don't surface the parser error to the user).
        """
        if not self.is_available() or not query.strip():
            return []
        try:
            with self._open() as conn:
                cursor = conn.execute(
                    "SELECT s.id, s.summary, s.repo, s.branch, "
                    "       s.started_at, s.last_active_at, s.turn_count "
                    "FROM search_index si "
                    "JOIN sessions s ON s.id = si.session_id "
                    "WHERE search_index MATCH ? "
                    "ORDER BY rank "
                    "LIMIT ?",
                    (query, limit),
                )
                return [self._row_to_session(row)
                        for row in cursor.fetchall()]
        except sqlite3.Error:
            return []

    def get_session(self, session_id: str) -> Optional[CopilotSession]:
        """Return one session by UUID, or None when missing / on error."""
        if not self.is_available():
            return None
        try:
            with self._open() as conn:
                row = conn.execute(
                    "SELECT id, summary, repo, branch, "
                    "       started_at, last_active_at, turn_count "
                    "FROM sessions WHERE id = ? LIMIT 1",
                    (session_id,),
                ).fetchone()
                return self._row_to_session(row) if row else None
        except sqlite3.Error:
            return None

    @staticmethod
    def _row_to_session(row) -> CopilotSession:
        return CopilotSession(
            id=row["id"],
            summary=(row["summary"] or "") if "summary" in row.keys() else "",
            repo=(row["repo"] or "") if "repo" in row.keys() else "",
            branch=(row["branch"] or "") if "branch" in row.keys() else "",
            started_at=row["started_at"] or 0,
            last_active_at=row["last_active_at"]
            if "last_active_at" in row.keys() else None,
            turn_count=(row["turn_count"] or 0)
            if "turn_count" in row.keys() else 0,
        )


# ─── Fixture helper for tests ───────────────────────────────────────────────

_SCHEMA_SQL = """
CREATE TABLE sessions (
    id              TEXT PRIMARY KEY,
    summary         TEXT,
    repo            TEXT,
    branch          TEXT,
    started_at      REAL,
    last_active_at  REAL,
    turn_count      INTEGER DEFAULT 0
);
CREATE VIRTUAL TABLE search_index USING fts5(session_id, content);
"""


def create_synthetic_session_store(path, sessions=()) -> None:
    """Build a session-store.db replica matching the documented schema.

    Used by tests + the bundled scenario fixture. `sessions` is an
    iterable of dicts with keys matching `CopilotSession` fields plus
    optional `content` (text indexed in search_index).

    TODO(L2): replace this synthetic schema with the real one captured
    from a live Copilot install once a subscription is available.
    Production callers never invoke this — only test fixtures do.
    """
    path = os.fspath(path)
    if os.path.exists(path):
        os.unlink(path)
    conn = sqlite3.connect(path)
    try:
        conn.executescript(_SCHEMA_SQL)
        for s in sessions:
            conn.execute(
                "INSERT INTO sessions "
                "(id, summary, repo, branch, started_at, "
                " last_active_at, turn_count) "
                "VALUES (?, ?, ?, ?, ?, ?, ?)",
                (
                    s["id"], s.get("summary", ""), s.get("repo", ""),
                    s.get("branch", ""), s.get("started_at", 0),
                    s.get("last_active_at"), s.get("turn_count", 0),
                ),
            )
            content = s.get("content")
            if content:
                conn.execute(
                    "INSERT INTO search_index (session_id, content) "
                    "VALUES (?, ?)",
                    (s["id"], content),
                )
        conn.commit()
    finally:
        conn.close()


__all__ = [
    "CopilotSession",
    "CopilotSessionStore",
    "create_synthetic_session_store",
]
