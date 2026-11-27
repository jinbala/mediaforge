from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from mediaforge.models.amazon_research import (
    AmazonListing,
    AmazonResearchReport,
    AmazonResearchRequest,
)

RunStatus = Literal[
    "queued", "running", "done", "partial_fail", "failed", "queue_failed", "interrupted"
]
StepStatus = Literal["pending", "success", "empty", "incomplete", "failed"]
RetryTarget = Literal["resume", "failed"]


class AmazonResearchRunRequest(AmazonResearchRequest):
    mode: Literal["store"] = "store"
    submission_id: UUID | None = None


class AmazonStoreSample(AmazonListing):
    # 店铺卡片价格保留原表示、显示币种与口径，供后续报告复核。
    family_asin: str | None = None
    color_group_asin: str | None = None
    product_type: str | None = None
    price_currency: str | None = None
    price_label: str | None = None
    color: str | None = None
    size: str | None = None
    brand: str | None = None
    store_url: str | None = None


class AmazonEvidence(BaseModel):
    key: str
    kind: Literal["section", "suggestions"]
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
    evidence: list[AmazonEvidence] = Field(default_factory=list)
    asin_sources: dict[str, list[str]] = Field(default_factory=dict)
    translation_status: StepStatus = "pending"
    translation_missing: list[str] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    suggestions: list[str] = Field(default_factory=list)


class AmazonResearchRunSummary(BaseModel):
    id: UUID
    mode: Literal["store"]
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
