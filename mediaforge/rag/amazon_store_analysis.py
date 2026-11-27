from collections import defaultdict
from datetime import datetime

from mediaforge.models.amazon_research import AmazonResearchReport, AmazonStorePage
from mediaforge.models.amazon_research_run import (
    AmazonEvidence,
    AmazonResearchRunRequest,
    AmazonResearchSnapshot,
    AmazonStoreArchive,
    AmazonStoreIdentity,
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
            for name in ("rating", "rating_count", "image_url"):
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
