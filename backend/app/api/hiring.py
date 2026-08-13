"""Hiring API 路由：AI 录用建议生成与查询。

端点（base: /api/interview/sessions）：
- POST /{session_id}/recommend   生成 AI 录用建议
- GET  /{session_id}/recommend   获取已有建议
"""

from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, status

from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.core.middleware.error_handler import ForbiddenError, NotFoundError
from app.models.candidate import Candidate
from app.models.interview import InterviewSession
from app.schemas.hiring import (
    GenerateRecommendationRequest,
    GenerateRecommendationResponse,
    HiringRecommendationOut,
)
from app.services.hiring import HiringError, HiringRecommendationService

logger = get_logger(__name__)

router = APIRouter(tags=["hiring"])


def _require_team(user) -> UUID:
    if user.team_id is None:
        raise ForbiddenError("当前用户未加入任何团队")
    return UUID(str(user.team_id))


async def _validate_session_in_team(db, session_id: UUID, team_id: UUID) -> InterviewSession:
    session = await db.get(InterviewSession, session_id)
    if session is not None:
        candidate = await db.get(Candidate, session.candidate_id)
        if candidate is not None and candidate.team_id == team_id:
            return session
    raise NotFoundError(
        f"interview session {session_id} 不存在或无权访问",
        resource="interview_session",
    )


@router.post(
    "/sessions/{session_id}/recommend",
    response_model=GenerateRecommendationResponse,
    status_code=status.HTTP_200_OK,
)
async def generate_recommendation(
    session_id: UUID,
    payload: GenerateRecommendationRequest,
    user: CurrentUser,
    db: DbSession,
) -> GenerateRecommendationResponse:
    """生成 AI 录用建议。

    基于简历 + JD + 面试反馈 + 评分，调用 LLM 生成建议。
    已有建议时直接返回现有结果。
    """
    team_id = _require_team(user)
    await _validate_session_in_team(db, session_id, team_id)

    service = HiringRecommendationService(db)
    try:
        recommendation = await service.generate(session_id=session_id)
    except HiringError as exc:
        logger.warning(
            "hiring_generate_failed",
            session_id=str(session_id),
            error=str(exc)[:200],
        )
        raise NotFoundError(
            f"录用建议生成失败：{exc}",
            resource="hiring",
        ) from exc

    await db.commit()

    return GenerateRecommendationResponse(
        recommendation=HiringRecommendationOut.model_validate(recommendation),
    )


@router.get(
    "/sessions/{session_id}/recommend",
    response_model=GenerateRecommendationResponse,
    status_code=status.HTTP_200_OK,
)
async def get_recommendation(
    session_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> GenerateRecommendationResponse:
    """获取已有的录用建议。"""
    team_id = _require_team(user)
    await _validate_session_in_team(db, session_id, team_id)

    service = HiringRecommendationService(db)
    recommendation = await service.get(session_id=session_id)
    if recommendation is None:
        raise NotFoundError(
            "尚未生成录用建议",
            resource="hiring",
        )

    return GenerateRecommendationResponse(
        recommendation=HiringRecommendationOut.model_validate(recommendation),
    )


__all__ = ["router"]
