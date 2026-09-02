"""SQLAlchemy 模型公共基类与 Timestamp mixin。"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import sqlalchemy as sa
from sqlalchemy import DateTime
from sqlalchemy.orm import Mapped, mapped_column

from app.core.db import Base


def _utcnow() -> datetime:
    """时间戳统一来源：Python 端 UTC 微秒精度。

    取代 server_default=now()：SQLite 的 CURRENT_TIMESTAMP 仅秒级，
    同秒插入的多行在 ORDER BY created_at 下顺序不确定（latest batch /
    feedback 排序类测试与真实业务的根因）；Python 端 default 双方言一致。
    """
    return datetime.now(timezone.utc)


class UUIDPKMixin:
    """UUID 主键 mixin：所有表统一使用 UUID v4 作为 PK。"""

    id: Mapped[uuid.UUID] = mapped_column(
        sa.Uuid,
        primary_key=True,
        default=uuid.uuid4,
    )


class TimestampMixin:
    """创建/更新时间戳 mixin（Python 端 UTC 微秒 default，见 _utcnow）。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        onupdate=_utcnow,
        nullable=False,
    )


class CreatedAtMixin:
    """仅 created_at（无 updated_at），适用于不可变记录。"""

    created_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        default=_utcnow,
        nullable=False,
    )


__all__ = ["Base", "UUIDPKMixin", "TimestampMixin", "CreatedAtMixin"]
