from langchain_anthropic import ChatAnthropic
from langchain_deepseek import ChatDeepSeek

from stock_agents.llm import default_llm
from stock_agents.config import LLMConfig


def test_deepseek_client():
    llm = default_llm(LLMConfig("deepseek", "ds-key", "deepseek-chat", 0.0, 512, "http://gw/v1"))
    assert isinstance(llm, ChatDeepSeek)
    assert llm.model_name == "deepseek-chat" and llm.max_tokens == 512
    assert llm.api_base == "http://gw/v1"


def test_anthropic_client():
    llm = default_llm(LLMConfig("anthropic", "k", "claude-sonnet-5-5"))
    assert isinstance(llm, ChatAnthropic) and llm.model == "claude-sonnet-5-5"
