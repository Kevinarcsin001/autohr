"""AuditLogService（任务 21）：敏感操作审计流水。

职责：
1. **写入**：``log(actor_id, action, target_type, target_id, before, after, ip, user_agent)``
   - 在当前 session 内写入 ``audit_logs`` 行；不自行 commit
   - 自动脱敏敏感字段（password / token / secret / api_key / cookie / authorization）
2. **查询**：``list_logs(...)`` admin 列表，支持过滤；按 team 隔离
3. **不可删除**：本服务不暴露 delete 接口；DB 层不强制（应用层禁止）

设计约束：
- middleware 只记录写方法（POST/PUT/PATCH/DELETE）+ 成功响应（2xx）
- service 内部显式调用 ``log()`` 补充业务语义 action（如 ``screening.override`` / ``member.invite``）
- audit_logs 表无 team_id；通过 actor_id → users.team_id join 实现 team 隔离
- ``before/after`` JSONB 存操作前后快照；自动脱敏（见 ``_redact``）

敏感字段白名单（脱敏目标）：
- password / password_hash / secret / token / api_key / authorization / cookie / refresh_token
"""
from __future__ import annotations

import re
from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.audit import AuditLog
from app.models.user import User

logger = get_logger(__name__)


# ============================================================================
# 常量
# ============================================================================


DEFAULT_PAGE_SIZE = 50
MAX_PAGE_SIZE = 200


SENSITIVE_KEYS: tuple[str, ...] = (
    "password",
    "password_hash",
    "secret",
    "token",
    "api_key",
    "apikey",
    "authorization",
    "cookie",
    "refresh_token",
    "access_token",
    "private_key",
)
"""敏感字段白名单：命中即替换为 ``[REDACTED]``。匹配大小写不敏感。"""

_REDACTED = "[REDACTED]"

_SENSITIVE_PATTERN = re.compile(
    "|".join(re.escape(k) for k in SENSITIVE_KEYS),
    flags=re.IGNORECASE,
)


# ============================================================================
# 脱敏
# ============================================================================


def _redact(value: Any) -> Any:
    """递归脱敏 dict / list / 字符串中的敏感字段。

    - dict key 命中 → 替换 value 为 ``[REDACTED]``
    - 字符串包含敏感词 → 替换为 ``[REDACTED]``
    - list → 递归每个元素
    """
    if isinstance(value, dict):
        return {
            k: (_REDACTED if _is_sensitive_key(k) else _redact(v))
            for k, v in value.items()
        }
    if isinstance(value, list):
        return [_redact(v) for v in value]
    if isinstance(value, str):
        # 字符串里直接含敏感词（如 "password=xxx"）→ 整体替换
        if _SENSITIVE_PATTERN.search(value):
            return _REDACTED
        return value
    return value


def _is_sensitive_key(key: str) -> bool:
    return bool(_SENSITIVE_PATTERN.search(key))


# ============================================================================
# AuditLogService
# ============================================================================


class AuditLogService:
    """审计流水写入 + 查询。

    用法：
    - ``service.log(actor_id=..., action="job.update", target_type="job", target_id=..., before=..., after=...)``
    - 不调 commit；由 endpoint 层统一 commit
    - 失败仅 log warning，不抛异常（审计失败不能阻塞业务）
    """

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def log(
        self,
        *,
        actor_id: UUID | None,
        action: str,
        target_type: str | None = None,
        target_id: UUID | None = None,
        before: dict[str, Any] | None = None,
        after: dict[str, Any] | None = None,
        ip: str | None = None,
        user_agent: str | None = None,
    ) -> AuditLog | None:
        """写一条审计日志；脱敏敏感字段；失败仅记录不抛。

        Returns:
            AuditLog ORM 实例（已 add 到 session，未 commit）；失败返回 None
        """
        try:
            entry = AuditLog(
                actor_id=actor_id,
                action=action,
                target_type=target_type,
                target_id=target_id,
                before=_redact(before) if before else None,
                after=_redact(after) if after else None,
                ip=ip,
                user_agent=user_agent[:500] if user_agent else None,
            )
            self._db.add(entry)
            await self._db.flush()
            return entry
        except Exception:  # noqa: BLE001
            logger.exception(
                "audit_log_write_failed",
                action=action,
                target_type=target_type,
                target_id=str(target_id) if target_id else None,
            )
            return None

    async def list_logs(
        self,
        *,
        team_id: UUID,
        action: str | None = None,
        target_type: str | None = None,
        target_id: UUID | None = None,
        actor_id: UUID | None = None,
        page: int = 1,
        page_size: int = DEFAULT_PAGE_SIZE,
    ) -> tuple[list[AuditLog], int]:
        """列出 team 内的 audit logs（按 actor_id JOIN users 过滤 team）。

        Returns:
            (items, total)
        """
        page = max(1, page)
        page_size = max(1, min(MAX_PAGE_SIZE, page_size))

        stmt = (
            select(AuditLog)
            .join(User, User.id == AuditLog.actor_id)
            .where(User.team_id == team_id)
        )
        if action:
            stmt = stmt.where(AuditLog.action == action)
        if target_type:
            stmt = stmt.where(AuditLog.target_type == target_type)
        if target_id:
            stmt = stmt.where(AuditLog.target_id == target_id)
        if actor_id:
            stmt = stmt.where(AuditLog.actor_id == actor_id)

        # 总数
        count_stmt = select(func.count()).select_from(stmt.subquery())
        total = (await self._db.execute(count_stmt)).scalar_one()

        # 分页：按 created_at 倒序 + id 兜底
        stmt = stmt.order_by(AuditLog.created_at.desc(), AuditLog.id.desc())
        stmt = stmt.offset((page - 1) * page_size).limit(page_size)
        items = (await self._db.execute(stmt)).scalars().all()
        return list(items), int(total)


__all__ = [
    "AuditLogService",
    "SENSITIVE_KEYS",
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
]
