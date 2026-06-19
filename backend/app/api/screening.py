"""Screening API 路由（任务 16 + 任务 20）：硬性筛选 + HR 改判 + 编排流水线 + SSE。

端点（base: /api/screening）：
- POST   /run                              对指定 job 跑筛选（可选 candidate_ids）
- POST   /pipeline                         异步编排：Filter → Scorer → Interview
- GET    /pipeline/{run_id}/events         SSE 进度推送（支持 Last-Event-ID 断线重连）
- GET    /pipeline/{run_id}/summary        取 run 结束后的 summary（轮询备用）
- GET    /results?job_id=&disqualified=    分页列出结果（带候选人姓名）
- GET    /results/{id}/overrides           某结果的改判历史
- PATCH  /results/{id}/override            HR 改判（写 manual_overrides）

权限：
- 所有端点要求当前用户 team_id 非空
- 跨 team 资源访问返回 404
"""
from __future__ import annotations

import json
import uuid
from uuid import UUID

from fastapi import APIRouter, BackgroundTasks, Header, Query, status
from fastapi.responses import StreamingResponse
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.core.middleware.error_handler import ForbiddenError, NotFoundError
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.screening import ScreeningResult
from app.schemas.screening import (
    OverrideRequest,
    OverrideResponse,
    PipelineRunRequest,
    PipelineRunResponse,
    PipelineSummary,
    ScreeningResultListItem,
    ScreeningResultListResponse,
    ScreeningResultOut,
    ScreeningRunRequest,
    ScreeningRunResponse,
)
from app.services.filter import FilterService
from app.services.screening_orchestrator import (
    ScreeningOrchestrator,
    progress_store,
)

logger = get_logger(__name__)

router = APIRouter(prefix="/screening", tags=["screening"])


def _require_team(user) -> UUID:
    if user.team_id is None:
        raise ForbiddenError("当前用户未加入任何团队")
    return UUID(str(user.team_id))


async def _validate_job_in_team(db, job_id: UUID, team_id: UUID) -> Job:
    """校验 job 归属 team；跨 team 返回 404。"""
    job = await db.get(Job, job_id)
    if job is None or job.team_id != team_id:
        raise NotFoundError(
            f"job {job_id} 不存在或无权访问", resource="job"
        )
    return job


# ============================================================================
# 运行筛选
# ============================================================================


@router.post(
    "/run",
    response_model=ScreeningRunResponse,
    status_code=status.HTTP_200_OK,
)
async def run_screening(
    payload: ScreeningRunRequest,
    user: CurrentUser,
    db: DbSession,
) -> ScreeningRunResponse:
    """对指定 job 跑硬性筛选。

    - 不传 ``candidate_ids`` → 默认对该 job 的候选人暂不自动展开
      （需要调用方明确传 ids；全量跑留给后续 celery 任务 17 接入）
    - 传 ``candidate_ids`` → 仅对这些跑
    """
    team_id = _require_team(user)
    await _validate_job_in_team(db, payload.job_id, team_id)

    candidate_ids = payload.candidate_ids or []

    # 跨 team candidate 校验
    if candidate_ids:
        result = await db.execute(
            select(Candidate).where(
                Candidate.id.in_(candidate_ids),
                Candidate.team_id == team_id,
            )
        )
        valid = {c.id for c in result.scalars().all()}
        candidate_ids = [cid for cid in candidate_ids if cid in valid]

    service = FilterService(db)
    summary = await service.run_for_candidates(
        job_id=payload.job_id, candidate_ids=candidate_ids
    )
    await db.commit()

    return ScreeningRunResponse(
        job_id=payload.job_id,
        processed=summary["processed"],
        disqualified=summary["disqualified"],
        passed=summary["passed"],
    )


# ============================================================================
# 列表
# ============================================================================


@router.get("/results", response_model=ScreeningResultListResponse)
async def list_results(
    user: CurrentUser,
    db: DbSession,
    job_id: UUID = Query(...),
    disqualified: bool | None = Query(default=None),
    page: int = Query(default=1, ge=1),
    page_size: int = Query(default=50, ge=1, le=200),
) -> ScreeningResultListResponse:
    """列出 job 的筛选结果。"""
    team_id = _require_team(user)
    await _validate_job_in_team(db, job_id, team_id)

    service = FilterService(db)
    limit = page_size
    offset = (page - 1) * page_size
    rows, total = await service.list_results(
        job_id=job_id,
        only_disqualified=disqualified,
        limit=limit,
        offset=offset,
    )
    items = [
        ScreeningResultListItem(
            id=r.id,
            candidate_id=r.candidate_id,
            candidate_name=name,
            disqualified=r.disqualified,
            reasons=r.reasons,
            manually_overridden=r.manually_overridden,
        )
        for r, name in rows
    ]
    disqualified_count = sum(1 for it in items if it.disqualified)
    return ScreeningResultListResponse(
        items=items, total=total, disqualified_count=disqualified_count
    )


