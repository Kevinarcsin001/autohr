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

import uuid
from pathlib import Path
from typing import Any
from uuid import UUID

from fastapi import APIRouter, File, Form, Query, UploadFile, status
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.core.middleware.error_handler import (
    ForbiddenError,
    NotFoundError,
)
from app.core.middleware.error_handler import (
    ValidationError as AppValidationError,
)
from app.models.candidate import Candidate
from app.models.interview import (
    InterviewFeedback,
    InterviewQuestion,
    InterviewSession,
    InterviewTurn,
)
from app.models.job import Job
from app.schemas.adaptive import (
    AdaptiveAnswerOut,
    AdaptiveAnswerRequest,
    AdaptiveNextOut,
    AdaptiveStartOut,
    AdaptiveStateOut,
)
from app.schemas.interview import (
    BatchFeedbackRequest,
    BatchFeedbackResponse,
    BatchListResponse,
    BatchResponse,
    CreateSessionRequest,
    FeedbackOut,
    FeedbackRequest,
    FeedbackResponse,
    InterviewQuestionListResponse,
    InterviewQuestionOut,
    InterviewSessionListItem,
    InterviewSessionListResponse,
    InterviewSessionOut,
    SessionDetailResponse,
    UpdateSessionRequest,
)
from app.services.adaptive_interview import (
    AdaptiveInterviewService,
)
from app.services.interview import InterviewError, InterviewService
from app.services.question_bank import QuestionBankService

logger = get_logger(__name__)

router = APIRouter(prefix="/interview", tags=["interview"])


# ============================================================================
# 局部 schemas
# ============================================================================


class _GenerateBody(BaseModel):
    """生成请求体。"""

    candidate_id: UUID
    job_id: UUID


class _ComposeRequest(BaseModel):
    """从题库凑分组卷请求（写入当前 session）。"""

    quotas: dict[UUID, int] | None = None
    tolerance: int = 5
    exclude_question_ids: list[UUID] | None = None
    dynamic: bool = True
    """默认 True：按候选人简历 + JD 动态匹配配额（目标约 30 题）。"""


class _ComposeResponse(BaseModel):
    """凑分组卷结果。"""

    batch_id: UUID
    question_count: int
    actual_total: int
    target_total: int
    deficits: list[dict] = []
    plan: dict | None = None
    """动态配额计划（signals + 各分类配额调整）；静态组卷时为 None。"""


# ============================================================================
# 工具
# ============================================================================


def _require_team(user) -> UUID:
    if user.team_id is None:
        raise ForbiddenError("当前用户未加入任何团队")
    return UUID(str(user.team_id))


async def _validate_session_in_team(db, session_id: UUID, team_id: UUID) -> InterviewSession:
    """校验 session 归属当前 team（session→candidate→team 链）；跨 team 一律 404。

    adaptive 的音频上传 / 打点等写路径必须先经此校验再触碰存储或 DB 写，
    防止持有他队 turn_id/session_id 的用户跨租户写入。
    """
    session_obj = await db.get(InterviewSession, session_id)
    if session_obj is None:
        raise NotFoundError(
            f"interview session {session_id} 不存在或无权访问",
            resource="interview_session",
        )
    candidate = await db.get(Candidate, session_obj.candidate_id)
    if candidate is None or candidate.team_id != team_id:
        raise NotFoundError(
            f"interview session {session_id} 不存在或无权访问",
            resource="interview_session",
        )
    return session_obj


async def _validate_candidate_in_team(db, candidate_id: UUID, team_id: UUID) -> Candidate:
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


async def _validate_question_in_team(db, question_id: UUID, team_id: UUID) -> InterviewQuestion:
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
        rows, _resolved = await service.list_latest_batch(candidate_id=candidate_id, job_id=job_id)
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
    batches, current, total = await service.list_batches(candidate_id=candidate_id, job_id=job_id)
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


# ============================================================================
# 面试会话 API
# ============================================================================


