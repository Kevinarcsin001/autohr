"""录用结果（效果回流）schema。

- ``OutcomeOut`` / ``OutcomeUpsertRequest``：结果录入与展示
- ``CalibrationReport``：评分 × 用人结果校准报告（评估报告 P1-5）
- ``FunnelStats``：招聘漏斗 + 渠道质量统计（评估报告 P1-4 最小闭环）
"""
from __future__ import annotations

import uuid
from datetime import datetime

from pydantic import BaseModel, ConfigDict, Field, computed_field

from app.models.outcome import FINAL_STATUS_VALUES

_FINAL_STATUS_PATTERN = r"^(hired|probation_passed|rejected|withdrawn)$"


# ============================================================================
# 结果录入
# ============================================================================


class OutcomeUpsertRequest(BaseModel):
    """HR 录入 / 更新最终用人结果。"""

    final_status: str = Field(pattern=_FINAL_STATUS_PATTERN)
    note: str | None = Field(default=None, max_length=2000)


class OutcomeOut(BaseModel):
    """结果行对外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    job_id: uuid.UUID
    candidate_id: uuid.UUID
    final_status: str
    note: str | None = None
    decided_at: datetime | None = None
    created_at: datetime | None = None


# ============================================================================
# 评分校准报告
# ============================================================================


class CalibrationBucket(BaseModel):
    """单个分数段的结果分布。"""

    score_min: int
    score_max: int
    total: int = 0
    hired: int = 0  # hired + probation_passed（正样本）
    rejected: int = 0  # rejected（负样本）
    other: int = 0  # withdrawn 等中性结果

    @computed_field  # type: ignore[prop-decorator]
    @property
    def hire_rate(self) -> float | None:
        """正样本命中率（分母 = 有明确结果的正负样本之和）。"""
        denom = self.hired + self.rejected
        if denom == 0:
            return None
        return round(self.hired / denom, 4)


class CalibrationReport(BaseModel):
    """评分 × 用人结果校准报告：分数越高 hire_rate 应单调递增。"""

    job_id: uuid.UUID | None = None
    buckets: list[CalibrationBucket]
    total_with_outcome: int = 0
    """已录入结果且完成评分的候选人数。"""


# ============================================================================
# 招聘漏斗
# ============================================================================


class ChannelQuality(BaseModel):
    """单渠道质量：来源 → 数量 → 通过率 → 录用数。"""

    source_type: str
    total: int = 0
    screened_pass: int = 0
    hired: int = 0

    @computed_field  # type: ignore[prop-decorator]
    @property
    def pass_rate(self) -> float | None:
        if self.total == 0:
            return None
        return round(self.screened_pass / self.total, 4)


class FunnelStats(BaseModel):
    """招聘漏斗统计（job 维度或 team 汇总）。"""

    job_id: uuid.UUID | None = None
    total_pool: int = 0  # 进入该 job 筛选池的候选人
    screened_pass: int = 0
    needs_review: int = 0
    disqualified: int = 0
    scored: int = 0
    interviewed: int = 0  # 存在面试会话
    hired: int = 0  # outcome ∈ {hired, probation_passed}
    channels: list[ChannelQuality] = Field(default_factory=list)


__all__ = [
    "OutcomeUpsertRequest",
    "OutcomeOut",
    "CalibrationBucket",
    "CalibrationReport",
    "ChannelQuality",
    "FunnelStats",
    "FINAL_STATUS_VALUES",
]
