"""LLM 输出 JSON 抽取与 Pydantic 解析（adapter 共用）。

模型常把 JSON 包裹在 ```json ...``` 代码块中，或在前后加自然语言说明。
本模块提供宽松解析：找到首个 ``{`` 与匹配的末尾 ``}``，按 JSON 解析。
"""
from __future__ import annotations

import json
import re
from typing import TypeVar

from pydantic import BaseModel, ValidationError

from app.adapters.llm.base import LLMSchemaError

T = TypeVar("T", bound=BaseModel)

_CODE_FENCE_RE = re.compile(r"```(?:json)?\s*(.*?)```", re.DOTALL)


def extract_json(text: str) -> str:
    """从模型输出中抽取 JSON 文本。

    优先级：
    1. ```json ...``` 代码块
    2. 首个 ``{`` 到末尾 ``}``（平衡括号匹配）

    找不到时抛 LLMSchemaError。
    """
    if not text or not text.strip():
        raise LLMSchemaError("Empty LLM output")

    fence = _CODE_FENCE_RE.search(text)
    if fence:
        return fence.group(1).strip()

    # 平衡括号匹配：找首个 '{' 后按层级匹配 '}'
    start = text.find("{")
    if start == -1:
        raise LLMSchemaError(f"No JSON object found in output: {text[:200]!r}")

    depth = 0
    in_string = False
    escape = False
    for i in range(start, len(text)):
        c = text[i]
        if in_string:
            if escape:
                escape = False
            elif c == "\\":
                escape = True
            elif c == '"':
                in_string = False
        else:
            if c == '"':
                in_string = True
            elif c == "{":
                depth += 1
            elif c == "}":
                depth -= 1
                if depth == 0:
                    return text[start : i + 1]

    raise LLMSchemaError(f"Unbalanced JSON in output: {text[:200]!r}")


def parse_to_schema(text: str, schema: type[T]) -> T:
    """把模型输出解析为 Pydantic 模型实例。

    失败抛 LLMSchemaError（含原始 ValidationError 信息）。
    """
    json_str = extract_json(text)
    try:
        data = json.loads(json_str)
    except json.JSONDecodeError as e:
        raise LLMSchemaError(f"Invalid JSON: {e}; raw={json_str[:200]!r}") from e

    try:
        return schema.model_validate(data)
    except ValidationError as e:
        raise LLMSchemaError(
            f"Schema validation failed: {e}; raw={json_str[:200]!r}"
        ) from e


__all__ = ["extract_json", "parse_to_schema"]
