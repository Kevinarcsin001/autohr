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

import time
from typing import Literal

from fastapi import APIRouter, Cookie, Request, Response, status
from sqlalchemy import select

from app.core import rate_limit
from app.core.config import settings
from app.core.deps import AdminUser, CurrentUser, DbSession
from app.core.middleware.error_handler import (
    NotFoundError,
    TooManyRequestsError,
)
from app.models.invite import TeamInvite
from app.models.user import User
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
_REFRESH_COOKIE_SAMESITE: Literal["lax", "strict", "none"] = "lax"


def _set_refresh_cookie(response: Response, refresh_token: str) -> None:
    """把 refresh token 写入 httpOnly cookie。"""
    response.set_cookie(
        key=_REFRESH_COOKIE_NAME,
        value=refresh_token,
        max_age=_REFRESH_COOKIE_MAX_AGE,
        httponly=True,
        # 生产走 nginx 同域 HTTPS 时强制 Secure；开发（http://localhost）保持 False 可用
        secure=settings.ENVIRONMENT == "production",
        samesite=_REFRESH_COOKIE_SAMESITE,
        path="/api/auth",
    )


def _clear_refresh_cookie(response: Response) -> None:
    """清除 refresh cookie。"""
    response.delete_cookie(
        key=_REFRESH_COOKIE_NAME,
        path="/api/auth",
    )


# ============================================================================
# 认证端点限流（进程内滑窗；多实例部署需换 Redis 后端，见 rate_limit 模块注释）
# ============================================================================


def _client_ip(request: Request) -> str:
    """客户端 IP：生产走 nginx，取 X-Forwarded-For 首段（nginx.conf 已设置该头）。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        return xff.split(",")[0].strip()
    return request.client.host if request.client else "unknown"


def _guard_auth_rate(
    request: Request,
    *,
    scope: str,
    extra: str | None = None,
    limit: int | None = None,
    window_seconds: int = 60,
) -> None:
    """按 (scope, ip[, 身份]) 限流；命中即 429。"""
    effective_limit = limit if limit is not None else settings.AUTH_RATE_LIMIT_LOGIN
    key = f"{scope}:{_client_ip(request)}" + (f":{extra}" if extra else "")
    if not rate_limit.allow(key, limit=effective_limit, window_seconds=window_seconds):
        raise TooManyRequestsError("请求过于频繁，请稍后再试")


def _build_auth_response(
    response: Response,
    user: User,
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
    request: Request,
    response: Response,
    db: DbSession,
) -> AuthResponse:
    """注册新用户。首位用户自动成为 admin 并创建默认 team。

    限流：每 IP 高频注册会被 429（配合下方冲突文案模糊化，把邮箱枚举
    压到不可实用）。
    """
    _guard_auth_rate(request, scope="register", limit=settings.AUTH_RATE_LIMIT_REGISTER)
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
    request: Request,
    response: Response,
    db: DbSession,
) -> AuthResponse:
    """邮箱密码登录（按 IP+邮箱双维度限流，防撞库）。"""
    _guard_auth_rate(request, scope="login")
    _guard_auth_rate(
        request, scope="login-id", extra=payload.email.lower().strip()
    )
    user, access, refresh = await auth_service.authenticate(
        db,
        email=payload.email,
        password=payload.password,
    )
    return _build_auth_response(response, user, access, refresh)


@router.post("/refresh")
async def refresh_token(
    request: Request,
    response: Response,
    db: DbSession,
    refresh_token: str | None = Cookie(default=None, alias=_REFRESH_COOKIE_NAME),
    body: RefreshRequest | None = None,
) -> dict[str, str]:
    """使用 refresh token 换取全新凭据对（**轮换**：旧 refresh 立即吊销）。

    - 优先从 httpOnly cookie 读取；缺失时允许 body 兜底（非浏览器客户端）
    - 响应同时携带新 refresh_token 并重设 cookie —— 服务端侧旧 jti 已进
      denylist，泄露的旧凭据在本次调用后即失效
    """
    _guard_auth_rate(request, scope="refresh", limit=30)
    token = refresh_token or (body.refresh_token if body else None)
    if not token:
        raise NotFoundError("Refresh token 缺失", resource="cookie")
    _user, access, new_refresh = await auth_service.refresh_access_token(
        db, refresh_token=token
    )
    _set_refresh_cookie(response, new_refresh)
    return {
        "access_token": access,
        "refresh_token": new_refresh,
        "token_type": "Bearer",
    }


@router.post("/logout", status_code=status.HTTP_204_NO_CONTENT)
async def logout(
    response: Response,
    refresh_token: str | None = Cookie(default=None, alias=_REFRESH_COOKIE_NAME),
) -> None:
    """吊销当前 refresh token 并清除 cookie。access token 由前端丢弃。"""
    if refresh_token:
        from app.core.security import decode_token
        from app.core.token_denylist import revoke

        try:
            payload = decode_token(refresh_token, expected_type="refresh")
            exp = payload.get("exp")
            remaining = (
                int(float(exp)) - int(time.time())
                if isinstance(exp, (int, float))
                else 0
            )
            await revoke(str(payload.get("jti") or ""), remaining)
        except Exception:  # noqa: BLE001 - 无效/过期 token 的登出静默成功
            pass
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
