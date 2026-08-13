"""Hiring 聚合：AI 生成的录用建议。"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, String
from sqlalchemy.orm import Mapped, mapped_column

from app.models._compat import GUID, JSONB_COMPAT
from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.types import HiringRecommendationEnum


class HiringRecommendation(UUIDPKMixin, CreatedAtMixin, Base):
    """AI 录用建议（per interview_session，唯一）。

    基于简历 + JD + 面试反馈生成，包含建议/理由/风险/试用期关注点。
    """

    __tablename__ = "hiring_recommendations"

    session_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
        index=True,
    )
    recommendation: Mapped[str] = mapped_column(HiringRecommendationEnum, nullable=False)
    reasons: Mapped[list[str] | None] = mapped_column(JSONB_COMPAT, nullable=True)
    risks: Mapped[list[str] | None] = mapped_column(JSONB_COMPAT, nullable=True)
    probation_focus: Mapped[list[str] | None] = mapped_column(JSONB_COMPAT, nullable=True)
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("llm_calls.id", ondelete="SET NULL"),
        nullable=True,
    )
    generated_by: Mapped[str | None] = mapped_column(String, nullable=True)


__all__ = ["HiringRecommendation"]
