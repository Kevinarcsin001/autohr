"""JSON 抽取工具单元测试。"""
from __future__ import annotations

import json

import pytest
from pydantic import BaseModel

from app.adapters.llm._json import extract_json, parse_to_schema
from app.adapters.llm.base import LLMSchemaError


class _Demo(BaseModel):
    name: str
    score: int
    tags: list[str]


class TestExtractJson:
    def test_plain_json(self) -> None:
        text = '{"a": 1, "b": 2}'
        assert extract_json(text) == text

    def test_code_fence(self) -> None:
        text = '说明：\n```json\n{"a": 1}\n```\n'
        assert json.loads(extract_json(text)) == {"a": 1}

    def test_code_fence_no_lang(self) -> None:
        text = '```\n{"a": 1}\n```'
        assert json.loads(extract_json(text)) == {"a": 1}

    def test_json_with_leading_prose(self) -> None:
        text = '好的，这是结果：{"name": "张三", "score": 85}'
        assert json.loads(extract_json(text)) == {"name": "张三", "score": 85}

    def test_nested_braces_in_strings(self) -> None:
        """字符串中的 { } 不应破坏平衡括号匹配。"""
        text = '{"msg": "包含 } 字符", "n": 1}'
        assert json.loads(extract_json(text)) == {"msg": "包含 } 字符", "n": 1}

    def test_empty_raises(self) -> None:
        with pytest.raises(LLMSchemaError):
            extract_json("")

    def test_no_json_raises(self) -> None:
        with pytest.raises(LLMSchemaError):
            extract_json("没有 JSON")

    def test_unbalanced_raises(self) -> None:
        with pytest.raises(LLMSchemaError):
            extract_json('{"a": 1')


class TestParseToSchema:
    def test_happy_path(self) -> None:
        text = json.dumps({"name": "李四", "score": 92, "tags": ["a", "b"]})
        result = parse_to_schema(text, _Demo)
        assert result.name == "李四"
        assert result.score == 92
        assert result.tags == ["a", "b"]

    def test_with_code_fence(self) -> None:
        text = '```json\n{"name": "王五", "score": 70, "tags": []}\n```'
        result = parse_to_schema(text, _Demo)
        assert result.name == "王五"

    def test_schema_validation_failure(self) -> None:
        text = '{"name": "x"}'  # 缺 score / tags
        with pytest.raises(LLMSchemaError):
            parse_to_schema(text, _Demo)

    def test_invalid_json_raises(self) -> None:
        with pytest.raises(LLMSchemaError):
            parse_to_schema("{bad json}", _Demo)
