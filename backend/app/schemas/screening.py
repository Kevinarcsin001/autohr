"""FilterService 用的 Pydantic schema（任务 16）。

包含：
- ``ScreeningResultOut``：screening_results 行的对外表示
- ``ScreeningResultListItem``：列表项（带候选人姓名）
- ``OverrideRequest``：HR 改判请求
- ``ScreeningRunRequest``：批量筛选请求
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# Out
# ============================================================================


class ScreeningResultOut(BaseModel):
    """screening_results 行的对外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    candidate_id: uuid.UUID
    disqualified: bool
    reasons: list[str] | None = None
    manually_overridden: bool = False


class ScreeningResultListItem(BaseModel):
    """列表项（带候选人姓名）。"""

    id: uuid.UUID
    candidate_id: uuid.UUID
    candidate_name: str | None
    disqualified: bool
    reasons: list[str] | None = None
    manually_overridden: bool = False


class ScreeningResultListResponse(BaseModel):
    items: list[ScreeningResultListItem]
    total: int
    disqualified_count: int


# ============================================================================
# 请求
# ============================================================================


class ScreeningRunRequest(BaseModel):
    """批量筛选请求。

    - 不传 ``candidate_ids`` → 对该 job 下所有候选人跑
    - 传 ``candidate_ids`` → 仅对这些跑
    """

    job_id: uuid.UUID
    candidate_ids: list[uuid.UUID] | None = None


class ScreeningRunResponse(BaseModel):
    """筛选运行结果摘要。"""

    job_id: uuid.UUID
    processed: int
    disqualified: int
    passed: int


class OverrideRequest(BaseModel):
    """HR 改判请求。

    ``new_disqualified`` 是改判后的 disqualified 值；
    ``new_reasons`` 是改判后的理由（可选，不传则清空）。
    必须填 ``reason`` 说明改判原因。
    """

    new_disqualified: bool
    new_reasons: list[str] | None = Field(default=None, max_length=10)
    reason: str = Field(..., min_length=1, max_length=500)


class OverrideResponse(BaseModel):
    """改判响应。"""

    screening_result: ScreeningResultOut
    override_id: uuid.UUID


# ============================================================================
# Pipeline（任务 20）：异步触发 + SSE
# ============================================================================


class PipelineRunRequest(BaseModel):
    """异步编排请求：Filter → Scorer(+Reasoning) → Interview。"""

    job_id: uuid.UUID
    candidate_ids: list[uuid.UUID] = Field(..., min_length=1, max_length=500)


class PipelineRunResponse(BaseModel):
    """异步编排触发后立即返回（progress 通过 SSE 拿）。"""

    run_id: uuid.UUID
    job_id: uuid.UUID
    total: int


class PipelineSummary(BaseModel):
    """run 结束后的 summary（也作为 SSE ``done`` 事件 data）。"""

    total: int
    passed: int
    disqualified: int
    failed: int
    failed_reasons: list[dict[str, Any]] = Field(default_factory=list)


__all__ = [
    "ScreeningResultOut",
    "ScreeningResultListItem",
    "ScreeningResultListResponse",
    "ScreeningRunRequest",
    "ScreeningRunResponse",
    "OverrideRequest",
    "OverrideResponse",
    "PipelineRunRequest",
    "PipelineRunResponse",
    "PipelineSummary",
]
