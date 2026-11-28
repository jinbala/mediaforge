from datetime import UTC, datetime
from unittest.mock import AsyncMock
from urllib.parse import parse_qs, urlencode, urlsplit

import httpx
import pytest
import respx
from bs4 import BeautifulSoup

from mediaforge.models.amazon_product import (
    ProductPage,
    ProductReviewPage,
    ProductSearch,
)
from mediaforge.rag.amazon_product_collection import (
    _amount,
    _parse_product_page,
    _parse_reviews,
    _product_images,
    choose_competitors,
    choose_queries,
    collect_product,
    collect_reviews,
    collect_search,
    review_url,
)
from mediaforge.rag.amazon_research import AMAZON, AmazonFetchError, normalize_source

ASIN = "B012345678"
PRODUCT_URL = f"{AMAZON}/dp/{ASIN}"
REVIEW_PATH = f"/product-reviews/{ASIN}"


@pytest.fixture
async def client():
    async with httpx.AsyncClient(follow_redirects=False) as value:
        yield value


@pytest.fixture
def product_html():
    return """
    <span id="productTitle">Linen trousers</span>
    <a id="bylineInfo">Visit the Sample Brand Store</a>
    <div id="corePrice_feature_div">
      <span class="a-price"><span class="a-offscreen">$25.99</span></span>
    </div>
    <span id="acrPopover" title="4.2 out of 5 stars"></span>
    <div id="feature-bullets"><ul><li><span class="a-list-item">Wide leg</span></li></ul></div>
    <div id="important-information">
      <div class="a-section content">
        <h4>Safety Information</h4><p>Discard the plastic bag.</p>
      </div>
    </div>
    <div id="productDetails_feature_div">
      <table><tr><th>Material</th><td>Linen</td></tr></table>
    </div>
    <div id="histogramTable"><table>
      <tr aria-label="5 star 70%"><td>5 stars</td><td>70%</td></tr>
      <tr aria-label="4 star 20%"><td>4 stars</td><td>20%</td></tr>
    </table></div>
    <img id="landingImage" alt="Front view"
      data-a-dynamic-image='{"https://m.media-amazon.com/images/I/front.jpg":[1200,1200]}'>
    <div id="aplus_feature_div"><div class="aplus-v2"><p>Product story</p></div></div>
    <input id="parentAsin" value="B098765432">
    <div id="twister"><span data-asin="B000000001"></span></div>
    <div data-hook="review" id="R1">
      <i data-hook="review-star-rating">2.0 out of 5 stars</i>
      <span data-hook="review-date">Reviewed in the United States on October 5, 2025</span>
      <span data-hook="format-strip">Size: M</span>
      <span data-hook="avp-badge">Verified Purchase</span>
      <span data-hook="helpful-vote-statement">12 people found this helpful</span>
      <span data-hook="review-body">The fabric feels light.</span>
      <a data-hook="review-title" href="/gp/customer-reviews/R1">Review</a>
    </div>
    <video src="https://m.media-amazon.com/images/S/video.mp4"
      poster="https://m.media-amazon.com/images/I/poster.jpg"></video>
    """


@respx.mock
async def test_collect_product_retains_page_evidence_and_review_fields(client, product_html):
    respx.get(PRODUCT_URL).respond(200, text=product_html)

    page = await collect_product(client, PRODUCT_URL)

    assert isinstance(page, ProductPage)
    assert page.product.asin == ASIN
    assert page.currency == "USD" and page.price_amount == 25.99
    assert page.specs == {"Material": "Linen"}
    assert page.important == ["Safety Information Discard the plastic bag.", "Wide leg"]
    assert page.aplus == ["Product story"]
    assert page.images[0].url.endswith("front.jpg")
    assert (page.images[0].width, page.images[0].height) == (1200, 1200)
    assert page.videos[0].poster.endswith("poster.jpg")
    assert page.rating_distribution == {5: 70.0, 4: 20.0}
    assert page.parent_asin == "B098765432"
    assert page.variant_asins == ["B000000001"]
    assert page.reviews[0].stars == 2
    assert page.reviews[0].date == "2025-10-05"
    assert page.reviews[0].variant == "Size: M"
    assert page.reviews[0].verified is True and page.reviews[0].helpful == 12
    assert page.field_evidence["important"] == "#important-information, #feature-bullets"
    assert page.screenshot_data_url is None


