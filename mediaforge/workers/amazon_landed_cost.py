import re
from datetime import UTC, datetime
from decimal import Decimal
from uuid import uuid4

from mediaforge.models.amazon_script_tools import CostLine, LandedInput, LandedResult
from mediaforge.rag.amazon_research import AmazonSourceError
from mediaforge.rag.amazon_script_samples import CBP_RULINGS, HTS, normalize_hs_code
from mediaforge.workers.amazon_fba_fees import covers, currency_round

CBP_VALUATION = "https://www.cbp.gov/trade/valuation"
USTR_301 = "https://ustr.gov/issue-areas/enforcement/section-301-investigations/search"


def calculate_landed(body: LandedInput) -> LandedResult:
    code = normalize_hs_code(body.hs_code)
    if not re.fullmatch(r"[0-9]{8}(?:[0-9]{2})?", code):
        raise AmazonSourceError("到岸成本须输入已确认的 8 位或 10 位美国 HTSUS 编码。")
    if body.origin == body.destination:
        raise AmazonSourceError("进口原产地不能与目的地相同。")
    pending: list[str] = []
    lines: list[CostLine] = []

    def add(name, amount, formula, source=None, kind="manual"):
        lines.append(
            CostLine(
                name=name,
                amount_usd=currency_round(amount) if amount is not None else None,
                formula=formula,
                source=source,
                source_type=kind if amount is not None else "unknown",
            )
        )
        if amount is None:
            pending.append(f"{name}：{formula}")

    fx = Decimal(1) if body.currency == "USD" else body.usd_per_currency
    fx_source = None if body.currency == "USD" else body.exchange_source
    if body.currency != "USD" and (not fx_source or not covers(fx_source, body.as_of)):
        fx = None
    for name, value in (
        ("货值", body.goods_value),
        ("国际运费", body.freight),
        ("保险", body.insurance),
    ):
        amount = value * fx if value is not None and fx is not None else None
        formula = (
            "未填写；如确认不适用可填 0"
            if value is None
            else "缺少覆盖进口日的汇率、来源或版本"
            if fx is None
            else f"{value} {body.currency} × {fx} USD/{body.currency}"
        )
        add(name, amount, formula, fx_source)

    # 美国交易价值通常不含可分列的国际运保费，不能用到岸货值直接代替海关计税价格。
    customs_value = body.customs_value_usd
    if customs_value is None:
        pending.append("请确认美国海关计税价格；不自动将国际运费和保险计入税基。")
    if not body.duties_confirmed or not body.duties:
        pending.append(
            "请确认一般关税及适用的 Chapter 99／301、配额、贸易救济税和口岸税费。"
            "若确认免税，录入有依据、金额为 0 的税费条目。"
        )
    for duty in body.duties:
        if not covers(duty.source, body.as_of):
            add(duty.name, None, "费率版本未覆盖预计进口日", duty.source)
        elif duty.basis == "customs_value":
            add(
                duty.name,
                customs_value * duty.rate if customs_value is not None else None,
                f"海关计税价格 × {duty.rate}"
                if customs_value is not None
                else "缺少已确认计税价格",
                duty.source,
            )
        elif duty.basis == "per_unit":
            add(
                duty.name,
                duty.amount * body.quantity,
                f"{duty.amount} USD/件 × {body.quantity} 件",
                duty.source,
            )
        else:
            add(duty.name, duty.amount, "票级固定税费（USD）", duty.source)
    for name, value in (("报关与口岸费用", body.brokerage_usd), ("境内运输", body.inland_usd)):
        add(
            name, value, "人工确认的美元费用" if value is not None else "未填写；如确认不适用可填 0"
        )
    subtotal = sum(
        (line.amount_usd for line in lines if line.amount_usd is not None), Decimal("0.00")
    )
    dotted = ".".join(filter(None, (code[:4], code[4:6], code[6:8], code[8:])))
    return LandedResult(
        report_id=uuid4(),
        collected_at=datetime.now(UTC),
        status="incomplete" if pending else "success",
        inputs=body,
        hs_code=code,
        customs_value_usd=customs_value,
        known_subtotal_usd=subtotal,
        total_usd=None if pending else subtotal,
        per_unit_usd=None if pending else currency_round(subtotal / body.quantity),
        lines=lines,
        pending=pending,
        review_urls=[f"{HTS}/?query={dotted}", CBP_VALUATION, USTR_301, CBP_RULINGS],
        warnings=[
            "仅用于美国进口测算；不从 HS 章号推定税率，不自动给予贸易协定减免。"
            "美国无联邦进口增值税，本表不计州销售税；防止同一项在税费和口岸费用重复录入。"
        ],
    )
