import asyncio
import json
from contextlib import asynccontextmanager
from datetime import UTC, datetime, timedelta
from uuid import UUID, uuid4

import httpx
import pytest
import respx
from kombu.exceptions import OperationalError
from sqlalchemy import update
from sqlalchemy.ext.asyncio import async_sessionmaker

from mediaforge.db import amazon_research_store as run_store
from mediaforge.db.tables import AmazonResearchRunTable
from mediaforge.gateway.dependencies import get_current_principal
from mediaforge.gateway.main import app
from mediaforge.gateway.middleware.observability import _should_log_body
from mediaforge.gateway.middleware.rate_limit import limiter
from mediaforge.gateway.routers import amazon_research as router
from mediaforge.models.amazon_research import AmazonStoreVariant
from mediaforge.models.amazon_research_run import AmazonResearchSnapshot, AmazonStoreSample
from mediaforge.models.tenant import Tenant, TenantPlan
from mediaforge.models.user import User
from mediaforge.orchestrator import amazon_research as pipeline
from mediaforge.rag import amazon_store_collection
from mediaforge.rag.amazon_research import AMAZON, amazon_public_client
from mediaforge.rag.amazon_store_analysis import (
    archive,
    build_product_matrix,
    section_index,
    summarize_archive,
)
from mediaforge.rag.amazon_store_collection import collect_evidence, section_step

API = "/api/v1/research/amazon/runs"
INITIAL = f"{AMAZON}/stores/Fixture/page/11111111-1111-1111-1111-111111111111"
HOME = f"{AMAZON}/stores/Fixture/page/22222222-2222-2222-2222-222222222222"
NEW = f"{AMAZON}/stores/Fixture/page/33333333-3333-3333-3333-333333333333"
ABOUT = f"{AMAZON}/stores/Fixture/page/44444444-4444-4444-4444-444444444444"
FAILED = f"{AMAZON}/stores/Fixture/page/55555555-5555-5555-5555-555555555555"
IDS = ("B000000001", "B000000002", "B000000003")
TEST_EMAIL = "reader@example.com"


def _nav(*links: tuple[str, str]) -> str:
    return "<nav>" + "".join(f'<a href="{url}">{label}</a>' for label, url in links) + "</nav>"


def _cards(*asins: str) -> str:
    variants = {
        "dimensionKeys": [{"symbol": "color_name"}, {"symbol": "size_name"}],
        "dimensionValues": [["Blue", "Red"], ["S", "M"]],
        "variations": [
            {"asin": asin, "values": specs}
            for asin, specs in zip(IDS[:3], [[0, 0], [0, 1], [1, 0]], strict=True)
        ],
    }
    products = [
        {
            "asin": asin,
            "detailPageLinkURL": f"/dp/{asin}",
            "title": {"displayString": "Cotton Ribbed Top"},
            "twisterVariations": variants,
            "productImages": {
                "altText": "Cotton Ribbed Top",
                "images": [
                    {
                        "variant": "MAIN",
                        "lowRes": {"url": f"https://m.media-amazon.com/images/I/{asin}.jpg"},
                    }
                ],
            },
        }
        for asin in asins
    ]
    return "".join(f'<a href="/dp/{asin}">Cotton Ribbed Top</a>' for asin in asins) + (
        "<script>" + json.dumps({"products": products}) + "</script>"
    )


@pytest.fixture
async def api(monkeypatch, db_engine):
    limiter.reset()
    maker = async_sessionmaker(db_engine, expire_on_commit=False)
    owner = uuid4(), uuid4()
    identity = {"owner": owner}
    enqueued = []

    @asynccontextmanager
    async def session():
        async with maker.begin() as current:
            yield current

    async def principal():
        tenant_id, user_id = identity["owner"]
        return (
            User(tenant_id=tenant_id, user_id=user_id, email=TEST_EMAIL),
            Tenant(tenant_id=tenant_id, name="Fixture workspace", plan=TenantPlan.starter),
        )

    def register(*, args, **_options):
        enqueued.append(args)

    async def translate(items, colors):
        for item in items:
            if item.title == "Cotton Ribbed Top":
                item.title_zh = "棉质罗纹上衣"
        return []

    monkeypatch.setattr(run_store, "get_session", session)
    monkeypatch.setattr(router.process_amazon_research, "apply_async", register)
    monkeypatch.setattr(pipeline, "localize_research_fields", translate)
    app.dependency_overrides[get_current_principal] = principal
    try:
        async with httpx.AsyncClient(
            transport=httpx.ASGITransport(app=app),
            base_url="http://mediaforge.test",
        ) as client:
            yield client, owner, identity, enqueued, maker
    finally:
        app.dependency_overrides.pop(get_current_principal, None)


