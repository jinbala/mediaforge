import asyncio
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID

import httpx
from loguru import logger

from mediaforge.db import amazon_research_store as store
from mediaforge.models.amazon_research_run import (
    AmazonEvidence,
    AmazonResearchRun,
    AmazonStoreSample,
    AmazonStoreSampleScope,
)
from mediaforge.orchestrator.amazon_category import collect_category
from mediaforge.rag.amazon_research import SUGGESTIONS, _fetch_suggestions, amazon_public_client
from mediaforge.rag.amazon_store_analysis import (
    archive,
    build_product_matrix,
    build_store_lifecycle,
    compare_store_samples,
    section_index,
    store_public_report,
    summarize_archive,
)
from mediaforge.rag.amazon_store_collection import (
    FETCH_CONCURRENCY,
    SECTION_ASIN_LIMIT,
    SECTION_LIMIT,
    collect_evidence,
    collect_listing_date,
    discover_sections,
    page_id,
    section_step,
)
from mediaforge.rag.amazon_store_translation import localize_research_fields


def _update_report(run: AmazonResearchRun) -> list[AmazonStoreSample]:
    run.snapshot.asin_sources = section_index(run.snapshot)
    listings = archive(run.snapshot)
    run.snapshot.product_archive = summarize_archive(listings)
    run.report = store_public_report(run.inputs, run.snapshot, run.created_at, listings)
    return listings


async def _collect_sections(client: httpx.AsyncClient, run: AmazonResearchRun, token: UUID) -> None:
    snapshot = run.snapshot
    pending = [
        item for item in snapshot.evidence if item.kind == "section" and item.status == "pending"
    ]
    semaphore = asyncio.Semaphore(FETCH_CONCURRENCY)

    async def collect(item: AmazonEvidence) -> AmazonEvidence:
        async with semaphore:
            return await collect_evidence(
                client,
                item,
                expected_root=snapshot.store_root,
                scope=snapshot.store_scope,
            )

    tasks = [asyncio.create_task(collect(item)) for item in pending]
    try:
        for task in asyncio.as_completed(tasks):
            item = await task
            index = next(
                i for i, previous in enumerate(snapshot.evidence) if previous.key == item.key
            )
            snapshot.evidence[index] = item
            logger.info(
                "Amazon research run={} item={} status={} reason={} count={} duration_ms={}",
                run.id,
                index,
                item.status,
                item.code,
                item.observed_count,
                item.duration_ms,
            )
            _update_report(run)
            await store.checkpoint(run, token, "section")
    finally:
        for task in tasks:
            if not task.done():
                task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)


def _mark_redirect_aliases(run: AmazonResearchRun) -> None:
    sections = [item for item in run.snapshot.evidence if item.kind == "section"]
    resolved = {
        page_id(item.url): item
        for item in sections
        if item.status in {"success", "empty", "incomplete"}
        and item.final_url
        and page_id(item.final_url) == page_id(item.url)
    }
    for item in sections:
        if not item.final_url or item.status not in {"success", "empty", "incomplete"}:
            continue
        target = resolved.get(page_id(item.final_url))
        if target and target is not item and (target.products or not item.products):
            item.status, item.code = "incomplete", "duplicate"
            item.reason = "链接转到了已采集的栏目。原采集记录保留，商品汇总按栏目去重。"


async def _collect_suggestions(client: httpx.AsyncClient, run: AmazonResearchRun) -> None:
    if not run.inputs.keyword:
        return
    snapshot = run.snapshot
    saved = next((item for item in snapshot.evidence if item.kind == "suggestions"), None)
    if saved and saved.status != "pending":
        return
    if saved is None:
        saved = AmazonEvidence(
            key="suggestions",
            kind="suggestions",
            url=SUGGESTIONS,
            title="搜索联想词",
            method="Amazon 美国站搜索建议接口",
        )
        snapshot.evidence.append(saved)
    started = monotonic()
    suggestions = await _fetch_suggestions(client, run.inputs.keyword)
    saved.collected_at = datetime.now(UTC)
    saved.attempts += 1
    saved.duration_ms = round((monotonic() - started) * 1000)
    saved.status = "failed" if suggestions is None else "success" if suggestions else "empty"
    saved.code = "suggestions_unavailable" if suggestions is None else None
    saved.reason = "Amazon 搜索联想暂不可用。" if suggestions is None else None
    saved.observed_count = len(suggestions) if suggestions is not None else None
    snapshot.suggestions = suggestions or []


async def _collect_store(run: AmazonResearchRun, token: UUID) -> bool:
    snapshot = run.snapshot
    snapshot.product_matrix = None
    if snapshot.store_scope is None:
        # 保存任务当时的样本边界，之后导出历史时无需以当前配置猜测。
        snapshot.store_scope = AmazonStoreSampleScope(
            section_limit=SECTION_LIMIT,
            asin_limit_per_section=SECTION_ASIN_LIMIT,
        )
    if not snapshot.evidence:
        snapshot.evidence.append(section_step(run.source, "提交的店铺或栏目"))
        await store.checkpoint(run, token, "section")
    async with amazon_public_client() as client:
        while True:
            await _collect_sections(client, run, token)
            sections = [item for item in snapshot.evidence if item.kind == "section"]
            added, snapshot.store_root, limited = discover_sections(
                sections,
                snapshot.store_root,
                snapshot.store_scope.section_limit,
            )
            if limited or not added:
                snapshot.store_scope.navigation_truncated = limited
            _mark_redirect_aliases(run)
            snapshot.evidence.extend(added)
            _update_report(run)
            await store.checkpoint(run, token, "section")
            if not added:
                break
        await _collect_suggestions(client, run)
    return limited


