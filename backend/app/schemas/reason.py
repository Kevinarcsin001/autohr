"""ReasoningService 用的 Pydantic schema（任务 18）。

包含：
- ``RecommendReasons``：LLM 输出的推荐理由（3-5 条要点）
- ``DisqualifyReasons``：硬性淘汰理由（指向被违反条件）
- ``ReasonOut`` / ``ReasonListResponse``：score_reasons 行对外表示
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator


# ============================================================================
# LLM 输出 schema（response_schema 用）
# ============================================================================


class RecommendReasons(BaseModel):
    """LLM 输出的推荐理由 schema。

    约束（任务 18 Restrictions）：
    - 必须是 3-5 条要点
    - 每条理由必须能在简历原文中找到事实支持（service 层做事实校验）
    - 每条理由可附 "evidence" 字段：HR 阅读时跳转的关键词
    """

    model_config = ConfigDict(extra="forbid")

    bullet_points: list[str] = Field(
        ...,
        min_length=3,
        max_length=5,
        description="3-5 条推荐理由（每条 1-2 句）",
    )
    evidence: list[str] = Field(
        default_factory=list,
        description="每条理由对应的关键词（用于事实校验时在原文匹配）",
    )

    @field_validator("bullet_points")
    @classmethod
    def _each_non_empty(cls, v: list[str]) -> list[str]:
        cleaned = [b.strip() for b in v if b and b.strip()]
        if not (3 <= len(cleaned) <= 5):
            raise ValueError(f"bullet_points must have 3-5 items, got {len(cleaned)}")
        return cleaned


class DisqualifyReasons(BaseModel):
    """LLM 输出的淘汰理由 schema。

    约束：
    - 必须明确指向被违反的硬性条件
    - 格式："<规则名>: <候选人值> vs <要求>" 或类似显式对比
    """

    model_config = ConfigDict(extra="forbid")

    bullet_points: list[str] = Field(
        ...,
        min_length=1,
        max_length=5,
        description="1-5 条淘汰理由（每条必须指向某硬性条件）",
    )

    @field_validator("bullet_points")
    @classmethod
    def _each_non_empty(cls, v: list[str]) -> list[str]:
        cleaned = [b.strip() for b in v if b and b.strip()]
        if not cleaned:
            raise ValueError("bullet_points cannot be empty")
        return cleaned


# ============================================================================
# Out
# ============================================================================


class ReasonOut(BaseModel):
    """score_reasons 行的对外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    score_id: uuid.UUID
    type: Literal["recommend", "disqualify"]
    bullet_points: list[str] | None
    validated: bool | None


class ReasonListResponse(BaseModel):
    items: list[ReasonOut]
    total: int


__all__ = [
    "RecommendReasons",
    "DisqualifyReasons",
    "ReasonOut",
    "ReasonListResponse",
]
