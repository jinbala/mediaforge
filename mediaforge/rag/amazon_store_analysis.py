from collections import defaultdict
from datetime import datetime

from mediaforge.models.amazon_research import AmazonResearchReport, AmazonStorePage
from mediaforge.models.amazon_research_run import (
    AmazonEvidence,
    AmazonResearchRunRequest,
    AmazonResearchSnapshot,
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
            sources[product.asin].append(section.key)
    return dict(sources)


def archive(snapshot: AmazonResearchSnapshot) -> list[AmazonStoreSample]:
    # 栏目卡片按原样保留，汇总按 ASIN 合并。规格键只用同一平台规格表的关系合并。
    sections = section_samples(snapshot)
    by_asin = {}
    families: dict[str, str] = {}
    colors: dict[str, str] = {}
    display_groups: dict[str, str] = {}

    def representative(groups: dict[str, str], member: str) -> str:
        while member in groups:
            member = groups[member]
        return member

    def merge(groups: dict[str, str], left: str, right: str) -> None:
        first, second = sorted((representative(groups, left), representative(groups, right)))
        if first != second:
            groups[second] = first

    for item in sections:
        if item.code == "duplicate":
            continue
        for sample in item.products:
            current = by_asin.setdefault(sample.asin, sample.model_copy(deep=True))
            for name in (
                "store_variant",
                "family_asin",
                "color_group_asin",
                "color",
                "size",
                "rating",
                "rating_count",
                "image_url",
            ):
                if getattr(current, name) is None:
                    setattr(current, name, getattr(sample, name))
            if current.price is None and sample.price is not None:
                current.price = sample.price
                current.price_currency = sample.price_currency
                current.price_label = sample.price_label
            if current.title == current.asin:
                current.title = sample.title
                current.title_zh = sample.title_zh
            if sample.family_asin:
                merge(families, sample.asin, sample.family_asin)
            if sample.color_group_asin:
                merge(colors, sample.asin, sample.color_group_asin)
            if sample.store_variant:
                merge(display_groups, sample.asin, sample.store_variant.group_asin)

    for asin, sample in by_asin.items():
        if sample.family_asin:
            sample.family_asin = representative(families, asin)
        if sample.color_group_asin:
            sample.color_group_asin = representative(colors, asin)
        if sample.store_variant:
            sample.store_variant.group_asin = representative(display_groups, asin)
    return list(by_asin.values())


def store_public_report(
    inputs: AmazonResearchRunRequest,
    snapshot: AmazonResearchSnapshot,
    started_at: datetime,
) -> AmazonResearchReport:
    sections = section_samples(snapshot)
    first = sections[0] if sections else None
    root = next(
        (item for item in sections if snapshot.store_root in {item.url, item.final_url}), None
    )
    heading = (root if root and root.collected_at else None) or first
    listings = archive(snapshot)
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
