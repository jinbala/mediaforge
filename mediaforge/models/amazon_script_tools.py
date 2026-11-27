from datetime import date, datetime
from decimal import Decimal
from typing import Annotated, Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, HttpUrl, StringConstraints, model_validator

Site = Literal["US"]
Text = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=240)]
Source = Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=2000)]
Money = Annotated[Decimal, Field(ge=0, le=100_000_000, decimal_places=2)]
Measure = Annotated[Decimal, Field(gt=0, le=1000, decimal_places=4)]
Rate = Annotated[Decimal, Field(ge=0, le=10, decimal_places=6)]
ToolStatus = Literal["success", "empty", "partial_fail", "failed", "incomplete"]
FbaCategory = Literal["home_kitchen", "toys", "sports", "consumer_electronics", "clothing", "media"]


class ToolInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    site: Site = "US"


class KeywordInput(ToolInput):
    tool: Literal["keywords"]
    keyword: Annotated[str, StringConstraints(strip_whitespace=True, min_length=1, max_length=100)]
    queries: list[Text] | None = Field(default=None, min_length=1, max_length=30)


class ListingInput(ToolInput):
    tool: Literal["listing"]
    source: Source


class PpcInput(ToolInput):
    tool: Literal["ppc"]
    sources: list[Source] = Field(min_length=1, max_length=6)


class RateSource(BaseModel):
    model_config = ConfigDict(extra="forbid")
    version: Text
    effective_from: date
    effective_to: date
    source_url: HttpUrl

    @model_validator(mode="after")
    def validate_period(self):
        if self.effective_to < self.effective_from:
            raise ValueError("费率有效期结束日期不得早于开始日期。")
        if (
            self.source_url.scheme != "https"
            or self.source_url.username
            or self.source_url.password
        ):
            raise ValueError("费率来源须为不含账号凭证的 HTTPS 链接。")
        return self


class ServiceCharge(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Text
    amount: Money
    source: RateSource


class FbaInput(ToolInput):
    tool: Literal["fba"]
    category: FbaCategory
    dimensions_in: tuple[Measure, Measure, Measure]
    weight_lb: Measure
    price_usd: Annotated[Money, Field(gt=0)]
    ship_date: date
    storage_start: date
    storage_days: int = Field(ge=0, le=730)
    selling_plan: Literal["individual", "professional"]
    dangerous_goods: bool = False
    fulfillment_override: ServiceCharge | None = None
    service_fees: list[ServiceCharge] = Field(default_factory=list, max_length=10)


class DutyInput(BaseModel):
    model_config = ConfigDict(extra="forbid")
    name: Text
    basis: Literal["customs_value", "per_unit", "fixed"]
    rate: Rate | None = None
    amount: Money | None = None
    source: RateSource

    @model_validator(mode="after")
    def validate_amount(self):
        if self.basis == "customs_value":
            valid = self.rate is not None and self.amount is None
        else:
            valid = self.amount is not None and self.rate is None
        if not valid:
            raise ValueError("从价税填写费率；按件或定额税费填写金额，两者不得同时填写。")
        return self


class LandedInput(ToolInput):
    tool: Literal["landed"]
    origin: Annotated[
        str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Z]{2}$")
    ]
    destination: Site = "US"
    hs_code: Text
    as_of: date
    currency: Annotated[
        str, StringConstraints(strip_whitespace=True, to_upper=True, pattern=r"^[A-Z]{3}$")
    ] = "USD"
    goods_value: Money
    freight: Money
    insurance: Money | None = None
    brokerage_usd: Money | None = None
    inland_usd: Money | None = None
    customs_value_usd: Money | None = None
    quantity: int = Field(ge=1, le=1_000_000)
    usd_per_currency: Annotated[Decimal, Field(gt=0, le=10000, decimal_places=8)] | None = None
    exchange_source: RateSource | None = None
    duties: list[DutyInput] = Field(default_factory=list, max_length=15)
    duties_confirmed: bool = False


