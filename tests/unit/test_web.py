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


# -- starting analyses from the web UI ---------------------------------------------------------

import threading  # noqa: E402

from stock_agents.config import LLMConfig  # noqa: E402
from stock_agents.web.jobs import JobManager  # noqa: E402

CFG = LLMConfig(provider="deepseek", api_key="k", model="m", max_tokens=99)
META = {"truncated": False, "input_tokens": 1, "output_tokens": 2}


def fake_result(**extra):
    return {"news_report": "n", "macro_report": "m", "fundamentals_report": "f", "outlook_report": "o",
            "news_meta": META, "macro_meta": META, "fundamentals_meta": META, "outlook_meta": META, **extra}


class Gate:
    """A fake workflow that blocks until released, reporting node progress like the real one."""

    def __init__(self, fail=None):
        self.release, self.started, self.calls, self.fail = threading.Event(), threading.Event(), [], fail

    def __call__(self, stock, llm, news_report=None, macro_report=None, fundamentals_report=None, progress=None):
        self.calls.append(stock)
        self.started.set()
        self.release.wait(5)
        if self.fail:
            raise self.fail
        for node in ("news_agent", "macro_agent", "fundamentals_agent", "outlook_agent"):
            progress(node)
        return fake_result()


@pytest.fixture
def runner(tmp_path):
    db = tmp_path / "r.db"
    gate = Gate()
    jobs = JobManager(db, llm_factory=lambda: (CFG, object()), workflow=gate)
    client = TestClient(create_app(db, LISTINGS, jobs))
    yield client, gate, db
    gate.release.set()
    jobs.shutdown()


def wait_for(client, run_id, status="running", timeout=5):
    import time

    deadline = time.time() + timeout
    while time.time() < deadline:
        run = client.get(f"/api/runs/{run_id}").json()
        if run["status"] != status:
            return run
        time.sleep(0.02)
    raise AssertionError("run did not finish")


def test_post_runs_starts_a_background_run_and_stores_the_reports(runner):
    c, gate, _ = runner
    response = c.post("/api/runs", json={"stock": "INFY"})
    assert response.status_code == 202
    body = response.json()
    assert body["stock"] == "Infosys"  # the company name, without "Limited"
    gate.started.wait(5)
    running = c.get(f"/api/runs/{body['run_id']}").json()
    assert running["status"] == "running"
    assert running["stages"]["news"] == "running" and running["stages"]["outlook"] == "waiting"
    gate.release.set()
    done = wait_for(c, body["run_id"])
    assert done["status"] == "ok" and done["stages"] is None and done["symbol"] == "INFY"
    assert sorted(r["agent"] for r in done["reports"]) == ["fundamentals", "macro", "news", "outlook"]
    assert gate.calls == ["Infosys"]
    # the stock page finds the reports by symbol
    assert len(c.get("/api/reports", params={"stock": "INFY"}).json()) == 3


def test_a_second_run_for_the_same_stock_is_refused_while_one_is_running(runner):
    c, gate, _ = runner
    first = c.post("/api/runs", json={"stock": "Infosys"}).json()["run_id"]
    gate.started.wait(5)
    again = c.post("/api/runs", json={"stock": "INFY"})
    assert again.status_code == 409 and again.json()["detail"]["run_id"] == first
    assert c.post("/api/runs", json={"stock": "TCS"}).status_code == 202  # other stocks are fine
    gate.release.set()
    wait_for(c, first)
    assert c.post("/api/runs", json={"stock": "INFY"}).status_code == 202  # and it can be run again afterwards


def test_a_failing_run_is_marked_failed_with_its_error(tmp_path):
    db = tmp_path / "f.db"
    gate = Gate(fail=RuntimeError("model exploded"))
    jobs = JobManager(db, llm_factory=lambda: (CFG, object()), workflow=gate)
    c = TestClient(create_app(db, LISTINGS, jobs))
    run_id = c.post("/api/runs", json={"stock": "TCS"}).json()["run_id"]
    gate.started.wait(5)
    gate.release.set()
    done = wait_for(c, run_id)
    assert done["status"] == "failed" and "model exploded" in done["error"]
    jobs.shutdown()


@pytest.mark.parametrize("body", [{}, {"stock": ""}, {"stock": "TCS", "max_news_age": -1}, {"stock": "Tata"}])
def test_post_runs_validates_input(runner, body):
    c, gate, _ = runner
    assert c.post("/api/runs", json=body).status_code in (400, 422)
    assert gate.calls == []


def test_post_runs_refuses_cross_origin_and_non_json_requests(runner):
    c, gate, _ = runner
    assert c.post("/api/runs", json={"stock": "TCS"}, headers={"origin": "http://evil.example"}).status_code == 403
    assert c.post("/api/runs", content="stock=TCS", headers={"content-type": "text/plain"}).status_code == 422
    assert c.post("/api/runs", json={"stock": "TCS"}, headers={"origin": "http://testserver"}).status_code == 202


def test_post_runs_without_an_api_key_says_so(tmp_path):
    def no_key():
        raise RuntimeError("ANTHROPIC_API_KEY is not set.")

    db = tmp_path / "k.db"
    c = TestClient(create_app(db, LISTINGS, JobManager(db, llm_factory=no_key)))
    response = c.post("/api/runs", json={"stock": "TCS"})
    assert response.status_code == 503 and "API_KEY" in response.json()["detail"]
    assert c.get("/api/runs").json() == []  # nothing recorded


def test_config_reports_whether_runs_are_possible(monkeypatch, tmp_path, client):
    monkeypatch.chdir(tmp_path)
    for var in ("ANTHROPIC_API_KEY", "DEEPSEEK_API_KEY"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setenv("LLM_PROVIDER", "deepseek")
    c, _ = client
    off = c.get("/api/config").json()
    assert off["can_run"] is False and "DEEPSEEK_API_KEY" in off["reason"]
    assert off["reuse_hours"]["fundamentals"] == 168
    monkeypatch.setenv("DEEPSEEK_API_KEY", "x")
    on = c.get("/api/config").json()
    assert on["can_run"] is True and on["provider"] == "deepseek"


def test_runs_left_running_by_a_dead_server_are_marked_failed_on_startup(tmp_path):
    from datetime import timedelta

    from stock_agents.storage.repository import utcnow

    db = tmp_path / "s.db"
    conn = connect(db)
    repo = Repository(conn)
    with conn:
        old = repo.start_run("all", "TCS", now=utcnow() - timedelta(hours=2))
        fresh = repo.start_run("all", "INFY")  # could be a CLI run still in progress
    conn.close()
    jobs = JobManager(db, llm_factory=lambda: (CFG, object()))
    conn = connect(db)
    repo = Repository(conn)
    assert repo.get_run(old).status == "failed" and "interrupted" in repo.get_run(old).error
    assert repo.get_run(fresh).status == "running"
    jobs.shutdown()
