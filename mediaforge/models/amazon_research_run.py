from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from mediaforge.models.amazon_category import CategoryAnalysis, CategoryInputs, CategoryScope
from mediaforge.models.amazon_product import ProductCosts, ProductDepth
from mediaforge.models.amazon_research import (
    AmazonListing,
    AmazonResearchReport,
    AmazonResearchRequest,
    AmazonReviewTopic,
    ResearchMode,
)

RunStatus = Literal[
    "queued", "running", "done", "partial_fail", "failed", "queue_failed", "interrupted"
]
StepStatus = Literal["pending", "success", "empty", "incomplete", "failed"]
RetryTarget = Literal["resume", "failed", "translation"]
TRANSLATION_KEYS = {
    "store": "store-translation",
    "bestsellers": "category-translation",
    "product": "product-translation",
}
EvidenceKind = Literal[
    "section", "suggestions", "board", "analysis", "product", "search", "competitor", "reviews"
]


class AmazonResearchContext(BaseModel):
    run_id: UUID
    asin: str | None = Field(default=None, pattern=r"^[A-Z0-9]{10}$")
    direction: str | None = Field(default=None, max_length=240)


class AmazonResearchRunRequest(AmazonResearchRequest):
    mode: ResearchMode = "store"
    site: Literal["US"] = "US"
    submission_id: UUID | None = None
    category: CategoryInputs | None = None
    product_costs: ProductCosts | None = None
    context: AmazonResearchContext | None = None
    supplemental_notes: str = Field(default="", max_length=2000)


class AmazonStoreSample(AmazonListing):
    # 店铺卡片价格保留原表示、显示币种与口径，供后续报告复核。
    family_asin: str | None = None
    color_group_asin: str | None = None
    has_variants: bool | None = None
    family_members: list[str] = Field(default_factory=list)
    color_members: list[str] = Field(default_factory=list)
    size_group_members: list[str] = Field(default_factory=list)
    product_type: str | None = None
    price_currency: str | None = None
    price_label: str | None = None
    color: str | None = None
    size: str | None = None
    brand: str | None = None
    store_url: str | None = None


class AmazonStoreIdentity(BaseModel):
    family_asin: str | None = None
    color_group_asin: str | None = None
    color: str | None = None
    size: str | None = None
    standalone: bool = False


class AmazonStoreArchive(BaseModel):
    asin_count: int
    family_count: int
    color_group_count: int
    unknown_family_count: int
    unknown_color_count: int
    unknown_size_count: int
    identities: dict[str, AmazonStoreIdentity]


class AmazonStoreSampleScope(BaseModel):
    section_limit: int
    asin_limit_per_section: int
    navigation_truncated: bool | None = None


class AmazonPriceObservation(BaseModel):
    section: str
    price_cents: int


class AmazonPriceBand(BaseModel):
    min_cents: int
    max_cents: int | None
    asins: list[str]
    share_pct: float | None


class AmazonSectionMetrics(BaseModel):
    section: str
    asin_count: int | None
    priced_count: int
    rating_count: int
    average_rating: float | None
    rating_total_count: int
    median_rating_total: float | None


class AmazonSectionOverlap(BaseModel):
    sections: tuple[str, str]
    asins: list[str]


class AmazonRatedStyle(BaseModel):
    asin: str
    count: int
    family_asin: str | None


class AmazonStoreProductMatrix(BaseModel):
    rules_version: Literal["us-store-matrix-v1"] = "us-store-matrix-v1"
    asin_count: int
    prices: dict[str, AmazonPriceObservation]
    price_bands: list[AmazonPriceBand]
    missing_price: list[str]
    missing_eligible_price: list[str]
    sections: list[AmazonSectionMetrics]
    overlaps: list[AmazonSectionOverlap]
    product_types: dict[str, list[str]]
    specification_combinations: dict[str, list[str]]
    model_candidates: dict[str, list[str]]
    rating_leaders: list[AmazonRatedStyle]
    new_sections: dict[str, list[str]]


