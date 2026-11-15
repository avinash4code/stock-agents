"""The header figures of the stock page (price, market cap, P/E, 52-week range, peer median P/E).

These come straight from Screener's page, never from the model, and are stored with the fundamentals
report's structured block so the page can draw its header without another fetch.
"""
from __future__ import annotations

import re

from stock_agents.fundamentals import CompanyFinancials
from stock_agents.screener import parse_number


def _numbers(text: str) -> list[float]:
    return [n for n in (parse_number(part) for part in re.findall(r"[\d,]+(?:\.\d+)?", text)) if n is not None]


def build_snapshot(fin: CompanyFinancials) -> dict:
    """Plain-JSON header data; any figure the page did not give is None."""
    ratios = fin.key_ratios
    quote = fin.quote
    price = quote.price if quote and quote.price is not None else next(iter(_numbers(ratios.get("Current Price", ""))), None)
    high_low = _numbers(ratios.get("High / Low", ""))
    market_cap = next(iter(_numbers(ratios.get("Market Cap", ""))), None)
    pe = next(iter(_numbers(ratios.get("Stock P/E", ""))), None)

    median_pe, median_label = None, None
    pc = fin.peers
    if pc is not None:
        i = pc.column("P/E")
        if i is not None and i < len(pc.median):
            median_pe, median_label = pc.median[i], pc.median_label
    return {
        "name": fin.name,
        "symbol": fin.symbol,
        "sector": list(fin.classification),
        "basis": fin.basis,
        "price": price,
        "change_pct": quote.change_pct if quote else None,
        "price_as_of": quote.as_of if quote else "",
        "market_cap_cr": market_cap,
        "pe": pe,
        "peer_median_pe": median_pe,
        "peer_median_label": median_label,
        "low_52w": min(high_low) if len(high_low) == 2 else None,
        "high_52w": max(high_low) if len(high_low) == 2 else None,
        "fetched": fin.fetched.isoformat(),
    }
