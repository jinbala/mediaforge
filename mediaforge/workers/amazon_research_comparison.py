from math import isfinite

from mediaforge.models.amazon_research_comparison import (
    ResearchComparisonMetric,
    ResearchRunComparison,
)
from mediaforge.models.amazon_research_run import (
    AmazonEvidence,
    AmazonResearchRun,
    AmazonResearchRunSummary,
)
from mediaforge.rag.amazon_category_collection import board_identity
from mediaforge.rag.amazon_research import ASIN, AmazonSourceError, normalize_source
from mediaforge.rag.amazon_store_collection import page_id

_OBSERVED = "Amazon 商品页实际采集值"
_ASSUMED = "用户输入假设，非实际成交或费用"
_CURRENCIES = {"AUD", "CAD", "EUR", "GBP", "JPY", "SGD", "USD"}


def _metric(
    comparison: ResearchRunComparison,
    label: str,
    before: int | float | None,
    after: int | float | None,
    unit: str,
    basis: str,
) -> None:
    if before is not None and after is not None and isfinite(before) and isfinite(after):
        comparison.metrics.append(
            ResearchComparisonMetric(
                label=label, before=before, after=after, unit=unit, basis=basis
            )
        )


def _compare_product(
    comparison: ResearchRunComparison, current: AmazonResearchRun, previous: AmazonResearchRun
) -> None:
    prior_depth, depth = previous.snapshot.product_depth, current.snapshot.product_depth
    if not prior_depth or not depth or not prior_depth.overview or not depth.overview:
        comparison.notes.append("至少一次缺少结构化商品详情；不根据旧报告中的显示文字推算指标。")
        return

    before, after = prior_depth.overview, depth.overview
    asin = before.product.asin.upper()
    if not ASIN.fullmatch(asin) or asin != after.product.asin.upper():
        comparison.notes.append("两次商品详情未确认同一个 ASIN，无法对比。")
        return
    currency = (before.currency or "").strip().upper()
    if currency not in _CURRENCIES or currency != (after.currency or "").strip().upper():
        comparison.notes.append("两次商品页币种不一致或未知，无法对比。")
        return

    _metric(comparison, "商品页售价", before.price_amount, after.price_amount, currency, _OBSERVED)
    for stars in range(1, 6):
        _metric(
            comparison,
            f"{stars} 星评分占比",
            before.rating_distribution.get(stars),
            after.rating_distribution.get(stars),
            "%",
            "Amazon 商品页公开评分分布，非手动录入的评论样本",
        )

    _metric(
        comparison,
        "商品页图片数",
        before.product.image_count,
        after.product.image_count,
        "张",
        _OBSERVED,
    )
    if before.price_amount is None or after.price_amount is None:
        comparison.notes.append("至少一次商品页没有可比较的单一售价；不以文字报价或假设售价替代。")

    prior_costs, costs = previous.inputs.product_costs, current.inputs.product_costs
    if prior_costs is not None and costs is not None:
        for label, field in (
            ("假设售价", "sale_price"),
            ("假设采购成本", "purchase"),
            ("假设头程成本", "inbound"),
            ("假设配送成本", "fulfillment"),
            ("假设仓储成本", "storage"),
        ):
            _metric(
                comparison,
                label,
                getattr(prior_costs, field),
                getattr(costs, field),
                "USD",
                _ASSUMED,
            )
        for label, prior_rate, rate in (
            ("假设广告费率", prior_costs.ad_pct, costs.ad_pct),
            ("假设退货费率", prior_costs.return_pct, costs.return_pct),
            ("假设佣金率", prior_costs.referral_pct, costs.referral_pct),
        ):
            if prior_rate is not None and rate is not None:
                _metric(comparison, label, prior_rate * 100, rate * 100, "%", _ASSUMED)

    prior_finance = prior_depth.analysis.finance if prior_depth.analysis else None
    finance = depth.analysis.finance if depth.analysis else None
    prior_override = prior_costs is not None and prior_costs.sale_price is not None
    override = costs is not None and costs.sale_price is not None
    if prior_finance and finance and prior_finance.currency == finance.currency == "USD":
        if prior_override != override:
            comparison.notes.append("两次利润模型的售价来源不同（商品页或手动覆盖），不比较结果。")
        else:
            basis = (
                "按用户覆盖售价与成本假设建模，非实际利润"
                if override
                else "按公开页售价与成本假设建模，非实际利润"
            )
            _metric(
                comparison,
                "测算单件利润",
                prior_finance.unit_profit,
                finance.unit_profit,
                "USD",
                basis,
            )
            _metric(comparison, "测算 ROI", prior_finance.roi_pct, finance.roi_pct, "%", basis)

    if not comparison.metrics:
        comparison.notes.append("没有同时具备可比较数值的商品指标。")