@respx.mock
async def test_collect_product_can_attach_screenshot(client, product_html, monkeypatch):
    capture = AsyncMock(return_value="data:image/png;base64,AAAA")
    monkeypatch.setattr(
        "mediaforge.rag.amazon_product_collection.capture_product_screenshot", capture,
    )
    respx.get(PRODUCT_URL).respond(200, text=product_html)

    page = await collect_product(client, PRODUCT_URL, include_screenshot=True)

    capture.assert_awaited_once_with(PRODUCT_URL, product_html.encode())
    assert page.screenshot_data_url == capture.return_value


@respx.mock
async def test_collect_product_keeps_manual_review_source(client, product_html):
    respx.get(PRODUCT_URL).respond(200, text=product_html)
    page = await collect_product(client, PRODUCT_URL, review_samples="Customer-provided sample")
    manual = next(review for review in page.reviews if review.source == "manual")
    assert manual.text == "Customer-provided sample"
    assert manual.id.startswith("manual-")
    assert manual.verified is None


@respx.mock
async def test_collect_product_rejects_cross_asin_redirect(client):
    other = f"{AMAZON}/dp/B000000001"
    respx.get(PRODUCT_URL).respond(302, headers={"location": other})
    respx.get(other).respond(200, text='<span id="productTitle">Wrong product</span>')
    with pytest.raises(AmazonFetchError) as error:
        await collect_product(client, PRODUCT_URL)
    assert error.value.code == "asin_redirect"


@respx.mock
async def test_collect_product_propagates_not_found(client):
    respx.get(PRODUCT_URL).respond(404)
    with pytest.raises(AmazonFetchError) as error:
        await collect_product(client, PRODUCT_URL)
    assert error.value.code == "not_found"


@respx.mock
async def test_collect_search_parses_organic_and_sponsored_rows_and_count(client):
    query = "linen trousers"
    url = f"{AMAZON}/s?{urlencode({'k': query})}"
    html = """
    <div id="search"><div class="s-desktop-toolbar"><span class="a-text-normal">
      1-2 of over 1,000 results for "linen trousers"
    </span></div>
    <div data-component-type="s-search-result" data-asin="B000000001">
      <h2><a href="/dp/B000000001"><span>Relaxed Linen Pants</span></a></h2>
      <span class="a-price"><span class="a-offscreen">$30.50</span></span>
    </div>
    <div data-component-type="s-search-result" data-asin="B000000002">
      <span class="puis-sponsored-label-text">Sponsored</span>
      <h2><a href="/dp/B000000002"><span>Sponsored Trousers</span></a></h2>
    </div></div>
    """
    respx.get(url).respond(200, text=html)

    result = await collect_search(client, query)

    assert isinstance(result, ProductSearch)
    assert [(row.asin, row.rank, row.sponsored) for row in result.rows] == [
        ("B000000001", 1, False), ("B000000002", 2, True),
    ]
    assert result.rows[0].price_amount == 30.5 and result.rows[0].currency == "USD"
    assert (result.result_min, result.result_max) == (1001, None)
    assert "1,000" in result.result_label
    assert result.collected_at.tzinfo == UTC


@respx.mock
async def test_collect_search_rejects_redirect_to_different_query(client):
    query = "linen trousers"
    url = f"{AMAZON}/s?{urlencode({'k': query})}"
    other = f"{AMAZON}/s?{urlencode({'k': 'different'})}"
    respx.get(url).respond(302, headers={"location": other})
    respx.get(other).respond(200, text="<div id='search'></div>")
    with pytest.raises(AmazonFetchError) as error:
        await collect_search(client, query)
    assert error.value.code == "redirect"


