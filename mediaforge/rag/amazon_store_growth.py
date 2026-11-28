import re

from mediaforge.models.amazon_research_run import (
    AmazonEvidence,
    AmazonGrowthAction,
    AmazonGrowthFinding,
    AmazonGrowthSource,
    AmazonLifecycleSample,
    AmazonMarketListing,
    AmazonResearchSnapshot,
    AmazonStoreGrowth,
    AmazonStoreSample,
)
from mediaforge.rag.amazon_store_analysis import section_samples

_PACK = re.compile(r"\b(?:[2-9]|[1-9]\d)[ -]*packs?\b|\bpack of (?:[2-9]|[1-9]\d)\b", re.I)
_SEASON = re.compile(
    r"\b(?:holiday|autumn|winter|summer|spring|christmas|halloween|easter)\b|节日|圣诞|秋季|冬季",
    re.I,
)


def _source(section: AmazonEvidence, asin: str | None = None) -> AmazonGrowthSource:
    return AmazonGrowthSource(
        label=section.title_zh or section.title,
        url=section.final_url or section.url,
        collected_at=section.collected_at,
        asin=asin,
        section=section.key,
    )


def growth_review_targets(snapshot: AmazonResearchSnapshot) -> list[AmazonLifecycleSample]:
    performance = snapshot.lifecycle.performance if snapshot.lifecycle else None
    if performance is None:
        return []
    identities = snapshot.product_archive.identities if snapshot.product_archive else {}
    groups = {}
    ranked = sorted(
        performance.low_rating, key=lambda row: (row.rating, -row.rating_total, row.asin)
    )
    for item in ranked:
        identity = identities.get(item.asin)
        family = identity.family_asin if identity else None
        groups.setdefault(family or item.asin, item)
    # 同款共享评分时只从一个 ASIN 抽读公开短评，最多核对两款。
    return list(groups.values())[:2]


def _price_gap(
    snapshot: AmazonResearchSnapshot, sections: dict[str, AmazonEvidence]
) -> AmazonGrowthFinding:
    matrix = snapshot.product_matrix
    bands = matrix.price_bands if matrix else []
    nonempty = [index for index, band in enumerate(bands) if band.asins]
    gap = next(
        (index for index in range(nonempty[0] + 1, nonempty[-1]) if not bands[index].asins),
        None,
    ) if nonempty else None
    observed = (
        f"{len(matrix.prices)} 个有效 USD 报价中未见两端都有样本的空价带。"
        if matrix and matrix.prices else "没有足够的有效 USD 报价确认内部价带空档。"
    )
    sources = []
    if gap is not None and matrix:
        band = bands[gap]
        lower = max(nonempty_index for nonempty_index in nonempty if nonempty_index < gap)
        upper = min(nonempty_index for nonempty_index in nonempty if nonempty_index > gap)
        observed = (
            f"在 {len(matrix.prices)} 个有效 USD 报价中，"
            f"USD {band.min_cents / 100:g}–<{band.max_cents / 100:g} 无样本，两侧价带有报价。"
        )
        indices = (lower, upper)
    else:
        indices = sorted({nonempty[0], nonempty[-1]}) if nonempty else []
    if matrix:
        for index in indices:
            asin = bands[index].asins[0]
            quote = matrix.prices[asin]
            sources.append(_source(sections[quote.section], asin))
    return AmazonGrowthFinding(
        key="price-gap", title="价格空档", priority="medium" if gap is not None else "low",
        observed=observed,
        hypothesis="店内报价分布只说明当前样本。空档处的需求、竞争和成本仍待验证。",
        action="补齐该区间的同规格竞品报价及成本，按包件和单件口径各复核一遍。",
        sources=sources,
    )


def _assortment(
    sections: dict[str, AmazonEvidence], products: dict[str, AmazonStoreSample]
) -> AmazonGrowthFinding:
    options = []
    for section in sections.values():
        if section.status != "success" or section.role not in {"category", "collection"}:
            continue
        known = {}
        for card in section.products:
            if family := products[card.asin].family_asin:
                known.setdefault(family, card.asin)
        if known:
            options.append((section, list(known.values())))
    section, asins = min(options, key=lambda row: (len(row[1]), row[0].key), default=(None, []))
    title = (section.title_zh or section.title) if section else None
    return AmazonGrowthFinding(
        key="assortment", title="可扩展栏目", priority="medium" if section else "low",
        observed=f"可读品类或合集栏目中，{title} 只确认到 {len(asins)} 个款。"
        if section else "公开栏目没有足够的已确认款用于筛选。",
        hypothesis="款式少可能源于选品深度或未采全，扩展方向需要需求和规格数据。",
        action="从来源 ASIN 对照常见规格，在同栏目补采两期，核对竞品覆盖与目标售价。",
        sources=[_source(section, asin) for asin in asins[:2]] if section else [],
    )


