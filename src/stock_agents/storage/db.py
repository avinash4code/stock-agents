"""SQLite connection and schema migrations."""
from __future__ import annotations

import sqlite3
from pathlib import Path

# Append-only: never edit a migration that has shipped, add a new one. The schema version
# is the number of migrations applied, stored in `PRAGMA user_version`.
MIGRATIONS = [
    # 1: runs and reports
    """
    CREATE TABLE runs (
        id          INTEGER PRIMARY KEY,
        command     TEXT NOT NULL,
        stock       TEXT,
        started_at  TEXT NOT NULL,
        finished_at TEXT,
        status      TEXT NOT NULL DEFAULT 'running' CHECK (status IN ('running', 'ok', 'failed')),
        error       TEXT,
        provider    TEXT,
        model       TEXT,
        max_tokens  INTEGER
    );

    CREATE TABLE reports (
        id             INTEGER PRIMARY KEY,
        run_id         INTEGER NOT NULL REFERENCES runs(id),
        agent          TEXT NOT NULL CHECK (agent IN ('news', 'macro', 'outlook')),
        stock          TEXT,               -- display name as typed; NULL for market-wide reports
        stock_key      TEXT,               -- normalised stock name used for lookups
        content_md     TEXT NOT NULL,
        truncated      INTEGER NOT NULL DEFAULT 0,
        input_tokens   INTEGER,
        output_tokens  INTEGER,
        created_at     TEXT NOT NULL,      -- UTC, ISO 8601
        news_report_id  INTEGER REFERENCES reports(id),   -- outlook only: the inputs it used
        macro_report_id INTEGER REFERENCES reports(id)
    );

    CREATE INDEX idx_reports_latest ON reports (agent, stock_key, created_at DESC);
    CREATE INDEX idx_reports_run ON reports (run_id);
    """,
]


class SchemaTooNew(RuntimeError):
    pass


def migrate(conn: sqlite3.Connection) -> int:
    """Apply any migrations the database is missing; returns the schema version."""
    version = conn.execute("PRAGMA user_version").fetchone()[0]
    if version > len(MIGRATIONS):
        raise SchemaTooNew(
            f"Database schema v{version} is newer than this program supports (v{len(MIGRATIONS)}); "
            "upgrade stock-analyst."
        )
    for number, sql in enumerate(MIGRATIONS[version:], start=version + 1):
        try:
            conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
        except sqlite3.Error:
            if conn.in_transaction:
                conn.execute("ROLLBACK")
            raise
    return len(MIGRATIONS)


def connect(path: str | Path) -> sqlite3.Connection:
    """Open (creating if needed) the database at `path` and bring its schema up to date."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path))
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn
