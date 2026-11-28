import json
from datetime import UTC, datetime, timedelta
from uuid import UUID

import pytest

from mediaforge.models.amazon_research import ResearchMode
from mediaforge.models.amazon_research_run import AmazonResearchRun
from mediaforge.workers.amazon_research_export import (
    build_evidence_bundle,
    build_report_index,
    build_report_markdown,
)

ASIN = "B000000001"
PRODUCT = f"https://www.amazon.com/dp/{ASIN}"
BOARD = "https://www.amazon.com/Best-Sellers/zgbs/test"
STORE = "https://www.amazon.com/stores/Test/page/11111111-1111-1111-1111-111111111111"
RUN_ID = UUID("22222222-2222-2222-2222-222222222222")
START = datetime(2026, 11, 27, 9, tzinfo=UTC)
END = START + timedelta(minutes=10)
UPDATED = END + timedelta(days=1)


def _run(
    mode: ResearchMode, source: str, snapshot: dict | None = None, inputs: dict | None = None
) -> AmazonResearchRun:
    return AmazonResearchRun.model_validate(
        {
            "id": RUN_ID,
            "mode": mode,
            "source": source,
            "canonical_source": source,
            "status": "done",
            "stage": "done",
            "created_at": START - timedelta(hours=1),
            "updated_at": UPDATED,
            "attempt": 1,
            "inputs": {"mode": mode, "source": source, **(inputs or {})},
            "snapshot": snapshot or {},
        }
    )


def _board_step(key: str, url: str, *, observed: bool) -> dict:
    return {
        "key": key,
        "kind": "board",
        "url": url,
        "title": key,
        "collected_at": START if observed else UPDATED,
        "status": "success" if observed else "failed",
        "observed_count": 1 if observed else None,
        "products": [{"asin": ASIN, "title": "Observed title", "url": PRODUCT}] if observed else [],
    }


def test_category_export_preserves_structured_rows_and_traceable_board_evidence():
    run = _run(
        "bestsellers",
        BOARD,
        {
            "evidence": [
                _board_step("main", BOARD + "?ref=main", observed=True),
                _board_step("failed-board", BOARD + "?ref=failed", observed=False),
                {
                    "key": "analysis",
                    "kind": "analysis",
                    "url": BOARD,
                    "title": "Analysis",
                    "collected_at": UPDATED,
                    "status": "success",
                },
            ],
            "asin_sources": {ASIN: ["main"]},
            "category_scope": {"main_limit": 20, "discovery_truncated": True},
            "category_analysis": {
                "status": "incomplete",
                "sections": [
                    {
                        "key": key,
                        "title": f"Saved {key}",
                        "reqs": ["REQ-012"],
                        "notes": [f"Saved note {key}"],
                        "tables": [
                            {
                                "title": "Saved table",
                                "columns": ["ASIN", "Observed", "Missing"],
                                "rows": [{"cells": [ASIN, "3.84", None], "asins": [ASIN]}],
                            }
                        ],
                    }
                    for key in ("opportunities", "competition", "keywords", "profit", "summary")
                ],
            },
        },
    )
    original = run.model_dump_json()
    index = build_report_index(run)
    assert index.run_id == RUN_ID
    assert [section.id for section in index.sections] == [
        "opportunities",
        "competition",
        "keywords",
        "profit",
        "summary",
    ]
    assert index.sections[0].sample_count == 1
    assert index.sections[0].status == "partial"
    assert index.collection_range.collected_from == index.collection_range.collected_to == START
    assert index.collection_range.category_scope.main_limit == 20
    assert index.collection_range.unique_asin_count == 1
    assert index.collection_range.observed_source_count == 1
    assert index.site == "US" and index.currency == "USD"
    data = json.loads(build_evidence_bundle(run).model_dump_json())
    assert data["version"] == index.version
    assert data["source_url"] == BOARD
    assert data["sections"][0]["tables"][0]["rows"] == [
        {"cells": [ASIN, "3.84", None], "asins": [ASIN]},
    ]
    by_key = {row["key"]: row for row in data["data_sources"]}
    source_id = by_key["main"]["id"]
    assert data["sections"][0]["source_ids"] == [source_id]
    assert data["samples"][0]["source_id"] == source_id
    assert data["samples"][0]["table"]["rows"][0]["cells"][:2] == [ASIN, "Observed title"]
    assert by_key["failed-board"]["provenance"] == "unknown"
    assert by_key["failed-board"]["sample_count"] is None
    assert by_key["analysis"]["provenance"] == "estimate"
    md = build_report_markdown(run, "opportunities")
    assert "Saved opportunities" in md and "Saved competition" not in md
    assert "Saved table" in md and "未知" in md
    assert f"<{BOARD}?ref=main>" in md
    assert START.isoformat() in md and UPDATED.isoformat() not in md
    assert "failed\\-board" not in md
    assert run.model_dump_json() == original


