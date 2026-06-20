"""简历上传 schema（任务 9）。

三阶段流程的请求/响应模型：
- intent：客户端告诉服务端"我准备传这几个文件"
- 客户端 PUT 直传 MinIO（用签名 URL）
- confirm：服务端嗅探 MIME + 写 candidate_resumes + 入 async_jobs

设计要点：
- 部分批次失败不阻塞：每个 file 都有独立 status/reject_reason
- 服务端永远返回 200（除非请求本身 schema 错 → 422）
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# intent
# ============================================================================


class UploadIntentItem(BaseModel):
    """单个文件元信息（intent 请求项）。"""

    model_config = ConfigDict(extra="forbid")

    filename: str = Field(..., min_length=1, max_length=255)
    size_bytes: int = Field(..., ge=0)
    mime_client: str = Field(..., min_length=1, max_length=100)


class UploadIntentRequest(BaseModel):
    """intent 请求体。"""

    model_config = ConfigDict(extra="forbid")

    files: list[UploadIntentItem] = Field(..., min_length=1)


class UploadIntentResponseItem(BaseModel):
    """intent 单项响应。"""

    model_config = ConfigDict(extra="forbid")

    upload_id: UUID
    filename: str
    file_key: str
    signed_url: str | None = Field(
        default=None, description="status=ok 时返回 PUT 签名 URL"
    )
    expires_in: int | None = None
    method: Literal["PUT"] = "PUT"
    status: Literal["ok", "rejected"]
    reject_reason: Literal[
        "size_exceeded",
        "extension_not_allowed",
        "batch_too_large",
    ] | None = None


class UploadIntentResponse(BaseModel):
    """intent 整体响应。"""

    model_config = ConfigDict(extra="forbid")

    items: list[UploadIntentResponseItem]
    accepted: int = Field(..., description="被接受的文件数")
    rejected: int = Field(..., description="被拒绝的文件数")


# ============================================================================
# confirm
# ============================================================================


class UploadConfirmItem(BaseModel):
    """单个 confirm 请求项。"""

    model_config = ConfigDict(extra="forbid")

    upload_id: UUID
    file_key: str = Field(..., min_length=1, max_length=512)


class UploadConfirmRequest(BaseModel):
    """confirm 请求体。"""

    model_config = ConfigDict(extra="forbid")

    items: list[UploadConfirmItem] = Field(..., min_length=1)
    job_id: UUID | None = Field(default=None, description="可选：关联到指定职位，创建 screening_result")


class UploadConfirmResponseItem(BaseModel):
    """confirm 单项响应。"""

    model_config = ConfigDict(extra="forbid")

    upload_id: UUID
    resume_id: UUID | None = Field(
        default=None, description="status=ok 时返回新建的 CandidateResume.id"
    )
    candidate_id: UUID | None = None
    status: Literal["ok", "rejected"]
    reject_reason: Literal[
        "object_missing",
        "mime_not_allowed",
        "mime_mismatch",
        "cross_team",
        "duplicate_enqueue",
    ] | None = None


class UploadConfirmResponse(BaseModel):
    """confirm 整体响应。"""

    model_config = ConfigDict(extra="forbid")

    items: list[UploadConfirmResponseItem]
    confirmed: int
    rejected: int


__all__ = [
    "UploadIntentItem",
    "UploadIntentRequest",
    "UploadIntentResponseItem",
    "UploadIntentResponse",
    "UploadConfirmItem",
    "UploadConfirmRequest",
    "UploadConfirmResponseItem",
    "UploadConfirmResponse",
]
