"""SQLAlchemy 模型公共基类与 Timestamp mixin。"""
from __future__ import annotations

import uuid
from datetime import datetime

import sqlalchemy as sa
from sqlalchemy import DateTime, func
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


class UUIDPKMixin:
    """UUID 主键 mixin：所有表统一使用 UUID v4 作为 PK。"""

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """创建/更新时间戳 mixin。

    created_at 由 DB 默认值 ``now()`` 写入；
    updated_at 由应用层 ORM 的 ``onupdate=func.now()`` 触发，避免遗漏。
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
