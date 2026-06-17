"""EmailConfig schema（任务 11）。

CRUD 接口（admin only）：
- POST /api/email-configs       创建或更新（每 team 一条）
- GET  /api/email-configs       当前 team 的配置（不返回 password）
- PATCH /api/email-configs      更新（含 password 可选）
- DELETE /api/email-configs     删除

status 字段用于前端展示抓取状态/告警级别。
"""
from __future__ import annotations

from datetime import datetime
from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, EmailStr, Field

AlertLevel = Literal["none", "warning", "critical"]


# ============================================================================
# 写入模型
# ============================================================================


class EmailConfigCreate(BaseModel):
    """创建请求（密码必填）。"""

    model_config = ConfigDict(extra="forbid")

    imap_host: str = Field(..., min_length=1, max_length=255)
    imap_port: int = Field(default=993, ge=1, le=65535)
    username: EmailStr
    password: str = Field(..., min_length=1, max_length=200)
    poll_interval_min: int = Field(default=15, ge=1, le=1440)
    enabled: bool = True


class EmailConfigUpdate(BaseModel):
    """更新请求（所有字段可选；password 不填则不变）。"""

    model_config = ConfigDict(extra="forbid")

    imap_host: str | None = Field(default=None, min_length=1, max_length=255)
    imap_port: int | None = Field(default=None, ge=1, le=65535)
    username: EmailStr | None = None
    password: str | None = Field(default=None, min_length=1, max_length=200)
    poll_interval_min: int | None = Field(default=None, ge=1, le=1440)
    enabled: bool | None = None
    # 管理员手动恢复（清除告警 / paused_until）
    clear_alert: bool = False


# ============================================================================
# 读出模型
# ============================================================================


class EmailConfigOut(BaseModel):
    """对外输出（不含 password）。"""

    model_config = ConfigDict(extra="forbid", from_attributes=True)

    id: UUID
    team_id: UUID
    imap_host: str
    imap_port: int
    username: str
    poll_interval_min: int
    enabled: bool
    last_fetched_at: datetime | None = None
    consecutive_failures: int = 0
    paused_until: datetime | None = None
    last_error_summary: str | None = None
    alert_level: AlertLevel = "none"
    created_at: datetime
    updated_at: datetime


class EmailConfigStatus(BaseModel):
    """运行状态摘要（前端 dashboard 用）。"""

    model_config = ConfigDict(extra="forbid")

    configured: bool
    enabled: bool
    is_paused: bool
    paused_until: datetime | None = None
    consecutive_failures: int = 0
    alert_level: AlertLevel = "none"
    last_fetched_at: datetime | None = None
    last_error_summary: str | None = None
    next_scheduled_in_seconds: int | None = Field(
        default=None, description="预计下次拉取的倒计时；None 表示未调度"
    )


__all__ = [
    "AlertLevel",
    "EmailConfigCreate",
    "EmailConfigUpdate",
    "EmailConfigOut",
    "EmailConfigStatus",
]
