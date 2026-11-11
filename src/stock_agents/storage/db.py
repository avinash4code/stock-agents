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
    # 2: fundamentals reports. SQLite cannot alter a CHECK constraint, so the table is rebuilt
    # with 'fundamentals' allowed as an agent and a link from outlooks to the report they used.
    """
    CREATE TABLE reports_new (
        id             INTEGER PRIMARY KEY,
        run_id         INTEGER NOT NULL REFERENCES runs(id),
        agent          TEXT NOT NULL CHECK (agent IN ('news', 'macro', 'outlook', 'fundamentals')),
        stock          TEXT,
        stock_key      TEXT,
        content_md     TEXT NOT NULL,
        truncated      INTEGER NOT NULL DEFAULT 0,
        input_tokens   INTEGER,
        output_tokens  INTEGER,
        created_at     TEXT NOT NULL,
        news_report_id         INTEGER REFERENCES reports(id),
        macro_report_id        INTEGER REFERENCES reports(id),
        fundamentals_report_id INTEGER REFERENCES reports(id)
    );
    INSERT INTO reports_new (id, run_id, agent, stock, stock_key, content_md, truncated, input_tokens,
                             output_tokens, created_at, news_report_id, macro_report_id)
        SELECT id, run_id, agent, stock, stock_key, content_md, truncated, input_tokens,
               output_tokens, created_at, news_report_id, macro_report_id FROM reports;
    DROP TABLE reports;
    ALTER TABLE reports_new RENAME TO reports;
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
    if version == len(MIGRATIONS):
        return version
    # Rebuilding a table (drop + rename) trips foreign keys that point at it, and PRAGMA foreign_keys
    # is ignored inside a transaction. So switch them off around the migrations (as the SQLite docs
    # advise) and verify the result afterwards.
    foreign_keys = conn.execute("PRAGMA foreign_keys").fetchone()[0]
    conn.execute("PRAGMA foreign_keys = OFF")
    try:
        for number, sql in enumerate(MIGRATIONS[version:], start=version + 1):
            try:
                conn.executescript(f"BEGIN;\n{sql}\nPRAGMA user_version = {number};\nCOMMIT;")
            except sqlite3.Error:
                if conn.in_transaction:
                    conn.execute("ROLLBACK")
                raise
            broken = conn.execute("PRAGMA foreign_key_check").fetchall()
            if broken:
                raise sqlite3.IntegrityError(f"migration {number} left {len(broken)} broken foreign key(s)")
    finally:
        conn.execute(f"PRAGMA foreign_keys = {'ON' if foreign_keys else 'OFF'}")
    return len(MIGRATIONS)


def connect(path: str | Path, *, check_same_thread: bool = True) -> sqlite3.Connection:
    """Open (creating if needed) the database at `path` and bring its schema up to date."""
    path = Path(path)
    if str(path) != ":memory:":
        path.parent.mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(str(path), check_same_thread=check_same_thread)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA foreign_keys = ON")
    conn.execute("PRAGMA journal_mode = WAL")
    migrate(conn)
    return conn
