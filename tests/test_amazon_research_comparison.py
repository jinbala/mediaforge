from datetime import UTC, datetime
from uuid import uuid4

import pytest

from mediaforge.models.amazon_category import CategoryScope
from mediaforge.models.amazon_product import (
    ProductAnalysis,
    ProductCosts,
    ProductDepth,
    ProductFinance,
    ProductPage,
    ProductTemplate,
)
from mediaforge.models.amazon_research import AmazonProduct, AmazonResearchReport, ResearchMode
from mediaforge.models.amazon_research_run import (
    AmazonEvidence,
    AmazonResearchRun,
    AmazonResearchRunRequest,
    AmazonResearchSnapshot,
    AmazonStoreSample,
    AmazonStoreSampleScope,
    StepStatus,
)
from mediaforge.workers.amazon_research_comparison import compare_research_runs

_NOW = datetime(2026, 11, 28, tzinfo=UTC)
_ASINS = tuple(f"B00000000{i}" for i in range(1, 7))
_AMAZON = "https://www.amazon.com"
_PRODUCT = f"{_AMAZON}/dp/{_ASINS[0]}"
_BOARD = f"{_AMAZON}/zgbs/fashion/123"
_BOARD_ALIAS = f"{_AMAZON}/gp/bestsellers/fashion/123?ref_=nav"
_STORE_ID = "62c7c3e2-be0e-4999-bc69-52f16ba436fa"
_STORE = f"{_AMAZON}/stores/Example/page/{_STORE_ID}"
_STORE_ALIAS = f"{_AMAZON}/stores/page/{_STORE_ID.upper()}?ref_=nav"
_OTHER_STORE = f"{_AMAZON}/stores/Example/page/b7ccc3e2-be0e-4999-bc69-52f16ba436fa"


def _run(
    mode: ResearchMode,
    *,
    snapshot: AmazonResearchSnapshot | None = None,
    costs: ProductCosts | None = None,
) -> AmazonResearchRun:
    source = {"product": _PRODUCT, "bestsellers": _BOARD, "store": _STORE}[mode]
    return AmazonResearchRun(
        id=uuid4(),
        mode=mode,
        source=source,
        status="partial_fail",
        stage="report",
        canonical_source=f"amazon:US:{mode}:{source}",
        created_at=_NOW,
        updated_at=_NOW,
        attempt=1,
        inputs=AmazonResearchRunRequest(mode=mode, source=source, product_costs=costs),
        snapshot=snapshot or AmazonResearchSnapshot(),
    )


def _product() -> AmazonProduct:
    return AmazonProduct(
        asin=_ASINS[0],
        title="Sample",
        url=_PRODUCT,
        image_brief="",
        price="$999.00",
        rating="4.5 out of 5",
        rating_count="9,000 ratings",
    )


def _analysis(profit: float, roi: float | None) -> ProductAnalysis:
    return ProductAnalysis(
        sections=[],
        image_plan=[],
        decision="",
        listing=ProductTemplate(
            title="", bullets=[], description="", aplus=[], backend_keywords="", conditions=[]
        ),
        finance=ProductFinance(currency="USD", unit_profit=profit, roi_pct=roi),
    )


def _product_run(
    *,
    price: float | None,
    currency: str | None = "USD",
    distribution: dict[int, float] | None = None,
    costs: ProductCosts | None = None,
    analysis: ProductAnalysis | None = None,
) -> AmazonResearchRun:
    return _run(
        "product",
        costs=costs,
        snapshot=AmazonResearchSnapshot(
            product_depth=ProductDepth(
                overview=ProductPage(
                    product=_product(),
                    collected_at=_NOW,
                    price_amount=price,
                    currency=currency,
                    rating_distribution=distribution or {},
                ),
                analysis=analysis,
            )
        ),
    )


