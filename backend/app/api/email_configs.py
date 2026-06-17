"""EmailConfig API 路由（任务 11）。

端点（base: /api/email-configs）：
- POST   /        创建（每 team 一条）
- GET    /        获取（不含 password）
- GET    /status  运行状态摘要
- PATCH  /        更新（含 clear_alert）
- DELETE /        删除

权限：admin only
"""
from __future__ import annotations

from datetime import datetime, timezone
from uuid import UUID

from fastapi import APIRouter, status

from app.core.deps import AdminUser, DbSession
from app.core.middleware.error_handler import ForbiddenError
from app.schemas.email import (
    EmailConfigCreate,
    EmailConfigOut,
    EmailConfigStatus,
    EmailConfigUpdate,
)
from app.services.email_config_service import EmailConfigService

router = APIRouter(prefix="/email-configs", tags=["email-configs"])


def _require_team(user) -> UUID:
    if user.team_id is None:
        raise ForbiddenError("当前用户未加入任何团队")
    return UUID(str(user.team_id))


def _to_out(cfg) -> EmailConfigOut:
    return EmailConfigOut.model_validate(cfg)


def _to_status(cfg) -> EmailConfigStatus:
    now = datetime.now(timezone.utc)
    is_paused = bool(cfg.paused_until and cfg.paused_until > now)
    next_in: int | None = None
    if cfg.enabled and not is_paused:
        # 简单估算：用 poll_interval_min 作为下次窗口
        next_in = max(cfg.poll_interval_min * 60, 60)
    return EmailConfigStatus(
        configured=True,
        enabled=cfg.enabled,
        is_paused=is_paused,
        paused_until=cfg.paused_until,
        consecutive_failures=cfg.consecutive_failures,
        alert_level=cfg.alert_level,
        last_fetched_at=cfg.last_fetched_at,
        last_error_summary=cfg.last_error_summary,
        next_scheduled_in_seconds=next_in,
    )


@router.post("/", response_model=EmailConfigOut, status_code=status.HTTP_201_CREATED)
async def create_email_config(
    payload: EmailConfigCreate,
    user: AdminUser,
    db: DbSession,
) -> EmailConfigOut:
    team_id = _require_team(user)
    service = EmailConfigService(db)
    cfg = await service.create(team_id=team_id, payload=payload)
    return _to_out(cfg)


@router.get("/", response_model=EmailConfigOut | None)
async def get_email_config(
    user: AdminUser,
    db: DbSession,
) -> EmailConfigOut | None:
    team_id = _require_team(user)
    service = EmailConfigService(db)
    cfg = await service.get_for_team(team_id)
    return _to_out(cfg) if cfg else None


@router.get("/status", response_model=EmailConfigStatus)
async def get_email_config_status(
    user: AdminUser,
    db: DbSession,
) -> EmailConfigStatus:
    team_id = _require_team(user)
    service = EmailConfigService(db)
    cfg = await service.get_for_team(team_id)
    if cfg is None:
        return EmailConfigStatus(
            configured=False,
            enabled=False,
            is_paused=False,
            paused_until=None,
            consecutive_failures=0,
            alert_level="none",
            last_fetched_at=None,
            last_error_summary=None,
            next_scheduled_in_seconds=None,
        )
    return _to_status(cfg)


@router.patch("/", response_model=EmailConfigOut)
async def update_email_config(
    payload: EmailConfigUpdate,
    user: AdminUser,
    db: DbSession,
) -> EmailConfigOut:
    team_id = _require_team(user)
    service = EmailConfigService(db)
    cfg = await service.update(team_id=team_id, payload=payload)
    return _to_out(cfg)


@router.delete("/", status_code=status.HTTP_204_NO_CONTENT)
async def delete_email_config(
    user: AdminUser,
    db: DbSession,
) -> None:
    team_id = _require_team(user)
    service = EmailConfigService(db)
    await service.delete(team_id=team_id)


__all__ = ["router"]
