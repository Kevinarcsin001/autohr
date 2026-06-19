"""审计中间件（任务 21）：自动拦截写方法（POST/PUT/PATCH/DELETE）。

策略：
- 只记录**成功响应**（status < 400）的写方法
- 自动推断 action：``HTTP_METHOD path``，例如 ``POST /api/jobs``
- actor_id / IP / user-agent 从请求头取；JWT 缺失时 actor_id=None（系统级 / 公开写端点）
- target_type / target_id **不自动推断**（业务语义由显式 service 调用补充）
- 失败仅 log warning，不阻塞响应

工作流：
1. middleware 拦截 request
2. 调下游 → response
3. 若 method ∈ {POST,PUT,PATCH,DELETE} 且 status < 400：
   - 从 Authorization header 解 JWT 取 actor_id
   - 取 X-Forwarded-For / client.host 等 IP
   - 取 User-Agent
   - 用 background_tasks 异步写 audit_log（不阻塞响应）

设计约束：
- 不读 request body / response body（避免性能 / 隐私问题；具体 before/after 由 service 显式 log）
- middleware 只做"谁在何时调用了什么写接口"的低级记录
- service 显式 log() 覆盖业务语义（如 ``screening.override``）

为何用 BackgroundTasks 而非直接 await？
- middleware 内拿不到 FastAPI BackgroundTasks；这里直接用独立 AsyncSession 写入，
  避免占用请求 session 的连接生命周期
"""
from __future__ import annotations

from uuid import UUID

from starlette.middleware.base import BaseHTTPMiddleware, RequestResponseEndpoint
from starlette.requests import Request
from starlette.responses import Response

from app.core.db import AsyncSessionLocal
from app.core.logging import get_logger
from app.core.security import JWTError, decode_token
from app.services.audit_log import AuditLogService

logger = get_logger(__name__)


WRITE_METHODS: frozenset[str] = frozenset({"POST", "PUT", "PATCH", "DELETE"})
"""只审计写方法；GET / HEAD / OPTIONS 不审计。"""

SKIP_PATHS: tuple[str, ...] = (
    "/api/auth/login",
    "/api/auth/register",
    "/api/auth/refresh",
    "/api/auth/logout",
    "/health",
    "/",
    "/docs",
    "/redoc",
    "/openapi.json",
)
"""跳过审计的路径：
- /api/auth/*：登录注册本身已记入业务表（refresh_token / users），重复审计无意义；
  登录失败也不希望泄露到审计表
- 系统路径：health/docs 不需要审计
"""


class AuditMiddleware(BaseHTTPMiddleware):
    """拦截写方法写 audit_logs。"""

    async def dispatch(
        self, request: Request, call_next: RequestResponseEndpoint
    ) -> Response:
        response = await call_next(request)

        # 只记录成功的写方法
        if (
            request.method.upper() in WRITE_METHODS
            and response.status_code < 400
            and not _should_skip(request.url.path)
        ):
            try:
                actor_id = _extract_actor_id(request)
                ip = _extract_client_ip(request)
                user_agent = request.headers.get("user-agent")
                action = f"{request.method.upper()} {request.url.path}"

                # 异步写入（不阻塞响应；用独立 session）
                await _write_audit_log(
                    actor_id=actor_id,
                    action=action,
                    ip=ip,
                    user_agent=user_agent,
                )
            except Exception:  # noqa: BLE001
                logger.exception("audit_middleware_write_failed")

        return response


# ============================================================================
# 工具
# ============================================================================


def _should_skip(path: str) -> bool:
    """是否跳过审计。"""
    for skip in SKIP_PATHS:
        if path == skip or path.startswith(skip + "/"):
            return True
    return False


def _extract_actor_id(request: Request) -> UUID | None:
    """从 Authorization Bearer JWT 解出 sub（user_id）。

    无 token / 无效 token → None（系统级 / 公开端点写）。
    """
    auth = request.headers.get("authorization")
    if not auth:
        return None
    parts = auth.split(" ", 1)
    if len(parts) != 2 or parts[0].lower() != "bearer":
        return None
    try:
        payload = decode_token(parts[1], expected_type="access")
    except (JWTError, FileNotFoundError):
        return None
    sub = payload.get("sub")
    if not sub:
        return None
    try:
        return UUID(str(sub))
    except (ValueError, TypeError):
        return None


def _extract_client_ip(request: Request) -> str | None:
    """取真实 client IP（优先 X-Forwarded-For / X-Real-IP）。"""
    xff = request.headers.get("x-forwarded-for")
    if xff:
        # X-Forwarded-For: client, proxy1, proxy2 → 取首个
        return xff.split(",", 1)[0].strip()
    x_real = request.headers.get("x-real-ip")
    if x_real:
        return x_real.strip()
    if request.client:
        return request.client.host
    return None


async def _write_audit_log(
    *,
    actor_id: UUID | None,
    action: str,
    ip: str | None,
    user_agent: str | None,
) -> None:
    """用独立 AsyncSession 写一条审计日志。

    独立 session 避免与请求 session 事务耦合；
    失败仅 log，不影响响应。
    """
    async with AsyncSessionLocal() as session:
        service = AuditLogService(session)
        await service.log(
            actor_id=actor_id,
            action=action,
            target_type=None,
            target_id=None,
            before=None,
            after=None,
            ip=ip,
            user_agent=user_agent,
        )
        await session.commit()


__all__ = ["AuditMiddleware", "WRITE_METHODS", "SKIP_PATHS"]
