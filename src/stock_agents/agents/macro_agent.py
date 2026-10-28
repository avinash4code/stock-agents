"""Macro agent: finds national/international developments that move Indian stock markets,
reads the most relevant articles and assesses the overall tone for the market."""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from stock_agents.agents.common import build_tool_agent, run_agent
from stock_agents.articles import read_article
from stock_agents.news import get_macro_news
from stock_agents.state import WorkflowState

NAME = "macro_agent"

SYSTEM_PROMPT = """You are a macro research analyst covering what moves the Indian stock market (NSE/BSE).
Step 1 - scan: call get_macro_news once per topic across the themes below (skip ones that are clearly \\
irrelevant, add others that matter right now):
  - wars, geopolitical tensions, sanctions, trade/tariff policy
  - crude oil and commodity prices
  - RBI repo rate, inflation, monetary policy; US Fed policy
  - bond yields (India 10-year G-sec, US Treasuries), currency (rupee/dollar), FPI/FII flows
  - Union Budget, taxation and major fiscal policy or government spending announcements
  - major elections and political developments
Step 2 - select: from all headlines choose the 3-4 most market-relevant stories (favour recent, high-impact, \\
distinct topics over duplicates).
Step 3 - read: call read_article on each selected link. If it returns an ERROR, try the next-best link; \\
if none can be read, rely on the headline and say so.
Step 4 - report, using exactly these sections:
  ## Key developments - for each selected story: what happened, why it matters for Indian equities, \\
which sectors are most affected (e.g. oil marketing, IT, banks, autos) and the likely direction.
  ## Other headlines noted - one line each for the themes you scanned but did not read in depth.
  ## Overall tone - positive / negative / mixed for Indian markets, with a short justification and the main \\
risks to watch.
Only report what the sources say; do not invent figures and do not give investment advice."""

TOOLS = [get_macro_news, read_article]

TASK = "Scan the latest national and international developments relevant to the Indian stock market."


def build_macro_agent(llm: BaseChatModel, tools: list | None = None):
    """Compile the macro agent graph."""
    return build_tool_agent(llm, tools if tools is not None else TOOLS, SYSTEM_PROMPT, NAME)


def macro_agent_node(llm: BaseChatModel):
    """Workflow node: writes `macro_report` (market-wide, independent of the stock)."""
    agent = build_macro_agent(llm)

    def node(state: WorkflowState) -> dict:
        return {"macro_report": run_agent(agent, TASK)}

    return node
