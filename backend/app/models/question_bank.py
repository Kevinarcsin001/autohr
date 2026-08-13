"""面试题库：可配置分类 + 带分值的预设题目，用于按分类配额凑 100 分组卷。

与 LLM 现场生成的 InterviewQuestion（绑定 candidate+job+batch）正交：
- 题库题是团队级的稳定资产，admin 维护
- InterviewQuestion.bank_question_id 反向标记题目来源（题库 vs AI 生成）
"""
from __future__ import annotations

import uuid

from sqlalchemy import Boolean, ForeignKey, Integer, String, Text, UniqueConstraint
from sqlalchemy.orm import Mapped, mapped_column

from app.models._compat import GUID, JSONB_COMPAT
from app.models.base import Base, TimestampMixin, UUIDPKMixin
from app.models.types import InterviewDimension


class QuestionCategory(UUIDPKMixin, TimestampMixin, Base):
    """题库分类（可配置标签，admin 可增删）。

    典型分类：基础 / RAG / agent / 模型微调。``target_points`` 为组卷默认配额
    （各分类配额相加建议 = 100），assemble 请求可临时 override。
    team 级隔离：每个团队维护自己的分类体系。
    """

    __tablename__ = "question_categories"
    __table_args__ = (
        UniqueConstraint("team_id", "slug", name="uq_question_category_team_slug"),
    )

    team_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    name: Mapped[str] = mapped_column(String(64), nullable=False)
    slug: Mapped[str] = mapped_column(String(64), nullable=False)
    target_points: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    sort_order: Mapped[int] = mapped_column(Integer, nullable=False, default=0)
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


class QuestionBankItem(UUIDPKMixin, TimestampMixin, Base):
    """题库题目（带分值，用于凑 100 分组卷）。

    ``points`` 是单题分值（如 10/15/20/30）；assemble 在分类内做子集和凑到目标分。
    ``dimension`` 可空：题库题可不绑面试维度；``category`` 才是技术领域分类。
    """

    __tablename__ = "question_bank_items"

    team_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    category_id: Mapped[uuid.UUID] = mapped_column(
        GUID,
        ForeignKey("question_categories.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dimension: Mapped[str | None] = mapped_column(InterviewDimension, nullable=True)
    question: Mapped[str] = mapped_column(Text, nullable=False)
    points: Mapped[int] = mapped_column(Integer, nullable=False, default=10)
    difficulty: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tags: Mapped[list[str] | None] = mapped_column(JSONB_COMPAT, nullable=True)
    reference_answer: Mapped[str | None] = mapped_column(Text, nullable=True)
    created_by: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    is_active: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)


__all__ = ["QuestionCategory", "QuestionBankItem"]
