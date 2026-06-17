"""User 模型：账户 + 角色 + 团队归属。"""
from __future__ import annotations

import uuid

from sqlalchemy import ForeignKey, Index
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.types import UserRole


class User(UUIDPKMixin, CreatedAtMixin, Base):
    """登录用户。

    - email 走 CITEXT（大小写不敏感）；迁移中建扩展 + UNIQUE 索引
    - password_hash 存 bcrypt 哈希（不放 EncryptedString，bcrypt 已是单向）
    - role 区分 admin / member，决定是否可邀请/移除成员
    - team_id 多团队场景下表示当前默认团队（可空，独立开发账号）
    """

    __tablename__ = "users"
    __table_args__ = (
        # 大小写不敏感的唯一约束（CITEXT 本身就大小写不敏感，但显式 unique 索引更清晰）
        Index("uq_users_email_lower", "email", unique=True),
    )

    email: Mapped[str] = mapped_column(CITEXT, nullable=False, index=True)
    password_hash: Mapped[str]
    name: Mapped[str]
    role: Mapped[str] = mapped_column(UserRole, default="member", nullable=False)
    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="SET NULL"),
        nullable=True,
    )

    def __repr__(self) -> str:
        return f"<User {self.id} {self.email!r} role={self.role}>"


__all__ = ["User"]
