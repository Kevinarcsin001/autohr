"""EmailConfig 模型：每团队 IMAP 抓取配置。"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import Boolean, DateTime, ForeignKey, Integer, String
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, TimestampMixin, UUIDPKMixin
from app.models.types import EncryptedString


class EmailConfig(UUIDPKMixin, TimestampMixin, Base):
    """团队级 IMAP 邮箱配置（用于自动抓取简历附件）。

    ``password_enc`` 走 Fernet 加密；``last_fetched_at`` 用于增量抓取；
    ``enabled`` 控制是否被 Beat 调度。

    退避状态（任务 11）：
    - ``consecutive_failures``：连续失败次数，成功后重置 0
    - ``paused_until``：暂停轮询直到何时；5 次全失败后置 ≥30 min
    - ``last_error_summary``：错误简述（不记邮件正文 / 密码）
    - ``alert_level``：none / warning / critical（前端展示）
    """

    __tablename__ = "email_configs"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    imap_host: Mapped[str] = mapped_column(String, nullable=False)
    imap_port: Mapped[int] = mapped_column(Integer, nullable=False, default=993)
    username: Mapped[str] = mapped_column(String, nullable=False)
    password_enc: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    poll_interval_min: Mapped[int] = mapped_column(Integer, default=15, nullable=False)
    last_fetched_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    enabled: Mapped[bool] = mapped_column(Boolean, default=True, nullable=False)

    # 退避状态（任务 11）
    consecutive_failures: Mapped[int] = mapped_column(
        Integer, default=0, nullable=False, server_default="0"
    )
    paused_until: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )
    last_error_summary: Mapped[str | None] = mapped_column(String, nullable=True)
    alert_level: Mapped[str] = mapped_column(
        String, default="none", nullable=False, server_default="none"
    )


__all__ = ["EmailConfig"]
