"""简历上传 API 路由（任务 9）。

端点（base: /api/uploads）：
- POST /intent   预筛 + 签发 PUT 签名 URL
- POST /confirm  MIME 嗅探 + 写 candidate_resumes + 入 async_jobs

权限：
- 所有端点要求当前用户 team_id 非空
- 跨 team 访问 → 该 item 标 rejected（不抛 403 避免暴露存在性）

响应策略：
- 整批 schema 错（如 files 空、批量超 100）→ 422
- 单 item 不合法（超 20MB / 扩展名非法）→ 该 item rejected，HTTP 200
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter

from app.core.deps import CurrentUser, DbSession
from app.core.logging import get_logger
from app.core.middleware.error_handler import ValidationError
from app.schemas.upload import (
    UploadConfirmRequest,
    UploadConfirmResponse,
    UploadConfirmResponseItem,
    UploadIntentRequest,
    UploadIntentResponse,
    UploadIntentResponseItem,
)
from app.services.ingestion.file_upload import FileUploadService

logger = get_logger(__name__)

router = APIRouter(prefix="/uploads", tags=["uploads"])


def _require_team(user) -> UUID:
    """要求 user.team_id 非空，返回 UUID。"""
    if user.team_id is None:
        raise ValidationError(
            "当前用户未加入任何团队，无法上传简历",
            field="team_id",
        )
    return UUID(str(user.team_id))


@router.post("/intent", response_model=UploadIntentResponse)
async def create_upload_intent(
    payload: UploadIntentRequest,
    user: CurrentUser,
    db: DbSession,
) -> UploadIntentResponse:
    """阶段 1：批量预筛 + 签发 PUT 签名 URL。"""
    team_id = _require_team(user)
    service = FileUploadService(db)

    try:
        items: list[UploadIntentResponseItem] = await service.create_intent(
            team_id=team_id, files=payload.files
        )
    except ValueError as exc:
        # 批量超限 → 422
        raise ValidationError(str(exc), field="files") from exc

    accepted = sum(1 for i in items if i.status == "ok")
    rejected = len(items) - accepted
    return UploadIntentResponse(
        items=items, accepted=accepted, rejected=rejected
    )


@router.post("/confirm", response_model=UploadConfirmResponse)
async def confirm_uploads(
    payload: UploadConfirmRequest,
    user: CurrentUser,
    db: DbSession,
) -> UploadConfirmResponse:
    """阶段 3：嗅探 MIME + 写 candidate_resumes + 入 async_jobs。"""
    team_id = _require_team(user)
    service = FileUploadService(db)

    items: list[UploadConfirmResponseItem] = await service.confirm_uploads(
        team_id=team_id, items=payload.items
    )
    confirmed = sum(1 for i in items if i.status == "ok")
    rejected = len(items) - confirmed
    return UploadConfirmResponse(
        items=items, confirmed=confirmed, rejected=rejected
    )


__all__ = ["router"]
