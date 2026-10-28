"""News agent: an LLM that calls news tools until it can summarise a stock's recent news.

Each agent module exposes a `build_<name>_agent(llm)` factory returning a compiled graph
over `AgentState`, which `stock_agents.graph` wires in as a node.
"""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from stock_agents.agents.common import build_tool_agent, run_agent
from stock_agents.articles import read_article
from stock_agents.news import get_stock_news
from stock_agents.state import WorkflowState

NAME = "news_agent"

SYSTEM_PROMPT = """You are a research assistant that gathers recent news about Indian listed stocks (NSE/BSE).
Use the get_stock_news tool to fetch headlines for the stock the user names. If results are empty or \
irrelevant, retry once with an alternate name or symbol.
Then call read_article on the 2-3 most relevant links for fuller detail; if it returns an ERROR, \
rely on the headline instead and do not retry that URL.
Then reply with: a one-line overview, 3-7 bullet points of the most relevant developments \
(each with source and date, drawing on article text where you read it), and the overall tone of coverage (positive / negative / mixed).
Only report what the articles say; do not invent facts and do not give investment advice."""

TOOLS = [get_stock_news, read_article]


def build_news_agent(llm: BaseChatModel, tools: list | None = None):
    """Compile the news agent graph."""
    return build_tool_agent(llm, tools if tools is not None else TOOLS, SYSTEM_PROMPT, NAME)


def news_agent_node(llm: BaseChatModel):
    """Workflow node: reads `stock`, writes `news_report`."""
    agent = build_news_agent(llm)

    def node(state: WorkflowState) -> dict:
        return {"news_report": run_agent(agent, f"Gather the latest news about {state['stock']}.")}

    return node
