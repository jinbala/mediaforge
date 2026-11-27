import re
from collections import Counter, defaultdict
from decimal import ROUND_CEILING, Decimal
from statistics import median

from mediaforge.models.amazon_category import (
    CategoryAnalysis,
    CategoryRow,
    CategorySection,
    CategoryTable,
)
from mediaforge.models.amazon_research_run import AmazonResearchRun, AmazonStoreSample
from mediaforge.workers.amazon_fba_fees import currency_round

D = Decimal
BANDS = ((D(0), D(20)), (D(20), D(40)), (D(40), D(75)), (D(75), None))
NOISE = set("the and for with from this that pack set are your our you new made pair item".split())


def _table(title, headers, rows):
    return CategoryTable(
        title=title,
        columns=headers,
        rows=[
            CategoryRow(cells=[str(x) if x is not None else None for x in values], asins=asins)
            for values, asins in rows
        ],
    )


def _price(product):
    # 不把非美元、价格范围或促销混合字段当成单一报价。
    if product.price_currency == "USD" and re.fullmatch(
        r"\$[\d,]+(?:\.\d{2})?", product.price or ""
    ):
        return D(product.price[1:].replace(",", ""))
    return None


def _ratings(row):
    text = (row.rating_count or "").replace(",", "")
    return int(text) if text.isascii() and text.isdigit() else None


def _rank(row):
    match = re.fullmatch(r"#?([0-9][0-9,]*)", (row.rank or "").strip())
    return int(match[1].replace(",", "")) if match else 999999


def _segments(run):
    boards = [item for item in run.snapshot.evidence if item.kind == "board" and item.products]
    if not boards:
        return []
    children = [item for item in boards if item.role != "main"]
    if children:
        return [(item.title, item.products) for item in children]
    # 无法发现子榜时，只用可见标题词分组，不推断 Amazon 未公开的产品分类。
    groups = defaultdict(list)
    for row in boards[0].products:
        word = next(
            (w for w in reversed(re.findall(r"[a-z]{3,}", row.title.lower())) if w not in NOISE),
            "其他",
        )
        groups[word].append(row)
    return sorted(groups.items(), key=lambda group: (-len(group[1]), group[0]))[:8]


def _profit(price, c):
    unit_cost = c.purchase_usd + c.inbound_usd + c.fulfillment_usd + c.storage_usd
    contribution = price * (1 - c.referral_rate - c.ads_rate - c.returns_rate) - unit_cost
    investment = c.purchase_usd + c.inbound_usd
    roi = contribution / investment * 100 if investment else None
    break_even = (
        (c.fixed_monthly_usd / contribution).to_integral_value(rounding=ROUND_CEILING)
        if contribution > 0
        else None
    )
    return unit_cost, contribution, roi, break_even


def _fmt(value):
    return currency_round(value) if value is not None else None


