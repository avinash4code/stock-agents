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


def test_anthropic_client_does_not_send_a_temperature():
    # Sonnet 5.5 / Fable reject non-default sampling values, so the configured temperature (0) must not be passed.
    llm = default_llm(LLMConfig("anthropic", "k", "claude-sonnet-5-5", temperature=0.0))
    assert llm.temperature is None
    llm._get_request_payload([("user", "hi")])  # would raise ValueError if temperature=0 were set


def test_build_llm_is_a_single_model_without_overrides():
    from stock_agents.llm import build_llm

    assert isinstance(build_llm(LLMConfig("deepseek", "k", "deepseek-flash")), ChatDeepSeek)


def test_build_llm_gives_each_agent_its_own_model_and_shares_identical_ones():
    from stock_agents.llm import PerAgentLLM, build_llm, model_for

    base = LLMConfig("deepseek", "ds", "deepseek-flash")
    pro = base.derive(model="deepseek-v4-pro")
    sonnet = LLMConfig("anthropic", "an", "claude-sonnet-5-5")
    llm = build_llm(base.with_agent_models({"fundamentals": pro, "outlook": pro, "macro": sonnet}))
    assert isinstance(llm, PerAgentLLM)
    assert model_for(llm, "news").model_name == "deepseek-flash"
    assert model_for(llm, "fundamentals").model_name == "deepseek-v4-pro"
    assert model_for(llm, "fundamentals") is model_for(llm, "outlook")
    assert isinstance(model_for(llm, "macro"), ChatAnthropic)


def test_model_for_passes_a_plain_model_through():
    from stock_agents.llm import model_for

    marker = object()
    assert model_for(marker, "news") is marker


def test_build_llm_uses_each_agents_temperature():
    from stock_agents.llm import build_llm, model_for

    base = LLMConfig("deepseek", "ds", "deepseek-flash", temperature=0.1)
    llm = build_llm(base.with_agent_models({"outlook": base.derive(temperature=0.3)}))
    assert model_for(llm, "news").temperature == 0.1 and model_for(llm, "outlook").temperature == 0.3
