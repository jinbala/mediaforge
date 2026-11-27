import re
from collections import Counter, defaultdict

from mediaforge.workers.amazon_skill_support import (
    UNKNOWN,
    D,
    asin,
    money,
    number,
    percent,
    pipe_rows,
    positive,
    rank_series,
    rate,
    ratio,
    score_out_of_five,
    section,
)


def _score(v):
    rows = []
    for candidate, demand, pressure, margin, difficulty, evidence in pipe_rows(v["data"], 6):
        demand, pressure, difficulty = (
            score_out_of_five(x) for x in (demand, pressure, difficulty)
        )
        profit = rate(margin)
        total = D(6) * demand + D(5) * (6 - pressure) + D(20) * profit + D(5) * (6 - difficulty)
        rows.append(
            (
                total,
                candidate,
                int(demand),
                int(pressure),
                percent(profit),
                int(difficulty),
                evidence,
            )
        )
    return [
        section(
            "选品评分卡",
            *[(*row[1:], money(row[0])) for row in sorted(rows, reverse=True)],
            columns=["候选", "需求", "竞争压力", "贡献率", "进入难度", "证据", "总分/100"],
        ),
        section(
            "权重与动作",
            ("规则", "需求 30%、反向竞争 25%、贡献率 20%、反向难度 25%。"),
            ("动作", "排序仅用于选出优先验证样品；复核评分事实、利润口径和 IP。"),
        ),
    ]


def _profit(v):
    price, units = positive(v["price"]), positive(v["units"], count=True)
    cogs, fixed = number(v["cost"]), number(v["fixed"])
    charges = [(name, number(amount)) for name, amount in pipe_rows(v["fees"], 2)]
    if len({name.casefold() for name, _ in charges}) != len(charges):
        raise ValueError("费用名称不可重复，避免同一费用重复计算。")
    additional = sum((charge for _, charge in charges), D(0))
    total_sales = price * units
    net = (price - cogs - additional) * units - fixed
    return [
        section(
            "净利润拆解 USD",
            ("销售收入", money(total_sales)),
            ("采购成本", money(cogs * units)),
            *[(label, money(amount * units)) for label, amount in charges],
            ("固定成本", money(fixed)),
            ("单件贡献（未分摊固定费）", money(price - cogs - additional)),
            ("净利润", money(net)),
            ("净利率", percent(net / total_sales)),
        ),
        section("对账", ("费用覆盖", "对照广告、退货损失、仓储、佣金等是否重叠或遗漏。")),
    ]


def _ranking(v):
    return [
        rank_series(v, "原始排名"),
        section(
            "事件与归因边界",
            ("样本", f"{asin(v['asin'])} · {v['keyword']}"),
            ("同期事实", v.get("events", UNKNOWN)),
            ("复核", "若波动明显，逐日核对价格、促销、库存及竞争；相关性不等于归因。"),
        ),
    ]


def _reprice(v):
    current = positive(v["price"])
    floor = max(D("0.01"), number(v["cost"]) + number(v["floor_margin"]))
    ceiling = positive(v["ceiling"])
    step, competition = positive(v["step"]), positive(v["competitor"])
    if floor > ceiling:
        raise ValueError("成本底价高于上限，无法给出可执行的边界。")
    if not floor <= current <= ceiling:
        suggestion = "当前价超边界，人工复核后调整"
    else:
        bounded = min(ceiling, current + step, max(floor, current - step, competition))
        suggestion = money(bounded) + " USD" if bounded != current else "维持现价"
    return [
        section(
            "价带与触发草案",
            ("底价／上限 USD", f"{money(floor)} / {money(ceiling)}"),
            ("同条件竞品价", money(competition)),
            ("建议", suggestion),
            ("停止条件", "商品、税费、配送条件、数据时间或库存不可比时，不跟价。未写入平台。"),
        )
    ]


def _returns(v):
    rows = []
    for cause, qty, loss, evidence in pipe_rows(v["data"], 4):
        qty = number(qty, count=True)
        action = (
            "核对实测尺寸、尺码图与退货变体"
            if re.search("尺码|尺寸|fit|size", cause, re.I)
            else "核对保护包装与运输环节"
            if re.search("破损|包装|damage|pack", cause, re.I)
            else "复核商品与页面承诺、验证样品和说明"
        )
        rows.append((qty * number(loss), cause, int(qty), evidence, action))
    return [
        section(
            "退货根因按损失排序",
            *[
                (cause, qty, money(total), evidence, action)
                for total, cause, qty, evidence, action in sorted(rows, reverse=True)
            ],
            columns=["原因", "件数", "损失 USD", "提供证据", "优先改进"],
        )
    ]


