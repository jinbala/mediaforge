from datetime import date
from decimal import Decimal
from typing import Annotated, Literal

from pydantic import BaseModel, ConfigDict, Field, StringConstraints

from mediaforge.models.amazon_script_tools import Money, Source, Text

PositiveCount = Annotated[int, Field(ge=1, le=100_000)]
Fraction = Annotated[Decimal, Field(ge=0, le=1, decimal_places=4)]
Asin = Annotated[str, StringConstraints(to_upper=True, pattern=r"^[A-Z0-9]{10}$")]


class CategoryCostInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    target_asin: Asin | None = None
    target_price_usd: Annotated[Money, Field(gt=0)] | None = None
    purchase_usd: Money
    inbound_usd: Money
    fulfillment_usd: Money
    storage_usd: Money
    referral_rate: Fraction
    ads_rate: Fraction
    returns_rate: Fraction
    fixed_monthly_usd: Money
    launch_fixed_usd: Money
    lean_units: PositiveCount
    standard_units: PositiveCount
    monthly_units: PositiveCount
    source_note: Text
    effective_on: date
    version: Text
    verified_quote_note: Text | None = None


class CategoryInputs(BaseModel):
    model_config = ConfigDict(extra="forbid")
    subcategories: list[Source] = Field(default_factory=list, max_length=5)
    costs: CategoryCostInputs | None = None


class CategoryScope(BaseModel):
    main_limit: int = 30
    child_limit: int = 5
    child_asin_limit: int = 20
    discovery_truncated: bool = False


class CategoryRow(BaseModel):
    # 统一表格结构同时用于页面和下载；ASIN 通过快照映射回实际榜单证据。
    cells: list[str | None]
    asins: list[str] = Field(default_factory=list)


class CategoryTable(BaseModel):
    title: str
    columns: list[str]
    rows: list[CategoryRow]


class CategorySection(BaseModel):
    key: Literal["opportunities", "competition", "keywords", "profit", "summary"]
    title: str
    reqs: list[str]
    notes: list[str]
    tables: list[CategoryTable] = Field(default_factory=list)


class CategoryAnalysis(BaseModel):
    version: Literal["category-us-v1"] = "category-us-v1"
    status: Literal["complete", "incomplete"]
    sections: list[CategorySection]
