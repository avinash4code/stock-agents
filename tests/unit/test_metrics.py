from dataclasses import replace

import pytest

from stock_agents.fundamentals import EMPTY, CompanyFinancials, Table
from stock_agents.metrics import (
    annual_derived, balance_derived, cash_flow_derived, fmt_value, pct_change, quarter_before, quarterly_derived,
    ratio, render_data_section, render_table, shareholding_changes, ttm_summary, year_before,
)
from tests.conftest import FETCHED, sample_financials

QUARTERS = ["Sep 2025", "Dec 2025", "Mar 2026", "Jun 2026", "Sep 2026"]


def make(quarterly_rows, periods=QUARTERS, kind="general", **tables) -> CompanyFinancials:
    quarterly = Table(periods=periods, rows=quarterly_rows)
    return CompanyFinancials(
        symbol="X", name="X Ltd", url="u", basis="consolidated", kind=kind, fetched=FETCHED,
        quarterly=quarterly, profit_loss=tables.pop("profit_loss", quarterly), **tables)


def row(fin_table: Table, label: str):
    return dict(zip(fin_table.periods, fin_table.rows[label]))


def test_pct_change_and_ratio():
    assert pct_change(110, 100) == pytest.approx(10) and pct_change(90, 100) == pytest.approx(-10)
    assert pct_change(5, None) is None and pct_change(None, 5) is None
    assert pct_change(5, 0) is None and pct_change(5, -10) is None  # no meaningful base
    assert ratio(50, 200, 100) == 25
    assert ratio(None, 2) is None and ratio(2, None) is None and ratio(2, 0) is None


def test_period_arithmetic():
    assert year_before("Sep 2026") == "Sep 2025" and year_before("TTM") is None
    assert quarter_before("Sep 2026") == "Jun 2026" and quarter_before("Mar 2026") == "Dec 2025"
    assert quarter_before("Jan 2026") == "Oct 2025" and quarter_before("TTM") is None and quarter_before("junk") is None


def test_growth_margins_and_other_income_for_a_known_series():
    fin = make({
        "Sales": [100.0, 110.0, 120.0, 130.0, 150.0], "Net Profit": [10.0, 11.0, 12.0, 13.0, 30.0],
        "EPS in Rs": [1.0, 1.1, 1.2, 1.3, 3.0], "Profit before tax": [14.0, 15.0, 16.0, 17.0, 40.0],
        "Other Income": [2.0, 3.0, 4.0, 5.0, 20.0], "Operating Profit": [20.0, 22.0, 24.0, 26.0, 30.0],
        "Interest": [2.0, 2.0, 2.0, 2.0, 0.0],
    })
    d = quarterly_derived(fin)
    assert row(d, "Sales YoY %")["Sep 2026"] == pytest.approx(50) and row(d, "Sales YoY %")["Jun 2026"] is None
    assert row(d, "Sales QoQ %")["Sep 2026"] == pytest.approx(150 / 130 * 100 - 100)
    assert row(d, "Sales QoQ %")["Sep 2025"] is None  # nothing before the first quarter
    assert row(d, "Net profit YoY %")["Sep 2026"] == pytest.approx(200) and row(d, "EPS YoY %")["Sep 2026"] == pytest.approx(200)
    assert row(d, "Net margin %")["Sep 2026"] == pytest.approx(20)
    assert row(d, "Other income / PBT %")["Sep 2026"] == pytest.approx(50)  # earnings-quality flag
    assert row(d, "Operating profit YoY %")["Sep 2026"] == pytest.approx(50)
    assert row(d, "Interest cover (x)")["Sep 2025"] == pytest.approx(10) and row(d, "Interest cover (x)")["Sep 2026"] is None  # no interest


def test_a_missing_quarter_breaks_qoq_and_yoy_not_the_whole_row():
    fin = make({"Sales": [100.0, 120.0, 130.0], "Net Profit": [1.0, 2.0, 3.0]}, periods=["Sep 2025", "Mar 2026", "Sep 2026"])  # no Dec/Jun
    d = quarterly_derived(fin)
    assert "Sales QoQ %" not in d.rows  # no adjacent quarters at all, so the row is dropped rather than shown as all n/a
    assert row(d, "Sales YoY %")["Sep 2026"] == pytest.approx(30)


def test_losses_have_no_growth_off_a_negative_base():
    fin = make({"Sales": [100.0] * 5, "Net Profit": [-20.0, -10.0, 5.0, 8.0, 10.0]})
    d = quarterly_derived(fin)
    assert "Net profit YoY %" not in d.rows  # every year-ago base is a loss or missing: no growth rate, no row
    assert row(d, "Net profit QoQ %")["Jun 2026"] == pytest.approx(60)
    assert row(d, "Net margin %")["Sep 2025"] == pytest.approx(-20)


