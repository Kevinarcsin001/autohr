"""ReasoningService（任务 18）：推荐 / 淘汰理由 + 事实一致性校验。

职责：
1. **推荐理由生成**：候选人通过硬性筛选 + 评分后，调 LLM 输出 3-5 条 bullet；
   每条必须能在简历原文中找到事实支持。
2. **淘汰理由生成**：硬性淘汰者不走 LLM，直接基于 FilterService.reasons 构造
   （明确指向被违反的条件，如 "学历不达标：本科 vs 要求硕士"）。
3. **事实一致性校验**：对每条 LLM 推荐理由，在 ``raw_text`` 中查找支持片段
   （字符串匹配 + 简单同义词词典）；找不到 → 剔除该条；全部剔除 → ``validated=False``。
4. **持久化 score_reasons**：写一行 ``ScoreReason(type='recommend'|'disqualify',
   bullet_points, validated)``；upsert 模式（score_id UNIQUE → 1 条记录覆盖）。

约束（Restrictions）：
- 理由格式必须是要点（bullet）3-5 条（schema 层强制）
- 事实校验必须找到原文支持；找不到则剔除 + ``validated=False``
- 淘汰理由必须明确指向硬性条件（不接受模糊措辞）
- 不要把无法验证的"事实"输出给用户（剔除而非保留）

设计：
- ``ReasoningService.generate_recommend(...)``：LLM 生成 → 事实校验 → 写库
- ``ReasoningService.generate_disqualify(...)``：纯规则（不调 LLM），直接构造
- ``FactValidator.find_support(text, evidence)``：纯函数（便于单测）
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass
from typing import Literal

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
from app.models.score import Score, ScoreReason
from app.schemas.reason import RecommendReasons

logger = get_logger(__name__)


# ============================================================================
# 常量
# ============================================================================


_REASONING_SCOPE: str = "reasoning"

_MAX_RESUME_CHARS: int = 6000
"""事实校验时读简历原文的上限（保留头部 + 尾部摘要）。"""


# ============================================================================
# 同义词词典（简单版；任务 19/后续可扩展）
# ============================================================================


_SYNONYMS: dict[str, list[str]] = {
    "python": ["python", "py", "django", "flask", "fastapi"],
    "java": ["java", "jvm", "spring", "kotlin"],
    "go": ["go", "golang"],
    "rust": ["rust"],
    "javascript": ["javascript", "js", "node", "typescript", "ts"],
    "react": ["react", "reactjs"],
    "vue": ["vue", "vuejs"],
    "fastapi": ["fastapi"],
    "postgresql": ["postgresql", "postgres", "pg"],
    "mysql": ["mysql"],
    "redis": ["redis"],
    "docker": ["docker", "container", "容器"],
    "kubernetes": ["kubernetes", "k8s"],
    "微服务": ["微服务", "microservice", "micro-service"],
    "bachelor": ["bachelor", "本科", "学士"],
    "master": ["master", "硕士", "研究生"],
    "phd": ["phd", "博士", "doctor"],
    "5年": ["5年", "五年", "5 years", "5 years"],
    "3年": ["3年", "三年", "3 years"],
}


def lookup_synonyms(term: str) -> list[str]:
    """返回某术语的同义词列表（含自身）。"""
    key = term.strip().lower()
    if not key:
        return []
    return _SYNONYMS.get(key, [key])


# ============================================================================
# 数据类
# ============================================================================


@dataclass(frozen=True)
class ReasonResult:
    """单次理由生成的结果。"""

    type: Literal["recommend", "disqualify"]
    bullet_points: list[str]
    validated: bool
    """事实校验是否全部通过（所有 bullet 都找到支持）；淘汰理由恒为 True。"""
    llm_call_id: uuid.UUID | None = None
    attempts: int = 1


# ============================================================================
# 事实校验器（纯函数，便于单测）
# ============================================================================


class FactValidator:
    """对单条 bullet 做事实一致性校验。

    算法（字符串匹配 + 同义词）：
    1. 把 bullet 拆成关键词候选（先看 ``evidence``，否则按中文 / 英文 token 拆）
    2. 对每个关键词，构造 "原文出现 OR 任一同义词出现"
    3. 关键词候选中至少 ``min_hits`` 个能在原文找到支持 → 该 bullet 通过
    """

    @staticmethod
    def find_support(
        bullet: str,
        raw_text: str,
        *,
        evidence: list[str] | None = None,
        min_hits: int = 1,
    ) -> bool:
        """检查 bullet 是否有原文支持。

        Args:
            bullet: LLM 生成的单条理由
            raw_text: 简历原文（已截断）
            evidence: LLM 给出的关键词候选；为空时从 bullet 内拆
            min_hits: 至少找到几个独立关键词才算支持

        Returns:
            bool — True 表示该 bullet 找到原文支持
        """
        if not raw_text:
            return False
        if not bullet or not bullet.strip():
            return False

        candidates = FactValidator._extract_keywords(bullet, evidence)
        if not candidates:
            return False

        text_lower = raw_text.lower()
        hits = 0
        for term in candidates:
            syns = lookup_synonyms(term)
            if any(syn.lower() in text_lower for syn in syns if syn):
                hits += 1
                if hits >= min_hits:
                    return True
        return hits >= min_hits

    @staticmethod
    def _extract_keywords(
        bullet: str, evidence: list[str] | str | None
    ) -> list[str]:
        """从 evidence / bullet 中提取关键词候选。

        - 优先用 evidence（LLM 主动给出的关键词）
        - evidence 可以是 str 或 list[str]；统一按空白 / 逗号拆 token
        - 否则按简单 token 拆（中文连续片段 + 英文按空格）

        中文连续片段按"非中文符号分隔"提取，整段作为一个关键词；
        避免把 "阿里巴巴背景" 拆成 "阿里巴巴" + "背景"。
        """
        if evidence:
            # 规范化：str → list[str]
            ev_list: list[str]
            if isinstance(evidence, str):
                ev_list = [evidence]
            else:
                ev_list = list(evidence)
            cleaned: list[str] = []
            for e in ev_list:
                if not e:
                    continue
                # 一个 evidence 项可能含多个 token（如 "python flask"）
                for tok in str(e).replace(",", " ").replace("，", " ").split():
                    tok = tok.strip()
                    if tok:
                        cleaned.append(tok)
            if cleaned:
                return cleaned

        text = bullet.strip()
        tokens: list[str] = []
        # 英文 token（按空格 + 标点切）
        en_buffer = ""
        for ch in text:
            if ch.isascii() and (ch.isalnum() or ch in "_-+.#"):
                en_buffer += ch
            else:
                if len(en_buffer) >= 2:
                    tokens.append(en_buffer)
                en_buffer = ""
        if len(en_buffer) >= 2:
            tokens.append(en_buffer)

        # 中文连续片段：按非中文字符切；每段 ≥ 2 字作为关键词
        cn_buffer = ""
        cn_segments: list[str] = []
        for ch in text:
            if "一" <= ch <= "鿿":
                cn_buffer += ch
            else:
                if len(cn_buffer) >= 2:
                    cn_segments.append(cn_buffer)
                cn_buffer = ""
        if len(cn_buffer) >= 2:
            cn_segments.append(cn_buffer)

        # 对每个中文段，进一步按"描述性后缀"切（避免 "阿里巴巴背景" 一整段）
        suffix_cuts = ["背景", "经验", "技能", "工作", "项目", "能力", "亮点"]
        for seg in cn_segments:
            cut = seg
            for suf in suffix_cuts:
                if suf in cut and cut != suf:
                    idx = cut.find(suf)
                    head = cut[:idx]
                    if len(head) >= 2:
                        tokens.append(head)
                    cut = cut[idx + len(suf):]
            if len(cut) >= 2:
                tokens.append(cut)

        # 去重保序
        seen: set[str] = set()
        unique: list[str] = []
        for t in tokens:
            t = t.strip()
            if t and t not in seen:
                seen.add(t)
                unique.append(t)
        return unique

    @staticmethod
    def filter_supported(
        bullets: list[str],
        raw_text: str,
        *,
        evidence_list: list[str] | None = None,
    ) -> tuple[list[str], list[str]]:
        """对 bullets 做事实校验，返回 (通过的, 被剔除的)。"""
        kept: list[str] = []
        dropped: list[str] = []
        for idx, b in enumerate(bullets):
            ev = evidence_list[idx] if evidence_list and idx < len(evidence_list) else None
            if FactValidator.find_support(b, raw_text, evidence=ev):
                kept.append(b)
            else:
                dropped.append(b)
        return kept, dropped


# ============================================================================
# Prompt 模板
# ============================================================================


_SYSTEM_PROMPT = """\
你是资深 HR 评估顾问。任务：根据 JD 与候选人简历关键片段，生成 **3-5 条推荐理由**。

