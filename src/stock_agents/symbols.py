"""Company name or symbol -> the symbol Screener uses, via NSE's official list of listed equities.

Screener's own search endpoint is disallowed for automated clients by its robots.txt, so names such
as "Infosys" are matched locally against NSE's `EQUITY_L.csv` (downloaded once, cached on disk).
"""
from __future__ import annotations

import csv
import io
import logging
import re
import time
from dataclasses import dataclass
from datetime import timedelta
from pathlib import Path

import httpx

from stock_agents.fundamentals import FundamentalsError

logger = logging.getLogger(__name__)

LISTINGS_URL = "https://nsearchives.nseindia.com/content/equities/EQUITY_L.csv"
CACHE_FILENAME = "nse_equities.csv"
MAX_AGE = timedelta(days=30)
USER_AGENT = "Mozilla/5.0 (compatible; stock-agents/0.1; personal research tool)"
SUFFIXES = (".NS", ".BO")
NAME_NOISE = {"limited", "ltd", "the"}


@dataclass(frozen=True)
class Listing:
    symbol: str
    name: str


def parse_listings(text: str) -> list[Listing]:
    reader = csv.DictReader(io.StringIO(text))
    listings = []
    for row in reader:
        row = {(k or "").strip().upper(): (v or "").strip() for k, v in row.items()}
        if row.get("SYMBOL") and row.get("NAME OF COMPANY"):
            listings.append(Listing(row["SYMBOL"], row["NAME OF COMPANY"]))
    return listings


def default_cache_path() -> Path:
    from stock_agents.config import db_path

    return db_path().parent / CACHE_FILENAME


def load_listings(
    cache_path: Path | None = None, client: httpx.Client | None = None,
    max_age: timedelta = MAX_AGE, now: float | None = None,
) -> list[Listing]:
    """NSE's listed equities: from the cache file if it is fresh, else downloaded (and cached).

    Never raises: if the download fails, a stale cache is used, else the list is empty (and
    `resolve_symbol` then only accepts things that already look like symbols).
    """
    path = cache_path or default_cache_path()
    now = time.time() if now is None else now
    cached = path.read_text(encoding="utf-8") if path.exists() else None
    if cached is not None and now - path.stat().st_mtime < max_age.total_seconds():
        return parse_listings(cached)
    owns_client = client is None
    client = client or httpx.Client(timeout=20.0, follow_redirects=True, headers={"User-Agent": USER_AGENT})
    try:
        response = client.get(LISTINGS_URL)
        response.raise_for_status()
        text = response.text
        listings = parse_listings(text)
        if not listings:
            raise ValueError("no rows in the NSE list")
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(text, encoding="utf-8")
        return listings
    except (httpx.HTTPError, ValueError, OSError) as exc:
        logger.warning("Could not refresh the NSE company list (%s)%s", exc, "; using the stale copy" if cached else "")
        return parse_listings(cached) if cached else []
    finally:
        if owns_client:
            client.close()


def normalize_name(name: str) -> str:
    words = re.sub(r"[^a-z0-9]+", " ", name.lower().replace("&", " and ")).split()
    while words and words[-1] in NAME_NOISE:
        words.pop()
    return " ".join(w for w in words if w != "the")


def search_listings(query: str, listings: list[Listing], limit: int = 10) -> list[Listing]:
    """Typeahead matches for a partial company name or symbol, best first.

    Order: exact symbol, symbol prefix, name starting with the text, every typed word starting
    some word of the name, then the text appearing anywhere in the name.
    """
    text = query.strip()
    if suffix := next((s for s in SUFFIXES if text.upper().endswith(s)), None):
        text = text[: -len(suffix)]
    if not text:
        return []
    upper, wanted = text.upper(), normalize_name(text)
    tokens = wanted.split()
    ranked: list[tuple[int, int, str, Listing]] = []
    for listing in listings:
        name = normalize_name(listing.name)
        words = name.split()
        if listing.symbol == upper:
            rank = 0
        elif listing.symbol.startswith(upper):
            rank = 1
        elif wanted and name.startswith(wanted):
            rank = 2
        elif tokens and all(any(w.startswith(t) for w in words) for t in tokens):
            rank = 3
        elif wanted and wanted in name:
            rank = 4
        else:
            continue
        ranked.append((rank, len(listing.name), listing.symbol, listing))
    ranked.sort(key=lambda r: r[:3])
    return [r[3] for r in ranked[:limit]]


def resolve_symbol(query: str, listings: list[Listing]) -> str:
    """The Screener symbol for a company name or symbol.

    `TCS`, `tcs.ns` and `Tata Consultancy Services` all give `TCS`; a 5-6 digit BSE code is passed
    through. A name matching several companies is an error that lists the candidates.
    """
    query = query.strip()
    if not query:
        raise FundamentalsError("stock must be a non-empty company name or symbol")
    for suffix in SUFFIXES:
        if query.upper().endswith(suffix):
            query = query[: -len(suffix)]
    if re.fullmatch(r"\d{5,6}", query):  # BSE scrip code
        return query
    upper = query.upper()
    symbols = {listing.symbol for listing in listings}
    if " " not in query and upper in symbols:
        return upper

    wanted = normalize_name(query)
    exact = [l for l in listings if normalize_name(l.name) == wanted]
    if len(exact) == 1:
        return exact[0].symbol
    matches = exact
    if not matches:
        tokens = set(wanted.split())
        matches = [l for l in listings if tokens and tokens <= set(normalize_name(l.name).split())]
        if len(matches) == 1:
            return matches[0].symbol
    if len(matches) > 1:
        options = ", ".join(f"{l.name} ({l.symbol})" for l in sorted(matches, key=lambda l: len(l.name))[:5])
        raise FundamentalsError(f"{query!r} matches several companies: {options}. Use the exchange symbol.")
    if re.fullmatch(r"[A-Za-z0-9&-]+", query):
        return upper  # not in the list (stale or empty list, or BSE-only): let Screener decide
    raise FundamentalsError(
        f"could not find a listed company named {query!r}; use the NSE symbol instead, e.g. 'TATAMOTORS' or 'INFY'"
    )
