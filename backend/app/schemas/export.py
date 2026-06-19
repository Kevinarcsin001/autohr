"""Export Pydantic schemas（任务 22）。"""
from __future__ import annotations

from typing import Any
from uuid import UUID

from pydantic import BaseModel, Field


class ExportRequest(BaseModel):
    """请求导出。"""

    job_id: UUID
    format: str = Field(default="xlsx", pattern="^(xlsx|csv)$")
    filters: dict[str, Any] | None = Field(
        default=None,
        description=(
            "可选过滤：disqualified(bool) / min_score(int) / "
            "team_ids(list[str])"
        ),
    )


class ExportSyncResponse(BaseModel):
    """同步导出响应（行数 ≤ 阈值）。"""

    mode: str = Field(pattern="^sync$")
    download_url: str
    expires_in: int = Field(ge=1, le=3600)
    row_count: int = Field(ge=0)
    file_key: str
    file_size: int = Field(ge=0)


class ExportAsyncResponse(BaseModel):
    """异步导出响应（行数 > 阈值）。"""

    mode: str = Field(pattern="^async$")
    job_id: UUID
    row_count: int = Field(ge=0)


class ExportResultQuery(BaseModel):
    """查询异步导出结果（async_jobs.payload['result']）。"""

    job_id: UUID
    status: str
    file_key: str | None = None
    file_size: int | None = None
    row_count: int | None = None
    download_url: str | None = None
    error: str | None = None


__all__ = [
    "ExportRequest",
    "ExportSyncResponse",
    "ExportAsyncResponse",
    "ExportResultQuery",
]
