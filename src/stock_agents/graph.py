"""The workflow graph: wires agents together. Add new agents as nodes here."""
from __future__ import annotations

from typing import Callable

from langchain_core.language_models import BaseChatModel
from langgraph.graph import END, START, StateGraph

from stock_agents.agents import fundamentals_agent, macro_agent, news_agent, outlook_agent
from stock_agents.llm import PerAgentLLM, build_llm, model_for
from stock_agents.state import WorkflowState


def build_graph(llm: BaseChatModel | PerAgentLLM | None = None):
    """Compile the workflow.

        START -> news_agent         -+
        START -> macro_agent        -+-> outlook_agent -> END
        START -> fundamentals_agent -+

    News, macro and fundamentals are independent, so they run in parallel; the outlook agent
    starts once all three have finished and reads their reports. `llm` is one model for every agent or a
    `PerAgentLLM`; by default it is built from the environment (`LLM_*`, plus `LLM_<AGENT>_*` per agent).
    """
    llm = llm or build_llm()

    graph = StateGraph(WorkflowState)
    graph.add_node(news_agent.NAME, news_agent.news_agent_node(model_for(llm, "news")))
    graph.add_node(macro_agent.NAME, macro_agent.macro_agent_node(model_for(llm, "macro")))
    graph.add_node(fundamentals_agent.NAME, fundamentals_agent.fundamentals_agent_node(model_for(llm, "fundamentals")))
    graph.add_node(outlook_agent.NAME, outlook_agent.outlook_agent_node(model_for(llm, "outlook")))
    graph.add_edge(START, news_agent.NAME)
    graph.add_edge(START, macro_agent.NAME)
    graph.add_edge(START, fundamentals_agent.NAME)
    graph.add_edge([news_agent.NAME, macro_agent.NAME, fundamentals_agent.NAME], outlook_agent.NAME)  # waits for all
    graph.add_edge(outlook_agent.NAME, END)
    return graph.compile()


def run_workflow(
    stock: str,
    llm: BaseChatModel | PerAgentLLM | None = None,
    news_report: str | None = None,
    macro_report: str | None = None,
    fundamentals_report: str | None = None,
    progress: Callable[[str], None] | None = None,
) -> dict:
    """Run the workflow for one stock; returns the final state.

    Pass `news_report`, `macro_report` and/or `fundamentals_report` to reuse existing reports:
    the matching agent is skipped and the supplied text is used as is. `progress(node_name)` is
    called as each node finishes.
    """
    initial = {"stock": stock}
    if news_report:
        initial["news_report"] = news_report
    if macro_report:
        initial["macro_report"] = macro_report
    if fundamentals_report:
        initial["fundamentals_report"] = fundamentals_report
    graph = build_graph(llm)
    if progress is None:
        return graph.invoke(initial)
    state = dict(initial)
    for update in graph.stream(initial, stream_mode="updates"):  # {node name: the keys it wrote}
        for node, written in update.items():
            state.update(written or {})
            progress(node)
    return state
