from dataclasses import replace

from stock_agents.fundamentals import Quote
from stock_agents.snapshot import build_snapshot
from tests.conftest import sample_financials


def test_snapshot_has_the_header_figures_from_the_page():
    fin = replace(sample_financials(), quote=Quote(2408.0, -1.43, "13 Nov - close price"))
    snap = build_snapshot(fin)
    assert (snap["price"], snap["change_pct"], snap["price_as_of"]) == (2408.0, -1.43, "13 Nov - close price")
    assert (snap["market_cap_cr"], snap["pe"], snap["low_52w"], snap["high_52w"]) == (903816.0, 16.5, 1976.0, 3350.0)
    assert snap["peer_median_pe"] == 18.56 and snap["peer_median_label"] == "Median: 73 Co."
    assert snap["sector"][0] == "Information Technology" and snap["basis"] == "consolidated"


def test_older_pages_without_a_price_strip_fall_back_to_the_key_ratios():
    snap = build_snapshot(sample_financials())  # the saved fixture has no day change or close date
    assert snap["price"] == 2498.0 and snap["change_pct"] is None and snap["price_as_of"] == ""


def test_missing_peers_and_ratios_leave_none():
    fin = replace(sample_financials(peers=False), key_ratios={})
    snap = build_snapshot(fin)
    assert snap["peer_median_pe"] is None and snap["pe"] is None and snap["high_52w"] is None and snap["price"] is None