# ============================================================================
# 任务 20：Pipeline（异步触发 + SSE）
# ============================================================================


async def _run_pipeline_in_background(
    run_id: uuid.UUID,
    job_id: uuid.UUID,
    candidate_ids: list[uuid.UUID],
) -> None:
    """BackgroundTask 入口：调 orchestrator，吞掉所有异常以避免 background 抛错。"""
    try:
        orchestrator = ScreeningOrchestrator()
        await orchestrator.run(
            run_id=run_id,
            job_id=job_id,
            candidate_ids=candidate_ids,
        )
    except Exception:  # noqa: BLE001
        logger.exception("pipeline_background_failed", run_id=str(run_id))
        try:
            await progress_store.append_done(
                run_id,
                summary={
                    "total": len(candidate_ids),
                    "passed": 0,
                    "disqualified": 0,
                    "failed": len(candidate_ids),
                    "failed_reasons": [
                        {
                            "candidate_id": "",
                            "stage": "pipeline",
                            "error": "internal error",
                        }
                    ],
                },
            )
        except Exception:  # noqa: BLE001
            pass


@router.post(
    "/pipeline",
    response_model=PipelineRunResponse,
    status_code=status.HTTP_202_ACCEPTED,
)
async def trigger_pipeline(
    payload: PipelineRunRequest,
    user: CurrentUser,
    db: DbSession,
    background_tasks: BackgroundTasks,
) -> PipelineRunResponse:
    """异步触发 Filter → Scorer → Interview 流水线。

    - 立即返回 ``run_id``，前端可通过 ``/pipeline/{run_id}/events`` 订阅 SSE
    - 跨 team 的 candidate_id 会被自动过滤
    - 进度推送：每个候选人完成（任一阶段）→ ``progress`` 事件
    """
    team_id = _require_team(user)
    await _validate_job_in_team(db, payload.job_id, team_id)

    # 过滤跨 team candidate
    result = await db.execute(
        select(Candidate).where(
            Candidate.id.in_(payload.candidate_ids),
            Candidate.team_id == team_id,
        )
    )
    valid_ids = [c.id for c in result.scalars().all()]

    run_id = uuid.uuid4()
    await progress_store.create(run_id, total=len(valid_ids))

    background_tasks.add_task(
        _run_pipeline_in_background,
        run_id,
        payload.job_id,
        valid_ids,
    )

    return PipelineRunResponse(
        run_id=run_id,
        job_id=payload.job_id,
        total=len(valid_ids),
    )


@router.get(
    "/pipeline/{run_id}/events",
)
async def stream_pipeline_events(
    run_id: UUID,
    user: CurrentUser,
    last_event_id: str | None = Header(
        default=None, alias="Last-Event-ID"
    ),
) -> StreamingResponse:
    """SSE 推送 pipeline 进度（支持断线重连）。

    - 通过 ``Last-Event-ID`` HTTP Header 传入上次最后看到的 event_id；
      server 从 ``event_id+1`` 开始推。
    - 流式输出 ``text/event-stream``；done 后自动断开。
    - 客户端可重连，server 端 events 保留在内存（run 结束后仍可读取 summary）。
    """
    _require_team(user)

    last_id = -1
    if last_event_id is not None:
        try:
            last_id = int(last_event_id)
        except ValueError:
            last_id = -1

    async def event_generator():
        next_id = last_id + 1
        # 先把存量事件一次性吐完
        events = progress_store.get_events_after(uuid.UUID(str(run_id)), last_id)
        for e in events:
            yield _format_sse(e)
            next_id = e.event_id + 1
            if e.type == "done":
                return

        # 未知 run_id（无 condition）→ 直接结束，避免 client 死等
        if not progress_store.has_run(uuid.UUID(str(run_id))):
            return

        # 流式订阅新事件
        while True:
            ev = await progress_store.wait_next_event(
                uuid.UUID(str(run_id)),
                after_event_id=next_id - 1,
                timeout=15.0,
            )
            if ev is None:
                # 发 keep-alive ping，避免代理超时断开
                yield ": ping\n\n"
                continue
            yield _format_sse(ev)
            next_id = ev.event_id + 1
            if ev.type == "done":
                return

    return StreamingResponse(
        event_generator(),
        media_type="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "Connection": "keep-alive",
            "X-Accel-Buffering": "no",
        },
    )


