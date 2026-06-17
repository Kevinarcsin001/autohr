"""LLMCall 模型：每次 LLM 调用的 token / 延迟 / 成本统计。"""
from __future__ import annotations

import uuid
from datetime import datetime
from decimal import Decimal

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, Numeric, String, Text, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin
from app.models.types import LLMScope


class LLMCall(UUIDPKMixin, Base):
    """LLM 调用记录（per call）。

    用于成本统计、性能分析与降级决策依据；可按 team / scope / adapter 维度聚合。
    """

    __tablename__ = "llm_calls"

    adapter: Mapped[str] = mapped_column(String, nullable=False, index=True)
    model: Mapped[str] = mapped_column(String, nullable=False)
    scope: Mapped[str] = mapped_column(LLMScope, nullable=False, index=True)
    tokens_in: Mapped[int | None] = mapped_column(Integer, nullable=True)
    tokens_out: Mapped[int | None] = mapped_column(Integer, nullable=True)
    latency_ms: Mapped[int | None] = mapped_column(Integer, nullable=True)
    cost_cny: Mapped[Decimal | None] = mapped_column(
        Numeric(10, 4), nullable=True
    )
    success: Mapped[bool] = mapped_column(Boolean, nullable=False, default=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    called_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )


__all__ = ["LLMCall"]
