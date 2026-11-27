import re
import unicodedata
from datetime import UTC, datetime
from decimal import Decimal

from mediaforge.models.amazon_skills import SkillId, SkillReport, SkillRequest
from mediaforge.rag.amazon_script_samples import lookup_hs
from mediaforge.workers.amazon_fba_fees import calculate_fba, currency_round
from mediaforge.workers.amazon_landed_cost import calculate_landed
from mediaforge.workers.amazon_skill_analytics import ANALYTICS
from mediaforge.workers.amazon_skill_definitions import SKILLS
from mediaforge.workers.amazon_skill_growth import GROWTH
from mediaforge.workers.amazon_skill_storefront import STOREFRONT
from mediaforge.workers.amazon_skill_support import money, number, percent, pipe_rows, section

ADVANCED_HANDLERS = {**GROWTH, **ANALYTICS, **STOREFRONT}

D = Decimal
BRAND_REGISTRY = "https://brandservices.amazon.com/brandregistry"
SELLER = "https://sellercentral.amazon.com/"


def _content(values, story=False):
    product, audience = values["product"], values["audience"]
    rows = [("首屏横幅", f"{product} · 面向 {audience}；实物场景横图、预留移动端安全区。")]
    if story:
        rows.append(
            ("品牌故事", values["story"] + "；用品牌素材和真实里程碑图，不添加未证实承诺。")
        )
    for i, feature in enumerate(values["benefits"].splitlines()[:6], 1):
        if feature.strip():
            rows.append(
                (
                    f"卖点模块 {i}",
                    f"{feature.strip()}；补可验证的细节图、实测尺寸／场景，图文保持同一事实。",
                )
            )
    rows.extend(
        [
            ("对比模块", "仅对比自有商品，逐栏列实测规格、适用对象和场景；无资料不填。"),
            ("证据与发布", values["evidence"] + "；核对图片权益、品牌资格及平台内容审核。"),
        ]
    )
    return section("高级 A+ / 品牌故事布局" if story else "A+ 模块与制作需求", *rows)


def _brand_report(values):
    if values["report_type"] == "购物篮":
        result = []
        for product, paired, orders in pipe_rows(values["data"], 3):
            a, b = number(paired, count=True), number(orders, count=True)
            if a > b:
                raise ValueError("联购数不得大于订单数。")
            result.append((product, int(a), int(b), percent(a / b if b else None)))
        return section(
            "购物篮同购证据", *result, columns=["关联商品", "联购数", "订单数", "联购率"]
        )
    result = []
    for word, rank, market_click, brand_click, market_conversion, brand_conversion in pipe_rows(
        values["data"], 6
    ):
        r, mc, bc, mv, bv = (
            number(v, count=True)
            for v in (rank, market_click, brand_click, market_conversion, brand_conversion)
        )
        if r < 1 or bc > mc or bv > mv:
            raise ValueError("总搜索排名必须至少为 1，品牌点击或转化不得大于市场总数。")
        result.append(
            (
                word,
                int(r),
                percent(bc / mc if mc else None),
                percent(bv / mv if mv else None),
                int(mc),
                int(mv),
            )
        )
    return section(
        "品牌授权搜索指标",
        *sorted(result, key=lambda row: row[1]),
        columns=["查询", "总搜索排名", "品牌点击份额", "品牌转化份额", "市场点击", "市场转化"],
    )


def _terms(values):
    # 先归一化、去重复，再按 UTF-8 字节（含分隔空格）校验美国站草案，不截断单个词。
    def tokens(text):
        return re.findall(r"[^\W_]+", unicodedata.normalize("NFKC", text).casefold())

    blocked = set(tokens(values.get("excluded", "")))
    words = [word for word in dict.fromkeys(tokens(values["keywords"])) if word not in blocked]
    chosen, skipped = [], []
    for word in words:
        if len(" ".join([*chosen, word]).encode("utf-8")) < 250:
            chosen.append(word)
        else:
            skipped.append(word)
    return section(
        "后台搜索词（草案）",
        ("建议文本", " ".join(chosen) or "无可用词"),
        ("UTF-8 字节数", len(" ".join(chosen).encode("utf-8"))),
        ("人工复核", "检查竞争对手商标、重复信息、主观词和不相关信息。"),
        ("因长度未纳入", " ".join(skipped) or "无"),
    )


def _promotion(price, cost, discount, fixed, fee, quantity, stack=D(0)):
    # 叠加情景按先扣活动折扣、再扣第二层折扣估算，不假定活动一定允许叠加。
    net = price * (1 - discount) * (1 - stack)
    unit = net - cost - fee
    return net, unit, unit * quantity - fixed


