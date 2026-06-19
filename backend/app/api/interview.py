"""Interview API 路由（任务 19）：AI 生成面试问题 + 反馈。

端点（base: /api/interview）：
- POST /generate                       首次生成 5-8 题（temperature=0.3）
- POST /regenerate                     重新生成（temperature=0.8，保留历史 batch）
- GET  /questions                      列出某 candidate × job 的题目（默认最新 batch；可选 batch_id）
- GET  /batches                        列出所有 batch（含当前 batch_id + 总题数）
- POST /questions/{question_id}/feedback  写反馈（同 question_id + reviewer_id 二次写覆盖）
- GET  /questions/{question_id}/feedback  列出某题的所有反馈

权限：
- 所有端点要求当前用户 team_id 非空
- 跨 team 资源访问返回 404（不暴露存在性）
- feedback 写入 reviewer_id 强制取当前用户 id（不接受前端传入）

注：题目生成由前端按需触发（score 完成后），不由 score 流程自动触发。
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.core.middleware.error_handler import ForbiddenError, NotFoundError
from app.models.candidate import Candidate
from app.models.interview import InterviewQuestion
from app.models.job import Job
from app.schemas.interview import (
    BatchListResponse,
    BatchResponse,
    FeedbackOut,
    FeedbackRequest,
    FeedbackResponse,
    InterviewQuestionListResponse,
    InterviewQuestionOut,
)
from app.services.interview import InterviewError, InterviewService

logger = get_logger(__name__)

router = APIRouter(prefix="/interview", tags=["interview"])


# ============================================================================
# 局部 schemas
# ============================================================================


class _GenerateBody(BaseModel):
    """生成请求体。"""

    candidate_id: UUID
    job_id: UUID


# ============================================================================
# 工具
# ============================================================================


def _require_team(user) -> UUID:
    if user.team_id is None:
        raise ForbiddenError("当前用户未加入任何团队")
    return UUID(str(user.team_id))


async def _validate_candidate_in_team(
    db, candidate_id: UUID, team_id: UUID
) -> Candidate:
    candidate = await db.get(Candidate, candidate_id)
    if candidate is None or candidate.team_id != team_id:
        raise NotFoundError(
            f"candidate {candidate_id} 不存在或无权访问",
            resource="candidate",
        )
    return candidate


async def _validate_job_in_team(db, job_id: UUID, team_id: UUID) -> Job:
    job = await db.get(Job, job_id)
    if job is None or job.team_id != team_id:
        raise NotFoundError(
            f"job {job_id} 不存在或无权访问",
            resource="job",
        )
    return job


async def _validate_question_in_team(
    db, question_id: UUID, team_id: UUID
) -> InterviewQuestion:
    """通过 candidate JOIN 校验 question 归属 team；跨 team 返回 404。"""
    stmt = (
        select(InterviewQuestion)
        .join(Candidate, Candidate.id == InterviewQuestion.candidate_id)
        .where(InterviewQuestion.id == question_id, Candidate.team_id == team_id)
    )
    q = (await db.execute(stmt)).scalar_one_or_none()
    if q is None:
        raise NotFoundError(
            f"interview question {question_id} 不存在或无权访问",
            resource="interview_question",
        )
    return q


def _question_to_out(q: InterviewQuestion) -> InterviewQuestionOut:
    """InterviewQuestion 行 → InterviewQuestionOut（feedback 由 list_feedback 单独取）。"""
    return InterviewQuestionOut(
        id=q.id,
        candidate_id=q.candidate_id,
        job_id=q.job_id,
        batch_id=q.batch_id,
        dimension=q.dimension,  # type: ignore[arg-type]
        question=q.question,
        sort_order=q.sort_order,
        generated_by=q.generated_by,
    )


# ============================================================================
# POST /interview/generate
# ============================================================================


@router.post(
    "/generate",
    response_model=BatchResponse,
    status_code=status.HTTP_200_OK,
)
async def generate_questions(
    payload: _GenerateBody,
    user: CurrentUser,
    db: DbSession,
) -> BatchResponse:
    """首次生成面试问题（temperature=0.3）。"""
    team_id = _require_team(user)
    await _validate_candidate_in_team(db, payload.candidate_id, team_id)
    await _validate_job_in_team(db, payload.job_id, team_id)

    service = InterviewService(db)
    try:
        result = await service.generate(
            candidate_id=payload.candidate_id,
            job_id=payload.job_id,
        )
    except InterviewError as exc:
        logger.warning(
            "interview_generate_failed",
            candidate_id=str(payload.candidate_id),
            job_id=str(payload.job_id),
            error=str(exc)[:200],
        )
        raise NotFoundError(
            f"面试题生成失败：{exc}",
            resource="interview",
        ) from exc

    questions = await service.list_batch(
        candidate_id=payload.candidate_id,
        job_id=payload.job_id,
        batch_id=result.batch_id,
    )

    await db.commit()

    return BatchResponse(
        batch_id=result.batch_id,
        questions=[_question_to_out(q) for q in questions],
        is_regeneration=result.is_regeneration,
        temperature=result.temperature,
    )


# ============================================================================
# POST /interview/regenerate
# ============================================================================


@router.post(
    "/regenerate",
    response_model=BatchResponse,
    status_code=status.HTTP_200_OK,
)
async def regenerate_questions(
    payload: _GenerateBody,
    user: CurrentUser,
    db: DbSession,
) -> BatchResponse:
    """重新生成面试问题（temperature=0.8，保留历史 batch）。"""
    team_id = _require_team(user)
    await _validate_candidate_in_team(db, payload.candidate_id, team_id)
    await _validate_job_in_team(db, payload.job_id, team_id)

    service = InterviewService(db)
    try:
        result = await service.regenerate(
            candidate_id=payload.candidate_id,
            job_id=payload.job_id,
        )
    except InterviewError as exc:
        logger.warning(
            "interview_regenerate_failed",
            candidate_id=str(payload.candidate_id),
            job_id=str(payload.job_id),
            error=str(exc)[:200],
        )
        raise NotFoundError(
            f"面试题重新生成失败：{exc}",
            resource="interview",
        ) from exc

    questions = await service.list_batch(
        candidate_id=payload.candidate_id,
        job_id=payload.job_id,
        batch_id=result.batch_id,
    )

    await db.commit()

    return BatchResponse(
        batch_id=result.batch_id,
        questions=[_question_to_out(q) for q in questions],
        is_regeneration=result.is_regeneration,
        temperature=result.temperature,
    )


# ============================================================================
# GET /interview/questions
# ============================================================================


@router.get(
    "/questions",
    response_model=InterviewQuestionListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_questions(
    user: CurrentUser,
    db: DbSession,
    candidate_id: UUID = Query(...),
    job_id: UUID = Query(...),
    batch_id: UUID | None = Query(default=None),
) -> InterviewQuestionListResponse:
    """列出某 candidate × job 的题目。

    - 不传 ``batch_id`` → 返回最新 batch
    - 传 ``batch_id`` → 返回指定 batch
    """
    team_id = _require_team(user)
    await _validate_candidate_in_team(db, candidate_id, team_id)
    await _validate_job_in_team(db, job_id, team_id)

    service = InterviewService(db)

    if batch_id is None:
        rows, _resolved = await service.list_latest_batch(
            candidate_id=candidate_id, job_id=job_id
        )
    else:
        rows = await service.list_batch(
            candidate_id=candidate_id,
            job_id=job_id,
            batch_id=batch_id,
        )

    return InterviewQuestionListResponse(
        items=[_question_to_out(q) for q in rows],
        total=len(rows),
    )


# ============================================================================
# GET /interview/batches
# ============================================================================


@router.get(
    "/batches",
    response_model=BatchListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_batches(
    user: CurrentUser,
    db: DbSession,
    candidate_id: UUID = Query(...),
    job_id: UUID = Query(...),
) -> BatchListResponse:
    """列出某 candidate × job 的所有 batch。"""
    team_id = _require_team(user)
    await _validate_candidate_in_team(db, candidate_id, team_id)
    await _validate_job_in_team(db, job_id, team_id)

    service = InterviewService(db)
    batches, current, total = await service.list_batches(
        candidate_id=candidate_id, job_id=job_id
    )
    return BatchListResponse(
        batches=batches,
        current_batch=current,
        total_questions=total,
    )


# ============================================================================
# POST /interview/questions/{question_id}/feedback
# ============================================================================


@router.post(
    "/questions/{question_id}/feedback",
    response_model=FeedbackResponse,
    status_code=status.HTTP_200_OK,
)
async def save_feedback(
    question_id: UUID,
    payload: FeedbackRequest,
    user: CurrentUser,
    db: DbSession,
) -> FeedbackResponse:
    """写反馈；同 question_id + reviewer_id 二次写覆盖。

    reviewer_id 强制取当前用户 id（不接受前端传入）。
    """
    team_id = _require_team(user)
    await _validate_question_in_team(db, question_id, team_id)

    service = InterviewService(db)
    feedback, question = await service.save_feedback(
        question_id=question_id,
        reviewer_id=user.id,
        payload=payload,
    )
    await db.commit()

    return FeedbackResponse(
        feedback=FeedbackOut.model_validate(feedback),
        question=_question_to_out(question),
    )


# ============================================================================
# GET /interview/questions/{question_id}/feedback
# ============================================================================


@router.get(
    "/questions/{question_id}/feedback",
    response_model=list[FeedbackOut],
    status_code=status.HTTP_200_OK,
)
async def list_feedback(
    question_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> list[FeedbackOut]:
    """列出某题的所有反馈（按时间倒序）。"""
    team_id = _require_team(user)
    await _validate_question_in_team(db, question_id, team_id)

    service = InterviewService(db)
    rows = await service.list_feedback(question_id=question_id)
    return [FeedbackOut.model_validate(r) for r in rows]


__all__ = ["router"]