def _page(
    url: str,
    *indices: int,
    status: StepStatus = "success",
    code: str | None = None,
    key: str | None = None,
    role: str | None = None,
) -> AmazonEvidence:
    return AmazonEvidence(
        key=key or url,
        kind="section" if "/stores/" in url else "board",
        url=url,
        title="Same display title",
        collected_at=_NOW,
        status=status,
        code=code,
        role=role,
        products=[
            AmazonStoreSample(
                asin=_ASINS[index], title="Sample", url=f"{_AMAZON}/dp/{_ASINS[index]}"
            )
            for index in indices
        ],
        observed_count=len(indices),
    )


def test_no_previous_returns_only_the_current_summary():
    current = _run("store")

    payload = compare_research_runs(current, None).model_dump(mode="json")

    assert set(payload) == {"previous", "current", "metrics", "notes"}
    assert payload["previous"] is None
    assert payload["current"]["id"] == str(current.id)
    assert not {"snapshot", "report", "inputs", "error"} & payload["current"].keys()
    assert payload["metrics"] == []
    assert payload["notes"]


@pytest.mark.parametrize("mismatch", ["mode", "source"])
def test_different_mode_or_canonical_source_has_no_metrics(mismatch):
    current = _run("store")
    if mismatch == "mode":
        previous = _run("bestsellers")
        previous.canonical_source = current.canonical_source
    else:
        previous = _run("store")
        previous.canonical_source = _OTHER_STORE

    result = compare_research_runs(current, previous)

    assert result.previous.id == previous.id
    assert result.metrics == []
    assert "不一致" in result.notes[0]


def test_product_keeps_quoted_price_typed_ratings_and_estimates_separate():
    previous = _product_run(
        price=29.99,
        distribution={1: 0, 4: 30},
        costs=ProductCosts(sale_price=35, purchase=5, ad_pct=0),
        analysis=_analysis(-1, None),
    )
    current = _product_run(
        price=27.49,
        currency="usd",
        distribution={1: 2, 5: 70},
        costs=ProductCosts(sale_price=31, purchase=6, ad_pct=0.1),
        analysis=_analysis(2, 10),
    )
    saved = [run.model_dump(mode="json") for run in (previous, current)]

    result = compare_research_runs(current, previous)
    metrics = {row.label: (row.before, row.after, row.unit, row.basis) for row in result.metrics}

    assert {label: values[:3] for label, values in metrics.items()} == {
        "商品页售价": (29.99, 27.49, "USD"),
        "1 星评分占比": (0, 2, "%"),
        "假设售价": (35, 31, "USD"),
        "假设采购成本": (5, 6, "USD"),
        "假设广告费率": (0, 10, "%"),
        "测算单件利润": (-1, 2, "USD"),
    }
    assert len({metrics[label][3] for label in ("商品页售价", "假设售价", "测算单件利润")}) == 3
    assert [run.model_dump(mode="json") for run in (previous, current)] == saved


@pytest.mark.parametrize("currency", [None, "CAD", "XXX"])
def test_product_requires_a_known_equal_currency(currency):
    previous = _product_run(price=10, currency=currency)
    current = _product_run(price=20)

    result = compare_research_runs(current, previous)

    assert result.metrics == []
    assert "币种" in result.notes[0]


def test_product_rejects_a_different_observed_asin_even_with_same_source():
    previous = _product_run(price=10)
    current = _product_run(price=20)
    current.snapshot.product_depth.overview.product.asin = _ASINS[1]

    result = compare_research_runs(current, previous)

    assert result.metrics == []
    assert "ASIN" in result.notes[0]


def test_product_does_not_substitute_manual_price_or_compare_unlike_models():
    previous = _product_run(price=10, analysis=_analysis(-1, 0))
    current = _product_run(price=None, costs=ProductCosts(sale_price=20), analysis=_analysis(1, 10))

    result = compare_research_runs(current, previous)

    assert result.metrics == []
    assert any("售价来源不同" in note for note in result.notes)


def test_legacy_report_only_product_does_not_supply_typed_metrics():
    previous = _run("product")
    previous.report = AmazonResearchReport(
        mode="product",
        source_url=_PRODUCT,
        title="Sample",
        collected_at=_NOW,
        product=_product(),
    )

    result = compare_research_runs(_product_run(price=20), previous)

    assert result.metrics == []
    assert "结构化" in result.notes[0]