@router.post(
    "/sessions",
    response_model=InterviewSessionOut,
    status_code=status.HTTP_201_CREATED,
)
async def create_session(
    payload: CreateSessionRequest,
    user: CurrentUser,
    db: DbSession,
) -> InterviewSessionOut:
    """手动创建面试会话（HR 发起面试）。"""
    team_id = _require_team(user)
    await _validate_candidate_in_team(db, payload.candidate_id, team_id)
    await _validate_job_in_team(db, payload.job_id, team_id)

    service = InterviewService(db)
    session = await service.create_session(
        candidate_id=payload.candidate_id,
        job_id=payload.job_id,
        interviewer_id=user.id,
    )
    await db.commit()

    return InterviewSessionOut.model_validate(session)


@router.get(
    "/sessions",
    response_model=InterviewSessionListResponse,
    status_code=status.HTTP_200_OK,
)
async def list_sessions(
    user: CurrentUser,
    db: DbSession,
    status: str | None = Query(default=None),
    job_id: UUID | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=20, ge=1, le=100),
) -> InterviewSessionListResponse:
    """列出团队内的面试会话。"""
    team_id = _require_team(user)
    service = InterviewService(db)
    rows, total = await service.list_sessions(
        team_id=team_id,
        status=status,
        job_id=job_id,
        limit=page_size,
        offset=(page - 1) * page_size,
    )
    items = [
        InterviewSessionListItem(
            id=r[0].id,
            candidate_id=r[0].candidate_id,
            candidate_name=r[1],
            job_id=r[0].job_id,
            job_title=r[2],
            status=r[0].status,
            interviewer_id=r[0].interviewer_id,
            interviewer_name=r[3],
            question_count=r[4],
            created_at=r[0].created_at.isoformat() if r[0].created_at else None,
        )
        for r in rows
    ]
    return InterviewSessionListResponse(items=items, total=total)


