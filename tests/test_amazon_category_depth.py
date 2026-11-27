from decimal import Decimal
from uuid import uuid4

import pytest
import respx
from bs4 import BeautifulSoup

from mediaforge.orchestrator import amazon_category
from mediaforge.rag.amazon_category_collection import _discover
from mediaforge.rag.amazon_research import AMAZON, SUGGESTIONS
from tests import test_amazon_store_depth as store_tests
from tests.test_amazon_store_depth import API, _run_submitted

store_api = store_tests.api

MAIN = f"{AMAZON}/gp/bestsellers/fashion"
MANUAL = f"{AMAZON}/gp/bestsellers/fashion/123456"
CHILD = f"{AMAZON}/gp/bestsellers/fashion/987654"
OTHER = f"{AMAZON}/gp/bestsellers/home-garden"


def cards(start, end):
    return "".join(
        f'<div class="zg-grid-general-faceout"><span class="zg-bdg-text">#{i}</span>'
        f'<a href="/dp/B{i:09d}"><img src="https://m.media-amazon.com/images/I/{i}.jpg"></a>'
        '<div class="p13n-sc-truncate">Cotton summer shirts</div><span data-brand>Fixture</span>'
        '<span class="p13n-sc-price">$24.00</span>'
        '<span class="a-icon-alt">4.4 out of 5 stars</span>'
        '<a><span class="a-size-small">250</span></a></div>'
        for i in range(start, end)
    )


@respx.mock
async def test_multiboard_limit_evidence_partial_retry_and_reports(store_api, monkeypatch):
    client, _, _, queue, _ = store_api

    async def translations(items, _variants):
        for item in items:
            item.title_zh = "测试译名"
        return []

    monkeypatch.setattr(amazon_category, "localize_research_fields", translations)
    main = respx.get(MAIN).respond(
        200,
        text=(
            f'<div id="zg_browseRoot"><ul><li><span class="zg_selected">Fashion</span><ul>'
            f'<li><a href="{CHILD}">Summer</a></li></ul></li>'
            f'<li><a href="{OTHER}">Other</a></li></ul></div>' + cards(1, 32)
        ),
    )
    manual = respx.get(MANUAL).respond(200, text=cards(1, 23))
    child = respx.get(CHILD).respond(503)
    respx.get(SUGGESTIONS).respond(200, json={"suggestions": [{"value": "cotton summer shirts"}]})
    costs = {
        "purchase_usd": "4",
        "inbound_usd": "2",
        "fulfillment_usd": "3",
        "storage_usd": "1",
        "referral_rate": "0.15",
        "ads_rate": "0.10",
        "returns_rate": "0.05",
        "fixed_monthly_usd": "100",
        "launch_fixed_usd": "50",
        "lean_units": 50,
        "standard_units": 100,
        "monthly_units": 70,
        "target_asin": "B000000001",
        "source_note": "固定样本",
        "version": "fixture",
        "effective_on": "2026-11-28",
    }
    body = {
        "mode": "bestsellers",
        "source": MAIN + "?pg=2",
        "keyword": "summer shirts",
        "category": {"subcategories": [MANUAL], "costs": costs},
        "submission_id": str(uuid4()),
    }
    response = await client.post(API, json=body)
    assert response.status_code == 202, response.text
    rid = response.json()["id"]
    assert (await client.post(API, json={**body, "submission_id": str(uuid4())})).json()[
        "id"
    ] == rid
    assert len(queue) == 1
    first = await _run_submitted(client, rid)
    assert first["status"] == "partial_fail", first
    snapshot = first["snapshot"]
    boards = [item for item in snapshot["evidence"] if item["kind"] == "board"]
    assert [item["url"] for item in boards] == [MAIN, MANUAL, CHILD]
    assert [len(item["products"]) for item in boards] == [30, 20, 0]
    assert boards[0]["products"][0]["rank"] == "#1"
    assert (
        boards[0]["products"][0]["image_url"]
        and boards[0]["products"][0]["price_currency"] == "USD"
    )
    assert len(snapshot["asin_sources"]["B000000001"]) == 2
    assert len(first["report"]["listings"]) == 30
    sections = {item["key"]: item for item in snapshot["category_analysis"]["sections"]}
    assert set(sections) == {"opportunities", "competition", "keywords", "profit", "summary"}
    assert len(sections["profit"]["tables"][-1]["rows"]) == 3
    assert Decimal(sections["profit"]["tables"][0]["rows"][0]["cells"][3]) == Decimal("6.8")
    child.respond(200, text=cards(1, 3))
    response = await client.post(f"{API}/{rid}/retry?target=failed&item_key={boards[-1]['key']}")
    assert response.status_code == 202, response.text
    final = await _run_submitted(client, rid, 2)
    assert len(final["snapshot"]["asin_sources"]["B000000001"]) == 3
    assert main.call_count == manual.call_count == 1 and child.call_count == 2
    assert (await client.get(API)).json()[0]["mode"] == "bestsellers"


@pytest.mark.parametrize(
    "source", ["https://example.com/gp/bestsellers/fashion", "https://amazon.com/dp/B123456789"]
)
async def test_invalid_child_is_rejected_before_queue(store_api, source):
    client, _, _, queue, _ = store_api
    response = await client.post(
        API, json={"mode": "bestsellers", "source": MAIN, "category": {"subcategories": [source]}}
    )
    assert response.status_code == 422 and not queue


def test_real_navigation_markup_and_tracking_path():
    soup = BeautifulSoup(
        '<ul class="_p13n_zg-browse-root__hash"><li>'
        f'<a href="{OTHER}">父级</a><ul><li><span>Fashion</span><span>(Current)</span></li>'
        '<li><span><ul class="_p13n_zg-browse-group__hash">'
        '<li><a href="/Best-Sellers-Baby/zgbs/fashion/7147444011/'
        'ref=zg_bs_nav_fashion_1">Baby</a></li>'
        "</ul></span></li></ul></li></ul>",
        "html.parser",
    )
    assert _discover(soup, MAIN) == [
        ("Baby", f"{AMAZON}/Best-Sellers-Baby/zgbs/fashion/7147444011")
    ]
