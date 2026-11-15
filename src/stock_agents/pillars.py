"""Sector-specific pillars: what the fundamentals agent is asked to rate.

Code, not the model, decides which pillars a company is rated on, so cards stay comparable between runs and
the UI can rely on stable keys. The profile is picked from Screener's own classification of the company
(`CompanyFinancials.classification`) and from whether it is a bank/financial company.
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from stock_agents.fundamentals import CompanyFinancials


@dataclass(frozen=True)
class PillarSpec:
    key: str
    label: str
    judge: str  # what the model should weigh, injected into the prompt


@dataclass(frozen=True)
class PillarProfile:
    name: str
    pillars: tuple[PillarSpec, ...]

    @property
    def keys(self) -> list[str]:
        return [p.key for p in self.pillars]

    def label(self, key: str) -> str:
        return next(p.label for p in self.pillars if p.key == key)


_VALUATION = PillarSpec("valuation", "Valuation",
                        "P/E against the peer median and against the company's own growth and returns; ignore loss-making peers")
_OWNERSHIP = PillarSpec("ownership", "Ownership",
                        "direction of promoter, FII and DII holdings over the last 4 and 8 quarters; promoter pledging")
_GROWTH = PillarSpec("growth", "Growth",
                     "latest-quarter YoY sales and profit growth, multi-year CAGR, and whether growth is accelerating or slowing")

GENERAL = PillarProfile("general", (
    _GROWTH,
    PillarSpec("profitability", "Profitability",
               "operating and net margin level and trend; earnings quality (other income share of profit, one-offs)"),
    PillarSpec("balance_sheet", "Balance sheet", "debt to equity, interest coverage and ROCE trend"),
    PillarSpec("cash_flow", "Cash flow",
               "operating cash flow against net profit, free cash flow, and working capital absorbing cash"),
    _VALUATION,
    _OWNERSHIP,
))

FINANCIAL = PillarProfile("financial", (
    PillarSpec("growth", "Growth",
               "net interest income or financing revenue, net profit, and any deposits/advances growth shown"),
    PillarSpec("profitability", "Profitability", "return on assets / equity and margin trend; do not use EBITDA or operating margin"),
    PillarSpec("asset_quality", "Asset quality",
               "NPA / credit-cost rows if the data has them; if it has none, give no rating and say so in the evidence"),
    PillarSpec("capital", "Capital & net worth",
               "net worth growth, leverage and capital adequacy if shown; do not use debt to equity"),
    PillarSpec("valuation", "Valuation",
               "P/E and price to book against the peer median and against returns; ignore loss-making peers"),
    _OWNERSHIP,
))

CAPITAL_INTENSIVE = PillarProfile("capital_intensive", (
    _GROWTH,
    PillarSpec("profitability", "Profitability", "operating and net margin level and trend through the cycle"),
    PillarSpec("leverage", "Leverage & coverage",
               "borrowings against net worth, direction of debt, interest coverage; high debt is normal here, judge the trend and the cover"),
    PillarSpec("capex_funding", "Capex & funding",
               "operating cash flow against capex, how expansion is funded (cash flow, borrowings or equity), asset growth against sales growth"),
    _VALUATION,
    _OWNERSHIP,
))

PROFILES = {p.name: p for p in (GENERAL, FINANCIAL, CAPITAL_INTENSIVE)}

# Words in Screener's sector > industry chain that mark a capital-intensive business.
CAPITAL_INTENSIVE_WORDS = re.compile(
    r"\b(power|utilit\w*|realty|real estate|construction|infrastructure|shipping|ports?|roads?)\b")


def profile_for(fin: CompanyFinancials) -> PillarProfile:
    if fin.is_financial:
        return FINANCIAL
    if CAPITAL_INTENSIVE_WORDS.search(" ".join(fin.classification).lower()):
        return CAPITAL_INTENSIVE
    return GENERAL


def prompt_block(profile: PillarProfile) -> str:
    """The pillar list as it appears in the fundamentals prompt."""
    lines = [f'- "{p.key}" ({p.label}): {p.judge}' for p in profile.pillars]
    return (
        "Rate exactly these pillars, in this order, each from 1 (very weak) to 5 (very strong; for valuation, 5 means "
        "cheap or attractive), or null if the data cannot support a rating:\n" + "\n".join(lines)
    )
