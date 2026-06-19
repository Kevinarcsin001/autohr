"""ScreeningOrchestrator（任务 20）：编排 Filter → Scorer → Interview。

职责：
1. **流水线**：对每个候选人按顺序跑 Filter → Scorer（含 Reasoning）→ Interview。
2. **错误隔离**：任一候选人失败不阻塞其他；失败原因聚合到 ``failed_reasons``。
3. **进度推送**：每完成一个候选人 → 通过 ``ProgressStore`` 推 ``progress`` 事件；
   SSE 端点订阅本 store，断线重连基于 ``Last-Event-ID``。

设计约束（Restrictions）：
- orchestrator 不直接调 LLM adapter，全部走各 service（Filter / Scorer / Reasoning / Interview）。
- 任一阶段失败不阻塞其他候选人。
- SSE 必须支持断线重连（基于 Last-Event-ID）。

进度模型：
- 每个 run 一个 ``run_id``（UUID）。
- ``ProgressStore`` 是 process-local in-memory 字典；事件自增 ``event_id``。
- ``Last-Event-ID`` HTTP Header → server 从 ``event_id+1`` 开始推。
- 三个事件类型：
  - ``started``：run 开始
  - ``progress``：单候选人完成（含阶段、状态、reason）
  - ``done``：run 结束（含 summary）

注：``ProgressStore`` 是进程级；多 worker 部署时需要 Redis Pub/Sub 才能跨进程；
当前单进程足够，YAGNI。
"""
from __future__ import annotations

import asyncio
import uuid
from dataclasses import dataclass, field
from typing import Any, Literal

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.db import AsyncSessionLocal
from app.core.logging import get_logger
from app.models.candidate import Candidate
from app.models.screening import ScreeningResult
from app.services.filter import FilterService
from app.services.interview import InterviewError, InterviewService
from app.services.scorer import ScorerError, ScorerService, ScoringInput, build_scoring_snippet
from app.workers.scorer_task import (
    CandidateNotFound,
    JobNotFound,
    StructureMissing,
    run_score,
)

logger = get_logger(__name__)


# ============================================================================
# 常量
# ============================================================================


PipelineStage = Literal["filter", "score", "interview"]
"""单候选人当前所处的阶段。"""


# ============================================================================
# ProgressStore：进程内进度事件存储
# ============================================================================


@dataclass
class ProgressEvent:
    """单个进度事件。"""

    event_id: int
    type: Literal["started", "progress", "done"]
    data: dict[str, Any]


