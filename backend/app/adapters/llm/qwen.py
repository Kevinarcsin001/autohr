"""通义千问 qwen-max 适配器。

依赖：``dashscope`` SDK。

行为：
- 用 ``dashscope.Generation.call`` 同步 API（包 ``asyncio.to_thread``）
- ``response_schema`` 传入时在 system prompt 后追加 JSON 输出指令
- token 统计从响应 ``usage`` 字段提取
- 异常映射为 ``LLMError`` / ``LLMTimeoutError``
"""
from __future__ import annotations

import asyncio
from typing import Any, TypeVar

from pydantic import BaseModel

from app.adapters.llm._json import parse_to_schema
from app.adapters.llm.base import (
    LLMError,
    LLMResponse,
    LLMSchemaError,
    LLMTimeoutError,
    Message,
    _Latency,
    estimate_cost_cny,
)
from app.core.config import settings
from app.core.logging import get_logger

T = TypeVar("T", bound=BaseModel)

_logger = get_logger(__name__)

_JSON_SUFFIX = (
    "\n\n请严格只输出一个 JSON 对象，不要附加任何说明文字、代码块标记或前后缀。"
)


class QwenAdapter:
    """通义千问 qwen-max 适配器。"""

    name: str = "qwen"
    default_model: str = settings.QWEN_MODEL or "qwen-max"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self._api_key = api_key or settings.DASHSCOPE_API_KEY
        if default_model:
            self.default_model = default_model

    async def chat(
        self,
        messages: list[Message],
        response_schema: type[T] | None = None,
        temperature: float = 0.2,
        timeout: float = 30.0,
        model: str | None = None,
    ) -> LLMResponse:
        used_model = model or self.default_model
        if not self._api_key:
            raise LLMError("DASHSCOPE_API_KEY is not configured")

        try:
            import dashscope
        except ImportError as e:
            raise LLMError(
                "dashscope SDK not installed; run pip install dashscope"
            ) from e

        dashscope.api_key = self._api_key
        prepared = self._prepare_messages(messages, response_schema)

        with _Latency() as latency:
            try:
                raw = await asyncio.to_thread(
                    dashscope.Generation.call,
                    model=used_model,
                    messages=prepared,
                    result_format="message",
                    temperature=temperature,
                    timeout=timeout,
                )
            except asyncio.TimeoutError as e:
                raise LLMTimeoutError(f"qwen timeout after {timeout}s") from e
            except Exception as e:
                msg = str(e).lower()
                if "timeout" in msg or "timed out" in msg:
                    raise LLMTimeoutError(str(e)) from e
                raise LLMError(f"qwen call failed: {e}") from e

        if raw.status_code != 200:
            raise LLMError(
                f"qwen api error: code={raw.status_code} "
                f"message={getattr(raw, 'message', '')}"
            )

        # 输出结构：{"output": {"choices": [{"message": {"content": "..."}}]}, "usage": {...}}
        output = getattr(raw, "output", None) or raw.get("output", {}) if isinstance(raw, dict) else {}
        choices = output.get("choices", []) if isinstance(output, dict) else []
        if not choices:
            raise LLMError(f"qwen empty choices: raw={raw!r}")
        content = choices[0]["message"]["content"] or ""

        usage = getattr(raw, "usage", None) or (raw.get("usage", {}) if isinstance(raw, dict) else {})
        tokens_in = usage.get("input_tokens", 0) if isinstance(usage, dict) else 0
        tokens_out = usage.get("output_tokens", 0) if isinstance(usage, dict) else 0

        parsed: Any = None
        if response_schema is not None:
            try:
                parsed = parse_to_schema(content, response_schema)
            except LLMSchemaError:
                _logger.warning(
                    "qwen_schema_parse_failed",
                    model=used_model,
                    content_preview=content[:200],
                )
                raise

        return LLMResponse(
            content=content,
            adapter=self.name,
            model=used_model,
            tokens_in=int(tokens_in or 0),
            tokens_out=int(tokens_out or 0),
            latency_ms=latency.ms,
            cost_cny=estimate_cost_cny(used_model, tokens_in or 0, tokens_out or 0),
            parsed=parsed,
        )

    @staticmethod
    def _prepare_messages(
        messages: list[Message], response_schema: type[BaseModel] | None
    ) -> list[dict[str, str]]:
        out: list[dict[str, str]] = []
        for m in messages:
            out.append({"role": m.role, "content": m.content})
        if response_schema is not None and out:
            out[0] = {
                "role": out[0]["role"],
                "content": out[0]["content"] + _JSON_SUFFIX,
            }
        return out


__all__ = ["QwenAdapter"]
