"""LLMRouter 测试：主备切换 / 熔断 / 重试 / llm_calls 写入。"""
from __future__ import annotations

import asyncio
from typing import Any

import pytest
from pydantic import BaseModel

from app.adapters.llm.base import (
    LLMError,
    LLMResponse,
    LLMSchemaError,
    LLMTimeoutError,
    Message,
)
from app.adapters.llm.mock import MockAdapter
from app.adapters.llm.router import LLMRouter, RoutePolicy, _CircuitBreaker


class _Demo(BaseModel):
    name: str
    score: int


def _make_response(adapter: str, content: str = "ok") -> LLMResponse:
    return LLMResponse(
        content=content,
        adapter=adapter,
        model=adapter,
        tokens_in=10,
        tokens_out=20,
        latency_ms=5,
    )


# ============================================================================
# 熔断器
# ============================================================================


class TestCircuitBreaker:
    def test_no_state_not_cooling(self) -> None:
        b = _CircuitBreaker(failure_threshold=3, cooldown_seconds=300)
        assert b.is_cooling("zhipu") is False

    def test_success_resets(self) -> None:
        b = _CircuitBreaker(failure_threshold=3, cooldown_seconds=300)
        b.record_failure("zhipu")
        b.record_failure("zhipu")
        b.record_success("zhipu")
        assert b.is_cooling("zhipu") is False

    def test_threshold_opens_circuit(self) -> None:
        b = _CircuitBreaker(failure_threshold=3, cooldown_seconds=300)
        for _ in range(3):
            b.record_failure("zhipu")
        assert b.is_cooling("zhipu") is True

    def test_below_threshold_not_open(self) -> None:
        b = _CircuitBreaker(failure_threshold=3, cooldown_seconds=300)
        b.record_failure("zhipu")
        b.record_failure("zhipu")
        assert b.is_cooling("zhipu") is False

    def test_cooldown_expires(self) -> None:
        """模拟冷却到期。"""
        import time as _time

        b = _CircuitBreaker(failure_threshold=3, cooldown_seconds=1)
        for _ in range(3):
            b.record_failure("zhipu")
        assert b.is_cooling("zhipu") is True

        # 把 cooling_until 改成过去时间
        b._states["zhipu"].cooling_until = _time.monotonic() - 1
        assert b.is_cooling("zhipu") is False


# ============================================================================
# 路由链解析
# ============================================================================


class TestResolveChain:
    def _make_router(
        self, primary="zhipu", fallback="qwen"
    ) -> LLMRouter:
        return LLMRouter(
            adapters={
                "zhipu": MockAdapter(name="zhipu"),
                "qwen": MockAdapter(name="qwen"),
                "mock": MockAdapter(name="mock"),
            },
            default_primary=primary,
            default_fallback=fallback,
            max_retries=1,
            failure_threshold=3,
            cooldown_seconds=300,
        )

    def test_default_chain(self) -> None:
        r = self._make_router()
        assert r._resolve_chain("default") == ["zhipu", "qwen"]

    def test_scope_policy_override(self) -> None:
        r = self._make_router()
        r.configure_scope("scoring", primary="qwen", fallback="mock")
        assert r._resolve_chain("scoring") == ["qwen", "mock"]

    def test_skips_cooling_adapter(self) -> None:
        r = self._make_router()
        # 让 zhipu 进入冷却
        for _ in range(3):
            r._breaker.record_failure("zhipu")
        # cooling 中的 zhipu 被跳过，只剩 qwen
        chain = r._resolve_chain("default")
        assert "zhipu" not in chain
        assert chain == ["qwen"]

    def test_all_cooling_returns_empty(self) -> None:
        r = self._make_router()
        for adapter in ("zhipu", "qwen"):
            for _ in range(3):
                r._breaker.record_failure(adapter)
        assert r._resolve_chain("default") == []

    def test_unknown_adapter_skipped(self) -> None:
        r = LLMRouter(
            adapters={"zhipu": MockAdapter(name="zhipu")},
            default_primary="zhipu",
            default_fallback="nonexistent",
        )
        assert r._resolve_chain("default") == ["zhipu"]


# ============================================================================
# 主备切换
# ============================================================================


