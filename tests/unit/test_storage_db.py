import sqlite3

import pytest

from stock_agents.storage import connect
from stock_agents.storage.db import MIGRATIONS, SchemaTooNew, migrate


def version(conn):
    return conn.execute("PRAGMA user_version").fetchone()[0]


def test_fresh_database_is_migrated_to_latest(tmp_path):
    conn = connect(tmp_path / "nested" / "dir" / "a.db")  # parent folders are created
    assert version(conn) == len(MIGRATIONS)
    tables = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {"runs", "reports"} <= tables


def test_migrating_twice_changes_nothing(tmp_path):
    conn = connect(tmp_path / "a.db")
    conn.execute("INSERT INTO runs (command, started_at) VALUES ('x', 't')")
    conn.commit()
    migrate(conn)
    assert version(conn) == len(MIGRATIONS)
    assert conn.execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_reopening_keeps_data(tmp_path):
    path = tmp_path / "a.db"
    conn = connect(path)
    conn.execute("INSERT INTO runs (command, started_at) VALUES ('x', 't')")
    conn.commit()
    conn.close()
    assert connect(path).execute("SELECT COUNT(*) FROM runs").fetchone()[0] == 1


def test_database_from_a_newer_version_is_refused(tmp_path):
    path = tmp_path / "a.db"
    raw = sqlite3.connect(path)
    raw.execute(f"PRAGMA user_version = {len(MIGRATIONS) + 1}")
    raw.close()
    with pytest.raises(SchemaTooNew, match="upgrade"):
        connect(path)


def test_pragmas(tmp_path):
    conn = connect(tmp_path / "a.db")
    assert conn.execute("PRAGMA foreign_keys").fetchone()[0] == 1
    assert conn.execute("PRAGMA journal_mode").fetchone()[0] == "wal"


def test_constraints_reject_bad_rows(tmp_path):
    conn = connect(tmp_path / "a.db")
    conn.execute("INSERT INTO runs (command, started_at) VALUES ('x', 't')")
    with pytest.raises(sqlite3.IntegrityError):  # unknown agent
        conn.execute("INSERT INTO reports (run_id, agent, content_md, created_at) VALUES (1,'bogus','c','t')")
    with pytest.raises(sqlite3.IntegrityError):  # dangling run
        conn.execute("INSERT INTO reports (run_id, agent, content_md, created_at) VALUES (99,'news','c','t')")
