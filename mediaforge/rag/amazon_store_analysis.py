import re
from collections import Counter, defaultdict
from datetime import datetime
from decimal import Decimal
from itertools import combinations
from statistics import mean, median
from uuid import UUID

from mediaforge.models.amazon_research import AmazonResearchReport, AmazonStorePage
from mediaforge.models.amazon_research_run import (
    AmazonBenchmarkPair,
    AmazonBenchmarkSearch,
    AmazonEvidence,
    AmazonMarketListing,
    AmazonPriceBand,
    AmazonPriceObservation,
    AmazonRatedStyle,
    AmazonResearchRunRequest,
    AmazonResearchSnapshot,
    AmazonSectionMetrics,
    AmazonSectionOverlap,
    AmazonSeriesChange,
    AmazonStoreArchive,
    AmazonStoreChange,
    AmazonStoreIdentity,
    AmazonStoreLifecycle,
    AmazonStoreProductMatrix,
    AmazonStoreSample,
)


def section_samples(snapshot: AmazonResearchSnapshot) -> list[AmazonEvidence]:
    return [item for item in snapshot.evidence if item.kind == "section"]


def section_index(snapshot: AmazonResearchSnapshot) -> dict[str, list[str]]:
    sources = defaultdict(list)
    for section in section_samples(snapshot):
        if section.code == "duplicate":
            continue
        for product in section.products:
            if section.key not in sources[product.asin]:
                sources[product.asin].append(section.key)
    return dict(sources)


# 同一规格的长短写法可互认，其他名称变化保留为冲突。
_SIZE_ALIASES = {
    "x-small": "xs",
    "small": "s",
    "medium": "m",
    "large": "l",
    "x-large": "xl",
    "xx-large": "xxl",
    "2xl": "xxl",
}


def _known_values(
    observations: dict[str, list[AmazonStoreSample]],
    field: str,
) -> dict[str, str]:
    result = {}
    for asin, products in observations.items():
        values = [value for product in products if (value := getattr(product, field))]
        keys = {" ".join(value.split()).casefold() for value in values}
        if field == "size":
            keys = {_SIZE_ALIASES.get(key, key) for key in keys}
        if len(keys) == 1:
            result[asin] = values[0]
    return result


class _VariantGroups:
    def __init__(self) -> None:
        self.parents: dict[str, str] = {}

    def root(self, member: str) -> str:
        while member in self.parents:
            member = self.parents[member]
        return member

    def join(self, member: str, members: list[str]) -> None:
        for related in members:
            first, second = sorted((self.root(member), self.root(related)))
            if first != second:
                self.parents[second] = first


