"""TeamInvite 模型：团队成员邀请（一次性 token）。

- 由 admin 发起：``invite_member(team_id, email, role)``
- 被邀请人通过邮件链接 ``/auth/accept-invite?token=xxx`` 完成注册
- token 一次性：accept 后 status=accepted，再次使用拒绝
- token 48h 过期；admin 可重新发起（旧记录 status=revoked）
"""
from __future__ import annotations

import uuid
from datetime import datetime

from sqlalchemy import DateTime, ForeignKey, String, UniqueConstraint
from sqlalchemy.dialects.postgresql import CITEXT, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.types import UserRole


class TeamInvite(UUIDPKMixin, CreatedAtMixin, Base):
    """团队成员邀请记录。

    ``UNIQUE(team_id, email, status='pending')`` 通过部分唯一索引在迁移中实现；
    ORM 层用 ``UniqueConstraint`` 仅作元数据声明，真实约束走迁移。
    """

    __tablename__ = "team_invites"
    __table_args__ = (
        UniqueConstraint("team_id", "email", "status", name="uq_team_invite_active"),
    )

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    email: Mapped[str] = mapped_column(CITEXT, nullable=False, index=True)
    name: Mapped[str] = mapped_column(String, default="", nullable=False)
    role: Mapped[str] = mapped_column(UserRole, default="member", nullable=False)
    invite_token: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, index=True
    )
    status: Mapped[str] = mapped_column(
        String, default="pending", nullable=False, index=True
    )  # pending | accepted | revoked
    invited_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )
    accepted_by: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=True,
    )
    expires_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), nullable=False, index=True
    )
    accepted_at: Mapped[datetime | None] = mapped_column(
        DateTime(timezone=True), nullable=True
    )


__all__ = ["TeamInvite"]