@router.get(
    "/sessions/{session_id}",
    response_model=SessionDetailResponse,
    status_code=status.HTTP_200_OK,
)
async def get_session(
    session_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> SessionDetailResponse:
    """获取面试会话详情（含题目+反馈+录用建议）。"""
    team_id = _require_team(user)

    service = InterviewService(db)
    session = await service.get_session(session_id=session_id)
    if session is None:
        raise NotFoundError(
            f"interview session {session_id} not found",
            resource="interview_session",
        )

    # 校验跨 team
    candidate = await db.get(Candidate, session.candidate_id)
    if candidate is None or candidate.team_id != team_id:
        raise NotFoundError(
            f"interview session {session_id} not found",
            resource="interview_session",
        )

    job = await db.get(Job, session.job_id)
    candidate_name = candidate.name if candidate else None
    candidate_email = candidate.email if candidate else None
    job_title = job.title if job else None
    job_jd_summary = (
        (job.jd_text[:200] + "...")
        if job and len(job.jd_text) > 200
        else (job.jd_text if job else None)
    )

    # 题目（按 session_id 关联）
    q_result = await db.execute(
        select(InterviewQuestion)
        .where(InterviewQuestion.session_id == session_id)
        .order_by(InterviewQuestion.sort_order.asc())
    )
    questions = list(q_result.scalars().all())

    # 为每道题附加最新反馈
    question_outs: list[InterviewQuestionOut] = []
    for q in questions:
        fb_result = await db.execute(
            select(InterviewFeedback)
            .where(InterviewFeedback.question_id == q.id)
            .order_by(InterviewFeedback.created_at.desc())
            .limit(1)
        )
        latest_fb = fb_result.scalar_one_or_none()
        question_outs.append(
            InterviewQuestionOut(
                id=q.id,
                session_id=q.session_id,
                candidate_id=q.candidate_id,
                job_id=q.job_id,
                batch_id=q.batch_id,
                dimension=q.dimension,
                question=q.question,
                sort_order=q.sort_order,
                generated_by=q.generated_by,
                feedback_id=latest_fb.id if latest_fb else None,
                feedback=latest_fb.feedback if latest_fb else None,
                rating=latest_fb.rating if latest_fb else None,
            )
        )

    # 录用建议
    rec = None
    from app.models.hiring import HiringRecommendation

    hr = await db.scalar(
        select(HiringRecommendation).where(HiringRecommendation.session_id == session_id)
    )
    if hr:
        rec = {
            "id": str(hr.id),
            "session_id": str(hr.session_id),
            "recommendation": hr.recommendation,
            "reasons": hr.reasons,
            "risks": hr.risks,
            "probation_focus": hr.probation_focus,
            "generated_by": hr.generated_by,
            "created_at": hr.created_at.isoformat() if hr.created_at else None,
        }

    return SessionDetailResponse(
        session=InterviewSessionOut.model_validate(session),
        candidate_name=candidate_name,
        candidate_email=candidate_email,
        job_title=job_title,
        job_jd_summary=job_jd_summary,
        questions=question_outs,
        recommendation=rec,
    )


@router.patch(
    "/sessions/{session_id}",
    response_model=InterviewSessionOut,
    status_code=status.HTTP_200_OK,
)
async def update_session(
    session_id: UUID,
    payload: UpdateSessionRequest,
    user: CurrentUser,
    db: DbSession,
) -> InterviewSessionOut:
    """更新面试会话状态/面试官/整体评价。"""
    team_id = _require_team(user)

    service = InterviewService(db)
    session = await service.get_session(session_id=session_id)
    if session is None:
        raise NotFoundError(
            f"interview session {session_id} not found",
            resource="interview_session",
        )
    candidate = await db.get(Candidate, session.candidate_id)
    if candidate is None or candidate.team_id != team_id:
        raise NotFoundError(
            f"interview session {session_id} not found",
            resource="interview_session",
        )

    updated = await service.update_session(
        session_id=session_id,
        status=payload.status,
        interviewer_id=payload.interviewer_id,
        overall_notes=payload.overall_notes,
    )
    await db.commit()

    return InterviewSessionOut.model_validate(updated)


@router.post(
    "/sessions/{session_id}/compose",
    response_model=_ComposeResponse,
    status_code=status.HTTP_200_OK,
)
async def compose_from_bank(
    session_id: UUID,
    user: CurrentUser,
    db: DbSession,
    payload: _ComposeRequest,
) -> _ComposeResponse:
    """从题库凑分组卷，写入当前 session（新 batch_id）。

    默认 ``dynamic=true``：按候选人简历 + JD 动态匹配配额（约 30 题）；
    凑不满时返回 deficits；调用方据 deficits 决定 abort / AI 兜底。
    """
    team_id = _require_team(user)
    service = InterviewService(db)
    session = await service.get_session(session_id=session_id)
    if session is None:
        raise NotFoundError(
            f"interview session {session_id} not found",
            resource="interview_session",
        )
    candidate = await db.get(Candidate, session.candidate_id)
    if candidate is None or candidate.team_id != team_id:
        raise NotFoundError(
            f"interview session {session_id} not found",
            resource="interview_session",
        )

    bank_svc = QuestionBankService(db)
    plan: dict | None = None
    if payload.dynamic:
        signals = await bank_svc.build_candidate_signals(
            team_id=team_id,
            candidate_id=session.candidate_id,
            job_id=session.job_id,
        )
        items, actual, deficits, plan = await bank_svc.plan_and_assemble(
            team_id=team_id,
            signals=signals,
            quotas=payload.quotas,
            tolerance=payload.tolerance,
            exclude_question_ids=payload.exclude_question_ids,
        )
    else:
        items, actual, deficits = await bank_svc.assemble(
            team_id=team_id,
            quotas=payload.quotas,
            tolerance=payload.tolerance,
            exclude_question_ids=payload.exclude_question_ids,
        )
    if not items:
        # 无题可选（题库空），不实例化，直接返回空缺口信息
        return _ComposeResponse(
            batch_id=uuid.uuid4(),  # 占位（未实例化）
            question_count=0,
            actual_total=0,
            target_total=sum(d["target"] for d in deficits),
            deficits=deficits,
        )

    batch_id = await QuestionBankService(db).instantiate_from_bank(
        candidate_id=session.candidate_id,
        job_id=session.job_id,
        session_id=session.id,
        items=items,
    )
    await db.commit()
    return _ComposeResponse(
        batch_id=batch_id,
        question_count=len(items),
        actual_total=actual,
        target_total=actual + sum(d["target"] for d in deficits),
        deficits=deficits,
        plan=plan,
    )


@router.post(
    "/sessions/{session_id}/feedback",
    response_model=BatchFeedbackResponse,
    status_code=status.HTTP_200_OK,
)
async def batch_save_feedback(
    session_id: UUID,
    payload: BatchFeedbackRequest,
    user: CurrentUser,
    db: DbSession,
) -> BatchFeedbackResponse:
    """批量保存面试反馈。"""
    team_id = _require_team(user)

    service = InterviewService(db)
    session = await service.get_session(session_id=session_id)
    if session is None:
        raise NotFoundError(
            f"interview session {session_id} not found",
            resource="interview_session",
        )
    candidate = await db.get(Candidate, session.candidate_id)
    if candidate is None or candidate.team_id != team_id:
        raise NotFoundError(
            f"interview session {session_id} not found",
            resource="interview_session",
        )

    saved, errors = await service.batch_save_feedback(
        session_id=session_id,
        reviewer_id=user.id,
        payload=payload,
    )
    await db.commit()

    return BatchFeedbackResponse(saved=saved, errors=errors)


# ============================================================================
# 渐进式自适应面试（M1：规则式选题 + LLM 对照评分；手输回答）
# ============================================================================


@router.post(
    "/sessions/{session_id}/adaptive/start",
    response_model=AdaptiveStartOut,
    status_code=status.HTTP_200_OK,
)
async def adaptive_start(
    session_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> AdaptiveStartOut:
    """启动自适应面试：简历/JD 信号 → 分支计划 → 首题（幂等，重复调用返回当前状态）。"""
    team_id = _require_team(user)
    result = await AdaptiveInterviewService(db).start(
        team_id=team_id, session_id=session_id, started_by=user.id
    )
    await db.commit()
    return result


@router.get(
    "/sessions/{session_id}/adaptive/state",
    response_model=AdaptiveStateOut,
)
async def adaptive_state(
    session_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> AdaptiveStateOut:
    """自适应面试大屏：分支进度 + 回合时间线 + 能力画像。"""
    team_id = _require_team(user)
    return await AdaptiveInterviewService(db).state(team_id=team_id, session_id=session_id)


@router.post(
    "/sessions/{session_id}/adaptive/answer",
    response_model=AdaptiveAnswerOut,
)
async def adaptive_answer(
    session_id: UUID,
    payload: AdaptiveAnswerRequest,
    user: CurrentUser,
    db: DbSession,
) -> AdaptiveAnswerOut:
    """提交回答（M1 手输文本）：保存回答并同步 LLM 评分 + 下一题决策。

    评分失败时回答仍保存（rating_error 携带原因），由 /adaptive/next 自动重试评分。
    """
    team_id = _require_team(user)
    result = await AdaptiveInterviewService(db).submit_answer(
        team_id=team_id,
        session_id=session_id,
        turn_id=payload.turn_id,
        answer_text=payload.answer_text,
    )
    await db.commit()
    return result


@router.post(
    "/sessions/{session_id}/adaptive/audio",
    status_code=status.HTTP_202_ACCEPTED,
)
async def adaptive_upload_audio(
    session_id: UUID,
    user: CurrentUser,
    db: DbSession,
    turn_id: UUID = Form(...),
    audio: UploadFile = File(...),
) -> dict[str, Any]:
    """上传本题音频（M2a）：存 MinIO + 入队 Celery 转写 → 转写完成自动评分。

    返回 {transcription_status, storage_key}；前端轮询 /adaptive/state 或等 SSE。
    """
    team_id = _require_team(user)
    # 归属校验前置：先证明 session 属于当前团队，再读文件、写存储
    await _validate_session_in_team(db, session_id, team_id)
    if audio.content_type and not audio.content_type.startswith("audio/"):
        raise AppValidationError("仅接受音频文件", field="audio")
    # 体积校验用 seek/tell（Spooled 文件 >1MB 已落盘，read() 会全量进内存）
    audio.file.seek(0, 2)
    audio_size = audio.file.tell()
    if audio_size == 0:
        raise AppValidationError("音频为空", field="audio")
    if audio_size > 50 * 1024 * 1024:
        raise AppValidationError("音频超过 50MB 上限", field="audio")
    audio.file.seek(0)

    # 回合存在性与归属（turn 必须属于 path 中已验证归属的 session）
    turn = await db.get(InterviewTurn, turn_id)
    if turn is None or turn.session_id != session_id:
        raise NotFoundError(f"turn {turn_id} not found", resource="interview_turn")

    from app.adapters.storage import get_storage

    storage = get_storage()
    suffix = Path(audio.filename or "audio.webm").suffix or ".webm"
    storage_key = f"interview-audio/turns/{turn_id}{suffix}"
    # 全部校验通过后才落存储（原实现 put 先于校验，可向任意 turn_id 写入对象）
    await storage.put_fileobj(
        storage_key,
        audio.file,
        size=audio_size,
        mime=audio.content_type or "audio/webm",
        encrypt=False,
    )
    if turn.transcription_status in ("pending", "processing"):
        # 超时自愈：pending/processing 超过 2 分钟视为任务丢失，允许重传覆盖
        # （turn 表无 updated_at，用 rating_evidence 里的时间戳或 created_at 近似）
        from datetime import datetime, timezone

        marker = (turn.rating_evidence or {}).get("audio_queued_at")
        stale = True
        if marker:
            try:
                queued = datetime.fromisoformat(str(marker))
                stale = (datetime.now(timezone.utc) - queued).total_seconds() > 120
            except ValueError:
                stale = True
        if not stale:
            raise AppValidationError("该题音频转写中，请稍候再上传", field="audio")
    if turn.answer_text is not None and turn.transcription_status == "done":
        raise AppValidationError("该题已有回答（转写完成），请先获取下一题", field="audio")
    turn.audio_storage_key = storage_key
    turn.transcription_status = "pending"
    from datetime import datetime, timezone

    turn.rating_evidence = {
        **(turn.rating_evidence or {}),
        "audio_queued_at": datetime.now(timezone.utc).isoformat(),
    }
    await db.commit()

    from app.workers.transcription_task import enqueue_transcription

    job_id = await enqueue_transcription(turn_id=turn_id)
    return {
        "turn_id": str(turn_id),
        "transcription_status": "pending",
        "async_job_id": str(job_id) if job_id else None,
        "storage_key": storage_key,
    }


@router.patch(
    "/sessions/{session_id}/adaptive/turns/{turn_id}/offset",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def adaptive_mark_offset(
    session_id: UUID,
    turn_id: UUID,
    payload: dict,
    user: CurrentUser,
    db: DbSession,
) -> None:
    """M2b 打点：标注本题在整场录制中的起始时间（毫秒或 mm:ss 字符串）。"""
    team_id = _require_team(user)
    await _validate_session_in_team(db, session_id, team_id)
    turn = await db.get(InterviewTurn, turn_id)
    if turn is None or turn.session_id != session_id:
        raise NotFoundError(f"turn {turn_id} not found", resource="interview_turn")
    raw = payload.get("audio_start_ms") or payload.get("offset")
    ms: int | None = None
    if isinstance(raw, (int, float)):
        ms = int(raw)
    elif isinstance(raw, str) and raw.strip():
        parts = raw.strip().split(":")
        try:
            nums = [float(p) for p in parts]
            ms = int(sum(v * 60 ** (len(nums) - 1 - i) for i, v in enumerate(nums)) * 1000)
        except ValueError:
            ms = None
    if ms is None or ms < 0:
        raise AppValidationError("audio_start_ms 需为毫秒数或 mm:ss", field="audio_start_ms")
    turn.audio_start_ms = ms
    await db.commit()


@router.post(
    "/sessions/{session_id}/adaptive/recording",
    status_code=status.HTTP_202_ACCEPTED,
)
async def adaptive_upload_recording(
    session_id: UUID,
    user: CurrentUser,
    db: DbSession,
    audio: UploadFile = File(...),
) -> dict[str, Any]:
    """M2b 上传整场会议录制（钉钉/腾讯会议云录制下载或本地录制）。

    上传后可继续 PATCH 各题起点打点，然后 POST …/recording/process 触发回捞。
    """
    _require_team(user)
    session = await AdaptiveInterviewService(db)._load_session(  # noqa: SLF001
        team_id=UUID(str(user.team_id)), session_id=session_id
    )
    if audio.content_type and not (
        audio.content_type.startswith("audio/") or audio.content_type.startswith("video/")
    ):
        raise AppValidationError("仅接受音视频文件", field="audio")
    # 500MB 整场录制：seek/tell 校验 + 流式分片上传（整读会叠爆 2G 容器内存）
    audio.file.seek(0, 2)
    audio_size = audio.file.tell()
    if audio_size == 0:
        raise AppValidationError("文件为空", field="audio")
    if audio_size > 500 * 1024 * 1024:
        raise AppValidationError("录制超过 500MB 上限", field="audio")
    audio.file.seek(0)

    from app.adapters.storage import get_storage

    suffix = Path(audio.filename or "rec.mp4").suffix or ".mp4"
    key = f"interview-audio/recordings/{session_id}{suffix}"
    await get_storage().put_fileobj(
        key,
        audio.file,
        size=audio_size,
        mime=audio.content_type or "video/mp4",
        encrypt=False,
    )
    session.recording_storage_key = key
    session.recording_status = "uploaded"
    await db.commit()
    return {"session_id": str(session_id), "recording_status": "uploaded", "storage_key": key}


@router.post(
    "/sessions/{session_id}/adaptive/recording/process",
    status_code=status.HTTP_202_ACCEPTED,
)
async def adaptive_process_recording(
    session_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> dict[str, Any]:
    """触发会后回捞：按各题打点区间转写 + 自动评分（异步）。"""
    team_id = _require_team(user)
    await AdaptiveInterviewService(db)._load_session(  # noqa: SLF001
        team_id=team_id, session_id=session_id
    )
    from app.workers.transcription_task import enqueue_recording_replay

    job_id = await enqueue_recording_replay(session_id=session_id)
    return {
        "session_id": str(session_id),
        "async_job_id": str(job_id) if job_id else None,
        "status": "queued",
    }


@router.get("/sessions/{session_id}/report")
async def session_report(
    session_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> dict[str, Any]:
    """会后报告：逐题轨迹 + 分支能力画像 + 完成度 + 录用建议（若已生成）。

    面试官一屏复盘；recommendation 为 null 时前端引导单独触发生成。
    """
    team_id = _require_team(user)
    return await AdaptiveInterviewService(db).build_report(
        team_id=team_id, session_id=session_id
    )


@router.post("/sessions/{session_id}/adaptive/turns/{turn_id}/promote")
async def promote_turn_to_bank(
    session_id: UUID,
    turn_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> dict[str, Any]:
    """把本场 AI 现场生成的追问题沉淀为题库候选（source=ai_followup，待审核）。

    面试官点「好问题，收进题库」→ 管理员审核通过后进入组卷池。
    幂等：重复点击返回已沉淀条目。
    """
    team_id = _require_team(user)
    item = await AdaptiveInterviewService(db).promote_turn_to_bank(
        team_id=team_id,
        session_id=session_id,
        turn_id=turn_id,
        user_id=user.id,
    )
    await db.commit()
    return {
        "id": str(item.id),
        "question": item.question,
        "source": item.source,
        "review_status": item.review_status,
        "category_id": str(item.category_id),
        "reference_answer": item.reference_answer,
    }


@router.post("/sessions/{session_id}/adaptive/direct/audio")
async def adaptive_direct_audio(
    session_id: UUID,
    user: CurrentUser,
    db: DbSession,
    audio: UploadFile = File(...),
) -> dict[str, Any]:
    """面试官语音指挥（「问问他 RAG」「来道简单的」）→ ASR 转写 → 语义出题。

    面试中面试官双手忙于记录，语音是指挥的自然形态。同步转写
    （短指令 1-3s），失败返回 422 带转写错误，不打断面试节奏。
    """
    team_id = _require_team(user)
    await _validate_session_in_team(db, session_id, team_id)
    if audio.content_type and not audio.content_type.startswith("audio/"):
        raise AppValidationError("仅接受音频文件", field="audio")
    data = await audio.read()
    if not data:
        raise AppValidationError("音频为空", field="audio")
    if len(data) > 25 * 1024 * 1024:
        raise AppValidationError("指令音频超过 25MB 上限", field="audio")

    from app.adapters.asr_client import ASRClient

    text = ""
    try:
        result = await ASRClient().transcribe(
            audio_bytes=data,
            filename=audio.filename or "command.webm",
        )
        text = str(result.get("text") or "").strip()
    except Exception as exc:  # noqa: BLE001 - ASR 故障不升级为 500
        logger.warning("adaptive_direct_asr_failed", error=str(exc)[:150])
        raise AppValidationError(
            "语音转写失败，请重试或改用文字指令", field="audio"
        ) from exc
    if not text:
        raise AppValidationError("未能识别出指令内容，请靠近麦克风重试", field="audio")

    svc = AdaptiveInterviewService(db)
    parsed = await svc.parse_directive(team_id=team_id, session_id=session_id, text=text)
    if not parsed.get("category_id") and not parsed.get("difficulty"):
        raise AppValidationError(
            f"无法识别指令「{text[:40]}」——试试包含分支关键词(如 RAG/Python/微服务)或难度词(简单/难点)",
            field="text",
        )
    result_out = await svc.next_question(
        team_id=team_id,
        session_id=session_id,
        force_category_id=uuid.UUID(parsed["category_id"]) if parsed.get("category_id") else None,
        difficulty_override=parsed.get("difficulty"),
    )
    await db.commit()
    return {"text": text, "parsed": parsed, "result": result_out.model_dump()}


@router.post("/sessions/{session_id}/adaptive/direct")
async def adaptive_direct(
    session_id: UUID,
    payload: dict,
    user: CurrentUser,
    db: DbSession,
) -> dict[str, Any]:
    """面试官自然语言指挥：「问问他 RAG」「来道简单的」「换个微服务题」→ 解析后直接出题。"""
    team_id = _require_team(user)
    text = str(payload.get("text") or "").strip()
    if not text:
        raise AppValidationError("指令不能为空", field="text")
    svc = AdaptiveInterviewService(db)
    parsed = await svc.parse_directive(team_id=team_id, session_id=session_id, text=text)
    if not parsed.get("category_id") and not parsed.get("difficulty"):
        raise AppValidationError(
            "无法识别指令——试试包含分支关键词(如 RAG/Python/微服务)或难度词(简单/难点)",
            field="text",
        )
    result = await svc.next_question(
        team_id=team_id,
        session_id=session_id,
        force_category_id=uuid.UUID(parsed["category_id"]) if parsed.get("category_id") else None,
        difficulty_override=parsed.get("difficulty"),
    )
    await db.commit()
    return {"parsed": parsed, "result": result.model_dump()}


@router.get("/sessions/{session_id}/adaptive/preview")
async def adaptive_preview(
    session_id: UUID,
    user: CurrentUser,
    db: DbSession,
    category_id: UUID | None = Query(default=None),
) -> list[dict]:
    """候选题预览：当前目标难度+信号相关度排序的备选题（未问过的）。"""
    team_id = _require_team(user)
    return await AdaptiveInterviewService(db).preview_candidates(
        team_id=team_id, session_id=session_id, category_id=category_id
    )


@router.get(
    "/sessions/{session_id}/adaptive/next",
    response_model=AdaptiveNextOut,
)
async def adaptive_next(
    session_id: UUID,
    user: CurrentUser,
    db: DbSession,
    force_category_id: UUID | None = Query(default=None),
    skip_current: bool = Query(default=False),
) -> AdaptiveNextOut:
    """获取下一题（含上一题的选题理由）；幂等：未回答的题即当前题。全部完成时 done=true。

    面试官控制：``?force_category_id=`` 指定分支出题；``?skip_current=true`` 跳过当前题换考点。
    """
    team_id = _require_team(user)
    result = await AdaptiveInterviewService(db).next_question(
        team_id=team_id,
        session_id=session_id,
        force_category_id=force_category_id,
        skip_current=skip_current,
    )
    await db.commit()
    return result


__all__ = ["router"]