async def _run_submitted(client, id: str, attempt: int = 1) -> dict:
    await pipeline.process_research(UUID(id), attempt)
    response = await client.get(f"{API}/{id}")
    assert response.status_code == 200, response.text
    return response.json()


@respx.mock
async def test_store_run_discovers_subpages_and_retries_only_selected_page(api):
    client, _, _, queue, _ = api
    home = respx.get(HOME).respond(
        200,
        text="<title>Amazon.com: Fixture</title>" + _cards(*IDS) + _nav(("New Arrivals", NEW)),
    )
    entry = respx.get(INITIAL).respond(
        200,
        text="<title>Fixture category</title>"
        + _cards(IDS[1])
        + _nav(("Home", HOME), ("About Us", ABOUT)),
    )
    respx.get(NEW).respond(200, text=_cards(IDS[1]) + _nav(("Hidden Collection", FAILED)))
    listing_date = respx.get(f"{AMAZON}/dp/{IDS[1]}").respond(
        200,
        text="<table><tr><th>Date First Available</th><td>November 1, 2026</td></tr></table>",
    )

    respx.get(ABOUT).respond(200, text="<main>Fixture manufacturer information.</main>")
    retry = respx.get(FAILED).respond(503)
    payload = {"mode": "store", "source": INITIAL, "submission_id": str(uuid4())}
    response = await client.post(API, json=payload)
    assert response.status_code == 202, response.text
    id = response.json()["id"]
    duplicate = await client.post(API, json={**payload, "submission_id": str(uuid4())})
    assert duplicate.json()["id"] == id
    assert len(queue) == 1
    first = await _run_submitted(client, id)
    assert first["status"] == "partial_fail", first.get("error")
    snap = first["snapshot"]
    assert snap["store_scope"] == {
        "section_limit": 30,
        "asin_limit_per_section": 30,
        "navigation_truncated": False,
    }

    assert snap["store_root"] == HOME
    assert first["canonical_source"].endswith(HOME.rsplit("/", 1)[-1])
    sections = {page["url"]: page for page in snap["evidence"] if page["kind"] == "section"}
    assert {url: (page["status"], page["code"]) for url, page in sections.items()} == {
        INITIAL: ("success", None),
        HOME: ("success", None),
        NEW: ("success", None),
        ABOUT: ("empty", "no_products"),
        FAILED: ("failed", "throttled"),
    }
    summary = snap["product_archive"]
    assert (summary["asin_count"], summary["family_count"], summary["color_group_count"]) == (
        3,
        1,
        2,
    )
    assert summary["unknown_family_count"] == 0
    assert snap["product_matrix"]["asin_count"] == 3
    assert snap["product_matrix"]["new_sections"]
    assert snap["lifecycle"]["change"] is None
    assert snap["lifecycle"]["dates"][0]["date_text"] == "November 1, 2026"
    assert snap["lifecycle"]["dates"][0]["label"] == "first_available"

    assert len(snap["asin_sources"][IDS[1]]) == 3
    assert len(first["report"]["listings"]) == 3
    assert sections[HOME]["products"][0]["image_url"].endswith(".jpg")
    assert sections[HOME]["products"][0]["store_variant"]["size"] == "S"
    assert sections[HOME]["products"][1]["store_variant"]["size"] == "M"
    assert first["report"]["listings"][0]["title_zh"] == "棉质罗纹上衣"
    assert sections[ABOUT]["excerpt"] == "Fixture manufacturer information."

    retry.respond(200, text="<main>No products found</main>")
    failed_key = sections[FAILED]["key"]
    queued = await client.post(f"{API}/{id}/retry?target=failed&item_key={failed_key}", json={})
    assert queued.json()["id"] == id
    assert queued.json()["report"]["listings"] == first["report"]["listings"]
    latest = await _run_submitted(client, id, 2)
    assert latest["status"] == "done", latest.get("error")
    recovered = next(item for item in latest["snapshot"]["evidence"] if item["key"] == failed_key)
    assert recovered["status"] == "empty" and recovered["attempts"] == 2
    assert len(home.calls) == len(entry.calls) == len(listing_date.calls) == 1
    assert len(retry.calls) == 2

    repeated = await client.post(API, json=payload)
    assert repeated.json()["id"] == id
    another = await client.post(API, json={"mode": "store", "source": HOME})
    assert another.json()["id"] != id
    assert len((await client.get(API)).json()) == 2


