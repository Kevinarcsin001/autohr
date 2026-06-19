"""ExtractorService（任务 14）：简历文本 → CandidateStructure。

流程：
1. 构造 system + user prompt（含 schema 描述 + JSON 示例 + 字段约束）
2. 调 ``LLMRouter.chat(response_schema=CandidateStructure, scope="extractor")``
3. schema 校验通过 → status="extracted"
4. 第一次 schema 校验失败（LLMSchemaError）→ 重试一次（追加 "上次错误" 反馈）
   重试仍失败 → status="partial_extracted"（保留解析出的部分字段，
   字段不合法置 null + confidence=0）
5. LLM 全部不可用（LLMError）→ status="failed"

约束：
- 字段无法确定时填 null + confidence=0；绝不臆造
- prompt 显式要求 JSON 输出 + 给出 schema 示例
- **不把完整简历原文写日志**（脱敏后写 hash/length 摘要）

返回 ``ExtractResult``：``(structure, status, llm_call_id?)``
"""
from __future__ import annotations

import hashlib
import uuid
from dataclasses import dataclass

from pydantic import ValidationError

from app.adapters.llm import (
    LLMError,
    LLMResponse,
    LLMRouter,
    LLMSchemaError,
    Message,
)
from app.core.logging import get_logger
from app.schemas.candidate_structure import (
    CandidateStructure,
    ExtractStatus,
)

logger = get_logger(__name__)


# ============================================================================
# 常量
# ============================================================================


_EXTRACTOR_SCOPE: str = "extractor"

_MAX_INPUT_CHARS: int = 12000
"""简历文本截断上限（≈ 12k 中文字 ≈ 18k tokens）。
超出截断头部，避免 LLM 限流。"""


# ============================================================================
# 结果
# ============================================================================


@dataclass(frozen=True)
class ExtractResult:
    """抽取结果（含状态 + 结构化数据 + 可选 llm_call_id）。"""

    structure: CandidateStructure
    status: ExtractStatus
    llm_call_id: uuid.UUID | None = None
    error: str | None = None
    attempts: int = 1  # 实际 LLM 调用次数（1 或 2）


# ============================================================================
# Prompt 模板
# ============================================================================


_SYSTEM_PROMPT = """\
你是一名严谨的简历解析助手。你的任务是从用户提供的简历文本中提取结构化字段，
严格输出符合给定 JSON schema 的对象。

# 输出要求

1. 必须输出**纯 JSON**（不带 markdown 代码块、不带注释）。
2. JSON 必须能通过下述 schema 校验。
3. **任何不确定的字段一律填 null（list 字段填空数组 []），并把对应的
   `*_confidence` 设为 0**。绝不臆造、绝不编造。
4. `*_confidence` 取值范围 [0.0, 1.0]，表示你对该字段抽取结果的可信度。
5. `education` 取值限定：'high_school' | 'bachelor' | 'master' | 'phd' | 'other'。
6. `years_of_experience` 必须是整数（0-80），无法判断填 null。
7. `skills` 是字符串数组（去重，最多 20 个）。
8. `work_history` 是数组；每条含 company/title/start_date/end_date/description。

# Schema 示例

```json
{
  "name": "张三",
  "name_confidence": 0.95,
  "phone": "13800138000",
  "phone_confidence": 0.9,
  "email": "zhangsan@example.com",
  "email_confidence": 0.9,
  "education": "bachelor",
  "education_confidence": 0.85,
  "years_of_experience": 5,
  "years_of_experience_confidence": 0.8,
  "skills": ["Python", "FastAPI", "PostgreSQL"],
  "skills_confidence": 0.9,
  "expected_salary": "20k-30k",
  "expected_salary_confidence": 0.7,
  "current_company": "ACME 公司",
  "current_company_confidence": 0.85,
  "work_history": [
    {
      "company": "ACME 公司",
      "title": "高级工程师",
      "start_date": "2020-03",
      "end_date": "present",
      "description": "负责后端架构"
    }
  ],
  "work_history_confidence": 0.85
}
```

请记住：宁可填 null + confidence=0，也不要猜。"""


_USER_PROMPT_TEMPLATE = """\
请从以下简历文本中抽取结构化字段，严格按 schema 输出 JSON。

# 简历文本
{resume_text}"""


_RETRY_SUFFIX_TEMPLATE = """\

# 重要：上一次抽取失败

错误信息：{error}

请仔细阅读上述错误，修正 JSON 输出。**所有不确定字段必须填 null + confidence=0**，
不要遗漏 schema 中任何字段，不要包含 schema 之外的字段。"""


# ============================================================================
# 异常
# ============================================================================


class ExtractorError(Exception):
    """Extractor 顶层错误。"""


# ============================================================================
# ExtractorService
# ============================================================================


