from pydantic import BaseModel, Field

from mediaforge.models.amazon_research_run import AmazonResearchRunSummary


class ResearchComparisonMetric(BaseModel):
    label: str
    before: int | float
    after: int | float
    unit: str | None = None
    basis: str | None = None


class ResearchRunComparison(BaseModel):
    previous: AmazonResearchRunSummary | None
    current: AmazonResearchRunSummary
    metrics: list[ResearchComparisonMetric] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
