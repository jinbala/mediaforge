from collections.abc import Iterable, Sequence
from datetime import UTC, datetime
from itertools import chain, islice
from math import isfinite
from string import punctuation
from urllib.parse import quote, urlsplit

from mediaforge.models.amazon_research_export import (
    ReportCell,
    ReportProvenance,
    ReportSectionStatus,
    ResearchCollectionRange,
    ResearchDataSource,
    ResearchEvidenceBundle,
    ResearchReportIndex,
    ResearchReportMetadata,
    ResearchReportRow,
    ResearchReportSection,
    ResearchReportTable,
    ResearchSectionEvidence,
    ResearchSourceSamples,
)
from mediaforge.models.amazon_research_run import AmazonResearchRun

_CATEGORY = (
    ("opportunities", "细分机会与价格分布"),
    ("competition", "竞争格局与进入门槛"),
    ("keywords", "关键词、SEO 与 PPC"),
    ("profit", "利润与启动成本"),
    ("summary", "市场机会评分与综合报告"),
)
_PRODUCT = (
    ("REQ-021", "商品档案与市场快照"),
    ("REQ-022", "关键词、搜索位置与品牌矩阵"),
    ("REQ-023", "竞品对标"),
    ("REQ-024", "评论声音、主题与改进机会"),
    ("REQ-025", "价格、成本与利润情景"),
    ("REQ-026", "Listing 优化"),
    ("REQ-027", "图片与视频方案"),
    ("REQ-028", "可编辑 Listing 模板"),
    ("REQ-029", "综合决策与验证计划"),
)
_STORE = (
    ("product_matrix", "产品矩阵"),
    ("competition", "竞争格局"),
    ("revenue", "销量与营收"),
    ("growth", "增长与新品观察"),
    ("summary", "综合报告"),
)
_OBSERVED = {"success", "empty", "incomplete"}
_MAX_SOURCES = 96
_MAX_ROWS = 120
_MAX_CELLS = 12_000
_MAX_TEXT = 200_000
_MAX_MARKDOWN_BYTES = 1_048_576
_OMITTED = "部分内容超过导出上限，已截断；统计口径仍为原始快照。"


