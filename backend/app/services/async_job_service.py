"""AsyncJobService：异步任务状态机 + 幂等入队 + 重启恢复（任务 12）。

职责：
- ``enqueue``：基于 ``idempotency_key`` 去重，存在则返回旧任务，否则插入新行
- ``mark_running / mark_success / mark_failed / mark_retry``：状态机推进
- ``recover_stuck_running``：worker 启动时把所有 ``running`` 改回 ``queued``
  （worker 崩溃后下次扫描会重新派发）

状态流转（与 AsyncJobStatus enum 一致）::

    queued ──claim──> running ──ok──> success
                          │
                          ├──exception──> retry (attempts<MAX) ──> queued
                          │
                          └──exception──> failed (attempts>=MAX)

约束：
- ``attempts`` 在每次 exception 时自增；< MAX 重试，>=MAX 终态 failed
- ``idempotency_key`` UNIQUE → DB 层兜底重复入队
- ``error`` 字段截断 4KB（避免长 traceback 撑爆表）
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone
from typing import Any, Literal

from sqlalchemy import select, update
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.middleware.error_handler import ConflictError
from app.models.async_job import AsyncJob

logger = get_logger(__name__)


# ============================================================================
# 常量
# ============================================================================


TaskType = Literal["parse", "extract", "screen", "score", "email_fetch", "export"]
JobStatus = Literal["queued", "running", "success", "failed", "retry"]

MAX_ATTEMPTS: int = 3
"""最大尝试次数（含首次）；超过则终止为 failed。"""

ERROR_SUMMARY_MAX_CHARS: int = 4000


# ============================================================================
# 工具
# ============================================================================


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _summarize_error(exc: BaseException) -> str:
    msg = type(exc).__name__
    detail = str(exc).strip()
    if detail:
        msg = f"{msg}: {detail[:500]}"
    return msg[:ERROR_SUMMARY_MAX_CHARS]


# ============================================================================
# AsyncJobService
# ============================================================================


class AsyncJobService:
    """异步任务状态机服务。

    所有写操作只 ``flush``（不开事务），调用方决定 commit。
    """

    def __init__(self, db: AsyncSession) -> None:
        self.db = db

    # ----- enqueue（幂等） -----

    async def enqueue(
        self,
        *,
        task_type: TaskType,
        target_id: uuid.UUID | None = None,
        payload: dict[str, Any] | None = None,
        idempotency_key: str | None = None,
    ) -> AsyncJob:
        """幂等入队。

        - 若 ``idempotency_key`` 已存在 → 返回旧任务（不重复入队）
        - 否则 INSERT 新行 status='queued' attempts=0
        - 并发场景下 UNIQUE 约束兜底：第二个事务会触发 ConflictError
        """
        if idempotency_key is not None:
            existing = await self.db.scalar(
                select(AsyncJob).where(
                    AsyncJob.idempotency_key == idempotency_key
                )
            )
            if existing is not None:
                logger.info(
                    "async_job_enqueue_dedup",
                    job_id=str(existing.id),
                    task_type=existing.task_type,
                    idempotency_key=idempotency_key,
                    existing_status=existing.status,
                )
                return existing

        job = AsyncJob(
            task_type=task_type,
            target_id=target_id,
            status="queued",
            attempts=0,
            idempotency_key=idempotency_key,
            payload=payload,
        )
        self.db.add(job)
        try:
            await self.db.flush()
        except Exception as exc:  # noqa: BLE001
            # UNIQUE(idempotency_key) 冲突时尝试重新读
            if "unique" in str(exc).lower() or "duplicate" in str(exc).lower():
                if idempotency_key is not None:
                    existing = await self.db.scalar(
                        select(AsyncJob).where(
                            AsyncJob.idempotency_key == idempotency_key
                        )
                    )
                    if existing is not None:
                        return existing
            raise ConflictError(
                "async_job enqueue failed",
                task_type=task_type,
                idempotency_key=idempotency_key,
            ) from exc

        logger.info(
            "async_job_enqueued",
            job_id=str(job.id),
            task_type=task_type,
            target_id=str(target_id) if target_id else None,
            idempotency_key=idempotency_key,
        )
        return job

    # ----- 状态机 -----

    async def mark_running(self, job_id: uuid.UUID) -> AsyncJob:
        job = await self._get(job_id)
        job.status = "running"
        job.started_at = _now()
        await self.db.flush()
        return job

    async def mark_success(
        self, job_id: uuid.UUID, *, result: dict[str, Any] | None = None
    ) -> AsyncJob:
        job = await self._get(job_id)
        job.status = "success"
        job.finished_at = _now()
        job.error = None
        # result 可选合并到 payload（避免新增字段；后续任务再决定是否单独建表）
        if result is not None:
            current = dict(job.payload or {})
            current.update({"result": result})
            job.payload = current
        await self.db.flush()
        logger.info(
            "async_job_succeeded",
            job_id=str(job.id),
            task_type=job.task_type,
            attempts=job.attempts,
        )
        return job

    async def mark_failed(
        self, job_id: uuid.UUID, error: BaseException | str
    ) -> AsyncJob:
        """永久失败（不再重试）。"""
        job = await self._get(job_id)
        msg = _summarize_error(error) if isinstance(error, BaseException) else str(error)[:ERROR_SUMMARY_MAX_CHARS]
        job.status = "failed"
        job.finished_at = _now()
        job.error = msg
        await self.db.flush()
        logger.warning(
            "async_job_failed",
            job_id=str(job.id),
            task_type=job.task_type,
            attempts=job.attempts,
            error=msg,
        )
        return job

    async def mark_retry(self, job_id: uuid.UUID, error: BaseException) -> AsyncJob:
        """进入 retry 中间态，``attempts += 1``。

        实际重试由 Celery 自己的 retry 机制驱动；本方法只负责持久化状态。
        若 ``attempts >= MAX_ATTEMPTS`` 则直接转 failed。
        """
        job = await self._get(job_id)
        job.attempts += 1
        job.error = _summarize_error(error)
        if job.attempts >= MAX_ATTEMPTS:
            job.status = "failed"
            job.finished_at = _now()
            logger.warning(
                "async_job_max_attempts_exceeded",
                job_id=str(job.id),
                attempts=job.attempts,
            )
        else:
            job.status = "retry"
            logger.info(
                "async_job_retrying",
                job_id=str(job.id),
                attempts=job.attempts,
            )
        await self.db.flush()
        return job

    # ----- 启动恢复 -----

    async def recover_stuck_running(self) -> int:
        """worker 启动钩子：把所有 ``running`` 改回 ``queued``。

        Returns:
            被恢复的任务数量

        场景：worker 进程崩溃 → 残留 running 状态；新 worker 上线后
        通过本方法把它们重新放回队列由 Celery 派发。
        """
        result = await self.db.execute(
            update(AsyncJob)
            .where(AsyncJob.status == "running")
            .values(status="queued", started_at=None, error=None)
        )
        affected = result.rowcount or 0
        await self.db.flush()
        if affected > 0:
            logger.warning(
                "async_jobs_recovered_from_running", count=affected
            )
        return affected

    # ----- 内部 -----

    async def _get(self, job_id: uuid.UUID) -> AsyncJob:
        job = await self.db.get(AsyncJob, job_id)
        if job is None:
            raise ValueError(f"AsyncJob {job_id} not found")
        return job


__all__ = [
    "AsyncJobService",
    "MAX_ATTEMPTS",
    "TaskType",
    "JobStatus",
]
