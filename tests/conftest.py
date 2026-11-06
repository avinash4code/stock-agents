from __future__ import annotations

import time
from datetime import date
from pathlib import Path

import pytest
from typing import ClassVar
from email.utils import formatdate
from urllib.parse import quote

from langchain_core.language_models import BaseChatModel
from langchain_core.language_models.fake_chat_models import GenericFakeChatModel
from langchain_core.outputs import ChatGeneration, ChatResult

def _rfc822(days_ago: float) -> str:
    return formatdate(time.time() - days_ago * 86400, usegmt=True)


def _item(title: str, publisher_url: str, source: str, days_ago: float) -> str:
    link = "http://www.bing.com/news/apiclick.aspx?ref=FexRss&amp;aid=&amp;url=" + quote(publisher_url, safe="")
    return (
        f"<item><title>{title}</title><link>{link}</link><pubDate>{_rfc822(days_ago)}</pubDate>"
        f"<News:Source>{source}</News:Source></item>"
    )


RSS_SAMPLE = (
    '<?xml version="1.0" encoding="utf-8"?><rss version="2.0" xmlns:News="https://www.bing.com/news/search">'
    "<channel><title>Bing News</title>"
    + _item("TCS wins $500m deal", "https://example.com/1", "Economic Times", 0.5)
    + _item("TCS Q2 profit rises 8%", "https://example.com/2", "Moneycontrol", 1)
    + _item("TCS announces buyback", "https://example.com/3", "", 2)
    + _item("TCS old story", "https://example.com/old", "Archive", 30)
    + "</channel></rss>"
).encode()


class FakeToolCallingModel(GenericFakeChatModel):
    """Scripted chat model that tolerates bind_tools (the real agent always calls it)."""

    def bind_tools(self, tools, **kwargs):
        return self


class AgentScriptedModel(BaseChatModel):
    """Fake LLM for multi-agent runs: each agent has its own response script.

    Agents run in parallel and share one model object, so responses are routed by the
    calling agent (identified from its system prompt) instead of by call order.
    `seen` records (agent, messages) for every call.
    """

    scripts: dict
    seen: list = []

    MARKERS: ClassVar[dict] = {
        "news": "gathers recent news about Indian listed stocks",
        "macro": "macro research analyst",
        "outlook": "equity research analyst",
        "fundamentals": "fundamental equity analyst",
    }

    @property
    def _llm_type(self) -> str:
        return "agent-scripted-fake"

    def bind_tools(self, tools, **kwargs):
        return self

    def _generate(self, messages, stop=None, run_manager=None, **kwargs):
        system = messages[0].content
        agent = next(name for name, marker in self.MARKERS.items() if marker in system)
        self.seen.append((agent, messages))
        return ChatResult(generations=[ChatGeneration(message=self.scripts[agent].pop(0))])

    def messages_for(self, agent: str) -> list:
        return [m for a, m in self.seen if a == agent]


FIXTURES = Path(__file__).parent / "fixtures"
FETCHED = date(2026, 11, 6)
PAGES = {  # fixture file -> the URL the page was served from
    "screener_tcs.html": "https://www.screener.in/company/TCS/consolidated/",
    "screener_infy_standalone.html": "https://www.screener.in/company/INFY/",
    "screener_hdfcbank.html": "https://www.screener.in/company/HDFCBANK/consolidated/",
    "screener_idea.html": "https://www.screener.in/company/IDEA/consolidated/",
}


def fixture_html(name: str) -> str:
    """A trimmed copy of a real Screener company page (see tests/fixtures)."""
    return (FIXTURES / name).read_text(encoding="utf-8")


def sample_financials(name: str = "screener_tcs.html", peers: bool = True):
    """A parsed fixture page; the TCS one also gets the peer table (TCS is a row in it)."""
    from dataclasses import replace

    from stock_agents.screener import parse_company_page, parse_peers

    fin = parse_company_page(fixture_html(name), PAGES[name], today=FETCHED)
    if peers and name == "screener_tcs.html":
        fin = replace(fin, peers=parse_peers(fixture_html("screener_peers_infy.html")))
    return fin


@pytest.fixture(autouse=True)
def no_real_financial_data(monkeypatch):
    """Tests never reach Screener: the fundamentals agent gets the TCS sample unless a test overrides this."""
    monkeypatch.setattr("stock_agents.agents.fundamentals_agent.fetch_fundamentals", lambda stock: sample_financials())
