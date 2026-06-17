"""团队管理 API 路由（任务 6）。

端点（base: /api/teams）：
- GET    /me                 获取当前用户的团队详情（含成员列表）
- GET    /{team_id}/members  列出 team 成员（admin / member 都可看）
- POST   /{team_id}/invite   admin 发起邀请（转发到 auth_service）
- PATCH  /{team_id}/members/{user_id}/role   admin 修改角色
- DELETE /{team_id}/members/{user_id}        admin 移除成员

权限：
- admin only：invite / update_role / remove
- 所有成员：list / detail
- 防失控：不能改自己 / 移除自己；不能降级/移除最后一位 admin
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.core.deps import AdminUser, CurrentUser, DbSession
from app.core.middleware.error_handler import ForbiddenError
from app.models.invite import TeamInvite
from app.models.team import Team
from app.schemas.auth import InviteOut
from app.schemas.team import (
    CreateInviteRequest,
    TeamDetailOut,
    TeamMemberOut,
    TeamOut,
    UpdateRoleRequest,
)
from app.services import team_service

router = APIRouter(prefix="/teams", tags=["teams"])


# ============================================================================
# 内部工具
# ============================================================================


async def _assert_team_belongs_to_user(
    db: DbSession, team_id: UUID, user
) -> Team:
    """确保 team 属于当前用户（普通成员查看自己 team 也走此校验）。"""
    if user.team_id is None or UUID(str(user.team_id)) != team_id:
        raise ForbiddenError(
            "无权访问该团队",
            team_id=str(team_id),
            user_team_id=str(user.team_id) if user.team_id else None,
        )
    return await team_service.get_team_or_404(db, team_id)


# ============================================================================
# 端点
# ============================================================================


@router.get("/me", response_model=TeamDetailOut)
async def get_my_team(
    user: CurrentUser,
    db: DbSession,
) -> TeamDetailOut:
    """当前用户所在团队的详情（含成员列表）。"""
    if user.team_id is None:
        raise ForbiddenError("当前用户未加入任何团队")
    team_id = UUID(str(user.team_id))
    team = await team_service.get_team_or_404(db, team_id)
    members = await team_service.list_members(db, team_id=team_id)
    return TeamDetailOut(
        team=TeamOut(id=str(team.id), name=team.name),
        members=[
            TeamMemberOut(
                id=str(m.id),
                email=m.email,
                name=m.name,
                role=m.role,
                created_at=m.created_at.isoformat(),
            )
            for m in members
        ],
    )


@router.get("/{team_id}/members", response_model=list[TeamMemberOut])
async def list_members(
    team_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> list[TeamMemberOut]:
    """列出指定 team 的成员（仅本 team 成员可访问）。"""
    await _assert_team_belongs_to_user(db, team_id, user)
    members = await team_service.list_members(db, team_id=team_id)
    return [
        TeamMemberOut(
            id=str(m.id),
            email=m.email,
            name=m.name,
            role=m.role,
            created_at=m.created_at.isoformat(),
        )
        for m in members
    ]


@router.post(
    "/{team_id}/invite",
    response_model=InviteOut,
    status_code=status.HTTP_201_CREATED,
)
async def invite_member(
    team_id: UUID,
    payload: CreateInviteRequest,
    admin: AdminUser,
    db: DbSession,
) -> InviteOut:
    """admin 发起团队邀请。"""
    # AdminUser 依赖已经保证 actor.role == 'admin'，但仍需校验 team 归属
    if admin.team_id is None or UUID(str(admin.team_id)) != team_id:
        raise ForbiddenError("无权在他人的团队中邀请成员")
    invite = await team_service.invite_team_member(
        db,
        team_id=team_id,
        email=payload.email,
        role=payload.role,
        name=payload.name,
        invited_by=admin.id,
    )
    return InviteOut(
        id=str(invite.id),
        email=invite.email,
        role=invite.role,
        invite_token=invite.invite_token,
        expires_at=invite.expires_at.isoformat(),
    )


@router.patch(
    "/{team_id}/members/{user_id}/role",
    response_model=TeamMemberOut,
)
async def update_member_role(
    team_id: UUID,
    user_id: UUID,
    payload: UpdateRoleRequest,
    admin: AdminUser,
    db: DbSession,
) -> TeamMemberOut:
    """admin 修改成员角色。

    防失控：
    - 不能修改自己的角色
    - 不能降级最后一位 admin
    """
    if admin.team_id is None or UUID(str(admin.team_id)) != team_id:
        raise ForbiddenError("无权在他人的团队中修改成员")

    target = await team_service.update_member_role(
        db,
        team_id=team_id,
        target_user_id=user_id,
        new_role=payload.role,
        actor_user_id=admin.id,
    )
    return TeamMemberOut(
        id=str(target.id),
        email=target.email,
        name=target.name,
        role=target.role,
        created_at=target.created_at.isoformat(),
    )


@router.delete(
    "/{team_id}/members/{user_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def remove_member(
    team_id: UUID,
    user_id: UUID,
    admin: AdminUser,
    db: DbSession,
) -> None:
    """admin 移除成员（解绑 team_id，不删账户）。"""
    if admin.team_id is None or UUID(str(admin.team_id)) != team_id:
        raise ForbiddenError("无权在他人的团队中移除成员")

    await team_service.remove_member(
        db,
        team_id=team_id,
        target_user_id=user_id,
        actor_user_id=admin.id,
    )


# ============================================================================
# 邀请列表（admin 看 team 所有邀请）
# ============================================================================


@router.get("/{team_id}/invites", response_model=list[InviteOut])
async def list_team_invites(
    team_id: UUID,
    admin: AdminUser,
    db: DbSession,
) -> list[InviteOut]:
    """列出 team 的全部邀请记录（admin only）。"""
    if admin.team_id is None or UUID(str(admin.team_id)) != team_id:
        raise ForbiddenError("无权查看他人团队的邀请")
    result = await db.execute(
        select(TeamInvite)
        .where(TeamInvite.team_id == team_id)
        .order_by(TeamInvite.created_at.desc())
    )
    invites = result.scalars().all()
    return [
        InviteOut(
            id=str(inv.id),
            email=inv.email,
            role=inv.role,
            invite_token=inv.invite_token if inv.status == "pending" else "",
            expires_at=inv.expires_at.isoformat(),
        )
        for inv in invites
    ]


__all__ = ["router"]
