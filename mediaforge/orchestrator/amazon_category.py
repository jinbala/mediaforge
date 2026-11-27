import asyncio
from datetime import UTC, datetime
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
from mediaforge.rag.amazon_store_translation import localize_research_fields


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


async def collect_category(run: AmazonResearchRun, token: UUID) -> None:
    snapshot = run.snapshot
    scope = snapshot.category_scope or CategoryScope()
    snapshot.category_scope = scope
    if not snapshot.evidence:
        snapshot.evidence.append(board_step(run.source, "提交的主榜单", "main"))
        await store.checkpoint(run, token, "boards")
    main = snapshot.evidence[0]
    async with amazon_public_client() as client:
        if main.status == "pending":
            main = await collect_board(client, main, scope.main_limit)
            snapshot.evidence[0] = main
            _publish(run)
            await store.checkpoint(run, token, "boards")
        manual = run.inputs.category.subcategories if run.inputs.category else []
        discovered, limited = discover_boards(main, manual, scope.child_limit)
        # 恢复任务以已经保存的榜单为准，新发现的链接只补入尚余的槽位。
        children = [item for item in snapshot.evidence[1:] if item.kind == "board"]
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
    await store.checkpoint(run, token, "category-analysis")
    boards = [item for item in snapshot.evidence if item.kind == "board"]
    if any(board.products for board in boards):
        try:
            snapshot.translation_missing = await localize_research_fields(
                boards + [row for board in boards for row in board.products], []
            )
            snapshot.translation_status = (
                "incomplete" if snapshot.translation_missing else "success"
            )
        except Exception as exc:
            snapshot.translation_status = "failed"
            logger.warning(
                "Amazon category run={} phase=translation error={}", run.id, type(exc).__name__
            )
    analysis = next((item for item in snapshot.evidence if item.kind == "analysis"), None)
    if analysis is None:
        analysis = AmazonEvidence(
            key="category-analysis",
            kind="analysis",
            title="生成类目专题",
            url=run.source,
            method="基于同一任务快照的版本化分析",
        )
        snapshot.evidence.append(analysis)
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
