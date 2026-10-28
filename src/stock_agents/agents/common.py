"""Building blocks shared by all agents."""
from __future__ import annotations

import logging
from dataclasses import dataclass

from langchain_core.language_models import BaseChatModel
from langchain_core.messages import HumanMessage, SystemMessage
from langgraph.graph import END, START, StateGraph
from langgraph.prebuilt import ToolNode, tools_condition

from stock_agents.state import AgentState

logger = logging.getLogger(__name__)

TRUNCATION_NOTE = (
    "> **Warning: this report is incomplete.** The model hit its output limit (LLM_MAX_TOKENS) "
    "before finishing. Reasoning models spend that budget on hidden thinking too - raise it in .env."
)

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


def was_truncated(message) -> bool:
    """True if the model stopped because it ran out of output tokens (OpenAI-style or Anthropic)."""
    meta = getattr(message, "response_metadata", None) or {}
    return meta.get("finish_reason") == "length" or meta.get("stop_reason") == "max_tokens"


@dataclass(frozen=True)
class AgentResult:
    text: str
    truncated: bool = False
    input_tokens: int | None = None
    output_tokens: int | None = None

    def meta(self) -> dict:
        """Everything except the text, as stored alongside the report."""
        return {
            "truncated": self.truncated,
            "input_tokens": self.input_tokens,
            "output_tokens": self.output_tokens,
        }


def total_usage(messages) -> tuple[int | None, int | None]:
    """Sum token usage over every model call in an agent's conversation."""
    usages = [m.usage_metadata for m in messages if getattr(m, "usage_metadata", None)]
    if not usages:
        return None, None
    return sum(u.get("input_tokens", 0) for u in usages), sum(u.get("output_tokens", 0) for u in usages)


def run_agent(agent, task: str) -> AgentResult:
    """Run an agent on a fresh conversation and return its final text plus run details.

    If the final reply was cut off by the token limit (or came back empty because
    reasoning used the whole budget), a visible warning is added to the report text.
    """
    result = agent.invoke(
        {"messages": [HumanMessage(task)]}, config={"recursion_limit": AGENT_RECURSION_LIMIT}
    )
    last = result["messages"][-1]
    text = message_text(last.content)
    input_tokens, output_tokens = total_usage(result["messages"])
    logger.info(
        "final reply: %d chars, finish=%s, tokens in/out=%s/%s",
        len(text),
        (getattr(last, "response_metadata", None) or {}).get("finish_reason"),
        input_tokens,
        output_tokens,
    )
    truncated = was_truncated(last)
    if truncated:
        logger.warning("Agent reply truncated by the output token limit; raise LLM_MAX_TOKENS.")
        text = f"{text.rstrip()}\n\n{TRUNCATION_NOTE}" if text.strip() else TRUNCATION_NOTE
    return AgentResult(text, truncated, input_tokens, output_tokens)