def test_product_export_uses_saved_status_and_provenance_without_recalculating():
    search_url = "https://www.amazon.com/s?k=fixture"
    run = _run(
        "product",
        PRODUCT,
        {
            "product_depth": {
                "overview": {
                    "product": {"asin": ASIN, "title": "Target", "url": PRODUCT, "image_brief": ""},
                    "collected_at": START,
                    "reviews": [{"id": "manual-1", "source": "manual", "text": "User input"}],
                    "screenshot_data_url": "data:image/png;base64,not-for-export",
                },
                "searches": [
                    {
                        "query": "fixture",
                        "url": search_url,
                        "collected_at": END,
                        "result_min": 1,
                        "result_max": 24,
                        "result_label": "1-24 results",
                    }
                ],
                "analysis": {
                    "sections": [
                        {
                            "req": f"REQ-{req:03}",
                            "title": f"Saved title {req}",
                            "status": "partial" if req == 25 else "complete",
                            "summary": ["Saved analysis"],
                            "tables": [
                                {
                                    "title": "Saved table",
                                    "columns": ["Estimate", "Unknown"],
                                    "rows": [[1234.56, None]],
                                }
                            ],
                            "sources": [{"label": "Search", "url": search_url, "collected_at": END}]
                            if req == 22
                            else [],
                        }
                        for req in range(21, 30)
                    ],
                    "image_plan": [],
                    "listing": {
                        "title": "",
                        "bullets": [],
                        "description": "",
                        "aplus": [],
                        "backend_keywords": "",
                        "conditions": [],
                    },
                    "finance": {},
                    "decision": "Saved decision",
                },
            },
        },
        inputs={"product_costs": {"fulfillment": 5}},
    )
    index = build_report_index(run)
    bundle = build_evidence_bundle(run)
    assert [section.id for section in index.sections] == [f"REQ-{i:03}" for i in range(21, 30)]
    assert index.sections[0].sample_count == 1
    assert index.sections[4].status == "partial"
    assert index.collection_range.collected_from == START
    assert index.collection_range.collected_to == END
    assert bundle.sections[4].tables[0].rows[0].cells == [1234.56, None]
    assert set(bundle.sections[4].provenance) == {"estimate", "manual", "unknown"}
    assert "manual" in bundle.sections[3].provenance
    assert bundle.samples[0].table.rows[0].cells == [1, 24, "1-24 results"]
    search = next(row for row in bundle.data_sources if row.url == search_url)
    assert bundle.sections[1].source_ids == [search.id]
    assert "not-for-export" not in bundle.model_dump_json()
    assert "Saved title 25" in build_report_markdown(run)
    with pytest.raises(KeyError, match="REQ-999"):
        build_report_markdown(run, "REQ-999")


