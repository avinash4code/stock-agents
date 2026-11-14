"""Chat model factory shared by all agents."""
from __future__ import annotations

import logging

from langchain_core.language_models import BaseChatModel

from stock_agents.config import AGENTS, LLMConfig

logger = logging.getLogger(__name__)


def default_llm(config: LLMConfig | None = None) -> BaseChatModel:
    """One chat model for `config` itself (per-agent overrides are ignored; see `build_llm`)."""
    config = config or LLMConfig.from_env()
    logger.info("LLM: provider=%s model=%s max_tokens=%s", config.provider, config.model, config.max_tokens)
    common = {"max_tokens": config.max_tokens}
    if config.provider == "deepseek":
        from langchain_deepseek import ChatDeepSeek

        if config.base_url:
            common["api_base"] = config.base_url
        return ChatDeepSeek(model=config.model, api_key=config.api_key, temperature=config.temperature, **common)

    from langchain_anthropic import ChatAnthropic

    if config.base_url:
        common["base_url"] = config.base_url
    # Newer Claude models (Sonnet 5.5, Fable, Opus 5.5) reject a non-default temperature, so it is not sent.
    return ChatAnthropic(model=config.model, api_key=config.api_key, **common)


class PerAgentLLM:
    """The chat model for each agent, for when they do not all share one."""

    def __init__(self, default: BaseChatModel, overrides: dict[str, BaseChatModel]):
        self.default = default
        self.overrides = overrides

    def for_agent(self, agent: str) -> BaseChatModel:
        return self.overrides.get(agent, self.default)


def build_llm(config: LLMConfig | None = None) -> BaseChatModel | PerAgentLLM:
    """The model(s) for `config`: a single chat model, or a `PerAgentLLM` if any agent has its own settings."""
    config = config or LLMConfig.from_env()
    default = default_llm(config)
    if not config.agent_overrides:
        return default
    built: dict[tuple, BaseChatModel] = {}  # agents with identical settings share one client
    overrides = {}
    for agent in AGENTS:
        agent_cfg = config.for_agent(agent)
        if agent_cfg is config:
            continue
        key = (agent_cfg.provider, agent_cfg.model, agent_cfg.temperature, agent_cfg.max_tokens, agent_cfg.base_url)
        if key not in built:
            built[key] = default_llm(agent_cfg)
        overrides[agent] = built[key]
    return PerAgentLLM(default, overrides)


def model_for(llm: BaseChatModel | PerAgentLLM, agent: str) -> BaseChatModel:
    """The chat model `agent` should use, whether `llm` is one shared model or a `PerAgentLLM`."""
    return llm.for_agent(agent) if isinstance(llm, PerAgentLLM) else llm
