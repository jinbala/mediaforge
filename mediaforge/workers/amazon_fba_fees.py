from calendar import monthrange
from datetime import UTC, date, datetime, timedelta
from decimal import ROUND_HALF_UP, Decimal
from uuid import uuid4

from mediaforge.models.amazon_script_tools import CostLine, FbaInput, FbaResult, RateSource

D = Decimal
PRICING = "https://sell.amazon.com/pricing"
HUB = "https://sellercentral.amazon.com/help/hub/reference/external/"


def _source(version: str, start: date, end: date, link: str) -> RateSource:
    return RateSource(version=version, effective_from=start, effective_to=end, source_url=link)


RULE_START, RULE_END = date(2026, 1, 15), date(2027, 1, 14)
PRICING_US = _source("amazon-us-referral-2026", RULE_START, RULE_END, PRICING)
STORAGE_US = _source("amazon-us-base-storage-2026", RULE_START, RULE_END, HUB + "G3EDYEF6KUCFQTNM")
SIZE_US = HUB + "G201105770"
# 明确记录内置费率覆盖的重量与时间；其他档位不套用相邻行，可填卖家后台费率。
FULFILLMENT = (
    (
        _source(
            "amazon-us-non-peak-2026", RULE_START, date(2026, 10, 14), HUB + "GABBX6GZPA8MSZGW"
        ),
        (("2.43", "3.32", "3.58"), ("2.49", "3.42", "3.68"), ("2.56", "3.45", "3.71")),
    ),
    (
        _source(
            "amazon-us-holiday-peak-2026", date(2026, 10, 15), RULE_END, HUB + "GABBX6GZPA8MSZGW"
        ),
        (("2.62", "3.51", "3.77"), ("2.68", "3.61", "3.87"), ("2.76", "3.65", "3.91")),
    ),
)
FUEL_US = _source("amazon-us-fuel-2026-04-17", date(2026, 4, 17), RULE_END, HUB + "G200209150")


def currency_round(value: Decimal) -> Decimal:
    return value.quantize(D("0.01"), rounding=ROUND_HALF_UP)


def covers(source: RateSource, day: date) -> bool:
    return source.effective_from <= day <= source.effective_to


def _tier(dims: tuple[Decimal, Decimal, Decimal], weight: Decimal) -> tuple[str, Decimal, Decimal]:
    length, width, height = dims
    dim_weight = length * width * height / 139
    if length <= 15 and width <= 12 and height <= D("0.75") and weight <= 1:
        return "small_standard", dim_weight, weight
    shipping = max(weight, dim_weight)
    if length <= 18 and width <= 14 and height <= 8 and shipping <= 20:
        return "large_standard", dim_weight, shipping
    girth = length + 2 * (width + height)
    if length <= 59 and width <= 33 and height <= 33 and girth <= 130 and shipping <= 50:
        return "large_bulky", dim_weight, shipping
    return "extra_large", dim_weight, shipping


def _storage(body: FbaInput, tier: str, volume: Decimal) -> tuple[Decimal | None, str]:
    if not body.storage_days:
        return D(0), "库存天数为 0"
    if body.dangerous_goods:
        return None, "危险品基础仓储费率需另行核对"
    current = body.storage_start
    end = current + timedelta(days=body.storage_days)
    if not covers(STORAGE_US, current) or not covers(STORAGE_US, end - timedelta(days=1)):
        return None, "库存期间超出内置费率有效期"
    standard = tier in {"small_standard", "large_standard"}
    lines = []
    total = D(0)
    while current < end:
        days_in_month = monthrange(current.year, current.month)[1]
        days = min((end - current).days, days_in_month - current.day + 1)
        peak = current.month >= 10
        rate = D(("2.40" if peak else "0.78") if standard else ("1.40" if peak else "0.56"))
        total += volume * rate * days / days_in_month
        lines.append(f"{current:%Y-%m}: {rate} USD/ft³ × {days}/{days_in_month}")
        current += timedelta(days=days)
    return total, "体积(ft³) × [" + " + ".join(lines) + "]"


