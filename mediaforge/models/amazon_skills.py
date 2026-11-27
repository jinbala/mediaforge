from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

from mediaforge.models.amazon_script_tools import FbaInput, FbaResult

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


class SkillRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    values: dict[str, str] = Field(default_factory=dict, max_length=25)
    fba: FbaInput | None = None


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
