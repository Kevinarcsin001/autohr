"""InterviewService（任务 19）：5-8 题面试问题 + regenerate + 反馈。

职责：
1. **生成问题**：score 完成后调 LLM(scope='interview') 生成 5-8 题，覆盖 4 维度；
   **必须至少 1 条 weakness 追问**（schema 层强制）。
2. **短板识别**：从 ``ParsedStructure`` 取 ``skills_confidence`` / ``years_of_experience_confidence``
   等低 confidence 字段，prompt 内提示 LLM 针对短板追问（不强制走规则）。
3. **regenerate**：保留历史 batch；用 ``temperature=0.8`` 重生成（首次默认 0.3）；
   新 batch_id 与旧 batch 互不覆盖，前端按 batch_id 切换查看。
4. **反馈**：HR/面试官对单题写 ``feedback`` + ``rating(1-5)``；同 question_id 二次写覆盖。

约束（Restrictions）：
- 必备技能 ``confidence < 0.7`` 时 prompt 内显式提示至少 1 条短板追问
- regenerate 必须 ``temperature=0.8`` + 保留历史 batch_id
- 反馈必须 reviewer_id + rating 1-5（可选）+ 文本（可选）
- 不在前端默认显示反馈输入框（前端责任，service 不强制）

设计：
- ``InterviewService.generate(...)``：首次生成；temperature=0.3
- ``InterviewService.regenerate(...)``：重生成；temperature=0.8；保留历史
- ``InterviewService.save_feedback(...)``：upsert 反馈
- ``InterviewService.list_by_candidate_job(...)``：按 batch 分组返回
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Any

from pydantic import ValidationError
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.llm import (
    LLMError,
    LLMSchemaError,
    LLMResponse,
    LLMRouter,
    Message,
)
from app.core.logging import get_logger
from app.core.middleware.error_handler import NotFoundError, ValidationError as AppValidationError
from app.models.candidate import (
    Candidate,
    CandidateResume,
    ParsedStructure,
)
from app.models.interview import InterviewFeedback, InterviewQuestion
from app.models.job import Job
from app.models.score import Score
from app.models.user import User
from app.schemas.candidate_structure import CandidateStructure
from app.schemas.interview import (
    FeedbackRequest,
    InterviewQuestionOut,
    InterviewQuestions,
)

logger = get_logger(__name__)


# ============================================================================
# 常量
# ============================================================================


_INTERVIEW_SCOPE: str = "interview"

_FIRST_TEMPERATURE: float = 0.3
"""首次生成的 temperature（稳定输出，多样性低）。"""

_REGENERATE_TEMPERATURE: float = 0.8
"""重新生成的 temperature（多样性高，与原 batch 拉开差异）。"""

_LOW_CONFIDENCE_THRESHOLD: float = 0.7
"""confidence < 此值视为短板（Restrictions 要求）。"""

_MAX_RESUME_CHARS: int = 4000
"""简历关键片段截断上限。"""


# ============================================================================
# 数据类
# ============================================================================


@dataclass(frozen=True)
class InterviewResult:
    """生成结果（一批）。"""

    batch_id: uuid.UUID
    question_count: int
    temperature: float
    is_regeneration: bool
    llm_call_id: uuid.UUID | None = None


# ============================================================================
# Prompt 模板
# ============================================================================


_SYSTEM_PROMPT = """\
你是资深技术面试官。任务：根据 JD 与候选人简历关键片段，生成 **5-8 个面试问题**。

# 输出要求

1. 必须输出**纯 JSON**（不带 markdown 代码块、不带注释）。
2. JSON schema：

```json
{
  "questions": [
    {"dimension": "skill", "question": "问题文本"},
    {"dimension": "project", "question": "..."},
    {"dimension": "weakness", "question": "..."},
    {"dimension": "culture", "question": "..."}
  ]
}
```

3. ``dimension`` 取值限定：``skill`` / ``project`` / ``weakness`` / ``culture``。
4. 问题数量 5-8 题。
5. **必须至少 1 条 ``weakness`` 维度**（针对候选人短板追问）。
6. 每题 4-500 字；不要啰嗦；要让候选人能展开回答。
7. 问题必须**针对该候选人的具体简历内容**（如具体技能、具体项目），不要泛泛而问。

# 维度说明

- ``skill``：技能深挖（针对简历声明的技能，深入到原理 / 取舍）
- ``project``：项目经历追问（角色 / 难点 / 量化结果）
- ``weakness``：潜在短板验证（针对 confidence 低 / 简历不充分的能力）
- ``culture``：文化匹配（团队协作 / 抗压 / 学习方式）

