import pytest

from stock_agents import cli
from stock_agents.config import LLMConfig
from stock_agents.storage import Repository, connect

CFG = LLMConfig(provider="deepseek", api_key="k", model="m", max_tokens=123)
META = {"truncated": False, "input_tokens": 5, "output_tokens": 7}


class FakeNode:
    """Stands in for an agent node; records the state it was called with."""

    def __init__(self, key, text, meta=META):
        self.key, self.text, self.meta, self.state = key, text, meta, None

    def __call__(self, state):
        self.state = state
        return {self.key: self.text, self.key.replace("report", "meta"): self.meta}


@pytest.fixture
def env(tmp_path, monkeypatch):
    db = tmp_path / "test.db"
    monkeypatch.chdir(tmp_path)  # no stray .env from the repo
    for var in ("STOCK_ANALYST_NEWS_MAX_AGE_HOURS", "STOCK_ANALYST_MACRO_MAX_AGE_HOURS"):
        monkeypatch.delenv(var, raising=False)
    monkeypatch.setattr(cli, "_llm", lambda: (CFG, object()))
    nodes = {
        "news": FakeNode("news_report", "NEWS BODY"),
        "macro": FakeNode("macro_report", "MACRO BODY"),
        "outlook": FakeNode("outlook_report", "OUTLOOK BODY"),
    }
    monkeypatch.setattr(cli.news_agent, "news_agent_node", lambda llm: nodes["news"])
    monkeypatch.setattr(cli.macro_agent, "macro_agent_node", lambda llm: nodes["macro"])
    monkeypatch.setattr(cli.outlook_agent, "outlook_agent_node", lambda llm: nodes["outlook"])
    return db, nodes


def run(db, *args):
    return cli.main(["--db", str(db), *args])


def repo_for(db):
    return Repository(connect(db))


def rows(db, sql):
    return [tuple(r) for r in connect(db).execute(sql)]


def test_news_runs_only_the_news_agent_and_stores_it(env, capsys):
    db, nodes = env
    assert run(db, "news", "TCS") == 0
    assert nodes["news"].state == {"stock": "TCS"} and nodes["macro"].state is None and nodes["outlook"].state is None
    (r,) = repo_for(db).list()
    assert (r.agent, r.stock, r.content_md, r.input_tokens, r.output_tokens) == ("news", "TCS", "NEWS BODY", 5, 7)
    assert rows(db, "SELECT command, stock, status, provider, model, max_tokens FROM runs") == [
        ("news", "TCS", "ok", "deepseek", "m", 123)
    ]
    assert "NEWS BODY" in capsys.readouterr().out


def test_macro_needs_no_stock(env):
    db, nodes = env
    assert run(db, "macro") == 0
    assert nodes["news"].state is None
    (r,) = repo_for(db).list()
    assert (r.agent, r.stock) == ("macro", None)


def test_outlook_uses_latest_stored_reports_and_records_provenance(env):
    db, nodes = env
    repo = repo_for(db)
    with repo.conn:
        rid = repo.start_run("seed", "TCS")
        repo.save_report(rid, "news", "OLD NEWS", stock="TCS")
        new_news = repo.save_report(rid, "news", "NEW NEWS", stock="tcs")
        new_macro = repo.save_report(rid, "macro", "NEW MACRO")
    assert run(db, "outlook", "TCS") == 0
    state = nodes["outlook"].state
    assert state["news_report"] == "NEW NEWS" and state["macro_report"] == "NEW MACRO"
    out = repo_for(db).latest("outlook", "TCS")
    assert (out.news_report_id, out.macro_report_id) == (new_news, new_macro)
    assert nodes["news"].state is None and nodes["macro"].state is None


def test_outlook_ignores_truncated_reports_by_default(env):
    db, nodes = env
    repo = repo_for(db)
    with repo.conn:
        rid = repo.start_run("seed", "TCS")
        repo.save_report(rid, "news", "GOOD NEWS", stock="TCS")
        repo.save_report(rid, "macro", "GOOD MACRO")
        repo.save_report(rid, "macro", "CUT OFF MACRO", truncated=True)
    run(db, "outlook", "TCS")
    assert nodes["outlook"].state["macro_report"] == "GOOD MACRO"


def test_outlook_accepts_explicit_ids(env):
    db, nodes = env
    repo = repo_for(db)
    with repo.conn:
        rid = repo.start_run("seed", "TCS")
        n1 = repo.save_report(rid, "news", "N1", stock="TCS")
        repo.save_report(rid, "news", "N2", stock="TCS")
        m1 = repo.save_report(rid, "macro", "M1")
        repo.save_report(rid, "macro", "M2")
    run(db, "outlook", "TCS", "--news-id", str(n1), "--macro-id", str(m1))
    assert (nodes["outlook"].state["news_report"], nodes["outlook"].state["macro_report"]) == ("N1", "M1")


