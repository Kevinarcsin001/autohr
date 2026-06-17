"""智谱 GLM-4-Plus 适配器。

依赖：``zhipuai`` SDK（已在 pyproject.toml 中）。

行为：
- 用 SDK 的 ``client.chat.completions.create`` 调用
- ``response_schema`` 传入时在 system prompt 后追加 JSON 输出指令；
  SDK 不直接支持 JSON mode（OpenAI 兼容层 4.5+ 支持 ``response_format``），
  本适配器在 system prompt 中显式要求 JSON 输出，再用宽松解析提取
- token 统计从 SDK 响应的 ``usage`` 字段提取
- 异常统一映射为 ``LLMError`` / ``LLMTimeoutError``
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


class ZhipuAdapter:
    """智谱 GLM-4-Plus 适配器。"""

    name: str = "zhipu"
    default_model: str = settings.ZHIPU_MODEL or "glm-4-plus"

    def __init__(
        self,
        api_key: str | None = None,
        default_model: str | None = None,
    ) -> None:
        self._api_key = api_key or settings.ZHIPU_API_KEY
        if default_model:
            self.default_model = default_model
        self._client: Any = None  # lazy

    def _get_client(self) -> Any:
        if self._client is None:
            if not self._api_key:
                raise LLMError("ZHIPU_API_KEY is not configured")
            try:
                from zhipuai import ZhipuAI
            except ImportError as e:
                raise LLMError(
                    "zhipuai SDK not installed; run pip install zhipuai"
                ) from e
            self._client = ZhipuAI(api_key=self._api_key)
        return self._client

    async def chat(
        self,
        messages: list[Message],
        response_schema: type[T] | None = None,
        temperature: float = 0.2,
        timeout: float = 30.0,
        model: str | None = None,
    ) -> LLMResponse:
        used_model = model or self.default_model
        client = self._get_client()

        prepared = self._prepare_messages(messages, response_schema)
        kwargs: dict[str, Any] = {
            "model": used_model,
            "messages": prepared,
            "temperature": temperature,
        }

        with _Latency() as latency:
            try:
                raw = await asyncio.to_thread(
                    client.chat.completions.create, **kwargs
                )
            except asyncio.TimeoutError as e:
                raise LLMTimeoutError(f"zhipu timeout after {timeout}s") from e
            except Exception as e:
                # zhipuai SDK 抛各种异常（APIStatusError / APIRequestError 等）
                msg = str(e).lower()
                if "timeout" in msg or "timed out" in msg:
                    raise LLMTimeoutError(str(e)) from e
                raise LLMError(f"zhipu call failed: {e}") from e

        content = raw.choices[0].message.content or ""
        usage = getattr(raw, "usage", None)
        tokens_in = getattr(usage, "prompt_tokens", 0) if usage else 0
        tokens_out = getattr(usage, "completion_tokens", 0) if usage else 0

        parsed: Any = None
        if response_schema is not None:
            try:
                parsed = parse_to_schema(content, response_schema)
            except LLMSchemaError as e:
                _logger.warning(
                    "zhipu_schema_parse_failed",
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


__all__ = ["ZhipuAdapter"]
