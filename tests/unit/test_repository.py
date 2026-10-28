from datetime import datetime, timedelta, timezone

import pytest

from stock_agents.storage import Repository, connect, stock_key

T0 = datetime(2026, 10, 28, 12, 0, tzinfo=timezone.utc)


@pytest.fixture
def repo():
    conn = connect(":memory:")
    repo = Repository(conn)
    repo.run_id = repo.start_run("all", "TCS", provider="deepseek", model="m", max_tokens=100, now=T0)
    return repo


def test_stock_key_normalises():
    assert stock_key("Tata Motors") == stock_key("tata-motors") == stock_key("  TATA  MOTORS! ") == "tata-motors"
    assert stock_key(None) is None and stock_key("???") is None


def test_save_and_get_roundtrip(repo):
    rid = repo.save_report(repo.run_id, "news", "body", stock="TCS", truncated=True, input_tokens=10,
                           output_tokens=20, created_at=T0)
    r = repo.get(rid)
    assert (r.agent, r.stock, r.content_md, r.truncated) == ("news", "TCS", "body", True)
    assert (r.input_tokens, r.output_tokens, r.created_at, r.run_id) == (10, 20, T0, repo.run_id)
    assert repo.get(999) is None


def test_validation(repo):
    with pytest.raises(ValueError, match="unknown agent"):
        repo.save_report(repo.run_id, "bogus", "x", stock="TCS")
    with pytest.raises(ValueError, match="need a stock"):
        repo.save_report(repo.run_id, "news", "x")
    repo.save_report(repo.run_id, "macro", "x")  # macro needs no stock


def test_latest_is_newest_and_matches_stock_loosely(repo):
    repo.save_report(repo.run_id, "news", "old", stock="TCS", created_at=T0)
    repo.save_report(repo.run_id, "news", "new", stock="tcs", created_at=T0 + timedelta(hours=1))
    repo.save_report(repo.run_id, "news", "other", stock="Infosys", created_at=T0 + timedelta(hours=2))
    assert repo.latest("news", "Tcs").content_md == "new"
    assert repo.latest("news", "Infosys").content_md == "other"
    assert repo.latest("news", "Wipro") is None
    assert repo.latest("outlook", "TCS") is None


def test_latest_macro_ignores_stock(repo):
    repo.save_report(repo.run_id, "macro", "m1", created_at=T0)
    repo.save_report(repo.run_id, "macro", "m2", created_at=T0 + timedelta(hours=1))
    assert repo.latest("macro").content_md == "m2"


def test_latest_skips_truncated_unless_asked(repo):
    repo.save_report(repo.run_id, "macro", "complete", created_at=T0)
    repo.save_report(repo.run_id, "macro", "cut off", truncated=True, created_at=T0 + timedelta(hours=1))
    assert repo.latest("macro").content_md == "complete"
    assert repo.latest("macro", include_truncated=True).content_md == "cut off"


def test_latest_max_age(repo):
    repo.save_report(repo.run_id, "macro", "m", created_at=T0)
    assert repo.latest("macro", max_age=timedelta(hours=6), now=T0 + timedelta(hours=5)) is not None
    assert repo.latest("macro", max_age=timedelta(hours=6), now=T0 + timedelta(hours=7)) is None


def test_list_filters_and_orders_newest_first(repo):
    ids = [
        repo.save_report(repo.run_id, "news", "a", stock="TCS", created_at=T0),
        repo.save_report(repo.run_id, "macro", "b", created_at=T0 + timedelta(hours=1)),
        repo.save_report(repo.run_id, "news", "c", stock="Infosys", created_at=T0 + timedelta(hours=2)),
    ]
    assert [r.id for r in repo.list()] == ids[::-1]
    assert [r.id for r in repo.list(agent="news")] == [ids[2], ids[0]]
    assert [r.id for r in repo.list(stock="tcs")] == [ids[0]]
    assert len(repo.list(limit=2)) == 2


def test_outlook_links_to_its_inputs(repo):
    n = repo.save_report(repo.run_id, "news", "n", stock="TCS")
    m = repo.save_report(repo.run_id, "macro", "m")
    o = repo.get(repo.save_report(repo.run_id, "outlook", "o", stock="TCS", news_report_id=n, macro_report_id=m))
    assert (o.news_report_id, o.macro_report_id) == (n, m)


def test_run_lifecycle(repo):
    row = repo.conn.execute("SELECT * FROM runs WHERE id = ?", (repo.run_id,)).fetchone()
    assert (row["status"], row["provider"], row["model"], row["max_tokens"]) == ("running", "deepseek", "m", 100)
    repo.finish_run(repo.run_id, "failed", "boom", now=T0)
    row = repo.conn.execute("SELECT * FROM runs WHERE id = ?", (repo.run_id,)).fetchone()
    assert (row["status"], row["error"], row["finished_at"]) == ("failed", "boom", "2026-10-28T12:00:00+00:00")


def test_exists_matches_agent_stock_and_time(repo):
    repo.save_report(repo.run_id, "news", "n", stock="TCS", created_at=T0)
    repo.save_report(repo.run_id, "macro", "m", created_at=T0)
    assert repo.exists("news", "tcs", T0) and repo.exists("macro", None, T0)
    assert not repo.exists("news", "TCS", T0 + timedelta(minutes=1))
    assert not repo.exists("outlook", "TCS", T0)