def _page_limit(run: AmazonResearchRun, item: AmazonEvidence) -> int | None:
    if run.mode == "store":
        scope = run.snapshot.store_scope
        return scope.asin_limit_per_section if scope else None
    scope = run.snapshot.category_scope
    if scope:
        return scope.main_limit if item.role == "main" else scope.child_asin_limit
    return None


def _page_identity(item: AmazonEvidence, run: AmazonResearchRun) -> str | None:
    try:
        url = normalize_source(run.mode, item.final_url or item.url)
    except (AmazonSourceError, ValueError):
        return None
    if run.mode == "store":
        return page_id(url)
    return f"{board_identity(url)}?pg={2 if url.endswith('?pg=2') else 1}"


def _page_samples(run: AmazonResearchRun) -> tuple[dict[str, AmazonEvidence], bool]:
    pages: dict[str, AmazonEvidence] = {}
    excluded = False
    kind = "section" if run.mode == "store" else "board"
    for item in run.snapshot.evidence:
        if item.kind != kind or item.code == "duplicate":
            continue
        count = len(item.products)
        cap = _page_limit(run, item)
        collected = (
            (item.status == "success" and count > 0)
            or (item.status == "empty" and count == 0)
            or (
                item.status == "incomplete"
                and item.code == "sample_limit"
                and cap is not None
                and cap > 0
                and count >= cap
            )
        )
        identity = _page_identity(item, run) if collected else None
        if (
            not identity
            or not item.collected_at
            or (cap is not None and cap <= 0)
            or item.observed_count not in {None, count}
            or not all(ASIN.fullmatch(row.asin) for row in item.products)
        ):
            excluded = True
            continue
        pages.setdefault(identity, item)
    return pages, excluded


def _compare_page_samples(
    comparison: ResearchRunComparison, current: AmazonResearchRun, previous: AmazonResearchRun
) -> None:
    before, excluded_before = _page_samples(previous)
    after, excluded_after = _page_samples(current)
    shared = before.keys() & after.keys()
    page = "店铺栏目" if current.mode == "store" else "榜单页面"
    if not shared:
        comparison.notes.append(f"没有两次均可靠采集的相同{page}，不比较商品数量。")
        return

    prior_asins: set[str] = set()
    asins: set[str] = set()
    limits: set[int] = set()
    unknown_limit = False
    for identity in shared:
        prior_item, item = before[identity], after[identity]
        prior_cap, cap = _page_limit(previous, prior_item), _page_limit(current, item)
        known_caps = [limit for limit in (prior_cap, cap) if limit is not None]
        limit = min(known_caps) if known_caps else None
        if limit is not None:
            limits.add(limit)
        unknown_limit |= prior_cap is None or cap is None
        prior_asins.update(row.asin.upper() for row in prior_item.products[:limit])
        asins.update(row.asin.upper() for row in item.products[:limit])

    _metric(
        comparison,
        "样本 ASIN 数（去重）",
        len(prior_asins),
        len(asins),
        "个",
        f"仅统计两次共同采集的 {len(shared)} 个{page}中的可比样本",
    )
    if limits:
        caps = "、".join(str(limit) for limit in sorted(limits))
        comparison.notes.append(f"每个{page}取两次已知采样上限中较小的值（{caps} 个 ASIN）。")
    if unknown_limit:
        comparison.notes.append("部分历史记录缺少采样上限；仅使用已保存的成功样本或明确空页。")
    if excluded_before or excluded_after or before.keys() != after.keys():
        comparison.notes.append("未采集、失败、范围不明或仅在一次采集的页面不计入数量。")
    comparison.notes.append("样本数量不是完整榜单或店铺的商品总数。")


def compare_research_runs(
    current: AmazonResearchRun, previous: AmazonResearchRun | None
) -> ResearchRunComparison:
    """Compare only evidence already saved in two same-owner research runs."""
    fields = set(AmazonResearchRunSummary.model_fields)
    result = ResearchRunComparison(
        current=AmazonResearchRunSummary(**current.model_dump(include=fields)),
        previous=(
            AmazonResearchRunSummary(**previous.model_dump(include=fields)) if previous else None
        ),
    )
    if previous is None:
        result.notes.append("暂无同来源的上一次调研，无法对比。")
    elif (
        current.mode != previous.mode
        or current.canonical_source != previous.canonical_source
        or current.snapshot.site != previous.snapshot.site
    ):
        result.notes.append("两次调研的类型、站点或来源不一致，无法对比。")
    elif current.mode == "product":
        _compare_product(result, current, previous)
    else:
        _compare_page_samples(result, current, previous)
    return result
