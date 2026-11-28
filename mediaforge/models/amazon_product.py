from datetime import date, datetime
from typing import Annotated, Literal

from pydantic import BaseModel, Field

from mediaforge.models.amazon_research import AmazonProduct

ReviewBucket = Literal["one", "two", "three", "recent"]
Money = Annotated[float, Field(ge=0, le=1_000_000)]
Ratio = Annotated[float, Field(ge=0, le=1)]
Percentage = Annotated[float, Field(ge=0, le=100)]


class ProductCosts(BaseModel):
    """美国站 USD 单件成本和人工确认的校准输入；空值不按零计算。"""

    purchase: Money | None = None
    inbound: Money | None = None
    fulfillment: Money | None = None
    storage: Money | None = None
    ad_pct: Ratio | None = None
    return_pct: Ratio | None = None
    referral_pct: Ratio | None = None
    sale_price: Annotated[float, Field(gt=0, le=1_000_000)] | None = None
    fee_date: date | None = None
    fee_source: str | None = Field(default=None, max_length=500)
    benchmark_bsr: int | None = Field(default=None, gt=0, le=100_000_000)
    benchmark_monthly_units: int | None = Field(default=None, gt=0, le=10_000_000)
    benchmark_category: str | None = Field(default=None, max_length=200)
    benchmark_source: str | None = Field(default=None, max_length=500)
    benchmark_date: date | None = None


class ProductMedia(BaseModel):
    url: str
    label: str | None = None
    width: int | None = None
    height: int | None = None
    poster: str | None = None


class ProductReviewSample(BaseModel):
    id: str
    text: str
    source: Literal["amazon", "manual"]
    url: str | None = None
    stars: int | None = Field(default=None, ge=1, le=5)
    date: str | None = None
    variant: str | None = None
    verified: bool | None = None
    helpful: int | None = Field(default=None, ge=0)


class ProductPage(BaseModel):
    product: AmazonProduct
    collected_at: datetime
    specs: dict[str, str] = Field(default_factory=dict)
    important: list[str] = Field(default_factory=list)
    aplus: list[str] = Field(default_factory=list)
    images: list[ProductMedia] = Field(default_factory=list)
    videos: list[ProductMedia] = Field(default_factory=list)
    currency: str | None = None
    price_amount: Money | None = None
    rating_distribution: dict[int, Percentage] = Field(default_factory=dict)
    reviews: list[ProductReviewSample] = Field(default_factory=list)
    parent_asin: str | None = None
    variant_asins: list[str] = Field(default_factory=list)
    field_evidence: dict[str, str] = Field(default_factory=dict)
    screenshot_data_url: str | None = None


class ProductSearchRow(BaseModel):
    asin: str
    title: str
    url: str
    rank: int = Field(gt=0)
    brand: str | None = None
    price_amount: Money | None = None
    currency: str | None = None
    sponsored: bool | None = None


class ProductSearch(BaseModel):
    query: str
    url: str
    collected_at: datetime
    rows: list[ProductSearchRow] = Field(default_factory=list)
    result_min: int | None = None
    result_max: int | None = None
    result_label: str | None = None


class ProductCandidate(BaseModel):
    asin: str
    title: str
    url: str
    reasons: list[str] = Field(default_factory=list)
    queries: list[str] = Field(default_factory=list)


class ProductReviewPage(BaseModel):
    bucket: ReviewBucket
    url: str
    collected_at: datetime
    rows: list[ProductReviewSample] = Field(default_factory=list)
    limited: bool = False


class ProductScenario(BaseModel):
    name: str
    monthly_units: int
    annual_revenue: float | None = None
    annual_profit: float | None = None


class ProductFinance(BaseModel):
    currency: str | None = None
    price: float | None = None
    category: str | None = None
    rank: int | None = None
    assumptions: list[str] = Field(default_factory=list)
    missing: list[str] = Field(default_factory=list)
    scenarios: list[ProductScenario] = Field(default_factory=list)
    unit_costs: dict[str, float | None] = Field(default_factory=dict)
    unit_profit: float | None = None
    roi_pct: float | None = None
    fee_source: str | None = None
    fee_date: date | None = None


class ProductCitation(BaseModel):
    label: str
    url: str
    collected_at: datetime | None = None
    asin: str | None = None


class ProductTable(BaseModel):
    title: str
    columns: list[str]
    rows: list[list[str | int | float | None]] = Field(default_factory=list)


class ProductSection(BaseModel):
    req: Literal[
        "REQ-021",
        "REQ-022",
        "REQ-023",
        "REQ-024",
        "REQ-025",
        "REQ-026",
        "REQ-027",
        "REQ-028",
        "REQ-029",
    ]
    title: str
    status: Literal["complete", "partial", "unknown"]
    summary: list[str] = Field(default_factory=list)
    tables: list[ProductTable] = Field(default_factory=list)
    sources: list[ProductCitation] = Field(default_factory=list)


class ProductShot(BaseModel):
    id: str
    type: Literal["image", "video"]
    title: str
    brief: str
    evidence: list[str] = Field(default_factory=list)


class ProductTemplate(BaseModel):
    title: str
    bullets: list[str]
    description: str
    aplus: list[str]
    backend_keywords: str
    conditions: list[str]


class ProductAnalysis(BaseModel):
    sections: list[ProductSection]
    image_plan: list[ProductShot]
    listing: ProductTemplate
    finance: ProductFinance
    decision: str


class ProductDepth(BaseModel):
    overview: ProductPage | None = None
    searches: list[ProductSearch] = Field(default_factory=list)
    candidates: list[ProductCandidate] = Field(default_factory=list)
    competitors: list[ProductPage] = Field(default_factory=list)
    review_pages: list[ProductReviewPage] = Field(default_factory=list)
    analysis: ProductAnalysis | None = None
