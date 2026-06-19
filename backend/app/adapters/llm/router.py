"""LLM 路由器：scope-based 主备切换 + 熔断 + 重试 + llm_calls 表写入。

策略：
1. 按 ``scope`` 查路由策略（primary, fallback）；找不到则用默认 (zhipu, qwen)。
2. 调 primary：
   - 失败重试 1 次（同模型）
   - 再失败 → 切 fallback
   - fallback 失败 → 抛错（design.md 错误处理 #5：双模型同时不可用）
3. 单模型 5 分钟内连续 3 次失败 → 标记 cooling 5 分钟，路由期间跳过。

所有调用（成功 / 失败）写入 ``llm_calls`` 表用于成本/性能/降级统计。

线程安全：``_CIRCUIT`` 在同一进程内共享；多 worker 场景下每个 worker 独立维护
（用 Redis 共享熔断状态留待后续优化）。
"""
from __future__ import annotations

import asyncio
import time
import uuid
from dataclasses import dataclass, field
from typing import Any, TypeVar

from pydantic import BaseModel

from app.adapters.llm.base import (
    BaseLLMAdapter,
    LLMError,
    LLMResponse,
    LLMSchemaError,
    LLMTimeoutError,
    Message,
)
from app.core.config import settings
from app.core.logging import get_logger

T = TypeVar("T", bound=BaseModel)

_logger = get_logger(__name__)

# ============================================================================
# 熔断器
# ============================================================================


@dataclass
class _CircuitState:
    """单 adapter 的熔断状态。"""

    consecutive_failures: int = 0
    cooling_until: float = 0.0  # timestamp (monotonic)


class _CircuitBreaker:
    """熔断器：5min 内连续 N 次失败 → cooling 5min。

    所有 adapter 共享一个实例（进程级）。
    """

    def __init__(
        self,
        failure_threshold: int = 3,
        cooldown_seconds: int = 300,
    ) -> None:
        self._failure_threshold = failure_threshold
        self._cooldown_seconds = cooldown_seconds
        self._states: dict[str, _CircuitState] = {}

    def is_cooling(self, adapter_name: str) -> bool:
        s = self._states.get(adapter_name)
        if s is None:
            return False
        return s.cooling_until > time.monotonic()

    def record_success(self, adapter_name: str) -> None:
        s = self._states.setdefault(adapter_name, _CircuitState())
        s.consecutive_failures = 0
        s.cooling_until = 0.0

    def record_failure(self, adapter_name: str) -> None:
        s = self._states.setdefault(adapter_name, _CircuitState())
        s.consecutive_failures += 1
        if s.consecutive_failures >= self._failure_threshold:
            s.cooling_until = time.monotonic() + self._cooldown_seconds
            _logger.warning(
                "llm_circuit_opened",
                adapter=adapter_name,
                failures=s.consecutive_failures,
                cooldown_seconds=self._cooldown_seconds,
            )


# ============================================================================
# 路由策略
# ============================================================================


@dataclass
class RoutePolicy:
    """单 scope 的路由策略。"""

    primary: str  # adapter name
    fallback: str | None = None


