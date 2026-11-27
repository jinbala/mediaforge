from mediaforge.models.amazon_skills import SkillDefinition, SkillField


# 字段和分隔顺序由目录返回给前端；只标注用户实际提供的证据，不代表平台已验证。
def text(key, label, required=True):
    return SkillField(key=key, label=label, required=required)


def rows(key, label, required=True):
    return SkillField(key=key, label=label, kind="lines", required=required)


def choice(key, label, *options):
    return SkillField(key=key, label=label, kind="choice", choices=list(options))


product = text("product", "自有商品名称／SKU")
source = text("source", "数据来源、适用站点和统计日期（不填账号或凭证）")
price = text("price", "售价 USD／件")
cost = text("cost", "成本或参考报价 USD／件")


def build(req, title, *fields, note="仅供分析或制定方案，不代替平台操作。"):
    group = "经营与增长" if req <= 75 else "数据分析" if req <= 87 else "店铺与供应链"
    return SkillDefinition(
        req=f"REQ-{req:03}", title=title, fields=list(fields), group=group, note=note
    )


ADVANCED = {
    item.req: item
    for item in (
        build(
            61,
            "跨站点经营",
            product,
            source,
            rows("data", "站点 | 需求评分1–5 | 竞争压力1–5 | 净贡献率% | 合规或配送缺口"),
        ),
        build(
            62,
            "跨站点 Listing",
            product,
            source,
            text("market", "目标站点与语言"),
            text("currency", "目标币种代码（ISO 4217）"),
            price,
            text("fx", "每美元兑换目标币金额"),
            text("adjustment", "目标币单件附加成本"),
            rows("content", "原文 | 目标语言审定草案 | 核对项"),
        ),
        build(
            63,
            "库存管理",
            product,
            source,
            rows("data", "SKU | 日均销量 | 可售 | 在途 | 交期天 | 安全天数 | 库龄天"),
        ),
        build(
            64,
            "关键词研究",
            text("seed", "核心种子词"),
            source,
            rows("data", "关键词 | 本期搜索量 | 前期可比搜索量 | 可比结果数"),
            rows("modifiers", "用途或规格修饰词（每行一个，可选）", False),
        ),
        build(
            65,
            "关键词监控",
            text("keyword", "关键词"),
            source,
            rows("data", "采样日期 | 自然排名（未进入样本填 未见）"),
        ),
        build(
            66,
            "Listing 图组策划",
            product,
            text("audience", "目标用户与场景"),
            rows("claims", "已确认卖点及证据（每行一项）"),
            text("dimensions", "实测尺寸与单位"),
        ),
        build(
            67,
            "Listing 优化",
            product,
            text("title", "现有标题"),
            rows("bullets", "现有五点（每行一条）"),
            rows("keywords", "目标词（每行一个）"),
            text("evidence", "可验证的商品事实"),
        ),
        build(
            68,
            "否定关键词",
            source,
            rows("data", "搜索词 | 点击 | 花费USD | 订单 | 意图（相关／不相关）"),
            text("min_clicks", "最低点击门槛"),
        ),
        build(
            69,
            "利基市场",
            source,
            rows("data", "方向 | 需求评分1–5 | 竞争压力1–5 | 贡献率% | 同口径增长%（可为负）"),
        ),
        build(
            70,
            "PPC 广告结构",
            product,
            source,
            price,
            cost,
            text("cvr", "假设点击转化率%"),
            rows("keywords", "高意图词（每行一个）"),
            rows("asins", "竞品 ASIN（每行一个，可选）", False),
        ),
        build(
            71,
            "价格监控",
            product,
            source,
            rows("data", "日期 | 标价USD | 购物车价USD（无填 未见） | 促销说明（无填 无）"),
            text("threshold", "价格变化告警阈值%"),
        ),
        build(
            72,
            "自有品牌规划",
            product,
            text("audience", "目标用户及痛点"),
            rows("features", "可核实的产品差异（每行一条）"),
            rows("data", "供应商代号 | MOQ | 交期天 | 单件报价USD | 核验状态（已核实／待确认）"),
            text("budget", "采购预算 USD"),
            source,
        ),
        build(
            73,
            "商品捆绑",
            product,
            source,
            rows("data", "组合名称 | 单买合计USD | 单件成本USD | 方案价USD | 最低件数"),
        ),
        build(
            74,
            "商品合规",
            product,
            choice("type", "美国站商品类型", "通用", "儿童用品", "含电池", "纺织", "食品接触用品"),
            text("use", "材质、用途与年龄范围"),
            rows("documents", "已备文档（每行一个，可选）", False),
            source,
        ),
        build(
            75,
            "商品摄影规划",
            product,
            text("audience", "目标用户与环境"),
            rows("features", "拍摄需验证的特征（每行一条）"),
            text("dimensions", "实测尺寸与单位"),
        ),
        build(
            76,
            "选品评分",
            source,
            rows("data", "候选 | 需求1–5 | 竞争压力1–5 | 贡献率% | 进入难度1–5 | 证据"),
        ),
        build(
            77,
            "利润分析",
            product,
            source,
            price,
            text("units", "销量（件）"),
            cost,
            rows("fees", "费用名 | 单件费用USD（如退款损失/广告/仓储）"),
            text("fixed", "期内固定成本 USD"),
        ),
        build(
            78,
            "排名监控",
            text("keyword", "关键词或榜单名称"),
            text("asin", "ASIN"),
            source,
            rows("data", "日期 | 名次（未进入填 未见）"),
            rows("events", "已知同期事件（每行一条，可选）", False),
        ),
        build(
            79,
            "重新定价策略",
            product,
            source,
            price,
            cost,
            text("floor_margin", "最低单件贡献 USD"),
            text("ceiling", "售价上限 USD"),
            text("competitor", "同口径竞品价 USD"),
            text("step", "最大单次调整 USD"),
        ),
        build(
            80,
            "降低退货",
            product,
            source,
            rows("data", "原因 | 退货件数 | 每件损失USD | 规格或商品页证据"),
        ),
        build(
            81, "评论分析", product, source, rows("data", "星级1–5 | 匿名评论片段（避免个人信息）")
        ),
        build(
            82,
            "评论获取策略",
            product,
            source,
            text("delivered", "符合核对口径的已送达订单数"),
            text("requested", "已按平台允许方式请求的订单数"),
            text("issues", "需售后跟进问题（不填个人信息）"),
        ),
        build(
            83,
            "销量估算",
            product,
            text("category", "标定类目"),
            text("rank", "观察到的 BSR"),
            source,
            rows("data", "同站同类目 BSR | 月销量下限 | 月销量中值 | 月销量上限"),
            text("season", "季节和标定适用口径"),
        ),
        build(
            84,
            "搜索优化",
            product,
            source,
            rows(
                "data",
                "搜索词 | 相关性（高／低） | 已索引（是／否／未知） | "
                "自然排名（未见） | 曝光 | 点击",
            ),
        ),
        build(
            85,
            "季节运营规划",
            product,
            source,
            rows("data", "月份1–12 | 预计销量 | 计划广告USD | 安全库存件数 | 业务节点"),
            text("lead", "补货交期天"),
        ),
        build(
            86,
            "卖家分析",
            source,
            rows(
                "data",
                "栏目 | ASIN | 月销量低 | 月销量高 | 单价USD | 阶段（新品／成熟／衰退／未知）",
            ),
        ),
        build(
            87,
            "物流与履约估算",
            product,
            source,
            rows("dims", "包裹长 | 宽 | 高（英寸，只填一行）"),
            text("weight", "实际重量 lb"),
            text("divisor", "承运商体积重除数"),
            text("months", "预计仓储月数"),
            text("storage", "仓储报价 USD/ft³/月"),
            text("fulfillment", "FBA 履约报价 USD/件"),
            text("removal", "移除报价 USD/件（不适用填0）"),
            text("fbm", "FBM 履约及对应仓储报价 USD/件"),
        ),
        build(
            88,
            "品牌店铺设计",
            text("brand", "品牌名称"),
            text("audience", "目标人群"),
            text("story", "已验证品牌故事"),
            rows("data", "系列/栏目 | 可售 ASIN | 页面主图主题"),
        ),
        build(
            89,
            "订阅保留策略",
            product,
            source,
            price,
            cost,
            text("discount", "订阅折扣%"),
            text("retained", "预计保留下期订户数"),
            text("subscribers", "起始订户数"),
            text("interval", "配送周期天"),
        ),
        build(
            90,
            "账户申诉",
            source,
            text("reason", "官方通知中的问题摘要（不要填写账户或人员信息）"),
            rows("evidence", "已核实的证据或记录（每行一条）"),
            rows("fixes", "根因 | 已执行或拟执行修正 | 验证证据"),
        ),
        build(
            91,
            "趋势与新品识别",
            product,
            source,
            text("launch", "可见上线日期 YYYY-MM-DD 或 未知"),
            rows("data", "日期 | 同一类目 BSR | 公开评分数"),
            text("season", "该类目的旺淡季信息；未知填 未知"),
        ),
        build(
            92,
            "变体策略",
            product,
            source,
            text("theme", "后台允许主题与来源"),
            rows("data", "父 ASIN | 子 ASIN | 颜色 | 尺码"),
            choice("same", "同款、功能与包装是否已核实", "已核实同款", "待核对"),
        ),
        build(
            93,
            "Vine 计划",
            product,
            source,
            choice("eligible", "后台资格状态", "已确认符合", "未确认", "不符合"),
            text("units", "计划提供件数"),
            text("fee", "后台登记费 USD"),
            cost,
            text("ship", "配送 USD／件"),
        ),
        build(
            94,
            "批发选品",
            product,
            source,
            price,
            rows("data", "供应商代号 | MOQ | 单件到仓成本USD | 周转天数 | 授权（已核实／待确认）"),
            text("fees", "预计销售费用 USD／件"),
            text("budget", "可用于首批采购的预算 USD"),
        ),
        build(
            95,
            "关税与到岸成本",
            note="复用 REQ-043 到岸成本和 REQ-044 USITC 查询；"
            "候选仅供核对，由用户确认税号及税费后再计算。",
        ),
    )
}
