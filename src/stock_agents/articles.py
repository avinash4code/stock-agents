"""Fetch a news article and extract its main text with trafilatura."""
from __future__ import annotations

import ipaddress
from urllib.parse import urlparse

import httpx
import trafilatura
from langchain_core.tools import tool

DEFAULT_TIMEOUT = 10.0
MAX_CHARS = 6000
MIN_CHARS = 200  # shorter than this is almost always a paywall/consent stub
USER_AGENT = "Mozilla/5.0 (compatible; stock-agents/0.1)"


class ArticleUnavailable(Exception):
    """The article could not be read; the agent should fall back to the headline."""


def _validate_url(url: str) -> None:
    parsed = urlparse(url)
    if parsed.scheme not in ("http", "https") or not parsed.hostname:
        raise ArticleUnavailable("only http(s) URLs are supported")
    host = parsed.hostname.lower()
    if host == "localhost" or host.endswith(".local"):
        raise ArticleUnavailable("local addresses are not allowed")
    try:
        ip = ipaddress.ip_address(host)
    except ValueError:
        pass
    else:
        if not ip.is_global:
            raise ArticleUnavailable("non-public addresses are not allowed")
    if host == "news.google.com":
        raise ArticleUnavailable(
            "Google News redirect links cannot be resolved to the publisher page"
        )


def extract_text(html: str, max_chars: int = MAX_CHARS) -> str:
    text = trafilatura.extract(html, include_comments=False, include_tables=False)
    if not text or len(text) < MIN_CHARS:
        raise ArticleUnavailable("no readable article text (paywall, bot block or JS-only page)")
    return text[:max_chars]


def fetch_article_text(
    url: str, max_chars: int = MAX_CHARS, client: httpx.Client | None = None
) -> str:
    _validate_url(url)
    owns_client = client is None
    client = client or httpx.Client(
        timeout=DEFAULT_TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT}
    )
    try:
        response = client.get(url)
        response.raise_for_status()
    except httpx.HTTPError as exc:
        raise ArticleUnavailable(f"fetch failed: {exc}") from exc
    finally:
        if owns_client:
            client.close()
    return extract_text(response.text, max_chars)


@tool
def read_article(url: str) -> str:
    """Read the full text of a news article from its URL (truncated to ~6000 characters).

    Use on the most relevant headlines when the headline alone is not enough. If it
    returns an ERROR, fall back to the headline and do not retry the same URL.
    """
    try:
        return fetch_article_text(url)
    except ArticleUnavailable as exc:
        return f"ERROR: could not read {url}: {exc}"
