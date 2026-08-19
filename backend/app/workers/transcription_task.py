"""面试音频转写任务（M2a）：MinIO 取音频 → ASR 容器转写 → 回填 turn → 自动评分。

复用 ``@async_task`` 状态机（AsyncJob: queued→running→success/failed，幂等 by job id）。

链路：
    POST /adaptive/audio （存 MinIO + turn.transcription_status=pending + 入队）
      → transcribe_turn_handler:
          1. MinIO 取音频字节
          2. ASRClient.transcribe(initial_prompt=职位+技能信号)  ← 领域词注入
          3. turn.answer_text = 转写文本; transcription_status=done
          4. 复用 AdaptiveInterviewService._rate_and_decide 自动评分+决策
          5. 失败：transcription_status=failed（前端可手动重试上传）
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.core.logging import get_logger
from app.models.async_job import AsyncJob
from app.models.interview import InterviewSession, InterviewTurn
from app.models.job import Job
from app.workers.tasks import async_task

logger = get_logger(__name__)


async def enqueue_transcription(*, turn_id: uuid.UUID) -> uuid.UUID | None:
    """创建 AsyncJob 并投递 Celery 任务（幂等：同 idempotency_key 复用）。"""
    import app.workers.tasks as tasks_mod

    idem = f"transcribe:{turn_id}"
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(
            select(AsyncJob).where(AsyncJob.idempotency_key == idem)
        )
        if existing is not None and existing.status in ("queued", "running", "success"):
            return existing.id
        job = AsyncJob(
            task_type="transcribe",
            idempotency_key=idem,
            payload={"turn_id": str(turn_id)},
        )
        session.add(job)
        await session.commit()
        job_id = job.id
    tasks_mod.transcribe_turn_handler.delay(str(job_id))
    return job_id


@async_task(name="transcribe_turn", task_type="transcribe")
async def transcribe_turn_handler(
    job_id: uuid.UUID, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    turn_id = uuid.UUID(str((payload or {}).get("turn_id")))

    async with AsyncSessionLocal() as session:
        turn = await session.get(InterviewTurn, turn_id)
        if turn is None:
            from app.workers.tasks import PermanentFailure

            raise PermanentFailure(f"turn {turn_id} not found")
        if turn.answer_text is not None:
            return {"status": "skipped", "reason": "answer already present"}

        turn.transcription_status = "processing"
        await session.commit()

        try:
            # 1) 取音频
            from app.adapters.asr_client import ASRClient, ASRError
            from app.adapters.storage import get_storage

            storage = get_storage()
            audio_bytes = await storage.get(turn.audio_storage_key or "")

            # 2) 领域词注入：职位标题 + adaptive_plan 信号（提升专名转写准确率）
            iv_session = await session.get(InterviewSession, turn.session_id)
            prompt_parts: list[str] = []
            if iv_session is not None:
                job = await session.get(Job, iv_session.job_id)
                if job is not None:
                    prompt_parts.append(job.title)
                plan = (iv_session.adaptive_plan or {}).get("signals") or []
                prompt_parts += [s.get("signal", "") for s in plan[:8]]

            client = ASRClient()
            result = await client.transcribe(
                audio_bytes=audio_bytes,
                filename=turn.audio_storage_key or "audio.webm",
                initial_prompt=" ".join(p for p in prompt_parts if p)[:400] or None,
            )
            text = (result.get("text") or "").strip()
            if not text:
                turn.transcription_status = "failed"
                await session.commit()
                return {"status": "empty_transcript"}

            # 3) 回填 + 自动评分
            from datetime import datetime, timezone

            from app.services.adaptive_interview import (
                AdaptiveInterviewError,
                AdaptiveInterviewService,
            )

            turn.answer_text = text[:20000]
            turn.answered_at = datetime.now(timezone.utc)
            turn.transcription_status = "done"
            turn.rating_evidence = {
                **(turn.rating_evidence or {}),
                "asr_language": result.get("language"),
                "asr_duration": result.get("duration"),
                "asr_model": result.get("model"),
                "asr_segments": (result.get("segments") or [])[:100],
            }

            if iv_session is not None and iv_session.status == "scheduled":
                iv_session.status = "in_progress"

            rating_error: str | None = None
            try:
                svc = AdaptiveInterviewService(session)
                await svc._rate_and_decide(session=iv_session, turn=turn)  # noqa: SLF001
            except AdaptiveInterviewError as exc:
                rating_error = str(exc)[:300]  # 转写成功但评分失败 → /next 重试

            await session.commit()
            return {
                "status": "done",
                "chars": len(text),
                "rating": turn.rating,
                "rating_error": rating_error,
            }
        except ASRError as exc:
            turn.transcription_status = "failed"
            turn.rating_evidence = {
                **(turn.rating_evidence or {}),
                "transcribe_error": str(exc)[:300],
            }
            await session.commit()
            logger.warning("transcription_failed", turn_id=str(turn_id), error=str(exc)[:200])
            raise
        except Exception:
            turn.transcription_status = "failed"
            await session.commit()
            raise


__all__ = ["enqueue_transcription", "transcribe_turn_handler"]
# ============================================================================
# M2b 会后回捞：整场录制 → 按 turn 时间区间逐段转写 + 自动评分
# ============================================================================


async def enqueue_recording_replay(*, session_id: uuid.UUID) -> uuid.UUID | None:
    """会后回捞任务入队（幂等）。"""
    from app.workers.transcription_task import replay_recording_handler

    idem = f"replay:{session_id}"
    async with AsyncSessionLocal() as session:
        existing = await session.scalar(
            select(AsyncJob).where(AsyncJob.idempotency_key == idem)
        )
        if existing is not None and existing.status in ("queued", "running", "success"):
            return existing.id
        job = AsyncJob(
            task_type="transcribe",
            idempotency_key=idem,
            payload={"session_id": str(session_id), "mode": "replay"},
        )
        session.add(job)
        await session.commit()
        job_id = job.id
    replay_recording_handler.delay(str(job_id))
    return job_id


@async_task(name="replay_recording", task_type="transcribe")
async def replay_recording_handler(
    job_id: uuid.UUID, payload: dict[str, Any] | None
) -> dict[str, Any] | None:
    """整场录制回捞：每题按 [audio_start_ms, 下一题起点] 区间转写 → 评分 → 回填。

    前置：session.recording_storage_key 已上传；各 turn.audio_start_ms 已标注。
    幂等：已有 answer_text 的回合跳过（可重跑补缺）。
    """
    session_id = uuid.UUID(str((payload or {}).get("session_id")))

    async with AsyncSessionLocal() as session:
        iv = await session.get(InterviewSession, session_id)
        if iv is None:
            from app.workers.tasks import PermanentFailure

            raise PermanentFailure(f"session {session_id} not found")
        if not iv.recording_storage_key:
            from app.workers.tasks import PermanentFailure

            raise PermanentFailure("session has no recording uploaded")

        turns = (
            await session.execute(
                select(InterviewTurn)
                .where(InterviewTurn.session_id == session_id)
                .order_by(InterviewTurn.seq.asc())
            )
        ).scalars().all()
        # 需要回填的回合：无 answer 且已标注起点（区间终点=下一题起点或录制尾）
        targets = [
            t for t in turns if t.answer_text is None and t.audio_start_ms is not None
        ]
        if not targets:
            iv.recording_status = "done"
            await session.commit()
            return {"status": "no_targets"}

        iv.recording_status = "processing"
        await session.commit()

        try:
            from app.adapters.asr_client import ASRClient, ASRError
            from app.adapters.storage import get_storage

            storage = get_storage()
            audio_bytes = await storage.get(iv.recording_storage_key)

            # 区间构造：终点 = 下一题起点；末题用下一题起点或 +10 分钟兜底
            starts = {t.id: t.audio_start_ms for t in turns if t.audio_start_ms is not None}
            ordered_starts = sorted(starts.values())
            ranges: list[tuple[float, float]] = []
            for t in targets:
                s = t.audio_start_ms / 1000.0
                later = [ms for ms in ordered_starts if ms > t.audio_start_ms]
                e = (later[0] / 1000.0) if later else s + 600.0
                ranges.append((s, e))

            # 领域词注入：职位 + 信号
            from app.models.job import Job

            job = await session.get(Job, iv.job_id)
            prompt_parts = [job.title] if job is not None else []
            prompt_parts += [
                s.get("signal", "") for s in (iv.adaptive_plan or {}).get("signals", [])[:8]
            ]

            client = ASRClient()
            result = await client.transcribe_segments(
                audio_bytes=audio_bytes,
                ranges=ranges,
                filename=iv.recording_storage_key.split("/")[-1],
                initial_prompt=" ".join(p for p in prompt_parts if p)[:400] or None,
            )

            from datetime import datetime, timezone

            from app.services.adaptive_interview import (
                AdaptiveInterviewError,
                AdaptiveInterviewService,
            )

            svc = AdaptiveInterviewService(session)
            filled, rating_errors = 0, 0
            for t, r in zip(targets, result.get("ranges", []), strict=False):
                if not r.get("ok"):
                    continue
                text = (r.get("text") or "").strip()
                if not text:
                    continue
                t.answer_text = text[:20000]
                t.answered_at = datetime.now(timezone.utc)
                t.transcription_status = "done"
                t.rating_evidence = {
                    **(t.rating_evidence or {}),
                    "asr_source": "recording_replay",
                    "asr_range": r.get("segments", [])[:20],
                }
                filled += 1
                try:
                    await svc._rate_and_decide(session=iv, turn=t)  # noqa: SLF001
                except AdaptiveInterviewError:
                    rating_errors += 1  # /next 可补评

            if iv.status == "scheduled":
                iv.status = "in_progress"
            iv.recording_status = "done"
            await session.commit()
            return {
                "status": "done",
                "targets": len(targets),
                "filled": filled,
                "rating_errors": rating_errors,
            }
        except ASRError as exc:
            iv.recording_status = "failed"
            await session.commit()
            logger.warning(
                "recording_replay_failed", session_id=str(session_id), error=str(exc)[:200]
            )
            raise
        except Exception:
            iv.recording_status = "failed"
            await session.commit()
            raise


__all__ = [
    "enqueue_transcription",
    "transcribe_turn_handler",
    "enqueue_recording_replay",
    "replay_recording_handler",
]
