import httpx
import pytest

from stock_agents.articles import ArticleUnavailable, extract_text, fetch_article_text, read_article

BODY = " ".join(["Infosys reported quarterly results and cut its revenue guidance for the year."] * 8)
HTML = f"""<html><body><nav><a href='/'>Home</a><a href='/x'>Markets</a></nav>
<article><h1>Infosys falls</h1><p>{BODY}</p></article>
<footer>Subscribe to our newsletter</footer></body></html>"""


def _client(handler):
    return httpx.Client(transport=httpx.MockTransport(handler))


def test_extract_text_returns_body_without_boilerplate():
    text = extract_text(HTML)
    assert "cut its revenue guidance" in text and "newsletter" not in text


def test_extract_text_truncates():
    assert len(extract_text(HTML, max_chars=50)) == 50


def test_extract_text_rejects_stub_pages():
    with pytest.raises(ArticleUnavailable):
        extract_text("<html><body><p>Please subscribe to read.</p></body></html>")


@pytest.mark.parametrize(
    "url",
    [
        "ftp://example.com/a",
        "file:///etc/passwd",
        "http://localhost/a",
        "http://127.0.0.1/a",
        "http://169.254.169.254/latest/meta-data",
        "http://192.168.1.1/",
        "https://news.google.com/rss/articles/abc",
    ],
)
def test_unsafe_or_unresolvable_urls_rejected(url):
    with pytest.raises(ArticleUnavailable):
        fetch_article_text(url, client=_client(lambda r: httpx.Response(200, text=HTML)))


def test_fetch_article_text_happy_path():
    assert "Infosys" in fetch_article_text(
        "https://example.com/a", client=_client(lambda r: httpx.Response(200, text=HTML))
    )


def test_http_error_becomes_article_unavailable():
    with pytest.raises(ArticleUnavailable, match="fetch failed"):
        fetch_article_text("https://example.com/a", client=_client(lambda r: httpx.Response(403)))


def test_tool_returns_error_string(monkeypatch):
    def boom(url):
        raise ArticleUnavailable("nope")

    monkeypatch.setattr("stock_agents.articles.fetch_article_text", boom)
    assert read_article.invoke({"url": "https://example.com/a"}) == (
        "ERROR: could not read https://example.com/a: nope"
    )