def archive(snapshot: AmazonResearchSnapshot) -> list[AmazonStoreSample]:
    # 以同一 ASIN 汇总读到的卡片。只有 Amazon 规格表允许关联不同 ASIN。
    observations = defaultdict(list)
    for section in section_samples(snapshot):
        if section.code != "duplicate":
            for product in section.products:
                observations[product.asin].append(product)
    sizes = _known_values(observations, "size")
    colors = _known_values(observations, "color")
    color_conflicts = {
        asin
        for asin, rows in observations.items()
        if asin not in colors and any(row.color for row in rows)
    }
    size_conflicts = {
        asin
        for asin, rows in observations.items()
        if asin not in sizes and any(row.size for row in rows)
    }

    blocked_folds = color_conflicts | size_conflicts

    def color_key(asin: str, value: str | None) -> str:
        return f"{' '.join(value.split()).casefold() if value else ''}\x1f{asin}"

    styles, color_groups, size_groups = _VariantGroups(), _VariantGroups(), _VariantGroups()
    for asin, rows in observations.items():
        for sample in rows:
            if sample.family_asin:
                styles.join(asin, [sample.family_asin, *sample.family_members])
            tone = colors.get(asin)
            # 分组键包含平台颜色名，防止重叠的部分规格表串起不同的配色。
            if sample.color_group_asin and tone:
                color_groups.join(
                    color_key(asin, tone),
                    [
                        color_key(key, tone)
                        for key in [sample.color_group_asin, *sample.color_members]
                        if key not in color_conflicts
                    ],
                )
            if sample.store_variant and asin not in blocked_folds:
                size_groups.join(
                    color_key(asin, tone),
                    [
                        color_key(key, tone)
                        for key in [sample.store_variant.group_asin, *sample.size_group_members]
                        if key not in blocked_folds
                    ],
                )
    # 未出现在栏目卡片上的规格只用于确认关系，确认范围不扩成全店在售目录。
    sibling_evidence = {
        styles.root(asin)
        for asin, rows in observations.items()
        if any(row.family_asin for row in rows)
    }
    color_identifiers = {}
    for asin in sorted(colors):
        rows = observations[asin]
        if any(row.color_group_asin for row in rows):
            key = color_groups.root(color_key(asin, colors[asin]))
            color_identifiers.setdefault(key, asin)

    result = []
    for asin, rows in observations.items():
        current = rows[0].model_copy(deep=True)
        for sample in rows[1:]:
            for name in ("rating", "rating_count", "image_url", "product_type"):
                if getattr(current, name) is None:
                    setattr(current, name, getattr(sample, name))
            if current.store_variant is None and sample.store_variant:
                current.store_variant = sample.store_variant.model_copy()
            if current.price is None and sample.price is not None:
                current.price, current.price_currency, current.price_label = (
                    sample.price,
                    sample.price_currency,
                    sample.price_label,
                )
            if current.title == current.asin:
                current.title, current.title_zh = sample.title, sample.title_zh
        family = styles.root(asin)
        standalone = (
            family not in sibling_evidence
            and {row.has_variants for row in rows}
            <= {
                None,
                False,
            }
            and any(row.has_variants is False for row in rows)
        )
        current.family_asin = family if family in sibling_evidence or standalone else None
        current.has_variants = False if standalone else None
        current.color, current.size = colors.get(asin), sizes.get(asin)
        variant_key = color_key(asin, current.color)
        current.color_group_asin = color_identifiers.get(color_groups.root(variant_key))
        if asin in blocked_folds:
            current.store_variant = None
        if current.store_variant:
            current.store_variant.group_asin = size_groups.root(variant_key).rsplit("\x1f", 1)[1]
        result.append(current)
    return result


def summarize_archive(products: list[AmazonStoreSample]) -> AmazonStoreArchive:
    identities = {
        item.asin: AmazonStoreIdentity(
            family_asin=item.family_asin,
            color_group_asin=item.color_group_asin,
            color=item.color,
            size=item.size,
            standalone=item.has_variants is False,
        )
        for item in products
    }
    return AmazonStoreArchive(
        asin_count=len(identities),
        family_count=len({item.family_asin for item in identities.values() if item.family_asin}),
        color_group_count=len(
            {item.color_group_asin for item in identities.values() if item.color_group_asin}
        ),
        unknown_family_count=sum(item.family_asin is None for item in identities.values()),
        unknown_color_count=sum(item.color_group_asin is None for item in identities.values()),
        unknown_size_count=sum(item.size is None for item in identities.values()),
        identities=identities,
    )


PRICE_BOUNDS_CENTS = (0, 1500, 2500, 4000, 6000)
_USD_PRICE = re.compile(r"(?:US\s*)?\$(\d{1,3}(?:,\d{3}){1,2}|\d{1,7})(?:\.(\d{2}))?")
_COUNT = re.compile(r"(?:\d{1,3}(?:,\d{3}){1,3}|\d{1,10})")
_MODEL_SUFFIX = re.compile(r"(?<![\w.])([A-Z]{1,3}-?\d{3,7}|\d{4,7})\s*$")


def public_usd_cents(product: AmazonStoreSample | AmazonMarketListing) -> int | None:
    # 仅使用单个明确的美元报价，保留区间、非 USD 和无法识别的原始金额供复核。
    if product.price_currency not in {None, "USD"}:
        return None
    match = _USD_PRICE.fullmatch((product.price or "").strip())
    if match is None:
        return None
    cents = int(match[1].replace(",", "")) * 100 + int(match[2] or 0)
    return cents if cents > 0 else None


def _rating_value(product: AmazonStoreSample) -> Decimal | None:
    if re.fullmatch(r"[0-5](?:\.\d{1,2})?", product.rating or ""):
        result = Decimal(product.rating)
        return result if 0 < result <= 5 else None
    return None


def _rating_total(product: AmazonStoreSample) -> int | None:
    if _COUNT.fullmatch(product.rating_count or ""):
        return int(product.rating_count.replace(",", ""))
    return None


