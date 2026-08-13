"""HiringRecommendationService：AI 录用建议生成。

基于简历 + JD + 面试反馈 + 评分，调用 LLM 生成录用建议（hire/reserve/reject）
+ 核心理由 + 潜在风险 + 试用期关注点。
"""

from __future__ import annotations

import uuid

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm import (
    LLMError,
    LLMResponse,
    LLMRouter,
    LLMSchemaError,
    Message,
)
from app.core.logging import get_logger
from app.core.middleware.error_handler import NotFoundError
from app.models.candidate import Candidate, CandidateResume
from app.models.hiring import HiringRecommendation
from app.models.interview import InterviewFeedback, InterviewQuestion, InterviewSession
from app.models.job import Job
from app.models.score import Score
from app.schemas.hiring import HiringDecisionOutput

logger = get_logger(__name__)

_HIRING_SCOPE: str = "hiring"

_SYSTEM_PROMPT = """\
你是资深招聘决策顾问。任务：基于候选人的简历、JD、面试表现，生成录用建议。

# 输出要求

1. 必须输出**纯 JSON**（不带 markdown 代码块、不带注释）。
2. JSON schema：

```json
{
  "recommendation": "hire",
  "reasons": ["理由1", "理由2", "理由3"],
  "risks": ["风险1", "风险2"],
  "probation_focus": ["关注点1", "关注点2", "关注点3"]
}
```

3. ``recommendation`` 取值：
   - ``hire``：建议录用，综合表现优秀
   - ``reserve``：保留，可作为备选
   - ``reject``：建议淘汰，不符合要求
4. ``reasons``：3-5 条核心理由，每条 ≤ 200 字
5. ``risks``：1-5 条潜在风险（如技能短板、频繁跳槽、缺乏某领域经验等），每条 ≤ 150 字
6. ``probation_focus``：2-5 条试用期重点观察方向，每条 ≤ 150 字

请基于事实做判断，不要过度推演。"""

_USER_PROMPT_TEMPLATE = """\
请为以下候选人生成录用建议。

# 职位信息

职位：{job_title}
JD 摘要：
{jd_text}

# 候选人信息

姓名：{candidate_name}
学历：{education}
工作年限：{years_of_experience}
技能：{skills}

# AI 评分（0-100）

综合：{total} / 技能：{skill} / 经验：{experience} / 学历：{edu_score} / 稳定性：{stability} / 潜力：{potential}

# 面试反馈

{interview_feedback}

# 简历摘要

{resume_snippet}

请输出 JSON。"""


class HiringError(Exception):
    """录用建议生成错误。"""


