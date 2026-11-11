from __future__ import annotations

import pytest
from fastapi.testclient import TestClient

from stock_agents.storage import Repository, connect
from stock_agents.symbols import Listing, search_listings
from stock_agents.web.app import create_app

LISTINGS = [
    Listing("TCS", "Tata Consultancy Services Limited"),
    Listing("TATAMOTORS", "Tata Motors Limited"),
    Listing("TATASTEEL", "Tata Steel Limited"),
    Listing("INFY", "Infosys Limited"),
    Listing("HDFCBANK", "HDFC Bank Limited"),
    Listing("IDEA", "Vodafone Idea Limited"),
]


def symbols(query, limit=10):
    return [l.symbol for l in search_listings(query, LISTINGS, limit)]


def test_search_ranks_exact_symbol_before_prefix_and_name():
    assert symbols("tcs") == ["TCS"]
    assert symbols("tata") == ["TATASTEEL", "TATAMOTORS", "TCS"]  # symbol prefixes first, then the name match
    assert symbols("idea")[0] == "IDEA"  # symbol match beats the Vodafone Idea name match


def test_search_matches_words_in_any_order_and_suffixes():
    assert symbols("consultancy tata") == ["TCS"]
    assert symbols("infy.ns") == ["INFY"]
    assert symbols("vodafone") == ["IDEA"]
    assert symbols("") == [] and symbols("zzzz") == []
    assert len(symbols("tata", limit=2)) == 2


@pytest.fixture
def client(tmp_path):
    db = tmp_path / "t.db"
    conn = connect(db)
    repo = Repository(conn)
    with conn:
        r1 = repo.start_run("all", "TCS", provider="anthropic", model="m")
        news = repo.save_report(r1, "news", "# news body", stock="TCS")
        macro = repo.save_report(r1, "macro", "macro body")
        repo.finish_run(r1)
        r2 = repo.start_run("all", "TCS")
        outlook = repo.save_report(r2, "outlook", "outlook body", stock="TCS", news_report_id=news, macro_report_id=macro)
        repo.finish_run(r2, "failed", "boom")
        repo.start_run("import")
    conn.close()
    return TestClient(create_app(db, LISTINGS)), {"news": news, "outlook": outlook, "r1": r1, "r2": r2}


def test_symbols_endpoint(client):
    c, _ = client
    assert c.get("/api/symbols", params={"q": "infos"}).json() == [{"symbol": "INFY", "name": "Infosys Limited"}]


def test_runs_list_newest_first_hides_imports_and_reports_reuse(client):
    c, ids = client
    runs = c.get("/api/runs").json()
    assert [r["id"] for r in runs] == [ids["r2"], ids["r1"]]
    second = runs[0]
    assert second["status"] == "failed" and second["error"] == "boom"
    assert [r["agent"] for r in second["reports"]] == ["outlook"]
    assert sorted(r["agent"] for r in second["reused"]) == ["macro", "news"]
    assert c.get("/api/runs", params={"stock": "tcs"}).json() != []
    assert c.get("/api/runs", params={"stock": "infy"}).json() == []
    # reports stored under the name typed on the command line are found by symbol
    assert len(c.get("/api/runs", params={"stock": "TCS"}).json()) == 2


def test_stock_filter_matches_company_name_aliases(tmp_path):
    db = tmp_path / "a.db"
    conn = connect(db)
    repo = Repository(conn)
    with conn:
        run = repo.start_run("all", "Infosys")
        repo.save_report(run, "news", "n", stock="Infosys")
    conn.close()
    c = TestClient(create_app(db, LISTINGS))
    assert len(c.get("/api/reports", params={"stock": "INFY"}).json()) == 1
    assert len(c.get("/api/runs", params={"stock": "INFY"}).json()) == 1
    assert c.get("/api/reports", params={"stock": "TCS"}).json() == []


def test_run_and_report_detail(client):
    c, ids = client
    assert c.get(f"/api/runs/{ids['r1']}").json()["model"] == "m"
    assert c.get("/api/runs/999").status_code == 404
    report = c.get(f"/api/reports/{ids['outlook']}").json()
    assert report["content_md"] == "outlook body"
    assert report["inputs"]["news"]["id"] == ids["news"]
    assert c.get("/api/reports/999").status_code == 404


def test_export_downloads_markdown(client):
    c, ids = client
    response = c.get(f"/api/reports/{ids['news']}/export")
    assert response.status_code == 200
    assert "attachment" in response.headers["content-disposition"]
    assert response.text.startswith("# News report: TCS")
