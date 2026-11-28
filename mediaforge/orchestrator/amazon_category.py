import asyncio
from datetime import UTC, datetime
from time import monotonic
from urllib.parse import urlsplit
from uuid import UUID

from loguru import logger

from mediaforge.db import amazon_research_store as store
from mediaforge.models.amazon_category import CategoryScope
from mediaforge.models.amazon_research import AmazonListing, AmazonResearchReport
from mediaforge.models.amazon_research_run import AmazonEvidence, AmazonResearchRun
from mediaforge.rag.amazon_category_analysis import analyze_category
from mediaforge.rag.amazon_category_collection import board_step, collect_board, discover_boards
from mediaforge.rag.amazon_research import (
    _fetch_suggestions,
    amazon_public_client,
    suggestion_request,
)
from mediaforge.rag.amazon_store_translation import (
    DetailField,
    TranslationInputs,
    TranslationStage,
    localize_research_fields,
)


def _publish(run: AmazonResearchRun) -> None:
    snapshot = run.snapshot
    sources: dict[str, list[str]] = {}
    products: dict[str, AmazonListing] = {}
    boards = [item for item in snapshot.evidence if item.kind == "board"]
    for board in boards:
        for product in board.products:
            products.setdefault(product.asin, product)
            sources.setdefault(product.asin, []).append(board.key)
    snapshot.asin_sources = sources
    # 主榜同一个 ASIN 的原始报价优先用于摘要，跨榜的原排名只从逐榜快照回查。
    main = boards[0]
    snapshot.notes = [
        f"取样：主榜最多 {snapshot.category_scope.main_limit} 件，最多 "
        f"{snapshot.category_scope.child_limit} 个子榜，每子榜最多 "
        f"{snapshot.category_scope.child_asin_limit} 件。"
    ]
    if snapshot.category_scope.discovery_truncated:
        snapshot.notes.append("发现的子榜超过采集上限，优先保留用户指定的榜单。")
    if snapshot.translation_status in {"incomplete", "failed"}:
        snapshot.notes.append("部分榜单字段未取得中文译文，仍可查看原文和来源。")
    run.report = AmazonResearchReport(
        mode="bestsellers",
        title=main.title,
        title_zh=main.title_zh,
        source_url=main.final_url or run.source,
        collected_at=main.collected_at or run.created_at,
        listings=list(products.values()),
        warnings=snapshot.notes,
        keyword=run.inputs.keyword or None,
        suggestions=snapshot.suggestions,
    )


def _translation_inputs(run: AmazonResearchRun) -> TranslationInputs:
    boards = [
        item for item in run.snapshot.evidence
        if item.kind == "board" and item.title != "提交的主榜单"
    ]
    products = [row for board in boards for row in board.products]
    variants = [row.store_variant for row in products if row.store_variant]
    return TranslationInputs(
        [*boards, *products, *variants],
        variants,
        [DetailField("category", row.product_type) for row in products if row.product_type],
    )


