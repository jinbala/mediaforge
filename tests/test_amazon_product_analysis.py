from datetime import UTC, date, datetime

from mediaforge.models.amazon_product import ProductCosts, ProductDepth, ProductSearch
from mediaforge.workers import amazon_product_analysis
from mediaforge.workers.amazon_product_analysis import analyze_product

NOW = datetime(2026, 11, 28, tzinfo=UTC)


def _depth(**overrides):
    page = {
        "product": {
            "asin": "B000000001",
            "title": "Reusable cotton storage bags",
            "url": "https://www.amazon.com/dp/B000000001",
            "brand": "Example",
            "price": "$18.00",
            "rating": "4.2",
            "rating_count": "120",
            "category": "Home & Kitchen",
            "sales_ranks": ["#120 in Home & Kitchen"],
            "highlights": [
                "Cotton fabric",
                "Reusable design",
                "Three sizes",
                "Machine washable",
                "Set of five bags",
            ],
            "image_brief": "",
            "description": "Reusable storage bags for produce.",
            "image_count": 2,
            "has_aplus": True,
        },
        "collected_at": NOW,
        "specs": {"Material": "Cotton muslin", "Count": "5"},
        "important": ["Set includes five bags"],
        "aplus": ["Brand story"],
        "images": [
            {
                "url": "https://images.example/1.jpg",
                "label": "Main image",
                "width": 1000,
                "height": 1000,
                "poster": None,
            },
            {
                "url": "https://images.example/2.jpg",
                "label": None,
                "width": 1000,
                "height": 1000,
                "poster": None,
            },
        ],
        "videos": [
            {
                "url": "https://images.example/demo.mp4",
                "label": "Demo",
                "poster": "https://images.example/poster.jpg",
            }
        ],
        "currency": "USD",
        "price_amount": 18,
        "rating_distribution": {},
        "parent_asin": "B000000000",
        "variant_asins": ["B000000002"],
        "field_evidence": {
            "price": "https://www.amazon.com/dp/B000000001#price",
            "aplus": "#aplus",
            "specs": "#details",
            "images": "#altImages",
            "videos": "video[src]",
        },
        "reviews": [
            {
                "id": "r1",
                "text": "The fabric is too thin.",
                "source": "amazon",
                "stars": 2,
                "url": "https://www.amazon.com/review/r1",
            },
            {
                "id": "r2",
                "text": "Good quality cotton bags.",
                "source": "amazon",
                "stars": 5,
                "url": "https://www.amazon.com/review/r2",
            },
        ],
    }
    data = {
        "overview": page,
        "searches": [],
        "candidates": [],
        "competitors": [],
        "review_pages": [],
        "analysis": None,
    }
    data.update(overrides)
    return ProductDepth.model_validate(data)


def test_missing_depth_keeps_nine_independently_readable_sections():
    report = analyze_product(
        ProductDepth.model_validate(
            {
                "overview": None,
                "searches": [],
                "candidates": [],
                "competitors": [],
                "review_pages": [],
                "analysis": None,
            }
        )
    )
    assert [section.req for section in report.sections] == [f"REQ-{n:03d}" for n in range(21, 30)]
    assert report.sections[0].status == "unknown"
    assert report.finance.unit_profit is None
    assert ["费用日期", None, None, None, None] in report.sections[4].tables[0].rows
    assert report.image_plan and len(report.image_plan) == 10
    assert report.listing.backend_keywords == ""
    assert all(row[1] is None for row in report.sections[5].tables[0].rows)
    assert all(row[1] is None for row in report.sections[8].tables[0].rows)
    assert report.decision.startswith("暂不作上架决定")


