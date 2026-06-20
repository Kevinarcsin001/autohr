"""候选人 / 去重 API 路由（任务 15）+ 详情聚合（任务 24）。

端点（base: /api/candidates）：
- GET    /dedup-matches                 列出当前 team pending 待审 match
- POST   /merge                         合并 src → dst
- PATCH  /dedup-matches/{match_id}      HR 决议 pending match（merged / rejected）
- GET    /{candidate_id}/detail         详情聚合（candidate + screening + score + structure + resume）
- GET    /{candidate_id}/resume-url     签名 URL（5min 过期）
- GET    /{candidate_id}/activity       活动时间线（audit_logs + manual_overrides UNION）

权限：
- 所有端点要求当前用户 team_id 非空
- 跨 team 资源访问返回 404（不暴露存在性）
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.middleware.error_handler import ForbiddenError, NotFoundError
from app.models.candidate import Candidate
from app.models.dedup import DedupMatch
from app.schemas.candidate_detail import (
    CandidateActivityListResponse,
    CandidateDetailResponse,
    ResumeUrlResponse,
)
from app.schemas.dedup import (
    DedupDecisionRequest,
    DedupMatchListItem,
    DedupMatchListResponse,
    MergeRequest,
    MergeResponse,
)
from app.services.candidate_detail import (
    DEFAULT_ACTIVITY_PAGE_SIZE,
    MAX_ACTIVITY_PAGE_SIZE,
    CandidateDetailService,
)
from app.services.dedup import DedupService

router = APIRouter(prefix="/candidates", tags=["candidates"])


def _require_team(user) -> UUID:
    if user.team_id is None:
        raise ForbiddenError("当前用户未加入任何团队")
    return UUID(str(user.team_id))


# ============================================================================
# dedup_match 列表
# ============================================================================


@router.get("/dedup-matches", response_model=DedupMatchListResponse)
async def list_dedup_matches(
    user: CurrentUser,
    db: DbSession,
    status_filter: str | None = Query(
        default="pending", pattern="^(pending|merged|rejected|all)$"
    ),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> DedupMatchListResponse:
    """列出当前 team 的 dedup_matches。"""
    team_id = _require_team(user)
    service = DedupService(db)

    limit = page_size
    offset = (page - 1) * page_size
    matches = await service.list_pending_matches(
        team_id=team_id, limit=limit, offset=offset
    )

    # 取候选人姓名
    candidate_ids = set()
    for m in matches:
        candidate_ids.add(m.candidate_a)
        candidate_ids.add(m.candidate_b)
    names: dict[UUID, str] = {}
    if candidate_ids:
        result = await db.execute(
            select(Candidate.id, Candidate.name).where(
                Candidate.id.in_(candidate_ids)
            )
        )
        for cid, name in result.all():
            names[cid] = name

    items = [
        DedupMatchListItem(
            id=m.id,
            candidate_a=m.candidate_a,
            candidate_b=m.candidate_b,
            name_a=names.get(m.candidate_a),
            name_b=names.get(m.candidate_b),
            similarity=m.similarity,
            status=m.status,
        )
        for m in matches
    ]
    return DedupMatchListResponse(items=items, total=len(items))


# ============================================================================
# 合并
# ============================================================================


@router.post("/merge", response_model=MergeResponse, status_code=status.HTTP_200_OK)
async def merge_candidates(
    payload: MergeRequest,
    user: CurrentUser,
    db: DbSession,
) -> MergeResponse:
    """合并 src → dst。

    把 src 的所有 sources/resumes 转移到 dst，src.merged_into 指向 dst。
    主字段（name/phone/email）按 ParsedStructure confidence 比较，若 src 更高则覆盖。
    """
    _require_team(user)
    service = DedupService(db)

    # 校验：src/dst 必须属于当前 team（跨 team 不暴露存在性）
    for cid in (payload.src_id, payload.dst_id):
        c = await db.get(Candidate, cid)
        if c is None or c.team_id != UUID(str(user.team_id)):
            raise NotFoundError(
                f"candidate {cid} 不存在或无权访问", resource="candidate"
            )

    sources_moved, resumes_moved, fields_updated = await service.merge(
        src_id=payload.src_id, dst_id=payload.dst_id
    )
    await db.commit()

    return MergeResponse(
        merged_id=payload.dst_id,
        archived_id=payload.src_id,
        sources_moved=sources_moved,
        resumes_moved=resumes_moved,
        fields_updated=fields_updated,
    )


# ============================================================================
# 决议 dedup_match
# ============================================================================


@router.patch(
    "/dedup-matches/{match_id}",
    response_model=dict,
    status_code=status.HTTP_200_OK,
)
async def decide_dedup_match(
    match_id: UUID,
    payload: DedupDecisionRequest,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    """HR 决议 pending dedup_match。

    - decision='merged'：合并 candidate_b → candidate_a，match.status='merged'
    - decision='rejected'：仅置 status='rejected'
    """
    _require_team(user)
    service = DedupService(db)

    # 校验 match 归属 team（通过 candidate_a 反查）
    match = await db.get(DedupMatch, match_id)
    if match is not None:
        ca = await db.get(Candidate, match.candidate_a)
        if ca is None or ca.team_id != UUID(str(user.team_id)):
            match = None
    if match is None:
        raise NotFoundError(
            f"dedup_match {match_id} 不存在或无权访问",
            resource="dedup_match",
        )

    updated = await service.decide_match(
        match_id=match_id,
        decision=payload.decision,
        actor_id=user.id,
    )
    await db.commit()

    return {
        "id": str(updated.id),
        "status": updated.status,
        "decided_by": str(updated.decided_by) if updated.decided_by else None,
    }


# ============================================================================
# 任务 24：候选人详情聚合
# ============================================================================


@router.get(
    "/{candidate_id}/detail",
    response_model=CandidateDetailResponse,
)
async def get_candidate_detail(
    candidate_id: UUID,
    user: CurrentUser,
    db: DbSession,
    job_id: UUID | None = Query(
        default=None, description="所属 job（可选；不传则取最新 screening/score）"
    ),
) -> CandidateDetailResponse:
    """聚合查询候选人详情（candidate + screening + score + structure + resume）。"""
    team_id = _require_team(user)
    service = CandidateDetailService(db)
    detail = await service.get_detail(
        team_id=team_id,
        candidate_id=candidate_id,
        job_id=job_id,
    )
    if detail is None:
        raise NotFoundError(
            f"candidate {candidate_id} 不存在或无权访问",
            resource="candidate",
        )
    return detail


# ============================================================================
# 任务 24：签名 URL（resume 预览用）
# ============================================================================


@router.get(
    "/{candidate_id}/resume-url",
    response_model=ResumeUrlResponse,
)
async def get_candidate_resume_url(
    candidate_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> ResumeUrlResponse:
    """取最新 resume 的 5min 签名 URL。"""
    team_id = _require_team(user)
    service = CandidateDetailService(db)
    result = await service.get_resume_url(
        team_id=team_id,
        candidate_id=candidate_id,
    )
    if result is None:
        raise NotFoundError(
            f"candidate {candidate_id} 不存在或无 resume 可访问",
            resource="candidate_resume",
        )
    return result


# ============================================================================
# 任务 24：活动时间线（audit + override UNION）
# ============================================================================


@router.get(
    "/{candidate_id}/activity",
    response_model=CandidateActivityListResponse,
)
async def list_candidate_activity(
    candidate_id: UUID,
    user: CurrentUser,
    db: DbSession,
    page: int = Query(default=1, ge=1),
    page_size: int = Query(
        default=DEFAULT_ACTIVITY_PAGE_SIZE, ge=1, le=MAX_ACTIVITY_PAGE_SIZE
    ),
) -> CandidateActivityListResponse:
    """取候选人的活动时间线（audit_logs + manual_overrides UNION 按时间倒序）。"""
    team_id = _require_team(user)
    service = CandidateDetailService(db)
    result = await service.list_activity(
        team_id=team_id,
        candidate_id=candidate_id,
        page=page,
        page_size=page_size,
    )
    if result is None:
        raise NotFoundError(
            f"candidate {candidate_id} 不存在或无权访问",
            resource="candidate",
        )
    items, total = result
    return CandidateActivityListResponse(
        items=items,
        total=total,
        page=page,
        page_size=page_size,
    )


__all__ = ["router"]
