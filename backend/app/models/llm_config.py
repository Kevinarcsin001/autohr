"""LLMConfig 模型：scope 级别的 LLM 路由策略覆盖（任务 25）。

设计：
- ``UNIQUE(team_id, scope)``：同 team 同 scope 仅一条覆盖
- ``scope`` ∈ extractor/scorer/reasoning/interview
- ``primary`` / ``fallback`` 是 adapter 名（zhipu / qwen / mock）
- ``model_overrides`` JSONB：可选的 per-adapter 模型名覆盖

任务 4 的 LLMRouter.scope_policies 是进程内内存，
LLMConfig 是 DB 持久化版本，应用启动 / 配置变更时刷新到 router。
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import (
    DateTime,
    ForeignKey,
    String,
    UniqueConstraint,
    func,
)
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin


class LLMConfig(UUIDPKMixin, CreatedAtMixin, Base):
    """scope 级别路由策略（per team）。

    设计要点：
    - team_id 为空 → 全局默认（admin 写入）；非空 → team 级覆盖
    - 老的 settings.LLM_PRIMARY / LLM_FALLBACK 仍作为兜底默认
    - 应用启动时由 LLMConfigService.sync_to_router 刷新内存
    """

    __tablename__ = "llm_configs"
    __table_args__ = (
        UniqueConstraint("team_id", "scope", name="uq_llm_config_team_scope"),
    )

    team_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=True,
        index=True,
    )
    scope: Mapped[str] = mapped_column(String, nullable=False, index=True)
    primary: Mapped[str] = mapped_column(String, nullable=False)
    fallback: Mapped[str | None] = mapped_column(String, nullable=True)
    model_overrides: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    timeout_seconds: Mapped[int | None] = mapped_column(
        nullable=True,
        comment="单模型超时；NULL → 使用 settings.LLM_TIMEOUT_SECONDS",
    )
    circuit_breaker_failures: Mapped[int | None] = mapped_column(
        nullable=True,
        comment="熔断阈值；NULL → 使用 settings.LLM_CIRCUIT_BREAKER_FAILURES",
    )
    updated_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        onupdate=func.now(),
        nullable=False,
    )


__all__ = ["LLMConfig"]
