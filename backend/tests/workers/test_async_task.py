"""@async_task 装饰器集成测试（任务 12）。

策略：
- 直接调用包装后的 task 函数（绕过 celery broker）来验证状态机；
- 用 monkeypatch 替换 ``AsyncSessionLocal`` → 测试 session，确保 DB 一致性；
- 验证 stub 任务（extract_structured 等）的执行 + 状态推进。

覆盖：
- 成功路径：handler 正常返回 → status=success，payload['result'] 落地
- 重试路径：handler 抛 → status=retry，attempts+=1
- 终态路径：MAX_ATTEMPTS 后 → status=failed
- Stub 任务签名：parse_resume/extract_structured/run_screening/score_candidate/run_export
  都能从包装函数中调出（不真正执行 celery 任务）
"""
from __future__ import annotations

import asyncio
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.models.async_job import AsyncJob
from app.services.async_job_service import AsyncJobService, MAX_ATTEMPTS
from app.workers import tasks as worker_tasks


async def _purge_db() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(text("TRUNCATE async_jobs RESTART IDENTITY CASCADE"))
        await session.commit()


@pytest.fixture(autouse=True)
async def clean_db() -> None:
    await _purge_db()
    yield
    await _purge_db()


# ============================================================================
# Helpers：把 task 内的 AsyncSessionLocal 替换为可控工厂
# ============================================================================


class _RecordingHandler:
    """记录被调用次数，可配置第几次成功 / 失败。"""

    def __init__(self, fail_until: int = 0) -> None:
        self.calls = 0
        self.fail_until = fail_until  # 前 N 次抛，N+1 次成功

    async def __call__(
        self, target_id: uuid.UUID | None, payload: dict[str, Any] | None
    ) -> dict[str, Any]:
        self.calls += 1
        if self.calls <= self.fail_until:
            raise RuntimeError(f"simulated transient {self.calls}")
        return {"ran": self.calls}


async def _run_task_via_run_handler(
    handler: _RecordingHandler, task_type: str, job_id: uuid.UUID
) -> None:
    """绕过 celery task wrapper，直接调内部 _run_handler。

    使用任务 12 在 tasks.py 中暴露的内部函数；正常 task wrapper 会调它。
    """
    # 模拟 _wrapped 内部行为：try _run_handler；handler 失败 → mark_retry
    try:
        await worker_tasks._run_handler(
            task_name="test_task",
            task_type=task_type,
            job_id=job_id,
            handler=handler,
            backoff=(1, 1, 1),
            bound_task=None,
        )
    except worker_tasks._RetrySignal as rs:
        # 模拟 celery 收到 retry signal：本测试不再实际 retry，但记录
        pytest.fail(f"unexpected retry signal: {rs}")


# ============================================================================
# 成功路径
# ============================================================================


async def test_run_handler_success_persists_result_in_payload() -> None:
    handler = _RecordingHandler(fail_until=0)

    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        job = await service.enqueue(
            task_type="parse",
            target_id=uuid.uuid4(),
            payload={"file_key": "k"},
            idempotency_key="rt-1",
        )
        await session.commit()

    await _run_task_via_run_handler(handler, "parse", job.id)

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AsyncJob))
        updated = result.scalar_one()

    assert handler.calls == 1
    assert updated.status == "success"
    assert updated.finished_at is not None
    assert updated.error is None
    assert updated.payload["result"] == {"ran": 1}
    # 原 payload 字段保留
    assert updated.payload["file_key"] == "k"


# ============================================================================
# 重试路径：handler 失败一次后下次成功
# ============================================================================


async def test_run_handler_failure_marks_retry_and_increments_attempts() -> None:
    handler = _RecordingHandler(fail_until=1)  # 第 1 次抛

    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        job = await service.enqueue(task_type="parse", idempotency_key="rt-2")
        await session.commit()

    # 第一次执行：handler 抛 → _run_handler 内部 mark_retry → 抛 _RetrySignal
    with pytest.raises(worker_tasks._RetrySignal) as exc_info:
        await worker_tasks._run_handler(
            task_name="test_task",
            task_type="parse",
            job_id=job.id,
            handler=handler,
            backoff=(1, 1, 1),
            bound_task=None,
        )

    assert isinstance(exc_info.value.cause, RuntimeError)
    assert exc_info.value.countdown == 1

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AsyncJob))
        updated = result.scalar_one()
    assert updated.status == "retry"
    assert updated.attempts == 1


# ============================================================================
# 终态路径：达到 MAX_ATTEMPTS 后转 failed
# ============================================================================


