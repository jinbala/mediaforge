import asyncio
from collections.abc import Awaitable, Callable
from datetime import UTC, datetime
from time import monotonic
from uuid import UUID

from loguru import logger

from mediaforge.db import amazon_research_store as store
from mediaforge.models.amazon_product import ProductDepth, ReviewBucket
from mediaforge.models.amazon_research import AmazonResearchReport
from mediaforge.models.amazon_research_run import AmazonEvidence, AmazonResearchRun
from mediaforge.rag.amazon_product_collection import (
    MAX_REVIEW_ROWS,
    choose_competitors,
    choose_queries,
    collect_product,
    collect_reviews,
    collect_search,
    review_url,
)
from mediaforge.rag.amazon_research import AmazonFetchError, amazon_public_client, search_url
from mediaforge.rag.amazon_store_translation import localize_research_fields
from mediaforge.workers.amazon_product_analysis import analyze_product

MAX_COMPETITORS = 4
REVIEW_BUCKETS: dict[ReviewBucket, str] = {
    "one": "1 星",
    "two": "2 星",
    "three": "3 星",
    "recent": "最新",
}
_COLLECTION_LIMIT = asyncio.Semaphore(3)


class ProductResearchSteps:
    """集中记录单品采集子项的执行时点、失败与持久化规则。"""

    def __init__(self, run: AmazonResearchRun, token: UUID):
        self.run = run
        self.token = token
        self.items = {item.key: item for item in run.snapshot.evidence}
        self.changed = False

    def register(self, key: str, kind: str, title: str, url: str) -> AmazonEvidence:
        if key not in self.items:
            item = AmazonEvidence(key=key, kind=kind, title=title, url=url)
            self.items[key] = item
            self.run.snapshot.evidence.append(item)
        return self.items[key]

    async def checkpoint(self, stage: str) -> None:
        await store.checkpoint(self.run, self.token, stage)

    async def collect[T](
        self,
        item: AmazonEvidence,
        stage: str,
        read: Callable[[], Awaitable[T]],
        record: Callable[[T], tuple[str, datetime, int]],
    ) -> None:
        # 定点重试由任务仓库把指定项重置为 pending；其余失败和成功项不重复访问。
        if item.status != "pending":
            return
        started = monotonic()
        item.attempts += 1
        item.collected_at = datetime.now(UTC)
        item.code = item.reason = None
        try:
            async with _COLLECTION_LIMIT:
                result = await read()
            item.final_url, item.collected_at, item.observed_count = record(result)
            item.status = "success" if item.observed_count else "empty"
            self.changed = True
        except AmazonFetchError as exc:
            item.status, item.code, item.reason = "failed", exc.code, str(exc)
            item.final_url = exc.url
        except Exception as exc:
            item.status, item.code = "failed", "collection_error"
            item.reason = "采集或解析此项失败；已保存其余可用样本。"
            logger.warning(
                "Amazon product run={} item={} error={}", self.run.id, item.key, type(exc).__name__
            )
        item.duration_ms = round((monotonic() - started) * 1000)
        logger.info(
            "Amazon product run={} item={} status={} reason={} count={} ms={}",
            self.run.id,
            item.key,
            item.status,
            item.code,
            item.observed_count,
            item.duration_ms,
        )
        await self.checkpoint(stage)


async def _sample(steps: ProductResearchSteps, depth: ProductDepth) -> None:
    run = steps.run
    main = steps.register("product", "product", "目标商品详情", run.source)
    await steps.checkpoint("product")
    async with amazon_public_client() as client:

        def save_overview(page):
            depth.overview = page
            _publish(run, depth)
            return page.product.url, page.collected_at, 1

        await steps.collect(
            main,
            "product",
            lambda: collect_product(
                client,
                run.source,
                review_samples=run.inputs.review_samples,
                include_screenshot=True,
            ),
            save_overview,
        )
        if depth.overview is None:
            return
        overview = depth.overview
        for index, query in enumerate(choose_queries(overview, run.inputs.keyword), 1):
            row = steps.register(
                f"search-{index}", "search", f"关键词样本：{query}", search_url(query)
            )

            def save_search(sample):
                depth.searches = [item for item in depth.searches if item.query != sample.query]
                depth.searches.append(sample)
                return sample.url, sample.collected_at, len(sample.rows)

            await steps.collect(
                row, "product-search", lambda: collect_search(client, query), save_search
            )

        # 已选的竞品在恢复时保留，只补剩余槽位；详情请求数不因外部错误而扩大。
        used = {candidate.asin for candidate in depth.candidates}
        for candidate in choose_competitors(overview, depth.searches):
            if len(depth.candidates) == MAX_COMPETITORS:
                break
            if candidate.asin not in used:
                depth.candidates.append(candidate)
                used.add(candidate.asin)
        for candidate in depth.candidates[:MAX_COMPETITORS]:
            row = steps.register(
                f"competitor-{candidate.asin}",
                "competitor",
                f"竞品详情：{candidate.asin}",
                candidate.url,
            )

            def save_competitor(page):
                if page.product.asin != candidate.asin:
                    raise AmazonFetchError(
                        "竞品链接返回了不同 ASIN。", "asin_mismatch", page.product.url
                    )
                depth.competitors.append(page)
                return page.product.url, page.collected_at, 1

            await steps.collect(
                row,
                "product-competitors",
                lambda: collect_product(client, candidate.url),
                save_competitor,
            )

        asin = overview.product.asin
        for bucket, label in REVIEW_BUCKETS.items():
            row = steps.register(
                f"reviews-{bucket}", "reviews", f"{label}评论", review_url(asin, bucket)
            )

            def save_reviews(page):
                depth.review_pages = [
                    item for item in depth.review_pages if item.bucket != page.bucket
                ]
                depth.review_pages.append(page)
                if page.limited:
                    row.reason = (
                        "已到当前采样上限。"
                        if len(page.rows) >= MAX_REVIEW_ROWS
                        else "评论页公开可读样本不足，已保留可见评论。"
                    )
                return page.url, page.collected_at, len(page.rows)

            await steps.collect(
                row, "product-reviews", lambda: collect_reviews(client, asin, bucket), save_reviews
            )


