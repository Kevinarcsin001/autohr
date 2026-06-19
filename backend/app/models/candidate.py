"""Candidate 聚合：去重后的"人" + 多来源 + 多简历版本 + 结构化抽取。"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Any

from sqlalchemy import DateTime, ForeignKey, String, Text, func
from sqlalchemy.dialects.postgresql import JSONB, UUID
from sqlalchemy.orm import Mapped, mapped_column

from app.models.base import Base, CreatedAtMixin, UUIDPKMixin
from app.models.types import EncryptedString, ParseStatus, SourceType


class Candidate(UUIDPKMixin, CreatedAtMixin, Base):
    """去重后的候选人（"人"的实体，多个来源合并到此）。"""

    __tablename__ = "candidates"

    team_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("teams.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    dedup_key: Mapped[str] = mapped_column(
        String, unique=True, nullable=False, index=True
    )
    name: Mapped[str] = mapped_column(EncryptedString, nullable=False)
    phone: Mapped[str | None] = mapped_column(EncryptedString, nullable=True)
    email: Mapped[str | None] = mapped_column(EncryptedString, nullable=True, index=True)
    merged_into: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="SET NULL"),
        nullable=True,
    )


class CandidateSource(UUIDPKMixin, Base):
    """每次投递/进入的来源记录（一个候选人可能多次投递）。"""

    __tablename__ = "candidate_sources"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_type: Mapped[str] = mapped_column(SourceType, nullable=False, index=True)
    source_meta: Mapped[dict[str, Any] | None] = mapped_column(JSONB, nullable=True)
    fetched_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class CandidateResume(UUIDPKMixin, Base):
    """每个来源对应的简历文件版本（解析后的纯文本也存此）。"""

    __tablename__ = "candidate_resumes"

    candidate_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidates.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    source_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_sources.id", ondelete="CASCADE"),
        nullable=False,
        index=True,
    )
    file_storage_key: Mapped[str] = mapped_column(String, nullable=False)
    file_mime: Mapped[str | None] = mapped_column(String, nullable=True)
    parsed_text: Mapped[str | None] = mapped_column(Text, nullable=True)
    parse_status: Mapped[str] = mapped_column(
        ParseStatus, default="pending", nullable=False, index=True
    )
    parse_error: Mapped[str | None] = mapped_column(Text, nullable=True)
    uploaded_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )


class ParsedStructure(UUIDPKMixin, Base):
    """结构化抽取结果（按 resume 版本）。"""

    __tablename__ = "parsed_structures"

    resume_id: Mapped[uuid.UUID] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("candidate_resumes.id", ondelete="CASCADE"),
        nullable=False,
        unique=True,
    )
    data: Mapped[dict[str, Any]] = mapped_column(JSONB, nullable=False)
    extracted_at: Mapped[datetime] = mapped_column(
        DateTime(timezone=True), server_default=func.now(), nullable=False
    )
    llm_call_id: Mapped[uuid.UUID | None] = mapped_column(
        UUID(as_uuid=True),
        ForeignKey("llm_calls.id", ondelete="SET NULL"),
        nullable=True,
    )


__all__ = ["Candidate", "CandidateSource", "CandidateResume", "ParsedStructure"]
