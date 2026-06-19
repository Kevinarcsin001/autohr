"""ScorerService 用的 Pydantic schema（任务 17）。

包含：
- ``ScoreDimensions``：LLM 输出的子维度 JSON schema
- ``ScoreOut`` / ``ScoreListItem``：scores 行的对外表示
"""
from __future__ import annotations

import uuid

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# LLM 输出 schema（response_schema 用）
# ============================================================================


class ScoreDimensions(BaseModel):
    """LLM 评分输出 schema（6 个 0-100 整数维度）。

    total 由 LLM 综合给出；其余 5 个子维度独立评分。
    """

    model_config = ConfigDict(extra="forbid")

    total: int = Field(..., ge=0, le=100, description="综合分（0-100）")
    skill: int = Field(..., ge=0, le=100, description="技能匹配度")
    experience: int = Field(..., ge=0, le=100, description="经验相关性")
    education: int = Field(..., ge=0, le=100, description="学历匹配")
    stability: int = Field(..., ge=0, le=100, description="稳定性（跳槽频率等）")
    potential: int = Field(..., ge=0, le=100, description="成长潜力")


# ============================================================================
# Out
# ============================================================================


class ScoreOut(BaseModel):
    """scores 行的对外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    candidate_id: uuid.UUID
    total: int
    skill: int | None
    experience: int | None
    education: int | None
    stability: int | None
    potential: int | None
    model_used: str | None = None
    llm_call_id: uuid.UUID | None = None


class ScoreListItem(BaseModel):
    """排名列表项（带候选人姓名 + 二级排序键）。"""

    id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_name: str | None
    total: int
    skill: int | None
    experience: int | None
    education: int | None
    stability: int | None
    potential: int | None
    model_used: str | None = None


class ScoreListResponse(BaseModel):
    items: list[ScoreListItem]
    total: int


# ============================================================================
# 请求
# ============================================================================


class ScoreRunRequest(BaseModel):
    """批量评分请求。"""

    job_id: uuid.UUID
    candidate_ids: list[uuid.UUID]


class ScoreRunResponse(BaseModel):
    """评分运行结果摘要。"""

    job_id: uuid.UUID
    processed: int
    failed: int


__all__ = [
    "ScoreDimensions",
    "ScoreOut",
    "ScoreListItem",
    "ScoreListResponse",
    "ScoreRunRequest",
    "ScoreRunResponse",
]