async def test_permanent_failure_marks_failed_without_retry() -> None:
    """PermanentFailure（如 PermanentParseFailure）→ 直接 mark_failed，不进 retry。"""

    async def always_fail(target_id, payload):
        raise worker_tasks.PermanentFailure("resume not found")

    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        job = await service.enqueue(task_type="parse", idempotency_key="rt-perm")
        await session.commit()

    # 第一次执行就该转 failed，不抛 _RetrySignal
    await worker_tasks._run_handler(
        task_name="test",
        task_type="parse",
        job_id=job.id,
        handler=always_fail,
        backoff=(1, 1, 1),
        bound_task=None,
    )

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AsyncJob))
        updated = result.scalar_one()

    assert updated.status == "failed"
    assert updated.attempts == 0  # 没有进入 retry 路径，attempts 没增加
    assert "resume not found" in (updated.error or "")


async def test_run_handler_max_attempts_marks_failed() -> None:
    """连续失败 MAX_ATTEMPTS 次后，最后一次 _run_handler 应让 status=failed 且不抛 retry。"""
    handler = _RecordingHandler(fail_until=99)  # 永远失败

    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        job = await service.enqueue(task_type="parse", idempotency_key="rt-3")
        await session.commit()

    # 模拟前 MAX_ATTEMPTS-1 次重试
    for _ in range(MAX_ATTEMPTS - 1):
        with pytest.raises(worker_tasks._RetrySignal):
            await worker_tasks._run_handler(
                task_name="test_task",
                task_type="parse",
                job_id=job.id,
                handler=handler,
                backoff=(1, 1, 1),
                bound_task=None,
            )

    # 第 MAX_ATTEMPTS 次：mark_retry 内部应转 failed，不再抛 _RetrySignal
    await worker_tasks._run_handler(
        task_name="test_task",
        task_type="parse",
        job_id=job.id,
        handler=handler,
        backoff=(1, 1, 1),
        bound_task=None,
    )

    async with AsyncSessionLocal() as session:
        result = await session.execute(select(AsyncJob))
        updated = result.scalar_one()

    assert updated.attempts == MAX_ATTEMPTS
    assert updated.status == "failed"
    assert updated.finished_at is not None


# ============================================================================
# 6 个 stub 任务签名 sanity check
# ============================================================================


def test_six_task_signatures_are_registered_in_celery() -> None:
    """6 个任务签名都应在 celery_app.tasks 中可解析。"""
    from app.workers.celery_app import app as celery_app

    expected = {
        "app.workers.tasks.parse_resume",
        "app.workers.tasks.extract_structured",
        "app.workers.tasks.run_screening",
        "app.workers.tasks.score_candidate",
        "app.workers.tasks.run_export",
        "app.workers.tasks.fetch_emails",
    }
    registered = set(celery_app.tasks.keys())
    missing = expected - registered
    assert not missing, f"missing tasks: {missing}"


async def test_parse_resume_handler_dispatches_to_run_parse(monkeypatch) -> None:
    """任务 13 接入后，parse_resume_handler 应调 run_parse 并返回其 summary。"""
    called: dict[str, Any] = {}

    async def fake_run_parse(**kwargs):
        called.update(kwargs)
        return {
            "resume_id": str(kwargs["target_id"]),
            "status": "success",
            "text_len": 100,
            "ocr_backend": "fake",
        }

    # 用 monkeypatch 替换 run_parse 引用（parse_resume_handler 内部 import 的）
    import app.workers.parser_task as pt

    monkeypatch.setattr(pt, "run_parse", fake_run_parse)

    target_id = uuid.uuid4()
    payload = {"file_key": "k", "mime": "application/pdf", "source": "upload"}

    result = await worker_tasks.parse_resume_handler(target_id, payload)

    assert result == {
        "resume_id": str(target_id),
        "status": "success",
        "text_len": 100,
        "ocr_backend": "fake",
    }
    assert called["target_id"] == target_id
    assert called["payload"] == payload


async def test_extract_structured_handler_requires_target_id() -> None:
    """extract_structured_handler 已接入任务 14 真实逻辑（不再是 stub）。
    缺 target_id 应抛 ValueError（由 run_extract 校验）。"""
    with pytest.raises(ValueError, match="target_id"):
        await worker_tasks.extract_structured_handler(None, None)


async def test_run_screening_stub_returns_status_marker() -> None:
    result = await worker_tasks.run_screening_handler(
        uuid.uuid4(), {"job_id": str(uuid.uuid4())}
    )
    assert result == {"status": "stub", "implemented_in": "task-15-17"}


async def test_score_candidate_stub_returns_status_marker() -> None:
    result = await worker_tasks.score_candidate_handler(
        uuid.uuid4(), {"job_id": str(uuid.uuid4())}
    )
    assert result == {"status": "stub", "implemented_in": "task-18-20"}


async def test_run_export_stub_returns_status_marker() -> None:
    result = await worker_tasks.run_export_handler(
        uuid.uuid4(), {"format": "xlsx"}
    )
    assert result == {"status": "stub", "implemented_in": "task-22"}