class ExtractorService:
    """简历文本 → CandidateStructure。"""

    def __init__(self, *, router: LLMRouter | None = None) -> None:
        self._router = router

    def _get_router(self) -> LLMRouter:
        if self._router is not None:
            return self._router
        # 懒加载默认 router
        from app.adapters.llm import build_default_router

        self._router = build_default_router()
        return self._router

    async def extract(self, text: str) -> ExtractResult:
        """主入口。

        Args:
            text: ParserService 输出的纯文本（resume.parsed_text）

        Returns:
            ExtractResult（status: extracted / partial_extracted / failed）
        """
        if not text or not text.strip():
            return ExtractResult(
                structure=CandidateStructure(),
                status="failed",
                error="empty input text",
            )

        truncated = self._truncate(text)
        self._log_safe_summary(text)

        messages = self._build_messages(truncated, retry_feedback=None)

        router = self._get_router()
        try:
            response = await router.chat(
                messages=messages,
                response_schema=CandidateStructure,
                temperature=0.1,
                scope=_EXTRACTOR_SCOPE,
            )
        except LLMSchemaError as exc:
            # 第一次 schema 不合 → 重试一次（带错误反馈）
            logger.warning(
                "extract_first_attempt_schema_error_retrying",
                error=str(exc)[:200],
            )
            return await self._retry_with_feedback(truncated, exc)

        except LLMError as exc:
            # Router 把 schema error 也包成 LLMError（当 fallback 链耗尽时）；
            # 用 __cause__ 区分：schema 不合 → 重试；网络/限流 → failed
            cause = exc.__cause__
            if isinstance(cause, LLMSchemaError):
                logger.warning(
                    "extract_first_attempt_schema_error_via_router_retrying",
                    error=str(cause)[:200],
                )
                return await self._retry_with_feedback(truncated, cause)

            logger.warning(
                "extract_llm_unavailable",
                error=str(exc)[:200],
            )
            return ExtractResult(
                structure=CandidateStructure(),
                status="failed",
                error=f"LLMError: {exc}"[:500],
            )

        structure = self._safe_parsed(response, CandidateStructure)
        if structure is None:
            # response_schema 路径成功但解析异常 — 视为 partial
            logger.warning(
                "extract_response_unparseable_fallback_partial",
            )
            return ExtractResult(
                structure=CandidateStructure(),
                status="partial_extracted",
                error="response.parsed was None despite schema pass",
                attempts=1,
            )

        return ExtractResult(
            structure=structure,
            status="extracted",
            attempts=1,
        )

    # ----- 重试一次 -----

    async def _retry_with_feedback(
        self, truncated_text: str, original_error: LLMSchemaError | BaseException
    ) -> ExtractResult:
        """追加 "上次错误" 反馈 → 第二次 chat。

        二次失败 → partial_extracted（保留解析出的部分字段，其他 null）
        """
        messages = self._build_messages(
            truncated_text,
            retry_feedback=str(original_error)[:500],
        )
        router = self._get_router()
        try:
            response = await router.chat(
                messages=messages,
                response_schema=CandidateStructure,
                temperature=0.0,
                scope=_EXTRACTOR_SCOPE,
            )
        except (LLMSchemaError, LLMError) as exc:
            logger.warning(
                "extract_retry_failed_partial_extracted",
                error=str(exc)[:200],
                cause_type=type(exc.__cause__).__name__ if exc.__cause__ else None,
            )
            return ExtractResult(
                structure=CandidateStructure(),
                status="partial_extracted",
                error=f"retry failed: {exc}"[:500],
                attempts=2,
            )

        structure = self._safe_parsed(response, CandidateStructure)
        if structure is None:
            return ExtractResult(
                structure=CandidateStructure(),
                status="partial_extracted",
                error="retry response.parsed was None",
                attempts=2,
            )

        # 注意：重试成功仍标 partial_extracted（按需求 7.4：重试一次后降级）
        return ExtractResult(
            structure=structure,
            status="partial_extracted",
            attempts=2,
        )

    # ----- prompt 构造 -----

    def _build_messages(
        self, truncated_text: str, *, retry_feedback: str | None
    ) -> list[Message]:
        user_content = _USER_PROMPT_TEMPLATE.format(resume_text=truncated_text)
        if retry_feedback:
            user_content += _RETRY_SUFFIX_TEMPLATE.format(error=retry_feedback)
        return [
            Message(role="system", content=_SYSTEM_PROMPT),
            Message(role="user", content=user_content),
        ]

    # ----- 工具 -----

    @staticmethod
    def _truncate(text: str) -> str:
        """超出上限截断（保留头部 + 末尾摘要）。"""
        if len(text) <= _MAX_INPUT_CHARS:
            return text
        head = text[: _MAX_INPUT_CHARS - 200]
        tail = text[-200:]
        return f"{head}\n...[truncated]...\n{tail}"

    @staticmethod
    def _log_safe_summary(text: str) -> None:
        """记录脱敏后的摘要（hash + 长度 + 字符密度），不写正文。"""
        h = hashlib.sha256(text.encode("utf-8")).hexdigest()[:16]
        logger.info(
            "extract_input_summary",
            text_sha256_prefix=h,
            text_len=len(text),
            text_nonascii_ratio=sum(1 for c in text if not c.isascii()) / max(len(text), 1),
        )

    @staticmethod
    def _safe_parsed(
        response: LLMResponse, schema: type[CandidateStructure]
    ) -> CandidateStructure | None:
        """优先用 response.parsed；为 None 时尝试 content JSON 解析。"""
        if isinstance(response.parsed, schema):
            return response.parsed
        try:
            return schema.model_validate_json(response.content)
        except ValidationError:
            return None


__all__ = [
    "ExtractorService",
    "ExtractorError",
    "ExtractResult",
]
