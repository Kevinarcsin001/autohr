"""EmailFetcherService（任务 11）：IMAP 增量抓取 + 附件入库 + 退避。

架构：
- IMAP（imap_tools 同步）在 ``_fetch_sync`` 内完成，**只收集**简历附件到内存
  （payload + filename + sender + subject_preview），不写库
- 回到 async 层后，逐个调 ``_handle_attachment``（异步 storage.put + 写库 + 入队）
- 这样避免同步上下文里调异步 storage 的麻烦

流程：
1. beat 每 N 分钟调度 ``fetch_all_active_configs``
2. 对每个 enabled email_config：
   a. 校验 ``paused_until``；未到 → 跳过
   b. IMAP 连接 + 登录；失败 → ``_record_failure``（退避 + 告警）
   c. ``last_fetched_at`` 之后的新邮件，逐封：
      - 按 Message-ID 去重
      - 识别简历附件（扩展名 ∈ 白名单 ∩ 主题/文件名含 "简历"/"resume"）
      - 收集到 list
   d. 回到 async 层 → 逐个附件入库
   e. 成功 → ``_record_success`` 清除告警

退避序列：15s/60s/300s/15min/30min 共 5 次（spec 需求 5.4 + tasks.md:94）
"""
from __future__ import annotations

import asyncio
import re
import uuid
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Any

import magic
from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.adapters.storage import S3StorageAdapter, get_storage
from app.core.logging import get_logger
from app.models.async_job import AsyncJob
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
)
from app.models.email_config import EmailConfig

logger = get_logger(__name__)


# ============================================================================
# 常量
# ============================================================================


BACKOFF_SEQUENCE_SECONDS: list[int] = [15, 60, 300, 900, 1800]
"""[15s, 1min, 5min, 15min, 30min]"""
MAX_FAILURES_BEFORE_PAUSE: int = len(BACKOFF_SEQUENCE_SECONDS)

_ATTACHMENT_EXT = {".pdf", ".doc", ".docx", ".png", ".jpg", ".jpeg"}
_RESUME_KEYWORDS = {
    "简历",
    "resume",
    "cv",
    "curriculum",
    "求职",
    "candidate",
}
_ALLOWED_MIME = {
    "application/pdf",
    "application/msword",
    "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "image/png",
    "image/jpeg",
}
_EXT_TO_MIME = {
    "pdf": "application/pdf",
    "doc": "application/msword",
    "docx": "application/vnd.openxmlformats-officedocument.wordprocessingml.document",
    "png": "image/png",
    "jpg": "image/jpeg",
    "jpeg": "image/jpeg",
}


# ============================================================================
# 异常
# ============================================================================


class EmailFetcherError(Exception):
    """IMAP 连接 / 认证 / 抓取错误（用于退避计数）。"""


# ============================================================================
# 退避状态机
# ============================================================================


def _now() -> datetime:
    return datetime.now(timezone.utc)


def _compute_backoff_until(failures: int) -> datetime | None:
    """根据当前连续失败次数，返回下次可重试时间。

    failures=1 → +15s
    failures=2 → +60s
    failures=3 → +300s
    failures=4 → +900s
    failures=5 → +1800s（同时触发 alert=critical）
    failures>5 → 持续 paused（不自动恢复，需管理员 clear_alert）
    """
    if failures == 0:
        return None
    idx = min(failures, len(BACKOFF_SEQUENCE_SECONDS)) - 1
    return _now() + timedelta(seconds=BACKOFF_SEQUENCE_SECONDS[idx])


def _compute_alert_level(failures: int) -> str:
    if failures >= MAX_FAILURES_BEFORE_PAUSE:
        return "critical"
    if failures >= 2:
        return "warning"
    return "none"


def _summarize_error(exc: Exception) -> str:
    """把异常转简短摘要（不记邮件正文 / 密码）。"""
    msg = type(exc).__name__
    detail = str(exc).strip()
    if detail:
        detail = re.sub(r"\s+", " ", detail)[:200]
        msg = f"{msg}: {detail}"
    return msg


# ============================================================================
# 附件收集结构
# ============================================================================


@dataclass(frozen=True)
class CollectedAttachment:
    """同步 IMAP 阶段收集的附件元信息（payload 已读入内存）。"""

    message_id: str
    sender: str
    subject_preview: str  # ≤100 字符；不含完整正文
    filename: str
    payload: bytes
    real_mime: str


# ============================================================================
# EmailFetcherService
# ============================================================================


