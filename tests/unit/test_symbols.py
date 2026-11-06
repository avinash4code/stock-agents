import os

import httpx
import pytest
import respx

from stock_agents.fundamentals import FundamentalsError
from stock_agents.symbols import LISTINGS_URL, Listing, load_listings, normalize_name, parse_listings, resolve_symbol

CSV = (
    "SYMBOL,NAME OF COMPANY, SERIES, DATE OF LISTING, PAID UP VALUE, MARKET LOT, ISIN NUMBER, FACE VALUE\n"
    "INFY,Infosys Limited,EQ,08-FEB-1995,5,1,INE009A01021,5\n"
    "TCS,Tata Consultancy Services Limited,EQ,25-AUG-2004,1,1,INE467B01029,1\n"
    "TMCV,Tata Motors Limited,EQ,12-NOV-2025,2,1,INE1TAE01010,2\n"
    "TMPV,Tata Motors Passenger Vehicles Limited,EQ,22-JUL-1998,2,1,INE155A01022,2\n"
    "TATASTEEL,Tata Steel Limited,EQ,01-JAN-1990,1,1,INE081A01020,1\n"
    "HDFCBANK,HDFC Bank Limited,EQ,08-NOV-1995,1,1,INE040A01034,1\n"
    "RELIANCE,Reliance Industries Limited,EQ,29-NOV-1995,10,1,INE002A01018,10\n"
    "M&M,Mahindra & Mahindra Limited,EQ,01-JAN-1990,5,1,INE101A01026,5\n"
)
LISTINGS = parse_listings(CSV)


def test_parse_listings_handles_the_padded_nse_header():
    assert len(LISTINGS) == 8 and LISTINGS[0] == Listing("INFY", "Infosys Limited")


@pytest.mark.parametrize("name, expected", [
    ("Infosys Limited", "infosys"), ("Mahindra & Mahindra Ltd.", "mahindra and mahindra"), ("The Tata Power Company Limited", "tata power company"),
])
def test_normalize_name(name, expected):
    assert normalize_name(name) == expected


@pytest.mark.parametrize("query, symbol", [
    ("INFY", "INFY"), ("infy", "INFY"), ("tcs.ns", "TCS"), ("TCS.BO", "TCS"), ("  Reliance  ", "RELIANCE"), ("M&M", "M&M"),
    ("532540", "532540"),  # BSE scrip code goes straight through
    ("Infosys", "INFY"), ("infosys limited", "INFY"), ("HDFC Bank", "HDFCBANK"), ("Tata Motors", "TMCV"),  # exact name beats a longer one
    ("Tata Consultancy Services", "TCS"), ("Tata Consultancy", "TCS"), ("Mahindra and Mahindra", "M&M"), ("Reliance Industries", "RELIANCE"),
])
def test_resolves_symbols_and_names(query, symbol):
    assert resolve_symbol(query, LISTINGS) == symbol


def test_an_ambiguous_name_lists_the_candidates():
    with pytest.raises(FundamentalsError, match="matches several companies") as exc:
        resolve_symbol("Tata", LISTINGS)
    assert "Tata Steel Limited (TATASTEEL)" in str(exc.value) and "Use the exchange symbol" in str(exc.value)


def test_unknown_input_is_passed_on_only_if_it_looks_like_a_symbol():
    assert resolve_symbol("Newlisting", LISTINGS) == "NEWLISTING"  # list may be stale; Screener will say if it is wrong
    assert resolve_symbol("INFY", []) == "INFY"  # list unavailable
    with pytest.raises(FundamentalsError, match="could not find a listed company named 'Some Unknown Company'"):
        resolve_symbol("Some Unknown Company", LISTINGS)
    with pytest.raises(FundamentalsError, match="could not find"):
        resolve_symbol("Infosys Technologies", [])
    with pytest.raises(FundamentalsError, match="non-empty"):
        resolve_symbol("   ", LISTINGS)


@respx.mock
def test_listings_are_downloaded_once_and_cached(tmp_path):
    cache = tmp_path / "data" / "nse.csv"
    route = respx.get(LISTINGS_URL).mock(return_value=httpx.Response(200, text=CSV))
    assert len(load_listings(cache)) == 8 and cache.exists()
    assert len(load_listings(cache)) == 8
    assert route.call_count == 1  # the second call used the fresh cache


@respx.mock
def test_a_stale_cache_is_refreshed(tmp_path):
    cache = tmp_path / "nse.csv"
    cache.write_text("SYMBOL,NAME OF COMPANY\nOLD,Old Limited\n")
    os.utime(cache, (0, 0))  # modified long ago
    respx.get(LISTINGS_URL).mock(return_value=httpx.Response(200, text=CSV))
    assert len(load_listings(cache)) == 8 and "INFY" in cache.read_text()


@respx.mock
def test_download_failure_falls_back_to_the_stale_copy_or_nothing(tmp_path):
    respx.get(LISTINGS_URL).mock(return_value=httpx.Response(503))
    cache = tmp_path / "nse.csv"
    assert load_listings(cache) == []  # no copy at all: empty, never an exception
    cache.write_text(CSV)
    os.utime(cache, (0, 0))
    assert len(load_listings(cache)) == 8
    respx.get(LISTINGS_URL).mock(side_effect=httpx.ConnectError("offline"))
    assert len(load_listings(cache)) == 8


@respx.mock
def test_an_empty_download_does_not_overwrite_the_cache(tmp_path):
    cache = tmp_path / "nse.csv"
    cache.write_text(CSV)
    os.utime(cache, (0, 0))
    respx.get(LISTINGS_URL).mock(return_value=httpx.Response(200, text="SYMBOL,NAME OF COMPANY\n"))
    assert len(load_listings(cache)) == 8 and cache.read_text() == CSV