async def collect_category(run: AmazonResearchRun, token: UUID) -> None:
    snapshot = run.snapshot
    scope = snapshot.category_scope or CategoryScope()
    snapshot.category_scope = scope
    main = next((item for item in snapshot.evidence if item.kind == "board"), None)
    if main is None:
        main = board_step(run.source, "提交的主榜单", "main")
        snapshot.evidence.insert(0, main)
        await store.checkpoint(run, token, "boards")
    translation = TranslationStage(
        run, "category-translation", "补充榜单中文字段",
        lambda: _translation_inputs(run), lambda stage: store.checkpoint(run, token, stage),
    )
    main_index = snapshot.evidence.index(main)
    async with amazon_public_client() as client:
        if main.status == "pending":
            main = await collect_board(client, main, scope.main_limit)
            snapshot.evidence[main_index] = main
            translation.refresh()
            _publish(run)
            await store.checkpoint(run, token, "boards")
        manual = run.inputs.category.subcategories if run.inputs.category else []
        discovered, limited = discover_boards(main, manual, scope.child_limit)
        # 恢复任务以已经保存的榜单为准，新发现的链接只补入尚余的槽位。
        children = [item for item in snapshot.evidence if item.kind == "board" and item is not main]
        known = {item.key for item in children}
        for board in discovered:
            if board.key in known:
                continue
            if len(children) >= scope.child_limit:
                limited = True
                break
            snapshot.evidence.append(board)
            children.append(board)
        scope.discovery_truncated = limited
        translation.refresh()
        await store.checkpoint(run, token, "boards")
        gate = asyncio.Semaphore(3)

        async def fetch_board(item):
            async with gate:
                return await collect_board(client, item, scope.child_asin_limit)

        tasks = [
            asyncio.create_task(fetch_board(item)) for item in children if item.status == "pending"
        ]
        try:
            for task in asyncio.as_completed(tasks):
                item = await task
                index = next(i for i, row in enumerate(snapshot.evidence) if row.key == item.key)
                snapshot.evidence[index] = item
                translation.refresh()
                logger.info(
                    "Amazon category run={} item={} status={} reason={} count={}",
                    run.id,
                    item.key,
                    item.status,
                    item.code,
                    item.observed_count,
                )
                _publish(run)
                await store.checkpoint(run, token, "boards")
        finally:
            for task in tasks:
                task.cancel()
            await asyncio.gather(*tasks, return_exceptions=True)
        keyword = run.inputs.keyword or next(
            (
                word
                for word in reversed(urlsplit(run.source).path.split("/"))
                if word and not word.isdigit() and word not in {"zgbs", "bestsellers"}
            ),
            "",
        )
        if keyword:
            keyword = keyword.replace("-", " ")[:100]
            search = next((i for i in snapshot.evidence if i.kind == "suggestions"), None)
            if search is None:
                search = AmazonEvidence(
                    key="suggestions",
                    kind="suggestions",
                    title="搜索联想：" + keyword,
                    url=str(suggestion_request(keyword)),
                    method="Amazon 美国站搜索建议接口",
                )
                snapshot.evidence.append(search)
            if search.status == "pending":
                data = await _fetch_suggestions(client, keyword)
                search.status = "failed" if data is None else "success" if data else "empty"
                search.reason = "搜索联想暂不可用，已保留榜单。" if data is None else None
                search.code = "suggestions_unavailable" if data is None else None
                search.collected_at = datetime.now(UTC)
                search.observed_count = len(data) if data is not None else None
                search.attempts += 1
                snapshot.suggestions = data or []
    _publish(run)
    boards = [item for item in snapshot.evidence if item.kind == "board"]
    await translation.execute(localize_research_fields, lambda: _publish(run))
    analysis = next((item for item in snapshot.evidence if item.key == "category-analysis"), None)
    if analysis is None:
        analysis = AmazonEvidence(
            key="category-analysis",
            kind="analysis",
            title="生成类目专题",
            url=run.source,
            method="基于同一任务快照的版本化分析",
        )
        snapshot.evidence.append(analysis)
    await store.checkpoint(run, token, "category-analysis")
    started = monotonic()
    analysis.collected_at = datetime.now(UTC)
    analysis.attempts += 1
    try:
        snapshot.category_analysis = analyze_category(run)
        analysis.status, analysis.code, analysis.reason = "success", None, None
        analysis.observed_count = len(snapshot.category_analysis.sections)
    except Exception as exc:
        analysis.status, analysis.code = "failed", "analysis_error"
        analysis.reason = "分析未完成；榜单证据仍可查看和下载，可重试分析。"
        logger.error("Amazon category run={} phase=analysis error={}", run.id, type(exc).__name__)
    analysis.duration_ms = round((monotonic() - started) * 1000)
    _publish(run)
    useful = any(item.products or item.status == "empty" for item in boards)
    partial = limited or any(i.status in {"failed", "incomplete"} for i in snapshot.evidence)
    await store.checkpoint(
        run,
        token,
        "report",
        status="failed" if not useful else "partial_fail" if partial else "done",
        error="没有可用榜单样本，请核对链接或重试。" if not useful else None,
    )