class ProgressStore:
    """进程级 run_id → events 列表存储；线程 / 协程安全。

    用法：
    - ``store.create(run_id, total)``：开始一个 run
    - ``store.append_progress(...)``：候选人完成 → 写一条 progress
    - ``store.append_done(...)``：run 结束 → 写一条 done
    - ``store.get_events_after(run_id, last_id)``：SSE 重连时取后续事件
    - ``store.wait_event(run_id, after_id, timeout)``：阻塞等下一个事件

    设计：用 ``asyncio.Condition`` 让 SSE 订阅者阻塞等待新事件，
    而不是忙轮询。``Last-Event-ID`` 由 SSE client 通过 HTTP header 传入。
    """

    def __init__(self) -> None:
        self._events: dict[uuid.UUID, list[ProgressEvent]] = {}
        self._meta: dict[uuid.UUID, dict[str, Any]] = {}
        self._conditions: dict[uuid.UUID, asyncio.Condition] = {}
        self._lock = asyncio.Lock()

    async def create(self, run_id: uuid.UUID, *, total: int) -> None:
        async with self._lock:
            self._events[run_id] = []
            self._meta[run_id] = {"total": total, "done": False}
            self._conditions[run_id] = asyncio.Condition()
        await self.append_started(run_id, total=total)

    async def append_started(self, run_id: uuid.UUID, *, total: int) -> None:
        await self._append(
            run_id,
            ProgressEvent(
                event_id=0,
                type="started",
                data={"total": total, "run_id": str(run_id)},
            ),
        )

    async def append_progress(
        self,
        run_id: uuid.UUID,
        *,
        candidate_id: uuid.UUID,
        candidate_name: str | None,
        stage: PipelineStage,
        status: Literal["ok", "failed"],
        reason: str | None = None,
    ) -> None:
        next_id = self._next_id(run_id)
        await self._append(
            run_id,
            ProgressEvent(
                event_id=next_id,
                type="progress",
                data={
                    "candidate_id": str(candidate_id),
                    "candidate_name": candidate_name,
                    "stage": stage,
                    "status": status,
                    "reason": reason,
                },
            ),
        )

    async def append_done(
        self, run_id: uuid.UUID, *, summary: dict[str, Any]
    ) -> None:
        next_id = self._next_id(run_id)
        async with self._lock:
            if run_id in self._meta:
                self._meta[run_id]["done"] = True
        await self._append(
            run_id,
            ProgressEvent(
                event_id=next_id,
                type="done",
                data={"summary": summary, "run_id": str(run_id)},
            ),
        )

    def _next_id(self, run_id: uuid.UUID) -> int:
        events = self._events.get(run_id, [])
        return events[-1].event_id + 1 if events else 0

    async def _append(self, run_id: uuid.UUID, event: ProgressEvent) -> None:
        cond = self._conditions.get(run_id)
        async with cond:
            self._events.setdefault(run_id, []).append(event)
            cond.notify_all()

    def get_events_after(
        self, run_id: uuid.UUID, last_event_id: int
    ) -> list[ProgressEvent]:
        """同步取 last_event_id 之后的所有事件（不含 last_event_id）。"""
        events = self._events.get(run_id, [])
        return [e for e in events if e.event_id > last_event_id]

    def is_done(self, run_id: uuid.UUID) -> bool:
        return self._meta.get(run_id, {}).get("done", False)

    def has_run(self, run_id: uuid.UUID) -> bool:
        """run_id 是否在本进程注册过（用于 SSE 早退判断）。"""
        return run_id in self._events

    async def wait_next_event(
        self,
        run_id: uuid.UUID,
        *,
        after_event_id: int,
        timeout: float = 30.0,
    ) -> ProgressEvent | None:
        """阻塞等下一个事件；超时返回 None（让 SSE 客户端知道还活着）。"""
        cond = self._conditions.get(run_id)
        if cond is None:
            return None
        async with cond:
            events = self._events.get(run_id, [])
            for e in events:
                if e.event_id > after_event_id:
                    return e
            try:
                await asyncio.wait_for(cond.wait(), timeout=timeout)
            except asyncio.TimeoutError:
                return None
            events = self._events.get(run_id, [])
            for e in events:
                if e.event_id > after_event_id:
                    return e
        return None

    def drop(self, run_id: uuid.UUID) -> None:
        """清理已完成 run 的内存（可被外部定期调用）。"""
        self._events.pop(run_id, None)
        self._meta.pop(run_id, None)
        self._conditions.pop(run_id, None)


# 进程级单例
progress_store = ProgressStore()


# ============================================================================
# RunSummary
# ============================================================================


@dataclass
class RunSummary:
    """run 结束后的汇总。"""

    total: int = 0
    passed: int = 0
    disqualified: int = 0
    failed: int = 0
    """score 或 interview 抛错的候选人计数（不影响 filter 淘汰）。"""

    failed_reasons: list[dict[str, str]] = field(default_factory=list)
    """``[{"candidate_id", "stage", "error"}]``"""

    def to_dict(self) -> dict[str, Any]:
        return {
            "total": self.total,
            "passed": self.passed,
            "disqualified": self.disqualified,
            "failed": self.failed,
            "failed_reasons": self.failed_reasons,
        }


# ============================================================================
# ScreeningOrchestrator
# ============================================================================


