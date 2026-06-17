"""认证相关 Pydantic schema。

约束：
- 密码：最少 8 位，必须同时含字母与数字（design.md 任务 5 Restrictions）
- email 由后端 CITEXT 列保证大小写不敏感
- invite_token 是一次性 url-safe token，不在此处校验长度
"""
from __future__ import annotations

import re

from pydantic import BaseModel, EmailStr, Field, field_validator

_PASSWORD_LETTER = re.compile(r"[A-Za-z]")
_PASSWORD_DIGIT = re.compile(r"\d")


def _validate_password(v: str) -> str:
    if len(v) < 8:
        raise ValueError("密码至少 8 位")
    if not _PASSWORD_LETTER.search(v):
        raise ValueError("密码必须包含字母")
    if not _PASSWORD_DIGIT.search(v):
        raise ValueError("密码必须包含数字")
    return v


class RegisterRequest(BaseModel):
    email: EmailStr
    password: str = Field(min_length=8, max_length=72)
    name: str = Field(min_length=1, max_length=64)

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _validate_password(v)


class LoginRequest(BaseModel):
    email: EmailStr
    password: str


class TokenPair(BaseModel):
    access_token: str
    refresh_token: str
    token_type: str = "Bearer"
    expires_in: int  # 秒


class RefreshRequest(BaseModel):
    refresh_token: str


class UserOut(BaseModel):
    id: str
    email: str
    name: str
    role: str
    team_id: str | None = None

    @classmethod
    def from_orm_user(cls, user: object) -> "UserOut":
        return cls(
            id=str(user.id),  # type: ignore[attr-defined]
            email=user.email,  # type: ignore[attr-defined]
            name=user.name,  # type: ignore[attr-defined]
            role=user.role,  # type: ignore[attr-defined]
            team_id=str(user.team_id) if getattr(user, "team_id", None) else None,  # type: ignore[attr-defined]
        )


class AuthResponse(BaseModel):
    """登录/注册成功响应：含 token 与用户信息。"""

    user: UserOut
    tokens: TokenPair


class InviteRequest(BaseModel):
    email: EmailStr
    role: str = Field(default="member", pattern="^(admin|member)$")
    name: str = Field(default="", max_length=64)


class InviteOut(BaseModel):
    id: str
    email: str
    role: str
    invite_token: str  # 一次性 token，邮件链接中携带
    expires_at: str  # ISO8601


class AcceptInviteRequest(BaseModel):
    invite_token: str
    name: str = Field(min_length=1, max_length=64)
    password: str = Field(min_length=8, max_length=72)

    @field_validator("password")
    @classmethod
    def _check_password(cls, v: str) -> str:
        return _validate_password(v)


__all__ = [
    "RegisterRequest",
    "LoginRequest",
    "TokenPair",
    "RefreshRequest",
    "UserOut",
    "AuthResponse",
    "InviteRequest",
    "InviteOut",
    "AcceptInviteRequest",
]