请记住：**短板维度至少 1 题，必须基于简历可观察的事实**。"""


_USER_PROMPT_TEMPLATE = """\
请为以下候选人生成 5-8 个面试问题。

# 职位信息

职位：{job_title}
JD 摘要：
{jd_text}

# 候选人评分（0-100）

- 综合：{total} / 技能：{skill} / 经验：{experience}

# 候选人结构化字段

- 学历：{education}
- 工作年限：{years_of_experience}
- 技能：{skills}

# 短板提示（confidence 偏低字段）

{weakness_hint}

# 简历关键片段

{resume_snippet}

请输出 JSON。"""


# ============================================================================
# 异常
# ============================================================================


class InterviewError(Exception):
    """Interview 顶层错误。"""


# ============================================================================
# InterviewService
# ============================================================================


class InterviewService:
    """面试问题生成 + regenerate + 反馈。"""

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

    # ----- 主入口：生成 -----

    async def generate(
        self,
        *,
        candidate_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> InterviewResult:
        """首次生成面试问题；temperature=0.3。"""
        return await self._generate_internal(
            candidate_id=candidate_id,
            job_id=job_id,
            is_regeneration=False,
        )

    async def regenerate(
        self,
        *,
        candidate_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> InterviewResult:
        """重新生成；temperature=0.8；保留历史 batch。"""
        return await self._generate_internal(
            candidate_id=candidate_id,
            job_id=job_id,
            is_regeneration=True,
        )

    async def _generate_internal(
        self,
        *,
        candidate_id: uuid.UUID,
        job_id: uuid.UUID,
        is_regeneration: bool,
    ) -> InterviewResult:
        candidate = await self._db.get(Candidate, candidate_id)
        if candidate is None:
            raise NotFoundError(
                f"candidate {candidate_id} not found", resource="candidate"
            )
        job = await self._db.get(Job, job_id)
        if job is None:
            raise NotFoundError(
                f"job {job_id} not found", resource="job"
            )

        score = await self._db.scalar(
            select(Score).where(
                Score.job_id == job_id,
                Score.candidate_id == candidate_id,
            )
        )
        # 评分未完成时也允许生成问题（HR 可能想提前）；prompt 内 total=-

        structure, parsed_text = await self._fetch_structure_and_text(candidate_id)
        snippet = self._truncate(parsed_text or "", _MAX_RESUME_CHARS)
        weakness_hint = self._build_weakness_hint(structure)

        temperature = (
            _REGENERATE_TEMPERATURE if is_regeneration else _FIRST_TEMPERATURE
        )

        self._log_safe_summary(
            candidate_id=candidate_id,
            job_id=job_id,
            snippet=snippet,
            is_regeneration=is_regeneration,
        )

        messages = self._build_messages(
            job_title=job.title,
            jd_text=self._truncate(job.jd_text, 800),
            score=score,
            structure=structure,
            weakness_hint=weakness_hint,
            snippet=snippet,
        )

        router = self._get_router()
        try:
            response = await router.chat(
                messages=messages,
                response_schema=InterviewQuestions,
                temperature=temperature,
                scope=_INTERVIEW_SCOPE,
            )
        except LLMSchemaError as exc:
            logger.warning("interview_schema_error", error=str(exc)[:200])
            raise InterviewError(f"LLM schema error: {exc}") from exc
        except LLMError as exc:
            logger.warning("interview_llm_unavailable", error=str(exc)[:200])
            raise InterviewError(f"LLM unavailable: {exc}") from exc

        questions_payload = self._safe_parsed(response)
        if questions_payload is None:
            raise InterviewError(
                f"LLM response.parsed is None; content={response.content[:200]!r}"
            )

        batch_id = uuid.uuid4()
        await self._persist_batch(
            batch_id=batch_id,
            candidate_id=candidate_id,
            job_id=job_id,
            questions=questions_payload,
            model_used=response.model,
        )

        llm_call_id = response.extra.get("llm_call_id")
        llm_call_id = llm_call_id if isinstance(llm_call_id, uuid.UUID) else None

        logger.info(
            "interview_generated",
            candidate_id=str(candidate_id),
            job_id=str(job_id),
            batch_id=str(batch_id),
            count=len(questions_payload.questions),
            is_regeneration=is_regeneration,
            temperature=temperature,
        )

        return InterviewResult(
            batch_id=batch_id,
            question_count=len(questions_payload.questions),
            temperature=temperature,
            is_regeneration=is_regeneration,
            llm_call_id=llm_call_id,
        )

    # ----- 列表 -----

    async def list_latest_batch(
        self,
        *,
        candidate_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> tuple[list[InterviewQuestion], uuid.UUID | None]:
        """列出最新 batch 的问题（按 sort_order 升序）+ batch_id。

        无 batch 时返回 ([], None)。
        """
        # 找最新 batch_id
        latest_batch = await self._db.scalar(
            select(InterviewQuestion.batch_id)
            .where(
                InterviewQuestion.candidate_id == candidate_id,
                InterviewQuestion.job_id == job_id,
            )
            .order_by(InterviewQuestion.created_at.desc())
            .limit(1)
        )
        if latest_batch is None:
            return [], None

        result = await self._db.execute(
            select(InterviewQuestion)
            .where(
                InterviewQuestion.candidate_id == candidate_id,
                InterviewQuestion.job_id == job_id,
                InterviewQuestion.batch_id == latest_batch,
            )
            .order_by(InterviewQuestion.sort_order.asc())
        )
        return list(result.scalars().all()), latest_batch

    async def list_batch(
        self,
        *,
        candidate_id: uuid.UUID,
        job_id: uuid.UUID,
        batch_id: uuid.UUID,
    ) -> list[InterviewQuestion]:
        """列出指定 batch 的问题。"""
        result = await self._db.execute(
            select(InterviewQuestion)
            .where(
                InterviewQuestion.candidate_id == candidate_id,
                InterviewQuestion.job_id == job_id,
                InterviewQuestion.batch_id == batch_id,
            )
            .order_by(InterviewQuestion.sort_order.asc())
        )
        return list(result.scalars().all())

    async def list_batches(
        self,
        *,
        candidate_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> tuple[list[uuid.UUID], uuid.UUID | None, int]:
        """列出所有 batch（按 created_at 倒序）+ 当前 batch + 总题数。"""
        result = await self._db.execute(
            select(InterviewQuestion.batch_id, InterviewQuestion.created_at)
            .where(
                InterviewQuestion.candidate_id == candidate_id,
                InterviewQuestion.job_id == job_id,
            )
            .order_by(InterviewQuestion.created_at.desc())
            .distinct()
        )
        rows = result.all()
        if not rows:
            return [], None, 0
        batches = [r[0] for r in rows]
        # 总题数
        count_result = await self._db.execute(
            select(InterviewQuestion).where(
                InterviewQuestion.candidate_id == candidate_id,
                InterviewQuestion.job_id == job_id,
            )
        )
        total = len(count_result.scalars().all())
        return batches, batches[0], total

    # ----- 反馈 -----

    async def save_feedback(
        self,
        *,
        question_id: uuid.UUID,
        reviewer_id: uuid.UUID,
        payload: FeedbackRequest,
    ) -> tuple[InterviewFeedback, InterviewQuestion]:
        """upsert 反馈：同 question_id 已有则更新。

        Returns:
            ``(feedback_row, question_row)``
        """
        question = await self._db.get(InterviewQuestion, question_id)
        if question is None:
            raise NotFoundError(
                f"interview question {question_id} not found",
                resource="interview_question",
            )

        # 校验 reviewer 存在
        reviewer = await self._db.get(User, reviewer_id)
        if reviewer is None:
            raise NotFoundError(
                f"reviewer {reviewer_id} not found", resource="user"
            )

        if payload.feedback is None and payload.rating is None:
            raise AppValidationError(
                "feedback 或 rating 至少需要提供一个"
            )

        existing = await self._db.scalar(
            select(InterviewFeedback).where(
                InterviewFeedback.question_id == question_id,
                InterviewFeedback.reviewer_id == reviewer_id,
            )
        )
        if existing is not None:
            if payload.feedback is not None:
                existing.feedback = payload.feedback
            if payload.rating is not None:
                existing.rating = payload.rating
            return existing, question

        new = InterviewFeedback(
            question_id=question_id,
            reviewer_id=reviewer_id,
            feedback=payload.feedback,
            rating=payload.rating,
        )
        self._db.add(new)
        await self._db.flush()
        return new, question

    async def list_feedback(
        self, *, question_id: uuid.UUID
    ) -> list[InterviewFeedback]:
        """列出某题的所有反馈（按时间倒序）。"""
        result = await self._db.execute(
            select(InterviewFeedback)
            .where(InterviewFeedback.question_id == question_id)
            .order_by(InterviewFeedback.created_at.desc())
        )
        return list(result.scalars().all())

    # ----- prompt 构造 -----

    def _build_messages(
        self,
        *,
        job_title: str,
        jd_text: str,
        score: Score | None,
        structure: CandidateStructure | None,
        weakness_hint: str,
        snippet: str,
    ) -> list[Message]:
        user_content = _USER_PROMPT_TEMPLATE.format(
            job_title=job_title,
            jd_text=jd_text,
            total=score.total if score else "-",
            skill=score.skill if score and score.skill is not None else "-",
            experience=(
                score.experience if score and score.experience is not None else "-"
            ),
            education=structure.education if structure else "(未知)",
            years_of_experience=(
                structure.years_of_experience if structure else "(未知)"
            ),
            skills=", ".join(structure.skills) if structure else "(无)",
            weakness_hint=weakness_hint,
            resume_snippet=snippet,
        )
        return [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=user_content),
        ]

    @staticmethod
    def _build_weakness_hint(
        structure: CandidateStructure | None,
    ) -> str:
        """从 structure 中挑出 confidence < 0.7 的字段作为短板提示。

        若 JobHardRequirement.required_skills 中某 skill 在 structure.skills
        但 skills_confidence < 0.7 → 也提示。
        """
        if structure is None:
            return "（无结构化数据；请基于简历片段自行识别潜在短板）"

        weaknesses: list[str] = []

        # 各字段 confidence 检查
        field_checks = [
            ("技能", structure.skills_confidence,
             f"skills={structure.skills}"),
            ("工作年限", structure.years_of_experience_confidence,
             f"years_of_experience={structure.years_of_experience}"),
            ("学历", structure.education_confidence,
             f"education={structure.education}"),
            ("当前公司", structure.current_company_confidence,
             f"current_company={structure.current_company}"),
            ("工作经历", structure.work_history_confidence,
             f"work_history={len(structure.work_history)} 条"),
        ]
        for label, conf, value in field_checks:
            if conf is not None and conf < _LOW_CONFIDENCE_THRESHOLD:
                weaknesses.append(f"- {label}（confidence={conf:.2f}, {value}）")

        if not weaknesses:
            return "（结构化字段 confidence 均 ≥ 0.7；请从简历片段中识别可能的潜在短板）"
        return "\n".join(weaknesses)

    # ----- 内部工具 -----

    async def _fetch_structure_and_text(
        self, candidate_id: uuid.UUID
    ) -> tuple[CandidateStructure | None, str | None]:
        """取最新 ParsedStructure + parsed_text。"""
        stmt = (
            select(ParsedStructure.data, CandidateResume.parsed_text)
            .join(
                CandidateResume,
                CandidateResume.id == ParsedStructure.resume_id,
            )
            .where(CandidateResume.candidate_id == candidate_id)
            .order_by(CandidateResume.uploaded_at.desc())
            .limit(1)
        )
        row = (await self._db.execute(stmt)).first()
        if row is None:
            return None, None

        structure_data, parsed_text = row
        inner = (
            structure_data.get("structure")
            if isinstance(structure_data, dict)
            else None
        )
        if not isinstance(inner, dict):
            return None, parsed_text
        try:
            return CandidateStructure.model_validate(inner), parsed_text
        except ValidationError:
            return None, parsed_text

    async def _persist_batch(
        self,
        *,
        batch_id: uuid.UUID,
        candidate_id: uuid.UUID,
        job_id: uuid.UUID,
        questions: InterviewQuestions,
        model_used: str,
    ) -> None:
        """写入本批问题（不覆盖历史 batch）。"""
        for idx, q in enumerate(questions.questions):
            self._db.add(
                InterviewQuestion(
                    candidate_id=candidate_id,
                    job_id=job_id,
                    batch_id=batch_id,
                    dimension=q.dimension,
                    question=q.question,
                    sort_order=idx,
                    generated_by=model_used,
                )
            )
        await self._db.flush()

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if not text:
            return ""
        if len(text) <= max_chars:
            return text
        head = text[: max_chars - 200]
        tail = text[-200:]
        return f"{head}\n...[truncated]...\n{tail}"

    @staticmethod
    def _safe_parsed(response: LLMResponse) -> InterviewQuestions | None:
        if isinstance(response.parsed, InterviewQuestions):
            return response.parsed
        try:
            return InterviewQuestions.model_validate_json(response.content)
        except ValidationError:
            return None

    @staticmethod
    def _log_safe_summary(
        *,
        candidate_id: uuid.UUID,
        job_id: uuid.UUID,
        snippet: str,
        is_regeneration: bool,
    ) -> None:
        snip_h = hashlib.sha256(snippet.encode("utf-8")).hexdigest()[:16]
        logger.info(
            "interview_input_summary",
            candidate_id=str(candidate_id),
            job_id=str(job_id),
            is_regeneration=is_regeneration,
            snippet_sha256_prefix=snip_h,
            snippet_len=len(snippet),
        )


__all__ = [
    "InterviewService",
    "InterviewError",
    "InterviewResult",
]