def test_store_exports_measured_prices_but_not_unsupported_revenue_or_growth():
    step = _board_step("first-section", STORE, observed=True)
    step["kind"] = "section"
    run = _run(
        "store",
        STORE,
        {
            "evidence": [step],
            "store_scope": {"section_limit": 3, "asin_limit_per_section": 15},
            "product_matrix": {
                "asin_count": 1,
                "prices": {ASIN: {"section": "first-section", "price_cents": 1999}},
                "price_bands": [
                    {"min_cents": 1000, "max_cents": 2000, "asins": [ASIN], "share_pct": 100}
                ],
                "missing_price": [],
                "missing_eligible_price": [],
                "sections": [],
                "overlaps": [],
                "product_types": {},
                "specification_combinations": {},
                "model_candidates": {},
                "rating_leaders": [],
                "new_sections": {},
            },
            "lifecycle": {
                "colors": {},
                "sizes": {},
                "missing_color": 1,
                "missing_size": 1,
                "dates": [
                    {
                        "asin": ASIN,
                        "url": PRODUCT,
                        "collected_at": END,
                        "label": "first_available",
                        "date_text": "2026-10-02",
                    }
                ],
            },
        },
    )
    index = build_report_index(run)
    assert [section.id for section in index.sections] == [
        "product_matrix",
        "competition",
        "revenue",
        "growth",
        "summary",
    ]
    assert [section.status for section in index.sections] == [
        "complete",
        "unknown",
        "unknown",
        "partial",
        "partial",
    ]
    assert index.collection_range.store_scope.asin_limit_per_section == 15
    assert index.collection_range.collected_to == END
    sections = {section.id: section for section in build_evidence_bundle(run).sections}
    assert sections["revenue"].provenance == ["unknown"]
    assert sections["revenue"].tables == []
    assert sections["revenue"].sample_count is None
    assert sections["growth"].provenance == ["observed", "unknown"]
    assert sections["growth"].tables[0].rows[0].cells[2] == "2026-10-02"
    md = build_report_markdown(run)
    assert "19\\.99–19\\.99" in md
    assert "不能换算为营收" in md
    assert "销量增长和上新节奏未获核实" in md


def test_store_with_only_saved_cards_does_not_require_a_precomputed_matrix():
    step = _board_step("first-section", STORE, observed=True)
    step["kind"] = "section"
    run = _run("store", STORE, {"evidence": [step]})
    product = build_evidence_bundle(run).sections[0]
    assert product.status == "partial" and product.sample_count == 1
    assert product.tables[0].rows[0].cells == [
        "first-section",
        ASIN,
        "Observed title",
        None,
        None,
    ]
    md = build_report_markdown(run, "product_matrix")
    assert "Observed title" in md and "不补算价格带" in md


@pytest.mark.parametrize("mode,size", [("store", 5), ("bestsellers", 5), ("product", 9)])
def test_pending_runs_keep_stable_sections_but_do_not_invent_samples(mode, size):
    run = _run(mode, PRODUCT).model_copy(update={"status": "queued"})
    index = build_report_index(run)
    assert len(index.sections) == size
    assert index.collection_range.collected_from is None
    assert index.collection_range.unique_asin_count is None
    assert all(section.status == "unknown" for section in index.sections)
    assert all(section.sample_count is None for section in index.sections)
    assert "实采时间范围：未知 至 未知" in build_report_markdown(run)


def test_untrusted_markdown_is_literal_and_exports_are_bounded():
    malicious = "[click](javascript:alert(1)) | <script>\n# injected"
    run = _run(
        "bestsellers",
        BOARD,
        {
            "evidence": [_board_step("malicious", "javascript:alert(1)", observed=True)],
            "category_analysis": {
                "status": "complete",
                "sections": [
                    {
                        "key": "opportunities",
                        "title": malicious,
                        "reqs": [],
                        "notes": [],
                        "tables": [
                            {
                                "title": malicious,
                                "columns": ["A | B", "C"],
                                "rows": [{"cells": [malicious, "宽" * 5000]} for _ in range(500)],
                            }
                        ],
                    },
                    {
                        "key": "summary",
                        "title": "A different section",
                        "reqs": [],
                        "notes": [],
                        "tables": [
                            {
                                "title": "Important",
                                "columns": ["A"],
                                "rows": [{"cells": ["Late content survives"]}],
                            }
                        ],
                    },
                ],
            },
        },
    )
    md = build_report_markdown(run)
    data = build_evidence_bundle(run)
    assert "<script>" not in md and "\n# injected" not in md
    assert "[click](javascript:" not in md
    assert "| A \\| B | C |" in md
    assert not any(source.url == "javascript:alert(1)" for source in data.data_sources)
    assert data.truncated and "已截断" in md
    assert len(md.encode("utf-8")) < 1_048_576
    assert len(data.model_dump_json().encode("utf-8")) < 1_048_576
    assert "Late content survives" in build_report_markdown(run, "summary")
