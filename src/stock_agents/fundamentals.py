"""Company financials from Screener.in: the data model, the error type and the fetch entry point.

Screener's tables are kept as generic `Table`s (period labels plus labelled rows) instead of a fixed
set of fields, so a new row on the page shows up in the report without code changes, and the
differences between ordinary companies and banks/NBFCs stay in the labels. All amounts are in
INR crore, as Screener shows them.
"""
from __future__ import annotations

from dataclasses import dataclass, field, replace
from datetime import date

SOURCE = "Screener.in"


class FundamentalsError(RuntimeError):
    """The company could not be found or fetched, or the page layout was not understood."""


@dataclass(frozen=True)
class Table:
    """A table of numbers: one value per period for each labelled row (`None` = blank on the page)."""

    periods: list[str]  # e.g. "Sep 2026", "Mar 2026", "TTM"
    rows: dict[str, list[float | None]] = field(default_factory=dict)
    percent: frozenset[str] = frozenset()  # labels of rows whose values are percentages
    decimals: int = 0  # decimals shown for non-EPS rows
    signed: bool = False  # show every value with an explicit + or - (for tables of changes)
    suffix: str = ""  # appended to the values of non-percentage rows (e.g. " pp")

    def __bool__(self) -> bool:
        return bool(self.periods and self.rows)

    def find(self, *labels: str) -> str | None:
        """The actual row label matching the first of `labels` (case-insensitive; exact match
        preferred over a prefix match), so callers can ask for "Sales" or "Revenue"."""
        for wanted in labels:
            low = wanted.lower()
            for actual in self.rows:
                if actual.lower() == low:
                    return actual
            for actual in self.rows:
                if actual.lower().startswith(low):
                    return actual
        return None

    def values(self, *labels: str) -> list[float | None] | None:
        label = self.find(*labels)
        return self.rows[label] if label else None

    def value_at(self, period: str, *labels: str) -> float | None:
        values = self.values(*labels)
        if values is None or period not in self.periods:
            return None
        return values[self.periods.index(period)]

    def last(self, count: int) -> "Table":
        """Only the newest `count` periods (the TTM column of annual tables counts as a period)."""
        if count >= len(self.periods):
            return self
        return replace(self, periods=self.periods[-count:], rows={k: v[-count:] for k, v in self.rows.items()})


EMPTY = Table(periods=[])


@dataclass(frozen=True)
class Peer:
    name: str
    symbol: str
    company_id: str  # Screener's id, used to tell which row is the company itself
    values: list[float | None]  # aligned with `PeerComparison.columns`


@dataclass(frozen=True)
class PeerComparison:
    """Screener's peer table: the company and the other companies in its industry, side by side."""

    columns: list[str]  # e.g. "P/E", "Div Yld %", "ROCE %"
    peers: list[Peer]
    median_label: str  # e.g. "Median: 73 Co." - the median covers a wider group than the rows listed
    median: list[float | None]

    def column(self, *labels: str) -> int | None:
        for wanted in labels:
            for i, name in enumerate(self.columns):
                if name.lower().startswith(wanted.lower()):
                    return i
        return None


@dataclass(frozen=True)
class CompanyFinancials:
    symbol: str  # Screener's slug, normally the NSE symbol
    name: str
    url: str
    basis: str  # "consolidated" or "standalone"
    kind: str  # "general", or "financial" for banks/NBFCs (different rows, EBITDA-style metrics meaningless)
    fetched: date
    quarterly: Table  # profit & loss by quarter, oldest first
    profit_loss: Table  # profit & loss by financial year, oldest first, ending with a TTM column
    balance_sheet: Table = EMPTY  # annual
    cash_flow: Table = EMPTY  # annual
    ratios: Table = EMPTY  # annual (debtor days, ROCE, ...)
    shareholding: Table = EMPTY  # quarterly, % held
    key_ratios: dict[str, str] = field(default_factory=dict)  # market cap, P/E, ROCE, ... as displayed
    growth: dict[str, dict[str, str]] = field(default_factory=dict)  # "Compounded Sales Growth" -> {"5 Years": "12%"}
    pros: list[str] = field(default_factory=list)  # Screener's own generated remarks
    cons: list[str] = field(default_factory=list)
    company_id: str = ""
    warehouse_id: str = ""  # id used by Screener's peer-table endpoint
    classification: list[str] = field(default_factory=list)  # sector > industry, as Screener files the company
    peers: PeerComparison | None = None  # fetched separately; None if unavailable

    @property
    def is_financial(self) -> bool:
        return self.kind == "financial"


def fetch_fundamentals(stock: str, client=None, listings=None, sleep=None) -> CompanyFinancials:
    """Resolve a company name or symbol and download its Screener page (consolidated if available)."""
    from stock_agents.screener import fetch_company
    from stock_agents.symbols import load_listings, resolve_symbol

    symbol = resolve_symbol(stock, load_listings(client=client) if listings is None else listings)
    return fetch_company(symbol, client=client, **({"sleep": sleep} if sleep else {}))
