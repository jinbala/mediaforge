from mediaforge.models.amazon_skills import SkillDefinition, SkillField
from mediaforge.workers.amazon_skill_advanced_definitions import ADVANCED


# 表单与后端用同一份字段定义，避免增加技能时前端字段和服务校验不同步。
def field(key, label, kind="text", required=True, choices=None):
    return SkillField(key=key, label=label, kind=kind, required=required, choices=choices or [])


_TEXTS = {
    "product": "自有商品／ASIN及类目",
    "audience": "目标人群",
    "benefits": "已确认卖点（每行一条）",
    "evidence": "规格或证据来源（名称、版本或日期；不要填写账号凭证）",
    "goal": "业务目标",
    "budget": "可用广告预算 USD",
    "price": "标价 USD／件",
    "cost": "单件成本 USD（含所有已知费用）",
    "keywords": "关键词（每行一个）",
    "quantity": "预计数量",
    "inventory": "可用库存",
    "source": "授权报表名称及统计日期（不要填写账号凭证）",
}


def f(key, kind="text", required=True, label=None):
    return field(key, label or _TEXTS[key], kind, required)


def choice(key, label, *choices):
    return field(key, label, "choice", choices=list(choices))


def skill(req, title, *fields, note=None):
    return SkillDefinition(
        req=f"REQ-{req:03}", title=title, fields=list(fields), **({"note": note} if note else {})
    )


SKILLS = {
    s.req: s
    for s in (
        skill(
            45, "A+ 详情内容", f("product"), f("audience"), f("benefits", "lines"), f("evidence")
        ),
        skill(
            46,
            "广告总体策略",
            f("product"),
            f("goal"),
            f("budget", "number"),
            choice("phase", "阶段", "新品", "增长", "成熟"),
        ),
        skill(
            47,
            "后台搜索词",
            f("keywords", "lines"),
            f("excluded", "lines", False, "已使用或禁止词（每行一个）"),
        ),
        skill(
            48,
            "品牌分析",
            f("source"),
            choice("report_type", "报表类型", "搜索查询表现", "购物篮"),
            f(
                "data",
                "lines",
                True,
                "搜索：查询词 | 总搜索排名 | 市场点击 | 品牌点击 | "
                "市场转化 | 品牌转化；购物篮：关联商品 | 联购数 | 订单数",
            ),
        ),
        skill(
            49,
            "品牌注册",
            f("product"),
            choice("mark", "商标类型", "文字", "图形"),
            choice("status", "商标状态", "已注册", "申请中", "未申请"),
            f("evidence", required=False),
        ),
        skill(
            50,
            "品牌定向促销",
            f("audience"),
            f("goal"),
            f("price", "number"),
            f("cost", "number"),
            f("discount", "number", label="折扣 %"),
            f("quantity", "number"),
            f("inventory", "number"),
        ),
        skill(
            51,
            "购物车资格分析",
            f("product"),
            f("price", "number"),
            f("comp", "number", False, "同条件竞品报价 USD"),
            f("inventory", "number"),
            choice("delivery", "配送", "FBA", "FBM"),
            f("metrics", "lines", False, "卖家后台指标或资格提示（每行一条）"),
        ),
        skill(
            52,
            "类目解锁",
            f("product"),
            choice("restriction", "后台状态", "待检查", "需申请", "被拒绝", "未提示限制"),
            f("documents", "lines", False, "现有资质、发票、授权及测试文件（只填类型）"),
        ),
        skill(
            53,
            "竞品分析",
            f("product"),
            f(
                "data",
                "lines",
                True,
                "逐行输入 ASIN | 价格USD | 评分 | 评价数 | 卖点/属性 | 变体/定位",
            ),
            f("listing", "lines", False, "可选：ASIN | 已复核的 Listing 表达（每行一条）"),
            f("source"),
        ),
        skill(
            54,
            "优惠券策略",
            f("price", "number"),
            f("cost", "number"),
            f("discount", "number", label="券面 %"),
            f("fee", "number", label="每单费用 USD"),
            f("fixed", "number", label="固定活动费 USD"),
            f("stack", "number", label="叠加折扣 %（无填 0）"),
            f("quantity", "number"),
        ),
        skill(
            55,
            "分时投放",
            f("source"),
            f("zone", label="统计时区"),
            f("data", "lines", True, "逐行输入 时段 | 花费USD | 广告销售USD | 转化单数"),
            f("acos", "number", label="目标 ACOS %"),
        ),
        skill(
            56,
            "促销机会",
            f("price", "number"),
            f("cost", "number"),
            f("inventory", "number"),
            f(
                "data",
                "lines",
                True,
                "逐行输入 活动类型（秒杀/优惠券/折扣） | 折扣% | 固定费USD | 单件费USD | 预计件数",
            ),
        ),
        skill(
            57,
            "展示广告",
            f("product"),
            f("audience"),
            f("goal"),
            f("budget", "number"),
            f("evidence", required=False),
        ),
        skill(
            58,
            "品牌故事内容",
            f("product"),
            f("audience"),
            f("benefits", "lines"),
            f("story", label="真实品牌故事／使命"),
            f("evidence"),
        ),
        skill(
            59,
            "FBA 费用计算",
            f("cost", "number", label="单件采购与头程成本 USD（不含 FBA 明细费用）"),
            f("quantity", "number", label="订单件数"),
            note="复用 REQ-042 已合并的计算器、版本、日期和缺项规则，另输出订单利润。",
        ),
        skill(
            60,
            "FBA 入库准备",
            f("product"),
            f("quantity", "number"),
            f("boxes", "number", label="箱数"),
            choice("handling", "商品包装属性", "普通", "易碎", "液体", "纺织", "电池/待确认危险品"),
            choice("label_type", "标签方案", "FNSKU", "制造商条码", "后台待确认"),
            f("plan", required=False, label="Seller Central 货件计划参考编号（可选）"),
        ),
    )
}

SKILLS.update(ADVANCED)