def test_report_uses_query_positions_source_aware_reviews_and_original_listing_template():
    depth = _depth(
        searches=[
            {
                "query": "cotton produce bags",
                "url": "https://www.amazon.com/s?k=cotton+produce+bags",
                "collected_at": NOW,
                "rows": [
                    {
                        "asin": "B000000009",
                        "title": "Cotton Produce Bags",
                        "url": "https://www.amazon.com/dp/B000000009",
                        "rank": 1,
                        "brand": "Rival",
                        "price_amount": 14.0,
                        "currency": "USD",
                        "sponsored": True,
                    },
                    {
                        "asin": "B000000001",
                        "title": "Reusable cotton bags",
                        "url": "https://www.amazon.com/dp/B000000001",
                        "rank": 3,
                        "brand": "Example",
                        "price_amount": 18.0,
                        "currency": "USD",
                        "sponsored": False,
                    },
                ],
                "result_min": 100,
                "result_max": 200,
                "result_label": "1-48 of over 100 results",
            }
        ],
        review_pages=[
            {
                "bucket": "one",
                "url": "https://www.amazon.com/reviews/B000000001",
                "collected_at": NOW,
                "rows": [
                    {
                        "id": "r3",
                        "text": "The cotton fabric feels rough.",
                        "source": "amazon",
                        "url": "https://www.amazon.com/review/r3",
                        "stars": 1,
                        "date": "2026-11-01",
                        "variant": "Small",
                        "verified": True,
                        "helpful": 2,
                    }
                ],
                "limited": False,
            }
        ],
    )
    report = analyze_product(depth)
    assert len(report.sections) == 9
    keyword = report.sections[1]
    positions = keyword.tables[1].rows
    assert [row[1] for row in positions] == [1, 3]
    assert positions[0][6] == "赞助" and positions[1][6] == "非赞助"
    review = report.sections[3]
    assert any(row[0] == "材质与触感" and row[1] == 2 for row in review.tables[1].rows)
    assert any(row[-1] == "https://www.amazon.com/review/r3" for row in review.tables[2].rows)
    assert report.sections[4].tables[0].rows
    assert "可核验" in report.listing.title
    assert "Cotton fabric" not in " ".join(report.listing.bullets)
    assert len(report.image_plan) == 10
    assert len({shot.id for shot in report.image_plan}) == 10
    assert report.image_plan[-1].type == "video"
    assert sum(shot.type == "image" for shot in report.image_plan) == 8
    assert sum(shot.type == "video" for shot in report.image_plan) == 2
    assert [section.req for section in report.sections] == [f"REQ-{n:03d}" for n in range(21, 30)]
    assert report.sections[6].tables[1].title == "图片与视频创作策略"
    assert report.sections[7].title == "可编辑 Listing 模板"


def _search(query, *offers):
    return ProductSearch(
        query=query,
        url=f"https://www.amazon.com/s?k={query.replace(' ', '+')}",
        collected_at=NOW,
        rows=[
            {
                "asin": f"B00000000{rank}",
                "title": "Rival Exclusive Headline",
                "url": f"https://www.amazon.com/dp/B00000000{rank}",
                "rank": rank,
                "brand": "Rival" if rank > 1 else "Example",
                "price_amount": price,
                "currency": currency,
            }
            for rank, (price, currency) in enumerate(offers, 1)
        ],
    )


def test_overview_and_keyword_opportunities_keep_sampling_and_field_evidence():
    short = _search(
        "cotton storage bags", (18, "USD"), (14, "usd"), (13, "CAD"), (2, None), (None, "CAD")
    )
    tail = _search("storage bags for produce", (18, "USD"))
    report = analyze_product(_depth(searches=[short, tail, _search("cotton bags with handles")]))
    overview, keywords = report.sections[:2]
    fields = {row[0]: row[1:] for row in overview.tables[0].rows}
    assert fields["价格金额"] == [18, "https://www.amazon.com/dp/B000000001#price"]
    assert fields["A+ 内容摘要（最多 3 段摘录）"] == ["Brand story", "#aplus"]
    assert ["Material", "Cotton muslin", "#details"] in overview.tables[1].rows
    assert [row[-1] for row in overview.tables[2].rows] == [
        "#altImages",
        "#altImages",
        "video[src]",
    ]
    assert overview.tables[2].rows[-1] == [
        "视频",
        "https://images.example/demo.mp4",
        "Demo",
        None,
        None,
        "https://images.example/poster.jpg",
        "video[src]",
    ]
    assert keywords.tables[3].rows == [
        [short.query, "CAD", 13, 13, 1, short.url],
        [short.query, "USD", 14, 18, 2, short.url],
        [tail.query, "USD", 18, 18, 1, tail.url],
    ]
    assert [(row[0], row[1], row[-1]) for row in keywords.tables[4].rows] == [
        ("短词", short.query, short.url),
        ("高意向长尾（规则候选）", tail.query, tail.url),
    ]


