from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

from httpx import AsyncClient
from sqlalchemy import update

from mediaforge.db import amazon_research_store as run_store
from mediaforge.db.tables import AmazonResearchRunTable
from mediaforge.gateway.dependencies import get_current_principal
from mediaforge.gateway.main import app
from mediaforge.gateway.routers import amazon_research as router
from mediaforge.models.amazon_research import AmazonResearchReport, ResearchMode
from mediaforge.models.amazon_research_run import (
    AmazonEvidence,
    AmazonMarketListing,
    AmazonResearchSnapshot,
    AmazonStoreSample,
    RunStatus,
)
from mediaforge.rag.amazon_research import AMAZON
from tests import test_amazon_store_depth as store_tests
from tests.test_amazon_store_depth import API, HOME, IDS, INITIAL, NEW

api = store_tests.api

COLLECTED_AT = datetime(2026, 11, 28, tzinfo=UTC)
BOARD = f"{AMAZON}/gp/bestsellers/fashion"


def _sampled_snapshot(url: str, *asins: str, store: bool = True) -> AmazonResearchSnapshot:
    page = AmazonEvidence(
        key="fixture-page",
        kind="section" if store else "board",
        url=url,
        title="Fixture page",
        status="success",
        collected_at=COLLECTED_AT,
        attempts=1,
        observed_count=len(asins),
        products=[
            AmazonStoreSample(asin=asin, title="Fixture sample", url=f"{AMAZON}/dp/{asin}")
            for asin in asins
        ],
    )
    return AmazonResearchSnapshot(
        store_root=url if store else None,
        evidence=[page],
        asin_sources={asin: [page.key] for asin in asins},
        translation_status="success",
    )


async def _save_run(
    client: AsyncClient,
    mode: ResearchMode,
    source: str,
    snapshot: AmazonResearchSnapshot,
    *,
    status: RunStatus = "done",
) -> str:
    submitted = await client.post(API, json={"mode": mode, "source": source})
    assert submitted.status_code == 202, submitted.text
    run_id = submitted.json()["id"]
    run, token, busy = await run_store.claim_run(UUID(run_id), 1)
    assert run is not None and token is not None and not busy
    run.snapshot = snapshot
    run.report = AmazonResearchReport(
        mode=mode,
        source_url=run.source,
        title="Fixture report",
        collected_at=COLLECTED_AT,
        listings=[sample for page in snapshot.evidence for sample in page.products],
    )
    await run_store.checkpoint(run, token, "report", status=status)
    return run_id


async def test_research_reports_and_comparison_require_the_authenticated_owner(api, monkeypatch):
    client, owner, identity, _, _ = api
    asin = IDS[0]
    run_id = await _save_run(client, "store", INITIAL, _sampled_snapshot(INITIAL, asin))
    path = f"{API}/{run_id}"

    index = await client.get(f"{path}/reports")
    assert index.status_code == 200, index.text
    assert index.json()["run_id"] == run_id
    sections = {section["id"]: section for section in index.json()["sections"]}
    matrix = sections["product_matrix"]
    assert matrix["sample_count"] == 1

    detail = await client.get(f"{path}/reports/product_matrix")
    assert detail.status_code == 200, detail.text
    markdown = detail.json()["markdown"]
    assert f"## {matrix['title']}\n" in markdown and asin in markdown
    assert f"## {sections['growth']['title']}\n" not in markdown
    full = await client.get(f"{path}/reports/full")
    assert full.status_code == 200, full.text
    assert f"## {sections['growth']['title']}\n" in full.json()["markdown"]
    assert (await client.get(f"{path}/reports/not-a-section")).status_code == 404

    evidence = await client.get(f"{path}/evidence")
    assert evidence.status_code == 200, evidence.text
    bundle = evidence.json()
    assert bundle["run_id"] == run_id
    (source,) = bundle["data_sources"]
    assert (source["url"], source["provenance"], source["sample_count"]) == (INITIAL, "observed", 1)
    (sample_group,) = bundle["samples"]
    assert sample_group["source_id"] == source["id"]
    assert sample_group["table"]["rows"][0]["cells"][0] == asin

    comparison = await client.get(f"{path}/comparison")
    assert comparison.status_code == 200, comparison.text
    assert comparison.json()["current"]["id"] == run_id
    assert comparison.json()["previous"] is None

    endpoints = (
        "",
        "/reports",
        "/reports/product_matrix",
        "/reports/full",
        "/evidence",
        "/comparison",
    )
    for other_owner in ((owner[0], uuid4()), (uuid4(), owner[1])):
        identity["owner"] = other_owner
        for suffix in endpoints:
            denied = await client.get(path + suffix)
            assert denied.status_code == 404, (suffix, denied.text)

    with monkeypatch.context() as patch:
        patch.delitem(app.dependency_overrides, get_current_principal)
        for suffix in endpoints:
            denied = await client.get(path + suffix)
            assert denied.status_code == 401, (suffix, denied.text)


