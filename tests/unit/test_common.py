from langchain_core.messages import AIMessage, HumanMessage

from stock_agents.agents.common import TRUNCATION_NOTE, message_text, run_agent, total_usage, was_truncated
from stock_agents.agents.outlook_agent import build_outlook_agent
from tests.conftest import FakeToolCallingModel


def test_message_text_plain_and_blocks():
    assert message_text("hi") == "hi"
    assert message_text([{"type": "text", "text": "a"}, {"type": "tool_use"}, {"type": "text", "text": "b"}]) == "ab"


def test_was_truncated_detects_both_provider_styles():
    assert was_truncated(AIMessage(content="x", response_metadata={"finish_reason": "length"}))
    assert was_truncated(AIMessage(content="x", response_metadata={"stop_reason": "max_tokens"}))
    assert not was_truncated(AIMessage(content="x", response_metadata={"finish_reason": "stop"}))
    assert not was_truncated(AIMessage(content="x"))


def _run(message):
    agent = build_outlook_agent(FakeToolCallingModel(messages=iter([message])))
    return run_agent(agent, "task")


def test_complete_reply_is_returned_untouched():
    result = _run(AIMessage(content="full", response_metadata={"finish_reason": "stop"}))
    assert (result.text, result.truncated) == ("full", False)


def test_cut_off_reply_gets_a_visible_warning(caplog):
    result = _run(AIMessage(content="partial sente", response_metadata={"finish_reason": "length"}))
    assert result.text.startswith("partial sente") and result.text.endswith(TRUNCATION_NOTE)
    assert result.truncated is True
    assert "LLM_MAX_TOKENS" in caplog.text


def test_empty_truncated_reply_is_the_warning_not_a_blank_report():
    result = _run(AIMessage(content="", response_metadata={"finish_reason": "length"}))
    assert result.text == TRUNCATION_NOTE and result.truncated


def test_total_usage_sums_every_model_call():
    msgs = [
        HumanMessage("x"),
        AIMessage(content="", usage_metadata={"input_tokens": 10, "output_tokens": 5, "total_tokens": 15}),
        AIMessage(content="", usage_metadata={"input_tokens": 20, "output_tokens": 7, "total_tokens": 27}),
    ]
    assert total_usage(msgs) == (30, 12)
    assert total_usage([HumanMessage("x"), AIMessage(content="no usage")]) == (None, None)


def test_run_agent_reports_token_usage():
    result = _run(AIMessage(content="ok", usage_metadata={"input_tokens": 3, "output_tokens": 4, "total_tokens": 7}))
    assert result.meta() == {"truncated": False, "input_tokens": 3, "output_tokens": 4}
