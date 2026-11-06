"""Integration: real graph, agents, tools and parsing, with only the network and LLM faked."""
import os
import threading
import time

import httpx
import pytest
import respx
from langchain_core.messages import AIMessage

from stock_agents.graph import run_workflow
from tests.conftest import RSS_SAMPLE, AgentScriptedModel
from tests.unit.test_articles import HTML

BING = "https://www.bing.com/news/search"


def calls(*tool_calls):
    return AIMessage(
        content="",
        tool_calls=[{"name": n, "args": a, "id": str(i)} for i, (n, a) in enumerate(tool_calls)],
    )


def llm_with(news, macro, outlook, fundamentals=None):
    fundamentals = fundamentals if fundamentals is not None else [AIMessage(content="FUNDAMENTALS")]
    return AgentScriptedModel(
        scripts={"news": news, "macro": macro, "outlook": outlook, "fundamentals": fundamentals}, seen=[]
    )


@respx.mock
def test_all_agents_fetch_read_and_report():
    search = respx.get(BING).mock(return_value=httpx.Response(200, content=RSS_SAMPLE))
    article = respx.get("https://example.com/1").mock(return_value=httpx.Response(200, text=HTML))
    llm = llm_with(
        news=[  # headlines -> read article -> report
            calls(("get_stock_news", {"stock": "TCS"})),
            calls(("read_article", {"url": "https://example.com/1"})),
            AIMessage(content="TCS: mixed-to-positive."),
        ],
        macro=[  # two topic scans -> read article -> report
            calls(("get_macro_news", {"topic": "crude oil prices"}), ("get_macro_news", {"topic": "RBI repo rate"})),
            calls(("read_article", {"url": "https://example.com/1"})),
            AIMessage(content="Macro: mixed."),
        ],
        outlook=[AIMessage(content="Outlook: neutral short term, positive long term.")],
    )

    result = run_workflow("TCS", llm=llm)

    assert result["news_report"] == "TCS: mixed-to-positive."
    assert result["macro_report"] == "Macro: mixed."
    assert result["outlook_report"] == "Outlook: neutral short term, positive long term."
    assert result["fundamentals_report"].endswith("FUNDAMENTALS")
    # agents run in parallel, so request order across them is not guaranteed
    queries = sorted(c.request.url.params["q"] for c in search.calls)
    assert queries == sorted(["TCS shares", "crude oil prices", "RBI repo rate"])
    assert article.call_count == 2

    # news agent's second turn saw the parsed headlines with unwrapped publisher URLs
    headlines = llm.messages_for("news")[1][-1].content
    assert "TCS wins $500m deal" in headlines and "Economic Times" in headlines
    assert "https://example.com/1" in headlines
    # macro agent saw both scans, then the extracted article text
    macro_turns = llm.messages_for("macro")
    assert [m.content for m in macro_turns[1] if m.type == "tool"] and len(
        [m for m in macro_turns[1] if m.type == "tool"]
    ) == 2
    assert "cut its revenue guidance" in macro_turns[2][-1].content
    # outlook agent saw both reports
    outlook_input = llm.messages_for("outlook")[0][1].content
    assert "TCS: mixed-to-positive." in outlook_input and "Macro: mixed." in outlook_input
    assert "FUNDAMENTALS" in outlook_input and "Tata Consultancy Services Ltd" in outlook_input


@respx.mock
def test_workflow_survives_news_and_article_outages():
    respx.get(BING).mock(return_value=httpx.Response(503))
    llm = llm_with(
        news=[calls(("get_stock_news", {"stock": "TCS"})), AIMessage(content="News source unavailable.")],
        macro=[
            calls(("get_macro_news", {"topic": "war"})),
            calls(("read_article", {"url": "https://news.google.com/rss/articles/x"})),
            AIMessage(content="Macro unavailable."),
        ],
        outlook=[AIMessage(content="Outlook: low confidence, little information.")],
    )
    result = run_workflow("TCS", llm=llm)
    assert result["news_report"] == "News source unavailable."
    assert result["macro_report"] == "Macro unavailable."
    assert result["outlook_report"].startswith("Outlook: low confidence")
    assert llm.messages_for("news")[1][-1].content.startswith("ERROR:")
    assert llm.messages_for("macro")[2][-1].content.startswith("ERROR:")  # unreadable link -> graceful error


