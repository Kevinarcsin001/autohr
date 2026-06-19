"""候选人详情 schema（任务 24）。

聚合 Candidate + ScreeningResult + Score + ParsedStructure + Resume
为前端详情页所需的一次性响应。

独立端点（不在 detail 内嵌套）：
- ``/resume-url``：返回 5min 签名 URL
- ``/activity``：审计日志 + 改判历史 UNION 后时间线展示
- reasons / interview：复用任务 18 / 19 的现有端点

设计原则：
- detail 只返回一层数据（candidate / screening / score / structure / resume）
- 分页项走独立端点，避免主请求过重
- 不暴露未脱敏 PII（service 层 team_id 过滤是唯一信任边界）
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field

from app.schemas.candidate_structure import CandidateStructure
from app.schemas.score import ScoreOut
from app.schemas.screening import ScreeningResultOut

# ============================================================================
# detail 端点
# ============================================================================


class CandidateResumeOut(BaseModel):
    """最新一条 resume（用于详情页 raw_text 预览 + reason 高亮）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    parsed_text: str | None = None
    file_storage_key: str
    mime_type: str | None = None
    filename: str | None = None


class CandidateDetailResponse(BaseModel):
    """候选人详情聚合响应。"""

    candidate: CandidateSummary
    screening_result: ScreeningResultOut | None = None
    score: ScoreOut | None = None
    parsed_structure: CandidateStructure | None = None
    resume: CandidateResumeOut | None = None


class CandidateSummary(BaseModel):
    """候选人基础信息（脱敏后）。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    name: str
    phone: str | None = None
    email: str | None = None
    source_type: str | None = None
    source_id: uuid.UUID | None = None
    created_at: datetime


# ============================================================================
# resume-url 端点
# ============================================================================


class ResumeUrlResponse(BaseModel):
    """签名 URL + 过期时间（前端用于过期重取）。"""

    url: str
    expires_at: datetime
    mime_type: str | None = None
    filename: str | None = None


# ============================================================================
# activity 端点（审计日志 + 改判历史 UNION）
# ============================================================================


ActivityType = Literal["audit_log", "override"]


class CandidateActivityItem(BaseModel):
    """时间线条目（audit 或 override）。"""

    type: ActivityType
    id: uuid.UUID
    created_at: datetime
    actor_id: uuid.UUID | None = None
    action: str
    summary: str
    details: dict[str, Any] | None = None


class CandidateActivityListResponse(BaseModel):
    """活动列表分页响应。"""

    items: list[CandidateActivityItem]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


__all__ = [
    "CandidateSummary",
    "CandidateResumeOut",
    "CandidateDetailResponse",
    "ResumeUrlResponse",
    "ActivityType",
    "CandidateActivityItem",
    "CandidateActivityListResponse",
]
