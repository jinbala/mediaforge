from datetime import UTC, date, datetime

import pytest

from mediaforge.models.amazon_product import ProductCosts, ProductPage
from mediaforge.models.amazon_research import AmazonProduct
from mediaforge.workers.amazon_product_finance import analyze_product_finance

COLLECTED = datetime(2026, 11, 28, 12, 0, tzinfo=UTC)
FEE_DATE = date(2026, 10, 15)


def page(
    *,
    category: str | None = "Trousers",
    ranks: list[str] | None = None,
    price: float | None = 20.0,
    currency: str | None = "USD",
) -> ProductPage:
    product = AmazonProduct(
        asin="B012345678",
        title="Sample trousers",
        url="https://www.amazon.com/dp/B012345678",
        category=category,
        sales_ranks=ranks if ranks is not None else ["#100 in Trousers"],
        image_brief="",
    )
    return ProductPage(
        product=product,
        currency=currency,
        price_amount=price,
        collected_at=COLLECTED,
    )


def complete_costs(**overrides) -> ProductCosts:
    values = {
        "purchase": 4.0,
        "inbound": 1.0,
        "fulfillment": 3.0,
        "storage": 1.0,
        "ad_pct": 0.10,
        "return_pct": 0.05,
        "referral_pct": 0.15,
        "sale_price": None,
        "fee_date": FEE_DATE,
        "fee_source": "authorized seller fee export",
        "benchmark_bsr": 100,
        "benchmark_monthly_units": 100,
        "benchmark_category": "Trousers",
        "benchmark_source": "authorized comparable listing report",
        "benchmark_date": date(2026, 11, 1),
    }
    values.update(overrides)
    return ProductCosts(**values)


def test_missing_inputs_do_not_become_zero_or_fake_scenarios():
    result = analyze_product_finance(None, None)

    assert result.currency is None
    assert result.price is None
    assert result.category is None
    assert result.rank is None
    assert result.scenarios == []
    assert result.unit_costs == {
        "purchase": None,
        "inbound": None,
        "fulfillment": None,
        "storage": None,
        "ad": None,
        "return": None,
        "referral": None,
    }
    assert result.unit_profit is None and result.roi_pct is None
    assert {"price", "category", "category_rank", "costs", "monthly_units"} <= set(result.missing)


def test_exact_same_category_bsr_calibration_is_reproducible_and_uses_usd_sale_override():
    result = analyze_product_finance(
        page(price=20.0, currency="EUR"), complete_costs(sale_price=22.0)
    )

    assert (result.currency, result.price, result.category, result.rank) == (
        "USD",
        22.0,
        "Trousers",
        100,
    )
    assert result.unit_costs == {
        "purchase": 4.0,
        "inbound": 1.0,
        "fulfillment": 3.0,
        "storage": 1.0,
        "ad": 2.2,
        "return": 1.1,
        "referral": 3.3,
    }
    assert result.unit_profit == pytest.approx(6.4)
    assert result.roi_pct == pytest.approx(128.0)
    assert [scenario.name for scenario in result.scenarios] == ["low", "mid", "high"]
    assert [scenario.monthly_units for scenario in result.scenarios] == [60, 100, 140]
    assert [scenario.annual_revenue for scenario in result.scenarios] == [15840.0, 26400.0, 36960.0]
    assert [scenario.annual_profit for scenario in result.scenarios] == [4608.0, 7680.0, 10752.0]
    assert result.fee_source == "authorized seller fee export"
    assert result.fee_date == FEE_DATE
    assert any("authorized comparable listing report" in item for item in result.assumptions)
    assert any("日期 2026-11-01" in item for item in result.assumptions)
    assert any("季节" in item for item in result.assumptions)
    assert "monthly_units" not in result.missing


def test_usd_sale_override_does_not_require_a_product_page():
    result = analyze_product_finance(None, complete_costs(sale_price=20.0))

    assert (result.currency, result.price) == ("USD", 20.0)
    assert (result.unit_profit, result.roi_pct) == (5.0, 100.0)
    assert "currency" not in result.missing
    assert result.scenarios == []


def test_page_currency_is_normalized_before_usd_calculation():
    result = analyze_product_finance(page(currency=" usd "), complete_costs())

    assert result.currency == "USD"
    assert result.unit_costs["referral"] == 3.0
    assert result.unit_profit == 5.0
    assert result.scenarios[1].annual_profit == 6000.0


def test_non_usd_page_price_keeps_revenue_but_does_not_mix_usd_costs():
    result = analyze_product_finance(page(currency=" eur "), complete_costs())

    assert (result.currency, result.price) == ("EUR", 20.0)
    assert result.unit_costs == {
        "purchase": 4.0,
        "inbound": 1.0,
        "fulfillment": 3.0,
        "storage": 1.0,
        "ad": None,
        "return": None,
        "referral": None,
    }
    assert result.unit_profit is None and result.roi_pct is None
    assert [scenario.annual_revenue for scenario in result.scenarios] == [14400.0, 24000.0, 33600.0]
    assert all(scenario.annual_profit is None for scenario in result.scenarios)
    assert "currency" not in result.missing
    assert "usd_price" in result.missing
    assert any("EUR" in item and "USD" in item for item in result.assumptions)


