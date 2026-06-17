"""结构化日志（structlog）配置 + PII 脱敏。

脱敏规则：
- 手机号（11 位数字，1[3-9] 开头）：保留前 3 后 2 → ``138****78``
- 邮箱：保留前 3 后 2 + 域名 → ``abc***@example.com``
- 身份证号（15/18 位）：保留前 3 后 2
- 敏感 key（password / token / api_key / secret / private_key / fernet_key / cookie / session 等）：
  值替换为 ``[REDACTED]``，永不输出
"""
from __future__ import annotations

import logging
import re
import sys
from typing import Any

import structlog

from app.core.config import settings

# ============================================================================
# PII 正则
# ============================================================================

# 中国大陆手机号：1[3-9]XXXXXXXXX，11 位 = 前 3 + 中 6 + 后 2
_PHONE_RE = re.compile(r"(?<!\d)(1[3-9]\d)\d{6}(\d{2})(?!\d)")
# 邮箱：local@domain，local 部分 ≥ 4 字符时脱敏
_EMAIL_RE = re.compile(r"([a-zA-Z0-9._%+-]{3})[a-zA-Z0-9._%+-]*@([a-zA-Z0-9.-]+)")
# 身份证号：15 或 18 位
_ID_CARD_RE = re.compile(r"(?<!\d)(\d{3})\d{8,12}([X\d]{2})(?!\d)")

# ============================================================================
# 敏感 key 集合（小写匹配，前缀/包含即可）
# ============================================================================

_SENSITIVE_KEYS: frozenset[str] = frozenset(
    {
        "password",
        "passwd",
        "pwd",
        "secret",
        "token",
        "authorization",
        "auth",
        "api_key",
        "apikey",
        "access_key",
        "secret_key",
        "private_key",
        "public_key",
        "fernet_key",
        "session",
        "cookie",
        "csrf",
        "bearer",
        "credit_card",
        "cardnumber",
        "cvv",
    }
)


def _is_sensitive_key(key: str) -> bool:
    """key 是否敏感（小写包含匹配）。"""
    lowered = key.lower()
    return any(s in lowered for s in _SENSITIVE_KEYS)


# ============================================================================
# 字符串值脱敏
# ============================================================================


def _mask_phone(value: str) -> str:
    return _PHONE_RE.sub(lambda m: f"{m.group(1)}****{m.group(2)}", value)


def _mask_email(value: str) -> str:
    return _EMAIL_RE.sub(lambda m: f"{m.group(1)}***@{m.group(2)}", value)


def _mask_id_card(value: str) -> str:
    return _ID_CARD_RE.sub(lambda m: f"{m.group(1)}**********{m.group(2)}", value)


def redact_value(value: Any) -> Any:
    """对字符串值做 PII 脱敏（手机/邮箱/身份证）。

    非字符串原样返回。
    """
    if not isinstance(value, str):
        return value
    masked = _mask_phone(value)
    masked = _mask_email(masked)
    masked = _mask_id_card(masked)
    return masked


# ============================================================================
# structlog 处理器
# ============================================================================


def redact_processor(
    _logger: Any, _method_name: str, event_dict: dict[str, Any]
) -> dict[str, Any]:
    """structlog 处理器：脱敏 event_dict 中的 PII 与敏感 key。

    嵌套 dict 与 list 递归处理（最大深度 5 层）。
    """
    return _redact_dict(event_dict, depth=0)


def _redact_dict(d: dict[str, Any], depth: int) -> dict[str, Any]:
    if depth > 5:
        return d
    out: dict[str, Any] = {}
    for k, v in d.items():
        if _is_sensitive_key(k):
            out[k] = "[REDACTED]"
        elif isinstance(v, str):
            out[k] = redact_value(v)
        elif isinstance(v, dict):
            out[k] = _redact_dict(v, depth + 1)
        elif isinstance(v, list):
            out[k] = _redact_list(v, depth + 1)
        else:
            out[k] = v
    return out


def _redact_list(lst: list[Any], depth: int) -> list[Any]:
    if depth > 5:
        return lst
    return [
        _redact_dict(item, depth + 1)
        if isinstance(item, dict)
        else _redact_list(item, depth + 1)
        if isinstance(item, list)
        else redact_value(item)
        if isinstance(item, str)
        else item
        for item in lst
    ]


# ============================================================================
# 配置入口
# ============================================================================


def configure_logging() -> None:
    """配置 structlog + stdlib logging 协同。

    - structlog 处理所有通过 ``get_logger()`` 获取的 logger
    - stdlib logging（uvicorn / celery）经 structlog 渲染输出
    - 脱敏处理器自动应用
    """
    log_level = getattr(logging, settings.LOG_LEVEL.upper(), logging.INFO)

    timestamper = structlog.processors.TimeStamper(fmt="iso", utc=True)
    shared_processors: list[Any] = [
        structlog.contextvars.merge_contextvars,
        structlog.processors.add_log_level,
        structlog.processors.StackInfoRenderer(),
        timestamper,
        redact_processor,
    ]

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.processors.format_exc_info,
            structlog.dev.ConsoleRenderer(
                colors=settings.ENVIRONMENT == "development",
                exception_formatter=structlog.dev.plain_traceback,
            ),
        ],
        wrapper_class=structlog.make_filtering_bound_logger(log_level),
        context_class=dict,
        logger_factory=structlog.PrintLoggerFactory(),
        cache_logger_on_first_use=True,
    )

    # 让 stdlib logging 走 structlog 渲染
    formatter = structlog.stdlib.ProcessorFormatter(
        processor=structlog.dev.ConsoleRenderer(
            colors=settings.ENVIRONMENT == "development"
        ),
        foreign_pre_chain=shared_processors,
    )

    handler = logging.StreamHandler(sys.stdout)
    handler.setFormatter(formatter)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(log_level)

    # uvicorn / celery 用自己的 logger，重定向到 root
    for name in (
        "uvicorn",
        "uvicorn.access",
        "uvicorn.error",
        "celery",
        "celery.worker",
        "sqlalchemy.engine",
    ):
        third_party = logging.getLogger(name)
        third_party.handlers.clear()
        third_party.propagate = True
        third_party.setLevel(log_level)


def get_logger(name: str | None = None) -> Any:
    """获取 structlog bound logger。"""
    return structlog.get_logger(name)


def bind_context(**kwargs: Any) -> None:
    """绑定上下文变量（如 user_id / request_id），自动出现在所有后续日志。

    典型用法：中间件中绑定 request_id；登录成功后绑定 user_id。
    """
    structlog.contextvars.bind_contextvars(**kwargs)


def clear_context() -> None:
    """清除上下文变量（请求结束时调用）。"""
    structlog.contextvars.clear_contextvars()
