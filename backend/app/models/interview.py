"""Interview 聚合：AI 生成的面试问题 + HR/面试官反馈。"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.types import InterviewDimension


class InterviewQuestion(UUIDPKMixin, CreatedAtMixin, Base):
    """AI 生成的面试题（按 batch_id 分批）。"""

    __tablename__ = "interview_questions"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(UUID(as_uuid=True), nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(InterviewDimension, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generated_by: Mapped[str | None] = mapped_column(String, nullable=True)


class InterviewFeedback(UUIDPKMixin, CreatedAtMixin, Base):
    """面试官/HR 对某题的反馈与评分。"""

    __tablename__ = "interview_feedbacks"

    question_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("interview_questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)


__all__ = ["InterviewQuestion", "InterviewFeedback"]
