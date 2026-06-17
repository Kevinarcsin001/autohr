"""ORM 模型统一导出。

按 spec design.md 顺序组织 17 张表（实际含 3 个延伸表共 20 个模型），
所有表继承 ``app.core.db.Base``。

导入此包即可触发 SQLAlchemy metadata 注册（Alembic autogenerate 依赖）。

注：``users.email`` CITEXT 由 Alembic 迁移创建扩展后用 ``CITEXT`` 列；
为避免在 ORM 中绑定 PostgreSQL 扩展类型，这里使用普通 ``String`` + lowercase
唯一索引（迁移中加 ``LOWER(email)`` UNIQUE INDEX）。
"""
from __future__ import annotations

from app.models.async_job import AsyncJob
from app.models.audit import AuditLog
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.dedup import DedupMatch
from app.models.email_config import EmailConfig
from app.models.interview import InterviewFeedback, InterviewQuestion
from app.models.invite import TeamInvite
from app.models.job import Job, JobHardRequirement, JobVersion
from app.models.llm_call import LLMCall
from app.models.score import Score, ScoreReason
from app.models.screening import ManualOverride, ScreeningResult
from app.models.team import Team
from app.models.user import User

__all__ = [
    # teams / users / invites
    "Team",
    "User",
    "TeamInvite",
    # jobs
    "Job",
    "JobVersion",
    "JobHardRequirement",
    # candidates
    "Candidate",
    "CandidateSource",
    "CandidateResume",
    "ParsedStructure",
    # screening
    "ScreeningResult",
    "ManualOverride",
    # scores
    "Score",
    "ScoreReason",
    # interviews
    "InterviewQuestion",
    "InterviewFeedback",
    # dedup
    "DedupMatch",
    # llm / async / email / audit
    "LLMCall",
    "AsyncJob",
    "EmailConfig",
    "AuditLog",
]
