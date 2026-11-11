from langchain_core.messages import AIMessage, HumanMessage

from stock_agents.agents.fundamentals_agent import (
    NAME, SYSTEM_PROMPT, build_fundamentals_agent, build_task, fundamentals_agent_node,
)
from stock_agents.fundamentals import FundamentalsError
from tests.conftest import FakeToolCallingModel, sample_financials


def llm(*texts):
    return FakeToolCallingModel(messages=iter([AIMessage(content=t) for t in texts]))


def test_named():
    assert NAME == "fundamentals_agent"


def test_prompt_sections_and_guardrails():
    p = SYSTEM_PROMPT.lower()
    for needle in ["## verdict", "## growth", "## profitability & earnings quality", "## balance sheet & capital efficiency",
                   "## shareholding & governance signals", "## versus peers", "## red flags & strengths",
                   "## watch next quarter", "## data limits",
                   "do not recalculate", "banks, nbfcs", "standalone vs. consolidated", "annual",
                   "do not provide buy/sell/hold", "no investment advice"]:
        assert needle in p, needle


def test_agent_is_a_single_llm_turn_with_no_tools():
    app = build_fundamentals_agent(llm("analysis"))
    assert set(app.get_graph().nodes) == {"__start__", "llm", "__end__"}
    assert [m.type for m in app.invoke({"messages": [HumanMessage("x")]})["messages"]] == ["human", "ai"]


def test_task_contains_the_stock_and_the_financial_tables():
    task = build_task("TCS", sample_financials())
    assert "Stock: TCS" in task and "<company_financials>" in task
    for needle in ["### Quarterly results", "### Annual profit and loss", "### Balance sheet (annual)",
                   "### Cash flow (annual)", "### Shareholding", "### Peer comparison", "| Sales |", "Sales YoY %"]:
        assert needle in task, needle


def test_node_stores_the_data_tables_followed_by_the_analysis():
    out = fundamentals_agent_node(llm("THE ANALYSIS"))({"stock": "TCS"})
    report = out["fundamentals_report"]
    assert report.startswith("## Financial data: Tata Consultancy Services Ltd")
    assert report.endswith("\n\nTHE ANALYSIS") and "| Sales |" in report
    assert out["fundamentals_meta"]["truncated"] is False and "fundamentals_error" not in out


def test_node_fetches_the_stock_it_was_given():
    asked = []
    node = fundamentals_agent_node(llm("a"), fetch=lambda stock: asked.append(stock) or sample_financials())
    node({"stock": "Infosys"})
    assert asked == ["Infosys"]


def test_node_skips_everything_when_a_report_is_already_supplied():
    def boom(stock):
        raise AssertionError("must not fetch")

    assert fundamentals_agent_node(llm(), fetch=boom)({"stock": "TCS", "fundamentals_report": "STORED"}) == {}


def test_node_reports_missing_data_without_calling_the_model():
    def unavailable(stock):
        raise FundamentalsError("no quarterly financials found for X.NS")

    model = llm()  # no scripted replies: a model call would raise StopIteration
    out = fundamentals_agent_node(model, fetch=unavailable)({"stock": "X"})
    assert out == {"fundamentals_error": "no quarterly financials found for X.NS"}


def test_node_retries_once_when_the_model_returns_nothing():
    out = fundamentals_agent_node(llm("", "SECOND TRY"))({"stock": "TCS"})
    assert out["fundamentals_report"].endswith("SECOND TRY") and out["fundamentals_meta"]["truncated"] is False


def test_node_flags_the_report_incomplete_when_the_model_stays_silent():
    out = fundamentals_agent_node(llm("", "  "))({"stock": "TCS"})
    assert "returned no analysis" in out["fundamentals_report"] and "| Sales |" in out["fundamentals_report"]
    assert out["fundamentals_meta"]["truncated"] is True  # so it is never reused
