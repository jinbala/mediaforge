from collections import defaultdict
from dataclasses import dataclass
from typing import Literal

from mediaforge.models.amazon_research_run import (
    AmazonBenchmarkPair,
    AmazonResearchRun,
    AmazonResearchSnapshot,
    AmazonStorePerformance,
)


@dataclass(frozen=True, slots=True)
class StoreRecommendation:
    channel: Literal["Listing", "广告", "评论", "促销", "库存"]
    target_asin: str
    competitor_asin: str | None
    audience: str | None
    finding: str
    action: str
    verification: str
    horizon: Literal[30, 60, 90]
    source_keys: list[str]


def _section_sources(snapshot: AmazonResearchSnapshot) -> dict[str, set[str]]:
    sources = defaultdict(set)
    for item in snapshot.evidence:
        if (
            item.kind == "section"
            and item.key
            and item.code != "duplicate"
            and item.status in {"success", "incomplete"}
        ):
            for card in item.products:
                if card.asin:
                    sources[card.asin].add(item.key)
    return dict(sources)


def _terms(pair: AmazonBenchmarkPair) -> tuple[str, ...]:
    return tuple(sorted({term.strip() for term in pair.shared_terms if term.strip()}))


def _benchmark_pairs(
    snapshot: AmazonResearchSnapshot,
    sources: dict[str, set[str]],
) -> list[AmazonBenchmarkPair]:
    if not snapshot.competition:
        return []
    return sorted(
        (
            pair
            for pair in snapshot.competition.pairs
            if pair.target_asin in sources
            and pair.competitor_asin
            and pair.competitor_asin not in sources
            and _terms(pair)
        ),
        key=lambda pair: (
            not pair.shared_category,
            -len(_terms(pair)),
            pair.target_asin,
            pair.competitor_asin,
            _terms(pair),
            pair.audience or "",
        ),
    )


def _reviews(
    performance: AmazonStorePerformance | None,
    sources: dict[str, set[str]],
) -> StoreRecommendation | None:
    if performance is None:
        return None
    sample = min(
        (
            row
            for row in performance.low_rating
            if row.basis == "low_rating"
            and row.section in sources.get(row.asin, ())
            and row.rating is not None
            and 0 < row.rating < performance.low_rating_threshold <= 5
            and row.rating_total is not None
            and row.rating_total >= performance.low_rating_total
            and row.rating_total > 0
        ),
        key=lambda row: (row.rating, -row.rating_total, row.asin, row.section),
        default=None,
    )
    if sample is None:
        return None
    return StoreRecommendation(
        "评论",
        sample.asin,
        None,
        None,
        f"栏目样本公开评分为 {sample.rating:g}/5，共 {sample.rating_total} 个评分；原因尚未核实。",
        "先核查自然产生的评论和退货原因；确认重复问题后，修正产品或页面说明，再扩大曝光。",
        "按近期反馈记录重复问题和退货原因，对照修正前后同口径数据；不诱导、筛选或交换评价。",
        30,
        [sample.section],
    )


def _listing(
    snapshot: AmazonResearchSnapshot,
    performance: AmazonStorePerformance | None,
    pairs: list[AmazonBenchmarkPair],
    sources: dict[str, set[str]],
) -> StoreRecommendation | None:
    details = snapshot.competition.listings if snapshot.competition else {}
    for pair in pairs:
        own, rival = details.get(pair.target_asin), details.get(pair.competitor_asin)
        if (
            own
            and rival
            and not own.problem
            and not rival.problem
            and own.asin == pair.target_asin
            and rival.asin == pair.competitor_asin
            and own.has_aplus is False
            and rival.has_aplus is True
        ):
            return StoreRecommendation(
                "Listing",
                pair.target_asin,
                pair.competitor_asin,
                pair.audience or None,
                "本款公开详情未检测到 A+ 内容，对标样本检测到 A+；不能据此判断转化差异。",
                "先复查实际页面与发布资格；若确有缺口，用自有素材补充已核实的规格和使用说明。",
                "核对移动端是否展示正确、内容是否对应目标变体；有基线再比较同口径转化。",
                30,
                sorted(sources[pair.target_asin])[:3],
            )
    sample = min(
        (
            row
            for row in (performance.new if performance else [])
            if row.basis in {"new_section", "recent_date"}
            and row.section in sources.get(row.asin, ())
        ),
        key=lambda row: (row.basis != "recent_date", row.asin, row.section),
        default=None,
    )
    if sample is not None:
        basis = "保存的公开上架日期较近" if sample.basis == "recent_date" else "样本见于新品栏目"
        return StoreRecommendation(
            "Listing",
            sample.asin,
            None,
            None,
            f"{basis}，尚无证据表明详情页存在缺项。",
            "投放前核对标题、主图和变体规格是否一致；只修正核对中确认的不一致。",
            "逐项对照 ASIN 及其规格，记录移动端检查结果和修订前的转化基线。",
            30,
            [sample.section],
        )
    return None