def test_category_uses_only_intersecting_pages_and_common_known_prefix():
    child = f"{_AMAZON}/zgbs/fashion/234"
    out_of_scope = f"{_AMAZON}/zgbs/fashion/456"
    previous = _run(
        "bestsellers",
        snapshot=AmazonResearchSnapshot(
            category_scope=CategoryScope(main_limit=4, child_asin_limit=2),
            evidence=[
                _page(_BOARD, 0, 1, 2, 3, status="incomplete", code="sample_limit", role="main"),
                _page(child, 0, 1, role="child"),
                _page(out_of_scope, 4, role="child"),
            ],
        ),
    )
    current = _run(
        "bestsellers",
        snapshot=AmazonResearchSnapshot(
            category_scope=CategoryScope(main_limit=2, child_asin_limit=2),
            evidence=[
                _page(_BOARD_ALIAS, 0, 1, status="incomplete", code="sample_limit", role="main"),
                _page(child, 5, status="failed", role="child"),
                _page(f"{_AMAZON}/zgbs/fashion/888", 3, role="child"),
            ],
        ),
    )

    result = compare_research_runs(current, previous)

    assert [(metric.before, metric.after) for metric in result.metrics] == [(2, 2)]
    assert "共同采集的 1 个榜单页面" in result.metrics[0].basis
    assert any("采样上限" in note for note in result.notes)
    assert any("失败" in note for note in result.notes)


def test_legacy_store_matches_page_identity_not_title_or_evidence_key_and_counts_empty():
    previous = _run(
        "store",
        snapshot=AmazonResearchSnapshot(
            evidence=[
                _page(_STORE, 0, 1, key="old key"),
                _page(_OTHER_STORE, 0, 2, key="unshared"),
            ]
        ),
    )
    current = _run(
        "store",
        snapshot=AmazonResearchSnapshot(
            store_scope=AmazonStoreSampleScope(section_limit=4, asin_limit_per_section=1),
            evidence=[_page(_STORE_ALIAS, status="empty", code="no_products", key="new key")],
        ),
    )

    result = compare_research_runs(current, previous)

    assert [(metric.before, metric.after) for metric in result.metrics] == [(1, 0)]
    assert any("历史记录" in note for note in result.notes)


def test_store_counts_distinct_asins_across_shared_pages_not_row_totals():
    previous = _run(
        "store",
        snapshot=AmazonResearchSnapshot(
            evidence=[
                _page(_STORE, 0, 1),
                _page(_OTHER_STORE, 0, 2),
            ]
        ),
    )
    current = _run(
        "store",
        snapshot=AmazonResearchSnapshot(
            evidence=[
                _page(_STORE, 0, 1),
                _page(_OTHER_STORE, 0, 1),
            ]
        ),
    )

    result = compare_research_runs(current, previous)

    assert [(metric.before, metric.after) for metric in result.metrics] == [(3, 2)]


@pytest.mark.parametrize(
    "status,code",
    [
        ("success", None),
        ("failed", "captcha"),
        ("incomplete", "dynamic"),
        ("incomplete", "sample_limit"),
        ("pending", None),
    ],
)
def test_unavailable_store_page_is_never_counted_as_zero(status, code):
    previous = _run("store", snapshot=AmazonResearchSnapshot(evidence=[_page(_STORE, 0)]))
    current = _run(
        "store",
        snapshot=AmazonResearchSnapshot(
            evidence=[
                _page(_STORE, status=status, code=code),
            ]
        ),
    )

    result = compare_research_runs(current, previous)

    assert result.metrics == []
    assert "不比较商品数量" in result.notes[0]


def test_requested_key_does_not_match_a_redirect_to_a_different_page():
    before = _page(_STORE, 0)
    after = _page(_STORE, 1)
    after.final_url = _OTHER_STORE
    previous = _run("store", snapshot=AmazonResearchSnapshot(evidence=[before]))
    current = _run("store", snapshot=AmazonResearchSnapshot(evidence=[after]))

    assert compare_research_runs(current, previous).metrics == []
