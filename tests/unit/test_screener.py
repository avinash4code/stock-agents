from datetime import date

import httpx
import pytest
import respx
from lxml import html

from stock_agents import screener
from stock_agents.fundamentals import FundamentalsError
from stock_agents.screener import fetch_company, parse_company_page, parse_number, parse_peers
from tests.conftest import FETCHED, PAGES, fixture_html, sample_financials

def edited(page, change):
    """The page after `change(root)` has edited its parsed tree."""
    root = html.fromstring(page)
    change(root)
    return html.tostring(root, encoding="unicode")


def _row(root, section, label):
    return next(tr for tr in root.xpath(f"//section[@id='{section}']//tbody/tr") if tr.xpath("string(./td[1])").split()[:len(label.split())] == label.split())


CONS = "https://www.screener.in/company/TCS/consolidated/"
BARE = "https://www.screener.in/company/TCS/"
PEERS = "https://www.screener.in/api/company/{}/peers/"
TCS_WAREHOUSE, INFY_STANDALONE_WAREHOUSE = "6599230", "1143"


def mock_peers(warehouse=TCS_WAREHOUSE, **response):
    response = response or {"status_code": 200, "text": fixture_html("screener_peers_infy.html")}
    return respx.get(PEERS.format(warehouse)).mock(return_value=httpx.Response(**response))


@pytest.mark.parametrize("text, expected", [
    ("9,03,816", 903816.0), ("73,188", 73188.0), ("+8%", 8.0), ("-96", -96.0), ("26%", 26.0),
    ("14.38%", 14.38), ("27,48,596", 2748596.0), ("₹ 4,79,676 Cr.", 479676.0), ("", None), ("  ", None), ("n/a", None),
])
def test_parse_number(text, expected):
    assert parse_number(text) == expected


def test_parses_a_consolidated_general_company():
    fin = sample_financials()
    assert (fin.symbol, fin.name, fin.basis, fin.kind) == ("TCS", "Tata Consultancy Services Ltd", "consolidated", "general")
    assert fin.fetched == FETCHED and fin.url == PAGES["screener_tcs.html"] and not fin.is_financial
    q = fin.quarterly
    assert len(q.periods) == 13 and (q.periods[0], q.periods[-1]) == ("Sep 2023", "Sep 2026")
    assert q.values("Sales")[-1] == 73188.0 and q.value_at("Sep 2026", "Net Profit") == 13934.0
    assert {"OPM %", "Tax %", "YoY Sales growth"} <= q.percent and "Sales" not in q.percent
    assert "Raw PDF" not in q.rows  # a row of links, not numbers
    assert fin.profit_loss.periods[-1] == "TTM" and fin.profit_loss.value_at("TTM", "Tax %") is None


def test_parses_every_section():
    fin = sample_financials()
    assert fin.balance_sheet.find("Borrowings") and fin.balance_sheet.periods[-1] == "Sep 2026"  # a half-year column
    assert fin.cash_flow.find("Cash from Operating") and fin.cash_flow.find("CFO/EBITDA")  # tooltip text not in the label
    assert fin.ratios.find("ROCE") and fin.ratios.find("Debtor Days")
    sh = fin.shareholding
    assert sh.periods[-1] == "Sep 2026" and sh.value_at("Sep 2026", "Promoters") == 71.77
    assert sh.value_at("Sep 2026", "No. of Shareholders") == 2579099.0 and sh.decimals == 2
    assert fin.key_ratios["Stock P/E"] == "16.5" and fin.key_ratios["Market Cap"].startswith("₹ 9,03,816")
    assert fin.growth["Compounded Sales Growth"]["5 Years"] == "10%"
    assert fin.pros and fin.cons and all(isinstance(p, str) for p in fin.pros + fin.cons)


def test_the_standalone_view_is_recognised():
    fin = sample_financials("screener_infy_standalone.html")
    assert (fin.symbol, fin.basis) == ("INFY", "standalone")
    assert fin.quarterly.value_at("Sep 2026", "Sales") == 40719.0


def test_banks_have_their_own_rows_and_are_flagged_financial():
    fin = sample_financials("screener_hdfcbank.html")
    assert fin.kind == "financial" and fin.is_financial
    assert fin.quarterly.find("Sales", "Revenue") == "Revenue" and fin.quarterly.find("Financing Profit")
    assert fin.balance_sheet.find("Deposits") and fin.balance_sheet.find("Borrowing")
    assert all(v is None for v in fin.quarterly.values("Gross NPA"))  # blank on the page, kept as None


def test_loss_making_companies_keep_negative_numbers():
    fin = sample_financials("screener_idea.html")
    assert fin.quarterly.values("Net Profit")[-4:] == [-5524.0, -5286.0, 51970.0, -3754.0]
    assert fin.balance_sheet.values("Reserves")[-1] < 0


