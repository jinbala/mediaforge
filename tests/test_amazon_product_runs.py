from datetime import UTC, datetime
from uuid import UUID, uuid4

from kombu.exceptions import OperationalError

from mediaforge.db import amazon_research_store as run_store
from mediaforge.gateway.routers import amazon_research as router
from mediaforge.models.amazon_product import (
    ProductAnalysis,
    ProductCandidate,
    ProductCosts,
    ProductFinance,
    ProductPage,
    ProductReviewPage,
    ProductReviewSample,
    ProductSearch,
    ProductTemplate,
)
from mediaforge.models.amazon_research import AmazonProduct
from mediaforge.models.amazon_research_run import AmazonResearchRunRequest
from mediaforge.orchestrator import amazon_product
from mediaforge.rag.amazon_research import AMAZON
from tests import test_amazon_store_depth as store_tests

API = store_tests.API
TARGET = "B000000001"
COMPETITOR = "B000000002"
PRODUCT_URL = f"{AMAZON}/dp/{TARGET}"

store_api = store_tests.api


def _product_page(asin: str) -> ProductPage:
    now = datetime.now(UTC)
    product = AmazonProduct(
        asin=asin,
        title=f"Fixture product {asin}",
        url=f"{AMAZON}/dp/{asin}",
        image_brief="Fixture image brief",
        reviews=[],
    )
    return ProductPage(product=product, collected_at=now)


def _analysis() -> ProductAnalysis:
    return ProductAnalysis(
        sections=[],
        image_plan=[],
        listing=ProductTemplate(
            title="Fixture listing",
            bullets=[],
            description="Fixture description",
            aplus=[],
            backend_keywords="fixture",
            conditions=[],
        ),
        finance=ProductFinance(currency="USD", price=29.99, unit_profit=8.5),
        decision="可继续验证",
    )


async def _run_product(client, run_id: str, attempt: int = 1) -> dict:
    await store_tests.pipeline.process_research(UUID(run_id), attempt)
    response = await client.get(f"{API}/{run_id}")
    assert response.status_code == 200, response.text
    return response.json()


async def test_product_url_is_canonicalized_and_invalid_origins_are_rejected(store_api):
    client, _, _, queued, _ = store_api
    response = await client.post(
        API,
        json={
            "mode": "product",
            "source": "https://www.amazon.com/gp/product/b000000001?tag=tracking&ref=detail",
            "keyword": " fixture ",
            "review_samples": "manual review text",
        },
    )
    assert response.status_code == 202, response.text
    assert response.json()["source"] == PRODUCT_URL
    assert response.json()["canonical_source"] == f"amazon:US:product:{PRODUCT_URL}"
    assert response.json()["inputs"]["keyword"] == "fixture"
    assert response.json()["inputs"]["review_samples"] == "manual review text"
    assert len(queued) == 1

    rejected = await client.post(
        API, json={"mode": "product", "source": "https://example.com/dp/B000000001"}
    )
    assert rejected.status_code == 422
    assert len(queued) == 1


async def test_product_duplicate_queue_resume_and_history_are_owner_scoped(store_api, monkeypatch):
    client, owner, identity, queued, _ = store_api
    submit = router.process_amazon_research.apply_async

    def unavailable(*_args, **_kwargs):
        raise OperationalError("fixture broker down")

    monkeypatch.setattr(router.process_amazon_research, "apply_async", unavailable)
    body = {
        "mode": "product",
        "source": f"https://www.amazon.com/dp/{TARGET}?tag=fixture",
        "review_samples": "manual review",
        "submission_id": str(uuid4()),
    }
    first = await client.post(API, json=body)
    assert first.status_code == 202
    run_id = first.json()["id"]
    assert first.json()["status"] == "queue_failed"

    monkeypatch.setattr(router.process_amazon_research, "apply_async", submit)
    duplicate = await client.post(API, json={**body, "source": PRODUCT_URL})
    assert duplicate.status_code == 202
    assert duplicate.json()["id"] == run_id
    assert duplicate.json()["status"] == "queue_failed"
    assert queued == []

    resumed = await client.post(f"{API}/{run_id}/retry?target=resume", json={})
    assert resumed.status_code == 202
    assert resumed.json()["status"] == "queued"
    assert queued == [(run_id, 2)]

    for other_owner in ((owner[0], uuid4()), (uuid4(), owner[1])):
        identity["owner"] = other_owner
        assert (await client.get(API)).json() == []
        assert (await client.get(f"{API}/{run_id}")).status_code == 404
        assert (
            await client.post(f"{API}/{run_id}/retry?target=resume", json={})
        ).status_code == 404
    identity["owner"] = owner
    assert (await client.get(API)).json()[0]["id"] == run_id


