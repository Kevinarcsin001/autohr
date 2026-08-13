"""Interview 聚合：AI 生成的面试问题 + 面试会话 + HR/面试官反馈。"""

from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models._compat import GUID
from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPKMixin
from app.models.types import InterviewDimension, InterviewSessionStatus


class InterviewSession(UUIDPKMixin, TimestampMixin, Base):
    """面试会话（一次面试的完整记录）。

    在 Pipeline 评分+面试题生成后自动创建（status=scheduled），
    HR 也可在候选人详情页手动发起。
    """

    __tablename__ = "interview_sessions"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    status: Mapped[str] = mapped_column(
        InterviewSessionStatus, default="scheduled", nullable=False, index=True
    )
    interviewer_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    overall_notes: Mapped[str | None] = mapped_column(Text, nullable=True)


class InterviewQuestion(UUIDPKMixin, CreatedAtMixin, Base):
    """面试题（按 batch_id 分批）：AI 生成 或 题库选题实例化。

    ``bank_question_id`` 标记来源：非空 → 来自题库（QuestionBankItem），
    空 → LLM 现场生成（generated_by 记模型名）。
    """

    __tablename__ = "interview_questions"

    session_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("interview_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    candidate_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    job_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    batch_id: Mapped[uuid.UUID] = mapped_column(GUID, nullable=False, index=True)
    dimension: Mapped[str] = mapped_column(InterviewDimension, nullable=False)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    generated_by: Mapped[str | None] = mapped_column(String, nullable=True)
    bank_question_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("question_bank_items.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


class InterviewFeedback(UUIDPKMixin, CreatedAtMixin, Base):
    """面试官/HR 对某题的反馈与评分。"""

    __tablename__ = "interview_feedbacks"

    session_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("interview_sessions.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    question_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("interview_questions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    reviewer_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )
    feedback: Mapped[str | None] = mapped_column(Text, nullable=True)
    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)


__all__ = ["InterviewSession", "InterviewQuestion", "InterviewFeedback"]