def _profit_section(run, products):
    cfg = run.inputs.category.costs if run.inputs.category else None
    section = CategorySection(
        key="profit",
        title="利润与启动成本",
        reqs=["REQ-018"],
        notes=[
            "估算以 USD、每件及每月为单位，不代表平台账单。"
            "采购、头程、履约、仓储为每件；佣金、广告、退货为售价比例。",
            "贡献利润 = 售价 × (1 − 三项比例费率) − 单件固定成本；"
            "ROI = 贡献利润 ÷（采购 + 头程）；盈亏平衡销量向上取整。",
        ],
    )
    if cfg is None:
        section.notes.append("成本未填写，不以 0 补齐；请在类目表单补充假设后重新提交。")
        return section, None
    section.notes.append(
        f"用户假设：{cfg.source_note} · 生效 {cfg.effective_on} · 版本 {cfg.version}；"
        "可替换，不是实采费率。"
    )
    target = next((p for p in products if p.asin == cfg.target_asin), None)
    investments = []
    projections = []
    for lower, upper in BANDS:
        selected = [
            p
            for p in products
            if (v := _price(p)) is not None and v >= lower and (upper is None or v < upper)
        ]
        if selected:
            values = [_price(p) for p in selected]
            name = f"{lower}–{upper}" if upper else f"{lower}+"
            price = median(values)
            cost, margin, roi, count = _profit(price, cfg)
            lo, hi = sorted((_profit(min(values), cfg)[1], _profit(max(values), cfg)[1]))
            investments.append(
                (
                    roi,
                    [name, price, cost, _fmt(lo), _fmt(hi), _fmt(roi), count],
                    [p.asin for p in selected],
                )
            )
    target_price = cfg.target_price_usd or (_price(target) if target else None)
    if target_price is not None:
        cost, unit_profit, roi, count = _profit(target_price, cfg)
        investments.append(
            (
                roi,
                [
                    f"目标 SKU {cfg.target_asin or '自定义'}",
                    target_price,
                    cost,
                    _fmt(unit_profit),
                    _fmt(unit_profit),
                    _fmt(roi),
                    count,
                ],
                [target.asin] if target else [],
            )
        )
    section.tables.append(
        _table(
            "价格带和目标 SKU · 按可算 ROI 降序",
            [
                "价格带 / SKU",
                "代表价",
                "单件成本",
                "贡献利润下限",
                "贡献利润上限",
                "代表 ROI %",
                "月盈亏平衡件数",
            ],
            [
                (values, ids)
                for _, values, ids in sorted(
                    investments, key=lambda row: (row[0] is None, -(row[0] or 0))
                )
            ],
        )
    )
    capital = cfg.purchase_usd + cfg.inbound_usd
    section.tables.append(
        _table(
            "启动预算（采购 + 头程）× 首批数量 + 一次性启动费",
            ["方案", "首批数量", "启动资金 USD"],
            [
                ([label, qty, _fmt(capital * qty + cfg.launch_fixed_usd)], [])
                for label, qty in [("精简", cfg.lean_units), ("标准", cfg.standard_units)]
            ],
        )
    )
    if target_price is None:
        section.notes.append("没有可用目标售价，六个月损益与目标 SKU 盈亏平衡暂不可算。")
    else:
        _, unit_profit, _, _ = _profit(target_price, cfg)
        for label, factor in [("低", D("0.6")), ("中", D("1")), ("高", D("1.4"))]:
            qty = int(cfg.monthly_units * factor)
            monthly = qty * unit_profit - cfg.fixed_monthly_usd
            projections.append(
                (
                    [
                        label,
                        qty,
                        *[
                            _fmt(monthly - (cfg.launch_fixed_usd if month == 1 else 0))
                            for month in range(1, 7)
                        ],
                        _fmt(6 * monthly - cfg.launch_fixed_usd),
                    ],
                    [target.asin] if target else [],
                )
            )
        section.tables.append(
            _table(
                "六个月损益 USD（稳定销量情景；第 1 月扣启动费）",
                ["情景", "月销量", "月1", "月2", "月3", "月4", "月5", "月6", "六个月合计"],
                projections,
            )
        )
    return section, cfg


def _keyword_section(run, segments):
    suggestions = run.snapshot.suggestions
    section = CategorySection(
        key="keywords",
        title="关键词、SEO 与 PPC",
        reqs=["REQ-016", "REQ-017"],
        notes=[
            "标题词计每个 ASIN 命中次数；联想是 Amazon "
            "搜索建议，不是结果数或搜索量。实际搜索量、结果数未采集。",
            "广告与运营只输出方案，不调用广告账户；否定词须核对自有 SKU 后再启用。",
        ],
    )
    rows = []
    campaigns = []
    season_terms = {
        "summer",
        "winter",
        "spring",
        "autumn",
        "halloween",
        "christmas",
        "holiday",
        "summerwear",
    }
    for name, products in segments:
        terms = defaultdict(list)
        tokens = set()
        for item in products:
            parts = [w for w in re.findall(r"[a-z]{3,}", item.title.lower()) if w not in NOISE]
            tokens.update(parts)
            for word in set(parts + [" ".join(parts[i : i + 2]) for i in range(len(parts) - 1)]):
                terms[word].append(item.asin)
        top = sorted(terms, key=lambda x: (-len(terms[x]), x))
        singles = [t for t in top if " " not in t][:8]
        local = [w for w in suggestions if set(re.findall(r"[a-z]{3,}", w.lower())) & tokens][:8]
        longtail = list(dict.fromkeys(local + [t for t in top if " " in t][:5]))[:8]
        season = sorted(tokens & season_terms)
        sources = [p.asin for p in products]
        rows.append(
            (
                [
                    name,
                    "；".join(f"{t} ({len(terms[t])})" for t in singles) or None,
                    "；".join(longtail) or None,
                    "；".join(local) or None,
                    "；".join(season) or None,
                    "品类 + 材质/功能 + 规格/使用场景",
                    " ".join(singles) or None,
                ],
                sources,
            )
        )
        if longtail or singles:
            exact = (longtail or singles)[0]
            phrase = (longtail + singles)[1:3]
            campaigns.append(
                (
                    [
                        name,
                        f"{exact} · 精准／明确商品意图",
                        "／".join(phrase) or None,
                        "仅当不售配件时否定 replacement / parts（词组）",
                        "第 1–14 天低预算验证；15–30 天按转化和 "
                        "ACOS 调整；用合规邀评；促销按季节证据试投",
                    ],
                    sources,
                )
            )
    section.tables.extend(
        [
            _table(
                "细分关键词及 Listing 草案",
                [
                    "细分",
                    "高频词（ASIN 数）",
                    "长尾候选",
                    "实采联想",
                    "样本季节词",
                    "标题公式",
                    "后台词草案",
                ],
                rows,
            ),
            _table(
                "PPC 与运营计划（方案）",
                ["细分", "精准词", "词组候选", "条件式否定词", "分阶段动作"],
                campaigns,
            ),
        ]
    )
    if not suggestions:
        section.notes.append("未取得搜索联想，保留标题计数。可填写搜索种子或重试搜索建议项。")
    return section


