from collections import Counter, defaultdict

from mediaforge.workers.amazon_skill_support import (
    UNKNOWN,
    asin,
    day,
    lines,
    money,
    number,
    percent,
    pipe_rows,
    positive,
    rate,
    ratio,
    section,
)


def _storefront(v):
    categories = defaultdict(list)
    for category, code, theme in pipe_rows(v["data"], 3):
        row = (asin(code), theme)
        if row not in categories[category]:
            categories[category].append(row)
    return [
        section(
            "店铺信息架构",
            ("首页", f"{v['brand']} · 面向 {v['audience']}"),
            ("品牌故事", v["story"]),
            ("导航", " / ".join(categories)),
            ("商品模块", "只展示自有且可售商品；移动端检查图文裁切和可购锚点。"),
        ),
        section(
            "栏目、商品与素材",
            *(
                (name, code, theme, f"https://www.amazon.com/dp/{code}")
                for name, products in categories.items()
                for code, theme in products
            ),
            columns=["栏目", "ASIN", "素材主题", "商品页"],
        ),
    ]


def _subscriptions(v):
    listed, cost = positive(v["price"]), number(v["cost"])
    discounted = listed * (1 - rate(v["discount"]))
    base, retained = (number(v[k], count=True) for k in ("subscribers", "retained"))
    retained_rate = ratio(retained, base)
    interval = int(positive(v["interval"], count=True))
    return [
        section(
            "订阅周期情景",
            ("周期", f"{interval} 天"),
            ("单件优惠 USD", money(listed - discounted)),
            ("订阅价 USD", money(discounted)),
            ("订阅单件贡献 USD", money(discounted - cost)),
            ("下期保留率", percent(retained_rate)),
            ("下期总贡献 USD", money(retained * (discounted - cost))),
            ("跟进", "按自然消耗周期核对缺货、取消、跳过及续订；折扣、资格以后台为准。"),
        )
    ]


def _appeal(v):
    return [
        section(
            "账户问题与证据",
            ("用户转述的通知", v["reason"]),
            ("渠道", "只在官方支持或绩效通知入口递交。"),
            *[(f"材料 {idx}", evidence) for idx, evidence in enumerate(lines(v["evidence"]), 1)],
        ),
        section(
            "申诉行动计划草案",
            *pipe_rows(v["fixes"], 3),
            columns=["根因", "纠正／预防", "验证证据"],
        ),
        section("提交前复核", ("清单", "逐项对应通知；区分已完成和拟执行，不捏造材料或提交记录。")),
    ]


def _trends(v):
    samples = {}
    for raw_date, rank, reviews in pipe_rows(v["data"], 3):
        at = day(raw_date)
        if at in samples:
            raise ValueError("同一类目每个采样日只允许一条记录。")
        samples[at] = (positive(rank, count=True), number(reviews, count=True))
    pairs = sorted(samples.items())
    launch = None if v["launch"] == "未知" else day(v["launch"])
    if launch is not None and launch > pairs[-1][0]:
        raise ValueError("上线日期不能晚于最近样本。")
    span = (pairs[-1][0] - pairs[0][0]).days
    change = (pairs[0][1][0] - pairs[-1][1][0]) / pairs[0][1][0] if span else None
    age = (pairs[-1][0] - launch).days if launch else None
    return [
        section(
            "公开排名、评分数与新品信号",
            *((at, int(rank), int(reviews)) for at, (rank, reviews) in pairs),
            columns=["日期", "BSR", "评分数"],
        ),
        section(
            "观察与限制",
            ("排名改善率", percent(change) if span else UNKNOWN),
            (
                "新品标记",
                "可见上线未满 90 天"
                if age is not None and age < 90
                else "非近 90 天"
                if age is not None
                else UNKNOWN,
            ),
            ("季节口径", v["season"]),
            ("判断", "至少两个同类目日期才比较趋势，不从一次波动推定销量。"),
        ),
    ]


def _variations(v):
    children = {}
    parent_codes = set()
    used = Counter()
    for parent, child, color, size in pipe_rows(v["data"], 4):
        parent, child = asin(parent), asin(child)
        if parent == child or child in children:
            raise ValueError("子 ASIN 必须唯一，且不得等于父 ASIN。")
        parent_codes.add(parent)
        children[child] = (parent, color, size)
        used[(parent, color.casefold(), size.casefold())] += 1
    if parent_codes & children.keys():
        raise ValueError("同一 ASIN 不可同时作为父与子。")
    return [
        section(
            "父子结构审计",
            *(
                (
                    parent,
                    child,
                    color,
                    size,
                    "重复颜色／尺码"
                    if used[(parent, color.casefold(), size.casefold())] > 1
                    else "待后台验证",
                )
                for child, (parent, color, size) in children.items()
            ),
            columns=["父 ASIN", "子 ASIN", "颜色", "尺码", "核对"],
        ),
        section(
            "允许主题及风险",
            ("主题依据", v["theme"]),
            ("同款事实", v["same"]),
            ("边界", "仅合并功能和包装一致且符合平台主题的真实变体；不能为合并评论而建立关系。"),
        ),
    ]


def _vine(v):
    units = positive(v["units"], count=True)
    fee, per_item = number(v["fee"]), number(v["cost"]) + number(v["ship"])
    return [
        section(
            "Vine 预算核对",
            ("资格状态", v["eligible"]),
            ("提供件数", int(units)),
            ("成本情景 USD", money(fee + units * per_item)),
            ("公式", "登记费＋件数×（单件成本＋配送）"),
            ("时机", "先核对账户、商品、评价数、库存和官方收费档，再选上线窗口。"),
            ("结果", "跟踪登记、实际领取、评价和样品成本，不做评价数量或正面倾向承诺。"),
        )
    ]


def _wholesale(v):
    budget, price, fee = number(v["budget"]), positive(v["price"]), number(v["fees"])
    rows = []
    for supplier, moq, cost, turn, authorization in pipe_rows(v["data"], 5):
        cost = positive(cost)
        qty = positive(moq, count=True)
        days = positive(turn, count=True)
        if authorization not in {"已核实", "待确认"}:
            raise ValueError("授权状态须为已核实或待确认。")
        cap = qty * cost
        contribution = price - cost - fee
        rows.append(
            (
                authorization == "已核实" and cap <= budget and contribution > 0,
                contribution,
                supplier,
                int(qty),
                money(cap),
                int(days),
                authorization,
            )
        )
    return [
        section(
            "批发进货与周转验证",
            *(
                (name, moq, cap, money(profit), turn, auth, "可送样" if ready else "暂缓")
                for ready, profit, name, moq, cap, turn, auth in sorted(rows, reverse=True)
            ),
            columns=["供应商", "MOQ", "首批投入 USD", "单件贡献 USD", "周转天数", "授权", "判断"],
        ),
        section(
            "谈判与验证",
            ("条款", "核对发票和品牌授权链，再谈样品、分批订单、退货和交期违约条款。"),
            ("限制", "贡献不包含未提供的税费或促销成本。"),
        ),
    ]


STOREFRONT = {
    "REQ-088": _storefront,
    "REQ-089": _subscriptions,
    "REQ-090": _appeal,
    "REQ-091": _trends,
    "REQ-092": _variations,
    "REQ-093": _vine,
    "REQ-094": _wholesale,
}