async def test_concurrent_submit_uses_one_queue_message_and_preserves_account_scope(api):
    client, owner, identity, queued, _ = api
    payloads = (
        {"mode": "store", "source": INITIAL},
        {"mode": "store", "source": INITIAL.replace("/Fixture", "") + "?ref_=known"},
    )
    first, second = await asyncio.gather(*(client.post(API, json=payload) for payload in payloads))
    assert first.status_code == second.status_code == 202
    id = first.json()["id"]
    assert second.json()["id"] == id
    assert len(queued) == 1
    for scope in ((owner[0], uuid4()), (uuid4(), owner[1])):
        identity["owner"] = scope
        assert (await client.get(API)).json() == []
        assert (await client.get(f"{API}/{id}")).status_code == 404
        assert (await client.post(f"{API}/{id}/retry?target=resume", json={})).status_code == 404


async def test_queue_failure_and_worker_lease_recovery(api, monkeypatch):
    client, owner, _, queued, maker = api
    register = router.process_amazon_research.apply_async

    def unavailable(*_args, **_kwargs):
        raise OperationalError("fixture broker down")

    monkeypatch.setattr(router.process_amazon_research, "apply_async", unavailable)
    response = await client.post(API, json={"mode": "store", "source": INITIAL})
    id = response.json()["id"]
    assert response.json()["status"] == "queue_failed"
    monkeypatch.setattr(router.process_amazon_research, "apply_async", register)
    resumed = await client.post(f"{API}/{id}/retry?target=resume", json={})
    assert resumed.json()["status"] == "queued"
    run, token, busy = await run_store.claim_run(UUID(id), 2)
    assert run and token and not busy
    assert await run_store.claim_run(UUID(id), 2) == (None, None, True)
    run.snapshot.evidence = [section_step(INITIAL, "入口")]
    run.snapshot.evidence[0].status = "empty"
    await run_store.checkpoint(run, token, "section")
    async with maker.begin() as session:
        await session.execute(
            update(AmazonResearchRunTable).values(
                lease_until=datetime.now(UTC) - timedelta(seconds=1)
            )
        )
    assert (await run_store.get_run(owner, UUID(id))).status == "interrupted"
    recovered, replacement, busy = await run_store.claim_run(UUID(id), 2)
    assert recovered.snapshot.evidence[0].status == "empty"
    assert replacement != token and not busy
    with pytest.raises(run_store.ResearchRunConflict):
        await run_store.checkpoint(run, token, "analysis")
    assert queued == [(id, 2)]


@respx.mock
async def test_blocked_entry_keeps_a_readable_snapshot(api):
    client, _, _, _, _ = api
    source = respx.get(INITIAL).respond(403)
    result = await client.post(API, json={"mode": "store", "source": INITIAL})
    blocked = await _run_submitted(client, result.json()["id"])
    assert blocked["status"] == "failed"
    assert blocked["error"] == "Amazon 页面暂不可访问（HTTP 403）。"
    assert blocked["snapshot"]["evidence"][0]["code"] == "blocked"
    assert blocked["report"]["listings"] == []
    assert len(source.calls) == 1


@respx.mock
async def test_header_logo_confirms_root_and_other_brand_is_excluded(api):
    client, _, _, _, _ = api
    alias = HOME.replace("/Fixture", "")
    canonical = f'<link rel="canonical" href="{HOME}">'
    logo = f'<a data-testid="header-logo" href="{alias}" aria-label="brand-logo"></a>'
    cross = (
        '<a data-testid="header-logo" href="/stores/page/abcdef12-3456-789a-bcde-f01234567890"></a>'
    )
    respx.get(HOME).respond(
        200,
        text=canonical
        + logo
        + "<title>Amazon.com: Fixture</title>"
        + _nav(("Home", alias), ("Collection", NEW)),
    )
    respx.get(NEW).respond(200, text=cross + _cards(IDS[2]))
    started = await client.post(API, json={"mode": "store", "source": HOME})
    result = await _run_submitted(client, started.json()["id"])
    found = result["snapshot"]["evidence"]
    assert result["snapshot"]["store_root"] == HOME
    assert found[0]["role"] == "home"
    assert (found[1]["status"], found[1]["code"]) == ("failed", "store_mismatch")
    assert result["report"]["listings"] == []