def test_banks_get_no_operating_profit_or_interest_cover_rows():
    fin = make({"Revenue": [100.0] * 5, "Financing Profit": [-5.0] * 5, "Net Profit": [10.0] * 5, "Interest": [50.0] * 5}, kind="financial")
    d = quarterly_derived(fin)
    assert "Sales YoY %" in d.rows and "Operating profit YoY %" not in d.rows and "Interest cover (x)" not in d.rows
    assert "Debt / net worth (x)" not in balance_derived(replace(fin, balance_sheet=Table(
        periods=["Mar 2026"], rows={"Equity Capital": [1.0], "Reserves": [9.0], "Borrowing": [50.0]}))).rows


def test_ttm_summary_sums_four_quarters_against_the_previous_four():
    fin = make({"Sales": [10.0] * 4 + [20.0] * 4, "Net Profit": [1.0, 1.0, 1.0, 1.0, 3.0, 3.0, 3.0, 3.0], "EPS in Rs": [0.5] * 8},
               periods=[f"{m} {y}" for y, m in [(2024, "Sep"), (2024, "Dec"), (2025, "Mar"), (2025, "Jun"), (2025, "Sep"), (2025, "Dec"), (2026, "Mar"), (2026, "Jun")]])
    t = ttm_summary(fin)
    assert (t["sales"], t["sales_growth"]) == (80.0, pytest.approx(100)) and t["net_profit_growth"] == pytest.approx(200)
    short = ttm_summary(make({"Sales": [1.0, 2.0, 3.0, 4.0], "Net Profit": [1.0] * 4, "EPS in Rs": [1.0] * 4}, periods=QUARTERS[:4]))
    assert short["sales"] == 10.0 and short["sales_growth"] is None  # not enough history for growth
    assert ttm_summary(make({"Sales": [1.0, None, 3.0, 4.0], "Net Profit": [1.0] * 4, "EPS in Rs": [1.0] * 4}, periods=QUARTERS[:4]))["sales"] is None


def test_annual_derived_skips_the_ttm_column_for_growth():
    pl = Table(periods=["Mar 2024", "Mar 2025", "TTM"], rows={
        "Sales": [100.0, 120.0, 130.0], "Net Profit": [10.0, 15.0, 18.0], "EPS in Rs": [1.0, 1.5, 1.8],
        "Profit before tax": [14.0, 20.0, 24.0], "Other Income": [1.0, 2.0, 6.0]})
    d = annual_derived(make({"Sales": [1.0], "Net Profit": [1.0]}, periods=["Sep 2026"], profit_loss=pl))
    assert row(d, "Net profit growth %") == {"Mar 2024": None, "Mar 2025": pytest.approx(50), "TTM": None}
    assert row(d, "Net margin %")["TTM"] == pytest.approx(18 / 130 * 100)
    assert row(d, "Other income / PBT %")["TTM"] == pytest.approx(25)


def test_balance_sheet_and_cash_flow_ratios():
    fin = make({"Sales": [1.0], "Net Profit": [1.0]}, periods=["Sep 2026"],
               balance_sheet=Table(periods=["Mar 2025", "Mar 2026"], rows={
                   "Equity Capital": [100.0, 100.0], "Reserves": [900.0, -200.0], "Borrowings": [500.0, 300.0]}),
               cash_flow=Table(periods=["Mar 2025", "Mar 2026"], rows={"Cash from Operating Activity": [150.0, 90.0]}),
               profit_loss=Table(periods=["Mar 2025", "Mar 2026", "TTM"], rows={"Sales": [1.0] * 3, "Net Profit": [100.0, -30.0, 0.0]}))
    b = balance_derived(fin)
    assert row(b, "Net worth (equity + reserves)") == {"Mar 2025": 1000.0, "Mar 2026": -100.0}
    assert row(b, "Debt / net worth (x)") == {"Mar 2025": 0.5, "Mar 2026": None}  # negative net worth: no ratio
    cf = cash_flow_derived(fin)
    assert row(cf, "Operating cash flow / net profit (x)") == {"Mar 2025": 1.5, "Mar 2026": None}  # loss year: none
    assert balance_derived(replace(fin, balance_sheet=EMPTY)).rows == {} and not balance_derived(replace(fin, balance_sheet=EMPTY))


