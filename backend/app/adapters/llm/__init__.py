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

    API key 缺失的 adapter 不会被注册（避免运行时才发现配置问题）。
    """
    adapters: dict[str, BaseLLMAdapter] = {}
    if settings.ZHIPU_API_KEY:
        adapters["zhipu"] = ZhipuAdapter()
    if settings.DASHSCOPE_API_KEY:
        adapters["qwen"] = QwenAdapter()
    # 测试/开发态默认带上 mock（不进入默认路由）
    adapters["mock"] = MockAdapter()
    return LLMRouter(adapters=adapters)


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
