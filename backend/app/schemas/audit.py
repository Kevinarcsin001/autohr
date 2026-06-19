"""Audit Log Pydantic schemas（任务 21）。"""
from __future__ import annotations

from datetime import datetime
from ipaddress import IPv4Address, IPv6Address
from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field, field_validator


class AuditLogOut(BaseModel):
    """单条审计日志输出。"""

    id: UUID
    actor_id: UUID | None = None
    action: str
    target_type: str | None = None
    target_id: UUID | None = None
    before: dict[str, Any] | None = None
    after: dict[str, Any] | None = None
    ip: str | None = None
    user_agent: str | None = None
    created_at: datetime

    class Config:
        from_attributes = True

    @field_validator("ip", mode="before")
    @classmethod
    def _coerce_ip(cls, value: Any) -> str | None:
        """INET 列返回 IPv4Address / IPv6Address；统一转 str。"""
        if value is None:
            return None
        if isinstance(value, (IPv4Address, IPv6Address)):
            return str(value)
        return str(value)


class AuditLogListResponse(BaseModel):
    """审计日志分页响应。"""

    items: list[AuditLogOut]
    total: int = Field(ge=0)
    page: int = Field(ge=1)
    page_size: int = Field(ge=1)


__all__ = ["AuditLogOut", "AuditLogListResponse"]