@pytest.mark.parametrize("currency", [None, "  "])
def test_missing_currency_leaves_revenue_and_profit_unknown(currency):
    result = analyze_product_finance(page(currency=currency), complete_costs())

    assert result.currency is None and result.price is None
    assert result.unit_profit is None and result.roi_pct is None
    assert result.unit_costs["referral"] is None
    assert {"price", "currency"} <= set(result.missing)
    assert [scenario.monthly_units for scenario in result.scenarios] == [60, 100, 140]
    assert all(s.annual_revenue is None and s.annual_profit is None for s in result.scenarios)
    assert any("币种未知" in item for item in result.assumptions)


def test_category_rank_selection_ignores_parent_rank():
    result = analyze_product_finance(
        page(ranks=["#7 in Clothing", "#100 in Trousers"]),
        complete_costs(),
    )

    assert result.rank == 100
    assert [scenario.monthly_units for scenario in result.scenarios] == [60, 100, 140]


def test_benchmark_can_match_observed_rank_label_instead_of_breadcrumb_leaf():
    result = analyze_product_finance(
        page(category="Trousers", ranks=["#7 in Clothing", "#100 in Trousers"]),
        complete_costs(benchmark_category=" clothing ", benchmark_bsr=7),
    )

    assert (result.category, result.rank) == ("Clothing", 7)
    assert [scenario.monthly_units for scenario in result.scenarios] == [60, 100, 140]


def test_conflicting_ranks_in_same_category_cannot_calibrate():
    result = analyze_product_finance(
        page(ranks=["#100 in Trousers", "#101 in trousers"]), complete_costs()
    )

    assert (result.category, result.rank) == ("Trousers", None)
    assert {"category_rank", "monthly_units"} <= set(result.missing)
    assert result.scenarios == []


def test_breadcrumb_label_alone_does_not_prove_a_rank():
    result = analyze_product_finance(page(ranks=[]), complete_costs())

    assert (result.category, result.rank) == ("Trousers", None)
    assert "monthly_units" in result.missing
    assert result.scenarios == []


@pytest.mark.parametrize(
    ("ranks", "benchmark_category", "phrase"),
    [
        (["#99 in Trousers"], "Trousers", "BSR 与商品 BSR 不同"),
        (["#100 in Trousers"], "Clothing", "类目不一致"),
        (["#100 in Clothing"], "Trousers", "类目不一致"),
    ],
)
def test_different_rank_or_category_never_interpolates_or_crosses_categories(
    ranks, benchmark_category, phrase
):
    result = analyze_product_finance(
        page(ranks=ranks),
        complete_costs(benchmark_category=benchmark_category),
    )

    assert result.scenarios == []
    assert result.unit_profit is not None
    assert "monthly_units" in result.missing
    assert any(phrase in item for item in result.assumptions)


def test_undated_or_unsourced_benchmark_stays_unknown():
    result = analyze_product_finance(
        page(),
        complete_costs(benchmark_source=None, benchmark_date=None),
    )

    assert result.scenarios == []
    assert "benchmark_source" in result.missing
    assert "benchmark_date" in result.missing
    assert "monthly_units" in result.missing
    assert any("信息不完整" in item for item in result.assumptions)


def test_retail_category_does_not_infer_referral_fee_with_dated_fee_inputs():
    result = analyze_product_finance(
        page(category="Home & Kitchen", ranks=["#100 in Home & Kitchen"]),
        complete_costs(referral_pct=None, benchmark_category="Home & Kitchen"),
    )

    assert result.unit_costs["referral"] is None
    assert "referral_pct" in result.missing
    assert result.unit_profit is None and result.roi_pct is None
    assert result.scenarios[1].annual_revenue == 24000.0
    assert result.scenarios[1].annual_profit is None
    assert any("计费类目" in item for item in result.assumptions)


def test_partial_costs_keep_known_breakdown_but_profit_unknown():
    result = analyze_product_finance(
        page(),
        ProductCosts(
            purchase=4.0,
            sale_price=20.0,
            fee_date=FEE_DATE,
            fee_source="seller override",
        ),
    )

    assert result.unit_costs["purchase"] == 4.0
    assert result.unit_costs["inbound"] is None
    assert result.unit_costs["ad"] is None
    assert result.unit_profit is None and result.roi_pct is None
    assert {"inbound", "fulfillment", "storage", "ad_pct", "return_pct", "referral_pct"} <= set(
        result.missing
    )
    assert result.fee_source == "seller override" and result.fee_date == FEE_DATE
    assert result.scenarios == []