def test_outlook_without_stored_reports_says_what_to_run(env):
    db, _ = env
    with pytest.raises(SystemExit, match="run `stock-analyst news TCS` first"):
        run(db, "outlook", "TCS")


def test_outlook_rejects_an_id_of_the_wrong_kind(env):
    db, _ = env
    repo = repo_for(db)
    with repo.conn:
        macro_id = repo.save_report(repo.start_run("seed"), "macro", "M")
    with pytest.raises(SystemExit, match="is not a news report"):
        run(db, "outlook", "TCS", "--news-id", str(macro_id))


def full_result(**extra):
    return {
        "news_report": "n", "macro_report": "m", "outlook_report": "o",
        "news_meta": META, "macro_meta": META, "outlook_meta": {**META, "truncated": True}, **extra,
    }


def test_all_stores_three_linked_reports_in_one_run(env, monkeypatch, capsys):
    db, _ = env
    monkeypatch.setattr(cli, "run_workflow", lambda stock, llm, news_report=None, macro_report=None: full_result())
    assert run(db, "all", "Infosys") == 0
    reports = {r.agent: r for r in repo_for(db).list()}
    assert set(reports) == {"news", "macro", "outlook"} and len({r.run_id for r in reports.values()}) == 1
    assert reports["outlook"].news_report_id == reports["news"].id
    assert reports["outlook"].macro_report_id == reports["macro"].id
    assert reports["outlook"].truncated and not reports["news"].truncated
    assert "INCOMPLETE" in capsys.readouterr().err


def test_legacy_form_without_a_command_runs_all(env, monkeypatch):
    db, _ = env
    monkeypatch.setattr(cli, "run_workflow", lambda stock, llm, news_report=None, macro_report=None: full_result())
    assert cli.main(["--db", str(db), "Infosys"]) == 0  # option before the stock
    assert cli.main(["Infosys", "--db", str(db)]) == 0  # option after it
    # the second run reuses the first run's news and macro reports (within 6h) and only adds an outlook
    assert len(repo_for(db).list(limit=50)) == 4


@pytest.fixture
def workflow(env, monkeypatch):
    """Fake workflow that behaves like the real one: supplied reports are used as is and not regenerated."""
    db, _ = env
    calls = []

    def fake(stock, llm, news_report=None, macro_report=None):
        calls.append({"news_report": news_report, "macro_report": macro_report})
        result = full_result()
        for key in ("news", "macro"):
            supplied = {"news": news_report, "macro": macro_report}[key]
            if supplied:
                result[f"{key}_report"] = supplied
                del result[f"{key}_meta"]
        return result

    monkeypatch.setattr(cli, "run_workflow", fake)
    return db, calls


def seed(db, agent, stock=None, age_hours=0.0, truncated=False):
    from datetime import datetime, timedelta, timezone

    repo = repo_for(db)
    with repo.conn:
        return repo.save_report(
            repo.start_run("seed"), agent, f"STORED {agent.upper()}", stock=stock, truncated=truncated,
            created_at=datetime.now(timezone.utc) - timedelta(hours=age_hours),
        )


def counts(db):
    return {a: len(repo_for(db).list(agent=a)) for a in ("news", "macro", "outlook")}


def test_all_reuses_recent_news_and_macro_by_default(workflow, capsys):
    db, calls = workflow
    news_id, macro_id = seed(db, "news", "TCS", age_hours=5), seed(db, "macro", age_hours=5)
    run(db, "all", "tcs")
    assert calls == [{"news_report": "STORED NEWS", "macro_report": "STORED MACRO"}]
    assert counts(db) == {"news": 1, "macro": 1, "outlook": 1}  # only the outlook is new
    (outlook,) = repo_for(db).list(agent="outlook")
    assert (outlook.news_report_id, outlook.macro_report_id) == (news_id, macro_id)
    err = capsys.readouterr().err
    assert f"Reusing news report #{news_id}" in err and f"Reusing macro report #{macro_id}" in err


def test_reports_older_than_the_limit_are_regenerated(workflow):
    db, calls = workflow
    seed(db, "news", "TCS", age_hours=7)
    seed(db, "macro", age_hours=7)
    run(db, "all", "TCS")
    assert calls == [{"news_report": None, "macro_report": None}]
    assert counts(db) == {"news": 2, "macro": 2, "outlook": 1}


