"""Scorer celery task 实现（任务 17 + 任务 18）。

职责：
1. 从 AsyncJob payload 拿 job_id；target_id = candidate.id
2. 取最新 ParsedStructure（任务 14 输出）+ Job
3. 构造 ScoringInput（含 build_scoring_snippet 截取关键片段）
4. 调 ``ScorerService.score(input)`` 写 scores 行
5. **任务 18 集成**：根据 ScreeningResult.disqualified 自动生成对应 reason：
   - disqualified=True → 调 ``ReasoningService.persist_disqualify``（不调 LLM）
   - disqualified=False / 无 ScreeningResult → 调 ``ReasoningService.generate_recommend``
     （带事实校验）

约束：
- 不把简历原文写日志（service 层已脱敏）
- PermanentFailure：候选人不存在 / 没有结构化数据 → 不重试
- Reasoning 失败不阻塞 score 已写入的结果（catch + logger.warning）
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm import LLMRouter
from app.core.logging import get_logger
from app.models.candidate import (
    Candidate,
    CandidateResume,
    ParsedStructure,
)
from app.models.job import Job
from app.models.score import Score
from app.models.screening import ScreeningResult
from app.schemas.candidate_structure import CandidateStructure
from app.services.reasoning import ReasoningError, ReasoningService
from app.services.scorer import ScorerError, ScorerService, ScoringInput, build_scoring_snippet

logger = get_logger(__name__)


class CandidateNotFound(Exception):
    """target_id 对应的候选人不存在。"""


class StructureMissing(Exception):
    """候选人没有 ParsedStructure（任务 14 还没跑过 / 失败了）。"""


class JobNotFound(Exception):
    """payload 里 job_id 对应的 Job 不存在。"""


async def run_score(
    *,
    db: AsyncSession,
    target_id: uuid.UUID,
    payload: dict[str, Any] | None,
    service: ScorerService | None = None,
    router: LLMRouter | None = None,
    reasoning_service: ReasoningService | None = None,
) -> dict[str, Any]:
    """执行评分 + 自动生成理由并写回 DB。

    Args:
        db: 异步 session（caller 控制 commit）
        target_id: candidate.id
        payload: ``{"job_id": str}`` 必填
        service: 测试可注入 ScorerService
        router: 测试可注入 LLMRouter（同时传给 Scorer + Reasoning）
        reasoning_service: 测试可注入 ReasoningService

    Returns:
        ``{"candidate_id", "job_id", "total", "model_used", "reason_status"}``

    Raises:
        CandidateNotFound, StructureMissing, JobNotFound, ScorerError
    """
    if not payload or "job_id" not in payload:
        raise ValueError("score_candidate requires payload['job_id']")

    if router is not None:
        try:
            team_id_raw = payload.get("team_id")
            if team_id_raw:
                router.set_team_context(uuid.UUID(str(team_id_raw)))
        except (ValueError, TypeError):
            pass

    job_id = uuid.UUID(str(payload["job_id"]))

    candidate = await db.get(Candidate, target_id)
    if candidate is None:
        raise CandidateNotFound(f"Candidate {target_id} not found")

    job = await db.get(Job, job_id)
    if job is None:
        raise JobNotFound(f"Job {job_id} not found")

    # 取最新 ParsedStructure
    structure_data = await _fetch_latest_structure(db, candidate_id=target_id)
    if structure_data is None:
        raise StructureMissing(
            f"Candidate {target_id} has no ParsedStructure; extractor not run?"
        )

    inner = structure_data.get("structure")
    if not isinstance(inner, dict):
        raise StructureMissing(
            f"ParsedStructure for candidate {target_id} malformed"
        )

    try:
        structure = CandidateStructure.model_validate(inner)
    except Exception as exc:
        raise StructureMissing(
            f"ParsedStructure.schema validation failed: {exc}"
        ) from exc

    parsed_text = await _fetch_latest_parsed_text(db, candidate_id=target_id)
    snippet = build_scoring_snippet(parsed_text)

    scoring_input = ScoringInput(
        job_id=job_id,
        candidate_id=target_id,
        job_title=job.title,
        jd_text=job.jd_text,
        structure=structure,
        resume_snippet=snippet,
    )

    service = service or ScorerService(db, router=router)
    result = await service.score(scoring_input)
    await db.flush()

    # 取最新 Score 行（service.score 已经 upsert）
    score_row = await db.scalar(
        select(Score).where(
            Score.job_id == job_id,
            Score.candidate_id == target_id,
        )
    )

    # ----- 任务 18 集成：自动生成 reason -----
    reason_status = await _try_generate_reason(
        db=db,
        score=score_row,
        job=job,
        parsed_text=parsed_text or "",
        job_id=job_id,
        candidate_id=target_id,
        router=router,
        reasoning_service=reasoning_service,
    )

    return {
        "candidate_id": str(target_id),
        "job_id": str(job_id),
        "total": result.dimensions.total,
        "model_used": result.model_used,
        "llm_call_id": str(result.llm_call_id) if result.llm_call_id else None,
        "reason_status": reason_status,
    }


async def _try_generate_reason(
    *,
    db: AsyncSession,
    score: Score | None,
    job: Job,
    parsed_text: str,
    job_id: uuid.UUID,
    candidate_id: uuid.UUID,
    router: LLMRouter | None,
    reasoning_service: ReasoningService | None,
) -> str:
    """根据 ScreeningResult 自动生成 recommend / disqualify 理由。

    失败不阻塞 score 已写入的结果。返回 reason_status 字符串。
    """
    if score is None:
        return "skipped_no_score"

    # 看 ScreeningResult 决定走 recommend 还是 disqualify
    sr = await db.scalar(
        select(ScreeningResult).where(
            ScreeningResult.job_id == job_id,
            ScreeningResult.candidate_id == candidate_id,
        )
    )

    service = reasoning_service or ReasoningService(db, router=router)

    try:
        if sr is not None and sr.disqualified and sr.reasons:
            await service.persist_disqualify(
                score_id=score.id,
                filter_reasons=sr.reasons,
            )
            return "disqualify_written"
        else:
            await service.generate_recommend(
                score=score,
                job_title=job.title,
                jd_text=job.jd_text,
                resume_text=parsed_text,
            )
            return "recommend_generated"
    except ReasoningError as exc:
        logger.warning(
            "reasoning_failed_score_kept",
            score_id=str(score.id),
            error=str(exc)[:200],
        )
        return f"reasoning_failed: {type(exc).__name__}"


# ============================================================================
# 内部
# ============================================================================


async def _fetch_latest_structure(
    db: AsyncSession, *, candidate_id: uuid.UUID
) -> dict[str, Any] | None:
    """取候选人最新 resume 的 ParsedStructure.data。"""
    stmt = (
        select(ParsedStructure.data)
        .join(
            CandidateResume,
            CandidateResume.id == ParsedStructure.resume_id,
        )
        .where(CandidateResume.candidate_id == candidate_id)
        .order_by(CandidateResume.uploaded_at.desc())
        .limit(1)
    )
    return await db.scalar(stmt)


async def _fetch_latest_parsed_text(
    db: AsyncSession, *, candidate_id: uuid.UUID
) -> str | None:
    """取候选人最新 resume 的 parsed_text（用于截取关键片段）。"""
    stmt = (
        select(CandidateResume.parsed_text)
        .where(CandidateResume.candidate_id == candidate_id)
        .order_by(CandidateResume.uploaded_at.desc())
        .limit(1)
    )
    return await db.scalar(stmt)


__all__ = [
    "run_score",
    "CandidateNotFound",
    "StructureMissing",
    "JobNotFound",
    "ScorerError",
]
