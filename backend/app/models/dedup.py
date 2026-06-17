"""DedupMatch 模型：疑似同人待人工合并。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.types import DedupMatchStatus


class DedupMatch(UUIDPKMixin, CreatedAtMixin, Base):
    """疑似同人待人工合并。

    ``candidate_a`` / ``candidate_b`` 表示两条候选人记录，
    ``similarity`` JSONB 存命中的字段与分数（如 phone_match=1.0, name_sim=0.85）。
    """

    __tablename__ = "dedup_matches"

    candidate_a: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    candidate_b: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    similarity: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    status: Mapped[str] = mapped_column(
        DedupMatchStatus, default="pending", nullable=False, index=True
    )
    decided_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )


__all__ = ["DedupMatch"]
