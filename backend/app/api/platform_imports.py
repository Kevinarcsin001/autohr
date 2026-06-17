"""招聘平台导入 API 路由（任务 10）。

端点（base: /api/platform-imports）：
- POST /detect   只检测不导入（用于前端给用户预览确认）
- POST /         上传 ZIP / Excel 并立即导入

请求体：multipart/form-data，字段 ``file``
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, File, UploadFile, status

from app.core.deps import CurrentUser, DbSession
from app.core.middleware.error_handler import ValidationError
from app.schemas.platform import (
    DetectionResult,
    PlatformImportResult,
)
from app.services.ingestion.platform_import import (
    PlatformImportAdapter,
    UnsupportedPlatformError,
)

router = APIRouter(prefix="/platform-imports", tags=["platform-imports"])

# 包大小上限（任务 10 包可能是 ZIP，最多 100 文件 × 20 MB = 2 GB；保守 200 MB）
_MAX_PACKAGE_BYTES = 200 * 1024 * 1024


def _require_team(user) -> UUID:
    if user.team_id is None:
        raise ValidationError(
            "当前用户未加入任何团队，无法导入平台包", field="team_id"
        )
    return UUID(str(user.team_id))


async def _read_upload(upload: UploadFile) -> tuple[str, bytes]:
    content = await upload.read()
    if len(content) == 0:
        raise ValidationError("文件为空", field="file")
    if len(content) > _MAX_PACKAGE_BYTES:
        raise ValidationError(
            f"包大小超限（>{_MAX_PACKAGE_BYTES // 1024 // 1024} MB）",
            field="file",
        )
    filename = upload.filename or "package"
    return filename, content


@router.post("/detect", response_model=DetectionResult)
async def detect_platform(
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
) -> DetectionResult:
    """仅检测平台类型 + 包类型，不写库。"""
    _require_team(user)
    filename, content = await _read_upload(file)
    adapter = PlatformImportAdapter(db)
    return await adapter.detect_platform(filename=filename, content=content)


@router.post(
    "/",
    response_model=PlatformImportResult,
    status_code=status.HTTP_201_CREATED,
)
async def import_package(
    user: CurrentUser,
    db: DbSession,
    file: UploadFile = File(...),
) -> PlatformImportResult:
    """上传平台导出包并立即导入。

    不支持的平台格式 → 422（携带 detection + support_feedback_url）。
    """
    team_id = _require_team(user)
    filename, content = await _read_upload(file)
    adapter = PlatformImportAdapter(db)
    try:
        result = await adapter.import_package(
            team_id=team_id, filename=filename, content=content
        )
    except UnsupportedPlatformError:
        # 直接向上抛 → 全局 error_handler 转 422 + context（detection / feedback）
        raise
    return result


__all__ = ["router"]