def _bundle(sections: dict[str, AmazonEvidence]) -> AmazonGrowthFinding:
    for section in sections.values():
        for card in section.products:
            if match := _PACK.search(card.title):
                return AmazonGrowthFinding(
                    key="bundle", title="组合销售", priority="medium",
                    observed=f"{card.asin} 的公开标题标注 {match[0]}。",
                    hypothesis="店铺存在多件装表达。新组合需求、实际包件和单位价须核实。",
                    action="核对当前包内数量和每件价，以同用途单件对照试算一个组合方案。",
                    sources=[_source(section, card.asin)],
                )
    return AmazonGrowthFinding(
        key="bundle", title="组合销售", priority="low",
        observed="已采标题未确认多件装、组合数量或搭配关系。",
        hypothesis="可否做组合销售待验证。",
        action="先记录同用途商品的使用场景及真实包件数量。",
    )


def _quality(
    snapshot: AmazonResearchSnapshot,
    sections: dict[str, AmazonEvidence],
    details: dict[str, AmazonMarketListing],
) -> AmazonGrowthFinding:
    targets = growth_review_targets(snapshot)
    observations, sources = [], []
    for item in targets:
        observations.append(f"{item.asin} 为 {item.rating:g} 星、{item.rating_total} 个公开评分。")
        sources.append(_source(sections[item.section], item.asin))
        if detail := details.get(item.asin):
            count = detail.review_sample_count
            topics = "、".join(
                f"{topic.label}提及 {topic.matches} 条" for topic in detail.review_topics
            )
            observations.append(detail.problem or (
                f"公开短评样本 {count} 条，{topics or '未识别主题'}。"
                if count else "公开详情未读到可用于分析的短评。"
            ))
            sources.append(AmazonGrowthSource(
                label="公开详情与短评", url=detail.url, asin=item.asin,
                collected_at=detail.collected_at,
            ))
    return AmazonGrowthFinding(
        key="quality", title="低评分与质量风险", priority="high" if targets else "low",
        observed="".join(observations) or "已采样本未达到低评分判断的最低样本门槛。",
        hypothesis="低评分需结合规格、批次和星级短评复核。主题提及不能单独确定负面原因。",
        action="按来源 ASIN 抽查低星原文，核对实物、尺码或做工及可取得的退货记录。",
        sources=sources,
    )


def _seasonal_source(
    sections: dict[str, AmazonEvidence],
) -> tuple[str | None, list[AmazonGrowthSource]]:
    for section in sections.values():
        if section.status not in {"success", "empty"}:
            continue
        if _SEASON.search(section.title):
            return section.title_zh or section.title, [_source(section)]
        for label, url in section.links:
            if _SEASON.search(label):
                # 导航可读取时保留该页证据，目标栏目即使未采到商品仍可核对导航。
                return label, [
                    AmazonGrowthSource(label=label, url=url, collected_at=section.collected_at),
                    _source(section),
                ]
    return None, []


def _risks(sections: dict[str, AmazonEvidence]) -> tuple[AmazonGrowthFinding, AmazonGrowthFinding]:
    branded = next(
        ((section, card) for section in sections.values()
         for card in section.products if card.brand),
        None,
    )
    seasonal, seasonal_sources = _seasonal_source(sections)
    return (
        AmazonGrowthFinding(
            key="ip", title="知识产权风险", priority="medium" if branded else "low",
            observed=f"{branded[1].asin} 的公开品牌字段为 {branded[1].brand}。"
            if branded else "当前公开样本未确认品牌或权属记录。",
            hypothesis="公开品牌与商品展示无法确定商标、外观或素材的授权范围。",
            action="规划款式和内容前，以自有素材和已核实的品牌、角色及外观授权为准。",
            sources=[_source(branded[0], branded[1].asin)] if branded else [],
        ),
        AmazonGrowthFinding(
            key="seasonality", title="季节风险", priority="medium" if seasonal else "low",
            observed=f"公开导航或标题可见 {seasonal} 栏目。"
            if seasonal else "已读栏目未见清晰的节庆或季节主题，历史需求未知。",
            hypothesis="题名是陈列信号，季节需求、活动及备货周期需跨时段验证。",
            action="按同一栏目与 ASIN 记录节前、节中和节后报价、促销与可取得的销量。",
            sources=seasonal_sources,
        ),
    )


def build_store_growth(
    snapshot: AmazonResearchSnapshot,
    samples: list[AmazonStoreSample],
    review_details: dict[str, AmazonMarketListing],
) -> AmazonStoreGrowth:
    sections = {
        item.key: item for item in section_samples(snapshot) if item.code != "duplicate"
    }
    ip, seasonality = _risks(sections)
    return AmazonStoreGrowth(
        opportunities=[
            _price_gap(snapshot, sections),
            _assortment(sections, {item.asin: item for item in samples}),
            _bundle(sections),
        ],
        risks=[_quality(snapshot, sections, review_details), ip, seasonality],
        plan=[
            AmazonGrowthAction(day=30, findings=["quality", "ip", "seasonality"],
                               action="补齐低星原文、规格和权属证据，建立同栏目观察基线。"),
            AmazonGrowthAction(day=60, findings=["price-gap", "assortment", "bundle"],
                               action="对可支持的报价、品类和组合假设做同规格对标及成本试算。"),
            AmazonGrowthAction(day=90, findings=["assortment", "quality", "seasonality"],
                               action="复采相同栏目，合并可取得的销量及退货证据，复核试行取舍。"),
        ],
        review_details=review_details,
    )
