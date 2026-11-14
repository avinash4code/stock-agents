from langchain_core.messages import AIMessage

from stock_agents.graph import build_graph, run_workflow
from tests.conftest import AgentScriptedModel, FakeToolCallingModel


def _llm(news="news!", macro="macro!", outlook="outlook!", fundamentals="fundamentals!"):
    return AgentScriptedModel(
        scripts={
            "news": [AIMessage(content=news)],
            "macro": [AIMessage(content=macro)],
            "outlook": [AIMessage(content=outlook)],
            "fundamentals": [AIMessage(content=fundamentals)],
        },
        seen=[],
    )


def test_news_macro_and_fundamentals_run_in_parallel_then_outlook():
    graph = build_graph(FakeToolCallingModel(messages=iter([]))).get_graph()
    assert set(graph.nodes) == {"__start__", "news_agent", "macro_agent", "fundamentals_agent", "outlook_agent", "__end__"}
    assert {(e.source, e.target) for e in graph.edges} == {
        ("__start__", "news_agent"),
        ("__start__", "macro_agent"),
        ("__start__", "fundamentals_agent"),
        ("news_agent", "outlook_agent"),
        ("macro_agent", "outlook_agent"),
        ("fundamentals_agent", "outlook_agent"),
        ("outlook_agent", "__end__"),
    }


def test_run_workflow_collects_each_agents_report():
    result = run_workflow("TCS", llm=_llm())
    assert {k: result[k] for k in ("stock", "news_report", "macro_report", "outlook_report")} == {
        "stock": "TCS",
        "news_report": "news!",
        "macro_report": "macro!",
        "outlook_report": "outlook!",
    }
    # the stored fundamentals report is the data tables followed by the model's analysis
    assert result["fundamentals_report"].startswith("## Financial data: Tata Consultancy Services Ltd")
    assert result["fundamentals_report"].endswith("fundamentals!")
    assert all(result[k]["truncated"] is False for k in ("news_meta", "macro_meta", "outlook_meta", "fundamentals_meta"))


def test_a_supplied_macro_report_is_reused_without_running_the_macro_agent():
    llm = AgentScriptedModel(
        scripts={"news": [AIMessage(content="news!")], "macro": [], "outlook": [AIMessage(content="outlook!")],
                 "fundamentals": [AIMessage(content="f!")]},
        seen=[],
    )
    result = run_workflow("TCS", llm=llm, macro_report="STORED MACRO")
    assert result["macro_report"] == "STORED MACRO" and "macro_meta" not in result
    assert llm.messages_for("macro") == []
    assert "STORED MACRO" in llm.messages_for("outlook")[0][1].content


def test_fundamentals_agent_sees_the_data_tables_but_not_the_other_reports():
    llm = _llm()
    run_workflow("TCS", llm=llm)
    (fund_in,) = llm.messages_for("fundamentals")
    task = fund_in[1].content
    assert "TCS" in task and "<company_financials>" in task and "| Sales |" in task and "Sales YoY %" in task
    assert "news!" not in task and "macro!" not in task
    assert "fundamentals!" in llm.messages_for("outlook")[0][1].content


def test_unavailable_financial_data_does_not_stop_the_workflow(monkeypatch):
    from stock_agents.fundamentals import FundamentalsError

    def boom(stock):
        raise FundamentalsError("no quarterly financials found for X.NS")

    monkeypatch.setattr("stock_agents.agents.fundamentals_agent.fetch_fundamentals", boom)
    llm = _llm()
    result = run_workflow("TCS", llm=llm)
    assert "fundamentals_report" not in result and "no quarterly financials" in result["fundamentals_error"]
    assert llm.messages_for("fundamentals") == []  # the model is not called without data
    assert "(not available)" in llm.messages_for("outlook")[0][1].content
    assert result["outlook_report"] == "outlook!"


def test_news_and_macro_agents_have_isolated_conversations():
    llm = _llm()
    run_workflow("TCS", llm=llm)
    (news_in,) = llm.messages_for("news")
    (macro_in,) = llm.messages_for("macro")
    assert "TCS" in news_in[1].content
    assert "TCS" not in " ".join(m.content for m in macro_in)
    assert "macro!" not in " ".join(m.content for m in news_in)
    assert "news!" not in " ".join(m.content for m in macro_in)


def test_outlook_agent_runs_once_after_both_and_receives_both_reports():
    llm = _llm()
    run_workflow("TCS", llm=llm)
    assert [a for a, _ in llm.seen][-1] == "outlook" and [a for a, _ in llm.seen].count("outlook") == 1
    (outlook_in,) = llm.messages_for("outlook")
    task = outlook_in[1].content
    assert "TCS" in task and "news!" in task and "macro!" in task


def test_supplied_news_and_macro_reports_skip_both_agents():
    llm = AgentScriptedModel(scripts={"news": [], "macro": [], "fundamentals": [], "outlook": [AIMessage(content="outlook!")]},
                             seen=[])
    result = run_workflow("TCS", llm=llm, news_report="STORED NEWS", macro_report="STORED MACRO",
                          fundamentals_report="STORED FUNDAMENTALS")
    assert (result["news_report"], result["macro_report"]) == ("STORED NEWS", "STORED MACRO")
    assert result["fundamentals_report"] == "STORED FUNDAMENTALS"
    assert "news_meta" not in result and "macro_meta" not in result and "fundamentals_meta" not in result
    assert llm.messages_for("news") == [] and llm.messages_for("macro") == [] and llm.messages_for("fundamentals") == []
    task = llm.messages_for("outlook")[0][1].content
    assert "STORED NEWS" in task and "STORED MACRO" in task and "STORED FUNDAMENTALS" in task


def test_run_workflow_reports_each_node_as_it_finishes():
    seen = []
    result = run_workflow("TCS", llm=_llm(), progress=seen.append)
    assert set(seen) == {"news_agent", "macro_agent", "fundamentals_agent", "outlook_agent"}
    assert seen[-1] == "outlook_agent"  # the outlook waits for the other three
    assert result["outlook_report"] and result["news_report"]


def test_each_agent_uses_its_own_model_when_given_a_per_agent_llm():
    from stock_agents.llm import PerAgentLLM

    cheap = _llm(news="cheap news", macro="cheap macro", fundamentals="cheap fundamentals")
    strong = _llm(outlook="strong outlook", fundamentals="strong fundamentals")
    result = run_workflow("TCS", llm=PerAgentLLM(cheap, {"fundamentals": strong, "outlook": strong}))
    assert (result["news_report"], result["macro_report"], result["outlook_report"]) == (
        "cheap news", "cheap macro", "strong outlook")
    assert result["fundamentals_report"].endswith("strong fundamentals")
    assert cheap.messages_for("outlook") == [] and cheap.messages_for("fundamentals") == []
    assert strong.messages_for("news") == [] and strong.messages_for("macro") == []
