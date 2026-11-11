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


def test_migration_2_keeps_existing_reports_and_links_and_allows_fundamentals(tmp_path):
    path = tmp_path / "old.db"
    raw = sqlite3.connect(path)
    raw.row_factory = sqlite3.Row
    raw.executescript(f"BEGIN;\n{MIGRATIONS[0]}\nPRAGMA user_version = 1;\nCOMMIT;")
    raw.execute("INSERT INTO runs (command, started_at) VALUES ('all', 't')")
    for agent in ("news", "macro"):
        raw.execute("INSERT INTO reports (run_id, agent, stock, stock_key, content_md, truncated, created_at)"
                    " VALUES (1, ?, 'TCS', 'tcs', ?, 1, 't')", (agent, f"{agent} body"))
    raw.execute("INSERT INTO reports (run_id, agent, stock, stock_key, content_md, created_at, news_report_id,"
                " macro_report_id) VALUES (1, 'outlook', 'TCS', 'tcs', 'o', 't', 1, 2)")
    raw.commit()
    raw.close()

    conn = connect(path)
    assert version(conn) == len(MIGRATIONS) >= 2
    rows = conn.execute("SELECT id, agent, content_md, truncated, news_report_id, macro_report_id,"
                        " fundamentals_report_id FROM reports ORDER BY id").fetchall()
    assert [tuple(r) for r in rows] == [
        (1, "news", "news body", 1, None, None, None),
        (2, "macro", "macro body", 1, None, None, None),
        (3, "outlook", "o", 0, 1, 2, None),
    ]
    conn.execute("INSERT INTO reports (run_id, agent, content_md, created_at, fundamentals_report_id)"
                 " VALUES (1, 'fundamentals', 'f', 't', NULL)")
    with pytest.raises(sqlite3.IntegrityError):  # the check still rejects unknown agents
        conn.execute("INSERT INTO reports (run_id, agent, content_md, created_at) VALUES (1,'bogus','c','t')")
    names = {r[0] for r in conn.execute("SELECT name FROM sqlite_master WHERE type='index'")}
    assert {"idx_reports_latest", "idx_reports_run"} <= names
    assert conn.execute("PRAGMA foreign_key_check").fetchall() == []


def test_connection_can_cross_threads_only_when_allowed(tmp_path):
    import threading

    def query_from_another_thread(conn):
        errors = []

        def work():
            try:
                conn.execute("SELECT 1").fetchone()
            except Exception as exc:  # noqa: BLE001
                errors.append(exc)

        t = threading.Thread(target=work)
        t.start()
        t.join()
        return errors

    assert query_from_another_thread(connect(tmp_path / "a.db"))
    assert not query_from_another_thread(connect(tmp_path / "b.db", check_same_thread=False))
