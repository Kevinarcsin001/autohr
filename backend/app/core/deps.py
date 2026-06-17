"""FastAPI 依赖注入。

任务 5 阶段：
- ``get_db_session`` 仍提供事务性 AsyncSession
- ``get_current_user_id`` 仅 sub（轻量）
- ``get_current_user`` 返回完整 User ORM（含 team_id / role），用于需要授权决策的端点
- ``require_admin`` 限制 admin 才能访问（如 invite_member）
- ``get_team_scope`` 返回当前用户 team_id（任务 6 扩展为多 team 切换）

任务 6 将扩展为支持 team 切换 / impersonation 等。
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from typing import Annotated
from uuid import UUID

from fastapi import Depends, Header, HTTPException, status
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import get_db
from app.core.logging import get_logger
from app.core.middleware.error_handler import ForbiddenError, UnauthorizedError
from app.core.security import JWTError, decode_token
from app.models.user import User

logger = get_logger(__name__)


# ============================================================================
# 数据库 session 依赖
# ============================================================================


async def get_db_session() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：AsyncSession 自动提交/回滚。"""
    async for session in get_db():
        yield session


DbSession = Annotated[AsyncSession, Depends(get_db_session)]


# ============================================================================
# JWT / 当前用户依赖
# ============================================================================


async def get_current_user_id(
    authorization: Annotated[str | None, Header()] = None,
) -> str:
    """FastAPI 依赖：从 ``Authorization: Bearer <jwt>`` 解析 user_id。

    Returns:
        user_id（str）

    Raises:
        HTTPException 401: 缺少 / 格式错误 / 过期 / 类型不匹配
    """
    if not authorization:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Missing Authorization header",
            headers={"WWW-Authenticate": "Bearer"},
        )

    parts = authorization.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid Authorization header format, expected 'Bearer <token>'",
            headers={"WWW-Authenticate": "Bearer"},
        )

    token = parts[1]
    try:
        payload = decode_token(token, expected_type="access")
    except JWTError as exc:
        logger.warning("jwt_decode_failed", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Invalid or expired token",
            headers={"WWW-Authenticate": "Bearer"},
        ) from exc
    except FileNotFoundError as exc:
        # JWT 密钥未配置（开发环境常见）
        logger.error("jwt_key_missing", error=str(exc))
        raise HTTPException(
            status_code=status.HTTP_500_INTERNAL_SERVER_ERROR,
            detail="JWT key not configured",
        ) from exc

    user_id = payload.get("sub")
    if not user_id:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="Token missing 'sub' claim",
            headers={"WWW-Authenticate": "Bearer"},
        )

    return str(user_id)


CurrentUserId = Annotated[str, Depends(get_current_user_id)]
"""当前登录用户 ID（基于 JWT 解码，无 DB 查询）。"""


async def get_current_user(
    authorization: Annotated[str | None, Header()] = None,
    db: AsyncSession = Depends(get_db_session),
) -> User:
    """FastAPI 依赖：返回完整 User ORM 对象。

    每次请求都会查 DB 一次（确保 user 仍然存在且 role 未变更）。

    Raises:
        HTTPException 401: token 无效 / 用户已删除
    """
    user_id = await get_current_user_id(authorization=authorization)
    result = await db.execute(select(User).where(User.id == UUID(user_id)))
    user = result.scalar_one_or_none()
    if user is None:
        raise HTTPException(
            status_code=status.HTTP_401_UNAUTHORIZED,
            detail="User not found or deleted",
            headers={"WWW-Authenticate": "Bearer"},
        )
    return user


CurrentUser = Annotated[User, Depends(get_current_user)]
"""当前登录用户（完整 ORM 对象，含 team_id / role）。"""


def require_admin(user: CurrentUser) -> User:
    """FastAPI 依赖：要求当前用户是 admin，否则 403。"""
    if user.role != "admin":
        raise ForbiddenError("仅团队管理员可执行此操作", required_role="admin")
    return user


AdminUser = Annotated[User, Depends(require_admin)]
"""当前用户必须是 admin。"""


async def get_optional_user_id(
    authorization: Annotated[str | None, Header()] = None,
) -> str | None:
    """FastAPI 依赖：可选用户 ID（无 token 返回 None，用于公开端点附带用户上下文）。"""
    if not authorization:
        return None
    try:
        return await get_current_user_id(authorization=authorization)
    except HTTPException:
        return None


OptionalUserId = Annotated[str | None, Depends(get_optional_user_id)]


__all__ = [
    "DbSession",
    "CurrentUserId",
    "CurrentUser",
    "AdminUser",
    "OptionalUserId",
    "get_db_session",
    "get_current_user_id",
    "get_current_user",
    "require_admin",
    "get_optional_user_id",
]