def _ads(
    pairs: list[AmazonBenchmarkPair],
    sources: dict[str, set[str]],
) -> StoreRecommendation | None:
    if not pairs:
        return None
    pair = pairs[0]
    terms = "、".join(term[:32] for term in _terms(pair)[:3])
    return StoreRecommendation(
        "广告",
        pair.target_asin,
        pair.competitor_asin,
        pair.audience or None,
        f"对标标题共同用词：{terms}；这是相关性线索，不代表搜索量或广告表现。",
        "完成页面和供货初查后，核实共同用词是否符合本款规格和用途，再考虑小预算测试。",
        "用搜索词报告检查相关性、点击、转化和 ACOS；按预设门槛决定是否继续。",
        60,
        sorted(sources[pair.target_asin])[:3],
    )


def _usd(cents: int) -> str:
    dollars, remainder = divmod(cents, 100)
    return f"${dollars:,}.{remainder:02d}"


def _promotion(
    performance: AmazonStorePerformance | None,
    pairs: list[AmazonBenchmarkPair],
    sources: dict[str, set[str]],
) -> StoreRecommendation | None:
    change = min(
        (
            row
            for row in (performance.price_changes if performance else [])
            if row.section in sources.get(row.asin, ())
            and min(row.previous_cents, row.current_cents) > 0
            and row.previous_cents != row.current_cents
        ),
        key=lambda row: (
            -abs(row.current_cents - row.previous_cents),
            row.asin,
            row.section,
            row.previous_cents,
            row.current_cents,
        ),
        default=None,
    )
    if change is not None:
        return StoreRecommendation(
            "促销",
            change.asin,
            None,
            None,
            f"同一栏目保存的公开标价从 {_usd(change.previous_cents)} 变为 "
            f"{_usd(change.current_cents)}；不能确认变价原因或促销效果。",
            "先复核同规格净价、历史活动和利润底线；基线清楚且毛利允许后才考虑小规模试验。",
            "跟踪净成交价、转化和贡献利润，与无活动的可比基线对照。",
            90,
            [change.section],
        )
    pair = max(
        (row for row in pairs if (row.price_gap_cents or 0) > 0),
        key=lambda row: (bool(row.shared_category), row.price_gap_cents or 0),
        default=None,
    )
    if pair is None or pair.price_gap_cents is None:
        return None
    return StoreRecommendation(
        "促销",
        pair.target_asin,
        pair.competitor_asin,
        pair.audience or None,
        f"本款公开标价比对标样本高 {_usd(pair.price_gap_cents)}；规格和实际成交价尚需核对。",
        "先核对套装、规格、到手价和毛利底线；确认可比且毛利允许后才考虑小规模试验。",
        "以可比基线复核转化、净成交价和贡献利润，不把标价差视为折扣幅度。",
        90,
        sorted(sources[pair.target_asin])[:3],
    )


def _inventory(
    snapshot: AmazonResearchSnapshot,
    sources: dict[str, set[str]],
) -> StoreRecommendation | None:
    change = snapshot.lifecycle.change if snapshot.lifecycle else None
    if change:
        comparable = set(change.section_keys)
        for asin in sorted(set(change.newly_seen) & sources.keys()):
            keys = sorted(sources[asin] & comparable)
            if keys:
                return StoreRecommendation(
                    "库存",
                    asin,
                    None,
                    None,
                    "本次在可比栏目样本中新见该 ASIN；不等于新品、到货或库存增加。",
                    "先核对该 ASIN 对应的 SKU、可售状态及补货交期，避免在确认供货前扩大曝光。",
                    "对照卖家后台的可售量、在途量和交期，记录核对结果再决定是否调整备货。",
                    30,
                    keys[:3],
                )
    candidates: list[tuple[int, str]] = []
    for sample in snapshot.revenue.samples if snapshot.revenue else []:
        listing = sample.listing
        if (
            sample.asin in sources
            and listing is not None
            and listing.asin == sample.asin
            and not listing.problem
            and listing.monthly_bought is not None
            and listing.monthly_bought > 0
            and listing.monthly_bought_label
        ):
            candidates.append((listing.monthly_bought, sample.asin))
    best = min(candidates, key=lambda row: (-row[0], row[1]), default=None)
    if best is None:
        return None
    count, asin = best
    return StoreRecommendation(
        "库存",
        asin,
        None,
        None,
        f"详情页记录过去一个月购买量提示（{count:,}+）；不是实销量或库存数据。",
        "先用卖家后台实销和交期复核该 ASIN 的库存覆盖，不用公开提示直接决定备货量。",
        "用可售量、在途量及实际近 30 天销量复算覆盖天数，并与补货交期比较。",
        30,
        sorted(sources[asin])[:3],
    )


def build_store_recommendations(run: AmazonResearchRun) -> list[StoreRecommendation]:
    """Return at most one recommendation per supported channel from the saved storefront.

    Resolve feedback, content and supply checks within 30 days, then validate ad relevance
    by day 60. Defer promotion tests to day 90 so there is a comparable and profitable baseline.
    """
    if run.mode != "store":
        return []
    snapshot = run.snapshot
    sources = _section_sources(snapshot)
    if not sources:
        return []
    performance = snapshot.lifecycle.performance if snapshot.lifecycle else None
    pairs = _benchmark_pairs(snapshot, sources)
    return [
        item
        for item in (
            _reviews(performance, sources),
            _listing(snapshot, performance, pairs, sources),
            _inventory(snapshot, sources),
            _ads(pairs, sources),
            _promotion(performance, pairs, sources),
        )
        if item is not None
    ]
