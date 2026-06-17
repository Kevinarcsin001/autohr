"""SQLAlchemy 模型公共基类与 Timestamp mixin。"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, func
from sqlalchemy.dialects.postgresql import UUID
from sqlalchemy.orm import DeclarativeBase, Mapped, mapped_column

from app.core.db import Base


class UUIDPKMixin:
    """UUID 主键 mixin：所有表统一使用 UUID v4 作为 PK。"""

    id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """创建/更新时间戳 mixin。

    created_at 由 DB 默认值 ``now()`` 写入；
    updated_at 由 DB trigger 维护（迁移脚本中创建），避免应用层遗漏。
    """

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


class CreatedAtMixin:
    """仅 created_at（无 updated_at），适用于不可变记录。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


__all__ = ["Base", "UUIDPKMixin", "TimestampMixin", "CreatedAtMixin"]