class HsInput(ToolInput):
    tool: Literal["hs"]
    query: Text


ScriptInput = Annotated[
    KeywordInput | ListingInput | PpcInput | FbaInput | LandedInput | HsInput,
    Field(discriminator="tool"),
]


class ScriptResult(BaseModel):
    report_id: UUID
    schema_version: Literal[1] = 1
    site: Site = "US"
    collected_at: datetime
    status: ToolStatus
    warnings: list[str] = Field(default_factory=list)


class QuerySample(BaseModel):
    query: str
    source_url: str
    collected_at: datetime
    status: Literal["success", "empty", "failed"]
    suggestions: list[str]
    reason: str | None = None


class KeywordEvidence(BaseModel):
    keyword: str
    queries: list[str]


class KeywordResult(ScriptResult):
    tool: Literal["keywords"] = "keywords"
    keyword: str
    language: Literal["en-US"] = "en-US"
    query_limit: Literal[30] = 30
    suggestion_limit_per_query: Literal[10] = 10
    samples: list[QuerySample]
    keywords: list[KeywordEvidence]


class ProductEvidence(BaseModel):
    asin: str
    source_url: str
    collected_at: datetime
    status: Literal["success", "failed"]
    reason: str | None = None
    title: str | None = None
    brand: str | None = None
    price: str | None = None
    price_currency: str | None = None
    category: str | None = None
    sales_ranks: list[str] | None = None
    highlights: list[str] | None = None
    rating_count: str | None = None
    field_sources: dict[str, str] = Field(default_factory=dict)
    missing_fields: list[str] = Field(default_factory=list)


class ListingEvidence(ProductEvidence):
    rating: str | None = None
    description: str | None = None
    category_path: list[str] | None = None
    first_available: str | None = None
    image_count: int | None = None
    has_aplus: bool | None = None
    image_url: str | None = None


class ListingResult(ScriptResult):
    tool: Literal["listing"] = "listing"
    listing: ListingEvidence


class PpcResult(ScriptResult):
    tool: Literal["ppc"] = "ppc"
    competitors: list[ProductEvidence]


class CostLine(BaseModel):
    name: str
    amount_usd: Decimal | None
    formula: str
    source: RateSource | None = None
    source_type: Literal["official", "manual", "calculated", "unknown"]


class FbaResult(ScriptResult):
    tool: Literal["fba"] = "fba"
    inputs: FbaInput
    currency: Literal["USD"] = "USD"
    size_tier: str
    sorted_dimensions_in: tuple[Decimal, Decimal, Decimal]
    dimensional_weight_lb: Decimal
    shipping_weight_lb: Decimal
    volume_ft3: Decimal
    known_subtotal_usd: Decimal
    total_usd: Decimal | None
    lines: list[CostLine]
    pending: list[str]


class LandedResult(ScriptResult):
    tool: Literal["landed"] = "landed"
    inputs: LandedInput
    hs_code: str
    currency: Literal["USD"] = "USD"
    customs_value_usd: Decimal | None
    known_subtotal_usd: Decimal
    total_usd: Decimal | None
    per_unit_usd: Decimal | None
    lines: list[CostLine]
    pending: list[str]
    review_urls: list[str]


class HsCandidate(BaseModel):
    code: str
    description: str
    general_rate_text: str | None
    special_rate_text: str | None
    column_2_rate_text: str | None
    source_url: str
    official_effective_period: str | None
    pending: list[str]


class HsResult(ScriptResult):
    tool: Literal["hs"] = "hs"
    query: str
    source_url: str
    observed_count: int
    candidate_limit: Literal[20] = 20
    candidates: list[HsCandidate]
    pending: list[str]


ScriptOutput = Annotated[
    KeywordResult | ListingResult | PpcResult | FbaResult | LandedResult | HsResult,
    Field(discriminator="tool"),
]
