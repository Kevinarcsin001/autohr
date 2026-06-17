"""EmailConfigService：每团队 IMAP 配置 CRUD（任务 11）。

约束：
- 每 team 至多一条 EmailConfig（``team_id`` UNIQUE）
- password 写库走 EncryptedString（Fernet）
- 对外响应永远不含 password（即使 update 时也不回显）
- clear_alert=True 时清除 paused_until / alert_level / consecutive_failures
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.middleware.error_handler import (
    ConflictError,
    NotFoundError,
)
from app.models.email_config import EmailConfig
from app.schemas.email import (
    EmailConfigCreate,
    EmailConfigUpdate,
)

logger = get_logger(__name__)


def _now() -> datetime:
    return datetime.now(timezone.utc)


class EmailConfigService:
    """每 team 一条 EmailConfig 的 CRUD 服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ----- read -----

    async def get_for_team(self, team_id: uuid.UUID) -> EmailConfig | None:
        result = await self.db.execute(
            select(EmailConfig).where(EmailConfig.team_id == team_id)
        )
        return result.scalar_one_or_none()

    async def get_for_team_required(self, team_id: uuid.UUID) -> EmailConfig:
        cfg = await self.get_for_team(team_id)
        if cfg is None:
            raise NotFoundError(
                "EmailConfig not found for team", resource="email_config"
            )
        return cfg

    # ----- create -----

    async def create(
        self, team_id: uuid.UUID, payload: EmailConfigCreate
    ) -> EmailConfig:
        existing = await self.get_for_team(team_id)
        if existing is not None:
            raise ConflictError(
                "Team already has an EmailConfig", team_id=str(team_id)
            )
        cfg = EmailConfig(
            team_id=team_id,
            imap_host=payload.imap_host,
            imap_port=payload.imap_port,
            username=payload.username,
            password_enc=payload.password,  # EncryptedString 自动加密
            poll_interval_min=payload.poll_interval_min,
            enabled=payload.enabled,
        )
        self.db.add(cfg)
        await self.db.flush()
        # 刷新拿到 server-side 默认值（updated_at、created_at）
        await self.db.refresh(cfg)
        logger.info(
            "email_config_created",
            config_id=str(cfg.id),
            team_id=str(team_id),
            host=payload.imap_host,
        )
        return cfg

    # ----- update -----

    async def update(
        self,
        team_id: uuid.UUID,
        payload: EmailConfigUpdate,
    ) -> EmailConfig:
        cfg = await self.get_for_team_required(team_id)

        if payload.imap_host is not None:
            cfg.imap_host = payload.imap_host
        if payload.imap_port is not None:
            cfg.imap_port = payload.imap_port
        if payload.username is not None:
            cfg.username = payload.username
        if payload.password is not None:
            cfg.password_enc = payload.password
        if payload.poll_interval_min is not None:
            cfg.poll_interval_min = payload.poll_interval_min
        if payload.enabled is not None:
            cfg.enabled = payload.enabled

        if payload.clear_alert:
            cfg.consecutive_failures = 0
            cfg.alert_level = "none"
            cfg.paused_until = None
            cfg.last_error_summary = None
            logger.info(
                "email_config_alert_cleared",
                config_id=str(cfg.id),
                team_id=str(team_id),
            )

        await self.db.flush()
        # 刷新拿 onupdate=now() 触发的 updated_at（否则 from_attributes 会触发懒加载 → async 报错）
        await self.db.refresh(cfg)
        return cfg

    # ----- delete -----

    async def delete(self, team_id: uuid.UUID) -> None:
        cfg = await self.get_for_team_required(team_id)
        await self.db.delete(cfg)
        await self.db.flush()


__all__ = ["EmailConfigService"]