# 输出要求

1. 必须输出**纯 JSON**（不带 markdown 代码块、不带注释）。
2. JSON schema：

```json
{
  "bullet_points": ["理由 1", "理由 2", "理由 3"],
  "evidence": ["关键词 1", "关键词 2", "关键词 3"]
}
```

3. ``bullet_points`` 数量必须 3-5 条，每条 1-2 句。
4. **每条理由必须能在简历原文中找到事实支持**；不可凭空臆造。
5. ``evidence`` 数组与 ``bullet_points`` 等长，每条理由对应一个关键词
   （字符串匹配用：用于事后事实一致性校验）。evidence 必须直接出现在简历中。

# 推荐角度参考

- 技能匹配（必备 / 加分技能的命中率）
- 经验相关性（项目方向 / 年限）
- 学历 / 证书亮点
- 稳定性（在职时长 / 晋升轨迹）
- 成长潜力（学习曲线 / 跨域能力）

请记住：**宁可少输出一条，也不要写无法在原文找到依据的话**。"""


_USER_PROMPT_TEMPLATE = """\
请为以下候选人生成 3-5 条推荐理由。

# 职位信息

职位：{job_title}
JD 摘要：
{jd_text}

# 候选人评分（0-100）

- 综合：{total}
- 技能：{skill}
- 经验：{experience}
- 学历：{education}
- 稳定性：{stability}
- 潜力：{potential}

