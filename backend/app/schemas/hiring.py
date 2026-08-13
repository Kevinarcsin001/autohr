"""HiringSchema：AI 录用建议的请求/响应 schema。"""

from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

HiringRecommendationLiteral = Literal["hire", "reserve", "reject"]


class GenerateRecommendationRequest(BaseModel):
    """生成录用建议请求（当前仅依赖 session 内数据，无额外参数）。"""

    pass


class HiringRecommendationOut(BaseModel):
    """录用建议的对外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    session_id: uuid.UUID
    recommendation: HiringRecommendationLiteral
    reasons: list[str] | None = None
    risks: list[str] | None = None
    probation_focus: list[str] | None = None
    generated_by: str | None = None
    created_at: str | None = None


class GenerateRecommendationResponse(BaseModel):
    """生成录用建议后的响应。"""

    recommendation: HiringRecommendationOut


# LLM 输出 schema（response_schema 用）


class HiringDecisionOutput(BaseModel):
    """LLM 应输出的结构化录用建议。

    字段约束：
    - recommendation: hire / reserve / reject
    - reasons: 3-5 条核心理由
    - risks: 1-3 条潜在风险
    - probation_focus: 2-4 条试用期关注点
    """

    model_config = ConfigDict(extra="forbid")

    recommendation: Literal["hire", "reserve", "reject"]
    reasons: list[str] = Field(..., min_length=3, max_length=5)
    risks: list[str] = Field(..., min_length=1, max_length=5)
    probation_focus: list[str] = Field(..., min_length=1, max_length=5)


__all__ = [
    "HiringRecommendationLiteral",
    "GenerateRecommendationRequest",
    "HiringRecommendationOut",
    "GenerateRecommendationResponse",
    "HiringDecisionOutput",
]
