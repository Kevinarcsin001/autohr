"""LLM 适配器基类与数据结构。

定义统一接口：
- ``Message``：消息体（role/content）
- ``LLMResponse``：标准响应（content / parsed / tokens_in / tokens_out / latency_ms / cost_cny / model / adapter）
- ``BaseLLMAdapter``：所有 adapter 的 Protocol

设计要点：
- ``response_schema`` 可选：传入 Pydantic 模型时，adapter 输出 JSON 并解析为该模型实例；
  解析失败抛 ``LLMSchemaError``（Router 据此降级）。
- ``cost_cny`` 由 adapter 根据 token 数 × 单价估算（pricing 表见 ``base.py`` 内）。
- ``latency_ms`` 由 adapter 测量（start/stop）。
"""
from __future__ import annotations

import time
from dataclasses import dataclass, field
from decimal import Decimal
from typing import Any, Literal, Protocol, TypeVar, runtime_checkable

from pydantic import BaseModel

T = TypeVar("T", bound=BaseModel)


class LLMSchemaError(Exception):
    """LLM 输出无法按给定 schema 解析。"""


class LLMTimeoutError(Exception):
    """LLM 调用超时。"""


class LLMError(Exception):
    """LLM 调用通用错误（网络 / 鉴权 / 限流等）。"""


@dataclass(slots=True)
class Message:
    """聊天消息。"""

    role: Literal["system", "user", "assistant"]
    content: str


@dataclass(slots=True)
class LLMResponse:
    """统一 LLM 响应。

    - ``content``：模型原始文本输出
    - ``parsed``：当请求指定 ``response_schema`` 时，解析后的 Pydantic 实例
    - ``tokens_in`` / ``tokens_out``：token 统计（用于成本与 llm_calls 表）
    - ``latency_ms``：本次调用耗时
    - ``cost_cny``：估算成本（人民币元，4 位小数）
    - ``model`` / ``adapter``：实际使用的模型与适配器（路由记录用）
    """

    content: str
    adapter: str
    model: str
    tokens_in: int = 0
    tokens_out: int = 0
    latency_ms: int = 0
    cost_cny: Decimal = Decimal("0")
    parsed: Any = None
    extra: dict[str, Any] = field(default_factory=dict)


# ============================================================================
# 单价表（人民币 元 / 1K tokens）— 来源：智谱 / 阿里云 2024 公开定价
# 仅用于估算，权威统计以账单为准
# ============================================================================

_PRICING_CNY_PER_1K: dict[str, tuple[Decimal, Decimal]] = {
    # model -> (input_per_1k, output_per_1k)
    "glm-4-plus": (Decimal("0.05"), Decimal("0.05")),
    "glm-4": (Decimal("0.1"), Decimal("0.1")),
    "qwen-max": (Decimal("0.04"), Decimal("0.12")),
    "qwen-plus": (Decimal("0.0008"), Decimal("0.002")),
    "mock": (Decimal("0"), Decimal("0")),
}


def estimate_cost_cny(
    model: str, tokens_in: int, tokens_out: int
) -> Decimal:
    """根据 token 数 + 模型单价估算人民币成本。

    未知模型返回 0（保守，不抛异常）。
    """
    pricing = _PRICING_CNY_PER_1K.get(model)
    if pricing is None:
        return Decimal("0")
    in_price, out_price = pricing
    return (
        in_price * Decimal(tokens_in) / Decimal(1000)
        + out_price * Decimal(tokens_out) / Decimal(1000)
    ).quantize(Decimal("0.0001"))


@runtime_checkable
class BaseLLMAdapter(Protocol):
    """LLM 适配器接口。

    实现方需提供：
    - ``name``：适配器名（"zhipu" / "qwen" / "mock"）
    - ``default_model``：默认模型
    - ``async def chat(...)``：核心调用方法

    ``chat`` 约定：
    - ``messages`` 非空，第一条通常是 system
    - ``response_schema`` 传入时，输出必须能解析为该 schema；失败抛 LLMSchemaError
    - 超时抛 LLMTimeoutError；其他错误抛 LLMError
    """

    name: str
    default_model: str

    async def chat(
        self,
        messages: list[Message],
        response_schema: type[T] | None = None,
        temperature: float = 0.2,
        timeout: float = 30.0,
        model: str | None = None,
    ) -> LLMResponse: ...


# ============================================================================
# 计时辅助
# ============================================================================


class _Latency:
    """简单耗时上下文管理器（毫秒）。"""

    def __init__(self) -> None:
        self.ms: int = 0

    def __enter__(self) -> _Latency:
        self._start = time.perf_counter()
        return self

    def __exit__(self, *_: Any) -> None:
        self.ms = int((time.perf_counter() - self._start) * 1000)


__all__ = [
    "BaseLLMAdapter",
    "LLMResponse",
    "Message",
    "LLMError",
    "LLMSchemaError",
    "LLMTimeoutError",
    "estimate_cost_cny",
    "_Latency",
]
