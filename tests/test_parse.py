"""Parser tests against a real eBay capture (2026-11-06, New + Buy It Now)."""
from __future__ import annotations

import gzip
from pathlib import Path

from crawler.parse import is_blocked, parse_result_count, parse_search_html

FIXTURE = Path(__file__).parent / "fixtures/real/search_active_2026-11-06.html.gz"


def rows():
    return parse_search_html(gzip.decompress(FIXTURE.read_bytes()).decode("utf-8", "replace"))


def test_not_blocked():
    html = gzip.decompress(FIXTURE.read_bytes()).decode("utf-8", "replace")
    assert is_blocked(html, 200) is None


def test_detects_interstitial():
    assert is_blocked("<h1>Pardon Our Interruption</h1>", 200) == "interstitial"
    assert is_blocked("", 403) == "http_403"


def test_result_count_survives_span_split():
    html = gzip.decompress(FIXTURE.read_bytes()).decode("utf-8", "replace")
    assert parse_result_count(html) == "1,200,000"


def test_card_count_and_required_fields():
    r = rows()
    assert 55 <= len(r) <= 62, len(r)
    assert all(x["eb_id"].isdigit() for x in r)
    assert all(x["price_usd"] for x in r)
    assert all(x["title"] for x in r)
    assert len({x["eb_id"] for x in r}) == len(r)


def test_units_badge_is_a_floor_and_parsed():
    r = rows()
    badged = {x["units_badge"] for x in r if x["units_badge"]}
    assert badged == {26, 63, 301}, badged


def test_watchers_and_shipping():
    r = rows()
    assert [x["watchers"] for x in r if x["watchers"]] == [12]
    assert 30 <= sum(1 for x in r if x["free_shipping"]) <= 40


def test_price_range_collapsed_to_min_max():
    r = rows()
    ranged = [x for x in r if x["price_max_usd"] and x["price_max_usd"] > x["price_usd"]]
    assert ranged, "expected at least one variation-priced listing"
    x = ranged[0]
    assert x["price_usd"] <= x["price_max_usd"]


def test_ad_placeholder_dropped():
    r = rows()
    assert all("shop on ebay" not in x["title"].lower() for x in r)
    assert all(x["eb_id"] != "123456" for x in r)
