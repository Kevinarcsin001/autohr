"""Interview 聚合：AI 生成的面试问题 + 面试会话 + HR/面试官反馈 + 渐进式回合。"""

from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text
from sqlalchemy.orm import Mapped, mapped_column

from app.models._compat import GUID, JSONB_COMPAT
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

    mode: Mapped[str] = mapped_column(
        String(16), default="batch", server_default="batch", nullable=False
    )
    """``batch``（一次性出卷，默认） | ``adaptive``（渐进式逐题推进）。"""

    adaptive_plan: Mapped[dict | None] = mapped_column(JSONB_COMPAT, nullable=True)
    """adaptive 启动时快照：匹配信号 + 分支计划（排序/亲和度），供前端展示与可解释性。"""

    recording_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    """M2b 会后回捞：整场会议录制文件（MinIO key）。"""
    recording_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """uploaded / processing / done / failed。"""


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


class InterviewTurn(UUIDPKMixin, CreatedAtMixin, Base):
    """渐进式面试的问答回合（账本：每题一行，全部决策与证据可回溯）。

    M1：题目来自题库（question_item_id），回答手输；
    M2：audio_storage_key + transcription_status 支撑音频链路；
    M3：rating_evidence 内可携带能力估计快照（ability_before/after）。
    """

    __tablename__ = "interview_turns"

    session_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("interview_sessions.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    seq: Mapped[int] = mapped_column(Integer, nullable=False)
    """回合序号（同 session 内递增）。"""

    question_item_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("question_bank_items.id", ondelete="SET NULL"),
        nullable=True,
    )
    question_text: Mapped[str] = mapped_column(Text, nullable=False)
    dimension: Mapped[str | None] = mapped_column(InterviewDimension, nullable=True)

    category_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("question_categories.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    category_name: Mapped[str | None] = mapped_column(String(64), nullable=True)
    """分支名冗余（分类删除后仍可展示）。"""

    answer_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    audio_storage_key: Mapped[str | None] = mapped_column(String(512), nullable=True)
    audio_start_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """M2b：本题在整场录制中的起始毫秒（面试官标注）；区间终点=下一题起点。"""
    transcription_status: Mapped[str | None] = mapped_column(String(16), nullable=True)
    """pending / processing / done / failed；M1 手输回答为 None。"""

    answered_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )

    rating: Mapped[int | None] = mapped_column(Integer, nullable=True)
    """LLM 自动评分 1-5；与 reference_answer 对照得出。"""
    rating_evidence: Mapped[dict | None] = mapped_column(JSONB_COMPAT, nullable=True)
    """{key_points_hit, key_points_missed, strengths, flaws, follow_up_suggestion, error?}"""
    rating_model: Mapped[str | None] = mapped_column(String(64), nullable=True)

    next_decision: Mapped[dict | None] = mapped_column(JSONB_COMPAT, nullable=True)
    """{action: deepen/retry/switch/complete, reason, next_category_id?, difficulty?}"""


__all__ = [
    "InterviewSession",
    "InterviewQuestion",
    "InterviewFeedback",
    "InterviewTurn",
]