@respx.mock
async def test_store_card_keeps_original_price_and_rating():
    page = respx.get(INITIAL).respond(
        200,
        text=f"""
            <a href="/dp/{IDS[0]}">Fixture</a>
            <script>{{"products":[{{"asin":"{IDS[0]}","detailPageLinkURL":"/dp/{IDS[0]}",
            "marketplaceOfferSummary":{{"newOfferSummary":{{
                "minPrice":{{"displayString":"S$9.49","currencyCode":"SGD"}},
                "maxPrice":{{"displayString":"S$9.49","currencyCode":"SGD"}}}}}},
            "customerReviewsSummary":{{"rating":{{"displayString":"4.4 out of 5 stars"}},
                                       "count":{{"displayString":"2,034"}}}}}}]}}</script>
        """,
    )
    async with amazon_public_client() as client:
        item = await collect_evidence(client, section_step(INITIAL, "Fixture"))
    sample = item.products[0]
    assert (sample.price, sample.price_currency, sample.rating, sample.rating_count) == (
        "S$9.49",
        "SGD",
        "4.4",
        "2,034",
    )
    assert "i18n-prefs=USD" in page.calls[0].request.headers["cookie"]


@respx.mock
@pytest.mark.parametrize(
    ("html", "code", "status"),
    [
        ('<main aria-busy="true"><i class="a-spinner"></i></main>', "dynamic", "incomplete"),
        ("<main>No products found</main>", "no_products", "empty"),
        ("<main><p>Unrecognized markup</p></main>", "markup", "incomplete"),
        ('<form action="/errors/validateCaptcha"></form>', "captcha", "failed"),
    ],
)
async def test_empty_dynamic_or_changed_markup_are_distinct(html, code, status):
    respx.get(INITIAL).respond(200, text=html)
    async with amazon_public_client() as client:
        item = await collect_evidence(client, section_step(INITIAL, "Fixtures"))
    assert (item.status, item.code) == (status, code)


def test_no_request_payload_logging():
    assert not _should_log_body(API)
    assert not _should_log_body(API + "/fixture")


def _variant_sample(asin, group, color, size):
    members = sorted([asin, group])
    return AmazonStoreSample(
        asin=asin,
        url=f"{AMAZON}/dp/{asin}",
        title="Cotton sample",
        family_asin=members[0],
        family_members=members,
        color=color,
        color_group_asin=members[0],
        color_members=members,
        size=size,
        size_group_members=members,
        store_variant=AmazonStoreVariant(
            group_asin=members[0],
            title="Cotton sample",
            color=color,
            size=size,
        ),
    )


def test_archive_uses_partial_variant_tables_and_counts_only_observed_ids():
    first, second, hidden = IDS
    unknown = "B000000004"
    standalone = "B000000005"
    red = "B000000006"
    left, right = section_step(INITIAL, "Fixture A"), section_step(NEW, "Fixture B")
    left.products = [
        _variant_sample(first, hidden, "Blue", "Small"),
        _variant_sample(red, hidden, "Red", "S"),
    ]
    right.products = [
        _variant_sample(first, hidden, "Blue", "S"),
        _variant_sample(second, hidden, "Blue", "Medium"),
        AmazonStoreSample(asin=unknown, url=f"{AMAZON}/dp/{unknown}", title="Cotton sample"),
        AmazonStoreSample(
            asin=standalone,
            url=f"{AMAZON}/dp/{standalone}",
            title="Cotton sample",
            has_variants=False,
        ),
    ]
    snapshot = AmazonResearchSnapshot(evidence=[left, right])
    products = {item.asin: item for item in archive(snapshot)}
    summary = summarize_archive(list(products.values()))
    assert (summary.asin_count, summary.family_count, summary.color_group_count) == (5, 2, 2)
    assert hidden not in summary.identities
    assert summary.unknown_family_count == 1
    assert summary.unknown_color_count == summary.unknown_size_count == 2
    left_blue, right_blue = products[first], products[second]
    assert left_blue.store_variant.group_asin == right_blue.store_variant.group_asin
    assert left_blue.color_group_asin == right_blue.color_group_asin
    assert products[red].color_group_asin != left_blue.color_group_asin
    assert products[red].store_variant.group_asin != left_blue.store_variant.group_asin
    assert summary.identities[standalone].standalone
    assert summary.identities[unknown].family_asin is None
    assert section_index(snapshot)[first] == [left.key, right.key]


