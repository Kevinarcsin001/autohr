"""Extractor celery task 实现（任务 14 接入 extract_structured_handler）。

职责：
1. 从 AsyncJob target_id（= candidate_resume.id）拿 resume
2. 校验 parse_status（必须 success；low_text/failed 跳过）
3. 调 ``ExtractorService.extract(parsed_text)``
4. 写/更新 ``parsed_structures``（resume_id UNIQUE）
5. 不更新 ``candidates`` 主字段（任务 15 DedupService 负责）

约束：
- 失败保留原 ParsedStructure 行（不删除），便于人工排查
- 不把简历原文写日志（service 层已脱敏）
- PermanentFailure：resume 不存在 / parse 未就绪 → 不重试
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm import LLMRouter
from app.core.logging import get_logger
from app.models.candidate import CandidateResume, ParsedStructure
from app.services.extractor import ExtractResult, ExtractorService

logger = get_logger(__name__)


class ResumeNotReady(Exception):
    """resume.parse_status != 'success'，无法抽取。"""


class ResumeNotFound(Exception):
    """target_id 对应的 CandidateResume 不存在。"""


class ResumeTextMissing(Exception):
    """resume.parsed_text 为空（不应该发生，但兜底）。"""


async def run_extract(
    *,
    db: AsyncSession,
    target_id: uuid.UUID,
    payload: dict[str, Any] | None,
    service: ExtractorService | None = None,
    router: LLMRouter | None = None,
) -> dict[str, Any]:
    """执行结构化抽取并写回 DB。

    Args:
        db: 异步 session（caller 控制 commit）
        target_id: candidate_resume.id
        payload: 可选 {team_id: str} 用于 LLM 路由 team 隔离
        service: 测试可注入 ExtractorService
        router: 测试可注入 LLMRouter（service 优先级更高）

    Returns:
        ``{"resume_id", "status", "fields_extracted", "attempts"}`` 摘要

    Raises:
        ResumeNotFound: resume 行不存在
        ResumeNotReady: parse_status != "success"
        ResumeTextMissing: parsed_text 为空
    """
    # 设置 router team 上下文（影响 llm_calls.team_id）
    if router is not None and payload and payload.get("team_id"):
        try:
            router.set_team_context(uuid.UUID(str(payload["team_id"])))
        except (ValueError, TypeError):
            pass

    resume = await db.get(CandidateResume, target_id)
    if resume is None:
        raise ResumeNotFound(f"CandidateResume {target_id} not found")

    if resume.parse_status != "success":
        raise ResumeNotReady(
            f"resume.parse_status={resume.parse_status!r}, expected 'success'"
        )

    if not resume.parsed_text:
        raise ResumeTextMissing(f"resume.parsed_text empty for {target_id}")

    service = service or ExtractorService(router=router)
    result: ExtractResult = await service.extract(resume.parsed_text)

    # 写 ParsedStructure（resume_id UNIQUE → upsert）
    await _upsert_parsed_structure(db, resume_id=target_id, result=result)
    await db.flush()

    fields_extracted = _count_non_null_fields(result.structure)

    logger.info(
        "extract_completed",
        resume_id=str(target_id),
        status=result.status,
        fields_extracted=fields_extracted,
        attempts=result.attempts,
    )

    return {
        "resume_id": str(target_id),
        "status": result.status,
        "fields_extracted": fields_extracted,
        "attempts": result.attempts,
    }


# ============================================================================
# 内部
# ============================================================================


async def _upsert_parsed_structure(
    db: AsyncSession,
    *,
    resume_id: uuid.UUID,
    result: ExtractResult,
) -> None:
    """按 resume_id 写入或更新 ParsedStructure 行。"""
    existing = await db.scalar(
        select(ParsedStructure).where(ParsedStructure.resume_id == resume_id)
    )

    data = {
        "structure": result.structure.model_dump(mode="json"),
        "status": result.status,
        "error": result.error,
        "attempts": result.attempts,
    }

    if existing is not None:
        existing.data = data  # type: ignore[assignment]
        return

    db.add(
        ParsedStructure(
            resume_id=resume_id,
            data=data,
        )
    )


def _count_non_null_fields(structure: Any) -> int:
    """统计结构化字段中非 null 的核心字段数（用于监控）。"""
    return sum(
        1
        for f in (
            structure.name,
            structure.phone,
            structure.email,
            structure.education,
            structure.years_of_experience,
            structure.expected_salary,
            structure.current_company,
        )
        if f is not None
    ) + (1 if structure.skills else 0) + (1 if structure.work_history else 0)


__all__ = [
    "run_extract",
    "ResumeNotReady",
    "ResumeNotFound",
    "ResumeTextMissing",
]