async def _localize(run: AmazonResearchRun) -> None:
    snapshot = run.snapshot
    sections = [
        item
        for item in snapshot.evidence
        if item.kind == "section" and item.title != "提交的店铺或栏目"
    ]
    fields = []
    for item in sections:
        if item.title.startswith("Amazon.com:") and item.role == "home":
            brand = item.title.split(":", 2)[1].strip()
            item.title_zh = f"{brand} 品牌店铺" if brand else None
        else:
            fields.append(item)
    products = [product for item in sections for product in item.products]
    variants = [product.store_variant for product in products if product.store_variant]
    try:
        snapshot.translation_missing = await localize_research_fields(
            fields + products + variants, variants
        )
        snapshot.translation_status = "incomplete" if snapshot.translation_missing else "success"
    except Exception as exc:
        snapshot.translation_status = "failed"
        logger.warning(
            "Amazon research run={} phase=translation reason={}", run.id, type(exc).__name__
        )


async def _report_store_lifecycle(
    run: AmazonResearchRun,
    token: UUID,
    products: list[AmazonStoreSample],
) -> None:
    snapshot = run.snapshot
    change = compare_store_samples(snapshot, await store.prior_store_snapshots(run))
    saved = {
        item.asin: item
        for item in (snapshot.lifecycle.dates if snapshot.lifecycle else [])
        if item.date_text
    }
    snapshot.lifecycle = build_store_lifecycle(products, change)
    new_samples = dict.fromkeys(
        asin for asins in snapshot.product_matrix.new_sections.values() for asin in asins
    )
    if change:
        new_samples.update(dict.fromkeys(change.newly_seen))
    # 优先查询公开新品栏目样本，最多三件。无日期也保留 ASIN、查询时间与原因。
    selected = list(new_samples)[:3]
    remaining = [asin for asin in selected if asin not in saved]
    if remaining:
        await store.checkpoint(run, token, "listing_dates")
        async with amazon_public_client() as client:
            collected = await asyncio.gather(
                *(collect_listing_date(client, asin) for asin in remaining)
            )
        saved.update((item.asin, item) for item in collected)
    snapshot.lifecycle.dates = [saved[asin] for asin in selected]


async def process_research(run_id: UUID, attempt: int) -> bool:
    started = monotonic()
    run, token, busy = await store.claim_run(run_id, attempt)
    if run is None or token is None:
        return busy
    snapshot = run.snapshot
    try:
        if run.mode == "bestsellers":
            await collect_category(run, token)
            return False
        limited = await _collect_store(run, token)
        await store.checkpoint(run, token, "translation")
        await _localize(run)
        scope = snapshot.store_scope
        snapshot.notes = [
            f"采集范围最多 {scope.section_limit} 个栏目，"
            f"每栏保留最多 {scope.asin_limit_per_section} 个 ASIN。"
        ]
        if limited:
            snapshot.notes.append("达到栏目数量上限，部分已发现的栏目尚未采集。")
        if not snapshot.store_root:
            snapshot.notes.append("公开导航未确认首页，报告首页链接暂用本次提交地址。")
        if snapshot.translation_status in {"incomplete", "failed"}:
            snapshot.notes.append("部分店铺字段未取得中文译文，仍可查看原文和对应商品。")
        listings = _update_report(run)
        snapshot.product_matrix = build_product_matrix(snapshot, listings)
        await _report_store_lifecycle(run, token, listings)
        sections = [
            item
            for item in snapshot.evidence
            if item.kind == "section" and item.code != "duplicate"
        ]
        useful = any(item.products or item.status in {"success", "empty"} for item in sections)
        partial = (
            limited
            or any(
                item.status in {"failed", "incomplete"} and item.code != "duplicate"
                for item in snapshot.evidence
            )
            or snapshot.translation_status in {"incomplete", "failed"}
        )
        status = "failed" if not useful else "partial_fail" if partial else "done"
        error = (
            next((item.reason for item in sections if item.reason), None) if not useful else None
        )
        await store.checkpoint(run, token, "report", status=status, error=error)
    except store.ResearchRunConflict:
        return False
    except Exception as exc:
        logger.error(
            "Amazon research run={} phase=collection reason={}", run.id, type(exc).__name__
        )
        await store.checkpoint(
            run,
            token,
            "boards" if run.mode == "bestsellers" else "section",
            status="partial_fail" if run.report else "failed",
            error="榜单采集未完成，已保存可用样本。" if run.mode == "bestsellers"
            else "栏目采集未完成，已保存可用样本。",
        )
    logger.info(
        "Amazon research run={} attempt={} duration_ms={}",
        run_id,
        attempt,
        round((monotonic() - started) * 1000),
    )
    return False