def test_conflicting_color_is_excluded_from_size_fold():
    first, second, hidden = IDS
    left, right = section_step(INITIAL, "Fixture A"), section_step(NEW, "Fixture B")
    left.products = [
        _variant_sample(first, hidden, "Blue", "S"),
        _variant_sample(second, hidden, "Blue", "M"),
    ]
    right.products = [_variant_sample(first, hidden, "Red", "Small")]
    products = {item.asin: item for item in archive(AmazonResearchSnapshot(evidence=[left, right]))}
    assert products[first].family_asin == products[second].family_asin
    assert products[first].color_group_asin is None
    assert products[first].store_variant is None
    assert products[first].size == "S"
    assert products[second].color_group_asin is not None


@respx.mock
async def test_archives_saved_before_the_new_summary_open_without_refetching(api):
    client, _, _, _, maker = api
    respx.get(INITIAL).respond(200, text=_cards(IDS[0], IDS[1]))
    started = await client.post(API, json={"mode": "store", "source": INITIAL})
    run_id = started.json()["id"]
    current = await _run_submitted(client, run_id)
    legacy = current["snapshot"]
    legacy.pop("product_archive")
    legacy.pop("store_scope")
    for section in legacy["evidence"]:
        for item in section["products"]:
            for key in ("has_variants", "family_members", "color_members", "size_group_members"):
                item.pop(key)
    async with maker.begin() as session:
        await session.execute(
            update(AmazonResearchRunTable)
            .where(AmazonResearchRunTable.id == UUID(run_id))
            .values(snapshot=legacy)
        )
    reopened = (await client.get(f"{API}/{run_id}")).json()
    assert reopened["snapshot"]["store_scope"] is None
    assert reopened["report"]["collected_at"] == current["report"]["collected_at"]
    assert reopened["report"]["listings"] == current["report"]["listings"]
    assert reopened["snapshot"]["product_archive"]["asin_count"] == 2
    assert reopened["snapshot"]["product_archive"]["family_count"] == 1
    assert len(respx.calls) == 1


def test_merge_does_not_mutate_the_source_variant():
    first, second, hidden = IDS
    original = _variant_sample(second, hidden, "Blue", "M")
    left, right = section_step(INITIAL, "Fixture A"), section_step(NEW, "Fixture B")
    left.products = [
        AmazonStoreSample(asin=second, url=f"{AMAZON}/dp/{second}", title="Cotton sample"),
        _variant_sample(first, hidden, "Blue", "S"),
    ]
    right.products = [original]
    merged = {item.asin: item for item in archive(AmazonResearchSnapshot(evidence=[left, right]))}
    assert merged[second].store_variant.group_asin == first
    assert original.store_variant.group_asin == second


@respx.mock
async def test_run_saves_applied_sampling_limits_and_preserves_page_fields(api, monkeypatch):
    client, _, _, _, _ = api
    monkeypatch.setattr(amazon_store_collection, "SECTION_LIMIT", 2)
    monkeypatch.setattr(pipeline, "SECTION_LIMIT", 2)
    respx.get(INITIAL).respond(
        200,
        text=_cards(IDS[0]) + _nav(("Collection", NEW), ("About us", ABOUT)),
    )
    respx.get(NEW).respond(200, text=_cards(IDS[1]))
    response = await client.post(API, json={"mode": "store", "source": INITIAL})
    result = await _run_submitted(client, response.json()["id"])
    snapshot = result["snapshot"]
    assert snapshot["store_scope"] == {
        "section_limit": 2,
        "asin_limit_per_section": 30,
        "navigation_truncated": True,
    }
    assert len(snapshot["evidence"]) == 2
    for index, section in enumerate(snapshot["evidence"]):
        assert section["collected_at"] and section["url"] and section["method"]
        assert section["observed_count"] == 1
        item = section["products"][0]
        assert item["asin"] == IDS[index]
        assert item["url"].endswith(IDS[index]) and item["image_url"].endswith(IDS[index] + ".jpg")
        assert item["price"] is None and item["rating"] is None and item["rating_count"] is None
        assert item["store_variant"]["size"] == ("S" if index == 0 else "M")


