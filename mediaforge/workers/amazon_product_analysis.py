import re
import unicodedata
from collections import Counter, defaultdict

from mediaforge.models.amazon_product import (
    ProductAnalysis,
    ProductCitation,
    ProductCosts,
    ProductDepth,
    ProductFinance,
    ProductPage,
    ProductReviewSample,
    ProductSection,
    ProductShot,
    ProductTable,
    ProductTemplate,
)
from mediaforge.workers.amazon_product_finance import analyze_product_finance


def _table(title, columns, rows) -> ProductTable:
    return ProductTable(title=title, columns=columns.split("｜"), rows=list(rows))


def _page_source(page: ProductPage, label: str) -> ProductCitation:
    return ProductCitation(
        label=label, url=page.product.url, asin=page.product.asin, collected_at=page.collected_at
    )


def _search_sources(depth: ProductDepth) -> list[ProductCitation]:
    return [
        ProductCitation(label=f"搜索：{s.query}", url=s.url, collected_at=s.collected_at)
        for s in depth.searches
    ]


def _section(req, title, status, summary, tables, sources=()) -> ProductSection:
    seen = set()
    unique = []
    for source in sources:
        key = (source.label, source.url, source.asin, source.collected_at)
        if key not in seen:
            unique.append(source)
            seen.add(key)
    return ProductSection(
        req=f"REQ-{req:03}",
        title=title,
        status=status,
        summary=summary,
        tables=tables,
        sources=unique,
    )


def _joined(values) -> str | None:
    return "；".join(str(value) for value in values if value) or None


def _bsrs(page: ProductPage) -> list[str]:
    return page.product.sales_ranks or ([page.product.rank] if page.product.rank else [])


def _bullets(page: ProductPage) -> list[str]:
    return [text for text in page.product.highlights if text.strip()]


def _overview(page: ProductPage | None) -> ProductSection:
    if page is None:
        return _section(21, "商品档案与市场快照", "unknown", ["未取得目标商品页。"], [])
    p = page.product
    aplus_excerpt = _joined([text.strip()[:160] for text in page.aplus if text.strip()][:3])
    images = [(m.url, m.label, m.width, m.height, m.poster) for m in page.images]
    if p.image_url and all(p.image_url != row[0] for row in images):
        images.insert(0, (p.image_url, None, None, None, None))
    video_count = len({m.url for m in page.videos}) or None
    values = [
        ("ASIN", p.asin, ("asin",)),
        ("标题", p.title or None, ("title",)),
        ("品牌", p.brand, ("brand",)),
        ("品牌店", p.store_url, ("store_url", "brand")),
        ("类目路径", _joined(p.category_path or []) or p.category, ("category_path", "category")),
        ("公开标价（原样）", p.price, ("price", "price_amount")),
        ("价格金额", page.price_amount, ("price_amount", "price")),
        ("币种", page.currency, ("currency", "price_amount", "price")),
        ("公开评分", p.rating, ("rating",)),
        ("公开评分数", p.rating_count, ("rating_count",)),
        ("类目 BSR（按原类目展示）", _joined(_bsrs(page)), ("sales_ranks", "rank")),
        ("首次可售日期（页面原文）", p.first_available, ("first_available",)),
        ("父 ASIN", page.parent_asin, ("parent_asin",)),
        ("观察到的变体 ASIN", _joined(page.variant_asins), ("variant_asins",)),
        ("重要信息", _joined(page.important), ("important",)),
        ("A+ 状态", _aplus(page), ("aplus",)),
        ("A+ 内容摘要（最多 3 段摘录）", aplus_excerpt, ("aplus",)),
        (
            "图片数（页面计数或可见链接）",
            _photo_count(page),
            ("image_count", "images", "image_url"),
        ),
        ("已采视频链接数", video_count, ("videos",)),
    ]

    def evidence(*keys):
        return next(
            (page.field_evidence[key] for key in keys if page.field_evidence.get(key)), p.url
        )

    status = (
        "complete"
        if all(
            (
                p.title,
                p.brand,
                p.category_path or p.category,
                p.rating,
                p.rating_count,
                _bsrs(page),
                page.currency,
                video_count,
            )
        )
        and (
            page.price_amount is not None
            and _aplus(page) is not None
            and _photo_count(page) is not None
        )
        else "partial"
    )
    return _section(
        21,
        "商品档案与市场快照",
        status,
        [
            "数据来自本次美国站页面；评分数不是文字评论数，BSR 不是销量。",
            "未取得的字段保留空值，不据此认定该字段在页面上不存在。"
            "字段证据优先列采集定位，未记录定位时引用商品页。",
            "A+ 为页面短摘录；媒体仅列已采链接和元数据，未检查画面或视频内容。",
        ],
        [
            _table(
                "商品公开字段",
                "项目｜观察值｜字段证据",
                (
                    (label, value, evidence(*keys) if value is not None else None)
                    for label, value, keys in values
                ),
            ),
            _table(
                "规格原文",
                "规格项｜页面值｜字段证据",
                ((key, value, evidence("specs")) for key, value in sorted(page.specs.items())),
            ),
            _table(
                "已采图组与视频",
                "类型｜链接｜标签｜宽｜高｜封面｜字段证据",
                [
                    *(("图片", *row, evidence("images", "image_url")) for row in images),
                    *(
                        ("视频", m.url, m.label, m.width, m.height, m.poster, evidence("videos"))
                        for m in page.videos
                    ),
                ],
            ),
        ],
        [_page_source(page, "目标商品页")],
    )


def _tokens(text: str) -> list[str]:
    return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())


def _phrase(query: str, *texts: str) -> str | None:
    query_tokens = _tokens(query)
    fields = [tokens for text in texts if (tokens := _tokens(text))]
    if not query_tokens or not fields:
        return None
    needle = " " + " ".join(query_tokens) + " "
    return "出现" if any(needle in " " + " ".join(tokens) + " " for tokens in fields) else "未出现"


