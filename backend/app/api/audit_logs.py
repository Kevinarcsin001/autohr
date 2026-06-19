"""Audit Log API 路由（任务 21）。

端点（base: /api/audit-logs）：
- GET /  分页列出 audit_logs（admin only；按 team 隔离）

权限：
- 必须 admin（``AdminUser`` 依赖）
- 跨 team 不可见（自动按 user.team_id 过滤）
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.core.deps import AdminUser, DbSession
from app.schemas.audit import AuditLogListResponse, AuditLogOut
from app.services.audit_log import (
    DEFAULT_PAGE_SIZE,
    MAX_PAGE_SIZE,
    AuditLogService,
)

router = APIRouter(prefix="/audit-logs", tags=["audit"])


@router.get("/", response_model=AuditLogListResponse)
async def list_audit_logs(
    user: AdminUser,
    db: DbSession,
    action: str | None = Query(default=None),
    target_type: str | None = Query(default=None),
    target_id: UUID | None = Query(default=None),
    actor_id: UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=MAX_PAGE_SIZE),
) -> AuditLogListResponse:
    """列出当前 team 的审计日志（admin only）。"""
    if user.team_id is None:
        return AuditLogListResponse(items=[], total=0, page=page, page_size=page_size)

    service = AuditLogService(db)
    items, total = await service.list_logs(
        team_id=UUID(str(user.team_id)),
        action=action,
        target_type=target_type,
        target_id=target_id,
        actor_id=actor_id,
        page=page,
        page_size=page_size,
    )
    return AuditLogListResponse(
        items=[AuditLogOut.model_validate(it) for it in items],
        total=total,
        page=page,
        page_size=page_size,
    )


__all__ = ["router"]
