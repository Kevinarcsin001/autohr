"""进程内滑动窗口限流：认证端点防撞库 / 批量枚举。

设计取舍（诚实标注）：
- 单 backend 实例内存实现——当前部署形态（docker compose 单容器）成立；
  若未来横向扩容为多实例，须换 Redis 后端（SETNX + EXPIRE 或令牌桶）
- 无锁的话并发读改写会有少量误差（±几次放行），对认证暴力破解的
  数量级防护目标而言可接受；key 总量上限防止内存被刷爆
- 限流命中不抛异常而是返回 False，由调用方决定 HTTP 形态（统一 429）
"""
from __future__ import annotations

import time
from collections import defaultdict, deque

_MAX_KEYS = 10_000
_buckets: dict[str, deque[float]] = defaultdict(deque)


def allow(key: str, limit: int, window_seconds: float = 60.0) -> bool:
    """key 在最近 window_seconds 内的次数 < limit 时记账并放行。"""
    now = time.monotonic()
    bucket = _buckets[key]
    cutoff = now - window_seconds

    # 惰性驱逐过期记录
    while bucket and bucket[0] <= cutoff:
        bucket.popleft()

    if len(bucket) >= limit:
        return False

    bucket.append(now)

    # key 总量熔断：超过上限整体重置（极端扫描场景宁可误伤也不 OOM）
    if len(_buckets) > _MAX_KEYS:
        for stale_key in [k for k, v in _buckets.items() if not v or v[-1] <= cutoff]:
            _buckets.pop(stale_key, None)

    return True


def reset() -> None:
    """清空全部计数（测试用）。"""
    _buckets.clear()
