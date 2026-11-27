import re

from mediaforge.workers.amazon_skill_support import (
    UNKNOWN,
    D,
    asin,
    ceil_count,
    day,
    lines,
    money,
    number,
    percent,
    pipe_rows,
    positive,
    rank_series,
    rate,
    score_out_of_five,
    section,
)


def _international(v):
    ranked = []
    for market, demand, pressure, margin, gap in pipe_rows(v["data"], 5):
        opportunity = score_out_of_five(demand) + 6 - score_out_of_five(pressure) + rate(margin) * 5
        ranked.append((opportunity, market, demand, pressure, margin, gap))
    return [
        section(
            "站点与试点优先级",
            *[
                (market, money(s), demand, pressure, margin + "%", gap)
                for s, market, demand, pressure, margin, gap in sorted(ranked, reverse=True)
            ],
            columns=["站点", "试点评分", "需求", "竞争压力", "净贡献率", "合规／配送缺口"],
        ),
        section(
            "试点门槛",
            ("数据", v["source"]),
            ("合规", "核对当地认证、税务、标签、商标和退货责任。"),
            ("试点", "通过合规和配送核对后再少量验证；评分＝需求＋反向竞争压力＋贡献率×5。"),
        ),
    ]


def _local_listing(v):
    currency = v["currency"].upper()
    if not re.fullmatch("[A-Z]{3}", currency):
        raise ValueError("目标币种须为三字母代码。")
    fx = positive(v["fx"])
    converted = positive(v["price"]) * fx + number(v["adjustment"])
    return [
        section(
            "跨站点文案核对",
            *pipe_rows(v["content"], 3),
            columns=["原文", "用户提供的目标语言草案", "本地化核对"],
        ),
        section(
            "同步与价格",
            ("目的地", v["market"]),
            ("目标报价基准", f"{money(converted)} {currency} = 源价 × {fx} + 目标币附加成本"),
            ("来源和日期", v["source"]),
            ("流程", "先核单位、字符长度、禁用词、当地税费和类目，再人工发布。"),
            ("取整", "报价仅显示两位小数，实际发布时按目标币种及站点定价规则处理。"),
        ),
    ]


def _inventory(v):
    result = []
    seen = set()
    for sku, daily, available, incoming, lead, buffer, age in pipe_rows(v["data"], 7):
        if sku in seen:
            raise ValueError("每个 SKU 只能有一条库存记录。")
        seen.add(sku)
        demand = number(daily)
        stock, transit, lag, safety, aged = [
            number(x, count=True) for x in (available, incoming, lead, buffer, age)
        ]
        target = ceil_count(demand * (lag + safety))
        gap = max(D(0), target - stock - transit)
        cover = money(stock / demand) if demand else UNKNOWN
        flags = "交期内可能缺货" if stock < demand * lag else "复核在途到货"
        if aged >= 181:
            flags += "；库龄已达 181 天，核对适用附加费及处置"
        result.append((sku, cover, target, int(gap), flags))
    return [
        section(
            "补货与库龄", *result, columns=["SKU", "可售覆盖天", "目标件数", "建议补货件数", "提醒"]
        ),
        section(
            "口径",
            ("补货", "目标＝日均销量×（交期＋安全天），向上取整；扣除可售与在途。"),
            ("限制", "缺货预警按当前可售计算，不能假设在途一定准时。"),
        ),
    ]


