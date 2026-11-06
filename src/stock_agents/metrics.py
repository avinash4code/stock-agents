"""Deterministic fundamentals maths and the markdown the LLM reads.

All arithmetic happens here, not in the model: the fundamentals agent only interprets the numbers
it is given. A derived value is `None` (shown as n/a) when its inputs are missing or meaningless.
"""
from __future__ import annotations

import re
from dataclasses import replace

from stock_agents.fundamentals import SOURCE, CompanyFinancials, Table

NA = "n/a"
QUARTERS_SHOWN = 10
YEARS_SHOWN = 8  # financial years in the annual P&L (plus the TTM column)
ANNUAL_SHOWN = 6
SHAREHOLDING_SHOWN = 8
SIGNED_HINTS = ("YoY", "QoQ", "growth", "change", "Δ")  # shown with an explicit + or -
MONTHS = ["Jan", "Feb", "Mar", "Apr", "May", "Jun", "Jul", "Aug", "Sep", "Oct", "Nov", "Dec"]


def pct_change(current: float | None, previous: float | None) -> float | None:
    """Percent change; None if either side is missing or the base is not positive (the change would
    be meaningless, e.g. profit growth off a loss)."""
    if current is None or previous is None or previous <= 0:
        return None
    return (current - previous) / previous * 100


def ratio(numerator: float | None, denominator: float | None, scale: float = 1.0) -> float | None:
    if numerator is None or denominator is None or denominator == 0:
        return None
    return numerator / denominator * scale


def _split(period: str) -> tuple[int, int] | None:
    """`"Sep 2026"` -> (2026, 9); `"TTM"` -> None."""
    match = re.fullmatch(r"([A-Za-z]{3}) (\d{4})", period)
    if not match or match[1].title() not in MONTHS:
        return None
    return int(match[2]), MONTHS.index(match[1].title()) + 1


def _label(year: int, month: int) -> str:
    return f"{MONTHS[month - 1]} {year}"


def year_before(period: str) -> str | None:
    parts = _split(period)
    return _label(parts[0] - 1, parts[1]) if parts else None


def quarter_before(period: str) -> str | None:
    parts = _split(period)
    if not parts:
        return None
    year, month = parts
    month -= 3
    if month < 1:
        year, month = year - 1, month + 12
    return _label(year, month)


def _by_period(table: Table, *labels: str) -> dict[str, float | None]:
    values = table.values(*labels)
    return dict(zip(table.periods, values)) if values is not None else {}


def _derived(periods: list[str], rows: dict[str, list[float | None]], percent_labels=None, decimals: int = 1) -> Table:
    rows = {k: v for k, v in rows.items() if any(x is not None for x in v)}
    percent = frozenset(k for k in rows if k.endswith("%")) if percent_labels is None else frozenset(percent_labels)
    return Table(periods=periods, rows=rows, percent=percent, decimals=decimals)


def _growth_row(table: Table, labels: tuple[str, ...], earlier) -> list[float | None]:
    """Percent change of a row against the period `earlier(period)` names, for every period."""
    series = _by_period(table, *labels)
    out = []
    for period in table.periods:
        before = earlier(period)
        out.append(pct_change(series.get(period), series.get(before)) if before else None)
    return out


def _share_of(numerator: list[float | None] | None, denominator: list[float | None] | None, scale=100.0):
    if numerator is None or denominator is None:
        return None
    return [ratio(n, d, scale) for n, d in zip(numerator, denominator)]


SALES = ("Sales", "Revenue")


def quarterly_derived(fin: CompanyFinancials) -> Table:
    q = fin.quarterly
    rows: dict[str, list[float | None]] = {
        "Sales YoY %": _growth_row(q, SALES, year_before),
        "Sales QoQ %": _growth_row(q, SALES, quarter_before),
        "Net profit YoY %": _growth_row(q, ("Net Profit",), year_before),
        "Net profit QoQ %": _growth_row(q, ("Net Profit",), quarter_before),
        "EPS YoY %": _growth_row(q, ("EPS",), year_before),
        "Net margin %": _share_of(q.values("Net Profit"), q.values(*SALES)),
        "Other income / PBT %": _share_of(q.values("Other Income"), q.values("Profit before tax")),
    }
    if not fin.is_financial:  # operating profit and interest cover mean something only outside financial companies
        rows["Operating profit YoY %"] = _growth_row(q, ("Operating Profit",), year_before)
        interest, operating = q.values("Interest"), q.values("Operating Profit")
        if interest is not None and operating is not None:
            rows["Interest cover (x)"] = [ratio(o, i) if i else None for o, i in zip(operating, interest)]
    return _derived(q.periods, {k: v for k, v in rows.items() if v is not None})


