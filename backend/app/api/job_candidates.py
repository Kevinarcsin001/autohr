"""Job 内候选人列表 API 路由（任务 23）。

端点（base: /api/jobs）：
- GET /{job_id}/candidates  聚合查询候选人列表（含 score/screening/structure）

支持查询参数：
- group: all/passed/disqualified/pending
- min_score / max_score: 评分区间
- education: high_school/bachelor/master/phd
- min_years / max_years: 工作年限
- skill: 技能子串（不区分大小写）
- source: upload/platform/email
- sort_by: total/skill/experience/education/stability/potential/name
- sort_order: asc/desc
- page / page_size: 分页（默认 1 / 50）

权限：
- 要求当前用户 team_id 非空
- 跨 team 访问 → 404
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser, DbSession
from app.core.middleware.error_handler import ForbiddenError, NotFoundError
from app.models.job import Job
from app.schemas.candidate_list import (
    CandidateListFilters,
    CandidateListResponse,
)
from app.services.candidate_list import DEFAULT_PAGE_SIZE, CandidateListService

router = APIRouter(prefix="/jobs", tags=["job-candidates"])


def _require_team(user) -> UUID:
    if user.team_id is None:
        raise ForbiddenError("当前用户未加入任何团队")
    return UUID(str(user.team_id))


async def _validate_job_in_team(db, job_id: UUID, team_id: UUID) -> Job:
    job = await db.get(Job, job_id)
    if job is None or job.team_id != team_id:
        raise NotFoundError(f"job {job_id} 不存在或无权访问", resource="job")
    return job


# ============================================================================
# GET /api/jobs/{job_id}/candidates
# ============================================================================


@router.get(
    "/{job_id}/candidates",
    response_model=CandidateListResponse,
)
async def list_job_candidates(
    job_id: UUID,
    user: CurrentUser,
    db: DbSession,
    group: str = Query(default="all", pattern="^(all|passed|disqualified|pending)$"),
    min_score: int | None = Query(default=None, ge=0, le=100),
    max_score: int | None = Query(default=None, ge=0, le=100),
    education: str | None = Query(
        default=None, pattern="^(high_school|bachelor|master|phd|other)$"
    ),
    min_years: int | None = Query(default=None, ge=0, le=80),
    max_years: int | None = Query(default=None, ge=0, le=80),
    skill: str | None = Query(default=None, max_length=100),
    source: str | None = Query(
        default=None, pattern="^(upload|platform|email)$"
    ),
    sort_by: str = Query(
        default="total",
        pattern="^(total|skill|experience|education|stability|potential|name)$",
    ),
    sort_order: str = Query(default="desc", pattern="^(asc|desc)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=DEFAULT_PAGE_SIZE, ge=1, le=200),
) -> CandidateListResponse:
    """聚合查询 job 内候选人列表。"""
    team_id = _require_team(user)
    await _validate_job_in_team(db, job_id, team_id)

    filters = CandidateListFilters(
        group=group,  # type: ignore[arg-type]
        min_score=min_score,
        max_score=max_score,
        education=education,
        min_years=min_years,
        max_years=max_years,
        skill=skill,
        source=source,
        sort_by=sort_by,  # type: ignore[arg-type]
        sort_order=sort_order,  # type: ignore[arg-type]
        page=page,
        page_size=page_size,
    )

    service = CandidateListService(db)
    items, total, group_counts = await service.list_for_job(
        team_id=team_id,
        job_id=job_id,
        filters=filters,
    )
    return CandidateListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
        group_counts=group_counts,
    )


__all__ = ["router"]
