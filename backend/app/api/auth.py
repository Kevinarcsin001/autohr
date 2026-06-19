"""认证 API 路由。

端点：
- POST /api/auth/register   普通注册（首位用户自动 admin）
- POST /api/auth/login      登录
- POST /api/auth/refresh    refresh → 新 access（refresh 走 httpOnly cookie）
- POST /api/auth/logout     清除 refresh cookie（access 由前端丢弃）
- POST /api/auth/invite     admin 发起邀请（需 admin）
- GET  /api/auth/invites    列出当前 team 的待接受邀请（需 admin）
- POST /api/auth/accept-invite  通过邀请链接注册

refresh token 策略：
- 登录/注册/接受邀请成功时通过 ``Set-Cookie: refresh_token=...; HttpOnly``
  下发到浏览器；前端无法 JS 读取，规避 XSS
- access token 在响应体里，前端存内存（Zustand）—— 设计文档任务 5
- /refresh 端点从 cookie 读取 refresh token；如果 cookie 缺失也允许 body 兜底
"""
from __future__ import annotations

from fastapi import APIRouter, Cookie, Response, status
from sqlalchemy import select

from app.core.deps import AdminUser, CurrentUser, DbSession
from app.core.middleware.error_handler import NotFoundError
from app.models.invite import TeamInvite
from app.schemas.auth import (
    AcceptInviteRequest,
    AuthResponse,
    InviteOut,
    InviteRequest,
    LoginRequest,
    RefreshRequest,
    RegisterRequest,
    UserOut,
)
from app.services import auth_service

router = APIRouter(prefix="/auth", tags=["auth"])

# ============================================================================
# 常量
# ============================================================================

_REFRESH_COOKIE_NAME = "autohr_refresh"
# Cookie 过期秒数 = refresh token 默认 7 天
_REFRESH_COOKIE_MAX_AGE = 7 * 24 * 3600
# Cookie 安全相关：本地开发 http://localhost，因此 secure=False；
# 生产同站跨端口需要 SameSite=Lax（前端 3001 → 后端 8000）
_REFRESH_COOKIE_SAMESITE = "lax"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """把 refresh token 写入 httpOnly cookie。"""
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=_REFRESH_COOKIE_MAX_AGE,
        httponly=True,
        secure=False,  # 本地开发；生产通过 ENV 切换
        samesite=_REFRESH_COOKIE_SAMESITE,
        path="/api/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    """清除 refresh cookie。"""
    response.delete_cookie(
        key=_REFRESH_COOKIE_NAME,
        path="/api/auth",
    )


def _build_auth_response(
    response: Response,
    user,
    access_token: str,
    refresh_token: str,
) -> AuthResponse:
    """统一组装 AuthResponse 并下发 refresh cookie。"""
    _set_refresh_cookie(response, refresh_token)
    from app.schemas.auth import TokenPair  # 局部导入避免循环

    return AuthResponse(
        user=UserOut.from_orm_user(user),
        tokens=TokenPair(
            access_token=access_token,
            refresh_token=refresh_token,  # 同时在 body 里返回，便于非浏览器客户端
            expires_in=30 * 60,  # 与 settings.JWT_ACCESS_TOKEN_EXPIRE_MINUTES 一致
        ),
    )


# ============================================================================
# 端点
# ============================================================================


@router.post(
    "/register",
    response_model=AuthResponse,
    status_code=status.HTTP_201_CREATED,
)
async def register(
    payload: RegisterRequest,
    response: Response,
    db: DbSession,
) -> AuthResponse:
    """注册新用户。首位用户自动成为 admin 并创建默认 team。"""
    user = await auth_service.register(
        db,
        email=payload.email,
        password=payload.password,
        name=payload.name,
    )
    from app.core.security import create_access_token, create_refresh_token

    access = create_access_token(
        subject=user.id,
        extra_claims={
            "team_id": str(user.team_id) if user.team_id else None,
            "role": user.role,
            "email": user.email,
        },
    )
    refresh = create_refresh_token(subject=user.id)
    return _build_auth_response(response, user, access, refresh)


@router.post("/login", response_model=AuthResponse)
async def login(
    payload: LoginRequest,
    response: Response,
    db: DbSession,
) -> AuthResponse:
    """邮箱密码登录。"""
    user, access, refresh = await auth_service.authenticate(
        db,
        email=payload.email,
        password=payload.password,
    )
    return _build_auth_response(response, user, access, refresh)


@router.post("/refresh")
async def refresh_token(
    response: Response,
    db: DbSession,
    refresh_token: str | None = Cookie(default=None, alias=_REFRESH_COOKIE_NAME),
    body: RefreshRequest | None = None,
) -> dict[str, str]:
    """使用 refresh token 获取新的 access token。

    优先从 httpOnly cookie 读取 refresh；如缺失允许 body 兜底（非浏览器客户端）。
    refresh token 本身不轮换（仍按原过期时间）。
    """
    token = refresh_token or (body.refresh_token if body else None)
    if not token:
        raise NotFoundError("Refresh token 缺失", resource="cookie")
    access = await auth_service.refresh_access_token(db, refresh_token=token)
    # 不轮换 refresh，但仍刷新 cookie 续期（max_age 重新计算）
    _set_refresh_cookie(response, token)
    return {"access_token": access, "token_type": "Bearer"}


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(response: Response) -> None:
    """清除 refresh cookie。access token 由前端丢弃。"""
    _clear_refresh_cookie(response)


@router.post(
    "/invite",
    response_model=InviteOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_invite(
    payload: InviteRequest,
    admin: AdminUser,
    db: DbSession,
) -> InviteOut:
    """团队管理员发起邀请。返回一次性 invite_token。

    注意：invite_token 同时在响应中明文返回，便于 admin 通过邮件/IM 转发；
    在邮件链路打通前，本任务暂由 admin 自行复制链接。
    """
    if admin.team_id is None:
        raise NotFoundError("当前用户未关联团队", resource="team")

    invite = await auth_service.invite_member(
        db,
        team_id=admin.team_id,
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


@router.get("/invites", response_model=list[InviteOut])
async def list_invites(
    admin: AdminUser,
    db: DbSession,
) -> list[InviteOut]:
    """列出当前 team 的所有邀请（按创建时间倒序，admin only）。"""
    if admin.team_id is None:
        return []
    result = await db.execute(
        select(TeamInvite)
        .where(TeamInvite.team_id == admin.team_id)
        .order_by(TeamInvite.created_at.desc())  # type: ignore[attr-defined]
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


@router.post("/accept-invite", response_model=AuthResponse)
async def accept_invite(
    payload: AcceptInviteRequest,
    response: Response,
    db: DbSession,
) -> AuthResponse:
    """通过邀请链接注册并加入团队。"""
    user, access, refresh = await auth_service.accept_invite(
        db,
        invite_token=payload.invite_token,
        name=payload.name,
        password=payload.password,
    )
    return _build_auth_response(response, user, access, refresh)


@router.get("/me", response_model=UserOut)
async def get_me(user: CurrentUser) -> UserOut:
    """获取当前登录用户信息。"""
    return UserOut.from_orm_user(user)


__all__ = ["router"]
