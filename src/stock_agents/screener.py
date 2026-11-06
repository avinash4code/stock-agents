"""Fetch and parse a company page from Screener.in (plain HTML, no login, one request per company).

Be a polite client: identify ourselves, wait between requests, retry once on throttling, never log in
and never use the search endpoints that robots.txt disallows. Parsing is strict: if the page does not
have the structure we expect we raise instead of returning partial numbers.
"""
from __future__ import annotations

import logging
import re
import time
from datetime import date
from urllib.parse import quote

import httpx
from lxml import html

from stock_agents.fundamentals import EMPTY, CompanyFinancials, FundamentalsError, Table

logger = logging.getLogger(__name__)

BASE_URL = "https://www.screener.in/company"
USER_AGENT = "Mozilla/5.0 (compatible; stock-agents/0.1; personal research tool)"
TIMEOUT = 20.0
REQUEST_GAP = 1.5  # seconds between our requests to Screener
MAX_RETRY_WAIT = 30.0
SKIP_LABELS = {"raw pdf"}


# -- fetching -----------------------------------------------------------------------------------

def _get(client: httpx.Client, url: str, sleep) -> httpx.Response:
    """GET with one retry when throttled (429) or on a server error (5xx)."""
    for attempt in (1, 2):
        try:
            response = client.get(url)
        except httpx.HTTPError as exc:
            raise FundamentalsError(f"could not reach Screener ({type(exc).__name__}: {exc})") from exc
        if response.status_code in (429, 500, 502, 503, 504) and attempt == 1:
            try:
                wait = float(response.headers.get("Retry-After", "5"))
            except ValueError:
                wait = 5.0
            logger.warning("Screener returned %s; retrying in %.0fs", response.status_code, min(wait, MAX_RETRY_WAIT))
            sleep(min(wait, MAX_RETRY_WAIT))
            continue
        return response
    raise AssertionError("unreachable")


