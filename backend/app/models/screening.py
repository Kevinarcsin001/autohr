"""Screening 聚合：硬性筛选结果 + HR 改判记录。"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import (
    Boolean,
    ForeignKey,
    Text,
    UniqueConstraint,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin


class ScreeningResult(UUIDPKMixin, CreatedAtMixin, Base):
    """硬性筛选结果（per job × candidate）。

    ``UNIQUE(job_id, candidate_id)`` 保证同一对只有一条最新结果。
    """

    __tablename__ = "screening_results"
    __table_args__ = (
        UniqueConstraint(
            "job_id", "candidate_id", name="uq_screening_job_candidate"
        ),
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
    disqualified: Mapped[bool] = mapped_column(Boolean, nullable=False, default=False)
    reasons: Mapped[list[str] | None] = mapped_column(JSONB, nullable=True)
    manually_overridden: Mapped[bool] = mapped_column(
        Boolean, nullable=False, default=False
    )


class ManualOverride(UUIDPKMixin, CreatedAtMixin, Base):
    """HR 改判记录（审计用，配合 screening_results.manually_overridden）。"""

    __tablename__ = "manual_overrides"

    screening_result_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("screening_results.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    actor_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )
    old_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    new_value: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    reason: Mapped[str | None] = mapped_column(Text, nullable=True)


__all__ = ["ScreeningResult", "ManualOverride"]
