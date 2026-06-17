"""ExtractorService 单元测试（任务 14）。

策略：注入 Mock LLM adapter，验证：
- 正常抽取路径 → status=extracted + schema 字段填充
- 字段无法确定 → null + confidence=0（mock 返回空对象）
- 第一次 schema 不合 → 重试一次
- 重试成功 → status=partial_extracted（按需求 7.4）
- LLM 不可用 → status=failed
- prompt 包含 schema 示例 + JSON 输出指令
- 输入超长截断
- 简历原文不进日志（只写 sha256 摘要）
"""
from __future__ import annotations

import asyncio
import json
from decimal import Decimal
from typing import Any
from unittest.mock import patch

import pytest
from pydantic import ValidationError

from app.adapters.llm import (
    LLMSchemaError,
    LLMError,
    LLMResponse,
    MockAdapter,
    Message,
)
from app.adapters.llm.router import LLMRouter
from app.schemas.candidate_structure import CandidateStructure
from app.services.extractor import ExtractorService


# ============================================================================
# Fixtures
# ============================================================================


def _make_router_with_mock(
    *,
    override: str | None = None,
    failures_before_success: int = 0,
    failure_exc: Exception | None = None,
) -> tuple[LLMRouter, MockAdapter]:
    """构造一个只挂 mock adapter 的 router（scope=extractor）。"""
    mock = MockAdapter(
        response_override=override,
        failures_before_success=failures_before_success,
        failure_exception=failure_exc,
        name="mock",
    )
    router = LLMRouter(
        adapters={"mock": mock},
        default_primary="mock",
        default_fallback=None,
    )
    return router, mock


_VALID_STRUCTURE_JSON = json.dumps(
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
        "skills": ["Python", "FastAPI"],
        "skills_confidence": 0.9,
        "expected_salary": "20k-30k",
        "expected_salary_confidence": 0.7,
        "current_company": "ACME",
        "current_company_confidence": 0.85,
        "work_history": [
            {
                "company": "ACME",
                "title": "高级工程师",
                "start_date": "2020-03",
                "end_date": "present",
                "description": "后端架构",
            }
        ],
        "work_history_confidence": 0.85,
    },
    ensure_ascii=False,
)


_EMPTY_STRUCTURE_JSON = json.dumps(
    {
        "name": None, "name_confidence": 0.0,
        "phone": None, "phone_confidence": 0.0,
        "email": None, "email_confidence": 0.0,
        "education": None, "education_confidence": 0.0,
        "years_of_experience": None, "years_of_experience_confidence": 0.0,
        "skills": [], "skills_confidence": 0.0,
        "expected_salary": None, "expected_salary_confidence": 0.0,
        "current_company": None, "current_company_confidence": 0.0,
        "work_history": [], "work_history_confidence": 0.0,
    },
    ensure_ascii=False,
)


# ============================================================================
# 正常路径
# ============================================================================


class TestExtractHappyPath:
    async def test_valid_json_returns_extracted_status(self) -> None:
        router, mock = _make_router_with_mock(override=_VALID_STRUCTURE_JSON)
        service = ExtractorService(router=router)

        result = await service.extract("张三的简历内容...")

        assert result.status == "extracted"
        assert result.attempts == 1
        assert result.structure.name == "张三"
        assert result.structure.email == "zhangsan@example.com"
        assert result.structure.years_of_experience == 5
        assert result.structure.skills == ["Python", "FastAPI"]
        assert result.structure.name_confidence == 0.95
        # mock 被调 1 次
        assert mock.call_count == 1

    async def test_prompt_includes_schema_example_and_json_instruction(self) -> None:
        router, mock = _make_router_with_mock(override=_VALID_STRUCTURE_JSON)
        service = ExtractorService(router=router)

        captured_messages: list[list[Message]] = []
        original_chat = mock.chat

        async def spy_chat(*, messages, **kw):  # noqa: ANN001
            captured_messages.append(list(messages))
            return await original_chat(messages=messages, **kw)

        with patch.object(mock, "chat", side_effect=spy_chat):
            await service.extract("test resume text")

        assert len(captured_messages) == 1
        msgs = captured_messages[0]
        assert msgs[0].role == "system"
        # 验证 prompt 包含关键约束
        sys_text = msgs[0].content
        assert "JSON" in sys_text
        assert "confidence" in sys_text
        assert "schema 示例" in sys_text or "Schema 示例" in sys_text
        assert "绝臆造" in sys_text or "不臆造" in sys_text
        # user message 包含简历文本
        assert msgs[1].role == "user"
        assert "test resume text" in msgs[1].content


# ============================================================================
# Null 字段（无法确定）
# ============================================================================


class TestNullFields:
    async def test_all_null_fields_returns_extracted_with_zero_confidence(self) -> None:
        """LLM 返回全 null → status=extracted（schema 合法），但字段全 null。"""
        router, _ = _make_router_with_mock(override=_EMPTY_STRUCTURE_JSON)
        service = ExtractorService(router=router)

        result = await service.extract("无法识别的乱码文本...")

        assert result.status == "extracted"
        assert result.structure.name is None
        assert result.structure.phone is None
        assert result.structure.email is None
        assert result.structure.name_confidence == 0.0


