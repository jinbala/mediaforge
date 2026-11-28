from dataclasses import asdict
from datetime import UTC, datetime, timedelta
from uuid import UUID

from mediaforge.models.amazon_research_run import (
    AmazonBenchmarkPair,
    AmazonEvidence,
    AmazonHistoricalPrice,
    AmazonLifecycleSample,
    AmazonMarketListing,
    AmazonResearchRun,
    AmazonResearchRunRequest,
    AmazonResearchSnapshot,
    AmazonRevenueSample,
    AmazonRevenueTotals,
    AmazonStoreChange,
    AmazonStoreCompetition,
    AmazonStoreLifecycle,
    AmazonStorePerformance,
    AmazonStoreRevenue,
    AmazonStoreSample,
    StepStatus,
)
from mediaforge.workers.amazon_research_export import (
    build_evidence_bundle,
    build_report_index,
    build_report_markdown,
)
from mediaforge.workers.amazon_store_recommendations import build_store_recommendations

ASIN = "B000000001"
OTHER = "B000000002"
RIVAL = "B000000003"
OTHER_RIVAL = "B000000004"
NOW = datetime(2026, 11, 28, tzinfo=UTC)
BEFORE = NOW - timedelta(days=32)
STORE = "https://www.amazon.com/stores/Example/page/11111111-1111-1111-1111-111111111111"


def _run(snapshot: AmazonResearchSnapshot) -> AmazonResearchRun:
    return AmazonResearchRun(
        id=UUID(int=1),
        mode="store",
        source=STORE,
        canonical_source=STORE,
        status="partial_fail",
        stage="complete",
        created_at=NOW,
        updated_at=NOW,
        attempt=1,
        inputs=AmazonResearchRunRequest(source=STORE),
        snapshot=snapshot,
    )


def _detail(asin: str, **fields) -> AmazonMarketListing:
    return AmazonMarketListing(
        asin=asin,
        url=f"https://www.amazon.com/dp/{asin}",
        collected_at=NOW,
        **fields,
    )


def _section(
    key: str,
    *asins: str,
    status: StepStatus = "success",
    code: str | None = None,
) -> AmazonEvidence:
    return AmazonEvidence(
        key=key,
        kind="section",
        url=STORE,
        title=key,
        status=status,
        code=code,
        collected_at=NOW,
        products=[
            AmazonStoreSample(
                asin=asin,
                title="Cotton Top",
                url=f"https://www.amazon.com/dp/{asin}",
            )
            for asin in asins
        ],
    )


def _performance(**fields) -> AmazonStorePerformance:
    return AmazonStorePerformance(
        **{
            "new": [],
            "mature": [],
            "low_rating": [],
            "unknown_lifecycle": 0,
            "bands": [],
            "observation_days": 90,
            "mature_rating_total": 100,
            "low_rating_total": 10,
            "low_rating_threshold": 4.0,
            **fields,
        }
    )


def _review(asin: str, section: str, rating: float, count: int) -> AmazonLifecycleSample:
    return AmazonLifecycleSample(
        asin=asin,
        section=section,
        basis="low_rating",
        rating=rating,
        rating_total=count,
    )


def _pair(**fields) -> AmazonBenchmarkPair:
    return AmazonBenchmarkPair(
        **{
            "target_asin": ASIN,
            "competitor_asin": RIVAL,
            "shared_terms": ["cotton", "ribbed"],
            "shared_category": "Tops",
            "audience": "女性款标记",
            "price_gap_cents": 500,
            "swot": {},
            **fields,
        }
    )


def _revenue(asin: str, listing: AmazonMarketListing) -> AmazonStoreRevenue:
    return AmazonStoreRevenue(
        family_count=1,
        unknown_family_count=0,
        representative_count=1,
        samples=[
            AmazonRevenueSample(
                family_asin=asin,
                asin=asin,
                members=[asin],
                sections=["section-main"],
                segment="representative",
                listing=listing,
            )
        ],
    )