def _format_sse(event) -> str:
    """格式化为 SSE 单事件块。"""
    payload = {
        "type": event.type,
        **event.data,
    }
    data_str = json.dumps(payload, ensure_ascii=False, default=str)
    return f"event: {event.type}\nid: {event.event_id}\ndata: {data_str}\n\n"


@router.get(
    "/pipeline/{run_id}/summary",
    response_model=PipelineSummary,
)
async def get_pipeline_summary(
    run_id: UUID,
    user: CurrentUser,
) -> PipelineSummary:
    """取 run 的 summary（轮询备用）；run 未完成返回当前累计快照。"""
    _require_team(user)
    events = progress_store.get_events_after(uuid.UUID(str(run_id)), -1)
    # 最后一个 done 事件含完整 summary
    for e in reversed(events):
        if e.type == "done":
            return PipelineSummary(**e.data["summary"])
    # 没找到 done → 累计当前进度
    total = 0
    passed = 0
    failed = 0
    disqualified = 0
    failed_reasons: list[dict[str, str]] = []
    for e in events:
        if e.type != "progress":
            continue
        total += 1
        if e.data.get("status") == "failed":
            failed += 1
            failed_reasons.append(
                {
                    "candidate_id": e.data.get("candidate_id", ""),
                    "stage": e.data.get("stage", ""),
                    "error": e.data.get("reason") or "",
                }
            )
        elif e.data.get("stage") == "filter":
            disqualified += 1
        else:
            passed += 1
    return PipelineSummary(
        total=total,
        passed=passed,
        disqualified=disqualified,
        failed=failed,
        failed_reasons=failed_reasons,
    )


# ============================================================================
# HR 改判
# ============================================================================


@router.patch(
    "/results/{result_id}/override",
    response_model=OverrideResponse,
    status_code=status.HTTP_200_OK,
)
async def override_result(
    result_id: UUID,
    payload: OverrideRequest,
    user: CurrentUser,
    db: DbSession,
) -> OverrideResponse:
    """HR 改判 disqualified + reasons。

    - 必须填 ``reason``
    - 写 ``manual_overrides`` 审计行（old/new value + actor + reason）
    - 标记 ``screening_results.manually_overridden = True``
    """
    team_id = _require_team(user)

    # 校验归属：通过 screening_result → candidate → team
    sr = await db.get(ScreeningResult, result_id)
    if sr is not None:
        cand = await db.get(Candidate, sr.candidate_id)
        if cand is None or cand.team_id != team_id:
            sr = None
    if sr is None:
        raise NotFoundError(
            f"screening_result {result_id} 不存在或无权访问",
            resource="screening_result",
        )

    service = FilterService(db)
    updated_sr, override = await service.override(
        screening_result_id=result_id,
        actor_id=user.id,
        new_disqualified=payload.new_disqualified,
        new_reasons=payload.new_reasons,
        reason=payload.reason,
    )
    await db.commit()

    return OverrideResponse(
        screening_result=ScreeningResultOut.model_validate(updated_sr),
        override_id=override.id,
    )


@router.get("/results/{result_id}/overrides", response_model=list)
async def list_overrides(
    result_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> list[dict]:
    """列出某 screening_result 的改判历史。"""
    team_id = _require_team(user)

    sr = await db.get(ScreeningResult, result_id)
    if sr is not None:
        cand = await db.get(Candidate, sr.candidate_id)
        if cand is None or cand.team_id != team_id:
            sr = None
    if sr is None:
        raise NotFoundError(
            f"screening_result {result_id} 不存在或无权访问",
            resource="screening_result",
        )

    service = FilterService(db)
    overrides = await service.list_overrides(screening_result_id=result_id)

    return [
        {
            "id": str(o.id),
            "actor_id": str(o.actor_id),
            "old_value": o.old_value,
            "new_value": o.new_value,
            "reason": o.reason,
            "created_at": o.created_at.isoformat() if o.created_at else None,
        }
        for o in overrides
    ]


__all__ = ["router"]
