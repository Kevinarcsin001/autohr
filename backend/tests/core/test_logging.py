"""structlog 配置 + PII 脱敏 单元测试。"""
from __future__ import annotations

import io
import logging
from contextlib import redirect_stdout

import structlog

from app.core.logging import (
    bind_context,
    clear_context,
    configure_logging,
    redact_processor,
    redact_value,
)

# ============================================================================
# redact_value（字符串脱敏）
# ============================================================================


class TestRedactValue:
    """redact_value 函数：手机/邮箱/身份证。"""

    def test_mask_phone(self) -> None:
        """中国大陆手机号脱敏：138****56。"""
        assert redact_value("我的手机是13812345678") == "我的手机是138****78"
        assert redact_value("18600001234") == "186****34"
        # 非手机号 11 位（不以 1[3-9] 开头）不脱敏
        assert redact_value("20000000000") == "20000000000"

    def test_mask_email(self) -> None:
        """邮箱脱敏：abc***@domain。"""
        assert redact_value("alice@example.com") == "ali***@example.com"
        assert redact_value("contact:john.doe@web.co") == "contact:joh***@web.co"

    def test_mask_id_card(self) -> None:
        """身份证号脱敏（18 位）。"""
        masked = redact_value("身份证11010119900101001X")
        # 应该被脱敏为 110**********1X
        assert "11010119900101001X" not in masked
        assert "1X" in masked

    def test_non_string_passthrough(self) -> None:
        """非字符串原样返回。"""
        assert redact_value(42) == 42
        assert redact_value(None) is None
        assert redact_value(["a", "b"]) == ["a", "b"]

    def test_no_pii_unchanged(self) -> None:
        """无 PII 的字符串原样返回。"""
        assert redact_value("hello world") == "hello world"
        assert redact_value("user_id=abc123") == "user_id=abc123"


# ============================================================================
# redact_processor（structlog 处理器：敏感 key + dict 递归）
# ============================================================================


class TestRedactProcessor:
    """structlog 处理器：敏感 key 全替换为 [REDACTED]，普通值脱敏。"""

    def test_sensitive_keys_redacted(self) -> None:
        event = {
            "password": "secret123",
            "token": "Bearer abc.def.ghi",
            "api_key": "sk-xxxxxxx",
            "Authorization": "Bearer xxx",
            "fernet_key": "AAAA-BBBB-CCCC",
        }
        out = redact_processor(None, "info", event)
        for v in out.values():
            assert v == "[REDACTED]"

    def test_normal_keys_redact_pii(self) -> None:
        event = {
            "phone": "13812345678",
            "email": "alice@example.com",
            "user_id": "uuid-123",
        }
        out = redact_processor(None, "info", event)
        assert out["phone"] == "138****78"
        assert out["email"] == "ali***@example.com"
        assert out["user_id"] == "uuid-123"

    def test_nested_dict_recursion(self) -> None:
        event = {
            "user": {
                "phone": "13812345678",
                "password": "secret",
                "name": "Alice",
            },
            "request_id": "req-abc",
        }
        out = redact_processor(None, "info", event)
        assert out["user"]["phone"] == "138****78"
        assert out["user"]["password"] == "[REDACTED]"
        assert out["user"]["name"] == "Alice"

    def test_list_of_dicts_recursion(self) -> None:
        event = {
            "candidates": [
                {"phone": "13812345678", "name": "A"},
                {"phone": "18600001234", "password": "x"},
            ]
        }
        out = redact_processor(None, "info", event)
        assert out["candidates"][0]["phone"] == "138****78"
        assert out["candidates"][1]["phone"] == "186****34"
        assert out["candidates"][1]["password"] == "[REDACTED]"

    def test_sensitive_substring_match(self) -> None:
        """子串匹配（user_token / refresh_token 等都应脱敏）。"""
        event = {
            "user_token": "abc",
            "refresh_token": "def",
            "session_id": "ghi",
            "x_password_x": "jkl",
        }
        out = redact_processor(None, "info", event)
        for v in out.values():
            assert v == "[REDACTED]"


# ============================================================================
# configure_logging + get_logger 端到端
# ============================================================================


class TestConfigureLogging:
    """configure_logging + get_logger 端到端输出含脱敏。"""

    def test_log_output_contains_redacted_phone(self) -> None:
        configure_logging()
        logger = structlog.get_logger("test")

        buf = io.StringIO()
        with redirect_stdout(buf):
            logger.info("user_login", phone="13812345678", email="alice@example.com")

        output = buf.getvalue()
        # 手机/邮箱被脱敏
        assert "138****78" in output
        assert "ali***@example.com" in output
        # 原文不应出现
        assert "13812345678" not in output
        assert "alice@example.com" not in output

    def test_log_output_redacts_password(self) -> None:
        configure_logging()
        logger = structlog.get_logger("test")

        buf = io.StringIO()
        with redirect_stdout(buf):
            logger.warning("auth_failed", password="supersecret", username="alice")

        output = buf.getvalue()
        assert "[REDACTED]" in output
        assert "supersecret" not in output
        # username 非敏感，应保留
        assert "alice" in output


# ============================================================================
# contextvars 绑定 / 清理
# ============================================================================


class TestContextVars:
    """bind_context / clear_context 协同。"""

    def test_bind_context_appears_in_logs(self) -> None:
        configure_logging()
        logger = structlog.get_logger("test")

        bind_context(user_id="u-123", team_id="t-1")

        buf = io.StringIO()
        with redirect_stdout(buf):
            logger.info("hello")

        output = buf.getvalue()
        assert "u-123" in output
        assert "t-1" in output

        clear_context()

    def test_clear_context_removes_binding(self) -> None:
        bind_context(user_id="u-123")
        clear_context()

        configure_logging()
        logger = structlog.get_logger("test")

        buf = io.StringIO()
        with redirect_stdout(buf):
            logger.info("after_clear")

        output = buf.getvalue()
        assert "u-123" not in output
