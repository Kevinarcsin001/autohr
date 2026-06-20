"""LLM 适配器层。

公共入口：``get_router()`` 返回应用级单例 LLMRouter，由 main.py lifespan 持有。
"""
from __future__ import annotations

from app.adapters.llm.base import (
    BaseLLMAdapter,
    LLMError,
    LLMResponse,
    LLMSchemaError,
    LLMTimeoutError,
    Message,
)
from app.adapters.llm.mock import MockAdapter
from app.adapters.llm.qwen import QwenAdapter
from app.adapters.llm.router import LLMRouter, RoutePolicy
from app.adapters.llm.zhipu import ZhipuAdapter


def build_default_router() -> LLMRouter:
    """根据 settings 构建默认 Router（zhipu 主 / qwen 备）。

    API key 缺失时自动回退到 Mock 适配器，确保开发环境可用。
    """
    adapters: dict[str, BaseLLMAdapter] = {}
    if settings.ZHIPU_API_KEY:
        adapters["zhipu"] = ZhipuAdapter()
    if settings.DASHSCOPE_API_KEY:
        adapters["qwen"] = QwenAdapter()
    # Mock 始终注册，作为无 API key 时的兜底
    adapters["mock"] = MockAdapter()

    # 如果真实 adapter 不可用，用 mock 替代主/备
    effective_primary = settings.LLM_PRIMARY
    effective_fallback = settings.LLM_FALLBACK or None
    if effective_primary not in adapters:
        effective_primary = "mock"
    if effective_fallback and effective_fallback not in adapters:
        effective_fallback = None

    router = LLMRouter(
        adapters=adapters,
        default_primary=effective_primary,
        default_fallback=effective_fallback,
    )
    return router


from app.core.config import settings  # noqa: E402

__all__ = [
    "BaseLLMAdapter",
    "Message",
    "LLMResponse",
    "LLMError",
    "LLMSchemaError",
    "LLMTimeoutError",
    "LLMRouter",
    "RoutePolicy",
    "ZhipuAdapter",
    "QwenAdapter",
    "MockAdapter",
    "build_default_router",
]