def test_eight_axis_scoring_distinguishes_absence_from_missing_and_shows_keyword_coverage():
    title_query = _search("cotton storage bags", (18, "USD"))
    miss_query = _search("cotton bags", (18, "USD"))
    depth = _depth(searches=[title_query, miss_query])
    depth.overview.aplus = []
    depth.overview.product.has_aplus = False
    depth.overview.product.description = None
    depth.overview.product.highlights = []
    listing = analyze_product(depth).sections[5]
    assert {row[0]: row[1] for row in listing.tables[0].rows} == {
        "标题": 2,
        "五点": None,
        "图片": 2,
        "A+": 0,
        "描述": None,
        "定价": 2,
        "评论": 2,
        "SEO": 1,
    }
    assert listing.tables[1].rows == [
        [title_query.query, "出现", None, None, title_query.url],
        [miss_query.query, "未出现", None, None, miss_query.url],
    ]


def test_backend_candidates_use_only_grounded_generic_tokens_and_reuse_report_outputs(monkeypatch):
    oversized = "é" * 126
    query = _search(
        f"cotton MUSLIN-muslin Rival Example free {oversized} exclusive B000000002",
        (18, "USD"),
        (14, "USD"),
    )
    depth = _depth(searches=[query, _search("cotton linen")])
    depth.overview.specs["Other"] = f"Rival Example free {oversized} linen B000000002"
    calls = []
    for name in ("_reviews", "_shots", "_template"):
        original = getattr(amazon_product_analysis, name)

        def tracked(*args, _name=name, _original=original):
            calls.append(_name)
            return _original(*args)

        monkeypatch.setattr(amazon_product_analysis, name, tracked)
    report = analyze_product(depth)
    assert calls == ["_reviews", "_shots", "_template"]
    assert report.listing.backend_keywords == "muslin"
    assert report.sections[7].tables[0].rows[-1][1] == report.listing.backend_keywords
    assert report.sections[7].tables[2].rows == [
        ["muslin", query.query, "规格：Material", "#details", query.url]
    ]
    assert any(str(row[0]).find("249 UTF-8") >= 0 for row in report.sections[7].tables[1].rows)
    assert [row[:4] for row in report.sections[6].tables[1].rows] == [
        [shot.id, shot.type, shot.title, shot.brief] for shot in report.image_plan
    ]


def test_decision_scores_observed_economics_without_treating_unsupported_metrics_as_zero():
    depth = _depth(searches=[_search("cotton bags"), _search("cotton storage bags", (18, "USD"))])
    loss = ProductCosts(
        purchase=20, inbound=1, fulfillment=1, storage=0, ad_pct=0, return_pct=0, referral_pct=0
    )
    report = analyze_product(depth, loss)
    decision = report.sections[8]
    assert report.finance.unit_profit == -4
    assert report.decision == "暂缓推进：当前输入下单件利润不为正"
    assert report.decision in decision.summary[0]
    assert {row[0]: row[1] for row in decision.tables[0].rows} == {
        "目标档案": 2,
        "搜索样本广度": 1,
        "候选价格证据": None,
        "评论样本": 1,
        "单件经济性": 0,
    }
    assert "-4" in decision.tables[0].rows[-1][-1]
    assert not report.finance.scenarios


def test_decision_requires_all_dimensions_before_small_scale_validation():
    depth = _depth(
        searches=[_search("cotton bags", (18, "USD")), _search("storage bags", (18, "USD"))],
        review_pages=[
            {
                "bucket": "two",
                "url": "https://www.amazon.com/reviews/B000000001",
                "collected_at": NOW,
                "rows": [{"id": "r3", "text": "Fabric is thin.", "source": "amazon", "stars": 2}],
            }
        ],
    )
    for index in (2, 3, 4):
        product = depth.overview.product.model_copy(update={"asin": f"B00000000{index}"})
        depth.competitors.append(depth.overview.model_copy(update={"product": product}))
    costs = ProductCosts(
        purchase=5,
        inbound=1,
        fulfillment=2,
        storage=0,
        ad_pct=0,
        return_pct=0,
        referral_pct=0.15,
        fee_source="Seller fee record",
        fee_date=date(2026, 11, 28),
        benchmark_category="Home & Kitchen",
        benchmark_bsr=120,
        benchmark_monthly_units=50,
        benchmark_source="Authorized observation",
        benchmark_date=date(2026, 11, 28),
    )
    report = analyze_product(depth, costs)
    assert report.finance.fee_date == date(2026, 11, 28)
    assert ["费用日期", "2026-11-28", None, None, None] in report.sections[4].tables[0].rows
    assert report.sections[8].status == "complete"
    assert [row[1] for row in report.sections[8].tables[0].rows] == [2] * 5
    assert report.decision == "进入小规模验证：当前证据达标，不等于建议上架"
    depth.competitors.pop()
    assert analyze_product(depth, costs).decision.startswith("暂不作上架决定")
