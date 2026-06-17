"""职位（JD）API 路由（任务 7）。

端点（base: /api/jobs）：
- POST   /                  创建职位（team 内任意成员）
- GET    /                  分页列表（支持 status 过滤）
- GET    /{job_id}          职位详情（含 hard_requirements）
- PATCH  /{job_id}          更新（写新版本快照）
- DELETE /{job_id}          删除（CASCADE）
- GET    /{job_id}/versions 版本历史

权限：
- 所有端点要求当前用户 team_id 非空
- 跨 team 访问返回 403
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.core.deps import CurrentUser, DbSession
from app.core.middleware.error_handler import ForbiddenError
from app.models.job import JobHardRequirement
from app.schemas.job import (
    HardRequirements,
    JobCreateRequest,
    JobListItem,
    JobListResponse,
    JobOut,
    JobUpdateRequest,
    JobVersionOut,
)
from app.services import job_service

router = APIRouter(prefix="/jobs", tags=["jobs"])


def _require_team(user) -> UUID:
    """要求 user.team_id 非空，返回 UUID。"""
    if user.team_id is None:
        raise ForbiddenError("当前用户未加入任何团队，无法管理职位")
    return UUID(str(user.team_id))


def _hard_to_schema(h: JobHardRequirement) -> HardRequirements:
    return HardRequirements(
        min_education=h.min_education,
        min_years=h.min_years,
        required_skills=h.required_skills,
        excluded_companies=h.excluded_companies,
    )


def _job_to_out(job, hard: JobHardRequirement) -> JobOut:
    return JobOut(
        id=str(job.id),
        team_id=str(job.team_id),
        title=job.title,
        jd_text=job.jd_text,
        status=job.status,
        current_version=job.current_version,
        llm_config=job.llm_config,
        hard_requirements=_hard_to_schema(hard),
        created_by=str(job.created_by),
        created_at=job.created_at.isoformat() if job.created_at else "",
        updated_at=job.updated_at.isoformat() if job.updated_at else "",
    )


@router.post("/", response_model=JobOut, status_code=status.HTTP_201_CREATED)
async def create_job(
    payload: JobCreateRequest,
    user: CurrentUser,
    db: DbSession,
) -> JobOut:
    """创建职位。"""
    team_id = _require_team(user)
    job = await job_service.create_job(
        db,
        team_id=team_id,
        created_by=user.id,
        payload=payload,
    )
    hard = await job_service.get_hard_requirements(db, job.id)
    return _job_to_out(job, hard)


@router.get("/", response_model=JobListResponse)
async def list_jobs(
    user: CurrentUser,
    db: DbSession,
    status: str | None = Query(default=None, pattern="^(draft|active|closed)$"),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=job_service.DEFAULT_PAGE_SIZE, ge=1, le=job_service.MAX_PAGE_SIZE),
) -> JobListResponse:
    """分页列表。"""
    team_id = _require_team(user)
    items, total = await job_service.list_jobs(
        db,
        team_id=team_id,
        status_filter=status,
        page=page,
        page_size=page_size,
    )
    return JobListResponse(
        items=[
            JobListItem(
                id=str(j.id),
                title=j.title,
                status=j.status,
                current_version=j.current_version,
                created_at=j.created_at.isoformat() if j.created_at else "",
                updated_at=j.updated_at.isoformat() if j.updated_at else "",
            )
            for j in items
        ],
        page=page,
        page_size=page_size,
        total=total,
    )


@router.get("/{job_id}", response_model=JobOut)
async def get_job(
    job_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> JobOut:
    """获取职位详情。"""
    job = await job_service.get_job(db, job_id=job_id, actor=user)
    hard = await job_service.get_hard_requirements(db, job.id)
    return _job_to_out(job, hard)


@router.patch("/{job_id}", response_model=JobOut)
async def update_job(
    job_id: UUID,
    payload: JobUpdateRequest,
    user: CurrentUser,
    db: DbSession,
) -> JobOut:
    """更新职位（写新版本快照）。"""
    job = await job_service.update_job(
        db,
        job_id=job_id,
        actor=user,
        payload=payload,
    )
    hard = await job_service.get_hard_requirements(db, job.id)
    return _job_to_out(job, hard)


@router.delete("/{job_id}", status_code=status.HTTP_204_NO_CONTENT)
async def delete_job(
    job_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> None:
    """删除职位（CASCADE）。"""
    await job_service.delete_job(db, job_id=job_id, actor=user)


@router.get("/{job_id}/versions", response_model=list[JobVersionOut])
async def list_job_versions(
    job_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> list[JobVersionOut]:
    """列出职位所有历史版本快照（按 version 倒序）。"""
    # 校验权限（确保 job 属于当前 user 的 team）
    await job_service.get_job(db, job_id=job_id, actor=user)
    versions = await job_service.list_versions(db, job_id=job_id)
    return [
        JobVersionOut(
            id=str(v.id),
            job_id=str(v.job_id),
            version=v.version,
            snapshot=v.snapshot,
            changed_by=str(v.changed_by) if v.changed_by else None,
            changed_at=v.changed_at.isoformat() if v.changed_at else "",
        )
        for v in versions
    ]


__all__ = ["router"]
