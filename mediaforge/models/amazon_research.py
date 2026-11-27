from datetime import datetime
from typing import Literal

from pydantic import BaseModel, Field

ResearchMode = Literal["product", "bestsellers", "store"]


class AmazonResearchRequest(BaseModel):
    mode: ResearchMode
    source: str = Field(min_length=1, max_length=2000)
    keyword: str = Field(default="", max_length=100)
    review_samples: str = Field(default="", max_length=4000)


class AmazonStoreVariant(BaseModel):
    # 组内各商品共用 group_asin，每个商品仍有自己的 ASIN 和尺码。
    group_asin: str
    size: str
    title: str
    title_zh: str | None = None
    color: str | None = None
    color_zh: str | None = None


class AmazonListing(BaseModel):
    asin: str
    title: str
    url: str
    title_zh: str | None = None
    image_url: str | None = None
    price: str | None = None
    rating: str | None = None
    rating_count: str | None = None
    rank: str | None = None
    store_variant: AmazonStoreVariant | None = None


class AmazonReview(BaseModel):
    text: str
    rating: str | None = None
    source: Literal["amazon", "manual"] = "amazon"


class AmazonReviewTopic(BaseModel):
    label: str
    matches: int
    suggestion: str


class AmazonProduct(AmazonListing):
    brand: str | None = None
    store_url: str | None = None
    category: str | None = None
    sales_ranks: list[str] = Field(default_factory=list)
    highlights: list[str] = Field(default_factory=list)
    reviews: list[AmazonReview] = Field(default_factory=list)
    review_topics: list[AmazonReviewTopic] = Field(default_factory=list)
    image_brief: str
    description: str | None = None
    category_path: list[str] | None = None
    first_available: str | None = None
    image_count: int | None = None
    has_aplus: bool | None = None


class AmazonStorePage(BaseModel):
    title: str
    url: str
    title_zh: str | None = None


class AmazonResearchReport(BaseModel):
    # 单品模式填 product，榜单填 listings，店铺可同时返回商品和栏目。
    mode: ResearchMode
    source_url: str
    title: str
    title_zh: str | None = None
    collected_at: datetime
    product: AmazonProduct | None = None
    listings: list[AmazonListing] = Field(default_factory=list)
    store_pages: list[AmazonStorePage] = Field(default_factory=list)
    keyword: str | None = None
    suggestions: list[str] = Field(default_factory=list)
    warnings: list[str] = Field(default_factory=list)