@pytest.mark.parametrize(
    ("bucket", "filter_value", "stars"),
    [
        ("one", "one_star", 1),
        ("two", "two_star", 2),
        ("three", "three_star", 3),
        ("recent", None, 5),
    ],
)
@respx.mock
async def test_collect_reviews_requests_only_selected_bucket(client, bucket, filter_value, stars):
    params = {"sortBy": "recent", "pageNumber": "1"}
    if filter_value:
        params["filterByStar"] = filter_value
    url = normalize_source("reviews", f"{AMAZON}{REVIEW_PATH}?{urlencode(params)}")
    html = f"""
    <div id="cm_cr-review_list"><div data-hook="review" id="R1">
      <i data-hook="review-star-rating">{stars}.0 out of 5 stars</i>
      <span data-hook="review-date">Reviewed in the United States on October 5, 2025</span>
      <span data-hook="review-body">Useful review</span>
      <span data-hook="avp-badge">Verified Purchase</span>
      <span data-hook="helpful-vote-statement">12 people found this helpful</span>
    </div></div>
    """
    route = respx.get(url).respond(200, text=html)

    result = await collect_reviews(client, ASIN, bucket)

    assert isinstance(result, ProductReviewPage)
    assert route.call_count == 1
    assert result.bucket == bucket and result.url == str(route.calls.last.request.url)
    assert urlsplit(result.url).path == REVIEW_PATH
    assert parse_qs(urlsplit(result.url).query) == {name: [value] for name, value in params.items()}
    assert result.rows[0].stars == stars and result.rows[0].helpful == 12
    assert result.rows[0].verified is True and result.rows[0].date == "2025-10-05"
    assert result.limited is False


@pytest.mark.parametrize("target", [PRODUCT_URL, f"https://www.amazon.ca{REVIEW_PATH}"])
@respx.mock
async def test_collect_reviews_rejects_redirect_outside_scope(client, target):
    url = review_url(ASIN, "recent")
    respx.get(url).respond(302, headers={"location": target})
    wrong_scope = respx.get(target).respond(200, text="<div>Wrong scope</div>")
    with pytest.raises(AmazonFetchError) as error:
        await collect_reviews(client, ASIN, "recent")
    assert error.value.code == "redirect"
    assert not wrong_scope.called


@pytest.mark.parametrize("bucket", ["recent", "two"])
@respx.mock
async def test_collect_reviews_rejects_redirect_with_removed_or_changed_bucket(client, bucket):
    requested = review_url(ASIN, "one")
    final = review_url(ASIN, bucket)
    respx.get(requested).respond(302, headers={"location": final})
    respx.get(final).respond(200, text='<div id="cm_cr-review_list"></div>')

    with pytest.raises(AmazonFetchError) as error:
        await collect_reviews(client, ASIN, "one")

    assert error.value.code == "redirect"


@respx.mock
async def test_collect_reviews_accepts_redirect_reordering_same_bucket_params(client):
    url = review_url(ASIN, "two")
    reordered = f"{AMAZON}{REVIEW_PATH}?filterByStar=two_star&pageNumber=1&sortBy=recent"
    route = respx.get(url).mock(side_effect=[
        httpx.Response(302, headers={"location": reordered}),
        httpx.Response(200, text='<div id="cm_cr-review_list"></div>'),
    ])

    result = await collect_reviews(client, ASIN, "two")

    assert route.call_count == 2
    assert result.url == url and result.bucket == "two"


@pytest.mark.parametrize("bucket", ["one", "recent"])
@respx.mock
async def test_collect_reviews_enforces_observed_stars_in_filtered_bucket(client, bucket):
    url = review_url(ASIN, bucket)
    html = """
    <div data-hook="review" id="Rwrong">
      <i data-hook="review-star-rating">4 out of 5 stars</i>
      <span data-hook="review-body">Unrelated to this filter.</span>
    </div>
    <div data-hook="review" id="Rinvalid">
      <i data-hook="review-star-rating">1.5 out of 5 stars</i>
      <span data-hook="review-body">Malformed rating.</span>
    </div>
    <div data-hook="review" id="Rmissing">
      <span data-hook="review-body">A note about size.</span>
    </div>
    <div data-hook="review" id="Rmatch">
      <i data-hook="cmps-review-star-rating">1.0 out of 5 stars</i>
      <span data-hook="review-body">A note about size.</span>
      <a data-hook="review-title" href="/gp/customer-reviews/Rmatch">Review</a>
    </div>
    """
    respx.get(url).respond(200, text=html)

    result = await collect_reviews(client, ASIN, bucket)

    assert result.url == url and result.bucket == bucket
    if bucket == "one":
        assert [(row.id, row.stars, row.source, row.url) for row in result.rows] == [
            ("Rmatch", 1, "amazon", f"{AMAZON}/gp/customer-reviews/Rmatch"),
        ]
        assert result.limited is True
    else:
        assert [(row.id, row.stars) for row in result.rows] == [
            ("Rwrong", 4), ("Rinvalid", None), ("Rmissing", None),
        ]
        assert result.limited is False


