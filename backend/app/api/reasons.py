"""Reasons API 路由（任务 18）：列出推荐/淘汰理由。

端点（base: /api/reasons）：
- GET /by-score/{score_id}    列出某 Score 的所有理由
- GET /by-job/{job_id}        列出某 Job 下所有理由（JOIN Score）

权限：
- 所有端点要求当前用户 team_id 非空
- 跨 team 资源访问返回 404

注：理由生成由评分流程（任务 17 ScorerService 调用 + 任务 18 ReasoningService 持久化）
触发，本端点只做读取。
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.middleware.error_handler import ForbiddenError, NotFoundError
from app.models.candidate import Candidate
from app.models.score import Score
from app.schemas.reason import ReasonListResponse, ReasonOut
from app.services.reasoning import ReasoningService

router = APIRouter(prefix="/reasons", tags=["reasons"])


def _require_team(user) -> UUID:
    if user.team_id is None:
        raise ForbiddenError("当前用户未加入任何团队")
    return UUID(str(user.team_id))


async def _validate_score_in_team(db, score_id: UUID, team_id: UUID) -> Score:
    """校验 score 归属 team；跨 team 返回 404。"""
    stmt = (
        select(Score)
        .join(Candidate, Candidate.id == Score.candidate_id)
        .where(Score.id == score_id, Candidate.team_id == team_id)
    )
    score = (await db.execute(stmt)).scalar_one_or_none()
    if score is None:
        raise NotFoundError(f"score {score_id} 不存在或无权访问", resource="score")
    return score


# ============================================================================
# GET /reasons/by-score/{score_id}
# ============================================================================


@router.get(
    "/by-score/{score_id}",
    response_model=ReasonListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_by_score(
    score_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> ReasonListResponse:
    """列出某 Score 的所有理由（recommend + disqualify）。"""
    team_id = _require_team(user)
    await _validate_score_in_team(db, score_id, team_id)

    service = ReasoningService(db)
    rows = await service.list_by_score(score_id=score_id)
    items = [ReasonOut.model_validate(r) for r in rows]
    return ReasonListResponse(items=items, total=len(items))


# ============================================================================
# GET /reasons/by-job/{job_id}
# ============================================================================


@router.get(
    "/by-job/{job_id}",
    response_model=ReasonListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_by_job(
    job_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> ReasonListResponse:
    """列出某 Job 下所有 score 的理由。"""
    team_id = _require_team(user)
    # 校验 job 归属（直接查 Score 是否有此 job_id + team 一致即可）
    from app.models.job import Job

    job = await db.get(Job, job_id)
    if job is None or job.team_id != team_id:
        raise NotFoundError(f"job {job_id} 不存在或无权访问", resource="job")

    service = ReasoningService(db)
    rows = await service.list_by_job(job_id=job_id)
    items = [ReasonOut.model_validate(r) for r, _score in rows]
    return ReasonListResponse(items=items, total=len(items))
