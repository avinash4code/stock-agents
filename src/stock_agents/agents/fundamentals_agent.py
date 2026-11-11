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
You are given a company's financial data from Screener.in as tables (amounts in INR crore): quarterly results, annual profit and loss with a trailing-twelve-month (TTM) column, annual balance sheet, cash flow, efficiency ratios, and the quarterly shareholding pattern. Growth rates and ratios are pre-calculated. Analyse them objectively; do not recalculate figures, and never invent data not present in the tables.

Strict Formatting Rules:
- Amounts must explicitly include the unit "INR crore".
- "n/a" means unavailable or not meaningful, never zero.
- Treat balance sheet, cash flow, and efficiency ratios as annual data (or half-year if indicated in the header); never describe them as quarterly.
- Standalone vs. Consolidated: State explicitly if figures are standalone, highlighting that they exclude subsidiaries and may misstate group-level performance.
- BFSI Exclusions: For banks, NBFCs, and insurers, do NOT discuss EBITDA, Operating Profit, Operating Margins, or Debt-to-Equity. Focus strictly on Net Interest Income/Financing Revenue, Net Profit, Asset Quality/NPA rows (if available), Net Worth, Deposits, and Capital Adequacy.
- Peer Table Context: Screener's peer list is an automated industry sample; use it strictly for relative standing. Evaluate growth directly from the company's primary quarterly table. Exclude loss-making or non-meaningful P/E outliers when comparing multiples.
- No Investment Advice: Do not provide buy/sell/hold recommendations, fair value targets, or speculative investment advice.

Use exactly this section structure:

## Verdict
Two to three sentences stating business trajectory (improving, stable, or deteriorating) anchored to the single primary quantitative driver.

## Growth
- Quarterly Trend: Analyze YoY performance first (accounting for seasonality), followed by QoQ momentum.
- Multi-Year Trajectory: State whether long-term revenue and net profit growth are accelerating, compounding steadily, or slowing. Highlight if the latest quarter materially deviates from the multi-year run rate.

## Profitability & Earnings Quality
- Margins: Trajectory of Operating and Net Profit margins (both quarterly and multi-year annual).
- Profit Quality: Quantify the proportion of Pre-Tax Profit (PBT) derived from Other Income. Identify any exceptional or one-off items distorting reported earnings. Note the effective tax rate consistency.

## Balance Sheet & Capital Efficiency
- Leverage & Solvency: Debt-to-equity (borrowings against net worth) and interest coverage ratio trajectory.
- Cash Flow Conversion: Operating cash flow compared against Net Profit (CFO / PAT). Note if free cash flow (FCF = CFO - Capex) is positive and whether working capital changes (inventory/debtor days) are absorbing cash.
- Return Ratios: Direction and consistency of ROCE over the annual periods shown.

## Shareholding & Governance Signals
- Institutional & Promoter Shifts: Direction of Promoter, FII, and DII holdings over the last 4 and 8 quarters. State if promoter pledging is present or changing.
- Retail Distribution: Direction of public shareholding and total shareholder count (flagging retail accumulation during institutional exits).

## Versus Peers
- Relative Standing: Position among listed peers on valuation (P/E), growth (latest quarter sales and profit variation), and profitability (ROCE).
- Multiple Justification: State whether valuation premiums or discounts are justified by relative growth and return profiles. (State "Peer table not available" if omitted).

## Red Flags & Strengths
- Strengths: Concise, bulleted list of quantitatively validated strengths (e.g., consistent FCF generation, steady ROCE expansion, institutional accumulation).
- Red Flags: Concise, bulleted list of data-backed warning signs (e.g., CFO lagging PAT, rising working-capital days, debt additions without asset expansion, promoter stake dilution/pledging).

## Watch Next Quarter
Two or three quantifiable thresholds for the upcoming result that would challenge or alter this analytical thesis (e.g., "Operating margin compressing below X%", "Borrowings increasing beyond INR Y crore", or "Debtor days expanding past Z days").

## Data Limits
Explicit list of limitations: data gaps (n/a rows), standalone vs. consolidated reporting, limited peer sample, or brief operating history."""


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
