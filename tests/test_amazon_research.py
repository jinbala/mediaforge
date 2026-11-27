import json
from collections import OrderedDict
from unittest.mock import AsyncMock

import httpx
import pytest
import respx

from mediaforge.config import Settings
from mediaforge.gateway.main import app
from mediaforge.gateway.middleware.rate_limit import limiter
from mediaforge.rag import amazon_store_translation
from mediaforge.rag.amazon_research import AMAZON, SUGGESTIONS, AmazonSourceError, normalize_source

ENDPOINT = "/api/v1/research/amazon"
AUTH = {"X-Api-Key": "test-api-key-starter"}
PRODUCT_URL = f"{AMAZON}/dp/B012345678"
CATEGORY_URL = f"{AMAZON}/gp/bestsellers/fashion"
STORE_URL = f"{AMAZON}/stores/Sample/page/11111111-1111-1111-1111-111111111111"
STORE_TAB = f"{AMAZON}/stores/page/22222222-2222-2222-2222-222222222222"

PRODUCT_HTML = """
<span id="productTitle">Linen trousers</span>
<img id="landingImage" src="data:image/gif;base64,AAAA"
     data-a-dynamic-image='{"https://m.media-amazon.com/images/I/trousers.jpg":[342,342]}'>
<a id="bylineInfo">Sample brand</a>
<span id="acrPopover" title="4.2 out of 5 stars"></span>
<span id="acrCustomerReviewText">1,234 ratings</span>
<div id="corePrice_feature_div"><span class="a-price">
  <span class="a-offscreen">$25.99</span>
</span></div>
<div id="feature-bullets"><ul><li><span class="a-list-item">Wide leg</span></li></ul></div>
<table id="productDetails_detailBullets_sections1">
<tr><th>Best Sellers Rank</th><td>#27 in Women's Pants (See Top 100)</td></tr>
</table>
<div data-hook="review">
  <i data-hook="review-star-rating">2.0 out of 5 stars</i>
  <span data-hook="review-body"><span>The fabric is too thin.</span></span>
</div>
"""


@pytest.fixture
async def client():
    limiter.reset()
    async with httpx.AsyncClient(
        transport=httpx.ASGITransport(app=app),
        base_url="http://mediaforge.test",
    ) as api:
        yield api


@pytest.fixture(autouse=True)
def isolated_store_translation(monkeypatch):
    # 每个测试从空缓存开始，默认清空 API Key，翻译用例再注入模拟模型响应。
    settings = Settings(openrouter_api_key="", _env_file=None)
    monkeypatch.setattr(amazon_store_translation, "get_settings", lambda: settings)
    monkeypatch.setattr(amazon_store_translation, "_translations", OrderedDict())
    return settings


@pytest.mark.parametrize(
    ("mode", "source"),
    [
        ("product", "https://amazon.com.example.test/dp/B012345678"),
        ("product", "https://www.amazon.com@127.0.0.1/dp/B012345678"),
        ("product", "https://www.amazon.com:8443/dp/B012345678"),
        ("product", "https://www.amazon.com/ap/signin?next=/dp/B012345678"),
        ("store", "https://www.amazon.com/stores/example/../page"),
        ("store", "https://www.amazon.co.uk/stores/page/22222222-2222-2222-2222-222222222222"),
        ("bestsellers", "https://www.amazon.com/gp/bestsellers/%2e%2e"),
    ],
)
def test_invalid_sources(mode, source):
    with pytest.raises(AmazonSourceError):
        normalize_source(mode, source)


def test_normalizes_compatibility_urls():
    assert normalize_source("product", " b012345678 ") == PRODUCT_URL
    assert normalize_source("product", PRODUCT_URL + "?tag=test") == PRODUCT_URL
    category_page = normalize_source("bestsellers", CATEGORY_URL + "?pg=2&tag=test")
    assert category_page == CATEGORY_URL + "?pg=2"
    assert normalize_source("store", STORE_URL + "?ref_=test") == STORE_URL


