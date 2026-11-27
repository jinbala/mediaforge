from decimal import Decimal

import httpx
import pytest
import respx

from mediaforge.gateway.main import app
from mediaforge.gateway.middleware.observability import _should_log_body
from mediaforge.gateway.middleware.rate_limit import limiter
from mediaforge.rag.amazon_research import AMAZON, SUGGESTIONS
from mediaforge.rag.amazon_script_samples import HTS_SEARCH

URL = "/api/v1/research/amazon/tools"
AUTH = {"X-Api-Key": "test-api-key-starter"}
PRODUCT = """
<span id="productTitle">Linen trousers</span><a id="bylineInfo">Brand: Sample</a>
<span id="acrCustomerReviewText">43 ratings</span><div id="priceblock_ourprice">$19.99</div>
<div id="wayfinding-breadcrumbs_feature_div">
  <ul><li><a>Clothing</a></li><li><a>Trousers</a></li></ul>
</div>
<div id="feature-bullets"><ul><li><span class="a-list-item">Wide leg</span></li></ul></div>
<div id="productDescription"><p>Visible product description.</p></div>
<div class="aplus-v2">A+ content</div>
<img id="landingImage" src="https://m.media-amazon.com/images/I/a.jpg">
<div id="altImages"><ul><li class="item"><img src="https://m.media-amazon.com/images/I/a._AC_.jpg"></li>
<li class="item"><img src="https://m.media-amazon.com/images/I/b.jpg"></li>
<li class="item videoThumbnail"><img src="https://m.media-amazon.com/images/I/video.jpg"></li>
</ul></div>
<table id="productDetails_detailBullets_sections1">
<tr><th>Date First Available</th><td>January 3, 2025</td></tr>
<tr><th>Best Sellers Rank</th><td>#10 in Trousers (See Top 100)</td></tr></table>
"""


@pytest.fixture
async def client():
    limiter.reset()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://test.local",
    ) as client:
        yield client


@pytest.mark.parametrize(
    "body",
    [
        {"tool": "listing", "source": "https://unrelated.example/dp/B012345678"},
        {"tool": "listing", "source": "B012345678", "site": "UK"},
        {"tool": "keywords", "keyword": "  "},
        {"tool": "keywords", "keyword": "pants", "queries": ["different"]},
        {"tool": "ppc", "sources": []},
        {"tool": "hs", "query": "123"},
    ],
)
async def test_validation(client, body):
    response = await client.post(URL, headers=AUTH, json=body)
    assert response.status_code == 422, response.text


async def test_auth_and_payload_logging(client):
    response = await client.post(URL, json={"tool": "hs", "query": "8518"})
    assert response.status_code == 401
    assert not _should_log_body(URL)


@respx.mock
async def test_req039_samples_all_expansions_and_retains_failures(client):
    queries = set()

    def upstream(request):
        assert request.url.params["mid"] == "ATVPDKIKX0DER"
        query = request.url.params["prefix"]
        queries.add(query)
        if query == "linen z":
            return httpx.Response(429)
        return httpx.Response(
            200,
            json={
                "suggestions": [
                    {"value": "Linen  Pants" if query == "linen" else "linen pants"},
                    {"value": "linen"},
                ]
            },
        )

    respx.get(SUGGESTIONS).mock(side_effect=upstream)
    response = await client.post(URL, headers=AUTH, json={"tool": "keywords", "keyword": "linen"})
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "partial_fail"
    assert len(queries) == 30 and {"best linen", "cheap linen", "top linen", "linen a"} <= queries
    assert len(result["keywords"]) == 1 and len(result["keywords"][0]["queries"]) == 29
    failed = result["samples"][-1]
    assert failed["status"] == "failed" and "prefix=linen+z" in failed["source_url"]
    assert failed["collected_at"]


@respx.mock
async def test_req040_listing_and_old_contract(client):
    product = f"{AMAZON}/dp/B012345678"
    respx.get(product).respond(200, text=PRODUCT)
    response = await client.post(
        URL, headers=AUTH, json={"tool": "listing", "source": "b012345678"}
    )
    assert response.status_code == 200, response.text
    listing = response.json()["listing"]
    assert listing["description"] == "Visible product description."
    assert listing["image_count"] == 2 and listing["has_aplus"] is True
    assert listing["first_available"] == "January 3, 2025"
    assert listing["category_path"] == ["Clothing", "Trousers"]
    assert listing["rating"] is None and "rating" in listing["missing_fields"]
    assert listing["field_sources"]["description"] == product
    old = await client.post(
        URL.rsplit("/", 1)[0],
        headers=AUTH,
        json={
            "mode": "product",
            "source": "B012345678",
        },
    )
    assert old.status_code == 200 and old.json()["product"]["image_brief"]