def _ad(flag: bool | None) -> str | None:
    return "赞助" if flag is True else "非赞助" if flag is False else None


_INTENT_MODIFIERS = {"for", "with", "without", "fits", "compatible", "pack", "set", "count"}
_QUERY_CONNECTORS = _INTENT_MODIFIERS | {
    "a",
    "an",
    "and",
    "at",
    "by",
    "in",
    "of",
    "on",
    "or",
    "the",
    "to",
}
_KeywordCoverage = tuple[str, str | None, str | None, str | None, str]


def _keywords(depth: ProductDepth) -> tuple[ProductSection, list[_KeywordCoverage]]:
    page = depth.overview
    asin = page.product.asin if page else None
    candidates = {item.asin for item in depth.candidates}
    candidates.update(item.product.asin for item in depth.competitors)
    bullets = _bullets(page) if page else []
    target_text = (page.product.title, *bullets, page.product.description or "") if page else ()
    target_tokens = {token for text in target_text for token in _tokens(text)}
    matrix, positions, brands, price_ranges, opportunities, coverage = [], [], [], [], [], []
    seen_opportunities = set()
    for search in depth.searches:
        target = [row for row in search.rows if row.asin == asin]
        title_match = _phrase(search.query, page.product.title) if page else None
        bullet_match = _phrase(search.query, *bullets)
        description_match = _phrase(search.query, page.product.description or "") if page else None
        coverage.append((search.query, title_match, bullet_match, description_match, search.url))
        matrix.append(
            (
                search.query,
                len(search.rows),
                len({row.asin for row in search.rows}),
                search.result_min,
                search.result_max,
                search.result_label,
                min((row.rank for row in target if row.sponsored is False), default=None),
                min((row.rank for row in target if row.sponsored is True), default=None),
                min((row.rank for row in target if row.sponsored is None), default=None),
                "已观测" if target else "样本内未见" if asin and search.rows else None,
                title_match,
                bullet_match,
                description_match,
                search.url,
            )
        )
        query_tokens = _tokens(search.query)
        query_key = tuple(query_tokens)
        anchors = [
            token
            for token in dict.fromkeys(query_tokens)
            if token in target_tokens and token not in _QUERY_CONNECTORS and not token.isdecimal()
        ]
        if (
            search.rows
            and query_key not in seen_opportunities
            and anchors
            and (
                target
                or "出现" in (title_match, bullet_match, description_match)
                or len(anchors) >= 2
            )
        ):
            intent = next(
                (token for token in query_tokens[1:-1] if token in _INTENT_MODIFIERS), None
            )
            if not intent:
                intent = next((token for token in query_tokens if token.isdecimal()), None)
            is_long_tail = bool(intent and len(query_tokens) >= 3)
            if is_long_tail or len(query_tokens) <= 3:
                seen_opportunities.add(query_key)
                opportunities.append(
                    (
                        "高意向长尾（规则候选）" if is_long_tail else "短词",
                        search.query,
                        " ".join(anchors),
                        len(search.rows),
                        "目标卡片已见" if target else "未见目标卡片；目标文案匹配",
                        f"用途/适配/数量修饰词：{intent}" if is_long_tail else None,
                        "已覆盖；验证相关流量"
                        if "出现" in (title_match, bullet_match, description_match)
                        else "核实用途及规格后再测试完整词组",
                        search.url,
                    )
                )
        # 同一 ASIN 在同一查询可能同时出现在赞助和非赞助位置；不折叠卡片位置。
        by_brand = defaultdict(set)
        prices = defaultdict(list)
        labels = {}
        for row in sorted(search.rows, key=lambda row: row.rank):
            positions.append(
                (
                    search.query,
                    row.rank,
                    row.asin,
                    "目标" if row.asin == asin else "候选" if row.asin in candidates else None,
                    row.title,
                    row.brand,
                    _ad(row.sponsored),
                    row.price_amount,
                    row.currency,
                    row.url,
                )
            )
            if row.brand and row.brand.strip():
                key = row.brand.strip().casefold()
                labels.setdefault(key, row.brand.strip())
                by_brand[key].add(row.asin)
            currency = (row.currency or "").strip().upper()
            if currency and row.price_amount is not None:
                prices[currency].append(row.price_amount)
        price_ranges.extend(
            (search.query, currency, min(amounts), max(amounts), len(amounts), search.url)
            for currency, amounts in sorted(prices.items())
        )
        total = len({row.asin for row in search.rows})
        for key, asins in sorted(by_brand.items(), key=lambda pair: (-len(pair[1]), pair[0])):
            brands.append(
                (
                    search.query,
                    labels[key],
                    len(asins),
                    total,
                    round(len(asins) / total * 100, 1),
                    _joined(sorted(asins)),
                )
            )
        unlabelled = {row.asin for row in search.rows} - set().union(*by_brand.values())
        if unlabelled:
            brands.append(
                (search.query, None, len(unlabelled), total, None, _joined(sorted(unlabelled)))
            )
    status = "unknown"
    if depth.searches:
        status = "complete" if page and all(s.rows for s in depth.searches) else "partial"
    columns = (
        "查询词｜卡片数｜去重 ASIN 数｜公开结果下界｜公开结果上界｜结果数原文｜"
        "目标非赞助位置｜目标赞助位置｜目标未知类型位置｜目标观察｜标题词覆盖｜五点词覆盖｜"
        "描述词覆盖｜搜索页"
    )
    return _section(
        22,
        "关键词、搜索位置与品牌矩阵",
        status,
        [
            "位置为本次采集卡片的原始序号，不是长期自然排名；样本内未见不代表未收录。",
            "词覆盖按忽略大小写、连续完整词匹配；公开结果数不等于搜索量。",
            "品牌占比按每词已观察去重 ASIN 计，赞助和非赞助合计；不等于市场份额。"
            "同一 ASIN 跨品牌标记冲突时可进入多行，品牌缺失不从标题推测。",
            "价格区间按查询和币种独立取实采卡片最低/最高金额；"
            "同一 ASIN 可重复，缺币种或金额不纳入。",
            "机会只列有卡片及目标相关词的实采查询；"
            "1–3 词可作短词，有用途、适配或数量修饰的至少 3 词列为长尾。"
            "高意向为词面规则而非购买/转化证据；没有合格样本不补造词。",
        ],
        [
            _table("查询与目标词覆盖矩阵", columns, matrix),
            _table(
                "观察到的卡片位置",
                "查询｜位置｜ASIN｜关系｜标题｜品牌｜位置类型｜价格｜币种｜链接",
                positions,
            ),
            _table(
                "样本品牌分布", "查询｜品牌｜ASIN 数｜样本 ASIN 数｜占比 %｜观察到的 ASIN", brands
            ),
            _table(
                "样本价格区间（按查询与币种）",
                "查询｜币种｜最低｜最高｜带价卡片数｜搜索页",
                price_ranges,
            ),
            _table(
                "样本短词与高意向长尾机会",
                "类型｜原查询｜目标文案匹配词｜卡片数｜目标证据｜意向规则｜下一步｜搜索页",
                opportunities,
            ),
        ],
        [*_search_sources(depth), *([_page_source(page, "目标商品页")] if page else [])],
    ), coverage