@respx.mock
async def test_product_endpoint_keeps_review_provenance_and_reports_topics(client):
    product = respx.get(PRODUCT_URL).respond(200, text=PRODUCT_HTML)
    keyword = respx.get(SUGGESTIONS).respond(
        200,
        json={"suggestions": [{"value": "linen trousers"}, {"value": "linen trousers"}]},
    )
    response = await client.post(
        ENDPOINT,
        headers=AUTH,
        json={
            "mode": "product",
            "source": "b012345678",
            "keyword": "linen",
            "review_samples": "The fabric is too thin.\nThe zipper broke.",
        },
    )
    assert response.status_code == 200, response.text
    assert product.called and keyword.called
    report = response.json()
    item = report["product"]
    assert item["image_url"] == "https://m.media-amazon.com/images/I/trousers.jpg"
    assert item["sales_ranks"] == ["#27 in Women's Pants"]
    assert (item["price"], item["rating"], item["rating_count"]) == ("$25.99", "4.2", "1,234")
    assert item["highlights"] == ["Wide leg"]
    assert [(sample["source"], sample["rating"]) for sample in item["reviews"]] == [
        ("amazon", "2.0"), ("manual", None),
    ]
    assert [(topic["label"], topic["matches"]) for topic in item["review_topics"]] == [
        ("面料与厚度", 1), ("做工与耐用", 1),
    ]
    assert "实物面料" in item["image_brief"]
    assert report["suggestions"] == ["linen trousers"]


