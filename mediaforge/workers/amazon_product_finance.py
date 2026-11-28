"""Transparent unit-profit and BSR-calibrated finance estimates for an Amazon product."""

from __future__ import annotations

import re
from decimal import ROUND_HALF_UP, Decimal

from mediaforge.models.amazon_product import (
    ProductCosts,
    ProductFinance,
    ProductPage,
    ProductScenario,
)
from mediaforge.models.amazon_research import AmazonProduct

D = Decimal
_CURRENCY_QUANTUM = D("0.01")
_SCENARIO_FACTORS = (("low", D("0.6")), ("mid", D("1")), ("high", D("1.4")))
_RANK_RE = re.compile(r"#\s*([0-9][0-9,]*)\s+in\s+([^#(\n]+)", re.IGNORECASE)


def _decimal(value: float | None) -> Decimal | None:
    return D(str(value)) if value is not None else None


def _money(value: Decimal | None) -> float | None:
    if value is None:
        return None
    return float(value.quantize(_CURRENCY_QUANTUM, rounding=ROUND_HALF_UP))


def _text(value: str | None) -> str | None:
    return (value.strip() or None) if value is not None else None


def _category_key(value: str) -> str:
    return " ".join(value.casefold().replace("’", "'").split())


def _category_rank(
    product: AmazonProduct | None, benchmark_category: str | None
) -> tuple[int | None, str | None]:
    """Select an observed BSR label, preferring the exact benchmark category."""

    if product is None:
        return None, None
    leaf = _text(product.category)
    if leaf is None and product.category_path:
        leaf = _text(product.category_path[-1])

    rows = [row for row in product.sales_ranks if row.strip()]
    if not rows and product.rank:
        rows = [product.rank]
    ranks: dict[str, tuple[int | None, str]] = {}
    for row in rows:
        for match in _RANK_RE.finditer(row):
            try:
                rank = int(match[1].replace(",", ""))
            except ValueError:
                continue
            label = match[2].strip()
            if not label or rank < 1:
                continue
            key = _category_key(label)
            if key not in ranks:
                ranks[key] = (rank, label)
            elif ranks[key][0] != rank:
                ranks[key] = (None, ranks[key][1])

    for preferred in (benchmark_category, leaf):
        if preferred and (key := _category_key(preferred)) in ranks:
            return ranks[key]
    return next(iter(ranks.values()), (None, leaf))


def _benchmark_monthly_units(
    costs: ProductCosts,
    rank: int | None,
    category: str | None,
    missing: list[str],
    assumptions: list[str],
) -> int | None:
    """Use only a dated, exact category/BSR observation; never interpolate or extrapolate."""

    benchmark_bsr = costs.benchmark_bsr
    benchmark_units = costs.benchmark_monthly_units
    benchmark_category = _text(costs.benchmark_category)
    benchmark_source = _text(costs.benchmark_source)
    benchmark_date = costs.benchmark_date
    benchmark_fields = {
        "benchmark_bsr": benchmark_bsr,
        "benchmark_monthly_units": benchmark_units,
        "benchmark_category": benchmark_category,
        "benchmark_source": benchmark_source,
        "benchmark_date": benchmark_date,
    }
    if all(value is None for value in benchmark_fields.values()):
        missing.append("monthly_units")
        assumptions.append("没有可核验的同类目、同 BSR 月销量标定；销量与情景保持未知。")
        return None

    if (
        benchmark_bsr is None
        or benchmark_units is None
        or benchmark_category is None
        or benchmark_source is None
        or benchmark_date is None
    ):
        missing.extend(name for name, value in benchmark_fields.items() if value is None)
        missing.append("monthly_units")
        assumptions.append("销量标定信息不完整；缺少日期、来源、类目、BSR 或月销量，未用于情景。")
        return None

    if rank is None or category is None:
        missing.append("monthly_units")
        assumptions.append("商品缺少可比较的类目 BSR；不跨类目或未知排名推算销量。")
        return None
    if _category_key(category) != _category_key(benchmark_category):
        missing.append("monthly_units")
        assumptions.append("商品 BSR 类目与销量标定类目不一致；不跨类目换算。")
        return None
    if rank != benchmark_bsr:
        missing.append("monthly_units")
        assumptions.append("销量标定 BSR 与商品 BSR 不同；单点标定不做区间外推或插值。")
        return None

    assumptions.append(
        "销量标定仅采用同类目、同 BSR 的用户／授权观测："
        f"{benchmark_bsr} BSR = {benchmark_units} 件/月；"
        f"来源 {benchmark_source}，日期 {benchmark_date.isoformat()}。"
    )
    return benchmark_units


def _scenario_values(
    monthly_units: int,
    price: Decimal | None,
    unit_profit: Decimal | None,
) -> list[ProductScenario]:
    scenarios = []
    for name, factor in _SCENARIO_FACTORS:
        units = int(D(monthly_units) * factor)
        annual_revenue = _money(D(units * 12) * price) if price is not None else None
        annual_profit = _money(D(units * 12) * unit_profit) if unit_profit is not None else None
        scenarios.append(
            ProductScenario(
                name=name,
                monthly_units=units,
                annual_revenue=annual_revenue,
                annual_profit=annual_profit,
            )
        )
    return scenarios


