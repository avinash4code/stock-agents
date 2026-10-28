from langchain_core.messages import AIMessage, HumanMessage
from langchain_core.tools import tool

from stock_agents.agents.macro_agent import NAME, SYSTEM_PROMPT, TOOLS, build_macro_agent, macro_agent_node
from tests.conftest import FakeToolCallingModel


@tool
def get_macro_news(topic: str) -> str:
    """Stub."""
    return f"1. headline on {topic}"


def _call(name, args, id_):
    return AIMessage(content="", tool_calls=[{"name": name, "args": args, "id": id_}])


def test_named_and_equipped():
    assert NAME == "macro_agent"
    assert [t.name for t in TOOLS] == ["get_macro_news", "read_article"]


def test_prompt_covers_required_themes_and_outputs():
    for theme in ["wars", "crude", "repo rate", "Budget", "elections", "bond yields", "fiscal"]:
        assert theme.lower() in SYSTEM_PROMPT.lower(), theme
    assert "3-4" in SYSTEM_PROMPT and "## Overall tone" in SYSTEM_PROMPT
    assert "do not give investment advice" in SYSTEM_PROMPT


def test_agent_can_scan_multiple_topics_before_answering():
    llm = FakeToolCallingModel(
        messages=iter(
            [
                _call("get_macro_news", {"topic": "crude oil"}, "1"),
                _call("get_macro_news", {"topic": "RBI repo rate"}, "2"),
                AIMessage(content="report"),
            ]
        )
    )
    out = build_macro_agent(llm, tools=[get_macro_news]).invoke({"messages": [HumanMessage("go")]})
    assert [m.type for m in out["messages"]] == ["human", "ai", "tool", "ai", "tool", "ai"]
    assert "crude oil" in out["messages"][2].content and "RBI repo rate" in out["messages"][4].content


def test_node_writes_macro_report_only():
    node = macro_agent_node(FakeToolCallingModel(messages=iter([AIMessage(content="report")])))
    assert node({"stock": "TCS"}) == {"macro_report": "report"}
