import httpx
import pytest

from stock_agents.news import build_params, fetch_macro_news, fetch_news, fetch_stock_news, get_macro_news, get_stock_news, parse_feed, unwrap_link
from tests.conftest import RSS_SAMPLE


def test_build_params_targets_india_edition():
    params = build_params("TCS shares")
    assert params["q"] == "TCS shares" and params["format"] == "rss"
    assert params["setmkt"] == "en-IN" and params["cc"] == "IN"


def test_unwrap_link_extracts_publisher_url():
    wrapped = "http://www.bing.com/news/apiclick.aspx?ref=FexRss&aid=&url=https%3a%2f%2fexample.com%2fa%3fx%3d1"
    assert unwrap_link(wrapped) == "https://example.com/a?x=1"


def test_unwrap_link_leaves_other_links_alone():
    assert unwrap_link("https://example.com/a") == "https://example.com/a"


def test_parse_feed_extracts_fields_and_limits():
    items = parse_feed(RSS_SAMPLE, max_items=2)
    assert len(items) == 2
    assert items[0]["title"] == "TCS wins $500m deal"
    assert items[0]["source"] == "Economic Times"
    assert items[0]["link"] == "https://example.com/1"
    assert items[0]["published"]


def test_parse_feed_missing_source_is_blank():
    assert parse_feed(RSS_SAMPLE, 10)[2]["source"] == ""


def test_parse_feed_filters_by_age():
    titles = [i["title"] for i in parse_feed(RSS_SAMPLE, 10, days=7)]
    assert "TCS old story" not in titles and len(titles) == 3
    assert len(parse_feed(RSS_SAMPLE, 10)) == 4  # no day filter


def test_parse_feed_garbage_returns_empty():
    assert parse_feed(b"not xml at all", 5) == []


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_fetch_stock_news_uses_query_and_parses():
    seen = {}

    def handler(request):
        seen["url"] = str(request.url)
        return httpx.Response(200, content=RSS_SAMPLE)

    items = fetch_stock_news("TCS", days=3, client=_client(handler))
    assert len(items) == 3  # the 30-day-old item is filtered out
    assert "bing.com/news/search" in seen["url"] and "TCS+shares" in seen["url"]


@pytest.mark.parametrize("kwargs", [{"stock": "  "}, {"stock": "TCS", "days": 0}, {"stock": "TCS", "max_items": 0}])
def test_fetch_stock_news_validates_input(kwargs):
    with pytest.raises(ValueError):
        fetch_stock_news(**kwargs, client=_client(lambda r: httpx.Response(200)))


def test_fetch_stock_news_raises_on_http_error():
    with pytest.raises(httpx.HTTPStatusError):
        fetch_stock_news("TCS", client=_client(lambda r: httpx.Response(503)))


def test_tool_formats_results(monkeypatch):
    monkeypatch.setattr(
        "stock_agents.news.fetch_stock_news",
        lambda stock, days, max_items: [
            {"title": "T", "source": "S", "published": "P", "link": "L"}
        ],
    )
    out = get_stock_news.invoke({"stock": "TCS"})
    assert out == "1. T | S | P | L"


def test_tool_reports_empty(monkeypatch):
    monkeypatch.setattr("stock_agents.news.fetch_stock_news", lambda *a, **k: [])
    assert "No news found" in get_stock_news.invoke({"stock": "XYZ"})


def test_tool_returns_error_string_instead_of_raising(monkeypatch):
    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("stock_agents.news.fetch_stock_news", boom)
    assert get_stock_news.invoke({"stock": "TCS"}).startswith("ERROR:")


def test_macro_search_uses_topic_as_query_and_validates():
    seen = {}

    def handler(request):
        seen["q"] = request.url.params["q"]
        return httpx.Response(200, content=RSS_SAMPLE)

    assert fetch_macro_news("crude oil prices", client=_client(handler))
    assert seen["q"] == "crude oil prices"
    with pytest.raises(ValueError):
        fetch_macro_news(" ", client=_client(handler))


def test_fetch_news_rejects_empty_query():
    with pytest.raises(ValueError):
        fetch_news("  ", client=_client(lambda r: httpx.Response(200)))


def test_macro_tool_formats_empty_and_errors(monkeypatch):
    monkeypatch.setattr("stock_agents.news.fetch_macro_news", lambda *a, **k: [])
    assert "No news found" in get_macro_news.invoke({"topic": "war"})

    def boom(*a, **k):
        raise httpx.ConnectError("down")

    monkeypatch.setattr("stock_agents.news.fetch_macro_news", boom)
    assert get_macro_news.invoke({"topic": "war"}).startswith("ERROR:")


def test_macro_tool_returns_numbered_items(monkeypatch):
    monkeypatch.setattr(
        "stock_agents.news.fetch_macro_news",
        lambda *a, **k: [{"title": "T", "source": "S", "published": "P", "link": "L"}],
    )
    assert get_macro_news.invoke({"topic": "war"}) == "1. T | S | P | L"