class ScreeningOrchestrator:
    """编排：Filter → Scorer（含 Reasoning）→ Interview。

    用法：
    - ``orchestrator.run(run_id, job_id, candidate_ids)``：同步串接；
      每完成一个候选人 → 写 progress；最后写 done。
    - ``ProgressStore`` 暴露给 SSE 端点。

    设计：
    - 每个候选人在**一个独立 session**里跑完全流程（避免 session 状态污染）；
      filter + score + interview 三个阶段共享此 session。
    - 任一阶段异常 → 写 progress(failed) + 加入 failed_reasons → 继续下一个候选人。
    - orchestrator **不调 LLM adapter**；全部走 service。
    """

    def __init__(self, *, router=None) -> None:
        self._router = router

    async def run(
        self,
        *,
        run_id: uuid.UUID,
        job_id: uuid.UUID,
        candidate_ids: list[uuid.UUID],
    ) -> RunSummary:
        """主入口：对每个候选人跑 filter → score → interview。

        ``run_id`` 用于进度推送。返回 ``RunSummary``。
        """
        summary = RunSummary(total=len(candidate_ids))
        if not candidate_ids:
            await progress_store.append_done(run_id, summary=summary.to_dict())
            return summary

        # 取候选人姓名用于进度展示
        name_map = await self._fetch_names(candidate_ids)

        for cid in candidate_ids:
            name = name_map.get(cid)
            stage_status = await self._process_candidate(
                run_id=run_id,
                job_id=job_id,
                cid=cid,
                name=name,
                summary=summary,
            )
            # _process_candidate 内部已处理异常；无需在此再分支
            _ = stage_status

        await progress_store.append_done(run_id, summary=summary.to_dict())
        return summary

    async def _process_candidate(
        self,
        *,
        run_id: uuid.UUID,
        job_id: uuid.UUID,
        cid: uuid.UUID,
        name: str | None,
        summary: RunSummary,
    ) -> None:
        """单个候选人完整流水线；任一阶段失败记入 summary + progress 后返回。"""
        try:
            async with AsyncSessionLocal() as session:
                # Stage 1: filter
                filter_service = FilterService(session)
                await filter_service.run_for_candidates(
                    job_id=job_id, candidate_ids=[cid]
                )
                await session.commit()

                sr = await session.scalar(
                    select(ScreeningResult).where(
                        ScreeningResult.job_id == job_id,
                        ScreeningResult.candidate_id == cid,
                    )
                )
                if sr is None:
                    raise RuntimeError(
                        f"filter did not write screening_result for {cid}"
                    )

                if sr.disqualified:
                    summary.disqualified += 1
                    await progress_store.append_progress(
                        run_id,
                        candidate_id=cid,
                        candidate_name=name,
                        stage="filter",
                        status="ok",
                        reason="; ".join(sr.reasons or []) or None,
                    )
                    return

                # Stage 2: score
                try:
                    await run_score(
                        db=session,
                        target_id=cid,
                        payload={
                            "job_id": str(job_id),
                            "team_id": None,
                        },
                        router=self._router,
                    )
                    await session.commit()
                except (CandidateNotFound, JobNotFound, StructureMissing) as exc:
                    await self._record_failure(
                        run_id=run_id, cid=cid, name=name, summary=summary,
                        stage="score", exc=exc,
                    )
                    return
                except Exception as exc:  # noqa: BLE001
                    logger.warning(
                        "orchestrator_score_failed",
                        candidate_id=str(cid),
                        error=str(exc)[:200],
                    )
                    await self._record_failure(
                        run_id=run_id, cid=cid, name=name, summary=summary,
                        stage="score", exc=exc,
                    )
                    return

                await progress_store.append_progress(
                    run_id,
                    candidate_id=cid,
                    candidate_name=name,
                    stage="score",
                    status="ok",
                )

                # Stage 3: interview（失败不阻塞 score 已写入的结果）
                try:
                    interview_service = InterviewService(session, router=self._router)
                    await interview_service.generate(
                        candidate_id=cid, job_id=job_id
                    )
                    await session.commit()
                except InterviewError as exc:
                    logger.warning(
                        "orchestrator_interview_failed",
                        candidate_id=str(cid),
                        error=str(exc)[:200],
                    )
                    await self._record_failure(
                        run_id=run_id, cid=cid, name=name, summary=summary,
                        stage="interview", exc=exc,
                    )
                    return

                summary.passed += 1
                await progress_store.append_progress(
                    run_id,
                    candidate_id=cid,
                    candidate_name=name,
                    stage="interview",
                    status="ok",
                )
        except Exception as exc:  # noqa: BLE001
            # filter 阶段未捕获 / 未知错误：标 failed（filter 阶段）
            logger.exception(
                "orchestrator_candidate_unexpected_failure",
                candidate_id=str(cid),
            )
            await self._record_failure(
                run_id=run_id, cid=cid, name=name, summary=summary,
                stage="filter", exc=exc,
            )

    @staticmethod
    async def _record_failure(
        *,
        run_id: uuid.UUID,
        cid: uuid.UUID,
        name: str | None,
        summary: RunSummary,
        stage: str,
        exc: BaseException,
    ) -> None:
        summary.failed += 1
        summary.failed_reasons.append(
            {
                "candidate_id": str(cid),
                "stage": stage,
                "error": f"{type(exc).__name__}: {exc}",
            }
        )
        await progress_store.append_progress(
            run_id,
            candidate_id=cid,
            candidate_name=name,
            stage=stage,  # type: ignore[arg-type]
            status="failed",
            reason=str(exc)[:200],
        )

    async def _fetch_names(
        self, candidate_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, str | None]:
        if not candidate_ids:
            return {}
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Candidate.id, Candidate.name).where(
                    Candidate.id.in_(candidate_ids)
                )
            )
            return {row[0]: row[1] for row in result.all()}


__all__ = [
    "ScreeningOrchestrator",
    "ProgressStore",
    "ProgressEvent",
    "RunSummary",
    "PipelineStage",
    "progress_store",
]
