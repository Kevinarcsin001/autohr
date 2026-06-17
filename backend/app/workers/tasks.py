"""Celery 任务定义（任务 12）。

设计：
- ``@async_task`` 装饰器把普通 async handler 包装成 celery task，自动驱动
  AsyncJob 状态机（queued → running → success / retry / failed）；
- 每个 celery task 接 ``async_job_id: UUID`` 参数，从 DB 拿 payload；
- 实际业务逻辑（parser / extractor / screening / scoring / export）
  通过 ``handlers[task_type]`` 分发；本任务只填占位 + TODO，留给
  任务 13/14/15-17/18-20/22 接入真实 handler；
- ``fetch_emails`` 已在任务 11 实现，beat 直接调度（不走 async_jobs 表，
  因为是周期性全局扫描，没有特定 target_id）。

约束：
- handler 必须幂等（基于 ``idempotency_key`` + ``target_id``）
- handler 内部不能直接调外部 SDK，必须走 adapter 层
- 失败自动重试 MAX_ATTEMPTS=3 次（指数退避：5s/25s/125s）
"""
from __future__ import annotations

import asyncio
import functools
import uuid
from collections.abc import Awaitable, Callable
from typing import Any

from celery.signals import worker_process_init

from app.core.db import AsyncSessionLocal
from app.core.logging import get_logger
from app.services.async_job_service import AsyncJobService, MAX_ATTEMPTS
from app.services.ingestion.email_fetcher import fetch_all_active_configs
from app.workers.celery_app import app as celery_app

logger = get_logger(__name__)


# ============================================================================
# 类型
# ============================================================================


Handler = Callable[[uuid.UUID, dict[str, Any] | None], Awaitable[dict[str, Any] | None]]
"""Handler 协议：``async def handler(target_id, payload) -> result_dict | None``"""


# ============================================================================
# @async_task 装饰器：通用状态机包装
# ============================================================================


def async_task(
    *,
    name: str,
    task_type: str,
    autoretry_backoff: tuple[int, ...] = (5, 25, 125),
) -> Callable[[Handler], Callable[..., Any]]:
    """把 async handler 包装成 celery task，自动驱动 AsyncJob 状态机。

    Args:
        name: celery 任务名（``app.workers.tasks.<name>``）
        task_type: 对应 ``AsyncJob.task_type`` enum 值
        autoretry_backoff: 各次重试的间隔秒（第 N 个元素 = 第 N 次失败后等待多久）

    包装后调用约定：``task(async_job_id: str | UUID)`` → 任何返回值都丢弃，
    结果通过 ``AsyncJob.payload["result"]`` 持久化。
    """

    def decorator(handler: Handler) -> Callable[..., Any]:
        @celery_app.task(
            name=f"app.workers.tasks.{name}",
            bind=True,
            acks_late=True,
            max_retries=MAX_ATTEMPTS - 1,
            autoretry_for=(Exception,),
            retry_backoff=False,  # 我们自己控退避序列
            retry_jitter=False,
            retry_kwargs={"max_retries": MAX_ATTEMPTS - 1},
        )
        @functools.wraps(handler)
        def _wrapped(self, async_job_id: str | uuid.UUID) -> dict[str, Any] | None:  # noqa: ANN001
            job_id = uuid.UUID(str(async_job_id))
            try:
                return asyncio.run(_run_handler(_wrapped.__name__, task_type, job_id, handler, autoretry_backoff, self))
            except _RetrySignal as rs:
                # 我们的 handler 决定抛 _RetrySignal 而非 Exception 来避免 celery 直接转 failed
                raise self.retry(exc=rs.cause, countdown=rs.countdown)
            except Exception:
                # 真正的失败：已经写过 mark_failed，不要让 celery 再自动重试
                logger.exception(
                    "async_task_terminal_failure",
                    task_name=name,
                    job_id=str(job_id),
                )
                return None

        return _wrapped

    return decorator


class _RetrySignal(Exception):
    """handler 想要重试时通过本信号传回 celery。"""

    def __init__(self, cause: BaseException, countdown: int) -> None:
        super().__init__(f"retry requested: {cause!r}")
        self.cause = cause
        self.countdown = countdown


class PermanentFailure(Exception):
    """handler 抛此异常表示永久失败，``@async_task`` 不重试，直接 mark_failed。

    场景示例：
    - 简历文件不存在 / 损坏 → 重试也无效
    - 任务参数非法（payload 缺字段）
    """