async def test_comparison_uses_an_older_owned_run_with_the_same_canonical_source(api):
    client, owner, identity, _, maker = api
    oldest = await _save_run(client, "store", HOME, _sampled_snapshot(HOME, *IDS))
    prior = await _save_run(client, "store", INITIAL, _sampled_snapshot(HOME, IDS[0]))
    other_source = await _save_run(client, "store", NEW, _sampled_snapshot(NEW, IDS[0]))
    identity["owner"] = owner[0], uuid4()
    other_owner = await _save_run(client, "store", HOME, _sampled_snapshot(HOME, *IDS))
    identity["owner"] = owner
    current = await _save_run(client, "store", HOME, _sampled_snapshot(HOME, *IDS[:2]))
    newer = await _save_run(client, "store", HOME, _sampled_snapshot(HOME, *IDS))

    order = (oldest, prior, other_source, other_owner, current, newer)
    async with maker.begin() as session:
        for minute, run_id in enumerate(order):
            await session.execute(
                update(AmazonResearchRunTable)
                .where(AmazonResearchRunTable.id == UUID(run_id))
                .values(created_at=COLLECTED_AT + timedelta(minutes=minute))
            )

    response = await client.get(f"{API}/{current}/comparison")
    assert response.status_code == 200, response.text
    result = response.json()
    before, after = result["previous"], result["current"]
    assert (before["id"], after["id"]) == (prior, current)
    assert before["source"] != after["source"]
    assert before["canonical_source"] == after["canonical_source"]
    assert [(row["before"], row["after"]) for row in result["metrics"]] == [(1, 2)]


async def test_category_context_requires_an_owned_source_and_a_matching_sampled_asin(api):
    client, owner, identity, queue, _ = api
    sampled, unsampled, _ = IDS
    source_id = await _save_run(
        client, "bestsellers", BOARD, _sampled_snapshot(BOARD, sampled, store=False)
    )
    context = {"run_id": source_id, "asin": sampled, "direction": "Inspect sampled product"}
    product = {"mode": "product", "source": sampled, "context": context}

    for invalid in (
        {**product, "source": unsampled, "context": {**context, "asin": unsampled}},
        {**product, "source": unsampled},
    ):
        response = await client.post(API, json=invalid)
        assert response.status_code == 422, response.text

    identity["owner"] = owner[0], uuid4()
    response = await client.post(API, json=product)
    assert response.status_code == 404, response.text
    assert queue == [(source_id, 1)]

    identity["owner"] = owner
    accepted = await client.post(API, json=product)
    assert accepted.status_code == 202, accepted.text
    child = accepted.json()
    assert child["source"] == f"{AMAZON}/dp/{sampled}"
    assert child["inputs"]["context"] == context
    assert queue == [(source_id, 1), (child["id"], 1)]