def test_product_matrix_recomputes_bands_overlap_and_ratings_from_public_samples():
    first, second, ranged = IDS
    absent = "B000000004"
    high = "B000000005"
    family = "B000000010"
    primary = section_step(INITIAL, "Fixture")
    new = section_step(NEW, "New Arrivals", "new")
    a = _variant_sample(first, family, "Blue", "Small")
    a.title = "Fixture building set 11379"
    a.product_type, a.price, a.price_currency = "TOY", "$15.00", "USD"
    a.rating, a.rating_count = "4.2", "100"
    b = _variant_sample(second, family, "Blue", "Medium")
    b.product_type, b.price, b.price_currency = "TOY", "S$19.99", "SGD"
    b.rating, b.rating_count = "3.8", "102"
    primary.products = [
        a,
        b,
        AmazonStoreSample(
            asin=ranged,
            title="Fixture calendar 2026",
            url=f"{AMAZON}/dp/{ranged}",
            price="$10 – $20",
            price_currency="USD",
            rating_count="1.2K",
        ),
    ]
    primary.observed_count = len(primary.products)
    new.products = [
        a.model_copy(update={"price": "$49.99"}),
        b.model_copy(update={"price": "$25.00", "price_currency": "USD"}),
        AmazonStoreSample(asin=absent, title="Fixture item", url=f"{AMAZON}/dp/{absent}"),
        AmazonStoreSample(asin=high, title="Fixture item", url=f"{AMAZON}/dp/{high}", price="$60"),
    ]
    new.observed_count = len(new.products)
    snapshot = AmazonResearchSnapshot(evidence=[primary, new])
    matrix = build_product_matrix(snapshot, archive(snapshot))
    assert matrix.asin_count == 5
    assert matrix.missing_price == [absent]
    assert matrix.missing_eligible_price == [ranged]
    assert matrix.prices[first].price_cents == 1500
    assert matrix.prices[first].section == primary.key
    assert matrix.prices[second].price_cents == 2500
    assert matrix.prices[second].section == new.key
    assert [band.asins for band in matrix.price_bands] == [[], [first], [second], [], [high]]
    assert matrix.price_bands[-1].share_pct == 33.3
    assert matrix.sections[0].average_rating == 4
    assert matrix.sections[0].median_rating_total == 101
    assert matrix.sections[0].rating_total_count == 2
    assert matrix.overlaps[0].asins == [first, second]
    assert matrix.product_types["TOY"] == [first, second]
    assert matrix.specification_combinations["Blue / s"] == [first]
    assert matrix.model_candidates == {"11379": [first]}
    assert [(row.asin, row.count) for row in matrix.rating_leaders] == [(second, 102)]
    assert matrix.new_sections == {new.key: [first, second, absent, high]}


def test_empty_product_matrix_preserves_unknown_price_share():
    snapshot = AmazonResearchSnapshot()
    matrix = build_product_matrix(snapshot, [])
    assert not matrix.prices and not matrix.new_sections and not matrix.rating_leaders
    assert all(band.share_pct is None for band in matrix.price_bands)


@respx.mock
async def test_store_lifecycle_compares_same_owner_snapshots_and_keeps_observation_dates(api):
    client, _, identity, _, _ = api
    entry = respx.get(INITIAL)
    entry.respond(200, text=_cards(IDS[0], IDS[1]))
    first = await client.post(API, json={"mode": "store", "source": INITIAL})
    first_id = first.json()["id"]
    prior = await _run_submitted(client, first_id)
    assert prior["snapshot"]["lifecycle"]["change"] is None
    assert prior["snapshot"]["lifecycle"]["sizes"] == {"S": 1, "M": 1}

    entry.respond(200, text=_cards(IDS[1], IDS[2]))
    release = respx.get(f"{AMAZON}/dp/{IDS[2]}").respond(
        200,
        text="<table><tr><th>Release date</th><td>October 10, 2026</td></tr></table>",
    )
    second = await client.post(API, json={"mode": "store", "source": INITIAL})
    current = await _run_submitted(client, second.json()["id"])
    lifecycle = current["snapshot"]["lifecycle"]
    change = lifecycle["change"]
    assert change["previous_run_id"] == first_id
    assert change["newly_seen"] == [IDS[2]]
    assert change["no_longer_seen"] == [IDS[0]]
    assert change["previous_collected_at"] < change["current_collected_at"]
    assert change["series_changes"] == [
        {
            "family_asin": IDS[0],
            "newly_seen": [IDS[2]],
            "no_longer_seen": [IDS[0]],
        }
    ]
    assert lifecycle["dates"][0]["label"] == "release_date"
    assert lifecycle["dates"][0]["url"].endswith(IDS[2])

    identity["owner"] = uuid4(), uuid4()
    other = await client.post(API, json={"mode": "store", "source": INITIAL})
    other = await _run_submitted(client, other.json()["id"])
    assert other["snapshot"]["lifecycle"]["change"] is None
    assert other["snapshot"]["lifecycle"]["dates"] == []
    assert len(release.calls) == 1