def fetch_company(symbol: str, client: httpx.Client | None = None, sleep=time.sleep, today: date | None = None) -> CompanyFinancials:
    """Download and parse a company's page: the consolidated view, else the standalone one."""
    owns_client = client is None
    client = client or httpx.Client(timeout=TIMEOUT, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        path = quote(symbol, safe="&-")
        urls = [f"{BASE_URL}/{path}/consolidated/", f"{BASE_URL}/{path}/"]
        for i, url in enumerate(urls):
            if i:
                sleep(REQUEST_GAP)
            response = _get(client, url, sleep)
            if response.status_code == 404:
                continue
            if response.status_code in (401, 403, 429):
                raise FundamentalsError(
                    f"Screener refused the request (HTTP {response.status_code}); it may be throttling or blocking automated access - try again later"
                )
            if response.status_code != 200:
                raise FundamentalsError(f"Screener returned HTTP {response.status_code} for {symbol!r}")
            return parse_company_page(response.text, str(response.url), today=today)
        raise FundamentalsError(
            f"Screener has no company page for {symbol!r}; check the symbol (use the NSE symbol, e.g. 'INFY')"
        )
    finally:
        if owns_client:
            client.close()


# -- parsing ------------------------------------------------------------------------------------

def _clean(text: str) -> str:
    return " ".join(text.split())


def parse_number(text: str) -> float | None:
    """`"9,03,816"`, `"+8%"`, `"-96"`, `"14.38%"` -> floats; blank or non-numeric -> None."""
    cleaned = re.sub(r"[,%+₹\s]|Cr\.?", "", text)
    if not cleaned:
        return None
    try:
        return float(cleaned)
    except ValueError:
        return None


def _row_label(cell) -> str:
    """The row name: the first text in the cell (before any '+' toggle or tooltip)."""
    for text in cell.xpath(".//text()"):
        cleaned = _clean(text)
        if cleaned:
            return cleaned
    return ""


def parse_table(table, decimals: int = 0) -> Table:
    """A Screener data table: header = period labels, rows = label then one cell per period."""
    periods = [_clean(th.text_content()) for th in table.xpath(".//thead//th")][1:]
    if not periods:
        raise FundamentalsError("Screener page layout changed: a financial table has no period headings")
    rows: dict[str, list[float | None]] = {}
    percent = set()
    for tr in table.xpath(".//tbody/tr"):
        cells = tr.xpath("./td")
        label = _row_label(cells[0]) if cells else ""
        if not label or label.lower() in SKIP_LABELS:
            continue
        texts = [_clean(c.text_content()) for c in cells[1:]]
        if len(texts) != len(periods):
            raise FundamentalsError(
                f"Screener page layout changed: row {label!r} has {len(texts)} values for {len(periods)} periods"
            )
        original, n = label, 2
        while label in rows:  # the same label twice in one table (should not happen): keep both
            label, n = f"{original} ({n})", n + 1
        rows[label] = [parse_number(t) for t in texts]
        if any("%" in t for t in texts):
            percent.add(label)
    return Table(periods=periods, rows=rows, percent=frozenset(percent), decimals=decimals)


def _section_table(root, section_id: str, container: str | None = None, required: bool = False, decimals: int = 0) -> Table:
    scope = f"//section[@id='{section_id}']" + (f"//div[@id='{container}']" if container else "")
    tables = root.xpath(f"{scope}//table[.//thead]")
    if not tables:
        if required:
            raise FundamentalsError(f"Screener page layout changed: no '{section_id}' table found")
        return EMPTY
    return parse_table(tables[0], decimals)


def _text(root, xpath: str) -> str:
    return _clean(root.xpath(f"string({xpath})"))


def _key_ratios(root) -> dict[str, str]:
    ratios = {}
    for item in root.xpath("//ul[@id='top-ratios']/li"):
        name, value = _text(item, ".//span[@class='name']"), _text(item, ".//span[contains(@class,'value')]")
        if name and value:
            ratios[name] = value
    return ratios


def _growth(root) -> dict[str, dict[str, str]]:
    growth = {}
    for table in root.xpath("//table[contains(@class,'ranges-table')]"):
        title = _clean(table.xpath("string(.//th)"))
        entries = {}
        for tr in table.xpath(".//tr[td]"):
            key, value = (_clean(td.text_content()) for td in tr.xpath("./td")[:2])
            entries[key.rstrip(":")] = value
        if title and entries:
            growth[title] = entries
    return growth


def _remarks(root, kind: str) -> list[str]:
    return [_clean(li.text_content()) for li in root.xpath(f"//div[@class='{kind}']//li")]


def _symbol_from(url: str, root) -> str:
    for candidate in (root.xpath("string(//link[@rel='canonical']/@href)"), url):
        match = re.search(r"/company/([^/?#]+)", candidate)
        if match:
            return match.group(1)
    return ""


def parse_company_page(page: str, url: str = "", today: date | None = None) -> CompanyFinancials:
    """Turn a Screener company page into `CompanyFinancials`; raises `FundamentalsError` on a layout we do not know."""
    root = html.fromstring(page)
    quarterly = _section_table(root, "quarters", required=True)
    profit_loss = _section_table(root, "profit-loss", required=True)
    balance_sheet = _section_table(root, "balance-sheet")
    for name, table in (("quarterly", quarterly), ("annual profit & loss", profit_loss)):
        if table.find("Net Profit") is None or table.find("Sales", "Revenue") is None:
            raise FundamentalsError(f"Screener page layout changed: the {name} table has no sales/net profit rows")

    figures = _text(root, "//section[@id='quarters']//p[contains(@class,'sub')]").lower()
    basis = "consolidated" if figures.startswith("consolidated") else "standalone" if figures.startswith("standalone") else ""
    if not basis:
        raise FundamentalsError("Screener page layout changed: cannot tell whether the figures are consolidated or standalone")
    financial = quarterly.find("Financing Profit") is not None or balance_sheet.find("Deposits") is not None

    return CompanyFinancials(
        symbol=_symbol_from(url, root),
        name=_text(root, "//h1") or _symbol_from(url, root),
        url=url,
        basis=basis,
        kind="financial" if financial else "general",
        fetched=today or date.today(),
        quarterly=quarterly,
        profit_loss=profit_loss,
        balance_sheet=balance_sheet,
        cash_flow=_section_table(root, "cash-flow"),
        ratios=_section_table(root, "ratios"),
        shareholding=_section_table(root, "shareholding", container="quarterly-shp", decimals=2),
        key_ratios=_key_ratios(root),
        growth=_growth(root),
        pros=_remarks(root, "pros"),
        cons=_remarks(root, "cons"),
    )