def _keywords(v):
    output = []
    seen = set()
    for key, current, previous, results in pipe_rows(v["data"], 4):
        if key.casefold() in seen:
            raise ValueError("同一关键词只能有一组可比数据。")
        seen.add(key.casefold())
        now, before, n = [number(x, count=True) for x in (current, previous, results)]
        delta = (now - before) / before if before else None
        metric = now / n if n else None
        output.append((metric, key, int(now), int(n), percent(delta), money(metric)))
    output.sort(key=lambda row: (row[0] is None, -(row[0] or 0), row[1]))
    longtails = []
    for word in lines(v.get("modifiers", "")) or ["尺寸", "材质", "适用场景"]:
        proposal = v["seed"] + " " + word
        if proposal.casefold() not in seen:
            seen.add(proposal.casefold())
            longtails.append((proposal, "规则组合，未采样"))
    return [
        section(
            "需求与竞争比较",
            *[
                (key, volume, results, delta, score)
                for _, key, volume, results, delta, score in output
            ],
            columns=["词", "提供的本期搜索量", "可比结果数", "较前期", "量／结果数"],
        ),
        section("长尾候选", *longtails, columns=["候选", "证据状态"]),
        section(
            "口径",
            ("来源", v["source"]),
            ("季节性", "两期变化仅为线索；至少再看上一季或去年同期。"),
            ("机会值", "量／结果数仅用于同口径排序，分母为零不计算。"),
        ),
    ]


def _keyword_monitor(v):
    return [
        rank_series(v),
        section(
            "追踪口径",
            ("关键词", v["keyword"]),
            ("来源", v["source"]),
            ("复核", "尽量固定站点、区域、设备和样本深度。未见不视为 0 或最末排名。"),
        ),
    ]


def _photo(v, listing):
    features = lines(v["claims"] if listing else v["features"])
    rows = [
        ("1 · 主图", "只显示实际套装物品，清晰还原外观；按类目核对白底、道具和构图约束。"),
        ("2 · 核心卖点", features[0] + "；近景、自然质感，不添加未证实文案。"),
        ("3 · 尺寸", v["dimensions"] + "；标出测量端点与单位。"),
        ("4 · 场景", v["audience"] + "；保留实物比例，准备一张横图及手机端裁切。"),
        ("5 · 结构细节", features[min(1, len(features) - 1)] + "；安排侧光、细节特写。"),
        ("6 · 内容物", "逐项展示实际包装内容，外部道具明示不附带。"),
        ("7 · 使用说明", "从真实操作流程提取三步，确认文字在移动端清晰。"),
    ]
    return [
        section("七图位与拍摄任务", *rows, columns=["图位", "画面与要求"]),
        section(
            "拍摄复核",
            ("对象", v["product"]),
            ("交付", "保留原片、取景与布光说明，逐项核对颜色、材质与知识产权。"),
        ),
    ]


def _listing(v):
    keywords = lines(v["keywords"])
    bullets = lines(v["bullets"])
    facts = v["evidence"]
    rows = []
    missing = []

    def contains(word, text):
        return bool(re.search(r"(?<!\w)" + re.escape(word) + r"(?!\w)", text, re.I))

    for word in keywords:
        title_hit = contains(word, v["title"])
        bullet_hit = any(contains(word, line) for line in bullets)
        if not (title_hit or bullet_hit):
            missing.append(word)
        rows.append((word, "命中" if title_hit else "缺", "命中" if bullet_hit else "缺"))
    return [
        section("关键词覆盖审计", *rows, columns=["词", "标题", "五点"]),
        section(
            "可编辑 Listing 草案",
            ("标题草案", f"{v['product']}｜[品牌]｜[核实材质/规格]｜[主要场景]"),
            (
                "五点草案",
                "核心卖点、主要使用场景、尺寸兼容性、包装清单、使用限制。"
                f"当前不同要点 {len(bullets)} 条。",
            ),
            ("可验证说明", facts),
            ("待验证后台词", " ".join(missing) or "无缺口"),
            ("核对", "仅在与产品一致时采用缺失词；后台词按 REQ-047 复核长度和重复。"),
        ),
    ]