def _product_section_metrics(section: AmazonEvidence) -> AmazonSectionMetrics:
    scores = [score for row in section.products if (score := _rating_value(row)) is not None]
    totals = [count for row in section.products if (count := _rating_total(row)) is not None]
    return AmazonSectionMetrics(
        section=section.key,
        asin_count=section.observed_count,
        priced_count=sum(public_usd_cents(row) is not None for row in section.products),
        rating_count=len(scores),
        average_rating=float(round(mean(scores), 2)) if scores else None,
        rating_total_count=len(totals),
        median_rating_total=median(totals) if totals else None,
    )


def build_product_matrix(
    snapshot: AmazonResearchSnapshot,
    products: list[AmazonStoreSample],
) -> AmazonStoreProductMatrix:
    sections = [item for item in section_samples(snapshot) if item.code != "duplicate"]
    prices = {}
    with_price_text = set()
    memberships = {}
    for section in sections:
        memberships[section.key] = {row.asin for row in section.products}
        for row in section.products:
            if row.price:
                with_price_text.add(row.asin)
            # 同一 ASIN 按栏目登记顺序取第一个有效美元报价，并保存使用的栏目。
            if row.asin not in prices and (cents := public_usd_cents(row)) is not None:
                prices[row.asin] = AmazonPriceObservation(section=section.key, price_cents=cents)
    bands = []
    for low, high in zip(PRICE_BOUNDS_CENTS, (*PRICE_BOUNDS_CENTS[1:], None), strict=True):
        members = sorted(
            asin
            for asin, quote in prices.items()
            if quote.price_cents >= low and (high is None or quote.price_cents < high)
        )
        bands.append(
            AmazonPriceBand(
                min_cents=low,
                max_cents=high,
                asins=members,
                share_pct=round(100 * len(members) / len(prices), 1) if prices else None,
            )
        )

    overlaps = [
        AmazonSectionOverlap(sections=(first, second), asins=sorted(shared))
        for first, second in combinations(memberships, 2)
        if (shared := memberships[first] & memberships[second])
    ]
    types, specs, models = defaultdict(list), defaultdict(list), defaultdict(list)
    leaders = []
    for product in products:
        if product.product_type:
            types[product.product_type].append(product.asin)
        if product.color and product.size:
            size_key = product.size.casefold()
            size = _SIZE_ALIASES.get(size_key, product.size)
            specs[f"{product.color} / {size}"].append(product.asin)
        # 商品标题末尾标准编号仅用于提示复核，不能确认是型号或系列。
        if (match := _MODEL_SUFFIX.search(product.title)) and not re.fullmatch(
            r"(?:19|20)\d{2}",
            match[1],
        ):
            models[match[1]].append(product.asin)
        if (total := _rating_total(product)) is not None:
            leaders.append(
                AmazonRatedStyle(
                    asin=product.asin,
                    count=total,
                    family_asin=product.family_asin,
                )
            )
    ordered_leaders = sorted(leaders, key=lambda row: (-row.count, row.asin))
    by_style = {}
    for row in ordered_leaders:
        # 同款可能共用评分池，只展示最高公开评分数的 ASIN，避免把变体评分数相加。
        by_style.setdefault(row.family_asin or row.asin, row)
    return AmazonStoreProductMatrix(
        asin_count=len(products),
        prices=prices,
        price_bands=bands,
        missing_price=sorted(
            product.asin for product in products if product.asin not in with_price_text
        ),
        missing_eligible_price=sorted(with_price_text - prices.keys()),
        sections=[_product_section_metrics(section) for section in sections],
        overlaps=overlaps,
        product_types=dict(types),
        specification_combinations=dict(specs),
        model_candidates=dict(models),
        rating_leaders=list(by_style.values())[:5],
        new_sections={
            section.key: sorted(memberships[section.key])
            for section in sections
            if section.role == "new"
        },
    )


def _complete_sections(snapshot: AmazonResearchSnapshot) -> dict[str, AmazonEvidence]:
    return {
        item.key: item
        for item in section_samples(snapshot)
        if item.status in {"success", "empty"} and item.collected_at
    }