REVIEW_TOPICS = {
    "尺寸与合身": r"size|fit|尺寸|尺码|合身",
    "材质与触感": r"fabric|material|soft|材质|面料|触感|柔软",
    "耐用和做工": r"broken|durab|quality|破损|损坏|质量|做工",
    "安装和使用": r"install|assembl|usability|安装|组装|使用",
    "包装和配送": r"ship|packag|包装|运输|配送",
    "价格和价值": r"price|value|价格|性价比|划算",
}


def _reviews(v):
    counts = Counter()
    themes = defaultdict(lambda: [0, 0, 0, []])
    for stars, review in pipe_rows(v["data"], 2):
        score = int(score_out_of_five(stars))
        counts[score] += 1
        bucket = 0 if score <= 2 else 1 if score == 3 else 2
        signals = [
            name for name, pattern in REVIEW_TOPICS.items() if re.search(pattern, review, re.I)
        ]
        for name in signals or ["其他／人工归类"]:
            cell = themes[name]
            cell[bucket] += 1
            if len(cell[3]) < 2:
                cell[3].append(review[:100])
    rows = [
        (name, *parts[:3], "；".join(parts[3]))
        for name, parts in sorted(themes.items(), key=lambda pair: (-pair[1][0], pair[0]))
    ]
    return [
        section("评分分布", *[(f"{score} 星", counts[score]) for score in range(1, 6)]),
        section(
            "主题按高优先投诉排序",
            *rows,
            columns=["主题", "负向 1–2 星", "3 星", "正向 4–5 星", "匿名片段"],
        ),
        section(
            "解释",
            ("情感", "仅以星级作代理；单条可命中多个主题。"),
            ("行动", "先复核负向最集中的主题，再用高星提及提炼购买动机和待验证卖点。"),
        ),
    ]


def _review_requests(v):
    eligible, requested = (number(v[key], count=True) for key in ("delivered", "requested"))
    coverage = ratio(requested, eligible)
    return [
        section(
            "合规邀评覆盖与跟进",
            ("已邀评覆盖", percent(coverage)),
            ("待后台核对", int(eligible - requested)),
            (
                "流程",
                "仅用平台允许的功能，在后台核对资格与时段，不重复请求，不要求好评或提供利益。",
            ),
            ("售后", "独立处理用户问题，不以删评或改评为条件。问题摘要：" + v["issues"]),
            ("跟进", "用同口径比较覆盖和问题解决率；不给特定人群选择性邀评。"),
        )
    ]


def _estimate(v):
    bsr = positive(v["rank"], count=True)
    ref = {}
    for rank, low, mid, high in pipe_rows(v["data"], 4):
        r = positive(rank, count=True)
        band = tuple(number(x, count=True) for x in (low, mid, high))
        if r in ref or not band[0] <= band[1] <= band[2]:
            raise ValueError("标定排名不得重复，且销量低≤中≤高。")
        ref[r] = band
    keys = sorted(ref)
    # 只在实际标定范围内插值；不把其他类目或更高/低排名的曲线外推。
    estimate = None
    if bsr in ref:
        estimate = ref[bsr]
    elif len(keys) >= 2 and keys[0] < bsr < keys[-1]:
        a, b = next((a, b) for a, b in zip(keys, keys[1:]) if a < bsr < b)
        weight = (bsr.ln() - a.ln()) / (b.ln() - a.ln())
        estimate = tuple(x + (y - x) * weight for x, y in zip(ref[a], ref[b], strict=True))
    return [
        section(
            "月销量估算（非平台销量）",
            ("站点及类目", "US · " + v["category"]),
            ("采样与版本", v["source"]),
            ("季节适用性", v["season"]),
            ("参考 BSR", int(bsr)),
            (
                "低／中／高件数",
                " / ".join(money(x) for x in estimate) if estimate is not None else "未覆盖该排名",
            ),
            ("方法", "直接取同排名样本；否则按相邻 BSR 的自然对数做区间内插值，不作区间外预测。"),
        ),
        section(
            "用户提供的标定点",
            *[(int(key), *(int(x) for x in ref[key])) for key in keys],
            columns=["BSR", "月低", "月中", "月高"],
        ),
    ]


