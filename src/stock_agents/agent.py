"""Single-agent LangGraph workflow: an LLM that calls a news tool until it can summarise."""
from __future__ import annotations

from typing import Annotated, Sequence, TypedDict

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import BaseMessage, HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.graph.message import add_messages
from langgraph.prebuilt import ToolNode, tools_condition

from stock_agents.articles import read_article
from stock_agents.config import LLMConfig
from stock_agents.news import get_stock_news

SYSTEM_PROMPT = """You are a research assistant that gathers recent news about Indian listed stocks (NSE/BSE).
Use the get_stock_news tool to fetch headlines for the stock the user names. If results are empty or \
irrelevant, retry once with an alternate name or symbol.
Then call read_article on the 2-3 most relevant links for fuller detail; if it returns an ERROR, \
rely on the headline instead and do not retry that URL.
Then reply with: a one-line overview, 3-7 bullet points of the most relevant developments \
(each with source and date, drawing on article text where you read it), and the overall tone of coverage (positive / negative / mixed).
Only report what the articles say; do not invent facts and do not give investment advice."""


class AgentState(TypedDict):
    messages: Annotated[Sequence[BaseMessage], add_messages]


def default_llm(config: LLMConfig | None = None) -> BaseChatModel:
    config = config or LLMConfig.from_env()
    common = {"temperature": config.temperature, "max_tokens": config.max_tokens}
    if config.provider == "deepseek":
        from langchain_deepseek import ChatDeepSeek

        if config.base_url:
            common["api_base"] = config.base_url
        return ChatDeepSeek(model=config.model, api_key=config.api_key, **common)

    from langchain_anthropic import ChatAnthropic

    if config.base_url:
        common["base_url"] = config.base_url
    return ChatAnthropic(model=config.model, api_key=config.api_key, **common)


def build_graph(llm: BaseChatModel | None = None, tools: list | None = None):
    """Compile the agent graph: START -> agent <-> tools -> END."""
    tools = tools if tools is not None else [get_stock_news, read_article]
    llm = llm or default_llm()
    llm_with_tools = llm.bind_tools(tools)

    def agent(state: AgentState) -> dict:
        messages = [SystemMessage(SYSTEM_PROMPT), *state["messages"]]
        return {"messages": [llm_with_tools.invoke(messages)]}

    graph = StateGraph(AgentState)
    graph.add_node("agent", agent)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "agent")
    graph.add_conditional_edges("agent", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "agent")
    return graph.compile()


def run_news_agent(stock: str, llm: BaseChatModel | None = None, recursion_limit: int = 20) -> str:
    """Run the agent for one stock and return its final summary text."""
    app = build_graph(llm)
    result = app.invoke(
        {"messages": [HumanMessage(f"Gather the latest news about {stock}.")]},
        config={"recursion_limit": recursion_limit},
    )
    content = result["messages"][-1].content
    if isinstance(content, list):  # Anthropic may return content blocks
        content = "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content
