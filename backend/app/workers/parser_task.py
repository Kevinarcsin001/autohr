"""Parser celery task 实现（任务 13 接入任务 12 的 parse_resume_handler）。

职责：
1. 从 AsyncJob 拿到 target_id（= candidate_resume.id）和 payload（file_key/mime）
2. 从 storage 下载文件
3. 调 ``ParserService.parse()`` 得到 text + status
4. 写回 ``candidate_resumes.parsed_text`` + ``parse_status`` + ``parse_error``
5. 解析成功后，**触发下游 extract 任务**（任务 14 实现）

约束：
- 失败保留原文件（不删 storage 对象）
- 不在内存保存全文（实际 parsed_text 还是要写库；为满足"流式"，
  我们把解析结果直接写库后释放，不在 Python 进程里长期持有）
- 失败不重试（损坏文件再试也没用）→ 直接 mark_failed
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage import S3StorageAdapter, get_storage
from app.core.logging import get_logger
from app.models.candidate import CandidateResume
from app.services.parser import ParserService, ParsedResult

logger = get_logger(__name__)


class ResumeNotFound(Exception):
    """target_id 对应的 CandidateResume 不存在（已被删？）。"""


class StorageObjectMissing(Exception):
    """file_key 在 MinIO 中不存在。"""


async def run_parse(
    *,
    db: AsyncSession,
    storage: S3StorageAdapter | None,
    target_id: uuid.UUID,
    payload: dict[str, Any] | None,
    parser: ParserService | None = None,
) -> dict[str, Any]:
    """执行解析并把结果写回 DB。

    Args:
        db: 异步 session（caller 控制 commit）
        storage: 对象存储（None 时用 get_storage()）
        target_id: candidate_resume.id
        payload: 含 file_key + mime
        parser: 测试可注入

    Returns:
        ``{"resume_id", "status", "text_len", "ocr_backend"}`` 摘要

    Raises:
        ResumeNotFound: 简历行不存在
        StorageObjectMissing: 文件对象不在 MinIO
    """
    if not payload:
        raise ValueError("payload missing for parse task")
    file_key = payload.get("file_key")
    mime = payload.get("mime")
    if not file_key or not mime:
        raise ValueError(f"payload missing file_key/mime: {payload}")

    # 1. 加载 resume 行
    resume = await db.get(CandidateResume, target_id)
    if resume is None:
        raise ResumeNotFound(f"CandidateResume {target_id} not found")

    storage = storage or get_storage()
    parser = parser or ParserService()

    # 2. 下载文件
    try:
        content = await storage.get(file_key)
    except Exception as exc:  # noqa: BLE001
        logger.exception(
            "parse_storage_get_failed",
            resume_id=str(target_id),
            file_key=file_key,
        )
        raise StorageObjectMissing(
            f"storage.get failed for {file_key}: {exc}"
        ) from exc

    # 3. 解析
    result: ParsedResult = await parser.parse(content, mime=mime)

    # 4. 写回 DB（流式：写完即可释放 content；本函数不持有引用）
    resume.parsed_text = result.text if result.text else None
    resume.parse_status = result.status  # success / low_text / failed
    resume.parse_error = result.error
    await db.flush()

    logger.info(
        "parse_completed",
        resume_id=str(target_id),
        status=result.status,
        text_len=len(result.text),
        ocr_backend=result.ocr_backend,
        mime=mime,
    )

    return {
        "resume_id": str(target_id),
        "status": result.status,
        "text_len": len(result.text),
        "ocr_backend": result.ocr_backend,
    }


__all__ = ["run_parse", "ResumeNotFound", "StorageObjectMissing"]