async def test_product_manual_reviews_costs_and_failed_subitem_retry_preserve_successful_evidence(
    store_api, monkeypatch
):
    client, _, _, queued, _ = store_api
    calls = {"product": 0, "competitor": 0, "search": {}, "reviews": {}, "manual": []}
    costs = ProductCosts(
        purchase=5,
        inbound=1,
        fulfillment=4,
        storage=0.5,
        ad_pct=0.1,
        return_pct=0.03,
        referral_pct=0.15,
        sale_price=29.99,
        fee_source="fixture fees",
        benchmark_bsr=1200,
        benchmark_monthly_units=500,
    )

    async def collect_product(_client, source, **kwargs):
        if COMPETITOR in source:
            calls["competitor"] += 1
            return _product_page(COMPETITOR)
        calls["product"] += 1
        page = _product_page(TARGET)
        calls["manual"].append(kwargs.get("review_samples"))
        if kwargs.get("review_samples"):
            page.reviews = [
                ProductReviewSample(
                    id="manual-review",
                    text=kwargs["review_samples"],
                    source="manual",
                )
            ]
        return page

    async def collect_search(_client, query):
        calls["search"][query] = calls["search"].get(query, 0) + 1
        return ProductSearch(
            query=query, url=f"{AMAZON}/s?k={query}", collected_at=datetime.now(UTC)
        )

    async def collect_reviews(_client, asin, bucket):
        calls["reviews"][bucket] = calls["reviews"].get(bucket, 0) + 1
        if bucket == "one" and calls["reviews"][bucket] == 1:
            raise RuntimeError("fixture review failure")
        return ProductReviewPage(
            bucket=bucket,
            url=f"{AMAZON}/product-reviews/{asin}?bucket={bucket}",
            collected_at=datetime.now(UTC),
            rows=[
                ProductReviewSample(
                    id=f"{bucket}-review", text=f"{bucket} star fixture", source="manual"
                )
            ]
            if bucket == "one"
            else [],
        )

    queries = [f"fixture query {number}" for number in range(1, 6)]
    monkeypatch.setattr(amazon_product, "collect_product", collect_product)
    monkeypatch.setattr(amazon_product, "collect_search", collect_search)
    monkeypatch.setattr(amazon_product, "collect_reviews", collect_reviews)
    monkeypatch.setattr(amazon_product, "choose_queries", lambda *_args: queries)
    monkeypatch.setattr(
        amazon_product,
        "choose_competitors",
        lambda *_args: [
            ProductCandidate(
                asin=COMPETITOR,
                title="Fixture competitor",
                url=f"{AMAZON}/dp/{COMPETITOR}",
            )
        ],
    )
    monkeypatch.setattr(amazon_product, "analyze_product", lambda _depth, _costs: _analysis())

    async def translate(_products, _colors):
        return []

    monkeypatch.setattr(amazon_product, "localize_research_fields", translate)

    response = await client.post(
        API,
        json={
            "mode": "product",
            "source": PRODUCT_URL,
            "keyword": "fixture keyword",
            "review_samples": "Manual review: stitching held after washing.",
            "product_costs": costs.model_dump(mode="json"),
        },
    )
    assert response.status_code == 202, response.text
    run_id = response.json()["id"]
    assert len(queued) == 1
    partial = await _run_product(client, run_id)
    assert partial["status"] == "partial_fail"
    assert partial["inputs"]["review_samples"] == "Manual review: stitching held after washing."
    assert partial["inputs"]["product_costs"]["purchase"] == 5
    assert partial["snapshot"]["product_depth"]["analysis"]["finance"]["unit_profit"] == 8.5

    evidence = {item["key"]: item for item in partial["snapshot"]["evidence"]}
    assert set(evidence) == {
        "product",
        *(f"search-{index}" for index in range(1, 6)),
        f"competitor-{COMPETITOR}",
        "product-translation",
        "reviews-one",
        "reviews-two",
        "reviews-three",
        "reviews-recent",
        "product-analysis",
    }
    assert evidence["product"]["status"] == "success"
    assert evidence[f"competitor-{COMPETITOR}"]["status"] == "success"
    assert evidence["reviews-one"]["status"] == "failed"
    assert evidence["reviews-two"]["status"] == "empty"
    successful_snapshot = partial["snapshot"]["product_depth"]
    assert successful_snapshot["overview"]["reviews"][0]["source"] == "manual"
    assert (
        successful_snapshot["overview"]["reviews"][0]["text"]
        == "Manual review: stitching held after washing."
    )
    assert calls["manual"] == ["Manual review: stitching held after washing."]
    assert len(successful_snapshot["searches"]) == 5
    assert [page["bucket"] for page in successful_snapshot["review_pages"]] == [
        "two",
        "three",
        "recent",
    ]

    retry = await client.post(f"{API}/{run_id}/retry?target=failed&item_key=reviews-one", json={})
    assert retry.status_code == 202, retry.text
    complete = await _run_product(client, run_id, 2)
    assert complete["status"] == "done"
    assert complete["snapshot"]["product_depth"]["analysis"]["finance"]["unit_profit"] == 8.5
    assert [page["bucket"] for page in complete["snapshot"]["product_depth"]["review_pages"]] == [
        "two",
        "three",
        "recent",
        "one",
    ]
    assert calls["product"] == 1
    assert calls["competitor"] == 1
    assert set(calls["search"].values()) == {1}
    assert calls["reviews"] == {"one": 2, "two": 1, "three": 1, "recent": 1}


def test_adding_product_costs_does_not_change_legacy_store_or_category_fingerprints():
    base_inputs = {
        "store": AmazonResearchRunRequest(mode="store", source=store_tests.INITIAL),
        "bestsellers": AmazonResearchRunRequest(
            mode="bestsellers",
            source="https://www.amazon.com/gp/bestsellers/fashion",
            keyword="fixture",
        ),
    }
    costs = ProductCosts(purchase=12, sale_price=25)
    for mode, request in base_inputs.items():
        augmented = request.model_copy(update={"product_costs": costs})
        assert run_store._fingerprint(augmented) == run_store._fingerprint(request), mode

    product_without_costs = AmazonResearchRunRequest(mode="product", source=PRODUCT_URL)
    product_with_costs = product_without_costs.model_copy(update={"product_costs": costs})
    assert run_store._fingerprint(product_with_costs) != run_store._fingerprint(
        product_without_costs
    )