def test_shareholding_changes_in_percentage_points():
    periods = [f"Q{i}" for i in range(9)]
    fin = make({"Sales": [1.0], "Net Profit": [1.0]}, periods=["Sep 2026"], shareholding=Table(periods=periods, rows={
        "Promoters": [60.0, 60.0, 60.0, 60.0, 59.0, 58.0, 57.0, 56.0, 55.0],
        "FIIs": [10.0, 10.0, 10.0, 10.0, 11.0, 12.0, 13.0, 14.0, 15.0],
        "No. of Shareholders": [100.0, 110.0, 120.0, 130.0, 140.0, 150.0, 160.0, 170.0, 200.0]}))
    t = shareholding_changes(fin)
    assert t.periods == ["Δ 1Q", "Δ 4Q", "Δ 8Q"] and t.signed
    assert t.rows["Promoters"] == [-1.0, -4.0, -5.0] and t.rows["FIIs"] == [1.0, 4.0, 5.0]
    assert t.rows["No. of shareholders (% change)"][2] == pytest.approx(100.0)
    short = shareholding_changes(replace(fin, shareholding=Table(periods=["a", "b"], rows={"Promoters": [5.0, 6.0]})))
    assert short.rows["Promoters"] == [1.0, None, None]
    assert not shareholding_changes(replace(fin, shareholding=EMPTY))


@pytest.mark.parametrize("label, value, percent, decimals, signed, expected", [
    ("Sales", 73188.0, False, 0, False, "73,188"), ("Sales", None, False, 0, False, "n/a"),
    ("EPS in Rs", 38.37, False, 0, False, "38.37"), ("EPS YoY %", 15.04, True, 1, False, "+15.0%"),
    ("OPM %", 26.0, True, 0, False, "26%"), ("Sales YoY %", -1.66, True, 1, False, "-1.7%"),
    ("Interest cover (x)", 74.66, False, 1, False, "74.7x"), ("No. of Shareholders", 2579099.0, False, 2, False, "2,579,099"),
    ("Promoters", 71.77, True, 2, False, "71.77%"), ("FIIs", -1.32, False, 2, True, "-1.32"),
])
def test_value_formatting(label, value, percent, decimals, signed, expected):
    assert fmt_value(label, value, percent, decimals, signed) == expected


def test_render_table_skips_empty_rows_and_empty_tables():
    t = Table(periods=["A", "B"], rows={"X": [1.0, 2.0], "Gross NPA %": [None, None]}, percent=frozenset({"Gross NPA %"}))
    assert render_table("T", t) == "| T | A | B |\n|---|---|---|\n| X | 1 | 2 |"
    assert render_table("T", EMPTY) == ""


def test_the_derived_numbers_for_real_tcs_data():
    fin = sample_financials()
    d = quarterly_derived(fin)
    assert row(d, "Sales YoY %")["Sep 2026"] == pytest.approx(73188 / 65799 * 100 - 100)
    assert row(d, "Sales YoY %")["Sep 2023"] is None
    t = ttm_summary(fin)
    assert t["sales"] == pytest.approx(67087 + 70698 + 72275 + 73188) and t["net_profit_growth"] == pytest.approx(4.4, abs=0.1)
    assert row(shareholding_changes(fin), "FIIs")["Δ 4Q"] == pytest.approx(9.01 - 10.33)


def test_the_data_section_for_a_company():
    text = render_data_section(sample_financials())
    assert text.startswith("## Financial data: Tata Consultancy Services Ltd")
    assert "**consolidated** figures; non-financial company; amounts in INR crore; fetched 06 Nov 2026" in text
    for heading in ["### Quarterly results", "### Annual profit and loss", "### Balance sheet (annual)", "### Cash flow (annual)",
                    "### Efficiency ratios (annual)", "### Shareholding (% held, quarterly)", "### Long-run growth",
                    "### Screener's own remarks"]:
        assert heading in text, heading
    assert "| Sales | 62,613 |" in text and text.count("| Quarter |") == 2  # 10 quarters shown, not all 13; shareholding too
    assert "Sep 2023" not in text.split("### Annual")[0]  # older quarters are cut
    assert "| TTM |" in text and "Last four quarters (INR crore): sales 283,248 (+9.9%" in text
    assert "Market Cap: ₹ 9,03,816 Cr." in text and "| Sales YoY % |" in text and "+11.2%" in text
    assert "Pros:" in text and "Cons:" in text


def test_the_data_section_flags_standalone_and_financial_companies():
    standalone = render_data_section(sample_financials("screener_infy_standalone.html"))
    assert "**standalone** figures" in standalone
    bank = render_data_section(sample_financials("screener_hdfcbank.html"))
    assert "bank / financial company" in bank and "| Revenue |" in bank
    assert "Interest cover" not in bank and "Debt / net worth" not in bank and "Gross NPA" not in bank  # blank rows are hidden


def test_sparse_companies_render_without_errors():
    text = render_data_section(make({"Sales": [1.0, 2.0], "Net Profit": [0.5, 1.0]}, periods=["Jun 2026", "Sep 2026"]))
    assert "### Quarterly results" in text and "### Balance sheet" not in text and "### Shareholding" not in text
