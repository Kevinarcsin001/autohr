"""AsyncJobService 单元测试（任务 12）。

覆盖：
- enqueue 幂等性（同 idempotency_key 不重复入队）
- mark_running / mark_success / mark_failed / mark_retry 状态机
- attempts 累加 + MAX_ATTEMPTS 后转 failed
- recover_stuck_running：running → queued 恢复
- error 字段截断
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.models.async_job import AsyncJob
from app.services.async_job_service import (
    ERROR_SUMMARY_MAX_CHARS,
    MAX_ATTEMPTS,
    AsyncJobService,
)


async def _purge_db() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text("TRUNCATE async_jobs RESTART IDENTITY CASCADE")
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def clean_db() -> None:
    await _purge_db()
    yield
    await _purge_db()


# ============================================================================
# enqueue 幂等性
# ============================================================================


async def test_enqueue_inserts_new_job_with_queued_status() -> None:
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        job = await service.enqueue(
            task_type="parse",
            target_id=uuid.uuid4(),
            payload={"file_key": "k"},
            idempotency_key="parse:abc",
        )
        await session.commit()

    assert job.status == "queued"
    assert job.attempts == 0
    assert job.task_type == "parse"
    assert job.queued_at is not None


async def test_enqueue_dedups_by_idempotency_key() -> None:
    """相同 idempotency_key 第二次 enqueue → 返回旧 job，不新增行。"""
    idem = "parse:resume-1"

    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        first = await service.enqueue(
            task_type="parse", target_id=uuid.uuid4(), idempotency_key=idem
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        second = await service.enqueue(
            task_type="parse", target_id=uuid.uuid4(), idempotency_key=idem
        )
        await session.commit()

    assert first.id == second.id

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AsyncJob))
        all_jobs = result.scalars().all()
    assert len(all_jobs) == 1


async def test_enqueue_without_idempotency_key_always_inserts() -> None:
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        a = await service.enqueue(task_type="parse")
        b = await service.enqueue(task_type="parse")
        await session.commit()

    assert a.id != b.id

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AsyncJob))
        all_jobs = result.scalars().all()
    assert len(all_jobs) == 2


# ============================================================================
# 状态机
# ============================================================================


async def test_mark_running_sets_started_at_and_status() -> None:
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        job = await service.enqueue(task_type="parse", idempotency_key="k1")
        await session.commit()

    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        updated = await service.mark_running(job.id)
        await session.commit()

    assert updated.status == "running"
    assert updated.started_at is not None


async def test_mark_success_sets_finished_at_and_clears_error() -> None:
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        job = await service.enqueue(task_type="parse", idempotency_key="k2")
        await service.mark_running(job.id)
        await service.mark_failed(job.id, "previous error")
        await session.commit()

    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        updated = await service.mark_success(job.id, result={"text_len": 42})
        await session.commit()

    assert updated.status == "success"
    assert updated.finished_at is not None
    assert updated.error is None
    assert updated.payload["result"] == {"text_len": 42}


async def test_mark_failed_persists_error_summary() -> None:
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        job = await service.enqueue(task_type="parse", idempotency_key="k3")
        await service.mark_failed(job.id, ValueError("boom"))
        await session.commit()

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AsyncJob))
        updated = result.scalar_one()

    assert updated.status == "failed"
    assert "ValueError" in (updated.error or "")
    assert "boom" in (updated.error or "")


async def test_mark_retry_increments_attempts_until_max() -> None:
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        job = await service.enqueue(task_type="parse", idempotency_key="k4")
        await session.commit()

    exc = RuntimeError("transient")

    # MAX_ATTEMPTS=3：第 1、2 次仍可重试（status=retry），第 3 次后转 failed
    for attempt in range(1, MAX_ATTEMPTS):
        async with AsyncSessionLocal() as session:
            service = AsyncJobService(session)
            updated = await service.mark_retry(job.id, exc)
            await session.commit()
        assert updated.attempts == attempt
        assert updated.status == "retry", f"attempt {attempt} should be retry"

    # 第 MAX_ATTEMPTS 次：转 failed
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        updated = await service.mark_retry(job.id, exc)
        await session.commit()

    assert updated.attempts == MAX_ATTEMPTS
    assert updated.status == "failed"
    assert updated.finished_at is not None


async def test_mark_retry_truncates_long_error_message() -> None:
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        job = await service.enqueue(task_type="parse", idempotency_key="k5")
        await session.commit()

    long_msg = "x" * (ERROR_SUMMARY_MAX_CHARS * 3)
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        updated = await service.mark_retry(job.id, ValueError(long_msg))
        await session.commit()

    assert len(updated.error or "") <= ERROR_SUMMARY_MAX_CHARS


# ============================================================================
# recover_stuck_running
# ============================================================================


async def test_recover_stuck_running_resets_to_queued() -> None:
    """worker 启动钩子：所有 running → queued。"""
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        # 三个任务：一个 queued，两个 running（模拟 worker 崩溃残留）
        queued = await service.enqueue(task_type="parse", idempotency_key="r1")
        running_a = await service.enqueue(task_type="parse", idempotency_key="r2")
        running_b = await service.enqueue(task_type="extract", idempotency_key="r3")
        await service.mark_running(running_a.id)
        await service.mark_running(running_b.id)
        await session.commit()

    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        recovered = await service.recover_stuck_running()
        await session.commit()

    assert recovered == 2

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AsyncJob))
        all_jobs = {j.idempotency_key: j for j in result.scalars().all()}

    assert all_jobs["r1"].status == "queued"  # 原本就 queued，没动
    assert all_jobs["r2"].status == "queued"  # 从 running 恢复
    assert all_jobs["r3"].status == "queued"  # 从 running 恢复
    # running 任务的 started_at 被清空
    assert all_jobs["r2"].started_at is None
    assert all_jobs["r3"].started_at is None


async def test_recover_stuck_running_returns_zero_when_none_running() -> None:
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        recovered = await service.recover_stuck_running()
        await session.commit()
    assert recovered == 0
