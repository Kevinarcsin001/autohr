"""AsyncJob 模型：异步任务断点续作表。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, UUIDPKMixin
from app.models.types import AsyncJobStatus, AsyncJobType


class AsyncJob(UUIDPKMixin, Base):
    """异步任务记录（断点续作 + 幂等键 + 重试计数）。

    ``idempotency_key`` UNIQUE：相同请求只执行一次（如同一份简历重复上传）；
    ``attempts`` 记录已尝试次数，配合 Celery 重试与 ``status='retry'``。
    """

    __tablename__ = "async_jobs"

    task_type: Mapped[str] = mapped_column(AsyncJobType, nullable=False, index=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True), nullable=True, index=True
    )
    status: Mapped[str] = mapped_column(
        AsyncJobStatus, default="queued", nullable=False, index=True
    )
    attempts: Mapped[int] = mapped_column(Integer, default=0, nullable=False)
    idempotency_key: Mapped[str | None] = mapped_column(
        String, unique=True, nullable=True, index=True
    )
    payload: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    error: Mapped[str | None] = mapped_column(Text, nullable=True)
    queued_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    started_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    finished_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["AsyncJob"]