def _negative(v):
    threshold = positive(v["min_clicks"], count=True)
    rows = []
    for term, clicks, spend, orders, intent in pipe_rows(v["data"], 5):
        clicks, orders = number(clicks, count=True), number(orders, count=True)
        spend = number(spend)
        if intent not in {"相关", "不相关"} or orders > clicks:
            raise ValueError("意图须为相关或不相关，订单数不能大于点击数。")
        eligible = orders == 0 and clicks >= threshold
        action = (
            "精准否定候选；扩到词组前检查是否误伤"
            if eligible and intent == "不相关"
            else ("保留词组，复核此精确词和落地页" if eligible else "暂不否定")
        )
        rows.append(
            (
                term,
                int(clicks),
                int(orders),
                money(spend),
                action,
                money(spend) if eligible and intent == "不相关" else "不估算",
            )
        )
    return [
        section(
            "否定词与潜在可避免支出",
            *rows,
            columns=["搜索词", "点击", "订单", "历史花费 USD", "建议", "最多可避免 USD"],
        ),
        section(
            "估算边界",
            ("说明", "仅估算历史窗口的上限；不代表未来节省，不将相关或已转化的词组一概排除。"),
        ),
    ]


def _niche(v):
    rows = []
    for market, d, p, m, g in pipe_rows(v["data"], 5):
        d, p, m = score_out_of_five(d), score_out_of_five(p), rate(m)
        growth = number(g.lstrip("-"), percentage=True) * (-1 if g.startswith("-") else 1)
        score = d + (6 - p) + m * 5 + (growth + 100) / 40
        rows.append((score, market, d, p, m, growth))
    return [
        section(
            "利基优先度",
            *[
                (market, int(d), int(p), percent(m), f"{growth}%", money(score))
                for score, market, d, p, m, growth in sorted(rows, reverse=True)
            ],
            columns=["方向", "需求", "竞争压力", "贡献率", "增长", "机会分"],
        ),
        section(
            "解释与验证",
            ("规则", "需求＋反向竞争＋贡献率×5＋（增长百分数＋100）÷40。"),
            ("验证", "用同一统计口径，并优先对排前的方向复核样本、采购和广告成本。"),
        ),
    ]


def _ppc(v):
    price = positive(v["price"])
    contribution = price - number(v["cost"])
    cvr = rate(v["cvr"])
    words = lines(v["keywords"])
    rows = [
        ("发现层", "自动／广泛", "用低风险预算收集实际搜索词"),
        ("转化层", "精准", " / ".join(words[:12])),
        ("探索层", "词组", " / ".join(words[:6])),
    ]
    if targets := lines(v.get("asins", "")):
        rows.append(("竞品层", "ASIN 定向", " / ".join(asin(value) for value in targets[:12])))
    return [
        section("PPC 分层方案", *rows, columns=["分层", "匹配／定向", "目标"]),
        section(
            "竞价边界",
            ("盈亏平衡 ACOS", percent(contribution / price) if contribution > 0 else "无正向空间"),
            ("盈亏平衡 CPC USD", money(max(D(0), contribution) * cvr)),
            ("公式", "ACOS＝广告前单件贡献／成交价；CPC＝贡献×点击转化率。未执行投放。"),
        ),
    ]


def _prices(v):
    threshold = rate(v["threshold"])
    seen, records = set(), []
    for at, list_price, buy_box, promo in pipe_rows(v["data"], 4):
        timestamp = day(at)
        if timestamp in seen:
            raise ValueError("同一天只能有一条价格记录。")
        seen.add(timestamp)
        records.append(
            (
                timestamp,
                positive(list_price),
                None if buy_box == "未见" else positive(buy_box),
                promo,
            )
        )
    rows, previous = [], None
    for date, listed, buy, promo in sorted(records):
        delta = (listed - previous) / previous if previous is not None else None
        rows.append(
            (
                date,
                money(listed),
                money(buy),
                promo,
                percent(delta) if delta is not None else "基线",
                "复核价格变动" if delta is not None and abs(delta) >= threshold and delta else "—",
            )
        )
        previous = listed
    return [
        section(
            "历史价格与促销",
            *rows,
            columns=["日期", "标价 USD", "购物车价 USD", "促销", "较前样本", "告警"],
        ),
        section(
            "范围",
            ("来源", v["source"]),
            ("提醒", "只比较相邻可见样本，不内插未采集日期，不证明促销导致变化。"),
        ),
    ]


