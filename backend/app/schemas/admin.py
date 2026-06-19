"""Admin schema（任务 25）：LLM 配置 CRUD + 统计查询。

设计：
- ``LLMConfigOut``：路由策略输出
- ``LLMConfigUpsertRequest``：upsert 入参（同 team × scope 唯一）
- ``StatsSummary`` / ``StatsSeries``：按时间序列 + 维度聚合
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# LLM 配置
# ============================================================================


LLMScopeLiteral = Literal["extractor", "scorer", "reasoning", "interview"]


class LLMConfigOut(BaseModel):
    """llm_configs 行对外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID | None = None
    scope: str
    primary: str
    fallback: str | None = None
    model_overrides: dict[str, Any] | None = None
    timeout_seconds: int | None = None
    circuit_breaker_failures: int | None = None
    updated_at: datetime


class LLMConfigUpsertRequest(BaseModel):
    """upsert 入参：scope + primary + fallback。

    - 不传 team_id → 全局默认（admin 可写）
    - 同 (team_id, scope) 已存在 → 更新；否则插入
    """

    scope: LLMScopeLiteral
    primary: str = Field(..., min_length=1, max_length=64)
    fallback: str | None = Field(default=None, max_length=64)
    model_overrides: dict[str, str] | None = None
    timeout_seconds: int | None = Field(default=None, ge=1, le=600)
    circuit_breaker_failures: int | None = Field(default=None, ge=1, le=20)
    team_id: uuid.UUID | None = None


class LLMConfigUpsertResponse(BaseModel):
    """upsert 后响应（含新行 + 是否新建）。"""

    config: LLMConfigOut
    created: bool


class LLMConfigListResponse(BaseModel):
    """LLM 配置列表响应（含全局默认 + team 自有）。"""

    items: list[LLMConfigOut]


# ============================================================================
# 统计
# ============================================================================


StatsRange = Literal["7d", "30d"]


class StatsSummary(BaseModel):
    """统计概要（单次返回）。"""

    range: StatsRange
    total_calls: int
    success_count: int
    failed_count: int
    success_rate: float
    total_tokens_in: int
    total_tokens_out: int
    total_cost_cny: float
    p50_latency_ms: int | None
    p95_latency_ms: int | None
    p99_latency_ms: int | None


class StatsByDimension(BaseModel):
    """按维度（scope / adapter / model）分组。"""

    dimension: str
    items: list[dict[str, Any]]


class StatsTimePoint(BaseModel):
    """时间序列单点。"""

    timestamp: str
    total_calls: int
    success_count: int
    failed_count: int
    total_cost_cny: float


class StatsTimeSeries(BaseModel):
    """时间序列（按天 / 按小时聚合）。"""

    range: StatsRange
    granularity: Literal["hour", "day"]
    points: list[StatsTimePoint]


class StatsResponse(BaseModel):
    """统计综合响应。"""

    summary: StatsSummary
    by_scope: StatsByDimension
    by_adapter: StatsByDimension
    time_series: StatsTimeSeries


__all__ = [
    "LLMScopeLiteral",
    "LLMConfigOut",
    "LLMConfigUpsertRequest",
    "LLMConfigUpsertResponse",
    "LLMConfigListResponse",
    "StatsRange",
    "StatsSummary",
    "StatsByDimension",
    "StatsTimePoint",
    "StatsTimeSeries",
    "StatsResponse",
]
