"""CandidateJobOutcome：候选人 × 职位的最终用人结果（效果回流闭环）。

背景（评估报告 P1-5）：系统此前不知道 85 分的候选人最终有没有入职、
试用期是否通过——评分模型永远无法校准。本表承接 HR 人工录入的
最终结果，支撑评分校准报告与招聘漏斗统计。

设计要点（SRP）：
- 与 ``hiring_recommendations``（AI 建议）语义分离：本表是**人工认定**的
  实际结果，是地面真相（ground truth）
- per (job_id, candidate_id) 唯一：同一人在不同职位的结果各自独立
- ``final_status`` 用 String + 应用层 Literal 校验（双方言兼容，避免
  PG enum 迁移负担）
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from sqlalchemy import DateTime, ForeignKey, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models._compat import GUID
from app.models.base import Base, CreatedAtMixin, UUIDPKMixin

FinalStatus = Literal[
    "hired",  # 已录用入职
    "probation_passed",  # 试用期通过（转正）——效果回流最强信号
    "rejected",  # 最终未录用（含面试后淘汰）
    "withdrawn",  # 候选人放弃
]
"""最终结果枚举；未录入 = 无行（查询侧视作 pending）。"""

FINAL_STATUS_VALUES: tuple[str, ...] = (
    "hired",
    "probation_passed",
    "rejected",
    "withdrawn",
)
"""合法值集合（API 层校验用）。"""


class CandidateJobOutcome(UUIDPKMixin, CreatedAtMixin, Base):
    """候选人 × 职位的最终用人结果（HR 人工录入，ground truth）。"""

    __tablename__ = "candidate_job_outcomes"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "candidate_id", name="uq_outcome_job_candidate"
        ),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    final_status: Mapped[str] = mapped_column(String(32), nullable=False)
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    decided_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    note: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["CandidateJobOutcome", "FinalStatus", "FINAL_STATUS_VALUES"]