def analyze_category(run: AmazonResearchRun) -> CategoryAnalysis:
    boards = [item for item in run.snapshot.evidence if item.kind == "board"]
    # 主榜优先，子榜按任务顺序补齐；不把同一商品的排名跨榜比较或累计。
    unique: dict[str, AmazonStoreSample] = {}
    for board in boards:
        for row in board.products:
            unique.setdefault(row.asin, row)
    panels = _segments(run)
    opportunities, competition, matrix, sourcing, rankings = [], [], [], [], []
    profit, costs = _profit_section(run, list(unique.values()))
    for name, products in panels:
        ids = [p.asin for p in products]
        prices = [v for p in products if (v := _price(p)) is not None]
        ratings = [v for p in products if (v := _ratings(p)) is not None]
        stars = [
            D(p.rating) for p in products if p.rating and re.fullmatch(r"[0-5](?:\.\d+)?", p.rating)
        ]
        brands = Counter(p.brand for p in products if p.brand)
        known = sum(brands.values())
        leaders = brands.most_common(2)
        top = sorted(products, key=_rank)[:3]
        mid = median(prices) if prices else None
        threshold = median(ratings) if ratings else None
        concentration = D(max(brands.values())) / known if known else None
        bundles = sum(
            bool(re.search(r"\b(?:\d+[ -]pack|multipack|set of|bundle)\b", p.title, re.I))
            for p in products
        )
        variants = sum(p.has_variants is True or p.store_variant is not None for p in products)
        variant_known = sum(
            p.has_variants is not None or p.store_variant is not None for p in products
        )
        price_counts = [
            sum(v >= lower and (upper is None or v < upper) for v in prices)
            for lower, upper in BANDS
        ]
        # 给出下一步可核查的行动，而不是根据一页榜单预测全市场。
        if len(prices) < 3 or len(ratings) < 3:
            opportunity = "先补报价和评分数，两项均至少有 3 个样本再比较。"
        elif threshold >= 250 and stars and median(stars) < D("4.3"):
            opportunity = "优先读差评，验证高评价量细分是否存在可改善的质量问题。"
        else:
            opportunity = f"从样本中位价 {mid} USD 起核对竞品，先确认单位贡献再决定是否试款。"
        opportunities.append(
            (
                [
                    name,
                    len(ids),
                    f"{len(prices)}/{len(ids)}",
                    min(prices) if prices else None,
                    mid,
                    max(prices) if prices else None,
                    _fmt(median(stars)) if stars else None,
                    threshold,
                    " / ".join(str(count) for count in price_counts) if prices else None,
                    opportunity,
                ],
                ids,
            )
        )
        competition.append(
            (
                [
                    name,
                    f"{known}/{len(ids)}",
                    " / ".join(f"{label} ({count})" for label, count in leaders) or None,
                    _fmt(concentration * 100) if concentration is not None else None,
                    threshold,
                    " / ".join(f"{p.rank or '未识别排名'} {p.asin}" for p in top),
                    mid,
                    bundles,
                    f"{variants}/{variant_known}" if variant_known else None,
                ],
                ids,
            )
        )
        # 各维度都要求可见证据；缺失时不补一个中间分，也不输出完整评分。
        demand = (
            min(
                5,
                1
                + int(threshold >= 50)
                + int(threshold >= 250)
                + int(threshold >= 1000)
                + int(threshold >= 5000),
            )
            if threshold is not None
            else None
        )
        pressure = (
            min(
                5,
                1
                + int(concentration >= D(".25"))
                + int(concentration >= D(".5"))
                + int(concentration >= D(".75"))
                + int(threshold is not None and threshold >= 1000),
            )
            if concentration is not None
            else None
        )
        margin = _profit(mid, costs)[1] / mid if costs and mid and mid > 0 else None
        profit_score = (
            1 + sum(margin >= v for v in (D(0), D(".1"), D(".2"), D(".3")))
            if margin is not None
            else None
        )
        # 门槛和需求共用同一个代理信号时不应互相抵消；进入难度另外计套装门槛。
        entry = (
            None
            if threshold is None
            else (
                1
                + int(threshold >= 250)
                + int(threshold >= 1000)
                + int(threshold >= 5000)
                + int(bundles * 2 >= len(ids))
            )
        )
        total = (
            demand + (6 - pressure) + profit_score + (6 - entry)
            if all(score is not None for score in (demand, pressure, profit_score, entry))
            else None
        )
        grade = (
            "优先验证"
            if total is not None and total >= 15
            else "审慎验证"
            if total is not None and total >= 10
            else "低优先"
            if total is not None
            else "证据不足"
        )
        matrix.append(([name, demand, pressure, profit_score, entry, total, grade, mid], ids))
        if total is not None:
            rankings.append((total, name, mid, ids))
        sourcing.append(
            (
                [
                    name,
                    " / ".join(p.title[:120] for p in top[:2]),
                    costs.lean_units if costs else None,
                    costs.verified_quote_note if costs else None,
                    "待验证材质、尺寸、包装、供应商 MOQ 与交期；"
                    "检查样品、商标/外观专利、标签及适用类目合规",
                ],
                [p.asin for p in top],
            )
        )
    best = max(rankings, key=lambda row: row[0]) if rankings else None
    sections = [
        CategorySection(
            key="opportunities",
            title="细分机会与价格分布",
            reqs=["REQ-012", "REQ-013", "REQ-014"],
            notes=[
                f"本次 {len(boards)} 个榜单，跨榜按 ASIN 去重后 {len(unique)} 件；"
                "子榜内的原排名及来源独立保留。",
                "子榜分组可重叠，无子榜时按主榜标题词回退。"
                "价格只计单一 USD 报价；评价数只是需求代理。",
                "优先复核有报价且评分量可见的细分；样本少或缺价时先补数据，不把榜单当全市场。",
            ],
            tables=[
                _table(
                    "细分报价与需求代理",
                    [
                        "细分",
                        "样本数",
                        "USD 覆盖",
                        "最低价",
                        "中位价",
                        "最高价",
                        "中位评分",
                        "评分数中位",
                        "USD 价带样本数（<20 / 20–<40 / 40–<75 / 75+）",
                        "机会与复核动作",
                    ],
                    opportunities,
                )
            ],
        ),
        CategorySection(
            key="competition",
            title="竞争格局与进入门槛",
            reqs=["REQ-015"],
            notes=[
                "头部品牌占比只以明确品牌样本为分母；"
                "未知品牌不计入其他品牌，也不以标题首词猜品牌。",
                "套装为标题描述的代理；榜单没公开规格关系时标未知。"
                "评分数中位作为进入门槛线索，不是销售门槛。",
            ],
            tables=[
                _table(
                    "头部竞争与变体证据",
                    [
                        "细分",
                        "品牌覆盖",
                        "头部品牌",
                        "头部品牌样本占比 %",
                        "评分门槛代理",
                        "头部商品原排名",
                        "中位价 USD",
                        "套装信号",
                        "可见变体数",
                    ],
                    competition,
                )
            ],
        ),
        _keyword_section(run, panels),
        profit,
        CategorySection(
            key="summary",
            title="市场机会评分与综合报告",
            reqs=["REQ-019", "REQ-020"],
            notes=[
                "评分各 1–5 分：需求依据评分数 50/250/1,000/5,000；"
                "进入难度看评分数 250/1,000/5,000 和半数商品存在套装信号；"
                "竞争压力依据品牌占比 25/50/75% 与评价门槛；利润依据贡献率 0/10/20/30%。",
                "总分 = 需求 + (6 − 竞争压力) + 利润 + (6 − "
                "进入难度)，15+ 优先验证、10–14 审慎验证；缺任何维度不排名。",
                f"当前优先验证：{best[1]}，目标价格参考样本中位 {best[2]} USD。"
                if best
                else "证据不足以推荐细分；先确认采样和成本。",
                "30 天：询价、实测规格与合规核查；60 天：验证首批转化和成本；"
                "90 天：按阈值扩款、完善品牌内容和广告结构。",
                "供应商报价只有用户明确填写才视为「用户标记已核实」，平台没有独立核验。",
            ],
            tables=[
                _table(
                    "可回查的机会评分矩阵",
                    ["细分", "需求", "竞争压力", "利润", "进入难度", "总分", "等级", "目标价 USD"],
                    matrix,
                ),
                _table(
                    "供货与风险复核",
                    [
                        "细分",
                        "主流商品规格起点（标题）",
                        "首批数量假设",
                        "已核实报价（用户填）",
                        "待人工验证",
                    ],
                    sourcing,
                ),
            ],
        ),
    ]
    return CategoryAnalysis(
        status="complete" if costs and best and unique else "incomplete", sections=sections
    )
