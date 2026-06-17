"""Celery application instance.

任务 1 阶段：仅创建最小 Celery 实例让 worker/beat 容器能启动（避免 ModuleNotFoundError）。
任务 11 接入：beat schedule（每 N 分钟轮询 enabled email_configs）。
任务 12 将扩展：autodiscover tasks、自定义 Task base（持久化 async_jobs 状态机）。
"""
from __future__ import annotations

from celery import Celery

from app.core.config import settings
from app.workers.scheduler import build_beat_schedule

app = Celery(
    "autohr",
    broker=settings.REDIS_URL,
    backend=settings.REDIS_URL,
    include=[  # 任务 11：先列出 fetch_emails 所在模块，beat 才能解析
        "app.workers.tasks",
    ],
)

app.conf.update(
    # 序列化
    task_serializer="json",
    result_serializer="json",
    accept_content=["json"],
    # 时区
    timezone="Asia/Shanghai",
    enable_utc=True,
    # 可靠性
    task_acks_late=True,
    task_reject_on_worker_lost=True,
    worker_max_tasks_per_child=1000,
    # 重试策略
    task_default_max_retries=3,
    # beat：任务 11 接线邮件轮询
    beat_schedule=build_beat_schedule(settings.EMAIL_POLL_INTERVAL_MIN),
)