@respx.mock
async def test_req041_competitors_align_when_one_fails(client):
    product = f"{AMAZON}/dp/B012345678"
    respx.get(product).respond(200, text=PRODUCT)
    respx.get(f"{AMAZON}/dp/B087654321").respond(403)
    response = await client.post(
        URL,
        headers=AUTH,
        json={
            "tool": "ppc",
            "sources": ["b012345678", product + "?tag=test", "B087654321"],
        },
    )
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "partial_fail"
    first, second = result["competitors"]
    assert first.keys() == second.keys()
    assert first["rating_count"] == "43" and first["price_currency"] == "USD"
    assert first["sales_ranks"] == ["#10 in Trousers"] and first["highlights"] == ["Wide leg"]
    assert first["field_sources"]["highlights"] == product
    assert second["reason"] and second["price"] is None


async def test_req042_fba_effective_dates_storage_surcharge_and_missing_band(client):
    body = {
        "tool": "fba",
        "category": "home_kitchen",
        "dimensions_in": [4, "0.5", 6],
        "weight_lb": "0.125",
        "price_usd": "10",
        "ship_date": "2026-10-15",
        "storage_start": "2026-09-30",
        "storage_days": 2,
        "selling_plan": "individual",
    }
    response = await client.post(URL, headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["size_tier"] == "small_standard"
    assert result["status"] == "success"
    lines = {line["name"]: line for line in result["lines"]}
    assert lines["配送费"]["amount_usd"] == "3.51"
    assert lines["配送费"]["source"]["effective_from"] == "2026-10-15"
    assert lines["燃油与物流附加费"]["amount_usd"] == "0.12"
    assert "1/30" in lines["基础仓储费"]["formula"] and "1/31" in lines["基础仓储费"]["formula"]
    assert result["total_usd"] == "6.12"
    response = await client.post(URL, headers=AUTH, json={**body, "weight_lb": "2"})
    result = response.json()
    assert result["status"] == "incomplete" and result["total_usd"] is None


RATE = {
    "version": "fixture-import-date",
    "effective_from": "2026-01-01",
    "effective_to": "2026-12-31",
    "source_url": "https://hts.usitc.gov/",
}


async def test_req043_landed_decimals_tax_basis_and_unknown_rates(client):
    body = {
        "tool": "landed",
        "origin": "CN",
        "destination": "US",
        "hs_code": "8518.30.20.00",
        "as_of": "2026-11-28",
        "currency": "EUR",
        "goods_value": "800",
        "freight": "100",
        "insurance": "0",
        "brokerage_usd": "25",
        "inland_usd": "10",
        "quantity": 10,
        "customs_value_usd": "880",
        "usd_per_currency": "1.1",
        "exchange_source": RATE,
        "duties_confirmed": True,
        "duties": [
            {"name": "test ad valorem", "basis": "customs_value", "rate": "0.075", "source": RATE},
            {"name": "test per-unit charge", "basis": "per_unit", "amount": "0.50", "source": RATE},
        ],
    }
    response = await client.post(URL, headers=AUTH, json=body)
    assert response.status_code == 200, response.text
    result = response.json()
    assert result["status"] == "success" and result["hs_code"] == "8518302000"
    assert Decimal(result["total_usd"]) == Decimal(1096)
    assert result["per_unit_usd"] == "109.60"
    response = await client.post(URL, headers=AUTH, json={**body, "as_of": "2027-01-01"})
    result = response.json()
    assert result["status"] == "incomplete" and result["total_usd"] is None
    assert result["known_subtotal_usd"] == "35.00" and result["pending"]


@respx.mock
async def test_req044_hs_code_and_description_use_authoritative_source(client):
    route = respx.get(HTS_SEARCH).respond(
        200,
        json=[
            {"htsno": "8518.30.20.00", "description": "Other headphones", "general": "Free"},
            {"htsno": "9902.13.84", "description": "Secondary code"},
        ],
    )
    for query, expected in [("85183020", "8518.30.20"), ("headphones", "headphones")]:
        response = await client.post(URL, headers=AUTH, json={"tool": "hs", "query": query})
        assert response.status_code == 200, response.text
        result = response.json()
        assert route.calls.last.request.url.params["keyword"] == expected
        assert len(result["candidates"]) == 1 and result["pending"]
        assert result["candidates"][0]["source_url"].startswith("https://hts.usitc.gov/")
    route.respond(503)
    response = await client.post(URL, headers=AUTH, json={"tool": "hs", "query": "headphones"})
    assert response.json()["status"] == "failed"
