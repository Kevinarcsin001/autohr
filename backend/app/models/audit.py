"""AuditLog 模型：所有敏感操作的审计流水。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, func
from sqlalchemy.orm import Mapped, mapped_column

from app.models._compat import GUID, INET_COMPAT, JSONB_COMPAT
from app.models.base import Base, UUIDPKMixin


class AuditLog(UUIDPKMixin, Base):
    """敏感操作审计（override / job.update / member.invite 等）。

    ``before`` / ``after`` JSONB 存操作前后快照；``ip`` INET 记录来源。
    """

    __tablename__ = "audit_logs"

    actor_id: Mapped[uuid.UUID | None] = mapped_column(
        GUID,
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
        index=True,
    )
    action: Mapped[str] = mapped_column(String, nullable=False, index=True)
    target_type: Mapped[str | None] = mapped_column(String, nullable=True, index=True)
    target_id: Mapped[uuid.UUID | None] = mapped_column(GUID, nullable=True)
    before: Mapped[dict[str, Any] | None] = mapped_column(JSONB_COMPAT, nullable=True)
    after: Mapped[dict[str, Any] | None] = mapped_column(JSONB_COMPAT, nullable=True)
    ip: Mapped[str | None] = mapped_column(INET_COMPAT, nullable=True)
    user_agent: Mapped[str | None] = mapped_column(String, nullable=True)
    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False, index=True
    )


__all__ = ["AuditLog"]
