"""State types for the workflow and for the agents inside it."""
from __future__ import annotations

from typing import Annotated, Sequence, TypedDict

from langchain_core.messages import BaseMessage
from langgraph.graph.message import add_messages


class AgentState(TypedDict):
    """Private to one agent: its own LLM/tool conversation."""

    messages: Annotated[Sequence[BaseMessage], add_messages]


class WorkflowState(TypedDict, total=False):
    """Shared across agents: each agent reads the input and writes its own report."""

    stock: str
    news_report: str
    macro_report: str
    outlook_report: str
    fundamentals_report: str
    # Set instead of a report when the financial data could not be fetched.
    fundamentals_error: str
    # Per-agent run details (truncated flag, token usage); saved next to each report.
    news_meta: dict
    macro_meta: dict
    outlook_meta: dict
    fundamentals_meta: dict