def analyze_product_finance(
    page: ProductPage | None,
    costs: ProductCosts | None,
) -> ProductFinance:
    """Combine USD costs only with USD pricing. Percentage inputs are fractions of price."""

    assumptions: list[str] = []
    missing: list[str] = []
    if costs is None:
        missing.append("costs")
        costs = ProductCosts()

    product = page.product if page is not None else None
    rank, category = _category_rank(product, _text(costs.benchmark_category))
    if category is None:
        missing.append("category")
    if rank is None:
        missing.append("category_rank")
    assumptions.append("BSR 标定仅用商品实际显示的同名排名类目，不以面包屑类目替代或跨类目换算。")
    assumptions.append("未观察到季节性；低／中／高仅为固定敏感性系数，不是季节预测。")

    override_price = _decimal(costs.sale_price)
    page_price = _decimal(page.price_amount) if page is not None else None
    page_currency = _text(page.currency) if page is not None else None
    currency = page_currency.upper() if page_currency is not None else None
    if override_price is not None:
        currency = "USD"
        price = override_price
        assumptions.append("售价采用用户覆盖值（USD）；未用页面售价和币种。")
    else:
        price = page_price if currency is not None else None
        if currency is None:
            missing.append("currency")
            if page_price is not None:
                assumptions.append("商品页售价币种未知；该金额未用于收入或利润计算。")
        elif page_price is not None:
            assumptions.append("售价采用商品页采集值。")
            if currency != "USD":
                missing.append("usd_price")
                assumptions.append(
                    f"商品页售价为 {currency}，只用于同币种收入；单件固定成本为 USD，"
                    "不混算费用、利润或 ROI。"
                )
    if price is None:
        missing.append("price")
    usd_price = price if currency == "USD" else None

    purchase = _decimal(costs.purchase)
    inbound = _decimal(costs.inbound)
    fulfillment = _decimal(costs.fulfillment)
    storage = _decimal(costs.storage)
    ad_pct = _decimal(costs.ad_pct)
    return_pct = _decimal(costs.return_pct)
    referral_pct = _decimal(costs.referral_pct)
    fee_source = _text(costs.fee_source)
    fee_date = costs.fee_date
    if fee_source and fee_date:
        assumptions.append(
            f"费用覆盖按来源 {fee_source} 和日期 {fee_date.isoformat()} 使用；请按适用期复核。"
        )
    elif any(
        value is not None for value in (fulfillment, storage, ad_pct, return_pct, referral_pct)
    ):
        assumptions.append("费用覆盖缺少完整来源或日期；不是内置已核验费率。")
    if referral_pct is None:
        assumptions.append(
            "零售浏览或 BSR 类目不能确认计费类目；未自动套用 Amazon 美国站佣金表，佣金待人工确认。"
        )

    missing.extend(
        name
        for name, value in (
            ("purchase", purchase),
            ("inbound", inbound),
            ("fulfillment", fulfillment),
            ("storage", storage),
            ("ad_pct", ad_pct),
            ("return_pct", return_pct),
            ("referral_pct", referral_pct),
        )
        if value is None
    )
    ad_amount = usd_price * ad_pct if usd_price is not None and ad_pct is not None else None
    return_amount = (
        usd_price * return_pct if usd_price is not None and return_pct is not None else None
    )
    referral_amount = (
        usd_price * referral_pct if usd_price is not None and referral_pct is not None else None
    )
    unit_costs = {
        "purchase": _money(purchase),
        "inbound": _money(inbound),
        "fulfillment": _money(fulfillment),
        "storage": _money(storage),
        "ad": _money(ad_amount),
        "return": _money(return_amount),
        "referral": _money(referral_amount),
    }
    values = (purchase, inbound, fulfillment, storage, ad_amount, return_amount, referral_amount)
    known_costs = [value for value in values if value is not None]
    raw_profit = (
        usd_price - sum(known_costs, D(0))
        if usd_price is not None and len(known_costs) == len(values)
        else None
    )
    investment = purchase + inbound if purchase is not None and inbound is not None else None
    roi_pct = (
        _money(raw_profit / investment * 100)
        if raw_profit is not None and investment is not None and investment > 0
        else None
    )
    if raw_profit is None:
        assumptions.append(
            "缺少完整可比的 USD 售价或费用输入；单件利润和 ROI 保持未知，不以 0 补齐。"
        )
    elif roi_pct is None:
        assumptions.append("采购加头程成本为零或不完整；ROI 保持未知。")

    monthly_units = _benchmark_monthly_units(costs, rank, category, missing, assumptions)
    scenarios = (
        _scenario_values(monthly_units, price, raw_profit) if monthly_units is not None else []
    )
    return ProductFinance(
        currency=currency,
        price=_money(price),
        category=category,
        rank=rank,
        assumptions=assumptions,
        missing=missing,
        scenarios=scenarios,
        unit_costs=unit_costs,
        unit_profit=_money(raw_profit),
        roi_pct=roi_pct,
        fee_source=fee_source,
        fee_date=fee_date,
    )
