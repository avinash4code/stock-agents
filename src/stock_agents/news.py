"""News retrieval for Indian stocks via the Bing News RSS feed (no API key needed).

Bing wraps each link in a click-tracking URL; we unwrap it to the publisher URL so
articles can be read directly.
"""
from __future__ import annotations

import calendar
import time
from urllib.parse import parse_qs, urlparse

import feedparser
import httpx
from langchain_core.tools import tool

BING_NEWS_RSS = "https://www.bing.com/news/search"
DEFAULT_TIMEOUT = 10.0
USER_AGENT = "Mozilla/5.0 (compatible; stock-agents/0.1)"


def build_params(query: str) -> dict:
    return {"q": query, "format": "rss", "setmkt": "en-IN", "cc": "IN"}


def unwrap_link(link: str) -> str:
    """Return the publisher URL from a Bing apiclick.aspx link (or the link unchanged)."""
    parsed = urlparse(link)
    if parsed.path.endswith("apiclick.aspx"):
        target = parse_qs(parsed.query).get("url")
        if target:
            return target[0]
    return link


def parse_feed(content: bytes | str, max_items: int, days: int | None = None, now: float | None = None) -> list[dict]:
    feed = feedparser.parse(content)
    cutoff = (now if now is not None else time.time()) - days * 86400 if days else None
    items = []
    for entry in feed.entries:
        if cutoff is not None and entry.get("published_parsed"):
            if calendar.timegm(entry.published_parsed) < cutoff:
                continue
        items.append(
            {
                "title": entry.get("title", "").strip(),
                "source": entry.get("news_source", ""),
                "published": entry.get("published", ""),
                "link": unwrap_link(entry.get("link", "")),
            }
        )
        if len(items) >= max_items:
            break
    return items


def fetch_news(
    query: str,
    days: int = 7,
    max_items: int = 10,
    client: httpx.Client | None = None,
) -> list[dict]:
    """Return recent news items (title, source, published, link) for a free-text query."""
    query = query.strip()
    if not query:
        raise ValueError("query must be non-empty")
    if days < 1 or max_items < 1:
        raise ValueError("days and max_items must be >= 1")

    owns_client = client is None
    client = client or httpx.Client(
        timeout=DEFAULT_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    try:
        response = client.get(BING_NEWS_RSS, params=build_params(query))
        response.raise_for_status()
    finally:
        if owns_client:
            client.close()
    return parse_feed(response.content, max_items, days=days)


def fetch_stock_news(stock: str, days: int = 7, max_items: int = 10, client: httpx.Client | None = None) -> list[dict]:
    """News for an Indian stock (company name or NSE/BSE symbol).

    "shares" is appended to favour company news over quote pages.
    """
    if not stock.strip():
        raise ValueError("stock must be a non-empty company name or symbol")
    return fetch_news(f"{stock.strip()} shares", days, max_items, client)


def fetch_macro_news(topic: str, days: int = 7, max_items: int = 10, client: httpx.Client | None = None) -> list[dict]:
    """News on a macro topic (e.g. "crude oil prices").

    The topic is searched as given: extra words such as "stock market impact" make Bing
    return older items, so market relevance comes from the agent's choice of topics.
    """
    if not topic.strip():
        raise ValueError("topic must be a non-empty string")
    return fetch_news(topic.strip(), days, max_items, client)


def format_items(items: list[dict]) -> str:
    return "\n".join(
        f"{i}. {n['title']} | {n['source']} | {n['published']} | {n['link']}"
        for i, n in enumerate(items, 1)
    )


@tool
def get_stock_news(stock: str, days: int = 7, max_items: int = 10) -> str:
    """Fetch recent news headlines for an Indian listed stock.

    Args:
        stock: Company name or NSE/BSE symbol, e.g. "Reliance Industries" or "TCS".
        days: How many days back to look (default 7).
        max_items: Maximum number of headlines to return (default 10).
    """
    try:
        items = fetch_stock_news(stock, days=days, max_items=max_items)
    except (httpx.HTTPError, ValueError) as exc:
        return f"ERROR: could not fetch news for {stock!r}: {exc}"
    if not items:
        return f"No news found for {stock!r} in the last {days} days."
    return format_items(items)


@tool
def get_macro_news(topic: str, days: int = 7, max_items: int = 8) -> str:
    """Fetch recent headlines on a national or international topic that moves stock markets.

    Args:
        topic: One topic per call, e.g. "RBI repo rate", "crude oil prices", "Union Budget",
            "US Fed and bond yields", "Middle East war", "general election".
        days: How many days back to look (default 7).
        max_items: Maximum number of headlines to return (default 8).
    """
    try:
        items = fetch_macro_news(topic, days=days, max_items=max_items)
    except (httpx.HTTPError, ValueError) as exc:
        return f"ERROR: could not fetch news for {topic!r}: {exc}"
    if not items:
        return f"No news found for {topic!r} in the last {days} days."
    return format_items(items)
