"""Exports API 路由（任务 22）。

端点（base: /api/exports）：
- POST /                请求导出（自动判断同步/异步）
- GET  /jobs/{job_id}   查询异步导出任务状态
- GET  /download        取 5min 签名下载 URL（需 file_key）

权限：
- 所有端点要求当前用户 team_id 非空
- 跨 team 资源访问返回 404
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query

from app.core.deps import CurrentUser, DbSession
from app.core.middleware.error_handler import ForbiddenError, NotFoundError
from app.models.async_job import AsyncJob
from app.schemas.export import (
    ExportRequest,
    ExportResultQuery,
)
from app.services.export import ExportService

router = APIRouter(prefix="/exports", tags=["exports"])


def _require_team(user) -> UUID:
    if user.team_id is None:
        raise ForbiddenError("当前用户未加入任何团队")
    return UUID(str(user.team_id))


# ============================================================================
# POST /  请求导出
# ============================================================================


@router.post("/", status_code=202)
async def request_export(
    payload: ExportRequest,
    user: CurrentUser,
    db: DbSession,
) -> dict:
    """请求导出；自动判断同步 vs 异步。

    - 行数 ≤ 5000 → 同步生成，返回 ``download_url``
    - 行数 > 5000 → 异步入队，返回 ``job_id``
    """
    team_id = _require_team(user)
    service = ExportService(db)
    result = await service.request_export(
        team_id=team_id,
        user_id=user.id,
        job_id=payload.job_id,
        filters=payload.filters or {},
        format=payload.format,
    )
    await db.commit()
    return result


# ============================================================================
# GET /jobs/{job_id}  查询异步导出状态
# ============================================================================


@router.get("/jobs/{job_id}", response_model=ExportResultQuery)
async def get_export_status(
    job_id: UUID,
    user: CurrentUser,
    db: DbSession,
) -> ExportResultQuery:
    """查异步导出任务状态。

    注：job_id 这里指 async_job.id（与 URL path 一致；导出任务以 async_job 为单位）。
    """
    _require_team(user)
    job = await db.get(AsyncJob, job_id)
    if job is None or job.task_type != "export":
        raise NotFoundError(
            f"export job {job_id} 不存在", resource="export_job"
        )
    # team 隔离：payload 内的 team_id 必须匹配
    payload_team_id = (job.payload or {}).get("team_id")
    if payload_team_id and UUID(str(payload_team_id)) != user.team_id:
        raise NotFoundError(
            f"export job {job_id} 不存在或无权访问", resource="export_job"
        )

    result = (job.payload or {}).get("result") or {}
    return ExportResultQuery(
        job_id=job.id,
        status=job.status,
        file_key=result.get("file_key"),
        file_size=result.get("file_size"),
        row_count=result.get("row_count"),
        download_url=result.get("download_url"),
        error=(
            job.error
            if job.status == "failed" and job.error
            else None
        ),
    )


# ============================================================================
# GET /download  取 5min 签名下载 URL
# ============================================================================


@router.get("/download")
async def get_download_url(
    user: CurrentUser,
    db: DbSession,
    file_key: str = Query(...),
) -> dict:
    """取 5min 签名下载 URL；校验 file_key 前缀归属 team。"""
    team_id = _require_team(user)
    service = ExportService(db)
    url = await service.get_signed_download_url(
        team_id=team_id, file_key=file_key
    )
    return {"download_url": url, "expires_in": 300}


__all__ = ["router"]