class HiringRecommendationService:
    """录用建议生成服务。"""

    def __init__(
        self,
        db: AsyncSession,
        *,
        router: LLMRouter | None = None,
    ) -> None:
        self._db = db
        self._router = router

    def _get_router(self) -> LLMRouter:
        if self._router is not None:
            return self._router
        from app.adapters.llm import build_default_router

        self._router = build_default_router()
        return self._router

    async def generate(self, *, session_id: uuid.UUID) -> HiringRecommendation:
        """生成录用建议。"""
        session = await self._db.get(InterviewSession, session_id)
        if session is None:
            raise NotFoundError(
                f"interview session {session_id} not found",
                resource="interview_session",
            )

        # 已有建议 → 返回已有
        existing = await self._db.scalar(
            select(HiringRecommendation).where(HiringRecommendation.session_id == session_id)
        )
        if existing is not None:
            return existing

        # 收集上下文数据
        candidate = await self._db.get(Candidate, session.candidate_id)
        if candidate is None:
            raise NotFoundError("candidate not found", resource="candidate")

        job = await self._db.get(Job, session.job_id)
        if job is None:
            raise NotFoundError("job not found", resource="job")

        score = await self._db.scalar(
            select(Score).where(
                Score.job_id == session.job_id,
                Score.candidate_id == session.candidate_id,
            )
        )

        # 面试题目 + 反馈
        q_result = await self._db.execute(
            select(InterviewQuestion)
            .where(InterviewQuestion.session_id == session_id)
            .order_by(InterviewQuestion.sort_order.asc())
        )
        questions = list(q_result.scalars().all())

        feedback_lines: list[str] = []
        overall_ratings: list[int] = []
        for q in questions:
            fb_result = await self._db.execute(
                select(InterviewFeedback)
                .where(InterviewFeedback.question_id == q.id)
                .order_by(InterviewFeedback.created_at.desc())
            )
            fbs = list(fb_result.scalars().all())
            for f in fbs:
                rating_str = f"({f.rating}/5)" if f.rating else ""
                fb_text = f.feedback or "(无文字反馈)"
                feedback_lines.append(f"- Q: {q.question[:80]}... → {fb_text} {rating_str}")
                if f.rating:
                    overall_ratings.append(f.rating)

        interview_feedback = "\n".join(feedback_lines) if feedback_lines else "（尚无面试反馈）"

        # 简历摘要
        resume_result = await self._db.execute(
            select(CandidateResume.parsed_text)
            .where(CandidateResume.candidate_id == session.candidate_id)
            .order_by(CandidateResume.uploaded_at.desc())
            .limit(1)
        )
        resume_text = resume_result.scalar_one_or_none() or ""

        # 结构化字段
        from app.models.candidate import ParsedStructure

        struct_result = await self._db.execute(
            select(ParsedStructure.data)
            .join(CandidateResume, CandidateResume.id == ParsedStructure.resume_id)
            .where(CandidateResume.candidate_id == session.candidate_id)
            .order_by(CandidateResume.uploaded_at.desc())
            .limit(1)
        )
        struct_row = struct_result.first()
        structure = struct_row[0] if struct_row else {}

        inner = structure.get("structure") if isinstance(structure, dict) else {}
        education = inner.get("education", "(未知)") if isinstance(inner, dict) else "(未知)"
        years = inner.get("years_of_experience", "(未知)") if isinstance(inner, dict) else "(未知)"
        skills = ", ".join(inner.get("skills", [])) if isinstance(inner, dict) else "(无)"

        # 构建 prompt
        messages = [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(
                role="user",
                content=_USER_PROMPT_TEMPLATE.format(
                    job_title=job.title,
                    jd_text=self._truncate(job.jd_text, 600),
                    candidate_name=candidate.name,
                    education=education,
                    years_of_experience=years,
                    skills=skills,
                    total=score.total if score else "-",
                    skill=score.skill if score and score.skill is not None else "-",
                    experience=score.experience if score and score.experience is not None else "-",
                    edu_score=score.education if score and score.education is not None else "-",
                    stability=score.stability if score and score.stability is not None else "-",
                    potential=score.potential if score and score.potential is not None else "-",
                    interview_feedback=interview_feedback,
                    resume_snippet=self._truncate(resume_text, 2000),
                ),
            ),
        ]

        router = self._get_router()
        try:
            response = await router.chat(
                messages=messages,
                response_schema=HiringDecisionOutput,
                temperature=0.3,
                scope=_HIRING_SCOPE,
            )
        except LLMSchemaError as exc:
            logger.warning("hiring_schema_error", error=str(exc)[:200])
            raise HiringError(f"LLM schema error: {exc}") from exc
        except LLMError as exc:
            logger.warning("hiring_llm_unavailable", error=str(exc)[:200])
            raise HiringError(f"LLM unavailable: {exc}") from exc

        decision = self._safe_parsed(response)
        if decision is None:
            raise HiringError(f"LLM response.parsed is None; content={response.content[:200]!r}")

        llm_call_id = response.extra.get("llm_call_id")
        if not isinstance(llm_call_id, uuid.UUID):
            llm_call_id = None

        rec = HiringRecommendation(
            session_id=session_id,
            recommendation=decision.recommendation,
            reasons=decision.reasons,
            risks=decision.risks,
            probation_focus=decision.probation_focus,
            llm_call_id=llm_call_id,
            generated_by=response.model,
        )
        self._db.add(rec)
        await self._db.flush()

        logger.info(
            "hiring_recommendation_generated",
            session_id=str(session_id),
            recommendation=decision.recommendation,
        )
        return rec

    async def get(self, *, session_id: uuid.UUID) -> HiringRecommendation | None:
        """获取已有录用建议。"""
        return await self._db.scalar(
            select(HiringRecommendation).where(HiringRecommendation.session_id == session_id)
        )

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if not text:
            return ""
        if len(text) <= max_chars:
            return text
        head = text[: max_chars - 150]
        tail = text[-150:]
        return f"{head}\n...[truncated]...\n{tail}"

    @staticmethod
    def _safe_parsed(response: LLMResponse) -> HiringDecisionOutput | None:
        if isinstance(response.parsed, HiringDecisionOutput):
            return response.parsed
        try:
            return HiringDecisionOutput.model_validate_json(response.content)
        except ValidationError:
            return None


__all__ = ["HiringRecommendationService", "HiringError"]
