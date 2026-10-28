from langchain_core.messages import AIMessage

from stock_agents.graph import build_graph, run_workflow
from tests.conftest import FakeToolCallingModel


def _llm(*texts, cls=FakeToolCallingModel):
    return cls(messages=iter([AIMessage(content=t) for t in texts]))


def test_workflow_runs_news_then_macro_then_outlook():
    graph = build_graph(_llm()).get_graph()
    assert set(graph.nodes) == {"__start__", "news_agent", "macro_agent", "outlook_agent", "__end__"}
    assert {(e.source, e.target) for e in graph.edges} == {
        ("__start__", "news_agent"),
        ("news_agent", "macro_agent"),
        ("macro_agent", "outlook_agent"),
        ("outlook_agent", "__end__"),
    }


def test_run_workflow_collects_each_agents_report():
    result = run_workflow("TCS", llm=_llm("news!", "macro!", "outlook!"))
    assert result == {
        "stock": "TCS",
        "news_report": "news!",
        "macro_report": "macro!",
        "outlook_report": "outlook!",
    }


def test_news_and_macro_agents_have_isolated_conversations():
    seen = []

    class Spy(FakeToolCallingModel):
        def _generate(self, messages, *a, **k):
            seen.append([m.content for m in messages])
            return super()._generate(messages, *a, **k)

    run_workflow("TCS", llm=_llm("news!", "macro!", "outlook!", cls=Spy))
    assert not any("news!" in c for c in seen[1])  # macro agent never sees the news report
    assert "TCS" in seen[0][1] and "TCS" not in " ".join(seen[1])


def test_outlook_agent_receives_both_reports():
    seen = []

    class Spy(FakeToolCallingModel):
        def _generate(self, messages, *a, **k):
            seen.append([m.content for m in messages])
            return super()._generate(messages, *a, **k)

    run_workflow("TCS", llm=_llm("news!", "macro!", "outlook!", cls=Spy))
    task = seen[2][1]
    assert "TCS" in task and "news!" in task and "macro!" in task
