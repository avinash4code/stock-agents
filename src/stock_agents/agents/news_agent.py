"""News agent: an LLM that calls news tools until it can summarise a stock's recent news.

Each agent module exposes a `build_<name>_agent(llm)` factory returning a compiled graph
over `AgentState`, which `stock_agents.graph` wires in as a node.
"""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from stock_agents.agents.common import build_tool_agent, run_agent
from stock_agents import prompts, structured
from stock_agents.articles import read_article
from stock_agents.news import get_stock_news
from stock_agents.state import WorkflowState

NAME = "news_agent"

PROSE = """You are a research assistant that gathers recent news about Indian listed stocks (NSE/BSE).
Use the get_stock_news tool to fetch headlines for the stock the user names. If results are empty or \
irrelevant, retry once with an alternate name or symbol.
Then call read_article on the 2-3 most relevant links for fuller detail; if it returns an ERROR, \
rely on the headline instead and do not retry that URL.
Then reply with: a one-line overview, 3-7 bullet points of the most relevant developments \
(each with source and date, drawing on article text where you read it), and the overall tone of coverage (positive / negative / mixed).
Only report what the articles say; do not invent facts and do not give investment advice.
For the JSON block: stance is the overall tone of coverage (mixed = neutral); red_flags are the most worrying \
items in the news; catalysts are the most important positive or pending events.

"""

SYSTEM_PROMPT = prompts.compose(PROSE, structured.instructions("news"))  # the local default

TOOLS = [get_stock_news, read_article]


def build_news_agent(llm: BaseChatModel, tools: list | None = None, prompt: prompts.Prompt | None = None):
    """Compile the news agent graph (the prompt is fetched from Langfuse if it is set up, else `PROSE`)."""
    prompt = prompt or prompts.resolve("news", PROSE)
    system = prompts.compose(prompt.text, structured.instructions("news"))
    return build_tool_agent(llm, tools if tools is not None else TOOLS, system, NAME)


def news_agent_node(llm: BaseChatModel):
    """Workflow node: reads `stock`, writes `news_report`.

    If the state already carries a `news_report` (a recent one reused from storage), the
    agent is skipped and nothing is written.
    """
    agent = build_news_agent(llm)

    def node(state: WorkflowState) -> dict:
        if state.get("news_report"):
            return {}
        result = run_agent(agent, f"Gather the latest news about {state['stock']}.", summary="news", repair_llm=llm)
        return {"news_report": result.text, "news_meta": result.meta()}

    return node