async def _run_handler(
    task_name: str,
    task_type: str,
    job_id: uuid.UUID,
    handler: Handler,
    backoff: tuple[int, ...],
    bound_task: Any,
) -> dict[str, Any] | None:
    """状态机驱动 + handler 调用。

    流程：
    1. ``mark_running``
    2. 调 handler（catch 异常）
    3. 成功 → ``mark_success``，写 payload['result']
    4. 失败 → ``mark_retry``（attempts<MAX）→ 抛 _RetrySignal 让 celery retry
              或 ``mark_failed``（attempts>=MAX）
    """
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        job = await service.mark_running(job_id)
        payload = job.payload
        target_id = job.target_id
        await session.commit()

    try:
        result = await handler(target_id, payload)
    except PermanentFailure as exc:
        # 永久失败：直接 mark_failed，不重试
        async with AsyncSessionLocal() as session:
            service = AsyncJobService(session)
            await service.mark_failed(job_id, exc)
            await session.commit()
        logger.warning(
            "async_task_permanent_failure",
            task_name=task_name,
            job_id=str(job_id),
            error=str(exc),
        )
        return None
    except Exception as exc:  # noqa: BLE001
        async with AsyncSessionLocal() as session:
            service = AsyncJobService(session)
            updated = await service.mark_retry(job_id, exc)
            await session.commit()
        # 已达 MAX → mark_retry 内部已置 failed；不再抛 retry
        if updated.status == "failed":
            logger.warning(
                "async_task_max_attempts_reached",
                task_name=task_name,
                job_id=str(job_id),
                attempts=updated.attempts,
            )
            return None
        # 还有重试机会 → 告诉 celery 多久后再来
        idx = min(updated.attempts - 1, len(backoff) - 1)
        countdown = backoff[idx]
        raise _RetrySignal(exc, countdown) from exc

    # 成功
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        await service.mark_success(job_id, result=result)
        await session.commit()

    logger.info(
        "async_task_succeeded",
        task_name=task_name,
        task_type=task_type,
        job_id=str(job_id),
    )
    return result


# ============================================================================
# 6 个任务签名
# ============================================================================


# --- parse_resume（任务 13 接入真实 ParserService） ---