def test_missing_sections_are_a_layout_error_not_partial_data():
    page = fixture_html("screener_tcs.html")
    with pytest.raises(FundamentalsError, match="no 'quarters' table"):
        parse_company_page(page.replace('id="quarters"', 'id="renamed"'))
    with pytest.raises(FundamentalsError, match="no 'profit-loss' table"):
        parse_company_page(page.replace('id="profit-loss"', 'id="renamed"'))
    with pytest.raises(FundamentalsError, match="the quarterly table has no sales/net profit rows"):
        parse_company_page(edited(page, lambda root: _row(root, "quarters", "Net Profit").getparent().remove(_row(root, "quarters", "Net Profit"))))
    with pytest.raises(FundamentalsError, match="consolidated or standalone"):
        parse_company_page(page.replace("Consolidated Figures", "Figures").replace("Standalone Figures", "Figures"))


def test_a_row_with_the_wrong_number_of_cells_is_a_layout_error():
    def drop_last_cell(root):
        row = _row(root, "quarters", "Sales")
        row.remove(row.xpath("./td")[-1])

    with pytest.raises(FundamentalsError, match="layout changed: row 'Sales' has 12 values for 13 periods"):
        parse_company_page(edited(fixture_html("screener_tcs.html"), drop_last_cell))


def test_optional_sections_may_be_absent():
    page = fixture_html("screener_tcs.html").replace('id="shareholding"', 'id="renamed"').replace('id="ratios"', 'id="renamed2"')
    fin = parse_company_page(page, CONS, today=date(2026, 1, 1))
    assert not fin.shareholding and not fin.ratios and fin.quarterly


# -- fetching -------------------------------------------------------------------------------------

@pytest.fixture
def slept():
    calls = []
    return calls


@respx.mock
def test_fetch_prefers_the_consolidated_page_and_makes_one_request(slept):
    route = respx.get(CONS).mock(return_value=httpx.Response(200, text=fixture_html("screener_tcs.html")))
    bare = respx.get(BARE).mock(return_value=httpx.Response(200, text=""))
    peers = mock_peers()
    fin = fetch_company("TCS", sleep=slept.append, today=FETCHED)
    assert route.call_count == 1 and bare.call_count == 0 and peers.call_count == 1
    assert slept == [screener.REQUEST_GAP]  # one pause, before the peer request
    assert (fin.symbol, fin.basis) == ("TCS", "consolidated") and fin.peers and len(fin.peers.peers) == 7
    for call in (route.calls[0], peers.calls[0]):
        headers = call.request.headers
        assert "stock-agents" in headers["user-agent"] and "x-requested-with" not in headers and "referer" not in headers


@respx.mock
def test_fetch_falls_back_to_the_standalone_page_after_a_pause(slept):
    respx.get(CONS).mock(return_value=httpx.Response(404, text="not found"))
    respx.get("https://www.screener.in/company/INFY/consolidated/").mock(return_value=httpx.Response(404))
    bare = respx.get("https://www.screener.in/company/INFY/").mock(
        return_value=httpx.Response(200, text=fixture_html("screener_infy_standalone.html")))
    mock_peers(INFY_STANDALONE_WAREHOUSE)
    fin = fetch_company("INFY", sleep=slept.append)
    assert bare.call_count == 1 and slept == [screener.REQUEST_GAP, screener.REQUEST_GAP]  # before the fallback, before the peers
    assert fin.basis == "standalone" and fin.peers


@respx.mock
def test_unknown_company_is_a_clear_error(slept):
    respx.get("https://www.screener.in/company/NOPE/consolidated/").mock(return_value=httpx.Response(404))
    respx.get("https://www.screener.in/company/NOPE/").mock(return_value=httpx.Response(404))
    with pytest.raises(FundamentalsError, match="no company page for 'NOPE'"):
        fetch_company("NOPE", sleep=slept.append)


@respx.mock
@pytest.mark.parametrize("status", [401, 403])
def test_being_blocked_is_reported_not_retried(status, slept):
    route = respx.get(CONS).mock(return_value=httpx.Response(status))
    with pytest.raises(FundamentalsError, match=f"refused the request \\(HTTP {status}\\)"):
        fetch_company("TCS", sleep=slept.append)
    assert route.call_count == 1


@respx.mock
def test_throttling_is_retried_once_after_retry_after(slept):
    route = respx.get(CONS).mock(side_effect=[
        httpx.Response(429, headers={"Retry-After": "7"}),
        httpx.Response(200, text=fixture_html("screener_tcs.html")),
    ])
    mock_peers()
    assert fetch_company("TCS", sleep=slept.append).symbol == "TCS"
    assert route.call_count == 2 and slept == [7.0, screener.REQUEST_GAP]


@respx.mock
def test_retry_wait_is_capped_and_a_second_throttle_is_an_error(slept):
    respx.get(CONS).mock(return_value=httpx.Response(429, headers={"Retry-After": "9999"}))
    with pytest.raises(FundamentalsError, match="refused the request \\(HTTP 429\\)"):
        fetch_company("TCS", sleep=slept.append)
    assert slept == [screener.MAX_RETRY_WAIT]


