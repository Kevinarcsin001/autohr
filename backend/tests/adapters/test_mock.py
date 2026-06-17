"""MockAdapter 单元测试。"""
from __future__ import annotations

from decimal import Decimal

import pytest
from pydantic import BaseModel

from app.adapters.llm.base import LLMError, LLMResponse, Message
from app.adapters.llm.mock import MockAdapter


class _Schema(BaseModel):
    name: str
    score: int
    tags: list[str]


class TestMockAdapter:
    def test_chat_without_schema(self) -> None:
        adapter = MockAdapter()
        assert adapter.name == "mock"
        assert adapter.default_model == "mock"

    async def test_chat_plain_text(self) -> None:
        adapter = MockAdapter(response_override="hello world")
        resp = await adapter.chat(messages=[Message("user", "hi")])
        assert isinstance(resp, LLMResponse)
        assert resp.content == "hello world"
        assert resp.adapter == "mock"
        assert resp.model == "mock"

    async def test_chat_with_schema_parsing(self) -> None:
        adapter = MockAdapter()
        resp = await adapter.chat(
            messages=[Message("user", "hi")],
            response_schema=_Schema,
        )
        assert resp.parsed is not None
        assert isinstance(resp.parsed, _Schema)
        assert resp.parsed.name.startswith("mock-")
        assert isinstance(resp.parsed.tags, list)

    async def test_failures_before_success(self) -> None:
        """前 2 次抛错，第 3 次成功。"""
        adapter = MockAdapter(
            failures_before_success=2, failure_exception=LLMError("boom")
        )
        with pytest.raises(LLMError):
            await adapter.chat(messages=[])
        with pytest.raises(LLMError):
            await adapter.chat(messages=[])
        resp = await adapter.chat(messages=[])
        assert resp.content  # success
        assert adapter.call_count == 3

    async def test_token_stats(self) -> None:
        adapter = MockAdapter(tokens_in=500, tokens_out=300)
        resp = await adapter.chat(messages=[])
        assert resp.tokens_in == 500
        assert resp.tokens_out == 300

    async def test_cost_estimate_with_custom_pricing(self) -> None:
        adapter = MockAdapter(
            tokens_in=500, tokens_out=300, cost_per_1k=Decimal("0.1")
        )
        resp = await adapter.chat(messages=[])
        # (500 + 300) / 1000 * 0.1 = 0.08
        assert resp.cost_cny == Decimal("0.0800")

    async def test_name_override(self) -> None:
        """MockAdapter 在 router 中可通过 name= 覆盖。"""
        adapter = MockAdapter(name="zhipu", default_model="glm-4-plus")
        resp = await adapter.chat(messages=[])
        assert resp.adapter == "zhipu"
        assert resp.model == "glm-4-plus"

    async def test_latency_ms_recorded(self) -> None:
        adapter = MockAdapter()
        resp = await adapter.chat(messages=[])
        assert resp.latency_ms >= 0
