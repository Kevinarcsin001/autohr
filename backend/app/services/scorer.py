"""ScorerService（任务 17）：候选人 × JD 综合评分 + 子维度。

流程：
1. 构造 system + user prompt（JD 摘要 + 结构化字段 + 简历关键片段，截断省 token）
2. 调 ``LLMRouter.chat(response_schema=ScoreDimensions, scope="scorer")``
3. Router 自动 fallback；scores.model_used 记录实际使用模型（取自 response.model）
4. upsert scores 行（UNIQUE job_id + candidate_id）
5. ``llm_call_id`` 从 ``response.extra["llm_call_id"]`` 取（Router 在写 llm_calls 后回填）

约束：
- 评分必须是 0-100 整数（schema 层校验）
- prompt 显式要求 LLM 引用具体简历片段作为打分依据
- 不要把简历原文整体塞进 prompt（取关键片段，省 token）
- 同分排名二级排序：skill > experience > name（字典序）

ScoringInput 由 caller 准备（避免 service 直接读 raw_text，便于单测）。
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
from app.models.candidate import Candidate
from app.models.score import Score
from app.schemas.candidate_structure import CandidateStructure
from app.schemas.score import ScoreDimensions

logger = get_logger(__name__)


# ============================================================================
# 常量
# ============================================================================


_SCORER_SCOPE: str = "scorer"

_MAX_JD_CHARS: int = 800
"""JD 摘要截断上限（避免长 JD 把 prompt 撑爆）。"""

_MAX_SNIPPET_CHARS: int = 3000
"""简历关键片段截断上限（≈ 3k 中文字 ≈ 4.5k tokens）。"""


# ============================================================================
# 数据类
# ============================================================================


@dataclass(frozen=True)
class ScoringInput:
    """单次评分的全部输入（避免 service 直接读 raw_text）。"""

    job_id: uuid.UUID
    candidate_id: uuid.UUID
    job_title: str
    jd_text: str
    structure: CandidateStructure
    resume_snippet: str
    """简历关键片段（由 caller 从 parsed_text 截取，避开 LLM prompt 撑爆）。"""


@dataclass(frozen=True)
class ScoreResult:
    """评分结果。"""

    dimensions: ScoreDimensions
    model_used: str
    llm_call_id: uuid.UUID | None
    attempts: int = 1


# ============================================================================
# Prompt 模板
# ============================================================================


_SYSTEM_PROMPT = """\
你是资深 HR 评估顾问。任务：根据 JD 与候选人简历关键片段，对候选人进行多维度评分。

# 输出要求

1. 必须输出**纯 JSON**（不带 markdown 代码块、不带注释）。
2. JSON 必须能通过下述 schema 校验，**所有 6 个维度都必须给出 0-100 的整数**。
3. 评分必须严格基于候选人简历中的具体事实（技能、年限、项目、学历等）；
   不要凭空臆断；找不到依据就给 50 上下的中位分，并尽量给出理由（理由放思考，不输出）。
4. ``total`` 是综合分（不是其余 5 个维度的平均），需你独立判断加权。

# 维度定义

- ``total``：综合匹配度（0-100）
- ``skill``：技能匹配度（候选人技能集与 JD 必备/加分技能的相关度）
- ``experience``：经验相关性（年限是否达标 + 项目方向是否对口）
- ``education``：学历匹配（学校层次 / 专业相关度 / 是否满足最低要求）
- ``stability``：稳定性（跳槽频率、最近任职时长；越稳定分越高）
- ``potential``：成长潜力（晋升轨迹、技术广度、学习能力的间接证据）

# Schema

```json
{
  "total": 80,
  "skill": 85,
  "experience": 80,
  "education": 70,
  "stability": 75,
  "potential": 80
}
```

请记住：**所有评分必须基于简历中可验证的事实片段**，宁可保守也不要虚高。"""


_USER_PROMPT_TEMPLATE = """\
请对以下候选人进行多维度评分（0-100 整数）。

# 职位信息

职位：{job_title}

JD 摘要：
{jd_text}

# 候选人结构化字段

- 姓名：{name}
- 学历：{education}
- 工作年限：{years_of_experience}
- 技能：{skills}
- 当前公司：{current_company}
- 工作经历：{work_history}

# 简历关键片段（节选）

{resume_snippet}