class AmazonListingDate(BaseModel):
    asin: str
    url: str
    collected_at: datetime
    label: Literal["first_available", "release_date"] | None = None
    date_text: str | None = None
    problem: str | None = None


class AmazonSeriesChange(BaseModel):
    family_asin: str
    newly_seen: list[str]
    no_longer_seen: list[str]


class AmazonStoreChange(BaseModel):
    previous_run_id: UUID
    previous_collected_at: datetime
    current_collected_at: datetime
    section_keys: list[str]
    newly_seen: list[str]
    no_longer_seen: list[str]
    series_changes: list[AmazonSeriesChange]


class AmazonLifecycleSample(BaseModel):
    asin: str
    section: str
    basis: Literal[
        "new_section", "recent_date", "first_available", "observed_again",
        "rating_total", "low_rating",
    ]
    rating: float | None = None
    rating_total: int | None = None
    date: AmazonListingDate | None = None


class AmazonBandPerformance(BaseModel):
    min_cents: int
    max_cents: int | None
    asin_count: int
    rating_count: int
    average_rating: float | None
    rating_total_count: int
    median_rating_total: float | None
    previous_count: int | None = None
    current_count: int | None = None


class AmazonHistoricalPrice(BaseModel):
    asin: str
    section: str
    previous_cents: int
    previous_collected_at: datetime
    current_cents: int
    current_collected_at: datetime


class AmazonStorePerformance(BaseModel):
    new: list[AmazonLifecycleSample]
    mature: list[AmazonLifecycleSample]
    low_rating: list[AmazonLifecycleSample]
    unknown_lifecycle: int
    bands: list[AmazonBandPerformance]
    observation_days: int
    mature_rating_total: int
    low_rating_total: int
    low_rating_threshold: float
    comparable_asins: int | None = None
    matched_prices: int | None = None
    increased: int = 0
    decreased: int = 0
    price_changes: list[AmazonHistoricalPrice] = Field(default_factory=list)


class AmazonStoreLifecycle(BaseModel):
    colors: dict[str, int]
    sizes: dict[str, int]
    missing_color: int
    missing_size: int
    dates: list[AmazonListingDate] = Field(default_factory=list)
    change: AmazonStoreChange | None = None
    performance: AmazonStorePerformance | None = None


class AmazonMarketListing(BaseModel):
    asin: str
    url: str
    collected_at: datetime
    title: str | None = None
    brand: str | None = None
    store_url: str | None = None
    category: str | None = None
    price: str | None = None
    price_currency: str | None = None
    rating: str | None = None
    rating_count: str | None = None
    monthly_bought: int | None = None
    monthly_bought_label: str | None = None
    review_sample_count: int | None = None
    review_topics: list[AmazonReviewTopic] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    image_count: int | None = None
    has_aplus: bool | None = None
    problem: str | None = None


class AmazonBenchmarkCandidate(BaseModel):
    asin: str
    title: str


class AmazonBenchmarkSearch(BaseModel):
    target_asin: str
    query: str
    url: str
    collected_at: datetime
    status: Literal["success", "empty", "failed"]
    candidates: list[AmazonBenchmarkCandidate] = Field(default_factory=list)
    problem: str | None = None


class AmazonBenchmarkPair(BaseModel):
    target_asin: str
    competitor_asin: str
    shared_terms: list[str]
    shared_category: str | None = None
    audience: str | None = None
    price_gap_cents: int | None = None
    swot: dict[str, str]


class AmazonStoreCompetition(BaseModel):
    targets: list[str]
    searches: list[AmazonBenchmarkSearch] = Field(default_factory=list)
    listings: dict[str, AmazonMarketListing] = Field(default_factory=dict)
    pairs: list[AmazonBenchmarkPair] = Field(default_factory=list)


class AmazonRevenueTotals(BaseModel):
    units: tuple[int, int, int] | None = None
    cents: tuple[int, int, int] | None = None