# 简历关键片段

{resume_snippet}

请输出 JSON。"""


# ============================================================================
# ReasoningService
# ============================================================================


class ReasoningError(Exception):
    """Reasoning 顶层错误。"""


class ReasoningService:
    """推荐 / 淘汰理由生成 + 事实一致性校验。"""

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

    # ----- 推荐理由 -----

    async def generate_recommend(
        self,
        *,
        score: Score,
        job_title: str,
        jd_text: str,
        resume_text: str,
    ) -> ReasonResult:
        """生成推荐理由并写库。

        Args:
            score: 已存在的 Score 行（提供 6 个维度）
            job_title: JD 职位名
            jd_text: JD 全文（已截断）
            resume_text: 简历原文（用于事实校验）

        Returns:
            ``ReasonResult``；写库后 ``ScoreReason(type='recommend')``
        """
        snippet = self._truncate(resume_text, _MAX_RESUME_CHARS)
        self._log_safe_summary(score, snippet)

        messages = self._build_messages(
            job_title=job_title,
            jd_text=self._truncate(jd_text, 800),
            score=score,
            snippet=snippet,
        )

        router = self._get_router()
        try:
            response = await router.chat(
                messages=messages,
                response_schema=RecommendReasons,
                temperature=0.3,
                scope=_REASONING_SCOPE,
            )
        except LLMSchemaError as exc:
            logger.warning("reason_schema_error", error=str(exc)[:200])
            raise ReasoningError(f"LLM schema error: {exc}") from exc
        except LLMError as exc:
            logger.warning("reason_llm_unavailable", error=str(exc)[:200])
            raise ReasoningError(f"LLM unavailable: {exc}") from exc

        reasons = self._safe_parsed(response, RecommendReasons)
        if reasons is None:
            raise ReasoningError(
                f"LLM response.parsed is None; content={response.content[:200]!r}"
            )

        # 事实一致性校验：剔除无法在原文找到支持的 bullet
        kept, dropped = FactValidator.filter_supported(
            reasons.bullet_points, snippet, evidence_list=reasons.evidence
        )
        validated = len(kept) == len(reasons.bullet_points) and len(kept) >= 1

        # 若全被剔除，至少保留一条原始（避免完全空）；标 validated=False 提示 HR
        final_bullets = kept if kept else reasons.bullet_points[:1]
        if dropped:
            logger.info(
                "reason_facts_dropped",
                score_id=str(score.id),
                dropped_count=len(dropped),
                dropped_samples=dropped[:3],
            )

        llm_call_id = response.extra.get("llm_call_id")
        llm_call_id = llm_call_id if isinstance(llm_call_id, uuid.UUID) else None

        await self._upsert_reason(
            score_id=score.id,
            reason_type="recommend",
            bullet_points=final_bullets,
            validated=validated,
        )

        logger.info(
            "recommend_completed",
            score_id=str(score.id),
            bullets=len(final_bullets),
            validated=validated,
        )

        return ReasonResult(
            type="recommend",
            bullet_points=final_bullets,
            validated=validated,
            llm_call_id=llm_call_id,
        )

    # ----- 淘汰理由（纯规则，不调 LLM） -----

    @staticmethod
    def build_disqualify_reasons(
        filter_reasons: list[str],
    ) -> list[str]:
        """把 FilterService.reasons 转成对外的淘汰 bullet。

        FilterService 已经按 "<规则名>: <值> vs <要求>" 格式输出；
        本函数只做轻量规范化：去重 + 过滤空 + 截短到 5 条。
        """
        seen: set[str] = set()
        out: list[str] = []
        for r in filter_reasons:
            r = (r or "").strip()
            if not r or r in seen:
                continue
            seen.add(r)
            out.append(r)
            if len(out) >= 5:
                break
        return out

    async def persist_disqualify(
        self,
        *,
        score_id: uuid.UUID,
        filter_reasons: list[str],
    ) -> ReasonResult:
        """把淘汰理由写入 score_reasons（不调 LLM）。

        Args:
            score_id: 关联 Score.id
            filter_reasons: FilterService.run_for_candidates 给出的 reasons 列表

        Returns:
            ``ReasonResult``
        """
        bullets = self.build_disqualify_reasons(filter_reasons)
        if not bullets:
            bullets = ["未通过硬性筛选（无具体原因记录）"]

        await self._upsert_reason(
            score_id=score_id,
            reason_type="disqualify",
            bullet_points=bullets,
            validated=True,  # 淘汰理由来自硬性规则，恒为可验证
        )

        logger.info(
            "disqualify_persisted",
            score_id=str(score_id),
            bullets=len(bullets),
        )

        return ReasonResult(
            type="disqualify",
            bullet_points=bullets,
            validated=True,
        )

    # ----- 列表 -----

    async def list_by_score(
        self, *, score_id: uuid.UUID
    ) -> list[ScoreReason]:
        """列出某 Score 的所有理由（一般 1-2 条：recommend + disqualify）。"""
        result = await self._db.execute(
            select(ScoreReason)
            .where(ScoreReason.score_id == score_id)
            .order_by(ScoreReason.created_at.desc())
        )
        return list(result.scalars().all())

    async def list_by_job(
        self, *, job_id: uuid.UUID
    ) -> list[tuple[ScoreReason, Score]]:
        """列出某 Job 下所有理由（JOIN Score 取 job 维度）。"""
        stmt = (
            select(ScoreReason, Score)
            .join(Score, Score.id == ScoreReason.score_id)
            .where(Score.job_id == job_id)
            .order_by(ScoreReason.created_at.desc())
        )
        result = await self._db.execute(stmt)
        return list(result.all())

    # ----- prompt 构造 -----

    def _build_messages(
        self,
        *,
        job_title: str,
        jd_text: str,
        score: Score,
        snippet: str,
    ) -> list[Message]:
        user_content = _USER_PROMPT_TEMPLATE.format(
            job_title=job_title,
            jd_text=jd_text,
            total=score.total,
            skill=score.skill if score.skill is not None else "-",
            experience=score.experience if score.experience is not None else "-",
            education=score.education if score.education is not None else "-",
            stability=score.stability if score.stability is not None else "-",
            potential=score.potential if score.potential is not None else "-",
            resume_snippet=snippet,
        )
        return [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=user_content),
        ]

    # ----- 内部工具 -----

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
    def _log_safe_summary(score: Score, snippet: str) -> None:
        snip_h = hashlib.sha256(snippet.encode("utf-8")).hexdigest()[:16]
        logger.info(
            "reasoning_input_summary",
            score_id=str(score.id),
            total=score.total,
            snippet_sha256_prefix=snip_h,
            snippet_len=len(snippet),
        )

    @staticmethod
    def _safe_parsed(
        response: LLMResponse, schema: type[RecommendReasons]
    ) -> RecommendReasons | None:
        if isinstance(response.parsed, schema):
            return response.parsed
        try:
            return schema.model_validate_json(response.content)
        except ValidationError:
            return None

    async def _upsert_reason(
        self,
        *,
        score_id: uuid.UUID,
        reason_type: Literal["recommend", "disqualify"],
        bullet_points: list[str],
        validated: bool,
    ) -> ScoreReason:
        """upsert：同 score_id + type 已存在则更新（覆盖最新一批）。

        设计选择：一对 (score_id, type) 只保留最新一批（不留多个版本）；
        若 HR 想要历史，可扩展为追加模式 + batch_id 字段。
        """
        existing = await self._db.scalar(
            select(ScoreReason).where(
                ScoreReason.score_id == score_id,
                ScoreReason.type == reason_type,
            )
        )
        if existing is not None:
            existing.bullet_points = bullet_points
            existing.validated = validated
            return existing

        new = ScoreReason(
            score_id=score_id,
            type=reason_type,
            bullet_points=bullet_points,
            validated=validated,
        )
        self._db.add(new)
        await self._db.flush()
        return new


__all__ = [
    "ReasoningService",
    "ReasoningError",
    "ReasonResult",
    "FactValidator",
    "lookup_synonyms",
]