def _dispatch(req, v, nums):
    def n(key):
        return nums.get(key, D(0))

    if req in {"REQ-045", "REQ-058"}:
        return [_content(v, story=req == "REQ-058")]
    if req == "REQ-046":
        allocation = {"新品": (70, 20, 10), "增长": (55, 25, 20), "成熟": (45, 30, 25)}[v["phase"]]
        amounts = [currency_round(n("budget") * part / 100) for part in allocation[:2]]
        amounts.append(n("budget") - sum(amounts))
        return [
            section(
                "广告预算与生命周期（方案）",
                *[
                    (label, f"{allocation[i]}% · {money(amounts[i])} USD · {action}")
                    for i, (label, action) in enumerate(
                        [
                            ("商品推广", "精准关键词与同类 ASIN 测试；按转化、ACOS 和库存复核。"),
                            ("品牌推广", "品牌检索词与品牌店落地页；以点击率、品牌新客衡量。"),
                            ("展示推广", "自有商品访客再触达与同类环境测试；对比频次与增量。"),
                        ]
                    )
                ],
                (
                    "目标与节奏",
                    f"{v['product']} · {v['goal']}；每周复核预算，"
                    "首周收样、次周去无效词、每月重新分配。",
                ),
            )
        ]
    if req == "REQ-047":
        return [_terms(v)]
    if req == "REQ-048":
        return [
            _brand_report(v),
            section(
                "来源与解释",
                ("用户提供的授权汇总", v["source"]),
                ("应用", "同一日期窗口比较份额与联购率；未包含的数据不做推断。"),
            ),
        ]
    if req == "REQ-049":
        return [
            section(
                "品牌注册资格与保护",
                ("商标状态", v["status"] + " · " + v["mark"]),
                (
                    "资格核对",
                    "核对国家/地区、商标主管机关、申请状态及品牌名是否符合 Amazon 当前资格。",
                ),
                (
                    "准备",
                    "与商标一致的品牌名称、登记或申请编号、商品／包装实物照片、主体与授权关系。",
                ),
                ("流程", f"在 {BRAND_REGISTRY} 核资格并申请，按官方通知完成所有权验证。"),
                ("保护", "获准后保留品牌使用证据，设定周期检查；由权利人通过官方入口举报。"),
            )
        ]
    if req == "REQ-050":
        qty = min(n("quantity"), n("inventory"))
        net, unit, total = _promotion(n("price"), n("cost"), n("discount") / 100, D(0), D(0), qty)
        return [
            section(
                "人群与收益假设",
                ("目标人群", v["audience"]),
                ("测试分组", f"先验证符合资格的目标人群，对照留出未触达组：{v['goal']}"),
                (
                    "折后价 / 单件贡献 / 总贡献",
                    f"{money(net)} / {money(unit)} / {money(total)} USD，"
                    f"按预计及可用库存较小值 {qty} 件；未含未填活动费。",
                ),
                ("核算口径", "比较触达/留出组的增量订单、贡献与复购；后台核对人群资格和排期。"),
            )
        ]
    if req == "REQ-051":
        gap = money(n("price") - n("comp")) + " USD" if "comp" in nums else "未提供同条件竞品价"
        return [
            section(
                "购物车资格诊断",
                ("价格相对差", gap),
                (
                    "库存",
                    "无可用库存，优先修正"
                    if n("inventory") == 0
                    else f"剩余 {int(n('inventory'))} 件，避免缺货",
                ),
                ("履约", "核对承诺到货日、区域和退货承诺；" + v["delivery"]),
                (
                    "后台依据",
                    v.get("metrics", "待提供：订单缺陷、预配送取消、晚发、有效跟踪及账户状态。"),
                ),
                ("判断", "价格与库存仅是诊断线索；是否具备或赢得资格请以后台状态为准。"),
            )
        ]
    if req == "REQ-052":
        return [
            section(
                "类目解锁资料清单",
                ("后台状态", v["restriction"]),
                ("自有资料", v.get("documents", "未提供")),
                (
                    "核对路径",
                    f"在 {SELLER} 添加 {v['product']} 后按后台实际限制申请，"
                    "确认品牌和子类目各自权限。",
                ),
                (
                    "有条件提交项",
                    "备齐近期供应链发票、真实性或授权证明、商品/包装照片及按类目要求的检测证件；"
                    "核对后台显示的窗口和数量。",
                ),
                ("拒绝时", "按拒绝原因逐项核对，以后台允许的路径补正，保存提交参考号。"),
            )
        ]
    if req == "REQ-053":
        rows = []
        seen = set()
        for asin, price, rating, count, selling, variation in pipe_rows(v["data"], 6):
            asin = asin.upper()
            if not re.fullmatch(r"[A-Z0-9]{10}", asin) or asin in seen or number(rating) > 5:
                raise ValueError("竞品 ASIN 须为互不重复的 10 位字母数字，评分范围 0–5。")
            seen.add(asin)
            rows.append(
                (
                    asin,
                    money(number(price)),
                    rating,
                    int(number(count, count=True)),
                    selling,
                    variation,
                    f"https://www.amazon.com/dp/{asin}",
                )
            )
        listings = {}
        for asin, text in pipe_rows(v.get("listing", ""), 2):
            asin = asin.upper()
            if asin not in seen or asin in listings:
                raise ValueError("Listing 摘录须对应已列出的竞品 ASIN，每个 ASIN 只填一次。")
            listings[asin] = text
        return [
            section(
                "竞品可追溯对比",
                *[
                    (row[0], *row[1:-1], listings.get(row[0], "未提供"), row[-1])
                    for row in sorted(rows, key=lambda r: D(r[1]))
                ],
                columns=[
                    "ASIN",
                    "价 USD",
                    "评分",
                    "评价数",
                    "卖点/属性",
                    "变体/定位",
                    "Listing 表达",
                    "商品页",
                ],
            ),
            section(
                "目标与验证",
                ("目标", v["product"]),
                ("数据来源", v["source"]),
                ("差异化验证", "沿样本中反馈较强的卖点与价差设计自有规格实验，不复制竞品文案。"),
            ),
        ]
    if req == "REQ-054":
        net, unit, total = _promotion(
            n("price"),
            n("cost"),
            n("discount") / 100,
            n("fixed"),
            n("fee"),
            n("quantity"),
            n("stack") / 100,
        )
        return [
            section(
                "优惠券情景估算",
                ("折后 USD／件", money(net)),
                ("贡献 USD／件", money(unit)),
                ("活动贡献 USD", money(total)),
                ("无活动对照 USD", money((n("price") - n("cost")) * n("quantity"))),
                ("叠加", "按顺序折扣试算；实际券费、有效期和叠加资格须以后台为准。"),
            )
        ]
    if req == "REQ-055":
        result = []
        for slot, spend, sales, orders in pipe_rows(v["data"], 4):
            a, b, c = number(spend), number(sales), number(orders, count=True)
            acos = a / b if b else None
            hint = (
                "样本不足，补采"
                if c < 5 or acos is None
                else "优先保留，低步长测试"
                if acos <= n("acos") / 100
                else "降低出价前先复核转化归因"
            )
            result.append((slot, money(a), money(b), int(c), percent(acos), hint))
        return [
            section(
                f"分时试验 · {v['zone']} · {v['source']}",
                *result,
                columns=["时段", "花费 USD", "广告销售 USD", "单量", "ACOS", "动作"],
            )
        ]
    if req == "REQ-056":
        result = []
        for label, discount, fixed, fee, units in pipe_rows(v["data"], 5):
            if label not in {"秒杀", "优惠券", "折扣"}:
                raise ValueError("活动类型须为秒杀、优惠券或折扣。")
            qty = min(number(units, count=True), n("inventory"))
            net, unit, total = _promotion(
                n("price"),
                n("cost"),
                number(discount, percentage=True) / 100,
                number(fixed),
                number(fee),
                qty,
            )
            result.append((label, int(qty), money(net), money(unit), money(total)))
        return [
            section(
                "按贡献排序的促销机会",
                *sorted(result, key=lambda r: D(r[-1]), reverse=True),
                columns=["类型", "库存约束件数", "折后价 USD", "贡献/件 USD", "贡献合计 USD"],
            ),
            section(
                "复核",
                ("决策条件", "以后台实际活动资格、时段、费率和可用库存核对；销量只是用户情景。"),
            ),
        ]
    if req == "REQ-057":
        retarget = currency_round(n("budget") * D(".60"))
        return [
            section(
                "展示推广账户草案",
                ("对象与目标", f"{v['product']} · {v['audience']} · {v['goal']}"),
                (
                    "再触达",
                    f"{money(retarget)} USD；排除已转化人群（若后台支持），测试频次与转化。",
                ),
                (
                    "竞品触达",
                    f"{money(n('budget') - retarget)} USD；选同类 ASIN/类目，"
                    "按相关性和预算约束启用。",
                ),
                (
                    "对照与素材",
                    "分别制作对照素材，按展示量、点击率、转化和回报周度复核，不执行投放。",
                ),
            )
        ]
    if req == "REQ-060":
        each, extra = divmod(int(n("quantity")), int(n("boxes")))
        if each == 0:
            raise ValueError("箱数不可超过件数。")
        handling = {
            "普通": "检查包装闭合与可扫描面",
            "易碎": "核对缓冲及耐跌落要求",
            "液体": "确认密封、防漏和后台包装要求",
            "纺织": "核对袋装、防尘及必要警告",
            "电池/待确认危险品": "发货前先确认危险品分类、资料及可运规则",
        }
        return [
            section(
                "FBA 入库准备清单",
                ("货件", v["product"] + " · " + v.get("plan", "待建计划")),
                (
                    "装箱建议",
                    f"{int(n('boxes')) - extra} 箱 × {each} 件；另 {extra} 箱 × {each + 1} 件；"
                    "称重、量箱后与后台限制核对。",
                ),
                ("商品标签", v["label_type"] + "；检查标签可扫描、必要时遮盖冲突条码。"),
                ("包装属性", handling[v["handling"]]),
                (
                    "发运前",
                    "核对分仓、箱标、托盘和承运人标签，"
                    "保存装箱清单及跟踪号；所有尺寸按入仓计划与后台要求。",
                ),
            )
        ]
    raise ValueError("暂不支持该技能。")