@respx.mock
def test_server_errors_and_network_failures_are_wrapped(slept):
    respx.get(CONS).mock(return_value=httpx.Response(500))
    with pytest.raises(FundamentalsError, match="HTTP 500"):
        fetch_company("TCS", sleep=slept.append)
    respx.get(CONS).mock(side_effect=httpx.ConnectError("offline"))
    with pytest.raises(FundamentalsError, match="could not reach Screener \\(ConnectError: offline\\)"):
        fetch_company("TCS", sleep=slept.append)


@respx.mock
def test_symbols_with_special_characters_are_url_safe(slept):
    route = respx.get("https://www.screener.in/company/M&M/consolidated/").mock(
        return_value=httpx.Response(200, text=fixture_html("screener_tcs.html")))
    mock_peers()
    fetch_company("M&M", sleep=slept.append)
    assert route.call_count == 1


# -- peers ----------------------------------------------------------------------------------------

def test_page_identifiers_and_classification_are_parsed():
    fin = sample_financials("screener_tcs.html", peers=False)
    assert (fin.company_id, fin.warehouse_id) == ("3365", TCS_WAREHOUSE) and fin.peers is None
    assert fin.classification == ["Information Technology", "IT - Software", "Computers - Software & Consulting"]  # repeats dropped


def test_parse_peers():
    pc = parse_peers(fixture_html("screener_peers_infy.html"))
    assert pc.columns[:3] == ["CMP Rs.", "P/E", "Mar Cap Rs.Cr."] and pc.columns[-1] == "ROCE %"
    assert [p.symbol for p in pc.peers][:3] == ["TCS", "INFY", "HCLTECH"] and len(pc.peers) == 7
    tcs = pc.peers[0]
    assert (tcs.name, tcs.company_id) == ("TCS", "3365")
    assert tcs.values[pc.column("P/E")] == 16.56 and tcs.values[pc.column("ROCE")] == 63.03
    assert pc.median_label == "Median: 73 Co." and pc.median[pc.column("P/E")] == 18.56 and len(pc.median) == len(pc.columns)
    assert pc.column("Qtr Sales Var") is not None and pc.column("Nonexistent") is None


@pytest.mark.parametrize("fragment, message", [
    ("<div>nothing here</div>", "no peer table"),
    ("<table><tr><th>A</th><th>B</th><th>C</th></tr><tr><td>1</td><td>x</td><td>2</td></tr></table>", "unexpected peer table columns"),
    ("<table><tr><th>S.No.</th><th>Company</th><th>P/E</th></tr><tr><td>1.</td><td>TCS</td></tr></table>", "peer row has 2 cells for 3 columns"),
    ("<table><tr><th>S.No.</th><th>Company</th><th>P/E</th></tr><tr><td></td><td>Median: 3 Co.</td><td>9</td></tr></table>", "no companies"),
])
def test_unrecognised_peer_tables_are_layout_errors(fragment, message):
    with pytest.raises(FundamentalsError, match=message):
        parse_peers(fragment)


@respx.mock
@pytest.mark.parametrize("peer_response", [
    {"status_code": 403}, {"status_code": 429, "headers": {"Retry-After": "5"}}, {"status_code": 500}, {"status_code": 404},
    {"status_code": 200, "text": "<p>Loading</p>"},  # a layout we do not know
])
def test_a_failing_peer_request_leaves_the_rest_intact_and_is_never_retried(peer_response, slept, caplog):
    respx.get(CONS).mock(return_value=httpx.Response(200, text=fixture_html("screener_tcs.html")))
    peers = mock_peers(**peer_response)
    with caplog.at_level("WARNING"):
        fin = fetch_company("TCS", sleep=slept.append)
    assert fin.peers is None and fin.quarterly and fin.symbol == "TCS"
    assert peers.call_count == 1 and slept == [screener.REQUEST_GAP]  # no retry, no extra pause
    assert "Peer comparison unavailable for TCS" in caplog.text


@respx.mock
def test_a_network_error_on_the_peer_request_is_not_fatal(slept):
    respx.get(CONS).mock(return_value=httpx.Response(200, text=fixture_html("screener_tcs.html")))
    respx.get(PEERS.format(TCS_WAREHOUSE)).mock(side_effect=httpx.ConnectError("offline"))
    assert fetch_company("TCS", sleep=slept.append).peers is None


@respx.mock
def test_no_peer_request_without_a_warehouse_id(slept):
    page = fixture_html("screener_tcs.html").replace(f'data-warehouse-id="{TCS_WAREHOUSE}"', "")
    respx.get(CONS).mock(return_value=httpx.Response(200, text=page))  # no peers route: any peer request would fail the test
    fin = fetch_company("TCS", sleep=slept.append)
    assert fin.peers is None and slept == []


@pytest.mark.live
def test_live_screener_page_parses_and_has_recent_quarters():
    fin = fetch_company("TCS")
    assert fin.basis == "consolidated" and len(fin.quarterly.periods) >= 8
    assert fin.quarterly.values("Sales")[-1] and fin.shareholding.values("Promoters")
    assert fin.peers and any(p.company_id == fin.company_id for p in fin.peers.peers)
