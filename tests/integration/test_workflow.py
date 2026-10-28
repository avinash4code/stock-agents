"""Integration: real graph, agents, tools and parsing, with only the network and LLM faked."""
import os

import httpx
import pytest
import respx
from langchain_core.messages import AIMessage

from stock_agents.graph import run_workflow
from tests.conftest import RSS_SAMPLE, FakeToolCallingModel
from tests.unit.test_articles import HTML

BING = "https://www.bing.com/news/search"


def calls(*tool_calls):
    return AIMessage(
        content="",
        tool_calls=[{"name": n, "args": a, "id": str(i)} for i, (n, a) in enumerate(tool_calls)],
    )


class Spy(FakeToolCallingModel):
    """Scripted model that records every message list it is given."""

    seen: list = []

    def _generate(self, messages, *a, **k):
        self.seen.append(messages)
        return super()._generate(messages, *a, **k)


def spy(script):
    return Spy(messages=iter(script), seen=[])


@respx.mock
def test_both_agents_fetch_read_and_report():
    search = respx.get(BING).mock(return_value=httpx.Response(200, content=RSS_SAMPLE))
    article = respx.get("https://example.com/1").mock(return_value=httpx.Response(200, text=HTML))
    llm = spy(
        [
            # news agent: headlines -> read article -> report
            calls(("get_stock_news", {"stock": "TCS"})),
            calls(("read_article", {"url": "https://example.com/1"})),
            AIMessage(content="TCS: mixed-to-positive."),
            # macro agent: two topic scans -> read article -> report
            calls(("get_macro_news", {"topic": "crude oil prices"}), ("get_macro_news", {"topic": "RBI repo rate"})),
            calls(("read_article", {"url": "https://example.com/1"})),
            AIMessage(content="Macro: mixed."),
            # outlook agent: a single reasoning turn over both reports
            AIMessage(content="Outlook: neutral short term, positive long term."),
        ]
    )

    result = run_workflow("TCS", llm=llm)

    assert result["news_report"] == "TCS: mixed-to-positive."
    assert result["macro_report"] == "Macro: mixed."
    assert result["outlook_report"] == "Outlook: neutral short term, positive long term."
    outlook_input = llm.seen[6][1].content  # [system prompt, task]
    assert "TCS: mixed-to-positive." in outlook_input and "Macro: mixed." in outlook_input
    queries = [c.request.url.params["q"] for c in search.calls]
    assert queries[0] == "TCS shares"
    # the two macro scans are issued in parallel, so their order is not guaranteed
    assert set(queries[1:]) == {
        "crude oil prices",
        "RBI repo rate",
    }
    assert article.call_count == 2
    # the news agent's LLM turn after the search saw the parsed headlines with publisher URLs
    headlines = llm.seen[1][-1].content
    assert "TCS wins $500m deal" in headlines and "Economic Times" in headlines
    assert "https://example.com/1" in headlines
    # the macro agent saw both scans and the extracted article text
    macro_tool_msgs = [m.content for m in llm.seen[4] if m.type == "tool"]
    assert len(macro_tool_msgs) == 2 and all("TCS wins" in m for m in macro_tool_msgs)
    assert "cut its revenue guidance" in llm.seen[5][-1].content


@respx.mock
def test_workflow_survives_news_and_article_outages():
    respx.get(BING).mock(return_value=httpx.Response(503))
    llm = spy(
        [
            calls(("get_stock_news", {"stock": "TCS"})),
            AIMessage(content="News source unavailable."),
            calls(("get_macro_news", {"topic": "war"})),
            calls(("read_article", {"url": "https://news.google.com/rss/articles/x"})),
            AIMessage(content="Macro unavailable."),
            AIMessage(content="Outlook: low confidence, little information."),
        ]
    )
    result = run_workflow("TCS", llm=llm)
    assert result["news_report"] == "News source unavailable."
    assert result["macro_report"] == "Macro unavailable."
    assert result["outlook_report"].startswith("Outlook: low confidence")
    assert llm.seen[1][-1].content.startswith("ERROR:")
    assert llm.seen[4][-1].content.startswith("ERROR:")  # unreadable link -> graceful error


@pytest.mark.live
@pytest.mark.skipif(
    not (os.getenv("ANTHROPIC_API_KEY") or os.getenv("DEEPSEEK_API_KEY")), reason="needs an LLM API key"
)
def test_live_end_to_end():
    result = run_workflow("Reliance Industries")
    assert all(len(result[k]) > 50 for k in ("news_report", "macro_report", "outlook_report"))
