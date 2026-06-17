"""Celery beat 调度配置（任务 11）。

任务 12 将在 celery_app.py 中接入 ``app.conf.beat_schedule``；
本模块集中维护调度 spec 便于阅读 + 单元测试。

调度项：
- ``fetch_emails_every_15min``：每 15 分钟扫描 enabled email_configs
  → 调用 ``fetch_all_active_configs``（实际 celery task 由任务 12 接线）
"""
from __future__ import annotations

from typing import Any

# 默认轮询间隔（spec 需求 5.1）
DEFAULT_POLL_INTERVAL_MIN = 15


def build_beat_schedule(poll_interval_min: int = DEFAULT_POLL_INTERVAL_MIN) -> dict[str, Any]:
    """返回 beat schedule 字典。

    TODO(task-12): ``fetch_emails`` 任务签名由 tasks.py 提供；
    现阶段先指向字符串路径，celery_app.py 在任务 12 启用 autodiscover 时识别。
    """
    return {
        "fetch_emails_every_interval": {
            "task": "app.workers.tasks.fetch_emails",
            "schedule": float(poll_interval_min * 60),
            # 显式标记这是邮件抓取任务（避免和未来其他周期任务混淆）
            "options": {"queue": "celery"},
        },
    }


__all__ = ["DEFAULT_POLL_INTERVAL_MIN", "build_beat_schedule"]