def compare_store_samples(
    current: AmazonResearchSnapshot,
    history: list[tuple[UUID, AmazonResearchSnapshot]],
) -> AmazonStoreChange | None:
    current_pages = _complete_sections(current)
    for run_id, previous in history:
        prior_pages = _complete_sections(previous)
        shared = sorted(current_pages.keys() & prior_pages.keys())
        if not shared:
            continue

        def portfolio(pages: dict[str, AmazonEvidence]) -> tuple[set[str], dict[str, set[str]]]:
            sample = AmazonResearchSnapshot(evidence=[pages[key] for key in shared])
            products = archive(sample)
            families = defaultdict(set)
            for product in products:
                if product.family_asin and product.has_variants is not False:
                    families[product.family_asin].add(product.asin)
            return {item.asin for item in products}, dict(families)

        before, before_families = portfolio(prior_pages)
        after, after_families = portfolio(current_pages)
        # 只对双方都能确认的规格组描述样本变化，避免把证据缺失当成产品停产。
        series = [
            AmazonSeriesChange(
                family_asin=family,
                newly_seen=sorted(after_families[family] - before_families[family]),
                no_longer_seen=sorted(before_families[family] - after_families[family]),
            )
            for family in sorted(before_families.keys() & after_families.keys())
            if before_families[family] != after_families[family]
        ]
        return AmazonStoreChange(
            previous_run_id=run_id,
            previous_collected_at=max(prior_pages[key].collected_at for key in shared),
            current_collected_at=max(current_pages[key].collected_at for key in shared),
            section_keys=shared,
            newly_seen=sorted(after - before),
            no_longer_seen=sorted(before - after),
            series_changes=series,
        )
    return None


def build_store_lifecycle(
    products: list[AmazonStoreSample],
    change: AmazonStoreChange | None,
) -> AmazonStoreLifecycle:
    colors = Counter(product.color for product in products if product.color)
    sizes = Counter(
        _SIZE_ALIASES.get(product.size.casefold(), product.size).upper()
        for product in products
        if product.size
    )
    return AmazonStoreLifecycle(
        colors=dict(colors),
        sizes=dict(sizes),
        missing_color=len(products) - sum(colors.values()),
        missing_size=len(products) - sum(sizes.values()),
        change=change,
    )


_MARKET_STOP_WORDS = {
    "the",
    "with",
    "and",
    "for",
    "pack",
    "new",
    "amazon",
    "women",
    "woman",
    "womens",
    "men",
    "mens",
    "size",
    "color",
    "small",
    "medium",
    "large",
}
_MARKET_AUDIENCES = {
    "women": re.compile(r"\b(?:women(?:'s|s)?|ladies)\b", re.I),
    "men": re.compile(r"\bmen(?:'s|s)?\b", re.I),
    "children": re.compile(r"\b(?:kids|children|toddler)\b", re.I),
}


def benchmark_targets(
    snapshot: AmazonResearchSnapshot,
    products: list[AmazonStoreSample],
) -> list[AmazonStoreSample]:
    # 代表样本优先来自畅销栏。每种平台类型最多一个，避免同款颜色占完对标预算。
    popular = {
        item.asin
        for section in section_samples(snapshot)
        if section.role == "popular"
        for item in section.products
    }
    selected = {}
    for sample in sorted(products, key=lambda item: item.asin not in popular):
        if sample.brand and sample.product_type and sample.title != sample.asin:
            selected.setdefault(sample.product_type, sample)
    return list(selected.values())[:2]


def _market_audience(title: str) -> str | None:
    return next(
        (kind for kind, pattern in _MARKET_AUDIENCES.items() if pattern.search(title)), None
    )


def _market_terms(title: str) -> set[str]:
    words = set(re.findall(r"[a-z]{3,}", title.casefold())) - _MARKET_STOP_WORDS
    return {word.removesuffix("s") if not word.endswith("ss") else word for word in words}


def market_query(sample: AmazonStoreSample, details: AmazonMarketListing) -> str | None:
    if not details.title or details.problem:
        return None
    title = details.title.casefold().replace("-", " ").replace("’", "'")
    audience = _market_audience(title)
    style = next(
        (
            term
            for term in (
                "maternity nursing",
                "long sleeve",
                "short sleeve",
                "wide leg",
                "mock neck",
                "crew neck",
                "race car",
                "starship",
                "building blocks",
            )
            if term in title
        ),
        "",
    )
    fallback = (sample.product_type or "").replace("_", " ").lower()
    category = details.category or fallback
    if category.casefold() in {"clothing", "women", "men", "toys & games"}:
        category = fallback
    prefix = {"women": "women's", "men": "men's", "children": "kids"}.get(audience, "")
    return " ".join(part for part in (prefix, style, category) if part)[:100] or None