async def test_translation_retry_resets_only_translation_evidence(api):
    client, _, _, queue, _ = api
    snapshot = _sampled_snapshot(INITIAL, IDS[0])
    snapshot.translation_status = "incomplete"
    snapshot.translation_missing = ["Fixture sample"]
    snapshot.evidence.extend(
        [
            AmazonEvidence(
                key="failed-page",
                kind="section",
                url=NEW,
                title="Unavailable page",
                status="failed",
                code="throttled",
                attempts=1,
            ),
            AmazonEvidence(
                key="store-translation",
                kind="analysis",
                url=INITIAL,
                title="Translation",
                status="incomplete",
                reason="Fixture sample not translated",
                attempts=1,
            ),
        ]
    )
    run_id = await _save_run(client, "store", INITIAL, snapshot, status="partial_fail")
    path = f"{API}/{run_id}"
    saved = await client.get(path)
    assert saved.status_code == 200, saved.text
    original = saved.json()

    response = await client.post(f"{path}/retry", params={"target": "translation"})
    assert response.status_code == 202, response.text
    retry = response.json()
    assert (retry["id"], retry["attempt"]) == (run_id, 2)
    assert (retry["status"], retry["stage"]) == ("queued", "retry-translation")
    assert retry["report"] == original["report"]
    expected = original["snapshot"]
    translation_step = next(
        item for item in expected["evidence"] if item["key"] == "store-translation"
    )
    assert expected["translation_status"] == translation_step["status"] == "incomplete"
    expected["translation_status"] = translation_step["status"] = "pending"
    assert retry["snapshot"] == expected
    assert queue == [(run_id, 1), (run_id, 2)]
    assert (await client.get(path)).json() == retry


async def test_runs_reject_ca_site_and_canadian_source(api):
    client, _, _, queue, _ = api
    product = {"mode": "product", "source": f"{AMAZON}/dp/{IDS[0]}"}

    invalid_site = await client.post(API, json={**product, "site": "CA"})
    assert invalid_site.status_code == 422, invalid_site.text
    assert any(error["loc"] == ["body", "site"] for error in invalid_site.json()["detail"])
    invalid_source = await client.post(
        API, json={**product, "source": f"https://www.amazon.ca/dp/{IDS[0]}"}
    )
    assert invalid_source.status_code == 422, invalid_source.text
    assert not queue
    assert (await client.get(API)).json() == []


async def test_category_store_resolution_reads_only_the_sampled_asin(api, monkeypatch):
    client, _, _, _, _ = api
    source_id = await _save_run(
        client, "bestsellers", BOARD, _sampled_snapshot(BOARD, IDS[0], store=False)
    )
    seen = []

    async def listing(_client, asin):
        seen.append(asin)
        return AmazonMarketListing(
            asin=asin, url=f"{AMAZON}/dp/{asin}", collected_at=COLLECTED_AT, store_url=INITIAL,
        )

    monkeypatch.setattr(router, "collect_market_listing", listing)
    path = f"{API}/{source_id}/samples"
    assert (await client.get(f"{path}/{IDS[1]}/store")).status_code == 404
    result = await client.get(f"{path}/{IDS[0]}/store")
    assert result.json() == {"url": INITIAL}
    assert seen == [IDS[0]]
    context = {"run_id": source_id, "asin": IDS[0], "direction": "服装细分机会"}
    submitted = await client.post(
        API, json={"mode": "store", "source": INITIAL, "context": context}
    )
    assert submitted.status_code == 202
    assert submitted.json()["inputs"]["context"] == context


async def test_manual_notes_remain_distinct_from_public_evidence(api):
    client, _, _, _, _ = api
    notes = "授权报价待验证 <script>not HTML</script>"
    response = await client.post(API, json={
        "mode": "store", "source": INITIAL, "supplemental_notes": f"  {notes}  ",
    })
    assert response.status_code == 202
    path = f"{API}/{response.json()['id']}"
    source = (await client.get(f"{path}/evidence")).json()
    assert source["supplemental_notes"] == notes
    assert source["collection_range"]["unique_asin_count"] is None
    markdown = (await client.get(f"{path}/reports/full")).json()["markdown"]
    assert "用户补充（manual" in markdown
    assert "<script>" not in markdown
