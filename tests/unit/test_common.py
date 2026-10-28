from stock_agents.agents.common import message_text


def test_message_text_plain_and_blocks():
    assert message_text("hi") == "hi"
    assert message_text([{"type": "text", "text": "a"}, {"type": "tool_use"}, {"type": "text", "text": "b"}]) == "ab"
