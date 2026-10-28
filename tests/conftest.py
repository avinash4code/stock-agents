from __future__ import annotations

import time
from email.utils import formatdate
from urllib.parse import quote

from langchain_core.language_models.fake_chat_models import GenericFakeChatModel

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