def test_news_reuse_is_per_stock(workflow):
    db, calls = workflow
    seed(db, "news", "Infosys", age_hours=1)
    run(db, "all", "TCS")
    assert calls[0]["news_report"] is None


def test_truncated_reports_are_never_reused(workflow):
    db, calls = workflow
    seed(db, "news", "TCS", age_hours=1, truncated=True)
    seed(db, "macro", age_hours=1, truncated=True)
    run(db, "all", "TCS")
    assert calls == [{"news_report": None, "macro_report": None}]


def test_cli_flags_override_the_default_limit(workflow):
    db, calls = workflow
    seed(db, "news", "TCS", age_hours=5)
    seed(db, "macro", age_hours=5)
    run(db, "all", "TCS", "--max-news-age", "2", "--max-macro-age", "24")  # news too old now, macro fine
    assert calls == [{"news_report": None, "macro_report": "STORED MACRO"}]


def test_zero_disables_reuse(workflow):
    db, calls = workflow
    seed(db, "news", "TCS")
    seed(db, "macro")
    run(db, "all", "TCS", "--max-news-age", "0", "--max-macro-age", "0")
    assert calls == [{"news_report": None, "macro_report": None}]


def test_env_settings_change_the_default_and_cli_beats_them(workflow, monkeypatch):
    db, calls = workflow
    seed(db, "news", "TCS", age_hours=5)
    seed(db, "macro", age_hours=5)
    monkeypatch.setenv("STOCK_ANALYST_NEWS_MAX_AGE_HOURS", "3")
    monkeypatch.setenv("STOCK_ANALYST_MACRO_MAX_AGE_HOURS", "12")
    run(db, "all", "TCS")
    assert calls[-1] == {"news_report": None, "macro_report": "STORED MACRO"}
    run(db, "all", "TCS", "--max-news-age", "6")  # CLI beats the env value of 3 (news from run 1 is fresh)
    assert calls[-1]["news_report"] is not None


def test_negative_age_is_rejected(workflow):
    db, _ = workflow
    with pytest.raises(SystemExit, match="must be >= 0"):
        run(db, "all", "TCS", "--max-news-age", "-1")


def test_standalone_news_and_macro_always_run_the_agent(env):
    db, nodes = env
    seed(db, "news", "TCS", age_hours=1)
    seed(db, "macro", age_hours=1)
    run(db, "news", "TCS")
    run(db, "macro")
    assert nodes["news"].state == {"stock": "TCS"} and nodes["macro"].state == {}
    assert counts(db) == {"news": 2, "macro": 2, "outlook": 0}


def test_a_failing_run_is_recorded_as_failed(env, monkeypatch):
    db, _ = env

    def boom(*a, **k):
        raise RuntimeError("model exploded")

    monkeypatch.setattr(cli, "run_workflow", boom)
    with pytest.raises(RuntimeError):
        run(db, "all", "TCS")
    assert rows(db, "SELECT status, error FROM runs") == [("failed", "RuntimeError: model exploded")]
    assert repo_for(db).list() == []


def test_reports_list_show_export(env, tmp_path, capsys):
    db, _ = env
    repo = repo_for(db)
    with repo.conn:
        rid = repo.save_report(repo.start_run("seed", "TCS"), "news", "THE BODY", stock="TCS", truncated=True)
    run(db, "reports", "list")
    listing = capsys.readouterr().out
    assert f"#{rid}" in listing and "news" in listing and "TCS" in listing and "INCOMPLETE" in listing
    run(db, "reports", "list", "--agent", "macro")
    assert "No reports stored yet." in capsys.readouterr().out
    run(db, "reports", "show", str(rid))
    assert "# News report: TCS" in capsys.readouterr().out
    run(db, "reports", "export", str(rid), "--dir", str(tmp_path / "out"))
    (exported,) = (tmp_path / "out").glob("tcs_*.md")
    assert "THE BODY" in exported.read_text()
    with pytest.raises(SystemExit, match="No report #999"):
        run(db, "reports", "show", "999")


def test_import_command(env, tmp_path, capsys):
    db, _ = env
    old = tmp_path / "old"
    old.mkdir()
    (old / "tcs_2026-10-26_1405.md").write_text("# News report: TCS\n\n_Generated x_\n\nbody\n")
    run(db, "import", str(old))
    assert "Imported 1 report(s)" in capsys.readouterr().out
    run(db, "import", str(old))
    assert "Imported 0 report(s); skipped 1" in capsys.readouterr().out
    with pytest.raises(SystemExit, match="not a folder"):
        run(db, "import", str(tmp_path / "missing"))
