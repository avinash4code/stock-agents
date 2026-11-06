"""Fundamentals agent: analyses a company's recent quarterly results.

The numbers are fetched from Screener.in and all arithmetic is done in code (`screener`, `metrics`); the LLM has
no tools and only interprets the tables it is given. If the data cannot be fetched the node writes
`fundamentals_error` instead of a report, and the model is not called.
"""
from __future__ import annotations

import logging
from dataclasses import replace

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph

from stock_agents.agents.common import run_agent
from stock_agents.fundamentals import CompanyFinancials, FundamentalsError, fetch_fundamentals
from stock_agents.metrics import render_data_section
from stock_agents.state import AgentState, WorkflowState

logger = logging.getLogger(__name__)

NAME = "fundamentals_agent"

EMPTY_NOTE = (
    "> **Warning: the model returned no analysis** (twice). Only the data tables above are available; "
    "re-run the fundamentals agent, or raise LLM_MAX_TOKENS if the model is a reasoning model."
)

SYSTEM_PROMPT = """You are a fundamental equity analyst covering Indian listed companies (NSE/BSE).
You are given a company's financial data from Screener.in as tables (amounts in INR crore): quarterly results, \
annual profit and loss with a trailing-twelve-month (TTM) column, annual balance sheet, cash flow and efficiency \
ratios, and the quarterly shareholding pattern. Growth rates and ratios have already been calculated. Analyse them; \
do not recalculate, and never invent numbers that are not in the tables.

Use exactly this structure:
  ## Verdict
  Two or three sentences: the direction of the business (improving / stable / deteriorating) and the single most \
important thing behind it.
  ## Growth
  Quarterly trend (YoY first, as most businesses are seasonal; QoQ for momentum), the annual record over several \
years, and whether growth is accelerating or slowing. Say if the latest quarter is out of line with the trend.
  ## Profitability
  Operating and net margin trend (quarterly and annual), the tax rate, and the quality of profit: how much of \
pre-tax profit comes from other income, and whether one-off items seem to distort a quarter.
  ## Balance sheet and cash flow
  Borrowings against net worth, interest cover, working-capital days, ROCE, operating cash flow against net \
profit, capex and free cash flow, over the annual years shown.
  ## Shareholding
  Direction of promoter, FII, DII and public holdings over the last 4 and 8 quarters, and what the change suggests \
(for example promoter selling, or institutions accumulating). Note the number of shareholders.
  ## Versus peers
  Where the company stands among its listed peers on valuation (P/E), growth (latest-quarter sales and profit \
variation), profitability (ROCE) and dividend yield, using the peer table and the position lines. Say whether any \
premium or discount looks justified by growth and returns. If there is no peer table, say that.
  ## Red flags and strengths
  Two short bullet lists. Only include items the numbers support (profit rising while operating cash flow falls, \
rising debt, falling promoter holding, a one-off quarter, ...).
  ## Watch next quarter
  Two or three specific metrics to check in the next result, with the level that would change the view.
  ## Data limits
  What was missing or unreliable (n/a rows, short history, consolidated vs standalone, seasonality, how few peers are \
shown, no historical valuation).

Rules: amounts are INR crore; say so when you quote one. "n/a" means unavailable or not meaningful, not zero. \
The header says whether the figures are consolidated or standalone: standalone figures leave out subsidiaries, so \
say that they may understate or misstate the group. Balance sheet, cash flow and efficiency ratios are annual (and \
the latest balance-sheet column may be a half-year), so do not call them quarterly. For banks, NBFCs and \
insurers (the header says so), EBITDA-style rows, "Operating Profit" / "Financing Profit", margins on them and \
borrowings against net worth are not meaningful: say so and rely on revenue, net profit, EPS, net worth, \
deposits, asset quality rows if filled, and shareholding. The peer table is Screener's own industry grouping: only a handful of companies are listed, the median row \
covers a wider group, and its "Qtr Profit Var %" / "Qtr Sales Var %" can differ from the growth in the tables above, \
so use it for relative standing only and take the company's own growth from its quarterly table. The "Key ratios" line is a snapshot at the fetch date \
and may be out of date; use it for context only. Screener's own pros/cons are generated hints: use them as leads, \
never as evidence on their own. Do not give buy/sell/hold recommendations or price targets. This is an analysis of \
reported numbers, not investment advice."""


def build_fundamentals_agent(llm: BaseChatModel):
    """Compile the agent graph: START -> llm -> END (no tools)."""

    def call_llm(state: AgentState) -> dict:
        return {"messages": [llm.invoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])]}

    graph = StateGraph(AgentState)
    graph.add_node("llm", call_llm)
    graph.add_edge(START, "llm")
    graph.add_edge("llm", END)
    return graph.compile(name=NAME)


def build_task(stock: str, data: CompanyFinancials) -> str:
    return (
        f"Stock: {stock}\n\n"
        f"<company_financials>\n{render_data_section(data)}\n</company_financials>\n\n"
        "Write the fundamental analysis."
    )


def fundamentals_agent_node(llm: BaseChatModel, fetch=None):
    """Workflow node: reads `stock`, writes `fundamentals_report`.

    If the state already carries a `fundamentals_report` (a recent one reused from storage) nothing
    is fetched or run. If the data cannot be fetched, `fundamentals_error` is written instead.
    The stored report is the data tables followed by the analysis.
    """
    agent = build_fundamentals_agent(llm)

    def node(state: WorkflowState) -> dict:
        if state.get("fundamentals_report"):
            return {}
        try:
            data = (fetch or fetch_fundamentals)(state["stock"])
        except FundamentalsError as exc:
            logger.warning("Fundamentals unavailable for %s: %s", state["stock"], exc)
            return {"fundamentals_error": str(exc)}
        task = build_task(state["stock"], data)
        result = run_agent(agent, task)
        if not result.text.strip():  # reasoning models sometimes answer with hidden reasoning only
            logger.warning("Empty fundamentals reply for %s; retrying once", state["stock"])
            result = run_agent(agent, task)
        if not result.text.strip():
            result = replace(result, text=EMPTY_NOTE, truncated=True)  # truncated => never reused or fed to the outlook
        report = f"{render_data_section(data)}\n\n{result.text}"
        return {"fundamentals_report": report, "fundamentals_meta": result.meta()}

    return node