def test_query_and_competitor_selection_uses_observed_product_searches(product_html):
    page = _parse_product_page(product_html.encode(), PRODUCT_URL, "")
    searches = [
        ProductSearch(
            query=query,
            url=f"{AMAZON}/s?k=test",
            collected_at=datetime.now(UTC),
            rows=[
                {
                    "asin": "B000000001",
                    "title": "Other linen trousers",
                    "url": f"{AMAZON}/dp/B000000001",
                    "rank": rank,
                    "brand": "Other Brand",
                    "price_amount": 30.0,
                    "currency": "USD",
                    "sponsored": False,
                },
                {
                    "asin": "B000000002",
                    "title": "Sponsored result",
                    "url": f"{AMAZON}/dp/B000000002",
                    "rank": rank + 1,
                    "brand": None,
                    "price_amount": None,
                    "currency": None,
                    "sponsored": True,
                },
                {
                    "asin": "B000000003",
                    "title": "Linen Trousers Storage Bag",
                    "url": f"{AMAZON}/dp/B000000003",
                    "rank": rank + 2,
                },
                {
                    "asin": "B000000004",
                    "title": "Plastic Safety Goggles",
                    "url": f"{AMAZON}/dp/B000000004",
                    "rank": rank + 3,
                },
            ],
        )
        for query, rank in (("linen alternatives", 2), ("linen reviews", 1))
    ]

    queries = choose_queries(page, "linen")
    candidates = choose_competitors(page, searches)

    assert len(queries) == 5 and len(set(queries)) == 5
    assert all("linen" in query.casefold() for query in queries)
    assert any("wide leg" in query.casefold() for query in queries)
    assert all("plastic" not in query.casefold() for query in queries)
    assert len(candidates) == 1
    assert candidates[0].asin == "B000000001"
    assert candidates[0].queries == ["linen alternatives", "linen reviews"]
    assert any("Different listed brand" in reason for reason in candidates[0].reasons)


def test_review_url_uses_reviews_normalization():
    source = f"{AMAZON}{REVIEW_PATH}?filterByStar=one_star&sortBy=recent&pageNumber=1"
    assert review_url(ASIN.lower(), "one") == normalize_source("reviews", source)


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        ("$1,299.99", (1299.99, "USD")),
        ("CA$25.00", (25.0, "CAD")),
        ("25.00 CAD", (25.0, "CAD")),
        ("AU$25.00 AUD", (25.0, "AUD")),
        ("S$25.00", (25.0, "SGD")),
        ("£18.50", (18.5, "GBP")),
        ("€18.50", (18.5, "EUR")),
        ("$10 - $20", (None, "USD")),
        ("CA$10 to CA$20", (None, "CAD")),
        ("$10 $20", (None, "USD")),
        ("$10 EUR 9", (None, None)),
        ("$10.00 USD", (10.0, "USD")),
        ("$12,99", (None, "USD")),
        ("$10.999", (None, "USD")),
        ("$-10.00", (None, "USD")),
    ],
)
def test_amount_currency_ranges_and_ambiguous_markers(value, expected):
    assert _amount(value) == expected


def test_product_gallery_reads_high_resolution_script_images():
    soup = BeautifulSoup(
        '<script>var colorImages = {"initial":[{'
        '"hiRes":"https://m.media-amazon.com/images/I/high.jpg",'
        '"large":"https://m.media-amazon.com/images/I/large.jpg"}]};</script>',
        "html.parser",
    )
    assert [image.url for image in _product_images(soup)] == [
        "https://m.media-amazon.com/images/I/high.jpg",
    ]


def test_review_collection_caps_usable_text_and_marks_limited():
    reviews = "".join(
        f'<div data-hook="review" id="R{i}"><span data-hook="review-body">'
        f'{"" if i == 0 else f"Review body {i}"}</span></div>'
        for i in range(12)
    )
    rows, limited = _parse_reviews(
        f'<div id="cm_cr-review_list">{reviews}</div>'.encode(),
        f"{AMAZON}/product-reviews/{ASIN}?sortBy=recent&pageNumber=1",
    )
    assert len(rows) == 10
    assert all(row.text for row in rows)
    assert limited is True