async def parse_resume_handler(
    target_id: uuid.UUID | None, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """解析简历附件（PDF/Word/Image）→ 提取文本 → 更新 candidate_resumes。

    Args (payload):
        file_key: str — MinIO object key
        mime: str — application/pdf | image/png | ...
        source: str — upload | email | platform

    实现（任务 13）：见 ``app/workers/parser_task.run_parse``。
    失败时不让状态机自动重试（损坏文件再试也无效）：
    - ``ResumeNotFound`` / ``StorageObjectMissing``：永久失败，抛 ``PermanentParseFailure``
    - ``ParserService`` 返回 status=failed：视为成功完成（已写库），不再重试
    - 真异常（DB / 网络）：自然抛出，状态机会按重试策略处理
    """
    from app.workers.parser_task import (
        ResumeNotFound,
        StorageObjectMissing,
        run_parse,
    )

    if target_id is None:
        raise ValueError("parse_resume requires target_id (candidate_resume.id)")

    async with AsyncSessionLocal() as session:
        try:
            summary = await run_parse(
                db=session,
                storage=None,
                target_id=target_id,
                payload=payload,
            )
        except (ResumeNotFound, StorageObjectMissing) as exc:
            # 永久失败：commit 现有 partial 改动后抛 PermanentParseFailure
            # 让上层 mark_failed（不进 retry）
            await session.commit()
            raise PermanentParseFailure(str(exc)) from exc
        await session.commit()

    # 解析任务完成（无论 status 是 success/low_text/failed，都视为任务执行成功）；
    # 解析失败已通过 parse_status='failed' + parse_error 落库
    return summary


class PermanentParseFailure(PermanentFailure):
    """Parser 任务永久失败（不可重试）；用于阻止 @async_task 重试。"""


parse_resume = async_task(name="parse_resume", task_type="parse")(parse_resume_handler)


# --- extract_structured（任务 14 接入 ExtractorService） ---


async def extract_structured_handler(
    target_id: uuid.UUID | None, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """LLM 抽取 CandidateStructure（name/phone/email/education/...）。

    Args:
        target_id: candidate_resume.id
        payload: 可选 {team_id: str} 用于 LLM 路由 team 隔离

    实现（任务 14）：见 ``app/workers/extractor_task.run_extract``。
    永久失败条件（不重试）：
    - resume 不存在 / parse_status != 'success' / parsed_text 为空
    """
    from app.workers.extractor_task import (
        ResumeNotFound,
        ResumeNotReady,
        ResumeTextMissing,
        run_extract,
    )

    if target_id is None:
        raise ValueError("extract_structured requires target_id (candidate_resume.id)")

    async with AsyncSessionLocal() as session:
        try:
            summary = await run_extract(
                db=session,
                target_id=target_id,
                payload=payload,
            )
        except (ResumeNotFound, ResumeNotReady, ResumeTextMissing) as exc:
            await session.commit()
            raise PermanentExtractFailure(str(exc)) from exc
        await session.commit()

    return summary


class PermanentExtractFailure(PermanentFailure):
    """Extractor 任务永久失败（不可重试）。"""


extract_structured = async_task(name="extract_structured", task_type="extract")(
    extract_structured_handler
)


# --- run_screening（任务 15-17 接入 ScreeningService） ---


async def run_screening_handler(
    target_id: uuid.UUID | None, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """对候选人跑筛选规则（硬性条件 + 软性加权）。

    Args:
        target_id: candidate.id
        payload: {"job_id": str}

    TODO(task-15/16/17): 接 ScreeningService。
    """
    logger.info(
        "run_screening_stub_called",
        target_id=str(target_id) if target_id else None,
    )
    return {"status": "stub", "implemented_in": "task-15-17"}


run_screening = async_task(name="run_screening", task_type="screen")(
    run_screening_handler
)


# --- score_candidate（任务 18-20 接入 ScorerService + LLMReasoner） ---


async def score_candidate_handler(
    target_id: uuid.UUID | None, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """对候选人评分（数值 + recommend/disqualify 原因）。

    Args:
        target_id: candidate.id
        payload: {"job_id": str}

    TODO(task-18/19/20): 接 ScorerService + ReasoningService。
    """
    logger.info(
        "score_candidate_stub_called",
        target_id=str(target_id) if target_id else None,
    )
    return {"status": "stub", "implemented_in": "task-18-20"}


score_candidate = async_task(name="score_candidate", task_type="score")(
    score_candidate_handler
)


# --- run_export（任务 22 接入 ExportService） ---


async def run_export_handler(
    target_id: uuid.UUID | None, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """批量导出候选人列表到 xlsx/csv。

    Args:
        target_id: 异步导出任务的发起人 user_id（可选）
        payload: {"job_id": str, "format": "xlsx"|"csv", "filter": {...}}

    TODO(task-22): 接 ExportService → 写文件 → 生成下载 URL。
    """
    logger.info(
        "run_export_stub_called",
        target_id=str(target_id) if target_id else None,
    )
    return {"status": "stub", "implemented_in": "task-22"}


run_export = async_task(name="run_export", task_type="export")(run_export_handler)


# ============================================================================
# fetch_emails（beat 触发；不走 async_jobs 表）
# ============================================================================


@celery_app.task(
    name="app.workers.tasks.fetch_emails",
    bind=True,
    max_retries=0,  # 退避由 EmailFetcherService 内部状态机管理
    acks_late=True,
)
def fetch_emails(self) -> dict[str, int]:  # noqa: ANN001
    """beat 入口：扫描所有 enabled email_config 抓取新邮件。

    不走 async_jobs 表（无特定 target_id，是周期性全局任务）；
    退避由 ``EmailFetcherService._record_failure`` 写到 email_configs。
    """
    logger.info(
        "fetch_emails_task_started",
        task_id=getattr(self, "request", None) and getattr(self.request, "id", None),
    )
    try:
        summary = asyncio.run(_run_fetch_all())
    except Exception:
        logger.exception("fetch_emails_task_failed")
        raise
    logger.info(
        "fetch_emails_task_done",
        configs_touched=len(summary),
        new_attachments=sum(summary.values()),
    )
    return summary


async def _run_fetch_all() -> dict[str, int]:
    async with AsyncSessionLocal() as session:
        return await fetch_all_active_configs(session)


# ============================================================================
# worker 启动钩子：恢复 stuck running 任务
# ============================================================================


@worker_process_init.connect
def _on_worker_init(**_: Any) -> None:
    """worker 子进程启动时：把残留 running 任务重置为 queued。

    通过 ``asyncio.run`` 在子进程内同步执行；失败仅记录日志（不阻塞 worker 启动）。
    """
    try:
        recovered = asyncio.run(_recover_stuck_jobs())
        if recovered > 0:
            logger.warning("worker_init_recovered_jobs", count=recovered)
    except Exception:  # noqa: BLE001
        logger.exception("worker_init_recover_failed")


async def _recover_stuck_jobs() -> int:
    async with AsyncSessionLocal() as session:
        service = AsyncJobService(session)
        n = await service.recover_stuck_running()
        await session.commit()
        return n


__all__ = [
    "async_task",
    "PermanentFailure",
    "PermanentParseFailure",
    "PermanentExtractFailure",
    "parse_resume",
    "parse_resume_handler",
    "extract_structured",
    "extract_structured_handler",
    "run_screening",
    "run_screening_handler",
    "score_candidate",
    "score_candidate_handler",
    "run_export",
    "run_export_handler",
    "fetch_emails",
]
