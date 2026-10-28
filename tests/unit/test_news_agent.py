from langchain_core.messages import AIMessage, HumanMessage, ToolMessage
from langchain_core.tools import tool

from stock_agents.agents.news_agent import NAME, SYSTEM_PROMPT, TOOLS, build_news_agent
from tests.conftest import FakeToolCallingModel


def _tool_call(stock="TCS"):
    return AIMessage(
        content="",
        tool_calls=[{"name": "get_stock_news", "args": {"stock": stock}, "id": "c1"}],
    )


@tool
def get_stock_news(stock: str) -> str:
    """Stub news tool."""
    return f"1. headline about {stock}"


def test_agent_is_named_news_agent():
    assert NAME == "news_agent"
    assert build_news_agent(FakeToolCallingModel(messages=iter([])), tools=[get_stock_news]).name == NAME


def test_default_tools():
    assert [t.name for t in TOOLS] == ["get_stock_news", "read_article"]


def test_agent_has_llm_and_tool_nodes():
    app = build_news_agent(FakeToolCallingModel(messages=iter([])), tools=[get_stock_news])
    assert set(app.get_graph().nodes) == {"__start__", "llm", "tools", "__end__"}


def test_no_tool_call_ends_after_one_llm_turn():
    llm = FakeToolCallingModel(messages=iter([AIMessage(content="nothing to do")]))
    out = build_news_agent(llm, tools=[get_stock_news]).invoke({"messages": [HumanMessage("hi")]})
    assert [m.type for m in out["messages"]] == ["human", "ai"]


def test_tool_call_loops_back_to_llm():
    llm = FakeToolCallingModel(messages=iter([_tool_call(), AIMessage(content="summary")]))
    out = build_news_agent(llm, tools=[get_stock_news]).invoke({"messages": [HumanMessage("news on TCS")]})
    msgs = out["messages"]
    assert [m.type for m in msgs] == ["human", "ai", "tool", "ai"]
    assert isinstance(msgs[2], ToolMessage) and "headline about TCS" in msgs[2].content
    assert msgs[-1].content == "summary"


def test_system_prompt_forbids_advice():
    assert "do not give investment advice" in SYSTEM_PROMPT


def test_node_writes_news_report_and_meta():
    from stock_agents.agents.news_agent import news_agent_node

    node = news_agent_node(FakeToolCallingModel(messages=iter([AIMessage(content="report")])))
    out = node({"stock": "TCS"})
    assert out["news_report"] == "report" and out["news_meta"]["truncated"] is False


def test_node_skips_the_agent_when_a_news_report_is_supplied():
    from stock_agents.agents.news_agent import news_agent_node

    node = news_agent_node(FakeToolCallingModel(messages=iter([])))  # any LLM call would raise
    assert node({"stock": "TCS", "news_report": "reused"}) == {}