class TestFallback:
    async def test_primary_success_no_fallback(self) -> None:
        router = LLMRouter(
            adapters={
                "zhipu": MockAdapter(name="zhipu", response_override="zhipu-ok")
            },
            default_primary="zhipu",
            default_fallback=None,
        )
        resp = await router.chat(messages=[])
        assert resp.content == "zhipu-ok"
        assert resp.adapter == "zhipu"

    async def test_primary_fails_switches_to_fallback(self) -> None:
        """primary 总是失败 → 切 fallback 成功。"""
        router = LLMRouter(
            adapters={
                "zhipu": MockAdapter(name="zhipu", failures_before_success=99),
                "qwen": MockAdapter(
                    name="qwen", response_override="qwen-ok"
                ),
            },
            default_primary="zhipu",
            default_fallback="qwen",
            max_retries=0,  # 不要重试，立刻切
        )
        resp = await router.chat(messages=[])
        assert resp.adapter == "qwen"
        assert resp.content == "qwen-ok"

    async def test_retry_then_success_on_primary(self) -> None:
        """primary 第 1 次失败，重试后成功。"""
        router = LLMRouter(
            adapters={
                "zhipu": MockAdapter(
                    name="zhipu", failures_before_success=1
                ),
                "qwen": MockAdapter(name="qwen", response_override="qwen"),
            },
            default_primary="zhipu",
            default_fallback="qwen",
            max_retries=1,
        )
        resp = await router.chat(messages=[])
        assert resp.adapter == "zhipu"
        assert router.adapters["zhipu"].call_count == 2  # 1 fail + 1 success

    async def test_all_fail_raises_llm_error(self) -> None:
        router = LLMRouter(
            adapters={
                "zhipu": MockAdapter(
                    name="zhipu", failures_before_success=99
                ),
                "qwen": MockAdapter(name="qwen", failures_before_success=99),
            },
            default_primary="zhipu",
            default_fallback="qwen",
            max_retries=0,
        )
        with pytest.raises(LLMError) as exc_info:
            await router.chat(messages=[])
        assert "All LLM adapters failed" in str(exc_info.value)

    async def test_schema_error_skips_retry_immediately(self) -> None:
        """LLMSchemaError 立刻切 fallback，不重试。"""
        from unittest.mock import AsyncMock

        bad = AsyncMock()
        bad.chat.side_effect = LLMSchemaError("bad schema")
        good = AsyncMock()
        good.chat.return_value = _make_response("qwen", "ok")
        good.name = "qwen"
        good.default_model = "qwen"

        router = LLMRouter(
            adapters={"zhipu": bad, "qwen": good},
            default_primary="zhipu",
            default_fallback="qwen",
            max_retries=3,  # 即使允许重试，schema error 应只调 1 次
        )
        resp = await router.chat(messages=[])
        assert resp.adapter == "qwen"
        # bad.chat 只被调一次（schema error 不重试）
        assert bad.chat.call_count == 1

    async def test_no_available_adapter_raises(self) -> None:
        """所有 adapter 都 cooling → 立刻抛错。"""
        router = LLMRouter(
            adapters={
                "zhipu": MockAdapter(name="zhipu"),
                "qwen": MockAdapter(name="qwen"),
            },
            default_primary="zhipu",
            default_fallback="qwen",
        )
        for name in ("zhipu", "qwen"):
            for _ in range(3):
                router._breaker.record_failure(name)
        with pytest.raises(LLMError) as exc_info:
            await router.chat(messages=[])
        assert "No available" in str(exc_info.value)


# ============================================================================
# token 统计 + llm_calls 写入
# ============================================================================


class TestCallLogging:
    async def test_success_logs_llm_call_row(self) -> None:
        """成功调用写入 llm_calls 表。"""
        from sqlalchemy import select

        from app.core.db import AsyncSessionLocal
        from app.models.llm_call import LLMCall

        router = LLMRouter(
            adapters={
                "zhipu": MockAdapter(
                    name="zhipu",
                    response_override="ok",
                    tokens_in=42,
                    tokens_out=88,
                )
            },
            default_primary="zhipu",
            default_fallback=None,
        )
        resp = await router.chat(messages=[], scope="scorer")
        assert resp.tokens_in == 42
        assert resp.tokens_out == 88

        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(LLMCall).where(
                        LLMCall.adapter == "zhipu", LLMCall.scope == "scorer"
                    )
                )
            ).scalars().all()
            assert len(rows) >= 1
            r = rows[-1]
            assert r.tokens_in == 42
            assert r.tokens_out == 88
            assert r.success is True
            assert r.cost_cny is not None

    async def test_failure_logs_error(self) -> None:
        """失败调用也写 llm_calls（success=False, error 非空）。"""
        from sqlalchemy import select

        from app.core.db import AsyncSessionLocal
        from app.models.llm_call import LLMCall

        router = LLMRouter(
            adapters={
                "zhipu": MockAdapter(
                    name="zhipu", failures_before_success=99
                ),
            },
            default_primary="zhipu",
            default_fallback=None,
            max_retries=0,
        )
        with pytest.raises(LLMError):
            await router.chat(messages=[], scope="interview")

        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(LLMCall).where(
                        LLMCall.scope == "interview", LLMCall.success == False  # noqa: E712
                    )
                )
            ).scalars().all()
            assert len(rows) >= 1
            assert rows[-1].error is not None
            assert "mock failure" in rows[-1].error

    async def test_team_id_propagation(self) -> None:
        """set_team_context 后 llm_calls.team_id 应被填充。"""
        import uuid

        from sqlalchemy import select

        from app.core.db import AsyncSessionLocal
        from app.models.llm_call import LLMCall
        from app.models.team import Team

        async with AsyncSessionLocal() as session:
            team = Team(name="t-router-test")
            session.add(team)
            await session.commit()
            await session.refresh(team)
            team_id = team.id

        router = LLMRouter(
            adapters={
                "zhipu": MockAdapter(
                    name="zhipu",
                    response_override="ok",
                    tokens_in=1,
                    tokens_out=1,
                )
            },
            default_primary="zhipu",
            default_fallback=None,
        )
        router.set_team_context(team_id)
        await router.chat(messages=[], scope="extractor")

        async with AsyncSessionLocal() as session:
            row = (
                await session.execute(
                    select(LLMCall).where(LLMCall.team_id == team_id).limit(1)
                )
            ).scalar_one_or_none()
            assert row is not None
            assert row.scope == "extractor"


# ============================================================================
# 失败后熔断器更新
# ============================================================================


class TestRouterCircuit:
    async def test_repeated_failures_open_circuit(self) -> None:
        """连续失败累计到阈值后，下次路由跳过该 adapter。"""
        router = LLMRouter(
            adapters={
                "zhipu": MockAdapter(name="zhipu", failures_before_success=99),
                "qwen": MockAdapter(
                    name="qwen", response_override="qwen-ok"
                ),
            },
            default_primary="zhipu",
            default_fallback="qwen",
            max_retries=0,
            failure_threshold=3,
        )
        # 触发 3 次主备切换（每次 zhipu 失败累计 1 次）
        for _ in range(3):
            resp = await router.chat(messages=[])
            assert resp.adapter == "qwen"

        # 第 4 次时 zhipu 应被 cooling 跳过，直接走 qwen
        assert router._breaker.is_cooling("zhipu") is True
