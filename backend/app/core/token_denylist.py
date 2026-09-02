"""Refresh token 吊销列表：Redis ``SETEX jti`` 存储，TTL = token 剩余有效期。

用途：
- refresh 轮换后旧 token 即时失效（被盗用重放检测的基础）
- logout 显式吊销当前 refresh

可用性策略：**fail-open** —— Redis 抖动时放行并告警日志。
拒绝服务式的全站锁死比短时间的吊销空窗更不可接受；Sentry/告警接入后
由 ``denylist_*_failed`` 日志事件驱动运维介入。
"""
from __future__ import annotations

from typing import Any

from app.core.config import settings
from app.core.logging import get_logger

logger = get_logger(__name__)

_PREFIX = "auth:deny:"


def _client() -> Any:
    """每次操作短连接（认证路径 QPS 低，池化收益小于生命周期管理成本）。"""
    import redis.asyncio as aioredis

    return aioredis.from_url(settings.REDIS_URL, socket_timeout=1.0)  # type: ignore[no-untyped-call]


async def revoke(jti: str, remaining_seconds: int) -> None:
    """将 jti 拉黑至其自然过期时刻；已过期或 Redis 异常时静默降级。"""
    if not jti or remaining_seconds <= 0:
        return
    try:
        client = _client()
        try:
            await client.setex(_PREFIX + jti, remaining_seconds, "1")
        finally:
            await client.aclose()
    except Exception as exc:  # noqa: BLE001 - 吊销失败不阻塞主流程
        logger.warning(
            "denylist_revoke_failed", error=str(exc)[:120], jti_prefix=jti[:8]
        )


async def is_revoked(jti: str) -> bool:
    """jti 是否已被吊销；Redis 异时时 fail-open 返回 False。"""
    if not jti:
        return False
    try:
        client = _client()
        try:
            exists = await client.exists(_PREFIX + jti)
        finally:
            await client.aclose()
        return bool(exists)
    except Exception as exc:  # noqa: BLE001 - fail-open，见模块注释
        logger.warning(
            "denylist_lookup_failed", error=str(exc)[:120], jti_prefix=jti[:8]
        )
        return False