class AmazonRevenueSample(BaseModel):
    family_asin: str
    asin: str
    members: list[str]
    sections: list[str]
    segment: Literal["representative", "tail"]
    listing: AmazonMarketListing | None = None
    price_cents: int | None = None
    monthly: AmazonRevenueTotals = Field(default_factory=AmazonRevenueTotals)


class AmazonStoreRevenue(BaseModel):
    family_count: int
    unknown_family_count: int
    representative_count: int
    samples: list[AmazonRevenueSample]
    factors_percent: tuple[int, int, int] = (100, 125, 150)
    annual_months: int = 12
    monthly: dict[str, AmazonRevenueTotals] = Field(default_factory=dict)
    public_store: AmazonRevenueTotals = Field(default_factory=AmazonRevenueTotals)
    section_monthly: dict[str, AmazonRevenueTotals] = Field(default_factory=dict)


class AmazonGrowthSource(BaseModel):
    label: str
    url: str
    collected_at: datetime | None = None
    asin: str | None = None
    section: str | None = None


class AmazonGrowthFinding(BaseModel):
    key: str
    title: str
    priority: Literal["high", "medium", "low"]
    observed: str
    hypothesis: str
    action: str
    sources: list[AmazonGrowthSource] = Field(default_factory=list)


class AmazonGrowthAction(BaseModel):
    day: Literal[30, 60, 90]
    action: str
    findings: list[str]


class AmazonStoreGrowth(BaseModel):
    opportunities: list[AmazonGrowthFinding]
    risks: list[AmazonGrowthFinding]
    plan: list[AmazonGrowthAction]
    review_details: dict[str, AmazonMarketListing] = Field(default_factory=dict)


class AmazonEvidence(BaseModel):
    key: str
    kind: EvidenceKind
    url: str
    title: str
    title_zh: str | None = None
    status: StepStatus = "pending"
    collected_at: datetime | None = None
    method: str = "Amazon 美国站公开 HTML"
    final_url: str | None = None
    home_url: str | None = None
    reason: str | None = None
    code: str | None = None
    attempts: int = 0
    duration_ms: int | None = None
    products: list[AmazonStoreSample] = Field(default_factory=list)
    observed_count: int | None = None
    links: list[tuple[str, str]] = Field(default_factory=list)
    role: str | None = None
    excerpt: str | None = None


class AmazonResearchSnapshot(BaseModel):
    version: int = 1
    site: Literal["US"] = "US"
    currency: Literal["USD"] = "USD"
    source_language: Literal["en-US"] = "en-US"
    report_language: Literal["zh-CN"] = "zh-CN"
    store_root: str | None = None
    store_scope: AmazonStoreSampleScope | None = None
    category_scope: CategoryScope | None = None
    category_analysis: CategoryAnalysis | None = None
    product_depth: ProductDepth | None = None
    evidence: list[AmazonEvidence] = Field(default_factory=list)
    asin_sources: dict[str, list[str]] = Field(default_factory=dict)
    product_archive: AmazonStoreArchive | None = None
    product_matrix: AmazonStoreProductMatrix | None = None
    lifecycle: AmazonStoreLifecycle | None = None
    competition: AmazonStoreCompetition | None = None
    revenue: AmazonStoreRevenue | None = None
    growth: AmazonStoreGrowth | None = None
    translation_status: StepStatus = "pending"
    translation_missing: list[str] = Field(default_factory=list)
    field_translations: dict[str, str] = Field(default_factory=dict)
    notes: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class AmazonResearchRunSummary(BaseModel):
    id: UUID
    mode: ResearchMode
    source: str
    status: RunStatus
    stage: str
    title: str | None = None
    canonical_source: str
    created_at: datetime
    updated_at: datetime
    attempt: int


class AmazonResearchRun(AmazonResearchRunSummary):
    error: str | None = None
    inputs: AmazonResearchRunRequest
    report: AmazonResearchReport | None = None
    snapshot: AmazonResearchSnapshot
