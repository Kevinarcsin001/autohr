"""Job 聚合：职位定义 + 版本快照 + 硬性条件。

- jobs：当前活跃版本（current_version 指针）
- job_versions：每次更新写入完整 snapshot，可追溯历史
- job_hard_requirements：结构化硬性条件（独立表便于查询 + 与 snapshot 解耦）
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, Integer, String, Text, func
from sqlalchemy.dialects.postgresql import ARRAY, JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, TimestampMixin, UUIDPKMixin
from app.models.types import EducationLevel, JobStatus


class Job(UUIDPKMixin, TimestampMixin, Base):
    """职位（当前版本指针）。"""

    __tablename__ = "jobs"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    title: Mapped[str] = mapped_column(Text, nullable=False)
    jd_text: Mapped[str] = mapped_column(Text, nullable=False)
    status: Mapped[str] = mapped_column(
        JobStatus, default="draft", nullable=False, index=True
    )
    llm_config: Mapped[dict[str, Any] | None] = mapped_column(
        JSONB, nullable=True
    )
    current_version: Mapped[int] = mapped_column(Integer, default=1, nullable=False)
    created_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )


class JobVersion(UUIDPKMixin, Base):
    """职位版本快照（含完整 hard_requirements / llm_config / JD 文本）。"""

    __tablename__ = "job_versions"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    version: Mapped[int] = mapped_column(Integer, nullable=False)
    snapshot: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    changed_by: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("users.id", ondelete="SET NULL"),
        nullable=False,
    )
    changed_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True),
        server_default=func.now(),
        nullable=False,
    )


class JobHardRequirement(UUIDPKMixin, Base):
    """结构化硬性条件（按 job 检索；snapshot 中也冗余一份用于历史）。"""

    __tablename__ = "job_hard_requirements"

    job_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("jobs.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    min_education: Mapped[str | None] = mapped_column(
        EducationLevel, nullable=True
    )
    min_years: Mapped[int | None] = mapped_column(Integer, nullable=True)
    required_skills: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )
    excluded_companies: Mapped[list[str] | None] = mapped_column(
        ARRAY(String), nullable=True
    )


__all__ = ["Job", "JobVersion", "JobHardRequirement"]