def _publish(run: AmazonResearchRun, depth: ProductDepth) -> None:
    if depth.overview:
        overview = depth.overview
        run.report = AmazonResearchReport(
            mode="product",
            source_url=overview.product.url,
            title=overview.product.title,
            title_zh=overview.product.title_zh,
            collected_at=overview.collected_at,
            product=overview.product,
            keyword=run.inputs.keyword or None,
            warnings=run.snapshot.notes,
        )


async def _translate(steps: ProductResearchSteps, depth: ProductDepth) -> None:
    run = steps.run
    if depth.overview is None:
        return
    row = steps.register("product-translation", "analysis", "补充商品中文字段", run.source)
    row.method = "现有词表与已配置文本模型"
    pages = [depth.overview, *depth.competitors]
    # 成功子项不重跑；新增未译详情时翻译器会保留已有译文，仅补缺项。
    if row.status != "pending" and not (steps.changed and row.observed_count != len(pages)):
        return
    row.attempts += 1
    row.observed_count = len(pages)
    row.collected_at = datetime.now(UTC)
    try:
        missing = await localize_research_fields([page.product for page in pages], [])
        run.snapshot.translation_missing = missing
        row.status = "incomplete" if missing else "success"
        row.code = None
        row.reason = "部分商品未取得译文，保留原文和来源。" if missing else None
        steps.changed = True
    except Exception as exc:
        row.status, row.code = "failed", "translation_error"
        row.reason = "翻译失败；可单独重试，原文和已采样本不受影响。"
        logger.warning(
            "Amazon product run={} phase=translation error={}", run.id, type(exc).__name__
        )
    run.snapshot.translation_status = row.status
    _publish(run, depth)
    await steps.checkpoint("product-translation")


async def _analyze(steps: ProductResearchSteps, depth: ProductDepth) -> None:
    run = steps.run
    row = steps.register("product-analysis", "analysis", "生成单品九专题分析", run.source)
    row.method = "基于同一快照的版本化规则"
    if row.status != "pending" and not steps.changed:
        return
    row.attempts += 1
    row.collected_at = datetime.now(UTC)
    try:
        depth.analysis = analyze_product(depth, run.inputs.product_costs)
        row.status, row.reason, row.code = "success", None, None
        row.observed_count = len(depth.analysis.sections)
    except Exception as exc:
        row.status, row.code = "failed", "analysis_error"
        row.reason = "专题分析失败；已采集证据仍可查看，可重试分析。"
        logger.error("Amazon product run={} phase=analysis error={}", run.id, type(exc).__name__)
    await steps.checkpoint("product-analysis")


async def collect_product_depth(run: AmazonResearchRun, token: UUID) -> None:
    snapshot = run.snapshot
    depth = snapshot.product_depth or ProductDepth()
    snapshot.product_depth = depth
    steps = ProductResearchSteps(run, token)
    await _sample(steps, depth)
    await _translate(steps, depth)
    await _analyze(steps, depth)
    snapshot.notes = ["Amazon 美国站公开样本；搜索数据不代表搜索量、长期排名或市场份额。"]
    if snapshot.translation_status in {"failed", "incomplete"}:
        snapshot.notes.append("部分字段未取得中文译文，仍保留原文和对应来源链接。")
    _publish(run, depth)
    partial = any(
        item.status in {"failed", "incomplete", "pending"} for item in steps.items.values()
    )
    await store.checkpoint(
        run,
        token,
        "report",
        status="failed" if depth.overview is None else "partial_fail" if partial else "done",
        error="未取得可用商品页。" if depth.overview is None else None,
    )
