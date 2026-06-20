"""Mock LLM 适配器（测试与本地开发用）。

行为：
- 不调用任何真实模型
- ``response_schema`` 传入时，根据 schema 字段构造合法 JSON 字符串
- ``response_override`` 可在初始化时注入固定回复
- ``failures_before_success`` 用于模拟重试场景：前 N 次抛错，第 N+1 次成功

测试用例优先使用本适配器，避免真实 API 调用。
"""
from __future__ import annotations

from decimal import Decimal
from typing import Any, TypeVar

from pydantic import BaseModel

from app.adapters.llm._json import parse_to_schema
from app.adapters.llm.base import (
    LLMError,
    LLMResponse,
    Message,
    _Latency,
    estimate_cost_cny,
)

T = TypeVar("T", bound=BaseModel)


def _build_mock_dict(schema: type[BaseModel]) -> dict[str, object]:
    """Build a mock dict for a nested Pydantic model (returns dict, not JSON)."""
    import json as _json
    return _json.loads(_build_mock_json(schema))


def _build_mock_json(schema: type[BaseModel]) -> str:
    """根据 schema 字段类型构造合法 mock JSON。"""
    import json
    import types
    import uuid
    from typing import Union, get_args, get_origin, get_type_hints

    sample: dict[str, Any] = {}
    hints = get_type_hints(schema)
    for name, annotation in hints.items():
        if name.startswith("_"):
            continue
        origin = get_origin(annotation)
        args = get_args(annotation)

        # unwrap Optional: Union[X, None] 或 X | None → X
        if origin is Union or origin is types.UnionType:
            non_none = [a for a in args if a is not type(None)]
            if len(non_none) == 1:
                annotation = non_none[0]
                origin = get_origin(annotation)
                args = get_args(annotation)

        # Literal type (e.g. Literal['bachelor', 'master']) → first arg
        if args and origin is not None and origin is not list and origin is not set and origin is not dict:
            sample[name] = args[0]
            continue

        if annotation is str:
            sample[name] = f"mock-{name}"
        elif annotation is int:
            sample[name] = 80
        elif annotation is float:
            sample[name] = 0.85
        elif annotation is bool:
            sample[name] = True
        elif annotation is Decimal:
            sample[name] = "12.34"
        elif origin in (list, set):
            inner = args[0] if args else str
            if inner is str:
                sample[name] = ["mock-item-1", "mock-item-2", "mock-item-3", "mock-item-4", "mock-item-5"]
            elif inner is int:
                sample[name] = [1, 2, 3, 4, 5]
            elif isinstance(inner, type) and issubclass(inner, BaseModel):
                # Pydantic model list — generate 5 mock items
                sample[name] = [_build_mock_dict(inner) for _ in range(5)]
            else:
                sample[name] = []
        elif origin is dict:
            sample[name] = {}
        elif annotation is uuid.UUID:
            sample[name] = str(uuid.uuid4())
        else:
            sample[name] = f"mock-{name}"
    # Post-process: ensure interview questions have diverse dimensions
    if "questions" in sample and isinstance(sample["questions"], list) and len(sample["questions"]) > 0:
        questions = sample["questions"]
        if isinstance(questions[0], dict) and "dimension" in questions[0]:
            dims = ["skill", "project", "weakness", "culture", "skill"]
            for i, q in enumerate(questions):
                q["dimension"] = dims[i % len(dims)]

    return json.dumps(sample, ensure_ascii=False)


class MockAdapter:
    """Mock LLM 适配器。

    在 Router 中通过 ``adapters={"zhipu": MockAdapter(name="zhipu")}`` 注册时，
    可覆盖 ``name`` 让 LLMResponse.adapter 与 llm_calls.adapter 正确反映"被路由的别名"。
    """

    default_model: str = "mock"

    def __init__(
        self,
        response_override: str | None = None,
        failures_before_success: int = 0,
        failure_exception: Exception | None = None,
        tokens_in: int = 100,
        tokens_out: int = 200,
        name: str = "mock",
        default_model: str | None = None,
        cost_per_1k: Decimal | None = None,
    ) -> None:
        self.name = name
        if default_model:
            self.default_model = default_model
        self._override = response_override
        self._failures = failures_before_success
        self._exc = failure_exception or LLMError("mock failure")
        self._call_count = 0
        self._tokens_in = tokens_in
        self._tokens_out = tokens_out
        self._cost_per_1k = cost_per_1k

    @property
    def call_count(self) -> int:
        return self._call_count

    async def chat(
        self,
        messages: list[Message],
        response_schema: type[T] | None = None,
        temperature: float = 0.2,
        timeout: float = 30.0,
        model: str | None = None,
    ) -> LLMResponse:
        self._call_count += 1
        with _Latency() as latency:
            if self._call_count <= self._failures:
                raise self._exc

            if self._override is not None:
                content = self._override
            elif response_schema is not None:
                content = _build_mock_json(response_schema)
            else:
                content = "mock response without schema"

        parsed = None
        if response_schema is not None:
            parsed = parse_to_schema(content, response_schema)

        if self._cost_per_1k is not None:
            cost = (
                self._cost_per_1k
                * Decimal(self._tokens_in + self._tokens_out)
                / Decimal(1000)
            ).quantize(Decimal("0.0001"))
        else:
            cost = estimate_cost_cny(
                model or self.default_model,
                self._tokens_in,
                self._tokens_out,
            )

        return LLMResponse(
            content=content,
            adapter=self.name,
            model=model or self.default_model,
            tokens_in=self._tokens_in,
            tokens_out=self._tokens_out,
            latency_ms=latency.ms,
            cost_cny=cost,
            parsed=parsed,
        )


__all__ = ["MockAdapter"]
