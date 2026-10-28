"""Building blocks shared by all agents."""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from stock_agents.state import AgentState

# Agents make many tool calls (each is two graph steps), so they get a roomy step budget.
AGENT_RECURSION_LIMIT = 50


def build_tool_agent(llm: BaseChatModel, tools: list, system_prompt: str, name: str):
    """Compile a tool-calling agent: START -> llm <-> tools -> END."""
    llm_with_tools = llm.bind_tools(tools)

    def call_llm(state: AgentState) -> dict:
        messages = [SystemMessage(system_prompt), *state["messages"]]
        return {"messages": [llm_with_tools.invoke(messages)]}

    graph = StateGraph(AgentState)
    graph.add_node("llm", call_llm)
    graph.add_node("tools", ToolNode(tools))
    graph.add_edge(START, "llm")
    graph.add_conditional_edges("llm", tools_condition, {"tools": "tools", END: END})
    graph.add_edge("tools", "llm")
    return graph.compile(name=name)


def message_text(content) -> str:
    if isinstance(content, list):  # Anthropic may return content blocks
        return "".join(b.get("text", "") for b in content if isinstance(b, dict))
    return content


def run_agent(agent, task: str) -> str:
    """Run an agent on a fresh conversation and return its final text."""
    result = agent.invoke(
        {"messages": [HumanMessage(task)]}, config={"recursion_limit": AGENT_RECURSION_LIMIT}
    )
    return message_text(result["messages"][-1].content)
