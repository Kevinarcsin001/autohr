"""FileUploadService：三阶段签名 URL 上传链路（任务 9）。

阶段：
1. ``create_intent``  → 客户端提供 [filename, size_bytes, mime_client]，
                        服务端预筛（扩展名 + 大小 + 批量配额），
                        为接受的文件签发 PUT URL。
2. 客户端 PUT <signed_url> 直接传 MinIO（后端零流量）。
3. ``confirm_uploads`` → 服务端 head 验存在 + 读前 ``UPLOAD_SNIFF_BYTES`` 字节做
                         ``python-magic.from_buffer`` 嗅探 → 真实 MIME 白名单校验 →
                         写 Candidate + CandidateSource + CandidateResume +
                         AsyncJob(task_type="parse")。

约束：
- MIME 不能只看扩展名（tasks.md:78 硬性要求）
- 单文件超限前端拒绝，服务端 intent 也再校验一次（防绕过）
- 批次中单文件失败不阻塞其他文件
- 跨 team 访问 file_key → rejected（不暴露存在性）

事务策略：service 接收 session，不 commit。
"""
from __future__ import annotations

import uuid
from pathlib import PurePosixPath
from typing import TYPE_CHECKING

import magic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage import (
    S3StorageAdapter,
    StorageNotFoundError,
    get_storage,
)
from app.core.config import settings
from app.core.logging import get_logger
from app.models.async_job import AsyncJob
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
)
from app.schemas.upload import (
    UploadConfirmItem,
    UploadConfirmResponseItem,
    UploadIntentItem,
    UploadIntentResponseItem,
)

if TYPE_CHECKING:
    pass

logger = get_logger(__name__)

# ============================================================================
# 配置（启动时从 settings 解析一次，避免每次请求 split）
# ============================================================================


def _parse_csv(value: str) -> set[str]:
    return {v.strip().lower() for v in value.split(",") if v.strip()}


_ALLOWED_MIME: set[str] = _parse_csv(settings.UPLOAD_ALLOWED_MIME_TYPES)
_ALLOWED_EXT: set[str] = _parse_csv(settings.UPLOAD_ALLOWED_EXTENSIONS)
_MAX_FILE_BYTES: int = settings.MAX_UPLOAD_FILE_SIZE_MB * 1024 * 1024
_MAX_BATCH: int = settings.MAX_UPLOAD_BATCH_SIZE

# 扩展名 → 嗅探必须匹配的 MIME（防伪装：.pdf 必须嗅出 application/pdf）
_EXT_TO_MIME: dict[str, str] = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


def _ext(filename: str) -> str:
    """提取扩展名（不含点，小写）。"""
    return PurePosixPath(filename).suffix.lstrip(".").lower()


def _is_allowed_size(size_bytes: int) -> bool:
    return 0 < size_bytes <= _MAX_FILE_BYTES


def _is_allowed_ext(filename: str) -> bool:
    return _ext(filename) in _ALLOWED_EXT


def _build_file_key(team_id: uuid.UUID, ext: str) -> str:
    """`{team_id}/{uuid}/{uuid}.{ext}` — 团队隔离前缀。

    中间一层 uuid 作为"上传会话"分组，便于审计 / 清理；
    末尾 uuid 作为对象实际名，避免同名覆盖。
    """
    safe_ext = ext or "bin"
    return f"{team_id}/{uuid.uuid4()}/{uuid.uuid4()}.{safe_ext}"


# ============================================================================
# FileUploadService
# ============================================================================