def calculate_fba(body: FbaInput) -> FbaResult:
    dims = tuple(sorted(body.dimensions_in, reverse=True))
    length, width, height = dims
    tier, dimensional, shipping = _tier((length, width, height), body.weight_lb)
    volume = length * width * height / 1728
    lines: list[CostLine] = []
    pending: list[str] = []

    def add(name, amount, formula, source=None, kind="official"):
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

    dated = covers(PRICING_US, body.ship_date)
    price = body.price_usd
    rate = D("0.15")
    if body.category == "consumer_electronics":
        rate = D("0.08")
    elif body.category == "clothing":
        rate = D("0.05" if price <= 15 else "0.10" if price <= 20 else "0.17")
    minimum = D(0) if body.category == "media" else D("0.30")
    add(
        "销售佣金",
        max(price * rate, minimum) if dated else None,
        f"max(售价 × {rate}, {minimum} USD)" if dated else "配送日超出内置费率版本",
        PRICING_US,
    )

    card = next(
        ((source, rows) for source, rows in FULFILLMENT if covers(source, body.ship_date)), None
    )
    oz = shipping * 16
    manual = body.fulfillment_override
    if manual:
        valid = covers(manual.source, body.ship_date)
        add(
            "配送费",
            manual.amount if valid else None,
            "人工确认的含适用附加费单件配送报价" if valid else "人工报价不覆盖配送日",
            manual.source,
            "manual",
        )
    elif (
        card
        and tier == "small_standard"
        and oz <= 6
        and not body.dangerous_goods
        and body.category != "clothing"
    ):
        version, rows = card
        band = 0 if price < 10 else 2 if price > 50 else 1
        index = 0 if oz <= 2 else 1 if oz <= 4 else 2
        fee = D(rows[index][band])
        add(
            "配送费", fee, f"小号标准：≤ {(index + 1) * 2} oz 档，售价位于第 {band + 1} 档", version
        )
        if covers(FUEL_US, body.ship_date):
            add("燃油与物流附加费", fee * D("0.035"), f"{fee} USD × 3.5%", FUEL_US)
    else:
        add("配送费", None, "缺少适用的尺寸/重量/品类/配送日费率，请填写 Seller Central 单件报价")

    # 仓储按月拆分使用实际日数，不能把跨旺季的库存乘同一个月费率。
    if body.storage_days and body.storage_start > date.max - timedelta(days=body.storage_days):
        storage, formula = None, "库存期间超出有效日期范围"
    else:
        storage, formula = _storage(body, tier, volume)
    add(
        "基础仓储费",
        storage,
        formula,
        STORAGE_US if body.storage_days else None,
        "official" if body.storage_days else "calculated",
    )
    if body.selling_plan == "individual":
        add(
            "个人销售计划费",
            D("0.99") if dated else None,
            "每件销售 0.99 USD" if dated else "配送日超出内置费率版本",
            PRICING_US,
        )
    if body.category == "media":
        add(
            "媒体成交服务费",
            D("1.80") if dated else None,
            "每件媒体商品 1.80 USD" if dated else "配送日超出内置费率版本",
            PRICING_US,
        )
    for fee in body.service_fees:
        valid = covers(fee.source, body.ship_date)
        add(
            fee.name,
            fee.amount if valid else None,
            "人工确认的单件适用费用" if valid else "报价已超出有效期",
            fee.source,
            "manual",
        )
    if body.storage_days > 181 and not any(
        "库龄" in fee.name and covers(fee.source, body.ship_date) for fee in body.service_fees
    ):
        add("超龄库存附加费", None, "库存超过 181 天，请确认适用库龄费用并按件填写")
    subtotal = sum((line.amount_usd for line in lines if line.amount_usd is not None), D("0.00"))
    return FbaResult(
        report_id=uuid4(),
        collected_at=datetime.now(UTC),
        status="incomplete" if pending else "success",
        inputs=body,
        size_tier=tier,
        sorted_dimensions_in=(length, width, height),
        shipping_weight_lb=shipping,
        dimensional_weight_lb=dimensional,
        volume_ft3=volume,
        known_subtotal_usd=subtotal,
        total_usd=None if pending else subtotal,
        lines=lines,
        pending=pending,
        warnings=[
            f"尺寸分级依据：{SIZE_US}；单件估算，以卖家后台测量和最终账单为准。",
            "专业计划订阅按账号计，不分摊到单件；入库配置、退货、移除及仓储利用率等按实际适用项录入。",
        ],
    )