class EmailFetcherService:
    """IMAP 增量抓取服务。"""

    def __init__(
        self,
        db: AsyncSession,
        storage: S3StorageAdapter | None = None,
        mailbox_factory: Any = None,
    ) -> None:
        self.db = db
        self.storage = storage or get_storage()
        # 默认 imap_tools.MailBox；测试可注入 fake（同接口）
        if mailbox_factory is None:
            from imap_tools import MailBox

            mailbox_factory = MailBox
        self._mailbox_factory = mailbox_factory

    # ----- 主入口 -----

    async def fetch_one(self, config: EmailConfig) -> int:
        """抓取单个 email_config 的所有新邮件。

        Returns:
            新入库的简历附件数

        失败时记录退避状态，不抛出（调用方拿 0 + 状态）。
        """
        if config.paused_until is not None and config.paused_until > _now():
            logger.info(
                "email_fetch_skipped_paused",
                config_id=str(config.id),
                paused_until=config.paused_until.isoformat(),
            )
            return 0

        # 同步阶段：连接 IMAP + 收集附件
        try:
            collected, latest_date = await asyncio.to_thread(
                self._collect_sync, config
            )
        except Exception as exc:  # noqa: BLE001
            await self._record_failure(config, exc)
            logger.warning(
                "email_fetch_failed",
                config_id=str(config.id),
                error=_summarize_error(exc),
            )
            return 0

        # 异步阶段：逐个入库
        new_count = 0
        for att in collected:
            try:
                ok = await self._handle_attachment(config, att)
                if ok:
                    new_count += 1
            except Exception:  # noqa: BLE001
                # 单个附件失败不影响整批
                logger.exception(
                    "email_attachment_persist_failed",
                    filename=att.filename,
                )

        # 更新 last_fetched_at（成功完成才更新）
        if latest_date is not None:
            config.last_fetched_at = latest_date

        await self._record_success(config)
        return new_count

    # ----- 同步阶段：IMAP -----

    def _collect_sync(
        self, config: EmailConfig
    ) -> tuple[list[CollectedAttachment], datetime | None]:
        """连接 IMAP → 抓取新邮件 → 收集简历附件。

        Returns:
            (附件列表, 最近邮件时间)
        """
        from imap_tools import AND  # 局部 import 避免模块顶层依赖

        with self._mailbox_factory(config.imap_host, config.imap_port).login(
            config.username, config.password_enc
        ) as mailbox:
            since_date = (
                config.last_fetched_at or _now() - timedelta(days=30)
            ).date()
            collected: list[CollectedAttachment] = []
            latest_date: datetime | None = None

            for msg in mailbox.fetch(
                AND(date_gte=since_date), reverse=True, limit=200, mark_seen=True
            ):
                message_id = self._extract_message_id(msg)
                # 邮件正文永远不进日志；subject 截断
                subject_preview = (msg.subject or "")[:100]
                sender = (msg.from_ or "").lower()

                latest_date = self._maybe_advance(latest_date, msg.date)

                if not self._looks_like_resume(msg):
                    continue

                for att in msg.attachments:
                    payload = att.payload or b""
                    if not payload or not att.filename:
                        continue
                    ext = (
                        "." + att.filename.rsplit(".", 1)[-1].lower()
                        if "." in att.filename
                        else ""
                    )
                    if ext not in _ATTACHMENT_EXT:
                        continue
                    try:
                        real_mime = magic.from_buffer(payload, mime=True)
                    except Exception:  # noqa: BLE001
                        continue
                    if real_mime not in _ALLOWED_MIME:
                        continue
                    expected = _EXT_TO_MIME.get(ext.lstrip("."))
                    if expected and real_mime != expected:
                        continue
                    collected.append(
                        CollectedAttachment(
                            message_id=message_id,
                            sender=sender,
                            subject_preview=subject_preview,
                            filename=att.filename,
                            payload=payload,
                            real_mime=real_mime,
                        )
                    )

            return collected, latest_date

    def _extract_message_id(self, msg: Any) -> str:
        """从邮件头取 Message-ID；缺失则合成稳定伪 ID。"""
        raw = ""
        try:
            raw = (msg.headers.get("message-id") or [""])[0].strip()
        except Exception:  # noqa: BLE001
            raw = ""
        if raw:
            return raw
        # 合成伪 ID（不依赖邮件正文）
        return f"synthetic:{abs(hash((msg.subject, msg.date_str))) & 0xFFFFFFFFffffffff:x}"

    def _maybe_advance(
        self, latest: datetime | None, msg_date: datetime | None
    ) -> datetime | None:
        if msg_date is None:
            return latest
        if msg_date.tzinfo is None:
            msg_date = msg_date.replace(tzinfo=timezone.utc)
        if latest is None or msg_date > latest:
            return msg_date
        return latest

    def _looks_like_resume(self, msg: Any) -> bool:
        """主题 + 附件名含简历关键词。"""
        haystack_parts: list[str] = []
        if msg.subject:
            haystack_parts.append(msg.subject.lower())
        for att in msg.attachments:
            if att.filename:
                haystack_parts.append(att.filename.lower())
        haystack = " ".join(haystack_parts)
        return any(kw in haystack for kw in _RESUME_KEYWORDS)

    # ----- 异步阶段：入库 -----

    async def _handle_attachment(
        self, config: EmailConfig, att: CollectedAttachment
    ) -> bool:
        """单个附件入库：storage.put + 写 Candidate/Source/Resume + 入 async_jobs。"""
        ext = (
            att.filename.rsplit(".", 1)[-1].lower()
            if "." in att.filename
            else "bin"
        )
        file_key = f"{config.team_id}/{uuid.uuid4()}/{uuid.uuid4()}.{ext}"
        try:
            await self.storage.put(
                file_key, att.payload, mime=att.real_mime, encrypt=True
            )
        except Exception:  # noqa: BLE001
            logger.exception(
                "email_attachment_storage_failed",
                filename=att.filename,
            )
            return False

        # TODO(task-14): 真实 dedup_key 由 dedup service 接管
        # 注意：dedup_key 不能含 file_key（每次 PUT 都是新 uuid），
        # 否则 IMAP 重抓同一封邮件会重复入库
        dedup_key = f"email:{config.id}:{att.message_id}:{att.filename}"
        existing = await self.db.scalar(
            select(Candidate).where(Candidate.dedup_key == dedup_key)
        )
        if existing is not None:
            return False

        candidate = Candidate(
            team_id=config.team_id,
            dedup_key=dedup_key,
            # 发件人邮箱作为 name 占位（任务 14 解析后用真实姓名覆盖）
            name=att.sender or att.filename,
            phone=None,
            email=att.sender or None,
        )
        self.db.add(candidate)
        await self.db.flush()

        source = CandidateSource(
            candidate_id=candidate.id,
            source_type="email",
            source_meta={
                "email_config_id": str(config.id),
                "message_id": att.message_id,
                "sender": att.sender,
                "subject_preview": att.subject_preview,
                "attachment_name": att.filename,
            },
        )
        self.db.add(source)
        await self.db.flush()

        resume = CandidateResume(
            candidate_id=candidate.id,
            source_id=source.id,
            file_storage_key=file_key,
            file_mime=att.real_mime,
            parse_status="pending",
        )
        self.db.add(resume)
        await self.db.flush()

        idem = f"parse:{resume.id}"
        existing_job = await self.db.scalar(
            select(AsyncJob).where(AsyncJob.idempotency_key == idem)
        )
        if existing_job is None:
            self.db.add(
                AsyncJob(
                    task_type="parse",
                    target_id=resume.id,
                    status="queued",
                    idempotency_key=idem,
                    payload={
                        "file_key": file_key,
                        "mime": att.real_mime,
                        "source": "email",
                    },
                )
            )
            await self.db.flush()
        return True

    # ----- 退避状态 -----

    async def _record_failure(self, config: EmailConfig, exc: Exception) -> None:
        config.consecutive_failures += 1
        config.last_error_summary = _summarize_error(exc)
        config.alert_level = _compute_alert_level(config.consecutive_failures)
        config.paused_until = _compute_backoff_until(config.consecutive_failures)
        await self.db.flush()
        logger.warning(
            "email_backoff_recorded",
            config_id=str(config.id),
            failures=config.consecutive_failures,
            alert=config.alert_level,
            paused_until=config.paused_until.isoformat()
            if config.paused_until
            else None,
        )

    async def _record_success(self, config: EmailConfig) -> None:
        if config.consecutive_failures > 0:
            config.consecutive_failures = 0
        config.last_error_summary = None
        config.alert_level = "none"
        config.paused_until = None
        await self.db.flush()


# ============================================================================
# Beat 入口（任务 12 接线 Celery 后改为 celery task）
# ============================================================================


async def fetch_all_active_configs(db: AsyncSession) -> dict[str, int]:
    """扫描所有 enabled + 未暂停的 email_config，逐个调 fetch_one。

    TODO(task-12): 替换为 celery beat 任务
    """
    result = await db.execute(
        select(EmailConfig).where(EmailConfig.enabled.is_(True))
    )
    configs = result.scalars().all()
    service = EmailFetcherService(db)
    summary: dict[str, int] = {}
    for cfg in configs:
        n = await service.fetch_one(cfg)
        summary[str(cfg.id)] = n
    await db.commit()
    return summary


__all__ = [
    "EmailFetcherService",
    "EmailFetcherError",
    "CollectedAttachment",
    "fetch_all_active_configs",
    "BACKOFF_SEQUENCE_SECONDS",
    "MAX_FAILURES_BEFORE_PAUSE",
    "_compute_backoff_until",
    "_compute_alert_level",
]