def run_skill(req: SkillId, body: SkillRequest) -> SkillReport:
    definition = SKILLS[req]
    values = {key: value.strip() for key, value in body.values.items() if value.strip()}
    known = {field.key for field in definition.fields}
    # REQ-059 复用 FBA 技能的标量，成本和数量作为订单利润附加输入。
    allowed_shared = {"REQ-059": {"cost", "quantity"}, "REQ-095": set()}.get(req, set())
    if set(body.values) - known - allowed_shared or (
        body.fba is not None
        and req != "REQ-059"
        or (body.landed is not None or body.hs is not None)
        and req != "REQ-095"
    ):
        raise ValueError("包含此技能不接受的字段。")
    # 每个领域处理器解析自己的多列行格式；目录中的标量校验在这里统一执行。
    nums = {}
    for field in definition.fields:
        value = values.get(field.key)
        if not value:
            if field.required:
                raise ValueError("缺少：" + field.label)
            continue
        if len(value) > (5000 if field.kind == "lines" else 400) or len(value.splitlines()) > 40:
            raise ValueError("内容超出输入上限：" + field.label)
        if field.kind == "choice" and value not in field.choices:
            raise ValueError("无效选项：" + field.label)
        if field.kind == "number":
            counts = {"quantity", "inventory", "boxes"}
            nums[field.key] = number(
                value,
                percentage=field.key in {"discount", "stack", "acos"},
                count=field.key in counts,
            )
            if field.key in {"boxes", "quantity", "price"} and nums[field.key] <= 0:
                raise ValueError("必须大于零：" + field.label)
    # REQ-059 的利润专属输入复用相同非负数和整件校验，随后交给 REQ-042 计算费用。
    if req == "REQ-059":
        nums["cost"] = number(values.get("cost", ""))
        nums["quantity"] = number(values.get("quantity", ""), count=True)
        if nums["quantity"] <= 0:
            raise ValueError("订单件数必须大于 0。")
    fba = None
    if req == "REQ-059":
        if body.fba is None:
            raise ValueError("请填写 REQ-042 的 FBA 计费参数。")
        # REQ-059 只扩展利润，不复制 REQ-042 的费用档位、取整规则或费率数据。
        fba = calculate_fba(body.fba)
        margin = (
            None if fba.total_usd is None else body.fba.price_usd - fba.total_usd - nums["cost"]
        )
        details = [
            section(
                "订单利润估算",
                ("尺寸档／计费磅", f"{fba.size_tier} / {fba.shipping_weight_lb}"),
                ("单件 FBA 费用 USD", "待确认" if fba.total_usd is None else money(fba.total_usd)),
                ("单件贡献 USD", "待确认" if margin is None else money(margin)),
                ("订单贡献 USD", "待确认" if margin is None else money(margin * nums["quantity"])),
            )
        ]
    elif req == "REQ-095":
        if body.landed is None and body.hs is None:
            raise ValueError("请填写 HS 查询或已确认的到岸成本参数。")
        hs_query = body.hs.query if body.hs is not None else "未查询"
        details = [
            section(
                "归类与税费核对",
                ("HS 候选查询", hs_query),
                ("执行顺序", "先核对归类，再用已确认的税号、税率和费用计算。"),
                ("缺项", "未确认费率及参数保留为待确认，不从候选税号推定关税。"),
            )
        ]
    else:
        handler = ADVANCED_HANDLERS.get(req)
        details = handler(values) if handler else _dispatch(req, values, nums)
    return SkillReport(
        req=req,
        title=definition.title,
        created_at=datetime.now(UTC),
        sections=details,
        confirmed_inputs=values,
        limitations=[definition.note, "美国站；用户输入不等于平台验证。"],
        fba=fba,
        landed=calculate_landed(body.landed) if body.landed is not None else None,
    )


async def generate_skill_report(req: SkillId, body: SkillRequest) -> SkillReport:
    report = run_skill(req, body)
    # 查询只提供候选，绝不把候选自动写入计税请求；两项都使用原脚本结果和来源。
    if req == "REQ-095" and body.hs is not None:
        report.hs = await lookup_hs(body.hs)
    return report