def _private_label(v):
    budget = number(v["budget"])
    rows = []
    for supplier, moq, lead, quote, status in pipe_rows(v["data"], 5):
        if status not in {"已核实", "待确认"}:
            raise ValueError("报价状态须为已核实或待确认。")
        qty = positive(moq, count=True)
        total = qty * positive(quote)
        rows.append(
            (
                supplier,
                int(qty),
                int(positive(lead, count=True)),
                money(total),
                status,
                "预算内可送样"
                if status == "已核实" and total <= budget
                else "先核验或压低首批成本",
            )
        )
    return [
        section(
            "供应商、MOQ 与预算",
            *rows,
            columns=["供应商", "MOQ", "交期天", "首批报价 USD", "核验", "动作"],
        ),
        section(
            "品牌资产及进入计划",
            ("目标人群", v["audience"]),
            ("差异证据", "；".join(lines(v["features"]))),
            ("执行顺序", "样品和使用验证 → 商标与素材权益核查 → 小批试销 → 按贡献与复购决定扩量。"),
        ),
    ]


def _bundles(v):
    rows = []
    for name, separate, cost, offer, qty in pipe_rows(v["data"], 5):
        base, price = positive(separate), positive(offer)
        rows.append(
            (
                name,
                int(positive(qty, count=True)),
                money(price),
                percent((base - price) / base),
                money(price - number(cost)),
                "组合价高于单买，需说明增值" if price > base else "按情景验证",
            )
        )
    return [
        section(
            "组合与阶梯价格",
            *rows,
            columns=["组合", "门槛件数", "组合 USD", "相对单买折扣", "贡献 USD", "风险"],
        ),
        section(
            "复核", ("上架", "核对商品捆绑资格、条码、包装、售后与补货责任；贡献按输入的单件成本。")
        ),
    ]


def _compliance(v):
    checks = {
        "通用": ("核对禁售产品、警告、实际用料及可追溯供应链", "CPSC 商品安全与 Amazon 产品政策"),
        "儿童用品": (
            "按年龄、用途和部件核对 CPSC/CPSIA、测试、CPC 和追溯标签是否适用",
            "https://www.cpsc.gov/Business--Manufacturing",
        ),
        "含电池": (
            "核对化学类型、额定参数、运输测试摘要、危险品申报及承运限制",
            "https://www.phmsa.dot.gov/lithiumbatteries",
        ),
        "纺织": (
            "核对纤维成分、原产地、护理标签及类别相关安全要求是否适用",
            "https://www.ftc.gov/business-guidance/industry/clothing-and-textiles",
        ),
        "食品接触用品": (
            "核对接触材料、温度和食品类型对应的适用要求",
            "https://www.fda.gov/food/food-ingredients-packaging",
        ),
    }
    need, agency = checks[v["type"]]
    return [
        section(
            "美国站合规核对",
            ("商品条件", v["use"]),
            ("已提供文档", v.get("documents", UNKNOWN)),
            ("待核对", need),
            ("权威入口", agency),
            ("限制", "清单不是合规判定；以适用的最新官方规则及后台为准。"),
        )
    ]


GROWTH = {
    "REQ-061": _international,
    "REQ-062": _local_listing,
    "REQ-063": _inventory,
    "REQ-064": _keywords,
    "REQ-065": _keyword_monitor,
    "REQ-066": lambda v: _photo(v, True),
    "REQ-067": _listing,
    "REQ-068": _negative,
    "REQ-069": _niche,
    "REQ-070": _ppc,
    "REQ-071": _prices,
    "REQ-072": _private_label,
    "REQ-073": _bundles,
    "REQ-074": _compliance,
    "REQ-075": lambda v: _photo(v, False),
}