def _pages(depth: ProductDepth) -> list[tuple[str, ProductPage]]:
    pages = [("目标", depth.overview)] if depth.overview else []
    seen = {page.product.asin for _, page in pages}
    for page in depth.competitors:
        if page.product.asin not in seen:
            pages.append(("候选", page))
            seen.add(page.product.asin)
    return pages


def _photo_count(page: ProductPage) -> int | None:
    if page.product.image_count is not None and page.product.image_count >= 0:
        return page.product.image_count
    return len({image.url for image in page.images}) or (1 if page.product.image_url else None)


def _aplus(page: ProductPage) -> str | None:
    if any(text.strip() for text in page.aplus) or page.product.has_aplus is True:
        return "已见"
    if page.product.has_aplus is False:
        return "页面明确未见"
    return None


def _comparison(depth: ProductDepth) -> ProductSection:
    pages = _pages(depth)
    overview = depth.overview
    comparable = {p.product.asin for role, p in pages if role == "候选"}
    target_asin = overview.product.asin if overview else None
    candidates = [c for c in depth.candidates if c.asin != target_asin]
    rows, bullets, specs, assets = [], [], [], []
    quoted_fields = ("price", "sales_ranks", "specs", "highlights", "images", "aplus")
    field_sources = []
    for role, page in pages:
        p = page.product
        gap = None
        if (
            role == "候选"
            and overview
            and page.currency
            and page.currency == overview.currency
            and page.price_amount is not None
            and overview.price_amount is not None
        ):
            gap = round(page.price_amount - overview.price_amount, 2)
        rows.append(
            (
                role,
                p.asin,
                p.title,
                p.brand,
                p.price,
                page.price_amount,
                page.currency,
                gap,
                p.rating,
                p.rating_count,
                _joined(_bsrs(page)),
                p.category,
                page.parent_asin,
                _joined(page.variant_asins),
                _photo_count(page),
                _aplus(page),
                p.url,
            )
        )
        entries = _bullets(page)[:5]
        bullets.append((p.asin, *(entries + [None] * (5 - len(entries)))))
        specs.extend((p.asin, key, val) for key, val in sorted(page.specs.items()))
        assets.append(
            (
                p.asin,
                p.image_url or (page.images[0].url if page.images else None),
                _joined(m.url for m in page.images),
                _joined(page.aplus),
            )
        )
        field_sources.extend(
            (p.asin, key, page.field_evidence[key])
            for key in quoted_fields
            if page.field_evidence.get(key)
        )
    status = "unknown"
    if pages or candidates:
        status = (
            "complete"
            if overview
            and comparable
            and all(c.asin in comparable for c in candidates)
            and all(
                p.specs
                and _bsrs(p)
                and len(_bullets(p)) >= 5
                and _photo_count(p) is not None
                and _aplus(p) is not None
                for _, p in pages
            )
            else "partial"
        )
    return _section(
        23,
        "竞品筛选与详情对照",
        status,
        [
            "候选依据来自采集时的搜索和详情；相似不等于直接竞品，也不代表顾客会替代购买。",
            "价差只比较同币种公开报价（候选减目标）；包装件数、变体及配送条件须另外核对。"
            "不同类目 BSR 不直接比较，不推算销量。",
            "五点、规格、图链、A+ 原文为对照证据，不作为可发布文案。",
        ],
        [
            _table(
                "入选候选及采集进度",
                "ASIN｜搜索标题｜查询｜入选依据｜详情观察｜链接",
                (
                    (
                        c.asin,
                        c.title,
                        _joined(c.queries),
                        _joined(c.reasons),
                        "已采" if c.asin in comparable else "未取得",
                        c.url,
                    )
                    for c in candidates
                ),
            ),
            _table(
                "商品横向对照",
                "角色｜ASIN｜标题｜品牌｜价格原文｜金额｜币种｜同币种价差｜"
                "评分｜评分数｜类目 BSR｜类目｜父 ASIN｜观察变体｜图数｜A+｜链接",
                rows,
            ),
            _table("五点卖点原文", "ASIN｜卖点 1｜卖点 2｜卖点 3｜卖点 4｜卖点 5", bullets),
            _table("规格对照", "ASIN｜规格项｜页面值", specs),
            _table("图组与 A+ 摘录", "ASIN｜主图链接｜已采图链｜A+ 原文", assets),
            _table("字段取证位置", "ASIN｜字段｜证据", field_sources),
        ],
        [*_search_sources(depth), *[_page_source(p, f"{role}商品页") for role, p in pages]],
    )


