import httpx
import pytest
import respx

from stock_agents.fundamentals import EMPTY, FundamentalsError, Table, fetch_fundamentals
from stock_agents.symbols import Listing
from tests.conftest import fixture_html

T = Table(periods=["Mar 2025", "Mar 2026", "TTM"],
          rows={"Sales": [1.0, 2.0, 3.0], "Sales growth": [None, 100.0, 50.0], "OPM %": [10.0, 20.0, 30.0]})


def test_find_prefers_an_exact_label_then_a_prefix_and_ignores_case():
    assert T.find("sales") == "Sales" and T.find("Sales growth") == "Sales growth"
    assert T.find("OPM") == "OPM %"
    assert T.find("Revenue", "Sales") == "Sales"  # first of the alternatives that exists
    assert T.find("Profit") is None


def test_values_and_value_at():
    assert T.values("Sales") == [1.0, 2.0, 3.0] and T.values("Nope") is None
    assert T.value_at("Mar 2026", "Sales") == 2.0 and T.value_at("TTM", "Sales growth") == 50.0
    assert T.value_at("Mar 2020", "Sales") is None and T.value_at("TTM", "Nope") is None


def test_last_keeps_the_newest_periods_and_the_row_formats():
    t = Table(periods=T.periods, rows=T.rows, percent=frozenset({"OPM %"}), decimals=2).last(2)
    assert t.periods == ["Mar 2026", "TTM"] and t.rows["Sales"] == [2.0, 3.0]
    assert t.percent == {"OPM %"} and t.decimals == 2
    assert T.last(10) is T


def test_empty_tables_are_falsy():
    assert not EMPTY and not Table(periods=["a"]) and T


@respx.mock
def test_fetch_fundamentals_resolves_the_name_then_fetches_the_page():
    route = respx.get("https://www.screener.in/company/TCS/consolidated/").mock(
        return_value=httpx.Response(200, text=fixture_html("screener_tcs.html")))
    peers = respx.get("https://www.screener.in/api/company/6599230/peers/").mock(
        return_value=httpx.Response(200, text=fixture_html("screener_peers_infy.html")))
    with httpx.Client() as client:
        fin = fetch_fundamentals("Tata Consultancy Services", client=client, sleep=lambda seconds: None,
                                 listings=[Listing("TCS", "Tata Consultancy Services Limited")])
    assert route.call_count == 1 and peers.call_count == 1 and fin.symbol == "TCS" and fin.peers


def test_fetch_fundamentals_reports_unresolvable_names_without_a_request():
    with pytest.raises(FundamentalsError, match="could not find a listed company"):
        fetch_fundamentals("Some Unknown Company", listings=[Listing("TCS", "Tata Consultancy Services Limited")])