def ttm_summary(fin: CompanyFinancials) -> dict[str, float | None]:
    """Sum of the last four quarters, against the four before them (needs eight quarters for growth)."""
    q = fin.quarterly
    out: dict[str, float | None] = {}
    for key, labels in (("sales", SALES), ("net_profit", ("Net Profit",)), ("eps", ("EPS",))):
        values = q.values(*labels)
        last4 = values[-4:] if values and len(values) >= 4 else None
        prev4 = values[-8:-4] if values and len(values) >= 8 else None
        total = sum(last4) if last4 and None not in last4 else None
        before = sum(prev4) if prev4 and None not in prev4 else None
        out[key], out[f"{key}_growth"] = total, pct_change(total, before)
    return out


def annual_derived(fin: CompanyFinancials) -> Table:
    pl = fin.profit_loss
    rows = {
        "Net profit growth %": _growth_row(pl, ("Net Profit",), year_before),
        "EPS growth %": _growth_row(pl, ("EPS",), year_before),
        "Net margin %": _share_of(pl.values("Net Profit"), pl.values(*SALES)),
        "Other income / PBT %": _share_of(pl.values("Other Income"), pl.values("Profit before tax")),
    }
    return _derived(pl.periods, {k: v for k, v in rows.items() if v is not None})


def balance_derived(fin: CompanyFinancials) -> Table:
    bs = fin.balance_sheet
    capital, reserves = bs.values("Equity Capital"), bs.values("Reserves")
    if capital is None or reserves is None:
        return Table(periods=[])
    net_worth = [None if c is None or r is None else c + r for c, r in zip(capital, reserves)]
    rows: dict[str, list[float | None]] = {"Net worth (equity + reserves)": net_worth}
    borrowings = bs.values("Borrowings", "Borrowing")
    if borrowings is not None and not fin.is_financial:
        rows["Debt / net worth (x)"] = [ratio(b, n) if n and n > 0 else None for b, n in zip(borrowings, net_worth)]
    return _derived(bs.periods, rows, percent_labels=(), decimals=0)


def cash_flow_derived(fin: CompanyFinancials) -> Table:
    cf = fin.cash_flow
    cfo = _by_period(cf, "Cash from Operating")
    net_profit = _by_period(fin.profit_loss, "Net Profit")
    row = [ratio(cfo.get(p), net_profit.get(p)) if (net_profit.get(p) or 0) > 0 else None for p in cf.periods]
    return _derived(cf.periods, {"Operating cash flow / net profit (x)": row}, percent_labels=())


def shareholding_changes(fin: CompanyFinancials) -> Table:
    """Change in each holder category's share, in percentage points, over 1, 4 and 8 quarters."""
    sh = fin.shareholding
    if not sh:
        return Table(periods=[])
    spans = [(1, "Δ 1Q"), (4, "Δ 4Q"), (8, "Δ 8Q")]
    rows: dict[str, list[float | None]] = {}
    for label, values in sh.rows.items():
        if label.lower().startswith("no. of"):
            continue
        row = []
        for back, _ in spans:
            now, then = values[-1], values[-1 - back] if len(values) > back else None
            row.append(None if now is None or then is None else now - then)
        rows[label] = row
    holders = sh.values("No. of Shareholders")
    holder_row = [pct_change(holders[-1], holders[-1 - back]) if holders and len(holders) > back else None for back, _ in spans]
    rows["No. of shareholders (% change)"] = holder_row
    table = _derived([name for _, name in spans], rows, decimals=2)
    holders = "No. of shareholders (% change)"
    return replace(table, percent=frozenset({holders} & set(table.rows)), signed=True, suffix=" pp")


# -- rendering ----------------------------------------------------------------------------------

def fmt_value(label: str, value: float | None, percent: bool, decimals: int, signed: bool = False, suffix: str = "") -> str:
    if value is None:
        return NA
    if label.endswith("(x)"):
        return f"{value:.1f}x"
    if label.lower().startswith("no. of") and not percent:  # counts, e.g. number of shareholders
        places = 0
    else:
        places = 2 if label.startswith("EPS") and not label.endswith("%") else decimals
    signed = signed or any(hint in label for hint in SIGNED_HINTS)
    text = f"{value:+,.{places}f}" if signed else f"{value:,.{places}f}"
    return text + ("%" if percent else suffix)


