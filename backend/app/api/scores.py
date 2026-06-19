"""Scores API 路由（任务 17）：综合评分列表 + 触发评分。

端点（base: /api/scores）：
- POST  /run                       对指定 job 跑评分（同步，仅用于已就绪候选人）
- GET   /?job_id=&page=&size=      分页列出评分（带候选人姓名，按需求 9.3 排序）

权限：
- 所有端点要求当前用户 team_id 非空
- 跨 team 资源访问返回 404

注：异步触发（celery 入队）由 ``/api/screening/run`` 协调或后续任务接入；
本端点 ``/run`` 走同步路径，仅用于测试 / 小批量场景。
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.core.middleware.error_handler import ForbiddenError, NotFoundError
from app.models.candidate import (
    Candidate,
    CandidateResume,
    ParsedStructure,
)
from app.models.job import Job
from app.schemas.candidate_structure import CandidateStructure
from app.schemas.score import (
    ScoreListItem,
    ScoreListResponse,
    ScoreRunRequest,
    ScoreRunResponse,
)
from app.services.scorer import ScorerError, ScorerService, ScoringInput, build_scoring_snippet

logger = get_logger(__name__)

router = APIRouter(prefix="/scores", tags=["scores"])


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
# POST /scores/run
# ============================================================================


@router.post(
    "/run",
    response_model=ScoreRunResponse,
    status_code=status.HTTP_200_OK,
)
async def run_scores(
    payload: ScoreRunRequest,
    user: CurrentUser,
    db: DbSession,
) -> ScoreRunResponse:
    """对指定 job 的候选人同步跑评分。

    - 跨 team 的 candidate_id 会被自动过滤
    - 无 ParsedStructure 的候选人跳过
    - 单个评分失败计入 ``failed`` 但不阻塞其他
    """
    team_id = _require_team(user)
    await _validate_job_in_team(db, payload.job_id, team_id)

    # 校验 candidate_ids 都在当前 team
    result = await db.execute(
        select(Candidate).where(
            Candidate.id.in_(payload.candidate_ids),
            Candidate.team_id == team_id,
        )
    )
    valid_candidates = list(result.scalars().all())
    # 过滤有效 candidate；后续 service 会再次按 id 查询，此处仅做 team 校验
    _ = {c.id for c in valid_candidates}

    service = ScorerService(db)

    processed = 0
    failed = 0
    for candidate in valid_candidates:
        try:
            scoring_input = await _build_scoring_input(
                db,
                job_id=payload.job_id,
                candidate=candidate,
            )
        except _SkipCandidate as exc:
            logger.info(
                "scores_run_skip_candidate",
                candidate_id=str(candidate.id),
                reason=str(exc),
            )
            failed += 1
            continue

        if scoring_input is None:
            failed += 1
            continue

        try:
            await service.score(scoring_input)
            processed += 1
        except ScorerError as exc:
            logger.warning(
                "scores_run_item_failed",
                candidate_id=str(candidate.id),
                error=str(exc)[:200],
            )
            failed += 1

    await db.commit()

    return ScoreRunResponse(
        job_id=payload.job_id,
        processed=processed,
        failed=failed,
    )


class _SkipCandidate(Exception):
    """候选人跳过（无 structure / 无 resume）。"""


async def _build_scoring_input(
    db,
    *,
    job_id: UUID,
    candidate: Candidate,
) -> ScoringInput | None:
    """构造 ScoringInput；无结构化数据时抛 _SkipCandidate。"""
    job = await db.get(Job, job_id)
    if job is None:
        raise _SkipCandidate(f"job {job_id} not found")

    stmt_structure = (
        select(ParsedStructure.data, CandidateResume.parsed_text)
        .join(
            CandidateResume,
            CandidateResume.id == ParsedStructure.resume_id,
        )
        .where(CandidateResume.candidate_id == candidate.id)
        .order_by(CandidateResume.uploaded_at.desc())
        .limit(1)
    )
    row = (await db.execute(stmt_structure)).first()
    if row is None:
        raise _SkipCandidate(
            f"candidate {candidate.id} has no ParsedStructure"
        )

    structure_data, parsed_text = row
    inner = structure_data.get("structure") if isinstance(structure_data, dict) else None
    if not isinstance(inner, dict):
        raise _SkipCandidate(
            f"candidate {candidate.id} ParsedStructure malformed"
        )

    try:
        structure = CandidateStructure.model_validate(inner)
    except Exception as exc:
        raise _SkipCandidate(
            f"candidate {candidate.id} structure validation failed: {exc}"
        ) from exc

    snippet = build_scoring_snippet(parsed_text)

    return ScoringInput(
        job_id=job_id,
        candidate_id=candidate.id,
        job_title=job.title,
        jd_text=job.jd_text,
        structure=structure,
        resume_snippet=snippet,
    )


# ============================================================================
# GET /scores
# ============================================================================


@router.get("", response_model=ScoreListResponse)
async def list_scores(
    user: CurrentUser,
    db: DbSession,
    job_id: UUID = Query(...),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> ScoreListResponse:
    """列出 job 的评分（按需求 9.3 排序：total → skill → experience → name）。"""
    team_id = _require_team(user)
    await _validate_job_in_team(db, job_id, team_id)

    service = ScorerService(db)
    limit = page_size
    offset = (page - 1) * page_size
    rows, total = await service.list_by_job(
        job_id=job_id, limit=limit, offset=offset
    )
    items = [
        ScoreListItem(
            id=s.id,
            candidate_id=s.candidate_id,
            candidate_name=name,
            total=s.total,
            skill=s.skill,
            experience=s.experience,
            education=s.education,
            stability=s.stability,
            potential=s.potential,
            model_used=s.model_used,
        )
        for s, name in rows
    ]
    return ScoreListResponse(items=items, total=total)
