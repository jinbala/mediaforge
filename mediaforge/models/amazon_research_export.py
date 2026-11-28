from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, Field

from mediaforge.models.amazon_category import CategoryScope
from mediaforge.models.amazon_research import ResearchMode
from mediaforge.models.amazon_research_run import AmazonStoreSampleScope, RunStatus

ReportSectionStatus = Literal["complete", "partial", "unknown"]
ReportProvenance = Literal["observed", "estimate", "manual", "unknown"]
ReportCell = str | int | float | None


class ResearchReportSection(BaseModel):
    id: str
    title: str
    status: ReportSectionStatus
    sample_count: int | None = None


class ResearchDataSource(BaseModel):
    id: str
    key: str | None = None
    kind: str
    title: str
    url: str | None = None
    requested_url: str | None = None
    collected_at: datetime | None = None
    status: str
    provenance: ReportProvenance
    method: str | None = None
    reason: str | None = None
    sample_count: int | None = None


class ResearchCollectionRange(BaseModel):
    collected_from: datetime | None = None
    collected_to: datetime | None = None
    source_count: int
    observed_source_count: int
    unique_asin_count: int | None = None
    store_scope: AmazonStoreSampleScope | None = None
    category_scope: CategoryScope | None = None


class ResearchReportMetadata(BaseModel):
    version: str = "amazon-research-report-v1"
    run_id: UUID
    mode: ResearchMode
    site: str
    currency: str
    status: RunStatus
    source_url: str | None = None
    collection_range: ResearchCollectionRange
    data_sources: list[ResearchDataSource] = Field(default_factory=list)
    truncated: bool = False


class ResearchReportIndex(ResearchReportMetadata):
    sections: list[ResearchReportSection]


class ResearchReportRow(BaseModel):
    cells: list[ReportCell]
    asins: list[str] = Field(default_factory=list)


class ResearchReportTable(BaseModel):
    title: str
    columns: list[str]
    rows: list[ResearchReportRow] = Field(default_factory=list)
    provenance: ReportProvenance | None = None


class ResearchSectionEvidence(ResearchReportSection):
    requirements: list[str] = Field(default_factory=list)
    provenance: list[ReportProvenance] = Field(default_factory=list)
    notes: list[str] = Field(default_factory=list)
    tables: list[ResearchReportTable] = Field(default_factory=list)
    source_ids: list[str] = Field(default_factory=list)


class ResearchSourceSamples(BaseModel):
    source_id: str
    table: ResearchReportTable


class ResearchEvidenceBundle(ResearchReportMetadata):
    sections: list[ResearchSectionEvidence]
    samples: list[ResearchSourceSamples] = Field(default_factory=list)
    supplemental_notes: str | None = None
    field_translations: dict[str, str] = Field(default_factory=dict)