def test_supported_channels_are_ordered_by_horizon_and_do_not_mutate_the_snapshot():
    run = _run(
        AmazonResearchSnapshot(
            evidence=[
                _section("section-main", ASIN),
                _section("section-more", ASIN, status="incomplete", code="sample_limit"),
                _section("section-alias", ASIN, status="empty", code="duplicate"),
            ],
            asin_sources={ASIN: ["stale", "section-alias"]},
            lifecycle=AmazonStoreLifecycle(
                colors={},
                sizes={},
                missing_color=0,
                missing_size=0,
                performance=_performance(low_rating=[_review(ASIN, "section-main", 3.6, 37)]),
            ),
            competition=AmazonStoreCompetition(
                targets=[ASIN],
                pairs=[_pair()],
                listings={
                    ASIN: _detail(ASIN, has_aplus=False),
                    RIVAL: _detail(RIVAL, has_aplus=True),
                },
            ),
            revenue=_revenue(
                ASIN,
                _detail(
                    ASIN,
                    monthly_bought=100,
                    monthly_bought_label="100+ bought in past month",
                ),
            ),
        )
    )
    before = run.model_dump_json()

    rows = build_store_recommendations(run)

    assert run.model_dump_json() == before
    assert [(row.channel, row.horizon) for row in rows] == [
        ("评论", 30),
        ("Listing", 30),
        ("库存", 30),
        ("广告", 60),
        ("促销", 90),
    ]
    reviews, listing, inventory, ads, promotion = rows
    assert all(row.target_asin == ASIN for row in rows)
    assert all(row.action and row.finding and row.verification for row in rows)
    assert reviews.source_keys == ["section-main"]
    assert all(row.source_keys == ["section-main", "section-more"] for row in rows[1:])
    assert reviews.audience is reviews.competitor_asin is None
    assert "3.6/5" in reviews.finding and "不诱导" in reviews.verification
    assert "A+" in listing.finding
    assert "100+" in inventory.finding and "不是实销量或库存数据" in inventory.finding
    assert "cotton、ribbed" in ads.finding and "不代表搜索量" in ads.finding
    assert "$5.00" in promotion.finding
    assert all(
        row.audience == "女性款标记" and row.competitor_asin == RIVAL
        for row in (listing, ads, promotion)
    )
    assert set(asdict(rows[0])) == {
        "channel",
        "target_asin",
        "competitor_asin",
        "audience",
        "finding",
        "action",
        "verification",
        "horizon",
        "source_keys",
    }


def test_workbench_exports_five_reports_from_the_same_snapshot():
    revenue = _revenue(
        ASIN,
        _detail(
            ASIN,
            monthly_bought=100,
            monthly_bought_label="100+ bought in past month",
        ),
    )
    totals = AmazonRevenueTotals(units=(100, 125, 150), cents=(150000, 187500, 225000))
    revenue.samples[0].monthly = totals
    revenue.samples[0].price_cents = 1500
    revenue.monthly = {"sample": totals}
    revenue.section_monthly = {"section-main": totals}
    run = _run(
        AmazonResearchSnapshot(
            evidence=[_section("section-main", ASIN)],
            lifecycle=AmazonStoreLifecycle(
                colors={},
                sizes={},
                missing_color=0,
                missing_size=0,
                performance=_performance(low_rating=[_review(ASIN, "section-main", 3.6, 37)]),
            ),
            competition=AmazonStoreCompetition(
                targets=[ASIN],
                pairs=[_pair()],
                listings={
                    ASIN: _detail(ASIN, has_aplus=False),
                    RIVAL: _detail(RIVAL, has_aplus=True),
                },
            ),
            revenue=revenue,
        )
    )
    index = build_report_index(run)
    exports = {row.id: build_report_markdown(run, row.id) for row in index.sections}
    assert list(exports) == ["product_matrix", "competition", "revenue", "growth", "summary"]
    for row in index.sections:
        assert f"## {row.title}\n" in exports[row.id]
        assert str(run.id) in exports[row.id].replace("\\-", "-")
    assert "100\\+ bought in past month" in exports["revenue"]
    assert "18000\\.00" in exports["revenue"]
    assert "可估样本月营收" in exports["summary"]
    assert "30／60／90 天运营验证计划" in exports["growth"]
    assert "运营验证计划" not in exports["revenue"]
    bundle = build_evidence_bundle(run)
    growth = next(row for row in bundle.sections if row.id == "growth")
    plan = next(table for table in growth.tables if table.title == "30／60／90 天运营验证计划")
    assert [row.cells[0] for row in plan.rows] == ["评论", "Listing", "库存", "广告", "促销"]
    ads = plan.rows[3]
    assert ads.asins == [ASIN, RIVAL]
    source_ids = set(str(ads.cells[-1]).split(", "))
    assert source_ids <= set(growth.source_ids)
    assert _detail(RIVAL).url in {row.url for row in bundle.data_sources if row.id in source_ids}


