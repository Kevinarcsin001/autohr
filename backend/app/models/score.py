"""Score 聚合：评分 + 子维度 + 推荐理由。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    Boolean,
    DateTime,
    ForeignKey,
    Integer,
    String,
    Text,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import ARRAY, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.types import ScoreReasonType


class Score(UUIDPKMixin, CreatedAtMixin, Base):
    """评分（per job × candidate）。

    子维度：skill / experience / education / stability / potential，各 0-100；
    total 由 LLM 给出或加权计算。
    """

    __tablename__ = "scores"
    __table_args__ = (
        UniqueConstraint("job_id", "candidate_id", name="uq_score_job_candidate"),
    )

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    total: Mapped[int] = mapped_column(Integer, nullable=False)
    skill: Mapped[int | None] = mapped_column(Integer, nullable=True)
    experience: Mapped[int | None] = mapped_column(Integer, nullable=True)
    education: Mapped[int | None] = mapped_column(Integer, nullable=True)
    stability: Mapped[int | None] = mapped_column(Integer, nullable=True)
    potential: Mapped[int | None] = mapped_column(Integer, nullable=True)
    model_used: Mapped[str | None] = mapped_column(String, nullable=True)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_calls.id", ondelete="SET NULL"),
        nullable=True,
    )


class ScoreReason(UUIDPKMixin, CreatedAtMixin, Base):
    """推荐/淘汰理由（与 score 1:N）。"""

    __tablename__ = "score_reasons"

    score_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("scores.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    type: Mapped[str] = mapped_column(ScoreReasonType, nullable=False)
    bullet_points: Mapped[list[str] | None] = mapped_column(ARRAY(String), nullable=True)
    validated: Mapped[bool | None] = mapped_column(Boolean, nullable=True)


__all__ = ["Score", "ScoreReason"]
