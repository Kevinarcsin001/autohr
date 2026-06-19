"""CandidateDetailService（任务 24）：详情聚合 + 签名 URL + 活动时间线。

职责：
1. ``get_detail(team_id, candidate_id, job_id)`` — 一次性聚合：
   Candidate + Screening + Score + ParsedStructure + 最新 resume
2. ``get_resume_url(team_id, candidate_id)`` — 5min 签名 URL（取最新 resume）
3. ``list_activity(team_id, candidate_id, page, page_size)`` — UNION 查询：
   audit_logs（target_type='candidate'）+ manual_overrides（candidate 关联）

安全边界：
- 所有方法**强制** team_id 过滤（通过 candidate.team_id 校验）
- 跨 team 访问返回 None / 404（service 层不抛 NotFoundError；由 API 层抛）
- 不暴露未脱敏 PII（service 层只返回 ORM 已脱敏字段）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import select, union_all, literal, func
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.config import settings
from app.core.logging import get_logger
from app.adapters.storage import S3StorageAdapter, get_storage
from app.models.audit import AuditLog
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.score import Score
from app.models.screening import ManualOverride, ScreeningResult
from app.models.user import User
from app.schemas.candidate_detail import (
    CandidateActivityItem,
    CandidateDetailResponse,
    CandidateResumeOut,
    CandidateSummary,
    ResumeUrlResponse,
)
from app.schemas.candidate_structure import CandidateStructure
from app.schemas.score import ScoreOut
from app.schemas.screening import ScreeningResultOut

logger = get_logger(__name__)


# ============================================================================
# 常量
# ============================================================================


RESUME_SIGNED_URL_EXPIRE_SECONDS = 300  # 5 min
DEFAULT_ACTIVITY_PAGE_SIZE = 20
MAX_ACTIVITY_PAGE_SIZE = 100


# ============================================================================
# CandidateDetailService
# ============================================================================


class CandidateDetailService:
    """候选人详情聚合服务（强制 team_id 过滤）。"""

    def __init__(
        self,
        db: AsyncSession,
        storage: S3StorageAdapter | None = None,
    ) -> None:
        self._db = db
        self._storage = storage

    # ----- 内部：取 storage（懒加载单例） -----

    def _get_storage(self) -> S3StorageAdapter:
        if self._storage is None:
            self._storage = get_storage()
        return self._storage

    # ----- 内部：team 校验 -----

    async def _get_candidate_in_team(
        self,
        *,
        team_id: uuid.UUID,
        candidate_id: uuid.UUID,
    ) -> Candidate | None:
        """取候选人；跨 team / 不存在 → None（不暴露存在性）。"""
        result = await self._db.execute(
            select(Candidate).where(
                Candidate.id == candidate_id,
                Candidate.team_id == team_id,
                Candidate.merged_into.is_(None),
            )
        )
        return result.scalar_one_or_none()

    # ========================================================================
    # get_detail
    # ========================================================================

    async def get_detail(
        self,
        *,
        team_id: uuid.UUID,
        candidate_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> CandidateDetailResponse | None:
        """聚合查询候选人详情。

        Returns:
            CandidateDetailResponse | None（跨 team / 不存在 → None）
        """
        candidate = await self._get_candidate_in_team(
            team_id=team_id, candidate_id=candidate_id
        )
        if candidate is None:
            return None

        # 并发可优化；当前为顺序查询（页面 LCP ≤ 2.5s，5 次查询总计 < 100ms 可接受）
        screening = await self._fetch_screening(
            candidate_id=candidate_id, job_id=job_id
        )
        score = await self._fetch_score(
            candidate_id=candidate_id, job_id=job_id
        )
        latest_resume = await self._fetch_latest_resume(candidate_id=candidate_id)
        parsed_structure = None
        if latest_resume is not None:
            parsed_structure = await self._fetch_parsed_structure(
                resume_id=latest_resume.id
            )

        # 取 latest source（用于 candidate summary）
        source = await self._fetch_latest_source(candidate_id=candidate_id)

        return CandidateDetailResponse(
            candidate=CandidateSummary(
                id=candidate.id,
                name=candidate.name,
                phone=candidate.phone,
                email=candidate.email,
                source_type=source.source_type if source else None,
                source_id=source.id if source else None,
                created_at=candidate.created_at,
            ),
            screening_result=(
                ScreeningResultOut.model_validate(screening)
                if screening is not None
                else None
            ),
            score=(
                ScoreOut.model_validate(score) if score is not None else None
            ),
            parsed_structure=parsed_structure,
            resume=(
                CandidateResumeOut(
                    id=latest_resume.id,
                    parsed_text=latest_resume.parsed_text,
                    file_storage_key=latest_resume.file_storage_key,
                    mime_type=latest_resume.file_mime,
                    filename=None,  # 当前 schema 无 filename 列；前缀推断由前端处理
                )
                if latest_resume is not None
                else None
            ),
        )

    # ========================================================================
    # get_resume_url
    # ========================================================================

    async def get_resume_url(
        self,
        *,
        team_id: uuid.UUID,
        candidate_id: uuid.UUID,
    ) -> ResumeUrlResponse | None:
        """生成最新 resume 的签名 URL（5 min 过期）。"""
        candidate = await self._get_candidate_in_team(
            team_id=team_id, candidate_id=candidate_id
        )
        if candidate is None:
            return None

        latest_resume = await self._fetch_latest_resume(candidate_id=candidate_id)
        if latest_resume is None:
            return None

        storage = self._get_storage()
        url = await storage.signed_url(
            latest_resume.file_storage_key,
            expires=RESUME_SIGNED_URL_EXPIRE_SECONDS,
            method="GET",
        )
        expires_at = datetime.now(timezone.utc) + timedelta(
            seconds=RESUME_SIGNED_URL_EXPIRE_SECONDS
        )

        return ResumeUrlResponse(
            url=url,
            expires_at=expires_at,
            mime_type=latest_resume.file_mime,
            filename=None,
        )

    # ========================================================================
    # list_activity
    # ========================================================================

    async def list_activity(
        self,
        *,
        team_id: uuid.UUID,
        candidate_id: uuid.UUID,
        page: int = 1,
        page_size: int = DEFAULT_ACTIVITY_PAGE_SIZE,
    ) -> tuple[list[CandidateActivityItem], int] | None:
        """UNION audit_logs + manual_overrides 按时间倒序。

        - audit: 通过 candidate.team_id → audit_logs.actor_id → users.team_id
        - override: 通过 screening_result.candidate_id → candidate.team_id

        Returns:
            (items, total) | None（跨 team → None）
        """
        candidate = await self._get_candidate_in_team(
            team_id=team_id, candidate_id=candidate_id
        )
        if candidate is None:
            return None

        page = max(1, page)
        page_size = max(1, min(MAX_ACTIVITY_PAGE_SIZE, page_size))

        # ----- audit 子查询：actor 所在 team 匹配 + target 是该 candidate -----
        audit_stmt = (
            select(
                literal("audit_log").label("type"),
                AuditLog.id.label("id"),
                AuditLog.created_at.label("created_at"),
                AuditLog.actor_id.label("actor_id"),
                AuditLog.action.label("action"),
                AuditLog.before.label("before"),
                AuditLog.after.label("after"),
            )
            .join(User, User.id == AuditLog.actor_id)
            .where(
                User.team_id == team_id,
                AuditLog.target_type == "candidate",
                AuditLog.target_id == candidate_id,
            )
        )

        # ----- override 子查询：通过 screening_result → candidate -----
        override_stmt = (
            select(
                literal("override").label("type"),
                ManualOverride.id.label("id"),
                ManualOverride.created_at.label("created_at"),
                ManualOverride.actor_id.label("actor_id"),
                literal("screening.override").label("action"),
                ManualOverride.old_value.label("before"),
                ManualOverride.new_value.label("after"),
            )
            .join(
                ScreeningResult,
                ScreeningResult.id == ManualOverride.screening_result_id,
            )
            .where(ScreeningResult.candidate_id == candidate_id)
        )

        union_stmt = union_all(audit_stmt, override_stmt).subquery()

        # 总数
        total = (
            await self._db.execute(select(func.count()).select_from(union_stmt))
        ).scalar_one()

        # 分页：created_at desc + id desc（稳定）
        rows = (
            await self._db.execute(
                select(union_stmt)
                .order_by(
                    union_stmt.c.created_at.desc(),
                    union_stmt.c.id.desc(),
                )
                .offset((page - 1) * page_size)
                .limit(page_size)
            )
        ).all()

        items: list[CandidateActivityItem] = []
        for r in rows:
            action = str(r.action) if r.action else ""
            details = None
            summary = action
            if r.type == "audit_log":
                # audit: 摘要 = action + before/after 关键变更
                summary = _audit_summary(action, r.before, r.after)
                details = {
                    "before": r.before,
                    "after": r.after,
                }
            else:  # override
                summary = _override_summary(r.before, r.after)
                details = {
                    "old_value": r.before,
                    "new_value": r.after,
                }
            items.append(
                CandidateActivityItem(
                    type=r.type,  # type: ignore[arg-type]
                    id=r.id,
                    created_at=r.created_at,
                    actor_id=r.actor_id,
                    action=action,
                    summary=summary,
                    details=details,
                )
            )

        return items, int(total)

    # ========================================================================
    # 内部：fetch 辅助
    # ========================================================================

    async def _fetch_screening(
        self, *, candidate_id: uuid.UUID, job_id: uuid.UUID
    ) -> ScreeningResult | None:
        result = await self._db.execute(
            select(ScreeningResult).where(
                ScreeningResult.candidate_id == candidate_id,
                ScreeningResult.job_id == job_id,
            )
        )
        return result.scalar_one_or_none()

    async def _fetch_score(
        self, *, candidate_id: uuid.UUID, job_id: uuid.UUID
    ) -> Score | None:
        result = await self._db.execute(
            select(Score).where(
                Score.candidate_id == candidate_id,
                Score.job_id == job_id,
            )
        )
        return result.scalar_one_or_none()

    async def _fetch_latest_resume(
        self, *, candidate_id: uuid.UUID
    ) -> CandidateResume | None:
        result = await self._db.execute(
            select(CandidateResume)
            .where(CandidateResume.candidate_id == candidate_id)
            .order_by(CandidateResume.uploaded_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _fetch_latest_source(
        self, *, candidate_id: uuid.UUID
    ) -> CandidateSource | None:
        result = await self._db.execute(
            select(CandidateSource)
            .where(CandidateSource.candidate_id == candidate_id)
            .order_by(CandidateSource.fetched_at.desc())
            .limit(1)
        )
        return result.scalar_one_or_none()

    async def _fetch_parsed_structure(
        self, *, resume_id: uuid.UUID
    ) -> CandidateStructure | None:
        """取 ParsedStructure.data.structure（schema 兼容校验）。"""
        result = await self._db.execute(
            select(ParsedStructure.data)
            .where(ParsedStructure.resume_id == resume_id)
            .limit(1)
        )
        row = result.first()
        if row is None:
            return None
        data = row[0] if row[0] is not None else {}
        if not isinstance(data, dict):
            return None
        structure_data = data.get("structure")
        if not isinstance(structure_data, dict):
            return None
        try:
            return CandidateStructure.model_validate(structure_data)
        except Exception:  # noqa: BLE001
            logger.warning(
                "parsed_structure_schema_mismatch",
                resume_id=str(resume_id),
            )
            return None


# ============================================================================
# 摘要构造（task 24 activity 显示）
# ============================================================================


def _audit_summary(action: str, before: Any, after: Any) -> str:
    """audit_logs 的展示摘要（中文友好）。"""
    if action == "screening.override":
        return "HR 改判候选人"
    if action == "candidate.merge":
        return "候选人合并"
    if action == "candidate.update":
        return "候选人信息更新"
    if action.startswith("screening."):
        return f"筛选操作：{action.split('.', 1)[1]}"
    if action.startswith("candidate."):
        return f"候选人操作：{action.split('.', 1)[1]}"
    return action or "操作"


def _override_summary(old_value: Any, new_value: Any) -> str:
    """manual_override 的展示摘要。"""
    if not isinstance(new_value, dict):
        return "HR 改判"
    new_dq = new_value.get("disqualified")
    if new_dq is True:
        return "HR 改判为淘汰"
    if new_dq is False:
        return "HR 改判为通过"
    return "HR 改判"


__all__ = [
    "CandidateDetailService",
    "RESUME_SIGNED_URL_EXPIRE_SECONDS",
    "DEFAULT_ACTIVITY_PAGE_SIZE",
    "MAX_ACTIVITY_PAGE_SIZE",
]
