"""Chat model factory shared by all agents."""
from __future__ import annotations

from langchain_core.language_models import BaseChatModel

from stock_agents.config import LLMConfig


def default_llm(config: LLMConfig | None = None) -> BaseChatModel:
    config = config or LLMConfig.from_env()
    common = {"temperature": config.temperature, "max_tokens": config.max_tokens}
    if config.provider == "deepseek":
        from langchain_deepseek import ChatDeepSeek

        if config.base_url:
            common["api_base"] = config.base_url
        return ChatDeepSeek(model=config.model, api_key=config.api_key, **common)

    from langchain_anthropic import ChatAnthropic

    if config.base_url:
        common["base_url"] = config.base_url
    return ChatAnthropic(model=config.model, api_key=config.api_key, **common)
