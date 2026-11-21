"""Agent prompts, optionally managed in Langfuse.

Each prompt is split in two. The prose (role, section structure, writing rules) can live in Langfuse as the text
prompt `stock-analyst/<agent>` and be edited there without a deploy; the JSON contract (`structured.instructions`:
schema shape, pillar keys, validators) always stays in code, so a prompt edit cannot break the UI cards or the
validation. The constants in the agent modules are the fallback: used when Langfuse is off, unreachable, has no such
prompt, or serves one that has lost a section heading the UI depends on.

Prompts are resolved each time an agent is built, i.e. per run, so the version named by `LANGFUSE_PROMPT_LABEL`
(default `production`) is picked up without a restart, within the SDK's cache (`LANGFUSE_PROMPT_CACHE_TTL`, 60s).
"""
from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass

from stock_agents import observability

logger = logging.getLogger(__name__)

PREFIX = "stock-analyst/"
PROMPTS = ("news", "macro", "fundamentals", "outlook", "repair")

# Section headings the UI or the downstream agents depend on (the frontend jumps to some of them by name).
# A Langfuse version missing one is rejected in favour of the local prompt.
REQUIRED_HEADINGS = {
    "macro": ("Key developments", "Overall tone"),
    "fundamentals": ("Verdict", "Growth", "Profitability", "Balance Sheet & Capital", "Shareholding & Governance",
                     "Versus Peers"),
    "outlook": ("Sector", "Company", "Summary"),
}


def label() -> str:
    return os.environ.get("LANGFUSE_PROMPT_LABEL", "").strip() or "production"


def cache_ttl() -> int:
    try:
        return max(0, int(os.environ.get("LANGFUSE_PROMPT_CACHE_TTL", "60")))
    except ValueError:
        return 60


@dataclass(frozen=True)
class Prompt:
    name: str  # the agent: news, macro, ...
    text: str
    version: int | None = None  # the Langfuse version; None for the local default
    source: str = "local"  # "langfuse" or "local"


def compose(prose: str, instructions: str) -> str:
    """The full system prompt: editable prose, then the code-owned JSON-block instructions."""
    return f"{prose.rstrip()}\n\n{instructions}"


def missing_headings(agent: str, text: str) -> list[str]:
    """Required section headings that `text` does not have."""
    return [h for h in REQUIRED_HEADINGS.get(agent, ())
            if not re.search(rf"^[ \t]*#{{2,3}}[ \t]*{re.escape(h)}", text, re.I | re.M)]


def resolve(agent: str, fallback: str) -> Prompt:
    """The prose for `agent`: the Langfuse prompt if one is available and sound, else `fallback`."""
    local = Prompt(agent, fallback)
    if not observability.enabled():
        return local
    try:
        from langfuse import get_client

        found = get_client().get_prompt(
            PREFIX + agent, label=label(), type="text", cache_ttl_seconds=cache_ttl(), fallback=fallback,
            max_retries=1, fetch_timeout_seconds=3)
    except Exception as exc:  # noqa: BLE001 - never fail a run over a prompt fetch
        logger.warning("Langfuse prompt %s unavailable (%s); using the local prompt", PREFIX + agent, exc)
        return local
    if getattr(found, "is_fallback", False):
        logger.warning("Langfuse prompt %s not available; using the local prompt", PREFIX + agent)
        return local
    text = found.prompt
    if not isinstance(text, str) or not text.strip():
        logger.warning("Langfuse prompt %s v%s is empty; using the local prompt", PREFIX + agent, found.version)
        return local
    lost = missing_headings(agent, text)
    if lost:
        logger.warning("Langfuse prompt %s v%s lacks required heading(s) %s; using the local prompt",
                       PREFIX + agent, found.version, lost)
        return local
    return Prompt(agent, text, found.version, "langfuse")


def defaults() -> dict[str, str]:
    """The local prose of every managed prompt: what `push` seeds Langfuse with."""
    from stock_agents import structured
    from stock_agents.agents import fundamentals_agent, macro_agent, news_agent, outlook_agent

    return {"news": news_agent.PROSE, "macro": macro_agent.PROSE, "fundamentals": fundamentals_agent.SYSTEM_PROMPT,
            "outlook": outlook_agent.PROSE, "repair": structured.REPAIR_SYSTEM}


def _is_missing(exc: Exception) -> bool:
    return getattr(exc, "status_code", None) == 404 or "not found" in str(exc).lower()


def push(force: bool = False) -> list[tuple[str, str]]:
    """Create the managed prompts in Langfuse from the local defaults, labelled `label()`.

    Returns (name, outcome) pairs. An existing prompt is left alone when it already matches ("unchanged") or
    differs ("differs": Langfuse is the source of truth once seeded); `force` pushes the local text as a new version.
    """
    if not observability.enabled():
        raise RuntimeError("Langfuse is not configured: set LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY")
    from langfuse import get_client

    client = get_client()
    outcomes = []
    for agent, prose in defaults().items():
        name = PREFIX + agent
        try:
            existing = client.get_prompt(name, label=label(), type="text", cache_ttl_seconds=0, max_retries=0)
        except Exception as exc:  # noqa: BLE001
            if not _is_missing(exc):
                raise
            existing = None
        if existing is not None and existing.prompt == prose:
            outcomes.append((name, f"unchanged (v{existing.version})"))
        elif existing is not None and not force:
            outcomes.append((name, f"differs from the local default (v{existing.version}); kept. --force pushes the local text as a new version"))
        else:
            created = client.create_prompt(
                name=name, prompt=prose, labels=[label()], type="text", commit_message="Seeded from the code default")
            outcomes.append((name, f"created v{created.version}" if existing is None else f"new version v{created.version}"))
    client.flush()
    return outcomes