请基于以上信息输出 JSON 评分。"""


# ============================================================================
# 异常
# ============================================================================


class ScorerError(Exception):
    """Scorer 顶层错误。"""


# ============================================================================
# 排序辅助
# ============================================================================


def score_sort_key(
    *, total: int, skill: int | None, experience: int | None, name: str
) -> tuple[int, int, int, str]:
    """同分排名二级排序键。

    要求：total 倒序 → skill 倒序 → experience 倒序 → name 字典序正序。

    用法：
        ``sorted(items, key=lambda x: score_sort_key(...))``
        由于倒序维度取负，正序 name 直接用，sorted 自然返回正确顺序。

    Args:
        total: 总分（必填）
        skill: 技能子分（None 视作 0）
        experience: 经验子分（None 视作 0）
        name: 候选人姓名（用于最终字典序正序）

    Returns:
        4 元组；sorted() 升序即为所求排名。
    """
    s = skill if skill is not None else 0
    e = experience if experience is not None else 0
    return (-total, -s, -e, name)


# ============================================================================
# ScorerService
# ============================================================================


class ScorerService:
    """候选人 × JD 综合评分。"""

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

    # ----- 主入口 -----

    async def score(self, payload: ScoringInput) -> ScoreResult:
        """对单个候选人评分并写库。

        Raises:
            ScorerError: LLM 全链路失败 / schema 始终不合法
        """
        jd_truncated = self._truncate(payload.jd_text, _MAX_JD_CHARS)
        snippet_truncated = self._truncate(
            payload.resume_snippet, _MAX_SNIPPET_CHARS
        )
        self._log_safe_summary(payload)

        messages = self._build_messages(
            payload=payload,
            jd_text=jd_truncated,
            snippet=snippet_truncated,
        )

        router = self._get_router()
        try:
            response = await router.chat(
                messages=messages,
                response_schema=ScoreDimensions,
                temperature=0.2,
                scope=_SCORER_SCOPE,
            )
        except LLMSchemaError as exc:
            logger.warning(
                "scorer_schema_error_all_failed",
                error=str(exc)[:200],
            )
            raise ScorerError(f"LLM schema error: {exc}") from exc
        except LLMError as exc:
            logger.warning(
                "scorer_llm_unavailable",
                error=str(exc)[:200],
            )
            raise ScorerError(f"LLM unavailable: {exc}") from exc

        dimensions = self._safe_parsed(response)
        if dimensions is None:
            raise ScorerError(
                f"LLM response.parsed is None; content={response.content[:200]!r}"
            )

        # upsert scores 行
        llm_call_id = response.extra.get("llm_call_id")
        await self._upsert_score(
            job_id=payload.job_id,
            candidate_id=payload.candidate_id,
            dimensions=dimensions,
            model_used=response.model,
            llm_call_id=llm_call_id if isinstance(llm_call_id, uuid.UUID) else None,
        )

        logger.info(
            "scorer_completed",
            job_id=str(payload.job_id),
            candidate_id=str(payload.candidate_id),
            total=dimensions.total,
            model_used=response.model,
            llm_call_id=str(llm_call_id) if llm_call_id else None,
        )

        return ScoreResult(
            dimensions=dimensions,
            model_used=response.model,
            llm_call_id=llm_call_id if isinstance(llm_call_id, uuid.UUID) else None,
        )

    # ----- 批量入口（共享 router / db session） -----

    async def score_batch(
        self, payloads: list[ScoringInput]
    ) -> list[ScoreResult | Exception]:
        """批量评分；任一失败不阻塞其他；返回与输入等长的结果列表。"""
        out: list[ScoreResult | Exception] = []
        for p in payloads:
            try:
                out.append(await self.score(p))
            except Exception as exc:  # noqa: BLE001
                logger.warning(
                    "scorer_batch_item_failed",
                    candidate_id=str(p.candidate_id),
                    error=str(exc)[:200],
                )
                out.append(exc)
        return out

    # ----- 列表（带二级排序） -----

    async def list_by_job(
        self, *, job_id: uuid.UUID, limit: int = 100, offset: int = 0
    ) -> tuple[list[tuple[Score, str | None]], int]:
        """列出 job 下的评分（按 total 倒序 → skill → experience → name 字典序）。

        Returns:
            ``([(score, candidate_name), ...], total)``
        """
        base = (
            select(Score, Candidate.name)
            .join(Candidate, Candidate.id == Score.candidate_id)
            .where(Score.job_id == job_id)
        )
        # 在 DB 层做 total / skill / experience 倒序；name 字典序在 Python 层补充稳定
        # （DB COALESCE + NULLS LAST 复杂度更高，留给 Python）
        stmt = base.order_by(
            Score.total.desc(),
            Score.skill.desc().nulls_last(),
            Score.experience.desc().nulls_last(),
        ).limit(limit).offset(offset)
        result = await self._db.execute(stmt)
        rows = list(result.all())

        # Python 层稳定化：保留 DB 顺序但相同 (total, skill, experience) 内按 name 升序
        # stable_sort = sorted with key 只能调整 tie 内部
        rows_sorted = self._stable_sort_by_name(rows)

        count_stmt = select(Score).where(Score.job_id == job_id)
        total = len((await self._db.execute(count_stmt)).scalars().all())
        return rows_sorted, total

    @staticmethod
    def _stable_sort_by_name(
        rows: list[tuple[Score, str | None]],
    ) -> list[tuple[Score, str | None]]:
        """保持 DB 排序不变，仅在 (total, skill, experience) 相同时按 name 升序。"""
        if not rows:
            return rows

        def group_key(item: tuple[Score, str | None]) -> tuple[int, int, int]:
            s, _name = item
            return (
                s.total,
                s.skill if s.skill is not None else -1,
                s.experience if s.experience is not None else -1,
            )

        out: list[tuple[Score, str | None]] = []
        i = 0
        n = len(rows)
        while i < n:
            j = i
            while (
                j + 1 < n
                and group_key(rows[j + 1]) == group_key(rows[i])
            ):
                j += 1
            group = rows[i : j + 1]
            if len(group) > 1:
                group = sorted(
                    group,
                    key=lambda x: (x[1] or ""),
                )
            out.extend(group)
            i = j + 1
        return out

    # ----- prompt 构造 -----

    def _build_messages(
        self,
        *,
        payload: ScoringInput,
        jd_text: str,
        snippet: str,
    ) -> list[Message]:
        user_content = _USER_PROMPT_TEMPLATE.format(
            job_title=payload.job_title,
            jd_text=jd_text,
            name=payload.structure.name or "(未知)",
            education=payload.structure.education or "(未知)",
            years_of_experience=payload.structure.years_of_experience or "(未知)",
            skills=", ".join(payload.structure.skills) or "(无)",
            current_company=payload.structure.current_company or "(未知)",
            work_history=self._format_work_history(payload.structure.work_history),
            resume_snippet=snippet,
        )
        return [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=user_content),
        ]

    @staticmethod
    def _format_work_history(work_history: Any) -> str:
        if not work_history:
            return "(无)"
        lines = []
        for w in work_history:
            company = getattr(w, "company", None) or "?"
            title = getattr(w, "title", None) or "?"
            lines.append(f"- {company} / {title}")
        return "\n".join(lines)

    # ----- 内部工具 -----

    @staticmethod
    def _truncate(text: str, max_chars: int) -> str:
        if len(text) <= max_chars:
            return text
        head = text[: max_chars - 200]
        tail = text[-200:]
        return f"{head}\n...[truncated]...\n{tail}"

    @staticmethod
    def _log_safe_summary(payload: ScoringInput) -> None:
        """脱敏日志：只记 hash 与长度，不写正文。"""
        jd_h = hashlib.sha256(payload.jd_text.encode("utf-8")).hexdigest()[:16]
        snip_h = hashlib.sha256(
            payload.resume_snippet.encode("utf-8")
        ).hexdigest()[:16]
        logger.info(
            "scorer_input_summary",
            job_id=str(payload.job_id),
            candidate_id=str(payload.candidate_id),
            jd_sha256_prefix=jd_h,
            jd_len=len(payload.jd_text),
            snippet_sha256_prefix=snip_h,
            snippet_len=len(payload.resume_snippet),
        )

    @staticmethod
    def _safe_parsed(response: LLMResponse) -> ScoreDimensions | None:
        if isinstance(response.parsed, ScoreDimensions):
            return response.parsed
        try:
            return ScoreDimensions.model_validate_json(response.content)
        except ValidationError:
            return None

    async def _upsert_score(
        self,
        *,
        job_id: uuid.UUID,
        candidate_id: uuid.UUID,
        dimensions: ScoreDimensions,
        model_used: str,
        llm_call_id: uuid.UUID | None,
    ) -> Score:
        """upsert scores 行（UNIQUE job_id + candidate_id）。"""
        existing = await self._db.scalar(
            select(Score).where(
                Score.job_id == job_id,
                Score.candidate_id == candidate_id,
            )
        )
        if existing is not None:
            existing.total = dimensions.total
            existing.skill = dimensions.skill
            existing.experience = dimensions.experience
            existing.education = dimensions.education
            existing.stability = dimensions.stability
            existing.potential = dimensions.potential
            existing.model_used = model_used
            existing.llm_call_id = llm_call_id
            return existing

        new = Score(
            job_id=job_id,
            candidate_id=candidate_id,
            total=dimensions.total,
            skill=dimensions.skill,
            experience=dimensions.experience,
            education=dimensions.education,
            stability=dimensions.stability,
            potential=dimensions.potential,
            model_used=model_used,
            llm_call_id=llm_call_id,
        )
        self._db.add(new)
        await self._db.flush()
        return new


# ============================================================================
# 公共辅助
# ============================================================================


def build_scoring_snippet(parsed_text: str | None, max_chars: int = 3000) -> str:
    """从 parsed_text 中取关键片段（截断头部 + 尾部摘要）。

    约束（需求 9.2 + Restrictions）：
    - 不把简历原文整体塞进 prompt
    - 头部通常含基本身份 + 教育背景
    - 尾部通常含近期项目（更相关）
    """
    if not parsed_text:
        return ""
    if len(parsed_text) <= max_chars:
        return parsed_text
    head_len = max_chars - 800
    head = parsed_text[:head_len]
    tail = parsed_text[-800:]
    return f"{head}\n...[truncated]...\n{tail}"


__all__ = [
    "ScorerService",
    "ScorerError",
    "ScoringInput",
    "ScoreResult",
    "score_sort_key",
    "build_scoring_snippet",
]