def render_table(title: str, table: Table) -> str:
    if not table:
        return ""
    head = "| " + " | ".join([title, *table.periods]) + " |"
    lines = [head, "|" + "---|" * (len(table.periods) + 1)]
    for label, values in table.rows.items():
        if all(v is None for v in values):
            continue
        cells = [fmt_value(label, v, label in table.percent, table.decimals, table.signed, table.suffix) for v in values]
        lines.append("| " + " | ".join([label, *cells]) + " |")
    return "\n".join(lines)


def _ttm_line(fin: CompanyFinancials) -> str:
    t = ttm_summary(fin)

    def part(name, total, growth, money=True):
        if total is None:
            return None
        text = f"{name} {total:,.0f}" if money else f"{name} {total:,.2f}"
        return text + (f" ({growth:+.1f}% vs the previous four quarters)" if growth is not None else "")

    parts = [part("sales", t["sales"], t["sales_growth"]), part("net profit", t["net_profit"], t["net_profit_growth"]),
             part("EPS", t["eps"], t["eps_growth"], money=False)]
    parts = [p for p in parts if p]
    return f"Last four quarters (INR crore): {'; '.join(parts)}" if parts else ""


def _remarks(title: str, remarks: list[str]) -> str:
    return f"{title}\n" + "\n".join(f"- {r}" for r in remarks) if remarks else ""


def render_data_section(fin: CompanyFinancials) -> str:
    """Everything fetched, as markdown: stored at the top of the fundamentals report."""
    kind = "bank / financial company (EBITDA-style rows are not meaningful)" if fin.is_financial else "non-financial company"
    header = (
        f"## Financial data: {fin.name}\n"
        f"_Source: {SOURCE} ({fin.url or fin.symbol}); symbol {fin.symbol}; **{fin.basis}** figures; {kind}; "
        f"amounts in INR crore; fetched {fin.fetched:%d %b %Y}_"
    )
    key = ", ".join(f"{k}: {v}" for k, v in fin.key_ratios.items())
    growth = [f"{title}: " + ", ".join(f"{k} {v}" for k, v in entries.items()) for title, entries in fin.growth.items()]
    sections = [
        header,
        f"**Key ratios at fetch time:** {key}" if key else "",
        "### Quarterly results\n" + render_table("Quarter", fin.quarterly.last(QUARTERS_SHOWN)),
        render_table("Derived (quarterly)", quarterly_derived(fin).last(QUARTERS_SHOWN)),
        _ttm_line(fin),
        "### Annual profit and loss (TTM = trailing twelve months)\n"
        + render_table("Financial year", fin.profit_loss.last(YEARS_SHOWN + 1)),
        render_table("Derived (annual)", annual_derived(fin).last(YEARS_SHOWN + 1)),
        "### Balance sheet (annual)\n" + render_table("Year-end", fin.balance_sheet.last(ANNUAL_SHOWN))
        if fin.balance_sheet else "",
        render_table("Derived (balance sheet)", balance_derived(fin).last(ANNUAL_SHOWN)),
        "### Cash flow (annual)\n" + render_table("Financial year", fin.cash_flow.last(ANNUAL_SHOWN))
        if fin.cash_flow else "",
        render_table("Derived (cash flow)", cash_flow_derived(fin).last(ANNUAL_SHOWN)),
        "### Efficiency ratios (annual)\n" + render_table("Financial year", fin.ratios.last(ANNUAL_SHOWN))
        if fin.ratios else "",
        "### Shareholding (% held, quarterly)\n" + render_table("Quarter", fin.shareholding.last(SHAREHOLDING_SHOWN))
        if fin.shareholding else "",
        render_table("Shareholding change (percentage points)", shareholding_changes(fin)),
        "### Long-run growth (as displayed by Screener)\n" + "\n".join(f"- {g}" for g in growth) if growth else "",
        _remarks("### Screener's own remarks (auto-generated hints, not verified facts)\nPros:", fin.pros),
        _remarks("Cons:", fin.cons),
    ]
    return "\n\n".join(s for s in sections if s)
