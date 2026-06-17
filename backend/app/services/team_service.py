"""团队管理服务。

业务规则（任务 6 / 需求 1.3）：
- ``list_members``：返回当前 team 全部成员（按 created_at 升序）
- ``update_member_role``：admin only；不能修改自己的角色（防失控）
- ``remove_member``：admin only；不能移除自己；目标成员 team_id 置空
- ``invite_member``：复用 auth_service.invite_member（避免重复实现）

事务策略：service 接收 session，不自行 commit。
"""
from __future__ import annotations

from uuid import UUID

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.middleware.error_handler import (
    ConflictError,
    ForbiddenError,
    NotFoundError,
    ValidationError,
)
from app.models.team import Team
from app.models.user import User
from app.services import auth_service


# ============================================================================
# 查询
# ============================================================================


async def get_team_or_404(db: AsyncSession, team_id: UUID) -> Team:
    """获取团队，不存在抛 NotFoundError。"""
    result = await db.execute(select(Team).where(Team.id == team_id))
    team = result.scalar_one_or_none()
    if team is None:
        raise NotFoundError("团队不存在", resource="team", team_id=str(team_id))
    return team


async def list_members(db: AsyncSession, *, team_id: UUID) -> list[User]:
    """列出 team 全部成员（按 created_at 升序）。"""
    result = await db.execute(
        select(User).where(User.team_id == team_id).order_by(User.created_at.asc())
    )
    return list(result.scalars().all())


async def get_member_or_404(
    db: AsyncSession, *, team_id: UUID, user_id: UUID
) -> User:
    """获取 team 内指定成员；不存在或不在本 team 抛 NotFoundError。"""
    result = await db.execute(
        select(User).where(User.id == user_id, User.team_id == team_id)
    )
    user = result.scalar_one_or_none()
    if user is None:
        raise NotFoundError(
            "成员不存在或不属于当前团队",
            resource="user",
            user_id=str(user_id),
        )
    return user


# ============================================================================
# 修改角色
# ============================================================================


async def update_member_role(
    db: AsyncSession,
    *,
    team_id: UUID,
    target_user_id: UUID,
    new_role: str,
    actor_user_id: UUID,
) -> User:
    """admin 修改成员角色。

    Raises:
        ForbiddenError: actor 试图修改自己的角色（防失控，可能造成无人 admin）
        ValidationError: 把最后一位 admin 降级（同上）
        NotFoundError: target 不在当前 team
    """
    target = await get_member_or_404(db, team_id=team_id, user_id=target_user_id)

    # 防失控 1：不能改自己
    if target_user_id == actor_user_id:
        raise ForbiddenError("不能修改自己的角色", actor=str(actor_user_id))

    # 防失控 2：若把最后一位 admin 降级则禁止
    if new_role != "admin" and target.role == "admin":
        members = await list_members(db, team_id=team_id)
        admin_count = sum(1 for m in members if m.role == "admin")
        if admin_count <= 1:
            raise ValidationError(
                "团队必须保留至少一位 admin，无法降级最后一位管理员",
                team_id=str(team_id),
            )

    target.role = new_role
    await db.flush()
    return target


# ============================================================================
# 移除成员
# ============================================================================


async def remove_member(
    db: AsyncSession,
    *,
    team_id: UUID,
    target_user_id: UUID,
    actor_user_id: UUID,
) -> None:
    """admin 移除成员（不删 user 记录，只解绑 team_id）。

    Raises:
        ForbiddenError: actor 试图移除自己（请用「离开团队」/登出，路径不同）
        ValidationError: 移除最后一位 admin
        NotFoundError: target 不在当前 team
    """
    target = await get_member_or_404(db, team_id=team_id, user_id=target_user_id)

    if target_user_id == actor_user_id:
        raise ForbiddenError("不能移除自己；如需离开请使用登出", actor=str(actor_user_id))

    # 不能移除最后一位 admin
    if target.role == "admin":
        members = await list_members(db, team_id=team_id)
        admin_count = sum(1 for m in members if m.role == "admin")
        if admin_count <= 1:
            raise ValidationError(
                "团队必须保留至少一位 admin，无法移除最后一位管理员",
                team_id=str(team_id),
            )

    target.team_id = None
    target.role = "member"  # 解绑后默认降为 member，避免游离 admin
    await db.flush()


# ============================================================================
# 邀请（复用 auth_service）
# ============================================================================


async def invite_team_member(
    db: AsyncSession,
    *,
    team_id: UUID,
    email: str,
    role: str,
    name: str,
    invited_by: UUID,
):
    """转发到 auth_service.invite_member，保持单一实现。"""
    return await auth_service.invite_member(
        db,
        team_id=team_id,
        email=email,
        role=role,
        name=name,
        invited_by=invited_by,
    )


__all__ = [
    "get_team_or_404",
    "list_members",
    "get_member_or_404",
    "update_member_role",
    "remove_member",
    "invite_team_member",
]