@respx.mock
def test_news_and_macro_agents_actually_overlap():
    """Each search blocks until the other agent's search has also started: this only
    completes if both agents run concurrently (sequential execution would time out)."""
    both_started = threading.Barrier(2, timeout=5)

    def handler(request):
        both_started.wait()
        return httpx.Response(200, content=RSS_SAMPLE)

    respx.get(BING).mock(side_effect=handler)
    llm = llm_with(
        news=[calls(("get_stock_news", {"stock": "TCS"})), AIMessage(content="n")],
        macro=[calls(("get_macro_news", {"topic": "crude oil"})), AIMessage(content="m")],
        outlook=[AIMessage(content="o")],
    )
    start = time.monotonic()
    assert run_workflow("TCS", llm=llm)["outlook_report"] == "o"
    assert time.monotonic() - start < 5


@pytest.mark.live
@pytest.mark.skipif(
    not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("DEEPSEEK_API_KEY")), reason="needs an LLM API key"
)
def test_live_end_to_end():
    result = run_workflow("Reliance Industries")
    assert all(len(result[k]) > 50 for k in ("news_report", "macro_report", "outlook_report"))


@respx.mock
def test_cli_end_to_end_stores_everything_in_the_database(tmp_path, monkeypatch):
    """Real CLI, graph, agents, tools and SQLite; only the network and the LLM are faked."""
    from stock_agents import cli
    from stock_agents.config import LLMConfig
    from stock_agents.storage import Repository, connect

    respx.get(BING).mock(return_value=httpx.Response(200, content=RSS_SAMPLE))
    respx.get("https://example.com/1").mock(return_value=httpx.Response(200, text=HTML))
    llm = llm_with(
        news=[calls(("get_stock_news", {"stock": "TCS"})), AIMessage(content="NEWS")],
        macro=[calls(("get_macro_news", {"topic": "crude oil"})), AIMessage(content="MACRO")],
        outlook=[AIMessage(content="OUTLOOK", usage_metadata={"input_tokens": 9, "output_tokens": 4, "total_tokens": 13})],
    )
    cfg = LLMConfig(provider="deepseek", api_key="k", model="deepseek-flash", max_tokens=500)
    monkeypatch.setattr(cli, "_llm", lambda: (cfg, llm))
    db = tmp_path / "x.db"

    assert cli.main(["--db", str(db), "all", "TCS"]) == 0

    conn = connect(db)
    repo = Repository(conn)
    run_row = conn.execute("SELECT * FROM runs").fetchall()
    assert len(run_row) == 1
    assert (run_row[0]["status"], run_row[0]["model"], run_row[0]["max_tokens"]) == ("ok", "deepseek-flash", 500)
    news, macro, outlook, fund = (repo.latest(a, "TCS") for a in ("news", "macro", "outlook", "fundamentals"))
    assert (news.content_md, macro.content_md, outlook.content_md) == ("NEWS", "MACRO", "OUTLOOK")
    assert fund.content_md.startswith("## Financial data:") and fund.content_md.endswith("FUNDAMENTALS")
    assert (outlook.news_report_id, outlook.macro_report_id, outlook.fundamentals_report_id) == (news.id, macro.id, fund.id)
    assert (outlook.input_tokens, outlook.output_tokens) == (9, 4)
    assert macro.stock is None

    # the standalone outlook command can now re-run from what was stored
    llm.scripts["outlook"].append(AIMessage(content="OUTLOOK 2"))
    assert cli.main(["--db", str(db), "outlook", "tcs"]) == 0
    assert repo.latest("outlook", "TCS").content_md == "OUTLOOK 2"
    assert "NEWS" in llm.messages_for("outlook")[-1][1].content