@respx.mock
async def test_bestsellers_returns_visible_ranks_when_suggestions_fail(client):
    respx.get(CATEGORY_URL).respond(
        200,
        text="""
        <h1>Clothing Best Sellers</h1>
        <div><span class="zg-bdg-text">#2</span><div class="zg-grid-general-faceout">
          <a href="/example/dp/B012345678?tag=test">
            <img src="https://images-na.ssl-images-amazon.com/images/I/shirt.jpg">
            <span class="p13n-sc-truncate">Shirt</span>
          </a>
          <span class="p13n-sc-price">$14</span><span class="a-icon-alt">4.5 out of 5 stars</span>
          <a><span class="a-size-small">231</span></a>
        </div></div>
        <div class="zg-grid-general-faceout">
          <a href="/dp/B012345678"><span class="p13n-sc-truncate">Duplicate</span></a>
        </div>
        """,
    )
    respx.get(SUGGESTIONS).respond(503)
    response = await client.post(
        ENDPOINT, headers=AUTH,
        json={"mode": "bestsellers", "source": CATEGORY_URL, "keyword": "shirt"},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["listings"] == [{
        "asin": "B012345678", "url": PRODUCT_URL, "title": "Shirt", "rank": "#2",
        "title_zh": None,
        "image_url": "https://images-na.ssl-images-amazon.com/images/I/shirt.jpg",
        "price": "$14", "rating": "4.5", "rating_count": "231",
        "store_variant": None,
    }]
    assert data["warnings"] and data["suggestions"] == []


@respx.mock
async def test_store_keeps_tabs_when_home_has_no_listing_and_reads_child_page(client):
    # 模拟只有栏目导航的店铺首页，以及商品卡片顺序和链接顺序不同的栏目页。
    respx.get(STORE_URL).respond(
        200, text=f'<title>Sample Store</title><a href="{STORE_TAB}?ref_=nav">Shirts</a>',
    )
    respx.get(STORE_TAB).respond(
        200,
        text="""
        <title>Shirts</title>
        <a href="/dp/B012345678"><img alt=""></a>
        <a href="/dp/B012345679">Shirt</a>
        <a href="/dp/B012345680">
          <img src="https://m.media-amazon.com.example.test/images/I/wrong.jpg" alt="Shirt">
        </a>
        <a href="/dp/B012345681">
          <img data-src="//m.media-amazon.com/images/I/black.jpg" alt="Shirt">
        </a>
        <script>var config = {"products":[
          {"asin":"B012345679","detailPageLinkURL":"/dp/B012345679","productImages":{
            "altText":"Shirt","images":[
              {"variant":"MAIN","hiRes":{"url":"https://m.media-amazon.com/images/I/red.jpg"}}
            ]
          }},
          {"asin":"B012345678","detailPageLinkURL":"/dp/B012345678","productImages":{
            "altText":"Shirt","images":[
              {"variant":"PT01","lowRes":{"url":"https://m.media-amazon.com/images/I/detail.jpg"}},
              {"variant":"MAIN","lowRes":{"url":"https://m.media-amazon.com/images/I/blue.jpg"},
                "hiRes":{"url":"https://m.media-amazon.com/images/I/blue-HD.jpg"}}
            ]
          }}
        ]};</script>
        """,
    )
    home = await client.post(ENDPOINT, headers=AUTH, json={"mode": "store", "source": STORE_URL})
    assert home.status_code == 200, home.text
    data = home.json()
    assert data["warnings"] and data["listings"] == []
    assert data["store_pages"] == [{"title": "Shirts", "title_zh": None, "url": STORE_TAB}]
    tab = await client.post(ENDPOINT, headers=AUTH, json={"mode": "store", "source": STORE_TAB})
    assert tab.status_code == 200, tab.text
    listings = tab.json()["listings"]
    assert [(item["asin"], item["image_url"]) for item in listings] == [
        ("B012345678", "https://m.media-amazon.com/images/I/blue.jpg"),
        ("B012345679", "https://m.media-amazon.com/images/I/red.jpg"),
        ("B012345680", None),
        ("B012345681", "https://m.media-amazon.com/images/I/black.jpg"),
    ]
    assert all(item["title"] == "Shirt" and item["rank"] is None for item in listings)


@respx.mock
async def test_store_reports_size_groups_from_amazon_variations(client):
    asins = ["B000000001", "B000000002", "B000000003", "B000000004", "B000000005"]
    dimension_keys = [{"symbol": "color_name"}, {"symbol": "size_name"}]
    # 红蓝两组各有 S、M 两个尺码，最后一条的尺码下标越界，应保留为独立商品。
    coordinates = [[0, 0], [0, 1], [1, 0], [1, 1], [1, 2]]
    twister = {
        "dimensionKeys": dimension_keys,
        "dimensionValues": [["Red", "Blue"], ["Small", "Medium"]],
        "variations": [
            {"asin": asin, "values": values}
            for asin, values in zip(asins, coordinates, strict=True)
        ],
    }
    products = [
        {
            "asin": asin, "detailPageLinkURL": f"/dp/{asin}",
            "title": {"displayString": "Ribbed Top"},
            "productImages": {"altText": f"Ribbed Top {asin}"},
            "twisterVariations": twister,
        }
        for asin in asins
    ]
    links = ''.join(f'<a href="/dp/{asin}">Top</a>' for asin in asins)
    respx.get(STORE_URL).respond(200, text=f"""
        <title>Amazon.com: Sample Store</title>
        {links}
        <script>var config = {json.dumps({"products": products})};</script>
    """)
    response = await client.post(
        ENDPOINT, headers=AUTH, json={"mode": "store", "source": STORE_URL},
    )
    assert response.status_code == 200, response.text
    listings = response.json()["listings"]
    assert [item["url"] for item in listings] == [f"{AMAZON}/dp/{asin}" for asin in asins]
    assert [item["title"] for item in listings] == [f"Ribbed Top {asin}" for asin in asins]
    assert [
        (variant["group_asin"], variant["size"], variant["color"], variant["title"])
        for item in listings[:4] if (variant := item["store_variant"])
    ] == [
        (asins[0], "Small", "Red", "Ribbed Top"), (asins[0], "Medium", "Red", "Ribbed Top"),
        (asins[2], "Small", "Blue", "Ribbed Top"), (asins[2], "Medium", "Blue", "Ribbed Top"),
    ]
    assert listings[-1]["store_variant"] is None


@respx.mock
async def test_novel_store_localizes_every_title_and_keeps_sources(
    client, monkeypatch, isolated_store_translation,
):
    isolated_store_translation.openrouter_api_key = "test-only"
    chat = AsyncMock(return_value='{"2":"女式亚麻长裤","1":"女式圆领长袖上衣","0":"春季衣橱"}')
    monkeypatch.setattr(amazon_store_translation.OpenRouterClient, "chat_completion", chat)
    respx.get(STORE_URL).respond(200, text=f"""
        <title>Amazon.com: A Brand: Spring Wardrobe</title>
        <a href="{STORE_TAB}">Women's Long-Sleeved Crew Neck Top</a>
        <a href="/dp/B012345678">Women's Linen Trousers</a>
    """)
    # 连续调研同一家店，两份报告均保留中英文及链接，第二次使用已缓存译文。
    for _ in range(2):
        response = await client.post(
            ENDPOINT, headers=AUTH, json={"mode": "store", "source": STORE_URL},
        )
        assert response.status_code == 200, response.text
        data = response.json()
        assert data["title_zh"] == "A Brand · 春季衣橱"
        assert data["title"] == "Amazon.com: A Brand: Spring Wardrobe"
        assert data["store_pages"] == [{
            "title": "Women's Long-Sleeved Crew Neck Top", "title_zh": "女式圆领长袖上衣",
            "url": STORE_TAB,
        }]
        assert data["listings"][0]["title_zh"] == "女式亚麻长裤"
        assert data["listings"][0]["title"] == "Women's Linen Trousers"
        assert data["listings"][0]["url"] == PRODUCT_URL
        assert not data["warnings"]
    chat.assert_awaited_once()
    assert STORE_URL not in chat.call_args.args[0][1]["content"]


@respx.mock
async def test_translation_failure_preserves_store_navigation(
    client, monkeypatch, isolated_store_translation,
):
    isolated_store_translation.openrouter_api_key = "test-only"
    chat = AsyncMock(side_effect=TimeoutError)
    monkeypatch.setattr(amazon_store_translation.OpenRouterClient, "chat_completion", chat)
    respx.get(STORE_URL).respond(200, text=f"""
        <title>Amazon.com: A Brand: Featured Deals</title>
        <a href="{STORE_TAB}">Women's Lace Three-Quarter Sleeve T-Shirt</a>
        <a href="/stores/page/33333333-3333-3333-3333-333333333333">New Arrivals</a>
    """)
    response = await client.post(
        ENDPOINT, headers=AUTH, json={"mode": "store", "source": STORE_URL},
    )
    assert response.status_code == 200, response.text
    data = response.json()
    assert data["title_zh"] == "A Brand · 精选优惠"
    assert data["store_pages"][0]["url"] == STORE_TAB
    assert data["store_pages"][0]["title_zh"] is None
    assert data["store_pages"][1]["title_zh"] == "新品上架"
    assert any("未取得中文译文" in warning for warning in data["warnings"])


@respx.mock
async def test_rejects_redirect_to_unapproved_host(client):
    respx.get(PRODUCT_URL).respond(302, headers={"location": "https://example.test/dp/B012345678"})
    response = await client.post(
        ENDPOINT, headers=AUTH, json={"mode": "product", "source": PRODUCT_URL},
    )
    assert response.status_code == 502, response.text
    assert len(respx.calls) == 1


@respx.mock
async def test_detects_amazon_verification_page_with_keyword_request(client):
    respx.get(PRODUCT_URL).respond(200, text='<form action="/errors/validateCaptcha"></form>')
    respx.get(SUGGESTIONS).respond(200, json={"suggestions": [{"value": "shirt"}]})
    response = await client.post(
        ENDPOINT, headers=AUTH, json={"mode": "product", "source": PRODUCT_URL, "keyword": "shirt"},
    )
    assert response.status_code == 502, response.text
    assert "访问验证" in response.json()["detail"]


@respx.mock
async def test_rejects_invalid_source_and_unauthenticated_requests_without_upstream_calls(client):
    payload = {"mode": "product", "source": "https://example.test/dp/B012345678"}
    anonymous = await client.post(ENDPOINT, json=payload)
    assert anonymous.status_code == 401, anonymous.text
    response = await client.post(ENDPOINT, headers=AUTH, json=payload)
    assert response.status_code == 422, response.text
    assert len(respx.calls) == 0
