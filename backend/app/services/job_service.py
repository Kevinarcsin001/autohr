"""JobService：职位 CRUD + 版本快照 + 硬性条件。

业务规则（任务 7 / 需求 2）：
- ``create_job``：team 隔离；写 v1 快照；同步初始化 JobHardRequirement（即使全空也建记录，便于后续 update）
- ``update_job``：仅 team 内成员可改；写新版本快照（含 before/after diff 与完整 snapshot）；
  不会触发评分重算（已存在的 screening_results / scores 保留原值）
- ``list_jobs``：team 隔离；status 过滤；分页（默认 20/页）；按 updated_at 倒序
- ``get_job``：含当前 hard_requirements
- ``list_versions``：返回所有历史快照（按 version 倒序）

事务策略：service 接收 session，不自行 commit。
"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.middleware.error_handler import (
    ForbiddenError,
    NotFoundError,
)
from app.models.job import Job, JobHardRequirement, JobVersion
from app.models.user import User
from app.schemas.job import (
    HardRequirements,
    JobCreateRequest,
    JobUpdateRequest,
)

# ============================================================================
# 默认分页
# ============================================================================

DEFAULT_PAGE_SIZE = 20
MAX_PAGE_SIZE = 100


# ============================================================================
# 工具
# ============================================================================


def _hard_requirements_to_dict(h: HardRequirements) -> dict[str, Any]:
    """转 dict 用于 snapshot 与 ORM 写入。"""
    return {
        "min_education": h.min_education,
        "min_years": h.min_years,
        "required_skills": h.required_skills,
        "excluded_companies": h.excluded_companies,
    }


def _make_snapshot(job: Job, hard: JobHardRequirement) -> dict[str, Any]:
    """构造完整快照（用于 job_versions.snapshot）。"""
    return {
        "title": job.title,
        "jd_text": job.jd_text,
        "status": job.status,
        "llm_config": job.llm_config,
        "hard_requirements": {
            "min_education": hard.min_education,
            "min_years": hard.min_years,
            "required_skills": hard.required_skills,
            "excluded_companies": hard.excluded_companies,
        },
    }


async def _get_job_or_404(db: AsyncSession, job_id: UUID) -> Job:
    result = await db.execute(select(Job).where(Job.id == job_id))
    job = result.scalar_one_or_none()
    if job is None:
        raise NotFoundError("职位不存在", resource="job", job_id=str(job_id))
    return job


async def get_hard_requirements(
    db: AsyncSession, job_id: UUID
) -> JobHardRequirement:
    """获取职位的硬性条件记录；缺失则补建空记录。"""
    result = await db.execute(
        select(JobHardRequirement).where(JobHardRequirement.job_id == job_id)
    )
    req = result.scalar_one_or_none()
    if req is None:
        # 兜底：理论上 create_job 已建，但若历史数据缺失则补一条空记录
        req = JobHardRequirement(job_id=job_id)
        db.add(req)
        await db.flush()
    return req


def _assert_team_scope(job: Job, user: User) -> None:
    """确保 job 属于用户 team。"""
    if user.team_id is None or job.team_id != user.team_id:
        raise ForbiddenError(
            "无权访问该职位",
            job_id=str(job.id),
            user_team_id=str(user.team_id) if user.team_id else None,
        )


# ============================================================================
# create
# ============================================================================


async def create_job(
    db: AsyncSession,
    *,
    team_id: UUID,
    created_by: UUID,
    payload: JobCreateRequest,
) -> Job:
    """创建职位：写 v1 + 快照 + hard_requirements。"""
    job = Job(
        team_id=team_id,
        title=payload.title,
        jd_text=payload.jd_text,
        status=payload.status,
        llm_config=payload.llm_config,
        current_version=1,
        created_by=created_by,
    )
    db.add(job)
    await db.flush()  # 拿 id

    # 初始化硬性条件（即使全空也建记录，避免后续 update 漏建）
    hard_data = _hard_requirements_to_dict(payload.hard_requirements)
    hard = JobHardRequirement(job_id=job.id, **hard_data)
    db.add(hard)
    await db.flush()

    # v1 快照
    snapshot = _make_snapshot(job, hard)
    version = JobVersion(
        job_id=job.id,
        version=1,
        snapshot=snapshot,
        changed_by=created_by,
    )
    db.add(version)
    await db.flush()
    return job


# ============================================================================
# update
# ============================================================================


async def update_job(
    db: AsyncSession,
    *,
    job_id: UUID,
    actor: User,
    payload: JobUpdateRequest,
) -> Job:
    """更新职位 + 写新版本快照。

    - 任意字段未传（None）则保留原值
    - hard_requirements 整体替换（None 表示清空所有字段，但不算"未传"）
    - current_version 自增 1
    - 不触发评分重算
    """
    job = await _get_job_or_404(db, job_id)
    _assert_team_scope(job, actor)

    # 应用字段更新
    if payload.title is not None:
        job.title = payload.title
    if payload.jd_text is not None:
        job.jd_text = payload.jd_text
    if payload.status is not None:
        job.status = payload.status
    if payload.llm_config is not None:
        job.llm_config = payload.llm_config

    hard = await get_hard_requirements(db, job.id)
    if payload.hard_requirements is not None:
        new_hard = _hard_requirements_to_dict(payload.hard_requirements)
        hard.min_education = new_hard["min_education"]
        hard.min_years = new_hard["min_years"]
        hard.required_skills = new_hard["required_skills"]
        hard.excluded_companies = new_hard["excluded_companies"]

    job.current_version += 1
    await db.flush()
    # onupdate=func.now() 让 updated_at 在 UPDATE 后被 expire；
    # asyncpg 默认 RETURNING 仅 INSERT，需要显式 refresh 避免 sync 序列化时触发懒加载（MissingGreenlet）
    await db.refresh(job, attribute_names=["updated_at"])

    snapshot = _make_snapshot(job, hard)
    version = JobVersion(
        job_id=job.id,
        version=job.current_version,
        snapshot=snapshot,
        changed_by=actor.id,
    )
    db.add(version)
    await db.flush()
    return job


# ============================================================================
# get / list
# ============================================================================


async def get_job(db: AsyncSession, *, job_id: UUID, actor: User) -> Job:
    job = await _get_job_or_404(db, job_id)
    _assert_team_scope(job, actor)
    return job


async def list_jobs(
    db: AsyncSession,
    *,
    team_id: UUID,
    status_filter: str | None = None,
    page: int = 1,
    page_size: int = DEFAULT_PAGE_SIZE,
) -> tuple[list[Job], int]:
    """分页列表，返回 (items, total)。"""
    page = max(1, page)
    page_size = max(1, min(MAX_PAGE_SIZE, page_size))

    stmt = select(Job).where(Job.team_id == team_id)
    if status_filter:
        stmt = stmt.where(Job.status == status_filter)

    # count
    count_stmt = select(func.count()).select_from(Job).where(Job.team_id == team_id)
    if status_filter:
        count_stmt = count_stmt.where(Job.status == status_filter)
    total = (await db.execute(count_stmt)).scalar_one()

    # page
    stmt = (
        stmt.order_by(Job.updated_at.desc())
        .offset((page - 1) * page_size)
        .limit(page_size)
    )
    items = list((await db.execute(stmt)).scalars().all())
    return items, total


async def list_versions(
    db: AsyncSession, *, job_id: UUID
) -> list[JobVersion]:
    """所有版本快照（按 version 倒序）。"""
    result = await db.execute(
        select(JobVersion)
        .where(JobVersion.job_id == job_id)
        .order_by(JobVersion.version.desc())
    )
    return list(result.scalars().all())


# ============================================================================
# 删除
# ============================================================================


async def delete_job(db: AsyncSession, *, job_id: UUID, actor: User) -> None:
    """删除职位（CASCADE 级联删 versions / hard_requirements / screening_results）。

    业务规则：仅 team 成员可删除；建议前端二次确认。
    """
    job = await _get_job_or_404(db, job_id)
    _assert_team_scope(job, actor)
    await db.delete(job)
    await db.flush()


__all__ = [
    "DEFAULT_PAGE_SIZE",
    "MAX_PAGE_SIZE",
    "create_job",
    "update_job",
    "get_job",
    "list_jobs",
    "list_versions",
    "delete_job",
]
