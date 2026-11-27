from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mediaforge.models.amazon_script_tools import (
    FbaInput,
    FbaResult,
    HsInput,
    HsResult,
    LandedInput,
    LandedResult,
)

SkillId = Literal[
    "REQ-045",
    "REQ-046",
    "REQ-047",
    "REQ-048",
    "REQ-049",
    "REQ-050",
    "REQ-051",
    "REQ-052",
    "REQ-053",
    "REQ-054",
    "REQ-055",
    "REQ-056",
    "REQ-057",
    "REQ-058",
    "REQ-059",
    "REQ-060",
    "REQ-061",
    "REQ-062",
    "REQ-063",
    "REQ-064",
    "REQ-065",
    "REQ-066",
    "REQ-067",
    "REQ-068",
    "REQ-069",
    "REQ-070",
    "REQ-071",
    "REQ-072",
    "REQ-073",
    "REQ-074",
    "REQ-075",
    "REQ-076",
    "REQ-077",
    "REQ-078",
    "REQ-079",
    "REQ-080",
    "REQ-081",
    "REQ-082",
    "REQ-083",
    "REQ-084",
    "REQ-085",
    "REQ-086",
    "REQ-087",
    "REQ-088",
    "REQ-089",
    "REQ-090",
    "REQ-091",
    "REQ-092",
    "REQ-093",
    "REQ-094",
    "REQ-095",
]


class SkillField(BaseModel):
    key: str
    label: str
    kind: Literal["text", "lines", "number", "choice"] = "text"
    choices: list[str] = Field(default_factory=list)
    required: bool = True


class SkillDefinition(BaseModel):
    req: SkillId
    title: str
    fields: list[SkillField]
    note: str = "仅生成分析或执行清单，不代替平台操作。"
    group: Literal["基础运营", "经营与增长", "数据分析", "店铺与供应链"] = "基础运营"


class SkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, str] = Field(default_factory=dict, max_length=25)
    fba: FbaInput | None = None
    landed: LandedInput | None = None
    hs: HsInput | None = None


class SkillSection(BaseModel):
    title: str
    rows: list[list[str]]
    columns: list[str] = Field(default_factory=lambda: ["项目", "建议／结果"])


class SkillReport(BaseModel):
    req: SkillId
    title: str
    created_at: datetime
    site: Literal["US"] = "US"
    sections: list[SkillSection]
    confirmed_inputs: dict[str, str]
    limitations: list[str]
    fba: FbaResult | None = None
    landed: LandedResult | None = None
    hs: HsResult | None = None