@dataclass
class LLMRouter:
    """LLM 路由器（应用级单例，由 main.py 持有）。

    Args:
        adapters: adapter 名 → 实例 映射
        default_primary: settings.LLM_PRIMARY
        default_fallback: settings.LLM_FALLBACK
        failure_threshold: 连续失败阈值（默认 3）
        cooldown_seconds: 熔断时长（默认 300s）
        max_retries: 单 adapter 重试次数（默认 1）
    """

    adapters: dict[str, BaseLLMAdapter]
    default_primary: str = field(default_factory=lambda: settings.LLM_PRIMARY)
    default_fallback: str | None = field(
        default_factory=lambda: settings.LLM_FALLBACK or None
    )
    failure_threshold: int = field(
        default_factory=lambda: settings.LLM_CIRCUIT_BREAKER_FAILURES
    )
    cooldown_seconds: int = field(
        default_factory=lambda: settings.LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS
    )
    max_retries: int = field(default_factory=lambda: settings.LLM_MAX_RETRIES)
    scope_policies: dict[str, RoutePolicy] = field(default_factory=dict)
    _breaker: _CircuitBreaker = field(init=False)
    _team_id: uuid.UUID | None = None

    def __post_init__(self) -> None:
        self._breaker = _CircuitBreaker(
            failure_threshold=self.failure_threshold,
            cooldown_seconds=self.cooldown_seconds,
        )

    def set_team_context(self, team_id: uuid.UUID | None) -> None:
        """设置当前 team_id（写 llm_calls 用）。可被中间件 / Celery task 重置。"""
        self._team_id = team_id

    def configure_scope(
        self, scope: str, primary: str, fallback: str | None = None
    ) -> None:
        """为指定 scope 配置路由策略（覆盖默认）。"""
        self.scope_policies[scope] = RoutePolicy(
            primary=primary, fallback=fallback
        )

    def _resolve_chain(self, scope: str) -> list[str]:
        """返回 scope 的调用链（primary → fallback），跳过 cooling 中的 adapter。"""
        policy = self.scope_policies.get(scope)
        primary = policy.primary if policy else self.default_primary
        fallback = policy.fallback if policy else self.default_fallback

        chain: list[str] = []
        for name in [primary, fallback]:
            if not name or name in chain:
                continue
            if name not in self.adapters:
                _logger.warning("llm_adapter_not_found", adapter=name)
                continue
            if self._breaker.is_cooling(name):
                _logger.info(
                    "llm_adapter_skipped_cooling", adapter=name, scope=scope
                )
                continue
            chain.append(name)
        return chain

    async def chat(
        self,
        messages: list[Message],
        response_schema: type[T] | None = None,
        temperature: float = 0.2,
        timeout: float = float(settings.LLM_TIMEOUT_SECONDS),
        scope: str = "default",
        model_overrides: dict[str, str] | None = None,
    ) -> LLMResponse:
        """路由 + 重试 + 熔断 + 写 llm_calls。

        Raises:
            LLMError: 所有 adapter 都失败
        """
        chain = self._resolve_chain(scope)
        if not chain:
            raise LLMError(
                f"No available LLM adapter for scope={scope!r} "
                "(all cooling or unconfigured)"
            )

        last_error: Exception | None = None
        for idx, adapter_name in enumerate(chain):
            adapter = self.adapters[adapter_name]
            model = (model_overrides or {}).get(adapter_name)
            attempts = self.max_retries + 1
            for attempt in range(1, attempts + 1):
                try:
                    resp = await adapter.chat(
                        messages=messages,
                        response_schema=response_schema,
                        temperature=temperature,
                        timeout=timeout,
                        model=model,
                    )
                    self._breaker.record_success(adapter_name)
                    await self._log_call(scope, resp, success=True)
                    return resp
                except (LLMTimeoutError, LLMError, LLMSchemaError) as e:
                    last_error = e
                    _logger.warning(
                        "llm_call_attempt_failed",
                        adapter=adapter_name,
                        attempt=attempt,
                        max_attempts=attempts,
                        error_type=type(e).__name__,
                        error_message=str(e)[:200],
                    )
                    # 仅对网络/超时类错误重试；schema 错误立刻切 fallback
                    if isinstance(e, LLMSchemaError):
                        break
                    if attempt >= attempts:
                        break
                    # 指数退避（base 200ms）
                    await asyncio.sleep(0.2 * (2 ** (attempt - 1)))

            # adapter 失败累计 → 更新熔断
            self._breaker.record_failure(adapter_name)
            if idx < len(chain) - 1:
                _logger.info(
                    "llm_switching_fallback",
                    failed=adapter_name,
                    fallback=chain[idx + 1],
                    scope=scope,
                )

        # 所有 adapter 都失败
        await self._log_call(
            scope,
            None,
            success=False,
            error=str(last_error)[:500] if last_error else "unknown",
        )
        raise LLMError(
            f"All LLM adapters failed for scope={scope!r}: "
            f"last_error={last_error!r}"
        ) from last_error

    async def _log_call(
        self,
        scope: str,
        response: LLMResponse | None,
        success: bool,
        error: str | None = None,
    ) -> None:
        """写 llm_calls 表（best-effort：DB 失败不阻塞主流程）。

        成功写入后把 ``call.id`` 回写到 ``response.extra["llm_call_id"]``，
        让调用方能在持久化业务数据时引用。
        """
        try:
            from app.core.db import AsyncSessionLocal
            from app.models.llm_call import LLMCall
            from app.models.types import LLMScope

            # 校验 scope 在 ENUM 内（fallback 到 'extractor'）
            valid_scopes = {
                e.name for e in LLMScope().enum_class.enums  # type: ignore[attr-defined]
            } if False else {"extractor", "scorer", "reasoning", "interview"}
            scope_value = scope if scope in valid_scopes else "extractor"

            async with AsyncSessionLocal() as session:
                call = LLMCall(
                    adapter=response.adapter if response else "unknown",
                    model=response.model if response else "unknown",
                    scope=scope_value,
                    tokens_in=response.tokens_in if response else 0,
                    tokens_out=response.tokens_out if response else 0,
                    latency_ms=response.latency_ms if response else 0,
                    cost_cny=response.cost_cny if response else None,
                    success=success,
                    error=error,
                    team_id=self._team_id,
                )
                session.add(call)
                await session.commit()
                if response is not None and success:
                    response.extra["llm_call_id"] = call.id
        except Exception as e:
            _logger.warning(
                "llm_call_log_failed",
                scope=scope,
                error=str(e)[:200],
            )


__all__ = ["LLMRouter", "RoutePolicy", "_CircuitBreaker"]