def benchmark_candidates(
    search: AmazonBenchmarkSearch,
    target: AmazonMarketListing,
    own_asins: set[str],
    own_brands: set[str],
) -> list[str]:
    target_title = target.title or ""
    audience = _market_audience(target_title)
    target_terms = _market_terms(target_title) - _market_terms(target.brand or "")
    scored = []
    for index, sample in enumerate(search.candidates):
        candidate_audience = _market_audience(sample.title)
        if sample.asin in own_asins or (audience and candidate_audience not in {None, audience}):
            continue
        if any(
            brand and re.search(rf"\b{re.escape(brand)}\b", sample.title.casefold())
            for brand in own_brands
        ):
            continue
        shared = target_terms & _market_terms(sample.title)
        if len(shared) >= 2:
            scored.append((-len(shared), index, sample.asin))
    # 同一搜索中先核对标题更接近的前两件，关键词来自页面，未标注为搜索量。
    return [asin for _, _, asin in sorted(scored)[:2]]


def build_benchmark_pair(
    target: AmazonMarketListing,
    rival: AmazonMarketListing,
) -> AmazonBenchmarkPair | None:
    if not target.title or not rival.title or not target.brand or not rival.brand:
        return None
    if target.brand.casefold() == rival.brand.casefold():
        return None
    own_audience, rival_audience = _market_audience(target.title), _market_audience(rival.title)
    if own_audience and rival_audience and own_audience != rival_audience:
        return None
    terms = sorted(
        (_market_terms(target.title) - _market_terms(target.brand))
        & (_market_terms(rival.title) - _market_terms(rival.brand))
    )
    same_category = (
        target.category
        and rival.category
        and (target.category.casefold() == rival.category.casefold())
    )
    # 类目不同或缺失时，需更多标题用词作为依据，明确标为待验证候选。
    if len(terms) < (2 if same_category else 4):
        return None
    left, right = public_usd_cents(target), public_usd_cents(rival)
    gap = left - right if left is not None and right is not None else None
    own_rating = (
        float(target.rating)
        if target.rating and re.fullmatch(r"[0-5](?:\.\d+)?", target.rating)
        else None
    )
    rival_rating = (
        float(rival.rating)
        if rival.rating and re.fullmatch(r"[0-5](?:\.\d+)?", rival.rating)
        else None
    )
    strengths = (
        "此款样本标价较低，可进一步核对同规格价值。"
        if gap is not None and gap < 0
        else "当前公开样本未支持价格优势。"
    )
    weakness = (
        "此款公开评分低于对标样本，可进一步核查反馈主题。"
        if own_rating is not None and rival_rating is not None and own_rating < rival_rating
        else "样本不足以确认相对评分劣势。"
    )
    opportunity = "按共同标题用词核查买家用途，再比较两款的可验证规格和卖点。"
    threat = (
        "对标样本标价较低，核对套装、尺寸或材质差异后再比较。"
        if gap is not None and gap > 0
        else "竞争威胁待验证，当前覆盖少量公开样本。"
    )
    return AmazonBenchmarkPair(
        target_asin=target.asin,
        competitor_asin=rival.asin,
        shared_terms=terms,
        shared_category=target.category if same_category else None,
        audience={"women": "女性款标记", "men": "男性款标记", "children": "儿童款标记"}.get(
            own_audience
        )
        if own_audience and own_audience == rival_audience
        else None,
        price_gap_cents=gap,
        swot={"优势": strengths, "劣势": weakness, "机会": opportunity, "威胁": threat},
    )


def store_public_report(
    inputs: AmazonResearchRunRequest,
    snapshot: AmazonResearchSnapshot,
    started_at: datetime,
    listings: list[AmazonStoreSample],
) -> AmazonResearchReport:
    sections = section_samples(snapshot)
    first = sections[0] if sections else None
    root = next(
        (item for item in sections if snapshot.store_root in {item.url, item.final_url}), None
    )
    heading = (root if root and root.collected_at else None) or first
    return AmazonResearchReport(
        mode="store",
        source_url=snapshot.store_root or inputs.source,
        title=heading.title if heading else "Amazon 品牌店铺",
        title_zh=heading.title_zh if heading else None,
        collected_at=started_at,
        listings=listings,
        store_pages=[
            AmazonStorePage(
                title=item.title, title_zh=item.title_zh, url=item.final_url or item.url
            )
            for item in sections
            if item.code != "duplicate"
        ],
        keyword=inputs.keyword or None,
        suggestions=snapshot.suggestions,
        warnings=snapshot.notes,
    )
