"""团队管理相关 Pydantic schema。

约束：
- 角色仅 admin / member（与 UserRole ENUM 一致）
- 不能修改自己的角色（防失控）：API 层校验
"""
from __future__ import annotations

from pydantic import BaseModel, ConfigDict, EmailStr, Field

from app.schemas.auth import InviteOut


class TeamOut(BaseModel):
    """团队信息。"""

    id: str
    name: str

    model_config = ConfigDict(from_attributes=True)


class TeamMemberOut(BaseModel):
    """团队成员条目。"""

    id: str
    email: str
    name: str
    role: str
    created_at: str  # ISO8601

    model_config = ConfigDict(from_attributes=True)


class UpdateRoleRequest(BaseModel):
    """修改成员角色。"""

    role: str = Field(pattern="^(admin|member)$")


class TeamDetailOut(BaseModel):
    """团队详情（含成员列表）。"""

    team: TeamOut
    members: list[TeamMemberOut]


class CreateInviteRequest(BaseModel):
    """复用 auth.InviteRequest 但放这里以便前端只调 /api/teams/.../invites。"""

    email: EmailStr
    role: str = Field(default="member", pattern="^(admin|member)$")
    name: str = Field(default="", max_length=64)


class InviteCreatedOut(InviteOut):
    """与 auth.InviteOut 兼容（用于 team 视图）。"""


__all__ = [
    "TeamOut",
    "TeamMemberOut",
    "TeamDetailOut",
    "UpdateRoleRequest",
    "CreateInviteRequest",
    "InviteCreatedOut",
]
