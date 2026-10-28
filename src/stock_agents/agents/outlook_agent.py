"""Outlook agent: combines the news and macro reports into a sector and company view
for the short, medium and long term. It has no tools; it reasons over the other agents' output."""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import SystemMessage
from langgraph.graph import END, START, StateGraph

from stock_agents.agents.common import run_agent
from stock_agents.state import AgentState, WorkflowState

NAME = "outlook_agent"

SYSTEM_PROMPT = """You are an equity research analyst covering Indian listed companies (NSE/BSE).
You are given a stock plus two research notes: recent company news, and the current macro environment.
Form a reasoned outlook for (1) the industry sector the company belongs to and (2) the company itself.

Time horizons - give a separate view for each:
  - Short term: up to 3 months
  - Medium term: 3 to 12 months
  - Long term: more than 1 year

Use exactly this structure:
  ## Sector
  Name the sector/industry and the company's position in it (leader, challenger, niche, ...).
  Then for each horizon: **Short term / Medium term / Long term** - stance (Positive / Neutral / Negative), \\
confidence (Low / Medium / High), the main drivers, and the main risks.
  ## Company
  Same format for each of the three horizons, tying each stance to specific points from the news and \\
macro notes (cite the item you rely on).
  ## Summary
  A short table or list of the six stances (sector x 3 horizons, company x 3 horizons) and one paragraph on \\
how the macro backdrop interacts with the company's own story.
  ## Assumptions and gaps
  What you took from general knowledge rather than from the notes (e.g. sector classification, structural \\
trends), and what information is missing that could change the view.

Rules: ground short- and medium-term views mainly in the notes; the long-term view may lean on structural \\
trends but must say so. If the notes are thin or contradictory, lower your confidence and say why. \\
Do not invent figures, and do not give buy/sell/hold recommendations or price targets. \\
This is an analytical outlook, not investment advice."""

MISSING = "(not available)"


def build_outlook_agent(llm: BaseChatModel):
    """Compile the outlook agent graph: START -> llm -> END."""

    def call_llm(state: AgentState) -> dict:
        return {"messages": [llm.invoke([SystemMessage(SYSTEM_PROMPT), *state["messages"]])]}

    graph = StateGraph(AgentState)
    graph.add_node("llm", call_llm)
    graph.add_edge(START, "llm")
    graph.add_edge("llm", END)
    return graph.compile(name=NAME)


def build_task(state: WorkflowState) -> str:
    return (
        f"Stock: {state['stock']}\n\n"
        f"<news_report>\n{state.get('news_report') or MISSING}\n</news_report>\n\n"
        f"<macro_report>\n{state.get('macro_report') or MISSING}\n</macro_report>\n\n"
        "Write the sector and company outlook."
    )


def outlook_agent_node(llm: BaseChatModel):
    """Workflow node: reads `stock`, `news_report`, `macro_report`; writes `outlook_report`."""
    agent = build_outlook_agent(llm)

    def node(state: WorkflowState) -> dict:
        result = run_agent(agent, build_task(state))
        return {"outlook_report": result.text, "outlook_meta": result.meta()}

    return node
