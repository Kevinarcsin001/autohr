"""候选人列表 schema（任务 23）。

聚合 Candidate + ScreeningResult + Score + ParsedStructure + Source
为前端三分组列表所需的一次性响应。
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# 类型别名
# ============================================================================


CandidateGroup = Literal["all", "passed", "disqualified", "pending"]
"""三分组：
- ``passed``：未淘汰（disqualified=false）
- ``disqualified``：已淘汰（disqualified=true）
- ``pending``：尚未筛选（无 screening_result 行）
- ``all``：全部（默认）
"""

SortBy = Literal[
    "total", "skill", "experience", "education", "stability", "potential", "name"
]
SortOrder = Literal["asc", "desc"]


# ============================================================================
# 列表项
# ============================================================================


class CandidateListItem(BaseModel):
    """候选人列表项（一次返回所有前端需要的字段）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    email: str | None = None
    phone: str | None = None

    # 来源
    source_type: str | None = None
    source_id: uuid.UUID | None = None

    # 筛选结果（per job）
    screening_id: uuid.UUID | None = None
    disqualified: bool | None = None
    """None = 尚未筛选（pending 组）；true/false = 已筛选"""
    screening_reasons: list[str] | None = None
    manually_overridden: bool = False

    # 评分（per job）
    score_id: uuid.UUID | None = None
    total: int | None = None
    skill: int | None = None
    experience: int | None = None
    education_score: int | None = None
    stability: int | None = None
    potential: int | None = None
    model_used: str | None = None

    # 结构化字段（从 latest ParsedStructure.data.structure 取）
    education: str | None = None
    years_of_experience: int | None = None
    current_company: str | None = None
    skills: list[str] = Field(default_factory=list)

    # 分组（前端 tab 切换用）
    group: Literal["passed", "disqualified", "pending"]

    created_at: str
    updated_at: str | None = None


class CandidateListResponse(BaseModel):
    items: list[CandidateListItem]
    total: int
    page: int
    page_size: int

    # 三分组各自的总数（不受 group 过滤影响，前端 tab 显示）
    group_counts: dict[str, int] = Field(
        default_factory=lambda: {"passed": 0, "disqualified": 0, "pending": 0}
    )


# ============================================================================
# 筛选 / 排序参数（API query 解析）
# ============================================================================


class CandidateListFilters(BaseModel):
    """候选人列表过滤参数（query 解析后用）。"""

    group: CandidateGroup = "all"
    min_score: int | None = Field(default=None, ge=0, le=100)
    max_score: int | None = Field(default=None, ge=0, le=100)
    education: str | None = None  # high_school / bachelor / master / phd
    min_years: int | None = Field(default=None, ge=0, le=80)
    max_years: int | None = Field(default=None, ge=0, le=80)
    skill: str | None = None  # 单技能子串（不区分大小写）
    source: str | None = None  # upload / platform / email
    sort_by: SortBy = "total"
    sort_order: SortOrder = "desc"
    page: int = Field(default=1, ge=1)
    page_size: int = Field(default=50, ge=1, le=200)


__all__ = [
    "CandidateGroup",
    "SortBy",
    "SortOrder",
    "CandidateListItem",
    "CandidateListResponse",
    "CandidateListFilters",
]
