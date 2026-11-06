from langchain_core.messages import AIMessage, HumanMessage

from stock_agents.agents.outlook_agent import (
    NAME, SYSTEM_PROMPT, build_outlook_agent, build_task, outlook_agent_node,
)
from tests.conftest import FakeToolCallingModel


def test_named():
    assert NAME == "outlook_agent"


def test_prompt_requires_sector_company_and_three_horizons():
    p = SYSTEM_PROMPT.lower()
    for needle in ["## sector", "## company", "## summary", "## assumptions and gaps",
                   "short term", "medium term", "long term", "up to 3 months", "more than 1 year"]:
        assert needle in p, needle
    assert "do not give buy/sell/hold" in p and "not investment advice" in p


def test_build_task_embeds_all_reports():
    task = build_task({"stock": "TCS", "news_report": "N-REPORT", "macro_report": "M-REPORT",
                       "fundamentals_report": "F-REPORT"})
    assert "<fundamentals_report>\nF-REPORT\n</fundamentals_report>" in task
    assert "Stock: TCS" in task
    assert "<news_report>\nN-REPORT\n</news_report>" in task
    assert "<macro_report>\nM-REPORT\n</macro_report>" in task


def test_build_task_marks_missing_reports():
    task = build_task({"stock": "TCS", "news_report": ""})
    assert task.count("(not available)") == 3


def test_agent_is_a_single_llm_turn_with_no_tools():
    llm = FakeToolCallingModel(messages=iter([AIMessage(content="outlook")]))
    app = build_outlook_agent(llm)
    assert set(app.get_graph().nodes) == {"__start__", "llm", "__end__"}
    out = app.invoke({"messages": [HumanMessage("x")]})
    assert [m.type for m in out["messages"]] == ["human", "ai"]


def test_node_writes_outlook_report():
    node = outlook_agent_node(FakeToolCallingModel(messages=iter([AIMessage(content="outlook")])))
    out = node({"stock": "TCS", "news_report": "n", "macro_report": "m"})
    assert out["outlook_report"] == "outlook" and out["outlook_meta"]["truncated"] is False
