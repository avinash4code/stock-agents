from langchain_core.messages import AIMessage

from stock_agents.graph import build_graph, run_workflow
from tests.conftest import AgentScriptedModel, FakeToolCallingModel


def _llm(news="news!", macro="macro!", outlook="outlook!"):
    return AgentScriptedModel(
        scripts={
            "news": [AIMessage(content=news)],
            "macro": [AIMessage(content=macro)],
            "outlook": [AIMessage(content=outlook)],
        },
        seen=[],
    )


def test_news_and_macro_run_in_parallel_then_outlook():
    graph = build_graph(FakeToolCallingModel(messages=iter([]))).get_graph()
    assert set(graph.nodes) == {"__start__", "news_agent", "macro_agent", "outlook_agent", "__end__"}
    assert {(e.source, e.target) for e in graph.edges} == {
        ("__start__", "news_agent"),
        ("__start__", "macro_agent"),
        ("news_agent", "outlook_agent"),
        ("macro_agent", "outlook_agent"),
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
    assert all(result[k]["truncated"] is False for k in ("news_meta", "macro_meta", "outlook_meta"))


def test_a_supplied_macro_report_is_reused_without_running_the_macro_agent():
    llm = AgentScriptedModel(
        scripts={"news": [AIMessage(content="news!")], "macro": [], "outlook": [AIMessage(content="outlook!")]},
        seen=[],
    )
    result = run_workflow("TCS", llm=llm, macro_report="STORED MACRO")
    assert result["macro_report"] == "STORED MACRO" and "macro_meta" not in result
    assert llm.messages_for("macro") == []
    assert "STORED MACRO" in llm.messages_for("outlook")[0][1].content


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
    llm = AgentScriptedModel(scripts={"news": [], "macro": [], "outlook": [AIMessage(content="outlook!")]}, seen=[])
    result = run_workflow("TCS", llm=llm, news_report="STORED NEWS", macro_report="STORED MACRO")
    assert (result["news_report"], result["macro_report"]) == ("STORED NEWS", "STORED MACRO")
    assert "news_meta" not in result and "macro_meta" not in result
    assert llm.messages_for("news") == [] and llm.messages_for("macro") == []
    task = llm.messages_for("outlook")[0][1].content
    assert "STORED NEWS" in task and "STORED MACRO" in task