_REVIEW_PATTERNS = {
    "尺寸与合身": r"size|fit|尺码|尺寸|合身",
    "材质与触感": r"fabric|material|soft|材质|面料|触感|柔软",
    "耐用和做工": r"broken|durab|quality|破损|损坏|质量|做工",
    "功能与使用": r"use|work|function|install|使用|功能|安装",
    "包装和配送": r"ship|packag|包装|运输|配送",
    "价格和价值": r"price|value|价格|性价比|划算",
}


def _review_bucket(stars: int | None) -> str:
    return (
        "负向 1–2 星"
        if stars is not None and stars <= 2
        else ("中性 3 星" if stars == 3 else "正向 4–5 星" if stars and stars >= 4 else "未标星")
    )


def _reviews(depth: ProductDepth) -> ProductSection:
    page = depth.overview
    samples: list[ProductReviewSample] = list(page.reviews) if page else []
    for review_page in depth.review_pages:
        samples.extend(review_page.rows)
    # De-duplicate overlapping review pages without erasing distinct text with missing IDs.
    unique, seen = [], set()
    for sample in samples:
        if not sample.text.strip():
            continue
        key = (sample.source, sample.id or sample.url, sample.text)
        if key not in seen:
            seen.add(key)
            unique.append(sample)
    themes = defaultdict(lambda: Counter())
    quotes = defaultdict(list)
    source_counts, star_counts = Counter(), Counter()
    all_rows = []
    for sample in unique:
        stars = sample.stars
        bucket = _review_bucket(stars)
        source_counts[sample.source] += 1
        star_counts[bucket] += 1
        matched = [
            name
            for name, pattern in _REVIEW_PATTERNS.items()
            if re.search(pattern, sample.text, re.I)
        ] or ["其他／未归类"]
        for theme in matched:
            themes[theme][bucket] += 1
            if len(quotes[theme]) < 3:
                quotes[theme].append((sample.source, stars, sample.text[:180], sample.url))
        all_rows.append(
            (
                sample.source,
                sample.id,
                stars,
                sample.date,
                sample.variant,
                sample.verified,
                sample.helpful,
                sample.text,
                sample.url,
            )
        )
    theme_rows = []
    for name, counts in sorted(
        themes.items(), key=lambda pair: (-pair[1]["负向 1–2 星"], -pair[1]["正向 4–5 星"], pair[0])
    ):
        evidence = _joined(
            f"{src}｜{stars if stars is not None else '未标星'}星｜{text}"
            for src, stars, text, _url in quotes[name]
        )
        theme_rows.append(
            (
                name,
                counts["负向 1–2 星"],
                counts["中性 3 星"],
                counts["正向 4–5 星"],
                counts["未标星"],
                evidence,
            )
        )
    trends = []
    dated = sorted(
        ((sample.date, sample) for sample in unique if sample.date is not None),
        key=lambda item: item[0],
    )
    if dated:
        split = max(1, len(dated) // 2)
        for theme in sorted(themes):

            def mentions(part):
                return sum(bool(re.search(_REVIEW_PATTERNS[theme], s.text, re.I)) for _, s in part)

            if theme not in _REVIEW_PATTERNS:
                continue
            first, second = dated[:split], dated[split:]
            trends.append(
                (
                    theme,
                    first[0][0] if first else None,
                    first[-1][0] if first else None,
                    mentions(first),
                    second[0][0] if second else None,
                    second[-1][0] if second else None,
                    mentions(second),
                    "仅为此样本分段变化；非总体趋势",
                )
            )
    has_review_page_samples = any(
        sample.text.strip() for p in depth.review_pages for sample in p.rows
    )
    status = (
        "unknown"
        if not unique
        else (
            "complete"
            if has_review_page_samples and not any(p.limited for p in depth.review_pages)
            else "partial"
        )
    )
    sources = [_page_source(page, "目标商品页评论")] if page else []
    sources.extend(
        ProductCitation(
            label=f"评论页：{item.bucket}",
            url=item.url,
            collected_at=item.collected_at,
            asin=page.product.asin if page else None,
        )
        for item in depth.review_pages
    )
    return _section(
        24,
        "评论声音、主题与改进机会",
        status,
        [
            f"去重文字样本 {len(unique)} 条；来源计数："
            + (
                _joined(f"{source} {count}" for source, count in sorted(source_counts.items()))
                or "无"
            ),
            "倾向以评论星级作粗略代理；同一评论可命中多个主题。人工录入和 Amazon 页面样本分列。",
            "分段变化只对已采样本作描述，不推断总体、因果或销量变化。"
            "原始短引文保留来源，发布前复核授权与隐私。",
            "根因只列可验证假设：对照原文、变体、样品与规格验证后再改。",
        ],
        [
            _table(
                "样本星级与来源",
                "分组｜条数",
                [
                    *sorted(star_counts.items()),
                    *((f"来源 {k}", v) for k, v in sorted(source_counts.items())),
                ],
            ),
            _table(
                "主题情感矩阵与短引文",
                "主题｜负向｜中性｜正向｜未标星｜样本引文（来源）",
                theme_rows,
            ),
            _table(
                "来源可追溯评论样本",
                "来源｜ID｜星级｜日期｜变体｜已验证｜有用数｜原文｜链接",
                all_rows,
            ),
            _table(
                "评论时间分段",
                "主题｜早段起｜早段止｜早段提及｜晚段起｜晚段止｜晚段提及｜解释",
                trends,
            ),
            _table(
                "根因验证与改进草案",
                "主题｜样本信号｜待验证根因｜下一步验证｜可调整项",
                (
                    (
                        name,
                        _joined(f"{src}：{text}" for src, _stars, text, _url in quotes[name]),
                        "未知；不可由关键词命中直接归因",
                        "抽样复核原评、变体及实物规格",
                        "以验证结果更新产品规格、说明或包装；不复制竞品表达",
                    )
                    for name in sorted(themes)
                ),
            ),
        ],
        sources,
    )


def _finance_table(finance: ProductFinance) -> ProductTable:
    fee_date = finance.fee_date.isoformat() if finance.fee_date is not None else None
    rows = [
        ("币种", finance.currency, None, None, None),
        ("售价", finance.price, None, None, None),
        ("类目", finance.category, None, None, None),
        ("BSR", finance.rank, None, None, None),
        ("单件利润", finance.unit_profit, None, None, None),
        ("ROI %", finance.roi_pct, None, None, None),
        ("费用来源", finance.fee_source, None, None, None),
        ("费用日期", fee_date, None, None, None),
    ]
    rows.extend(
        (f"单件成本/{key}", value, None, None, None) for key, value in finance.unit_costs.items()
    )
    rows.extend(
        (f"缺项/{i + 1}", value, None, None, None) for i, value in enumerate(finance.missing)
    )
    rows.extend(
        (f"假设/{i + 1}", value, None, None, None) for i, value in enumerate(finance.assumptions)
    )
    rows.extend(
        (
            f"销量情景/{scenario.name}",
            None,
            scenario.monthly_units,
            scenario.annual_revenue,
            scenario.annual_profit,
        )
        for scenario in finance.scenarios
    )
    return _table("财务 worker 输出（原单位/缺项）", "指标｜值｜件数/月｜年销售额｜年利润", rows)


def _listing(depth: ProductDepth, coverage: list[_KeywordCoverage]) -> ProductSection:
    page = depth.overview
    product = page.product if page else None
    title = (product.title.strip() or None) if product else None
    description = ((product.description or "").strip() or None) if product else None
    bullets = _bullets(page) if page else []
    photos = _photo_count(page) if page else None
    aplus = _aplus(page) if page else None
    aplus_text = _joined(text.strip() for text in page.aplus) if page else None
    price = ((product.price or "").strip() or None) if product else None
    normalized_price = bool(
        page and page.price_amount is not None and (page.currency or "").strip()
    )
    rating = ((product.rating or "").strip() or None) if product else None
    rating_count = ((product.rating_count or "").strip() or None) if product else None
    # Count each normalized query once and only when at least one field can be checked.
    checked = {
        tuple(_tokens(query)): "出现" in (in_title, in_bullets, in_description)
        for query, in_title, in_bullets, in_description, _ in coverage
        if any(value is not None for value in (in_title, in_bullets, in_description))
    }
    hits = sum(checked.values())
    seo_score = (0 if not hits else 2 if hits == len(checked) else 1) if checked else None
    axes = [
        ("标题", 2 if title else None, "2=已采非空标题", title),
        (
            "五点",
            2 if len(bullets) >= 5 else 1 if bullets else None,
            "1=已采 1–4 条；2=已采至少 5 条",
            _joined(bullets[:5]),
        ),
        (
            "图片",
            min(2, photos) if photos is not None else None,
            "0=页面计数为 0；1=有 1 张；2=有至少 2 张；非画面评分",
            photos,
        ),
        (
            "A+",
            2 if aplus_text else 1 if aplus == "已见" else 0 if aplus else None,
            "0=页面标记未见；1=标记已见但无文案；2=已采文案",
            aplus_text or aplus,
        ),
        ("描述", 2 if description else None, "2=已采非空描述", description),
        (
            "定价",
            2 if normalized_price else 1 if price else None,
            "1=仅有报价原文；2=有金额和币种",
            price or (f"{page.price_amount} {page.currency}" if normalized_price else None),
        ),
        (
            "评论",
            (int(bool(rating)) + int(bool(rating_count))) or None,
            "1=有公开评分或评分数之一；2=两者均有",
            _joined(
                (
                    f"评分 {rating}" if rating else None,
                    f"评分数 {rating_count}" if rating_count else None,
                )
            ),
        ),
        (
            "SEO",
            seo_score,
            "实采查询按可查公开文案去重：0=无完整词命中；1=部分；2=全部",
            f"命中 {hits}/{len(checked)} 个可查查询" if checked else None,
        ),
    ]
    scores = [
        (
            axis,
            score,
            "未知／未采" if score is None else "未达该轴规则" if score == 0 else "有证据",
            rule,
            value,
        )
        for axis, score, rule, value in axes
    ]
    status = (
        "unknown"
        if page is None
        else "complete"
        if all(score is not None for _, score, _, _ in axes)
        else "partial"
    )
    return _section(
        26,
        "Listing 八轴评分与词覆盖",
        status,
        [
            "各轴按公开证据独立计 0–2 分。空分是未知／未采，区别于明示未见或已核对但未命中的 0 分。"
            "不推断转化质量或排名，不计算综合分。",
            "词覆盖按规范化后的完整连续词匹配；未采字段留空，已采字段中未命中才标未出现。"
            "后台 Search Terms 不可见，待卖家核验。",
        ],
        [
            _table("8 轴可观察性评分", "轴｜分数（0–2）｜证据状态｜计分规则｜本次证据", scores),
            _table("SEO 查询词覆盖", "实采查询｜标题｜五点｜描述｜搜索页", coverage),
        ],
        [*([_page_source(page, "目标商品页")] if page else []), *_search_sources(depth)],
    )


def _media(depth: ProductDepth, shots: list[ProductShot]) -> ProductSection:
    page = depth.overview
    rows = []
    if page:
        for media in page.images:
            # Alt/label can establish visible text only.
            # URL or dimensions do not prove image content.
            rows.append(
                (
                    media.url,
                    media.label,
                    media.width,
                    media.height,
                    "可判：链接、尺寸" + ("、标签文字" if media.label else "") + "; 内容构图未知",
                    None,
                    media.poster,
                )
            )
    if page and page.videos:
        for media in page.videos:
            rows.append(
                (
                    media.url,
                    media.label,
                    media.width,
                    media.height,
                    "可判：视频链接、尺寸"
                    + ("、标签文字" if media.label else "")
                    + "; 视频画面未知",
                    None,
                    media.poster,
                )
            )
    status = "unknown" if not page or not rows else "partial"
    return _section(
        27,
        "现有媒体逐项审阅",
        status,
        [
            "未提供媒体内容本身；只记录采集到的标签、URL 和尺寸，"
            "不能评价画面清晰度、构图、真实性或合规性。",
            "未展示的媒体内容评分保持 null，不以空值当零分。",
        ],
        [
            _table("图片/视频证据逐项", "链接｜标签/Alt｜宽｜高｜可判项目｜内容评分｜封面", rows),
            _table(
                "图片与视频创作策略",
                "编号｜类型｜标题｜拍摄简报｜证据来源",
                (
                    (shot.id, shot.type, shot.title, shot.brief, _joined(shot.evidence))
                    for shot in shots
                ),
            ),
        ],
        [_page_source(page, "目标商品页媒体")] if page else [],
    )


def _shots(depth: ProductDepth, review: ProductSection) -> list[ProductShot]:
    page = depth.overview
    theme_rows = review.tables[1].rows
    evidence = []
    for row in theme_rows:
        if row[1] and row[1] > 0:
            evidence.append(f"评论样本主题「{row[0]}」：{row[1]} 条负向（REQ-024）")
    if page and page.specs:
        evidence.append("目标页有结构化规格字段（REQ-021），具体值待实测核验")
    if page and page.images:
        evidence.append(f"页面已采 {len(page.images)} 个图链；未能检视内容（REQ-027）")
    if not evidence:
        evidence.append("无可用于产品特定改进的采集证据；以下为通用验证镜头")
    basics = [
        (
            "S01",
            "image",
            "白底主图",
            "自有实物单件，按平台主图规范拍摄；核对真实颜色、数量、包装内容。",
        ),
        ("S02", "image", "核心细节", "微距记录可验证的材质、接口、做工或关键结构；按实物选择。"),
        (
            "S03",
            "image",
            "尺寸与比例",
            "实测并标注单位、测量点和包装内件数；尺寸未确认前不写数值。",
        ),
        ("S04", "image", "真实使用场景", "展示目标使用环境和操作过程，不添加未验证功能或效果。"),
        ("S05", "image", "功能步骤", "用连续步骤说明真实操作、兼容条件和限制；实测后制作。"),
        ("S06", "image", "包装与清单", "平铺展示自有包装及实际包含物，清楚区分不随附物品。"),
        (
            "S07",
            "image",
            "选择与维护",
            "通过真实规格/维护信息说明选型、清洁或存放；不作无依据承诺。",
        ),
    ]
    shots = [
        ProductShot(id=i, type=kind, title=title, brief=brief, evidence=evidence[:])
        for i, kind, title, brief in basics
    ]
    if theme_rows and any(row[1] for row in theme_rows):
        top = next(row for row in theme_rows if row[1])
        shots[1] = ProductShot(
            id="S02",
            type="image",
            title=f"改进验证：{top[0]}",
            brief="针对负向样本主题拍摄可测细节；先验证根因，再决定是否调整规格。",
            evidence=[f"REQ-024 中 {top[0]} 有 {top[1]} 条负向样本"],
        )
    shots.extend(
        [
            ProductShot(
                id="S08",
                type="image",
                title="A+ 对照模块",
                brief="仅列自有商品已核验规格与适用场景；资格、事实和图片权益审核后制作。",
                evidence=evidence[:],
            ),
            ProductShot(
                id="S09",
                type="video",
                title="实物演示",
                brief="连续拍摄真实开箱、安装/使用及停止条件；只展示样品可复现行为。",
                evidence=evidence[:],
            ),
            ProductShot(
                id="S10",
                type="video",
                title="核心功能近景",
                brief="用第二段视频近距离展示可重复的真实细节或操作；不得声称未测试的性能。",
                evidence=evidence[:],
            ),
        ]
    )
    return shots


_BACKEND_MAX_BYTES = 249  # Conservative draft budget; seller verifies the current limit.
_BACKEND_EXCLUSIONS = _QUERY_CONNECTORS | {
    "amazon",
    "are",
    "as",
    "be",
    "best",
    "buy",
    "cheap",
    "deal",
    "free",
    "genuine",
    "is",
    "new",
    "no",
    "not",
    "official",
    "only",
    "sale",
    "this",
    "top",
}


def _backend_candidates(depth: ProductDepth) -> list[tuple[str, str, str, str, str]]:
    page = depth.overview
    if page is None:
        return []
    product = page.product
    visible = {
        token
        for text in (product.title, *_bullets(page), product.description or "")
        for token in _tokens(text)
    }
    brand_names = [
        product.brand,
        *(p.product.brand for p in depth.competitors),
        *(row.brand for search in depth.searches for row in search.rows),
    ]
    identifiers = [
        product.asin,
        *(p.product.asin for p in depth.competitors),
        *(candidate.asin for candidate in depth.candidates),
        *(row.asin for search in depth.searches for row in search.rows),
    ]
    blocked = _BACKEND_EXCLUSIONS | {
        token for name in (*brand_names, *identifiers) if name for token in _tokens(name)
    }
    spec_terms = {
        token: f"规格：{key}"
        for key, value in sorted(page.specs.items())
        for token in _tokens(value)
    }
    candidates = []
    seen = set()
    remaining = _BACKEND_MAX_BYTES
    for search in depth.searches:
        tokens = _tokens(search.query)
        if not search.rows or not (
            visible.intersection(tokens).difference(blocked)
            or any(row.asin == product.asin for row in search.rows)
        ):
            continue
        for token in tokens:
            if token in seen or token in blocked or token in visible or not token.isalpha():
                continue
            if token not in spec_terms:
                continue
            cost = len(token.encode("utf-8")) + bool(candidates)
            if cost > remaining:
                continue
            candidates.append(
                (
                    token,
                    search.query,
                    spec_terms[token],
                    page.field_evidence.get("specs") or product.url,
                    search.url,
                )
            )
            remaining -= cost
            seen.add(token)
    return candidates


def _template(backend_keywords: str) -> ProductTemplate:
    return ProductTemplate(
        title="[自有商品类型] [可核验核心规格] [适用对象]",
        bullets=[
            "[实测核心规格] — [测量值、单位、测量条件；仅填自有商品已验证数据]",
            "[解决的使用任务] — [说明真实使用条件及限制，不承诺未验证效果]",
            "[材质/结构] — [以自有样品或供应链规格证明的事实]",
            "[包装内容] — [逐件列出实际随附物；明确不包含物品]",
            "[适配/维护] — [适用型号、维护方式及不适用情形，实测后填写]",
        ],
        description=(
            "[自有商品说明：用途、实测规格、使用步骤、适配边界、包装清单与维护。"
            "逐项以自有样品验证后替换占位符。]"
        ),
        aplus=[
            "[模块：自有品牌与商品事实]",
            "[模块：实测细节与尺寸]",
            "[模块：适用场景及限制]",
            "[对比表：仅列自有商品、同口径实测字段]",
        ],
        backend_keywords=backend_keywords,
        conditions=[
            "后台候选仅取有卡片的实采查询与目标公开规格共同出现的通用单词；不采竞品文案。",
            f"候选用空格分隔、去重、草案不超过 {_BACKEND_MAX_BYTES} UTF-8 字节；"
            "不重复目标现有标题/五点/描述中的单词。",
            "不填品牌、商标、ASIN、促销词或未经证实的属性；"
            "候选须核对自有商品、商标、索引和美国站当前规则后才可使用。",
        ],
    )


def _decision(
    depth: ProductDepth, finance: ProductFinance, review: ProductSection
) -> tuple[ProductSection, str]:
    page = depth.overview
    pages = _pages(depth)
    missing = list(finance.missing)
    target_currency = (page.currency or "").strip().upper() if page else ""
    profile = [
        label
        for label, observed in (
            ("标题", page and page.product.title.strip()),
            ("类目", page and (page.product.category_path or page.product.category)),
            (
                "同币种定价所需的金额及币种",
                page and page.price_amount is not None and target_currency,
            ),
        )
        if observed
    ]
    profile_score = 2 if len(profile) == 3 else 1 if page else None
    queries = {
        tuple(tokens)
        for search in depth.searches
        if search.rows and (tokens := _tokens(search.query))
    }
    search_score = min(2, len(queries)) or None
    comparables = [
        candidate.product.asin
        for role, candidate in pages
        if role == "候选"
        and target_currency
        and candidate.price_amount is not None
        and (candidate.currency or "").strip().upper() == target_currency
    ]
    competition_score = (2 if len(comparables) >= 3 else 1) if comparables else None
    review_count = len(review.tables[2].rows)
    review_score = (2 if review.status == "complete" else 1) if review_count else None
    profit = finance.unit_profit
    finance_score = None
    if profit is not None:
        if profit <= 0:
            finance_score = 0
        elif (
            finance.roi_pct is not None
            and finance.roi_pct > 0
            and finance.fee_date
            and finance.fee_source
            and finance.scenarios
            and not finance.missing
        ):
            finance_score = 2
        else:
            finance_score = 1
    if profile_score != 2:
        missing.append("目标商品页或核心档案字段")
    if search_score != 2:
        missing.append("至少两个不同查询的非空卡片样本")
    if competition_score != 2:
        missing.append("至少三个目标同币种有价候选详情；另核实直接可比性")
    if review_score != 2:
        missing.append("非空、未标采集受限的评论页样本")
    if not finance.fee_source or not finance.fee_date:
        missing.append("费用来源或日期")
    if finance.roi_pct is None:
        missing.append("可计算的 ROI")
    dimensions = [
        (
            "目标档案",
            profile_score,
            "1=已采商品页但核心字段不齐；2=标题、类目、金额和币种齐备",
            f"已采：{_joined(profile) or '仅商品页'}" if page else "未取得目标商品页",
        ),
        (
            "搜索样本广度",
            search_score,
            "1=一个；2=两个及以上去重非空查询；非需求/销量评分",
            f"有卡片的去重查询：{len(queries)}" if queries else "没有可用的搜索卡片样本",
        ),
        (
            "候选价格证据",
            competition_score,
            "1=1–2 个；2=至少 3 个目标同币种有价候选详情；非直接竞品认定",
            _joined(comparables) or "未获得可用的同币种有价候选详情",
        ),
        (
            "评论样本",
            review_score,
            "1=有样本但无非空评论页或采集受限；2=有非空评论页且未标受限",
            f"REQ-024 去重文字样本 {review_count} 条；采集状态：{review.status}"
            if review_count
            else "没有可用的文字评论样本",
        ),
        (
            "单件经济性",
            finance_score,
            "0=单件利润不为正；1=为正但佐证不足；"
            "2=利润与 ROI 均为正、有日期的费用来源、校准情景且财务未列缺项",
            f"REQ-025 单件利润 {profit} {finance.currency or ''}；"
            f"ROI {finance.roi_pct if finance.roi_pct is not None else '未知'}；"
            f"费用来源/日期 {'齐备' if finance.fee_source and finance.fee_date else '不齐'}；"
            f"校准情景 {'有' if finance.scenarios else '未取得'}"
            if profit is not None
            else "无法计算单件利润；见财务缺项",
        ),
    ]
    pending = _joined(name for name, score, _, _ in dimensions if score != 2)
    if finance_score == 0:
        decision = "暂缓推进：当前输入下单件利润不为正"
    elif pending:
        decision = f"暂不作上架决定：先核验 {pending}"
    else:
        decision = "进入小规模验证：当前证据达标，不等于建议上架"
    status = (
        "unknown"
        if all(score is None for _, score, _, _ in dimensions)
        else (
            "partial"
            if missing or any(score is None for _, score, _, _ in dimensions)
            else "complete"
        )
    )
    tests = [
        (
            "直接竞争",
            "同站点、同用途、同适配对象与相近规格；人工确认 3 个直接竞品。",
            "记录 ASIN、页面时间、价格/币种/套装数量、规格、履约与购买对象；"
            "逐项证实，不用标题相似替代。",
        ),
        (
            "替代品",
            "用户同一任务的不同解决方案，不预设可替代。",
            "访谈/搜索查询核实任务与使用场景；比较总成本、效果条件、兼容性和切换成本。",
        ),
        (
            "配件",
            "核实是否为目标商品的配套、耗材或安装附件。",
            "核对接口/型号、适配清单、包装内容及复购关系；不能仅凭分类邻近定性。",
        ),
    ]
    return _section(
        29,
        "决策摘要与可执行验证",
        status,
        [
            f"结论：{decision}。",
            "各维度独立计 0–2 分，空分为不可评分；不求总分。财务为 0 时暂缓；"
            "五项均为 2 才进入小规模验证，否则先核验未达标维度。",
            "候选定位只可作待验证假设：直接竞品 / 替代品 / 配件须逐项核对用途、规格与交易条件。",
            "优先动作：按 REQ-021–028 的缺项补采原页、同条件搜索、评论和自有样品；"
            "核对后再决定差异化规格。",
        ],
        [
            _table("决策维度与依据", "维度｜分数（0–2）｜计分规则｜本次理由", dimensions),
            _table(
                "财务摘要（由独立 finance worker 计算）",
                "指标｜值｜件数/月｜年销售额｜年利润",
                [
                    ("币种", finance.currency, None, None, None),
                    ("价格", finance.price, None, None, None),
                    ("单件利润", finance.unit_profit, None, None, None),
                    ("ROI %", finance.roi_pct, None, None, None),
                ]
                + [
                    (f"{s.name} 情景", None, s.monthly_units, s.annual_revenue, s.annual_profit)
                    for s in finance.scenarios
                ],
            ),
            _table("缺失输入", "未取得 / 待核", [(item,) for item in dict.fromkeys(missing)]),
            _table("最小验证计划", "类型｜纳入规则｜核验与记录", tests),
        ],
        [
            *[_page_source(candidate, f"{role}商品页") for role, candidate in pages],
            *_search_sources(depth),
            *review.sources,
        ],
    ), decision


def analyze_product(depth: ProductDepth, costs: ProductCosts | None = None) -> ProductAnalysis:
    """Assemble a deterministic, source-aware report from captured product evidence."""
    page = depth.overview
    finance = analyze_product_finance(page, costs)
    keywords, coverage = _keywords(depth)
    review = _reviews(depth)
    shots = _shots(depth, review)
    backend_candidates = _backend_candidates(depth)
    template = _template(" ".join(row[0] for row in backend_candidates))
    decision_section, decision = _decision(depth, finance, review)
    sections = [
        _overview(page),
        keywords,
        _comparison(depth),
        review,
        _section(
            25,
            "价格、成本与利润情景",
            "complete" if not finance.missing else "partial",
            [
                "全部财务字段、销量标定、缺项和假设由独立 finance worker 提供；不重复估算。",
                "BSR 仅按同类目观察；没有同类目同 BSR 且有来源日期的标定时，不推销量。",
            ],
            [_finance_table(finance)],
            [_page_source(page, "财务售价来源")] if page else [],
        ),
        _listing(depth, coverage),
        _media(depth, shots),
        _section(
            28,
            "可编辑 Listing 模板",
            "partial" if page else "unknown",
            [
                "前台文案只含占位结构，不复用目标或竞品原文；"
                "用户须用自有商品样品和授权资料确认事实。",
                "后台候选仅供验证，不代表现有后台词或已收录；无符合约束的实采候选则留空。",
            ],
            [
                _table(
                    "商品文案草案",
                    "部分｜编辑内容",
                    [
                        ("标题", template.title),
                        *(
                            (f"五点 {index}", bullet)
                            for index, bullet in enumerate(template.bullets, 1)
                        ),
                        ("描述", template.description),
                        ("A+ 模块", _joined(template.aplus)),
                        ("后台搜索词候选", template.backend_keywords or None),
                    ],
                ),
                _table("适用及确认条件", "待确认条件", ((item,) for item in template.conditions)),
                _table(
                    "通用后台词候选与依据",
                    "候选词｜原查询｜目标规格字段｜字段证据｜搜索页",
                    backend_candidates,
                ),
            ],
            [
                *([_page_source(page, "目标商品事实边界")] if page else []),
                *(
                    source
                    for source in _search_sources(depth)
                    if any(row[-1] == source.url for row in backend_candidates)
                ),
            ],
        ),
        decision_section,
    ]
    return ProductAnalysis(
        sections=sections, image_plan=shots, listing=template, finance=finance, decision=decision
    )