# ============================================================================
# 重试路径（schema 不合）
# ============================================================================


class TestRetryOnSchemaError:
    async def test_first_schema_error_retries_once_with_feedback(self) -> None:
        """第一次返回非法 JSON → 重试 → 重试返回合法 → status=partial_extracted。"""
        invalid_json = "{not valid json"
        valid_json = _VALID_STRUCTURE_JSON

        call_count = 0

        class _FlakyAdapter:
            name = "mock"
            default_model = "mock"

            async def chat(
                self, *, messages, response_schema, temperature, timeout, model
            ):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise LLMSchemaError("invalid json: syntax error")
                return LLMResponse(
                    content=valid_json,
                    adapter="mock",
                    model="mock",
                    parsed=response_schema.model_validate_json(valid_json),
                )

        adapter = _FlakyAdapter()
        router = LLMRouter(adapters={"mock": adapter}, default_primary="mock")
        service = ExtractorService(router=router)

        result = await service.extract("张三的简历...")

        assert call_count == 2
        assert result.status == "partial_extracted"
        assert result.attempts == 2
        assert result.structure.name == "张三"

    async def test_retry_feedback_appended_to_user_message(self) -> None:
        """重试时 user message 应包含 "上次错误" 反馈。"""
        captured: list[str] = []

        class _CapturingAdapter:
            name = "mock"
            default_model = "mock"

            async def chat(
                self, *, messages, response_schema, temperature, timeout, model
            ):
                user_text = messages[-1].content
                captured.append(user_text)
                if len(captured) == 1:
                    raise LLMSchemaError("missing required field: name")
                return LLMResponse(
                    content=_VALID_STRUCTURE_JSON,
                    adapter="mock",
                    model="mock",
                    parsed=response_schema.model_validate_json(_VALID_STRUCTURE_JSON),
                )

        adapter = _CapturingAdapter()
        router = LLMRouter(adapters={"mock": adapter}, default_primary="mock")
        service = ExtractorService(router=router)

        await service.extract("张三的简历...")

        assert len(captured) == 2
        # 第二次（重试）的 user message 应包含错误反馈
        assert "missing required field" in captured[1]
        assert "上次" in captured[1] or "上一次" in captured[1]

    async def test_retry_still_fails_returns_partial(self) -> None:
        """两次都 schema 不合 → status=partial_extracted + 空 structure。"""
        router, _ = _make_router_with_mock(
            failure_exc=LLMSchemaError("always fails")
        )
        service = ExtractorService(router=router)

        result = await service.extract("...")

        assert result.status == "partial_extracted"
        assert result.attempts == 2  # router 内部重试 + 我们的重试 = 总 2 次
        assert result.error is not None
        assert result.structure.name is None


# ============================================================================
# LLM 不可用
# ============================================================================


class TestLLMUnavailable:
    async def test_llm_error_returns_failed_status(self) -> None:
        # MockAdapter 默认 failures_before_success=0，必须显式指定大数才会持续抛
        router, _ = _make_router_with_mock(
            failures_before_success=10,
            failure_exc=LLMError("all adapters dead"),
        )
        service = ExtractorService(router=router)

        result = await service.extract("...")

        assert result.status == "failed"
        assert "LLMError" in (result.error or "")
        assert result.structure.name is None

    async def test_empty_input_returns_failed(self) -> None:
        router, _ = _make_router_with_mock(override=_VALID_STRUCTURE_JSON)
        service = ExtractorService(router=router)

        result = await service.extract("")
        assert result.status == "failed"
        assert "empty" in (result.error or "").lower()

        result2 = await service.extract("   ")
        assert result2.status == "failed"


# ============================================================================
# 输入截断 + 日志脱敏
# ============================================================================


class TestInputHandling:
    async def test_long_input_truncated(self) -> None:
        router, mock = _make_router_with_mock(override=_VALID_STRUCTURE_JSON)
        service = ExtractorService(router=router)

        long_text = "x" * 20000  # 超过 _MAX_INPUT_CHARS
        result = await service.extract(long_text)

        assert result.status == "extracted"
        # 截断后消息不应超过 ~12k 头部 + 200 尾部 + 模板
        # 这里只验证 service 不抛异常

    async def test_resume_text_not_logged_in_plaintext(
        self, caplog: pytest.LogCaptureFixture
    ) -> None:
        """简历原文绝对不能进日志（只允许 sha256 prefix / 长度）。"""
        import logging

        caplog.set_level(logging.INFO)

        router, _ = _make_router_with_mock(override=_VALID_STRUCTURE_JSON)
        service = ExtractorService(router=router)

        secret_text = "SUPER_SECRET_RESUME_CONTENT_13800138000_zhangsan@example.com"
        await service.extract(secret_text)

        # 所有日志拼接后不应包含原文 PII
        all_log_text = caplog.text
        assert "SUPER_SECRET_RESUME_CONTENT" not in all_log_text
        assert "13800138000" not in all_log_text
