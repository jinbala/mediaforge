import re
from collections import defaultdict
from datetime import datetime
from decimal import Decimal
from itertools import combinations
from statistics import mean, median

from mediaforge.models.amazon_research import AmazonResearchReport, AmazonStorePage
from mediaforge.models.amazon_research_run import (
    AmazonEvidence,
    AmazonPriceBand,
    AmazonPriceObservation,
    AmazonRatedStyle,
    AmazonResearchRunRequest,
    AmazonResearchSnapshot,
    AmazonSectionMetrics,
    AmazonSectionOverlap,
    AmazonStoreArchive,
    AmazonStoreIdentity,
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


def public_usd_cents(product: AmazonStoreSample) -> int | None:
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
