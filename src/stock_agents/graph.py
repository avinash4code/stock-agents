"""The workflow graph: wires agents together. Add new agents as nodes here."""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from stock_agents.agents import fundamentals_agent, macro_agent, news_agent, outlook_agent
from stock_agents.llm import default_llm
from stock_agents.state import WorkflowState


def build_graph(llm: BaseChatModel | None = None):
    """Compile the workflow.

        START -> news_agent         -+
        START -> macro_agent        -+-> outlook_agent -> END
        START -> fundamentals_agent -+

    News, macro and fundamentals are independent, so they run in parallel; the outlook agent
    starts once all three have finished and reads their reports.
    """
    llm = llm or default_llm()

    graph = StateGraph(WorkflowState)
    graph.add_node(news_agent.NAME, news_agent.news_agent_node(llm))
    graph.add_node(macro_agent.NAME, macro_agent.macro_agent_node(llm))
    graph.add_node(fundamentals_agent.NAME, fundamentals_agent.fundamentals_agent_node(llm))
    graph.add_node(outlook_agent.NAME, outlook_agent.outlook_agent_node(llm))
    graph.add_edge(START, news_agent.NAME)
    graph.add_edge(START, macro_agent.NAME)
    graph.add_edge(START, fundamentals_agent.NAME)
    graph.add_edge([news_agent.NAME, macro_agent.NAME, fundamentals_agent.NAME], outlook_agent.NAME)  # waits for all
    graph.add_edge(outlook_agent.NAME, END)
    return graph.compile()


def run_workflow(
    stock: str,
    llm: BaseChatModel | None = None,
    news_report: str | None = None,
    macro_report: str | None = None,
    fundamentals_report: str | None = None,
) -> dict:
    """Run the workflow for one stock; returns the final state.

    Pass `news_report`, `macro_report` and/or `fundamentals_report` to reuse existing reports:
    the matching agent is skipped and the supplied text is used as is.
    """
    initial = {"stock": stock}
    if news_report:
        initial["news_report"] = news_report
    if macro_report:
        initial["macro_report"] = macro_report
    if fundamentals_report:
        initial["fundamentals_report"] = fundamentals_report
    return build_graph(llm).invoke(initial)