def test_saved_price_history_precedes_a_competitor_gap_and_uses_its_exact_section():
    run = _run(
        AmazonResearchSnapshot(
            evidence=[_section("section-main", ASIN), _section("section-history", ASIN)],
            lifecycle=AmazonStoreLifecycle(
                colors={},
                sizes={},
                missing_color=0,
                missing_size=0,
                performance=_performance(
                    price_changes=[
                        AmazonHistoricalPrice(
                            asin=ASIN,
                            section="section-history",
                            previous_cents=2599,
                            current_cents=2299,
                            previous_collected_at=BEFORE,
                            current_collected_at=NOW,
                        ),
                        AmazonHistoricalPrice(
                            asin=ASIN,
                            section="section-missing",
                            previous_cents=9000,
                            current_cents=2000,
                            previous_collected_at=BEFORE,
                            current_collected_at=NOW,
                        ),
                    ]
                ),
            ),
            competition=AmazonStoreCompetition(targets=[ASIN], pairs=[_pair()]),
        )
    )

    ads, promotion = build_store_recommendations(run)

    assert ads.competitor_asin == RIVAL
    assert promotion.channel == "促销" and promotion.competitor_asin is None
    assert promotion.source_keys == ["section-history"]
    assert "$25.99 变为 $22.99" in promotion.finding
    assert "不能确认变价原因或促销效果" in promotion.finding


def test_newly_seen_inventory_is_a_verification_not_a_stock_or_launch_claim():
    run = _run(
        AmazonResearchSnapshot(
            evidence=[_section("section-overlap", ASIN), _section("section-new", ASIN)],
            lifecycle=AmazonStoreLifecycle(
                colors={},
                sizes={},
                missing_color=0,
                missing_size=0,
                change=AmazonStoreChange(
                    previous_run_id=UUID(int=2),
                    previous_collected_at=BEFORE,
                    current_collected_at=NOW,
                    section_keys=["section-overlap"],
                    newly_seen=[ASIN],
                    no_longer_seen=[OTHER],
                    series_changes=[],
                ),
                performance=_performance(
                    new=[
                        AmazonLifecycleSample(
                            asin=ASIN,
                            section="section-new",
                            basis="new_section",
                        )
                    ]
                ),
            ),
        )
    )

    listing, inventory = build_store_recommendations(run)

    assert (listing.channel, listing.source_keys) == ("Listing", ["section-new"])
    assert "尚无证据表明详情页存在缺项" in listing.finding
    assert (inventory.channel, inventory.source_keys) == ("库存", ["section-overlap"])
    assert "不等于新品、到货或库存增加" in inventory.finding
    assert "卖家后台" in inventory.verification


def test_ignores_stale_unrelated_or_unusable_evidence_even_when_a_section_exists():
    snapshot = AmazonResearchSnapshot(
        evidence=[
            _section("section-main", ASIN),
            _section("failed", OTHER, status="failed"),
            _section("duplicate", OTHER, code="duplicate"),
            AmazonEvidence(
                key="search",
                kind="search",
                url=STORE,
                title="Search",
                status="success",
                products=_section("unused", OTHER).products,
            ),
        ],
        asin_sources={OTHER: ["section-main", "failed", "search", "duplicate"]},
        lifecycle=AmazonStoreLifecycle(
            colors={},
            sizes={},
            missing_color=0,
            missing_size=0,
            performance=_performance(
                low_rating=[
                    _review(OTHER, "section-main", 1.2, 100),
                    _review(ASIN, "section-main", 2.5, 3),
                ]
            ),
        ),
        competition=AmazonStoreCompetition(
            targets=[OTHER, ASIN],
            pairs=[_pair(target_asin=OTHER), _pair(competitor_asin=ASIN)],
        ),
        revenue=_revenue(ASIN, _detail(ASIN, monthly_bought=100)),
    )
    assert build_store_recommendations(_run(snapshot)) == []
    snapshot.evidence.clear()
    assert build_store_recommendations(_run(snapshot)) == []


def test_channel_ties_are_stable_source_keys_are_bounded_and_non_store_runs_are_ignored():
    snapshot = AmazonResearchSnapshot(
        evidence=[_section(f"section-{idx}", ASIN, OTHER) for idx in range(5, 0, -1)],
        lifecycle=AmazonStoreLifecycle(
            colors={},
            sizes={},
            missing_color=0,
            missing_size=0,
            performance=_performance(
                low_rating=[
                    _review(OTHER, "section-1", 3.2, 24),
                    _review(ASIN, "section-1", 3.2, 24),
                ]
            ),
        ),
        competition=AmazonStoreCompetition(
            targets=[ASIN],
            pairs=[
                _pair(competitor_asin=OTHER_RIVAL, shared_category=None),
                _pair(price_gap_cents=0),
            ],
        ),
    )
    run = _run(snapshot)
    reviews, ads, promotion = first = build_store_recommendations(run)
    assert reviews.target_asin == ASIN
    assert ads.competitor_asin == RIVAL
    assert promotion.competitor_asin == OTHER_RIVAL  # Only the positive price gap is actionable.
    assert ads.source_keys == ["section-1", "section-2", "section-3"]

    run.snapshot.evidence.reverse()
    run.snapshot.lifecycle.performance.low_rating.reverse()
    run.snapshot.competition.pairs.reverse()
    assert build_store_recommendations(run) == first
    assert build_store_recommendations(run.model_copy(update={"mode": "product"})) == []
