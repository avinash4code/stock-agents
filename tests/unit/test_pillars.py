from dataclasses import replace

import pytest

from stock_agents.pillars import CAPITAL_INTENSIVE, FINANCIAL, GENERAL, profile_for, prompt_block
from tests.conftest import PAGES, sample_financials


def test_ordinary_companies_get_the_general_profile():
    fin = sample_financials("screener_tcs.html")
    assert profile_for(fin) is GENERAL


def test_banks_get_the_financial_profile():
    fin = sample_financials("screener_hdfcbank.html")
    assert fin.is_financial and profile_for(fin) is FINANCIAL


@pytest.mark.parametrize("chain, expected", [
    (["Utilities", "Power", "Power Generation"], CAPITAL_INTENSIVE),
    (["Real Estate", "Realty", "Residential"], CAPITAL_INTENSIVE),
    (["Industrials", "Construction", "Civil Construction"], CAPITAL_INTENSIVE),
    (["Consumer Discretionary", "Textiles", "Export Houses"], GENERAL),  # "port" inside "export" is not a port
    (["Healthcare", "Pharma", "Pharmaceuticals"], GENERAL),
    ([], GENERAL),
])
def test_capital_intensive_sectors_are_matched_on_whole_words(chain, expected):
    fin = replace(sample_financials("screener_tcs.html"), classification=chain)
    assert profile_for(fin) is expected


def test_every_fixture_gets_a_profile():
    for name in PAGES:
        assert profile_for(sample_financials(name)).pillars


def test_prompt_block_lists_the_pillars_in_order():
    block = prompt_block(FINANCIAL)
    assert block.index('"asset_quality"') < block.index('"valuation"')
    assert "null" in block