def _search(v):
    rows = []
    for term, relevance, indexed, rank, impressions, clicks in pipe_rows(v["data"], 6):
        if relevance not in {"高", "低"} or indexed not in {"是", "否", "未知"}:
            raise ValueError("相关性填高或低；索引填是、否或未知。")
        views, visits = (number(x, count=True) for x in (impressions, clicks))
        ctr = ratio(visits, views)
        pos = None if rank == "未见" else positive(rank, count=True)
        action = (
            "不硬加无关词"
            if relevance == "低"
            else "先复核索引、类目和词覆盖"
            if indexed != "是"
            else "复核排名、首图和标题点击表现"
        )
        rows.append((term, relevance, indexed, int(pos) if pos else "未见", percent(ctr), action))
    return [
        section(
            "SEO 索引与表现诊断",
            *rows,
            columns=["查询", "相关性", "索引", "名次", "CTR", "优先动作"],
        ),
        section("可比性", ("范围", "固定设备、地区、搜索方式与日期；没有曝光的数据不能推断 CTR。")),
    ]


def _season(v):
    lead = number(v["lead"], count=True)
    seen = set()
    rows = []
    for month, units, ads, safety, event in pipe_rows(v["data"], 5):
        month = int(positive(month, count=True))
        if month > 12 or month in seen:
            raise ValueError("月份须为互不重复的 1–12。")
        seen.add(month)
        units, safety = number(units, count=True), number(safety, count=True)
        rows.append(
            (
                month,
                int(units),
                money(number(ads)),
                int(units + safety),
                f"至少提前 {int(lead)} 天",
                event,
            )
        )
    return [
        section(
            "月份、广告与备货情景",
            *sorted(rows),
            columns=["月份", "销量假设", "广告 USD", "备货目标件", "补货窗口", "内容／促销节点"],
        ),
        section(
            "复核",
            ("备货", "单月销量加安全库存；未扣在手与在途，执行前对账。"),
            ("触发", "旺季前核对素材权益、库存、折扣资格和预算上限。"),
        ),
    ]


def _seller(v):
    scope = defaultdict(dict)
    product = {}
    for page, key, low, high, price, phase in pipe_rows(v["data"], 6):
        key = asin(key)
        a, b, value = number(low, count=True), number(high, count=True), number(price)
        if a > b or phase not in {"新品", "成熟", "衰退", "未知"}:
            raise ValueError("销量低值不得超过高值；阶段须为新品、成熟、衰退或未知。")
        ref = (a, b, value, phase)
        if key in product and product[key] != ref:
            raise ValueError("同一 ASIN 跨栏目须使用一致的销量、售价和阶段口径。")
        product[key] = ref
        scope[page][key] = ref

    def summarize(name, samples):
        return (
            name,
            len(samples),
            money(sum(a * value for a, b, value, _ in samples.values())),
            money(sum(b * value for a, b, value, _ in samples.values())),
        )

    phases = Counter(item[-1] for item in product.values())
    return [
        section(
            "跨栏目销售额情景",
            *(summarize(page, aset) for page, aset in scope.items()),
            summarize("全店去重", product),
            columns=["栏目", "ASIN", "月销售额低 USD", "月销售额高 USD"],
        ),
        section(
            "生命周期与运营观察",
            *sorted(phases.items()),
            ("局限", "单栏独立统计，跨栏不可相加；阶段、销量均为用户输入，非平台实际收入。"),
        ),
    ]


def _logistics(v):
    dims = list(pipe_rows(v["dims"], 3))
    if len(dims) != 1:
        raise ValueError("包裹尺寸只允许一行。")
    length, width, height = (positive(cell) for cell in dims[0])
    cw = length * width * height / positive(v["divisor"])
    billable = max(cw, positive(v["weight"]))
    volume = length * width * height / 1728
    storage = volume * number(v["months"]) * number(v["storage"])
    fba = storage + number(v["fulfillment"])
    removal = number(v["removal"])
    fbm = number(v["fbm"])
    return [
        section(
            "体积和计费重量",
            ("体积 ft³", str(volume.quantize(D("0.0001")))),
            ("体积重量 lb", money(cw)),
            ("参考计费 lb", money(billable)),
            ("口径", "长×宽×高÷承运商除数；实重与体积重取大，实际上取整方式按承运商。"),
        ),
        section(
            "同一件 FBA／FBM 比较 USD",
            ("仓储", money(storage)),
            ("FBA 不移除", money(fba)),
            ("FBA 含移除", money(fba + removal)),
            ("FBM", money(fbm)),
            ("差额 FBA 不移除减 FBM", money(fba - fbm)),
            ("限制", "只采用输入报价和周期；平台正式履约费可在 REQ-042 核对。"),
        ),
    ]


ANALYTICS = {
    "REQ-076": _score,
    "REQ-077": _profit,
    "REQ-078": _ranking,
    "REQ-079": _reprice,
    "REQ-080": _returns,
    "REQ-081": _reviews,
    "REQ-082": _review_requests,
    "REQ-083": _estimate,
    "REQ-084": _search,
    "REQ-085": _season,
    "REQ-086": _seller,
    "REQ-087": _logistics,
}