class FileUploadService:
    """三阶段上传 service。

    构造函数注入 db + storage 便于测试替换。
    """

    def __init__(
        self,
        db: AsyncSession,
        storage: S3StorageAdapter | None = None,
    ) -> None:
        self.db = db
        self.storage = storage or get_storage()

    # ----- 阶段 1：intent -----

    async def create_intent(
        self,
        *,
        team_id: uuid.UUID,
        files: list[UploadIntentItem],
    ) -> list[UploadIntentResponseItem]:
        """批量预筛 + 签发 PUT 签名 URL。

        单文件非法只把该文件标 rejected，其他正常签发。
        """
        # 整批数量上限：超 → 全部拒（且后续 raise 由调用层转 422）
        if len(files) > _MAX_BATCH:
            raise ValueError(
                f"批量超出上限：收到 {len(files)} 个，最大 {_MAX_BATCH}"
            )

        results: list[UploadIntentResponseItem] = []
        for item in files:
            upload_id = uuid.uuid4()
            ext = _ext(item.filename)

            if not _is_allowed_size(item.size_bytes):
                results.append(
                    UploadIntentResponseItem(
                        upload_id=upload_id,
                        filename=item.filename,
                        file_key="",
                        status="rejected",
                        reject_reason="size_exceeded",
                    )
                )
                continue

            if not _is_allowed_ext(item.filename):
                results.append(
                    UploadIntentResponseItem(
                        upload_id=upload_id,
                        filename=item.filename,
                        file_key="",
                        status="rejected",
                        reject_reason="extension_not_allowed",
                    )
                )
                continue

            file_key = _build_file_key(team_id, ext)
            signed_url = await self.storage.signed_url(
                file_key, method="PUT"
            )
            results.append(
                UploadIntentResponseItem(
                    upload_id=upload_id,
                    filename=item.filename,
                    file_key=file_key,
                    signed_url=signed_url,
                    expires_in=settings.STORAGE_SIGNED_URL_EXPIRE_SECONDS,
                    status="ok",
                )
            )

        return results

    # ----- 阶段 3：confirm -----

    async def confirm_uploads(
        self,
        *,
        team_id: uuid.UUID,
        items: list[UploadConfirmItem],
        job_id: uuid.UUID | None = None,
    ) -> list[UploadConfirmResponseItem]:
        """嗅探 MIME + 落库 + 入队。可选 job_id 关联职位。

        任一 item 失败 → 该 item 标 rejected，其他不受影响。
        """
        results: list[UploadConfirmResponseItem] = []
        for item in items:
            res = await self._confirm_one(team_id=team_id, item=item, job_id=job_id)
            results.append(res)
        return results

    async def _confirm_one(
        self,
        *,
        team_id: uuid.UUID,
        job_id: uuid.UUID | None = None,
        item: UploadConfirmItem,
    ) -> UploadConfirmResponseItem:
        # 跨 team 校验：file_key 第一段必须是当前 team_id
        try:
            prefix_team = str(item.file_key).split("/", 1)[0]
        except (ValueError, AttributeError):
            return UploadConfirmResponseItem(
                upload_id=item.upload_id,
                status="rejected",
                reject_reason="cross_team",
            )
        if prefix_team != str(team_id):
            logger.warning(
                "upload_cross_team_access",
                actor_team=str(team_id),
                file_key_team=prefix_team,
            )
            return UploadConfirmResponseItem(
                upload_id=item.upload_id,
                status="rejected",
                reject_reason="cross_team",
            )

        # 对象存在性校验
        try:
            exists = await self.storage.exists(item.file_key)
        except Exception:
            logger.exception("storage_exists_failed", file_key=item.file_key)
            return UploadConfirmResponseItem(
                upload_id=item.upload_id,
                status="rejected",
                reject_reason="object_missing",
            )
        if not exists:
            return UploadConfirmResponseItem(
                upload_id=item.upload_id,
                status="rejected",
                reject_reason="object_missing",
            )

        # MIME 嗅探：读前 N 字节
        try:
            head_bytes = await self.storage.get_range(
                item.file_key, 0, settings.UPLOAD_SNIFF_BYTES - 1
            )
        except StorageNotFoundError:
            return UploadConfirmResponseItem(
                upload_id=item.upload_id,
                status="rejected",
                reject_reason="object_missing",
            )
        except Exception:
            logger.exception("storage_get_range_failed", file_key=item.file_key)
            return UploadConfirmResponseItem(
                upload_id=item.upload_id,
                status="rejected",
                reject_reason="object_missing",
            )

        try:
            real_mime = magic.from_buffer(head_bytes, mime=True)
        except Exception:
            logger.exception("magic_sniff_failed", file_key=item.file_key)
            return UploadConfirmResponseItem(
                upload_id=item.upload_id,
                status="rejected",
                reject_reason="mime_not_allowed",
            )

        if real_mime not in _ALLOWED_MIME:
            logger.warning(
                "upload_mime_rejected",
                file_key=item.file_key,
                real_mime=real_mime,
            )
            return UploadConfirmResponseItem(
                upload_id=item.upload_id,
                status="rejected",
                reject_reason="mime_not_allowed",
            )

        # 扩展名 ↔ MIME 一致性校验（防伪装：.pdf 必须嗅出 application/pdf）
        ext = _ext(item.file_key)
        expected_mime = _EXT_TO_MIME.get(ext)
        if expected_mime is not None and real_mime != expected_mime:
            logger.warning(
                "upload_mime_mismatch",
                file_key=item.file_key,
                ext=ext,
                expected=expected_mime,
                real_mime=real_mime,
            )
            return UploadConfirmResponseItem(
                upload_id=item.upload_id,
                status="rejected",
                reject_reason="mime_mismatch",
            )

        # 写库：Candidate（dedup_key 占位）+ Source + Resume
        # TODO(task-14): real dedup_key strategy —— 当前用 file_key 占位，
        #                解析后由 task 14 dedup service 用真实 name+phone+email 合并
        dedup_key = f"upload:{item.file_key}"
        try:
            existing = await self.db.scalar(
                select(Candidate).where(Candidate.dedup_key == dedup_key)
            )
            if existing is not None:
                # 重复 confirm 同一 file_key：直接复用现有记录，不重复入队
                resume = await self.db.scalar(
                    select(CandidateResume).where(
                        CandidateResume.file_storage_key == item.file_key
                    )
                )
                return UploadConfirmResponseItem(
                    upload_id=item.upload_id,
                    resume_id=resume.id if resume else None,
                    candidate_id=existing.id,
                    status="rejected",
                    reject_reason="duplicate_enqueue",
                )

            candidate = Candidate(
                team_id=team_id,
                dedup_key=dedup_key,
                # 上传时刻真实姓名未知，暂用对象 basename 作为可读标记
                name=item.file_key.rsplit("/", 1)[-1],
                phone=None,
                email=None,
            )
            self.db.add(candidate)
            await self.db.flush()  # 拿 candidate.id

            source = CandidateSource(
                candidate_id=candidate.id,
                source_type="upload",
                source_meta={"upload_id": str(item.upload_id)},
            )
            self.db.add(source)
            await self.db.flush()  # 拿 source.id

            resume = CandidateResume(
                candidate_id=candidate.id,
                source_id=source.id,
                file_storage_key=item.file_key,
                file_mime=real_mime,
                parse_status="pending",
            )
            self.db.add(resume)
            await self.db.flush()  # 拿 resume.id

            # 入 async_jobs 队列并立即派遣 Celery 任务
            idem_key = f"parse:{resume.id}"
            existing_job = await self.db.scalar(
                select(AsyncJob).where(AsyncJob.idempotency_key == idem_key)
            )
            if existing_job is None:
                job = AsyncJob(
                    task_type="parse",
                    target_id=resume.id,
                    status="queued",
                    idempotency_key=idem_key,
                    payload={"file_key": item.file_key, "mime": real_mime},
                )
                self.db.add(job)
                await self.db.flush()
                # 派遣 Celery 任务
                from app.workers.tasks import parse_resume
                parse_resume.delay(str(job.id))

            # 关联职位：创建 screening_result（待筛选状态）
            if job_id:
                from app.models.screening import ScreeningResult
                existing_sr = await self.db.scalar(
                    select(ScreeningResult).where(
                        ScreeningResult.job_id == job_id,
                        ScreeningResult.candidate_id == candidate.id,
                    )
                )
                if existing_sr is None:
                    self.db.add(
                        ScreeningResult(
                            job_id=job_id,
                            candidate_id=candidate.id,
                            disqualified=False,
                            reasons=None,
                            manually_overridden=False,
                        )
                    )
                    await self.db.flush()

            return UploadConfirmResponseItem(
                upload_id=item.upload_id,
                resume_id=resume.id,
                candidate_id=candidate.id,
                status="ok",
            )
        except Exception:
            logger.exception("confirm_persist_failed", file_key=item.file_key)
            # 抛出去让外层事务回滚 —— DB 层错误不能 swallow 成 rejected
            raise


__all__ = ["FileUploadService"]