class _Budget:
    def __init__(self, characters: int = _MAX_TEXT, cells: int = _MAX_CELLS) -> None:
        self.truncated = False
        self.characters = characters
        self.cells = cells

    def take(self, items: Iterable, limit: int) -> list:
        values = list(islice(items, limit + 1))
        if len(values) > limit:
            self.truncated = True
        return values[:limit]

    def text(self, value: str, limit: int = 512) -> str:
        allowed = min(limit, self.characters)
        if len(value) > allowed:
            self.truncated = True
            value = value[: max(0, allowed - 1)] + "…" if allowed else ""
        self.characters -= len(value)
        return value

    def url(self, value: str | None) -> str | None:
        if not value:
            return None
        if len(value) > min(2000, self.characters):
            self.truncated = True
            return None
        if any(char.isspace() or ord(char) < 32 or char == "\\" for char in value):
            return None
        try:
            parts = urlsplit(value)
            if parts.scheme in {"http", "https"} and parts.hostname and parts.username is None:
                self.characters -= len(value)
                return value
        except ValueError:
            pass
        return None

    def cell(self, value: ReportCell) -> ReportCell:
        if isinstance(value, str):
            return self.text(value)
        if isinstance(value, float) and not isfinite(value):
            return None
        return value

    def table(
        self,
        title: str,
        columns: Sequence[str],
        rows: Iterable[Sequence[ReportCell]],
        provenance: ReportProvenance | None = None,
        asins: Iterable[Sequence[str]] | None = None,
    ) -> ResearchReportTable:
        headers = [self.text(col, 120) for col in self.take(columns, 24)]
        output = ResearchReportTable(
            title=self.text(title, 160), columns=headers, provenance=provenance
        )
        if not headers:
            return output
        row_limit = min(_MAX_ROWS, self.cells // len(headers))
        ids = iter(asins) if asins is not None else None
        for cells in self.take(rows, row_limit):
            if len(cells) > len(headers):
                self.truncated = True
            row = [self.cell(value) for value in cells[: len(headers)]]
            row.extend([None] * (len(headers) - len(row)))
            refs = [self.text(asin, 32) for asin in self.take(next(ids, ()), 30)] if ids else []
            output.rows.append(ResearchReportRow(cells=row, asins=refs))
            self.cells -= len(headers)
        return output


class _Sources:
    def __init__(self, budget: _Budget) -> None:
        self.budget = budget
        self.rows: list[ResearchDataSource] = []
        self.by_key: dict[str, str] = {}
        self.by_location: dict[tuple[str, datetime | None], str] = {}
        self.sample_rows: list[tuple[str, str, Sequence[str], Iterable[Sequence[ReportCell]]]] = []
        self.section_budgets: list[_Budget] = []
        self.count = 0
        self.observed = 0
        self.first: datetime | None = None
        self.last: datetime | None = None

    def add(
        self,
        kind: str,
        title: str,
        url: str | None = None,
        collected_at: datetime | None = None,
        *,
        status: str = "success",
        key: str | None = None,
        requested_url: str | None = None,
        method: str | None = None,
        reason: str | None = None,
        count: int | None = None,
        manual: bool = False,
        reuse: bool = False,
    ) -> str | None:
        location = (url, collected_at) if url else None
        if reuse and location in self.by_location:
            return self.by_location[location]
        self.count += 1
        derived = kind == "analysis" and status in _OBSERVED
        observed = not manual and not derived and status in _OBSERVED
        provenance: ReportProvenance = "unknown"
        if manual:
            provenance = "manual"
        elif observed:
            provenance = "observed"
        elif derived:
            provenance = "estimate"
        if observed:
            self.observed += 1
            if collected_at is not None:
                instant = (
                    collected_at.replace(tzinfo=UTC)
                    if collected_at.tzinfo is None
                    else (collected_at.astimezone(UTC))
                )
                self.first = min(self.first, instant) if self.first else instant
                self.last = max(self.last, instant) if self.last else instant
        if len(self.rows) >= _MAX_SOURCES:
            self.budget.truncated = True
            return None
        source_id = f"s{self.count}"
        self.rows.append(
            ResearchDataSource(
                id=source_id,
                key=self.budget.text(key, 200) if key is not None else None,
                kind=kind,
                title=self.budget.text(title, 240),
                url=self.budget.url(url),
                requested_url=self.budget.url(requested_url) if requested_url != url else None,
                collected_at=collected_at,
                status=status,
                provenance=provenance,
                method=self.budget.text(method, 200) if method else None,
                reason=self.budget.text(reason) if reason else None,
                sample_count=count,
            )
        )
        if key is not None:
            self.by_key[key] = source_id
        if location is not None and observed:
            self.by_location[location] = source_id
            if requested_url:
                self.by_location[(requested_url, collected_at)] = source_id
        return source_id

    def ids(self, *kinds: str) -> list[str]:
        return [row.id for row in self.rows if row.kind in kinds]

    def section_budget(self) -> _Budget:
        budget = _Budget(characters=24_000, cells=1600)
        self.section_budgets.append(budget)
        return budget

    def sample(
        self,
        source_id: str | None,
        title: str,
        columns: Sequence[str],
        rows: Iterable[Sequence[ReportCell]],
    ) -> None:
        if source_id is not None:
            self.sample_rows.append((source_id, title, columns, rows))

    def samples(self) -> list[ResearchSourceSamples]:
        samples = []
        for source_id, title, columns, rows in self.sample_rows:
            table = self.budget.table(title, columns, rows, "observed")
            if table.rows:
                samples.append(ResearchSourceSamples(source_id=source_id, table=table))
        return samples


def _listings(sources: _Sources, source_id: str | None, products: Iterable) -> None:
    sources.sample(
        source_id,
        "公开商品卡片",
        [
            "ASIN",
            "标题",
            "原始价格",
            "币种",
            "评分",
            "评分数",
            "排名",
            "商品 URL",
            "同款组",
            "配色组",
            "配色",
            "尺码",
            "原报价标签",
        ],
        (
            (
                p.asin,
                p.title,
                p.price,
                getattr(p, "price_currency", None),
                p.rating,
                p.rating_count,
                p.rank,
                p.url,
                getattr(p, "family_asin", None),
                getattr(p, "color_group_asin", None),
                getattr(p, "color", None),
                getattr(p, "size", None),
                getattr(p, "price_label", None),
            )
            for p in products
        ),
    )


def _collect_sources(run: AmazonResearchRun, sources: _Sources, include_samples: bool) -> None:
    snapshot = run.snapshot
    for item in snapshot.evidence:
        source_id = sources.add(
            item.kind,
            item.title_zh or item.title,
            item.final_url or item.url,
            item.collected_at,
            status=item.status,
            key=item.key,
            requested_url=item.url,
            method=item.method,
            reason=item.reason,
            count=item.observed_count
            if item.observed_count is not None
            else (len(item.products) if item.products else None),
        )
        if include_samples and item.products and item.status in _OBSERVED:
            _listings(sources, source_id, item.products)
    if run.mode == "store":
        if competition := snapshot.competition:
            for search in competition.searches:
                source_id = sources.add(
                    "benchmark_search",
                    search.query,
                    search.url,
                    search.collected_at,
                    status=search.status,
                    reason=search.problem,
                    count=None if search.status == "failed" else len(search.candidates),
                    reuse=True,
                )
                if include_samples and search.candidates:
                    sources.sample(
                        source_id,
                        "公开竞品搜索样本",
                        ["ASIN", "标题"],
                        ((p.asin, p.title) for p in search.candidates),
                    )
            for item in competition.listings.values():
                sources.add(
                    "benchmark_listing",
                    item.title or item.asin,
                    item.url,
                    item.collected_at,
                    status="failed" if item.problem else "success",
                    reason=item.problem,
                    count=None if item.problem else 1,
                    reuse=True,
                )
        if lifecycle := snapshot.lifecycle:
            for item in lifecycle.dates:
                sources.add(
                    "listing_date",
                    item.asin,
                    item.url,
                    item.collected_at,
                    status="incomplete" if item.problem else "success",
                    reason=item.problem,
                    count=1 if item.date_text else None,
                    reuse=True,
                )
    elif run.mode == "product" and (depth := snapshot.product_depth):
        pages = chain([depth.overview] if depth.overview else [], depth.competitors)
        for page in pages:
            sources.add(
                "product" if page is depth.overview else "competitor",
                page.product.title,
                page.product.url,
                page.collected_at,
                count=1,
                reuse=True,
            )
        for search in depth.searches:
            source_id = sources.add(
                "search",
                search.query,
                search.url,
                search.collected_at,
                status="success" if search.rows else "empty",
                count=len(search.rows),
                reuse=True,
            )
            if include_samples:
                sources.sample(
                    source_id,
                    "关键词公开结果数（非搜索量）",
                    ["下界", "上界", "原文"],
                    [(search.result_min, search.result_max, search.result_label)],
                )
        for page in depth.review_pages:
            sources.add(
                "reviews",
                f"评论页：{page.bucket}",
                page.url,
                page.collected_at,
                status="incomplete" if page.limited else "success" if page.rows else "empty",
                count=len(page.rows),
                reuse=True,
            )
    report = run.report
    if report and not sources.observed:
        source_id = sources.add(
            "legacy_report",
            report.title_zh or report.title,
            report.source_url,
            report.collected_at,
            count=len(report.listings) if report.listings else (1 if report.product else None),
        )
        if include_samples:
            _listings(
                sources, source_id, report.listings or ([report.product] if report.product else [])
            )


def _unknown(section_id: str, title: str, *sources: str) -> ResearchSectionEvidence:
    return ResearchSectionEvidence(
        id=section_id,
        title=title,
        status="unknown",
        provenance=["unknown"],
        notes=["当前快照没有此章节的已保存分析或可用证据。"],
        source_ids=list(sources),
    )


def _category_sections(run: AmazonResearchRun, sources: _Sources) -> list[ResearchSectionEvidence]:
    snapshot = run.snapshot
    analysis = snapshot.category_analysis
    saved = {section.key: section for section in analysis.sections} if analysis else {}
    board_ids = sources.ids("board", "legacy_report")
    costs = run.inputs.category.costs if run.inputs.category else None
    manual_id = (
        sources.add("manual", "用户填写的类目成本假设", status="provided", manual=True)
        if costs
        else None
    )
    sections = []
    for key, title in _CATEGORY:
        if key not in saved:
            sections.append(_unknown(key, title, *board_ids))
            continue
        budget = sources.section_budget()
        item = saved[key]
        asins = {asin for table in item.tables for row in table.rows for asin in row.asins}
        keys = {key for asin in asins for key in snapshot.asin_sources.get(asin, ())}
        linked = {sources.by_key[key] for key in keys if key in sources.by_key}
        source_ids = [entry for entry in board_ids if entry in linked] or board_ids.copy()
        if key == "keywords":
            source_ids += sources.ids("suggestions")
        has_rows = any(table.rows for table in item.tables)
        status: ReportSectionStatus = (
            "complete"
            if has_rows and analysis and analysis.status == "complete"
            else "partial"
            if has_rows
            else "unknown"
        )
        provenance: list[ReportProvenance] = []
        if asins or (key == "keywords" and snapshot.suggestions):
            provenance.append("observed")
        if key in {"profit", "summary"} and costs:
            provenance.append("manual")
            if manual_id:
                source_ids.append(manual_id)
        if key in {"keywords", "profit", "summary"} and has_rows:
            provenance.append("estimate")
        if status != "complete" or not provenance:
            provenance.append("unknown")
        sections.append(
            ResearchSectionEvidence(
                id=key,
                title=budget.text(item.title, 160),
                status=status,
                sample_count=len(asins) if asins else None,
                requirements=[budget.text(req, 32) for req in budget.take(item.reqs, 12)],
                provenance=provenance,
                notes=[budget.text(note) for note in budget.take(item.notes, 20)],
                tables=[
                    budget.table(
                        table.title,
                        table.columns,
                        (row.cells for row in table.rows),
                        "estimate" if key == "profit" else None,
                        asins=(row.asins for row in table.rows),
                    )
                    for table in budget.take(item.tables, 20)
                ],
                source_ids=source_ids,
            )
        )
    return sections


def _product_sections(run: AmazonResearchRun, sources: _Sources) -> list[ResearchSectionEvidence]:
    depth = run.snapshot.product_depth
    analysis = depth.analysis if depth else None
    saved = {section.req: section for section in analysis.sections} if analysis else {}
    costs = run.inputs.product_costs
    has_costs = costs is not None and bool(costs.model_dump(exclude_none=True))
    manual_reviews = bool(
        depth
        and depth.overview
        and any(
            row.source == "manual"
            for row in chain(depth.overview.reviews, depth.overview.product.reviews)
        )
    )
    cost_id = (
        sources.add("manual", "用户填写的成本或销量标定", status="provided", manual=True)
        if has_costs
        else None
    )
    review_id = (
        sources.add("manual", "用户提供的评论样本", status="provided", manual=True)
        if manual_reviews
        else None
    )
    sections = []
    for key, title in _PRODUCT:
        if key not in saved:
            sections.append(_unknown(key, title, *sources.ids("product", "legacy_report")))
            continue
        budget = sources.section_budget()
        item = saved[key]
        source_ids = list(
            dict.fromkeys(
                entry
                for citation in item.sources
                if (
                    entry := sources.add(
                        "citation", citation.label, citation.url, citation.collected_at, reuse=True
                    )
                )
                is not None
            )
        )
        provenance: list[ReportProvenance] = ["observed"] if item.sources else []
        is_manual = (has_costs and key in {"REQ-025", "REQ-029"}) or (
            manual_reviews and key in {"REQ-024", "REQ-026", "REQ-027", "REQ-029"}
        )
        if is_manual:
            provenance.append("manual")
            if key in {"REQ-025", "REQ-029"} and cost_id:
                source_ids.append(cost_id)
            if key in {"REQ-024", "REQ-026", "REQ-027", "REQ-029"} and review_id:
                source_ids.append(review_id)
        if key >= "REQ-025" and any(table.rows for table in item.tables):
            provenance.append("estimate")
        if item.status != "complete" or not provenance:
            provenance.append("unknown")
        sections.append(
            ResearchSectionEvidence(
                id=key,
                title=budget.text(item.title, 160),
                status=item.status,
                sample_count=1 if depth and depth.overview and key == "REQ-021" else None,
                requirements=[item.req],
                provenance=provenance,
                notes=[budget.text(note) for note in budget.take(item.summary, 20)],
                tables=[
                    budget.table(table.title, table.columns, table.rows)
                    for table in budget.take(item.tables, 20)
                ],
                source_ids=source_ids,
            )
        )
    return sections


def _money(cents: int) -> str:
    return f"{cents / 100:.2f}"


def _store_sections(run: AmazonResearchRun, sources: _Sources) -> list[ResearchSectionEvidence]:
    snapshot = run.snapshot
    product, peers, revenue, growth, summary = [_unknown(key, title) for key, title in _STORE]
    observed = sources.ids("section", "legacy_report")
    product.source_ids = observed
    if matrix := snapshot.product_matrix:
        budget = sources.section_budget()
        collection_complete = all(
            item.status in {"success", "empty"}
            for item in snapshot.evidence
            if item.kind == "section"
        ) and not (snapshot.store_scope and snapshot.store_scope.navigation_truncated)
        if matrix.asin_count:
            product.status = (
                "complete"
                if collection_complete
                and not (matrix.missing_price or matrix.missing_eligible_price)
                else "partial"
            )
        product.sample_count = matrix.asin_count
        product.provenance = ["observed"] if matrix.asin_count else []
        if product.status != "complete":
            product.provenance.append("unknown")
        product.notes = [
            f"规则版本 {matrix.rules_version}。价格带左闭右开；缺价不按零计。",
            f"有效单价 {len(matrix.prices)}/{matrix.asin_count}；缺价 {len(matrix.missing_price)}；"
            f"报价不符合单价口径 {len(matrix.missing_eligible_price)}。",
            "标题型号是待验证候选；评分数可能跨变体共享，不是销量。",
        ]
        if matrix.prices:
            prices = [row.price_cents for row in matrix.prices.values()]
            product.notes.append(
                f"已观察单价范围：{snapshot.currency} {_money(min(prices))}–{_money(max(prices))}。"
            )
        product.tables = [
            budget.table(
                "价格带",
                ["下界", "上界（不含，空为无上限）", "样本数", "占比 %"],
                (
                    (
                        _money(band.min_cents),
                        _money(band.max_cents) if band.max_cents is not None else None,
                        len(band.asins),
                        band.share_pct,
                    )
                    for band in matrix.price_bands
                ),
                "observed",
                asins=(band.asins for band in matrix.price_bands),
            ),
            budget.table(
                "有效单价",
                ["ASIN", "栏目键", f"单价 {snapshot.currency}"],
                (
                    (asin, quote.section, _money(quote.price_cents))
                    for asin, quote in matrix.prices.items()
                ),
                "observed",
            ),
            budget.table(
                "栏目样本与评分",
                [
                    "栏目键",
                    "ASIN 数",
                    "有效单价数",
                    "评分样本数",
                    "平均评分",
                    "评分数样本数",
                    "评分数中位数",
                ],
                (
                    (
                        row.section,
                        row.asin_count,
                        row.priced_count,
                        row.rating_count,
                        row.average_rating,
                        row.rating_total_count,
                        row.median_rating_total,
                    )
                    for row in matrix.sections
                ),
                "observed",
            ),
            budget.table(
                "产品组合",
                ["分类口径", "名称", "ASIN 数"],
                ((label, name, len(ids)) for label, name, ids in _matrix_groups(matrix)),
                asins=(ids for _, _, ids in _matrix_groups(matrix)),
            ),
            budget.table(
                "评分数领先款",
                ["ASIN", "评分数", "同款组"],
                ((row.asin, row.count, row.family_asin) for row in matrix.rating_leaders),
                "observed",
            ),
        ]
    else:
        raw_steps = [
            item
            for item in snapshot.evidence
            if item.kind == "section" and item.status in _OBSERVED and item.products
        ]
        legacy_items = run.report.listings if run.report and not raw_steps else []
        if raw_steps or legacy_items:
            product.status = "partial"
            product.sample_count = _unique_asins(run)
            product.provenance = ["observed", "unknown"]
            product.notes = ["未保存产品矩阵；以下仅列已观察商品，不补算价格带或分组。"]
            rows = chain(
                (
                    (step.key, row.asin, row.title_zh or row.title, row.price, row.price_currency)
                    for step in raw_steps
                    for row in step.products
                ),
                (
                    (None, row.asin, row.title_zh or row.title, row.price, None)
                    for row in legacy_items
                ),
            )
            product.tables = [
                sources.section_budget().table(
                    "公开商品卡片",
                    ["栏目键", "ASIN", "标题", "原报价", "币种"],
                    rows,
                    "observed",
                )
            ]
    peers.source_ids = sources.ids("benchmark_search", "benchmark_listing")
    if competition := snapshot.competition:
        budget = sources.section_budget()
        observed_count = sum(not row.problem for row in competition.listings.values())
        has_evidence = observed_count > 0 or any(
            search.status == "success" and search.candidates for search in competition.searches
        )
        peers.status = "partial" if has_evidence else "unknown"
        peers.provenance = ["observed", "unknown"] if has_evidence else ["unknown"]
        if competition.pairs:
            peers.provenance.append("estimate")
        peers.sample_count = observed_count if competition.listings else None
        peers.notes = [
            "仅涵盖已采公开搜索与商品详情；价格、评分及候选不能代表市场份额。",
            "受众与 SWOT 为待验证判断，不是平台观测。",
        ]
        peers.tables = [
            budget.table(
                "公开竞品商品详情",
                [
                    "ASIN",
                    "标题",
                    "品牌",
                    "类目",
                    "原始报价",
                    "币种",
                    "评分",
                    "评分数",
                    "商品 URL",
                    "采集时间",
                    "缺失原因",
                ],
                (
                    (
                        row.asin,
                        row.title,
                        row.brand,
                        row.category,
                        row.price,
                        row.price_currency,
                        row.rating,
                        row.rating_count,
                        row.url,
                        row.collected_at.isoformat(),
                        row.problem,
                    )
                    for row in competition.listings.values()
                ),
                "observed",
            ),
            budget.table(
                "已保存的配对与判断",
                [
                    "目标 ASIN",
                    "竞品 ASIN",
                    "共同类目",
                    "共同标题词",
                    "受众",
                    f"标价差 {snapshot.currency}",
                    "SWOT",
                ],
                (
                    (
                        pair.target_asin,
                        pair.competitor_asin,
                        pair.shared_category,
                        _joined(pair.shared_terms, budget),
                        pair.audience,
                        _money(pair.price_gap_cents) if pair.price_gap_cents is not None else None,
                        _joined((f"{name}: {value}" for name, value in pair.swot.items()), budget),
                    )
                    for pair in competition.pairs
                ),
            ),
        ]
    revenue.notes = [
        "未知：快照没有已核实的销量、营收或可用的营收估算。标价及评分数不能换算为营收。"
    ]
    growth.source_ids = list(dict.fromkeys(observed + sources.ids("listing_date")))
    growth.notes = ["未知：销量增长和上新节奏未获核实；公开样本的出现或消失不代表上架或下架。"]
    if lifecycle := snapshot.lifecycle:
        budget = sources.section_budget()
        dates = sum(bool(row.date_text) for row in lifecycle.dates)
        has_evidence = bool(dates or lifecycle.change or lifecycle.colors or lifecycle.sizes)
        if has_evidence:
            growth.status = "partial"
            growth.provenance = ["observed", "unknown"]
        growth.sample_count = dates if lifecycle.dates else None
        growth.tables = [
            budget.table(
                "公开商品日期",
                ["ASIN", "日期类型", "原文", "商品 URL", "采集时间", "缺失原因"],
                (
                    (
                        row.asin,
                        row.label,
                        row.date_text,
                        row.url,
                        row.collected_at.isoformat(),
                        row.problem,
                    )
                    for row in lifecycle.dates
                ),
                "observed",
            ),
            budget.table(
                "配色与尺码样本",
                ["口径", "名称", "ASIN 数"],
                chain(
                    (("配色", name, count) for name, count in lifecycle.colors.items()),
                    (("尺码", name, count) for name, count in lifecycle.sizes.items()),
                    [
                        ("配色", "未知", lifecycle.missing_color),
                        ("尺码", "未知", lifecycle.missing_size),
                    ],
                ),
                "observed",
            ),
        ]
        if change := lifecycle.change:
            growth.notes.append(f"上期任务 {change.previous_run_id}；仅比较共同可读栏目。")
            growth.tables.append(
                budget.table(
                    "两期公开样本对照（非增长率）",
                    ["上期采集时间", "本期采集时间", "共同栏目", "新观察数", "未再观察数"],
                    [
                        (
                            change.previous_collected_at.isoformat(),
                            change.current_collected_at.isoformat(),
                            _joined(change.section_keys, budget),
                            len(change.newly_seen),
                            len(change.no_longer_seen),
                        )
                    ],
                    "observed",
                )
            )
            growth.tables.append(
                budget.table(
                    "公开样本变化明细",
                    ["口径", "范围", "数量"],
                    ((label, name, len(ids)) for label, name, ids in _change_groups(change)),
                    "observed",
                    asins=(ids for _, _, ids in _change_groups(change)),
                )
            )
    summary.source_ids = observed
    budget = sources.section_budget()
    archive = snapshot.product_archive
    summary.sample_count = (
        archive.asin_count
        if archive
        else (product.sample_count if product.sample_count is not None else _unique_asins(run))
    )
    summary.notes = ["报告仅覆盖本任务的 Amazon 公开样本；缺失项不是零，也不是全店、全市场结论。"]
    if summary.sample_count:
        summary.status = "partial"
        summary.provenance = ["observed", "unknown"]
    if archive:
        summary.tables = [
            budget.table(
                "商品归档及未知归属",
                ["指标", "样本数"],
                [
                    ("去重 ASIN", archive.asin_count),
                    ("已确认款式", archive.family_count),
                    ("已确认配色组", archive.color_group_count),
                    ("同款未知", archive.unknown_family_count),
                    ("配色未知", archive.unknown_color_count),
                    ("尺码未知", archive.unknown_size_count),
                ],
                "observed",
            )
        ]
    summary.notes += [budget.text(note) for note in budget.take(snapshot.notes, 12)]
    return [product, peers, revenue, growth, summary]


def _joined(items: Iterable[str], budget: _Budget) -> str:
    values = [budget.text(item, 120) for item in budget.take(items, 20)]
    return "；".join(values)


def _matrix_groups(matrix):
    for label, groups in (
        ("商品类型", matrix.product_types),
        ("规格组合", matrix.specification_combinations),
        ("标题型号候选（待验证）", matrix.model_candidates),
        ("新品栏目", matrix.new_sections),
    ):
        yield from ((label, name, asins) for name, asins in groups.items())
    for overlap in matrix.overlaps:
        yield "栏目重叠", " / ".join(overlap.sections), overlap.asins


def _change_groups(change):
    yield "新观察", "共同可读栏目", change.newly_seen
    yield "未再观察", "共同可读栏目", change.no_longer_seen
    for row in change.series_changes:
        yield "新观察", row.family_asin, row.newly_seen
        yield "未再观察", row.family_asin, row.no_longer_seen


def _unique_asins(run: AmazonResearchRun) -> int | None:
    snapshot = run.snapshot
    if snapshot.asin_sources:
        return len(snapshot.asin_sources)
    if run.mode == "store" and snapshot.product_archive:
        return snapshot.product_archive.asin_count
    if run.mode == "store" and snapshot.product_matrix:
        return snapshot.product_matrix.asin_count
    if run.mode == "product" and (depth := snapshot.product_depth):
        asins = {p.product.asin for p in depth.competitors}
        asins.update(row.asin for search in depth.searches for row in search.rows)
        if depth.overview:
            asins.add(depth.overview.product.asin)
        return len(asins) if asins else None
    observed = {
        p.asin for row in snapshot.evidence for p in row.products if row.status in _OBSERVED
    }
    if run.report and not observed:
        observed.update(p.asin for p in run.report.listings)
        if run.report.product:
            observed.add(run.report.product.asin)
    if observed:
        return len(observed)
    if any(row.kind in {"board", "section"} and row.status == "empty" for row in snapshot.evidence):
        return 0
    return None


def _report(run: AmazonResearchRun, *, include_samples: bool) -> ResearchEvidenceBundle:
    budget = _Budget()
    sources = _Sources(budget)
    source_url = budget.url(run.report.source_url if run.report else None) or (
        budget.url(run.canonical_source) or budget.url(run.source)
    )
    _collect_sources(run, sources, include_samples)
    builders = {
        "bestsellers": _category_sections,
        "product": _product_sections,
        "store": _store_sections,
    }
    sections = builders[run.mode](run, sources)
    samples = sources.samples()
    translated = {}
    if include_samples:
        for original, chinese in budget.take(run.snapshot.field_translations.items(), 500):
            if len(original) + len(chinese) > budget.characters:
                budget.truncated = True
                break
            budget.characters -= len(original) + len(chinese)
            translated[original] = chinese
    return ResearchEvidenceBundle(
        run_id=run.id,
        mode=run.mode,
        site=run.snapshot.site,
        currency=run.snapshot.currency,
        status=run.status,
        source_url=source_url,
        data_sources=sources.rows,
        sections=sections,
        collection_range=ResearchCollectionRange(
            collected_from=sources.first,
            collected_to=sources.last,
            source_count=sources.count,
            observed_source_count=sources.observed,
            unique_asin_count=_unique_asins(run),
            store_scope=run.snapshot.store_scope,
            category_scope=run.snapshot.category_scope,
        ),
        truncated=budget.truncated or any(item.truncated for item in sources.section_budgets),
        samples=samples,
        supplemental_notes=run.inputs.supplemental_notes or None,
        field_translations=translated,
    )


def build_report_index(run: AmazonResearchRun) -> ResearchReportIndex:
    report = _report(run, include_samples=False)
    return ResearchReportIndex(
        **report.model_dump(include=set(ResearchReportMetadata.model_fields)),
        sections=[
            ResearchReportSection(
                id=row.id,
                title=row.title,
                status=row.status,
                sample_count=row.sample_count,
            )
            for row in report.sections
        ],
    )


def build_evidence_bundle(run: AmazonResearchRun) -> ResearchEvidenceBundle:
    return _report(run, include_samples=True)


def _md(value: ReportCell | datetime) -> str:
    if value is None:
        return "未知"
    if isinstance(value, datetime):
        return value.isoformat()
    # Escape ASCII punctuation as literal text, so even URLs, HTML, entities, and pipes
    # from the snapshot cannot become links, markup, or table syntax. Collapse line breaks.
    text = " ".join(str(value).split())
    return "".join(f"\\{char}" if char in punctuation else char for char in text)


def _md_url(value: ReportCell | datetime) -> str:
    # Only used for source URLs already validated by _Budget.url. Quote Markdown and
    # table delimiters before emitting an autolink.
    return f"<{quote(value, safe=':/?&=%#@+;,-._~')}>" if isinstance(value, str) else _md(value)


def _md_table(
    headers: Sequence[str],
    rows: Iterable[Sequence[ReportCell | datetime]],
    *,
    url_column: int | None = None,
) -> Iterable[str]:
    yield "| " + " | ".join(map(_md, headers)) + " |"
    yield "| " + " | ".join("---" for _ in headers) + " |"
    for row in rows:
        yield (
            "| "
            + " | ".join(
                _md_url(value) if index == url_column else _md(value)
                for index, value in enumerate(row)
            )
            + " |"
        )


def _source_markdown(sources: Iterable[ResearchDataSource]) -> Iterable[str]:
    yield "\n### 数据来源\n"
    yield from _md_table(
        ["编号", "来源", "URL", "采集时间", "状态", "来源口径", "样本数", "说明"],
        (
            (
                row.id,
                row.title,
                row.url,
                row.collected_at,
                row.status,
                row.provenance,
                row.sample_count,
                row.reason or row.method,
            )
            for row in sources
        ),
        url_column=2,
    )


def _markdown_lines(
    report: ResearchEvidenceBundle, sections: list[ResearchSectionEvidence], selected: bool
) -> Iterable[str]:
    coverage = report.collection_range
    yield "# Amazon 调研报告\n"
    yield f"- 任务：{_md(str(report.run_id))}；模式：{_md(report.mode)}；状态：{_md(report.status)}"
    yield f"- 站点：{_md(report.site)}；报告币种：{_md(report.currency)}"
    yield f"- 来源 URL：{_md_url(report.source_url)}"
    yield f"- 实采时间范围：{_md(coverage.collected_from)} 至 {_md(coverage.collected_to)}"
    yield (
        f"- 记录来源：{coverage.source_count}；含已观测来源：{coverage.observed_source_count}；"
        f"去重 ASIN：{_md(coverage.unique_asin_count)}"
    )
    if scope := coverage.store_scope:
        truncated = None if scope.navigation_truncated is None else str(scope.navigation_truncated)
        yield (
            f"- 采样上限：{scope.section_limit} 栏，每栏 {scope.asin_limit_per_section} ASIN；"
            f"导航截断：{_md(truncated)}"
        )
    if scope := coverage.category_scope:
        yield (
            f"- 采样上限：主榜 {scope.main_limit}，子榜 {scope.child_limit} 个，"
            f"每子榜 {scope.child_asin_limit} ASIN；发现过程截断：{scope.discovery_truncated}"
        )
    yield ("- 口径：observed＝公开样本；estimate＝推断或情景；"
           "manual＝用户输入；unknown＝未知，不按零处理。")
    if report.supplemental_notes:
        yield "- 用户补充（manual；未独立核验、不计入实采指标）：" + _md(report.supplemental_notes)
    if report.truncated:
        yield "- " + _OMITTED
    for section in sections:
        yield f"\n## {_md(section.title)}\n"
        yield (
            f"- 章节：{_md(section.id)}；状态：{_md(section.status)}；"
            f"样本数：{_md(section.sample_count)}；口径：{_md(' / '.join(section.provenance))}"
        )
        if section.source_ids:
            yield f"- 证据编号：{_md(', '.join(section.source_ids))}"
        for note in section.notes:
            yield f"- {_md(note)}"
        for table in section.tables:
            if not table.columns or not table.rows:
                continue
            ref_column = any(row.asins for row in table.rows)
            marker = f"（{_md(table.provenance)}）" if table.provenance else ""
            yield f"\n### {_md(table.title)}{marker}\n"
            yield from _md_table(
                table.columns + (["证据 ASIN"] if ref_column else []),
                (row.cells + ([", ".join(row.asins)] if ref_column else []) for row in table.rows),
            )
    refs = {source_id for section in sections for source_id in section.source_ids}
    source_list = [row for row in report.data_sources if not selected or row.id in refs]
    if source_list:
        yield from _source_markdown(source_list)


def build_report_markdown(run: AmazonResearchRun, section: str | None = None) -> str:
    report = _report(run, include_samples=False)
    sections = report.sections
    if section is not None:
        sections = [row for row in sections if row.id == section]
        if not sections:
            raise KeyError(section)
    lines = []
    size = 0
    ending = "\n" + _OMITTED + "\n"
    capacity = _MAX_MARKDOWN_BYTES - len(ending.encode("utf-8"))
    for line in _markdown_lines(report, sections, selected=section is not None):
        size += len(line.encode("utf-8")) + 1
        if size > capacity:
            return "\n".join(lines) + "\n" + ending
        lines.append(line)
    return "\n".join(lines) + "\n"
