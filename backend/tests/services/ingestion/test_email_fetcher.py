"""EmailFetcherService 单元测试（任务 11）。

测试策略：
- ``mailbox_factory`` 注入 fake：模拟 imap_tools.MailBox 接口
- ``storage`` 注入 fake：模拟 S3StorageAdapter.put
- 真实 DB（PostgreSQL）→ 验证 Candidate/Source/Resume/AsyncJob 写入
- 退避状态机：用 ``_compute_backoff_until`` + ``_compute_alert_level`` 直接断言
"""
from __future__ import annotations

import uuid
from contextlib import contextmanager
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any

import pytest
from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.async_job import AsyncJob
from app.models.candidate import Candidate, CandidateResume, CandidateSource
from app.models.email_config import EmailConfig
from app.models.team import Team
from app.services.ingestion.email_fetcher import (
    BACKOFF_SEQUENCE_SECONDS,
    EmailFetcherService,
    _compute_alert_level,
    _compute_backoff_until,
    fetch_all_active_configs,
)


# ============================================================================
# Fake IMAP（imap_tools 接口最小实现）
# ============================================================================


@dataclass
class FakeAttachment:
    filename: str
    payload: bytes


@dataclass
class FakeMessage:
    subject: str
    from_: str
    date: datetime
    date_str: str
    attachments: list[FakeAttachment] = field(default_factory=list)
    message_id: str = ""
    headers: dict[str, list[str]] = field(default_factory=dict)


class FakeMailboxLoginContext:
    """``mailbox.login()`` 返回的上下文管理器。"""

    def __init__(self, msgs: list[FakeMessage], raise_on_login: Exception | None = None):
        self._msgs = msgs
        self._raise = raise_on_login

    def __enter__(self) -> "FakeMailboxLoginContext":
        if self._raise is not None:
            raise self._raise
        return self

    def __exit__(self, *exc) -> None:  # noqa: ANN001
        return None

    def fetch(self, *_args, **_kwargs) -> list[FakeMessage]:
        return list(self._msgs)


class FakeMailbox:
    """模拟 imap_tools.MailBox（构造 + login 上下文）。"""

    def __init__(self, msgs: list[FakeMessage], raise_on_login: Exception | None = None):
        self._msgs = msgs
        self._raise = raise_on_login
        # 记录调用参数（测试可断言）
        self.last_host: str | None = None
        self.last_port: int | None = None

    def __call__(self, host: str, port: int) -> "FakeMailbox":
        self.last_host = host
        self.last_port = port
        return self

    def login(self, user: str, password: str) -> FakeMailboxLoginContext:  # noqa: ARG002
        return FakeMailboxLoginContext(self._msgs, raise_on_login=self._raise)


# ============================================================================
# Fake storage
# ============================================================================


class FakeStorage:
    def __init__(self) -> None:
        self.puts: list[tuple[str, bytes, str]] = []

    async def put(self, key: str, payload: bytes, *, mime: str, encrypt: bool = False) -> None:  # noqa: ARG002
        self.puts.append((key, payload, mime))


# ============================================================================
# 工具：构造测试用的 EmailConfig
# ============================================================================


_PDF = (
    b"%PDF-1.4\n"
    b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
    b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
    b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>endobj\n"
    b"xref\n0 4\n0000000000 65535 f \n"
    b"trailer<</Root 1 0 R/Size 4>>\nstartxref\n0\n%%EOF"
)

_PNG_SIGNATURE = b"\x89PNG\r\n\x1a\n" + b"\x00" * 64


async def _purge_db() -> None:
    from sqlalchemy import text

    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "TRUNCATE users, teams, team_invites, jobs, candidates, "
                "candidate_resumes, candidate_sources, parsed_structures, "
                "screening_results, scores, score_reasons, "
                "interview_questions, interview_feedbacks, dedup_matches, "
                "manual_overrides, llm_calls, async_jobs, audit_logs, "
                "email_configs, job_versions, job_hard_requirements "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def clean_db() -> None:
    await _purge_db()
    yield
    await _purge_db()


async def _make_config(*, enabled: bool = True, paused_until: datetime | None = None,
                       consecutive_failures: int = 0) -> tuple[EmailConfig, Team]:
    """创建一个 team + EmailConfig 并直接写库（绕过 service 层加密）。"""
    async with AsyncSessionLocal() as session:
        team = Team(name=f"team-{uuid.uuid4().hex[:8]}")
        session.add(team)
        await session.flush()
        cfg = EmailConfig(
            team_id=team.id,
            imap_host="imap.example.com",
            imap_port=993,
            username="box@example.com",
            password_enc="secret",  # EncryptedString 在 flush 时加密
            poll_interval_min=15,
            enabled=enabled,
            paused_until=paused_until,
            consecutive_failures=consecutive_failures,
        )
        session.add(cfg)
        await session.commit()
        await session.refresh(cfg)
        return cfg, team


def _make_msg(*, subject: str = "[简历] 申请",
              from_: str = "hr@example.com",
              message_id: str = "<m1@example.com>",
              attachments: list[FakeAttachment] | None = None,
              days_ago: int = 0) -> FakeMessage:
    msg_date = datetime.now(timezone.utc) - timedelta(days=days_ago)
    return FakeMessage(
        subject=subject,
        from_=from_,
        date=msg_date,
        date_str=msg_date.isoformat(),
        attachments=attachments or [],
        message_id=message_id,
        headers={"message-id": [message_id]} if message_id else {},
    )


# ============================================================================
# 退避状态机
# ============================================================================


def test_backoff_sequence_is_15_60_300_900_1800() -> None:
    assert BACKOFF_SEQUENCE_SECONDS == [15, 60, 300, 900, 1800]


def test_compute_backoff_until_returns_none_for_zero_failures() -> None:
    assert _compute_backoff_until(0) is None


def test_compute_backoff_until_uses_sequence_by_failure_count() -> None:
    # failures=1 → +15s, failures=2 → +60s ...
    for idx, expected_offset in enumerate(BACKOFF_SEQUENCE_SECONDS, start=1):
        until = _compute_backoff_until(idx)
        assert until is not None
        delta = (until - datetime.now(timezone.utc)).total_seconds()
        # 容忍 5s 抖动（测试运行时间）
        assert expected_offset - 5 <= delta <= expected_offset + 5


def test_compute_backoff_until_caps_at_max_for_failures_over_5() -> None:
    until = _compute_backoff_until(99)
    assert until is not None
    delta = (until - datetime.now(timezone.utc)).total_seconds()
    # 第 5 档是 1800s
    assert 1790 <= delta <= 1810


def test_compute_alert_level_thresholds() -> None:
    assert _compute_alert_level(0) == "none"
    assert _compute_alert_level(1) == "none"
    assert _compute_alert_level(2) == "warning"
    assert _compute_alert_level(4) == "warning"
    assert _compute_alert_level(5) == "critical"
    assert _compute_alert_level(99) == "critical"


# ============================================================================
# fetch_one 主流程
# ============================================================================


async def test_fetch_one_writes_candidate_source_resume_and_async_job() -> None:
    cfg, _team = await _make_config()
    msgs = [_make_msg(
        subject="应聘：张三的简历",
        attachments=[FakeAttachment("resume.pdf", _PDF)],
    )]
    storage = FakeStorage()
    service = EmailFetcherService(
        db=None,  # 占位，下面手动注入
        storage=storage,
        mailbox_factory=FakeMailbox(msgs),
    )

    # 注入真实 session
    async with AsyncSessionLocal() as session:
        service.db = session
        # 把 cfg 重新 attach 到本 session
        cfg = await session.merge(cfg)
        count = await service.fetch_one(cfg)
        await session.commit()

    assert count == 1
    assert len(storage.puts) == 1

    async with AsyncSessionLocal() as session:
        candidates = (await session.execute(select(Candidate))).scalars().all()
        sources = (await session.execute(select(CandidateSource))).scalars().all()
        resumes = (await session.execute(select(CandidateResume))).scalars().all()
        jobs = (await session.execute(select(AsyncJob))).scalars().all()

    assert len(candidates) == 1
    assert candidates[0].dedup_key.startswith(f"email:{cfg.id}:")
    assert candidates[0].email == "hr@example.com"

    assert len(sources) == 1
    assert sources[0].source_type == "email"
    assert sources[0].source_meta["message_id"] == "<m1@example.com>"
    assert sources[0].source_meta["sender"] == "hr@example.com"

    assert len(resumes) == 1
    assert resumes[0].parse_status == "pending"
    assert resumes[0].file_mime == "application/pdf"

    assert len(jobs) == 1
    assert jobs[0].task_type == "parse"
    assert jobs[0].status == "queued"
    assert jobs[0].idempotency_key == f"parse:{resumes[0].id}"


async def test_fetch_one_skipped_when_paused() -> None:
    paused = datetime.now(timezone.utc) + timedelta(minutes=10)
    cfg, _ = await _make_config(paused_until=paused)

    msgs = [_make_msg(attachments=[FakeAttachment("r.pdf", _PDF)])]
    service = EmailFetcherService(
        db=None, storage=FakeStorage(), mailbox_factory=FakeMailbox(msgs),
    )
    async with AsyncSessionLocal() as session:
        service.db = session
        cfg = await session.merge(cfg)
        count = await service.fetch_one(cfg)
        await session.commit()

    assert count == 0
    # IMAP 不应被调用（fetch 直接 return 0）


async def test_fetch_one_dedups_by_message_id_and_filename() -> None:
    """同一封邮件再次抓取不应重复写库。

    实际场景：beat 多次轮询、IMAP 还能取到上次已处理的邮件 → 应跳过。
    """
    cfg, _ = await _make_config()
    msgs = [_make_msg(
        subject="简历",
        attachments=[FakeAttachment("r.pdf", _PDF)],
    )]

    async with AsyncSessionLocal() as session:
        service = EmailFetcherService(
            db=session, storage=FakeStorage(),
            mailbox_factory=FakeMailbox(msgs),
        )
        cfg = await session.merge(cfg)
        first = await service.fetch_one(cfg)
        await session.commit()

    async with AsyncSessionLocal() as session:
        service = EmailFetcherService(
            db=session, storage=FakeStorage(),
            mailbox_factory=FakeMailbox(msgs),  # 同样的 msg
        )
        cfg = await session.merge(cfg)
        second = await service.fetch_one(cfg)
        await session.commit()

    assert first == 1
    assert second == 0  # 第二次 dedup_key 撞了，跳过

    async with AsyncSessionLocal() as session:
        candidates = (await session.execute(select(Candidate))).scalars().all()
    assert len(candidates) == 1


async def test_fetch_one_skips_non_resume_subjects() -> None:
    cfg, _ = await _make_config()
    # 没有 "简历"/"resume" 关键词
    msgs = [_make_msg(
        subject="周报 - 项目进度",
        attachments=[FakeAttachment("report.pdf", _PDF)],
    )]
    service = EmailFetcherService(
        db=None, storage=FakeStorage(), mailbox_factory=FakeMailbox(msgs),
    )
    async with AsyncSessionLocal() as session:
        service.db = session
        cfg = await session.merge(cfg)
        count = await service.fetch_one(cfg)
        await session.commit()

    assert count == 0


async def test_fetch_one_rejects_disallowed_extension() -> None:
    cfg, _ = await _make_config()
    msgs = [_make_msg(
        subject="简历",
        # .exe 扩展名不在白名单
        attachments=[FakeAttachment("resume.exe", b"MZ" + b"\x00" * 200)],
    )]
    service = EmailFetcherService(
        db=None, storage=FakeStorage(), mailbox_factory=FakeMailbox(msgs),
    )
    async with AsyncSessionLocal() as session:
        service.db = session
        cfg = await session.merge(cfg)
        count = await service.fetch_one(cfg)
        await session.commit()

    assert count == 0


async def test_fetch_one_rejects_mime_mismatch() -> None:
    """扩展名 .pdf 但内容是 PNG → 一致性检查拒绝。"""
    cfg, _ = await _make_config()
    msgs = [_make_msg(
        subject="简历",
        attachments=[FakeAttachment("resume.pdf", _PNG_SIGNATURE)],
    )]
    service = EmailFetcherService(
        db=None, storage=FakeStorage(), mailbox_factory=FakeMailbox(msgs),
    )
    async with AsyncSessionLocal() as session:
        service.db = session
        cfg = await session.merge(cfg)
        count = await service.fetch_one(cfg)
        await session.commit()

    assert count == 0


# ============================================================================
# 退避：失败 → 暂停 → 5 次后 critical
# ============================================================================


async def test_record_failure_sets_backoff_on_imap_login_error() -> None:
    cfg, _ = await _make_config()
    # IMAP 登录抛异常
    fake_mb = FakeMailbox(msgs=[], raise_on_login=ConnectionRefusedError("auth failed"))
    service = EmailFetcherService(
        db=None, storage=FakeStorage(), mailbox_factory=fake_mb,
    )
    async with AsyncSessionLocal() as session:
        service.db = session
        cfg = await session.merge(cfg)
        count = await service.fetch_one(cfg)
        await session.commit()

    assert count == 0
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EmailConfig).where(EmailConfig.id == cfg.id)
        )
        updated = result.scalar_one()
    assert updated.consecutive_failures == 1
    assert updated.alert_level == "none"  # 1 次失败不告警
    assert updated.paused_until is not None
    assert updated.last_error_summary and "ConnectionRefusedError" in updated.last_error_summary


async def test_record_failure_5_times_sets_critical_and_alert() -> None:
    """5 次连续失败应触发 critical alert。

    注意：每次失败 ``_record_failure`` 都会设置 ``paused_until``，
    下次 ``fetch_one`` 会被 paused 跳过；
    所以这里直接调 ``_record_failure`` 验证状态机而非走完整 fetch_one。
    """
    cfg, _ = await _make_config()
    exc = ConnectionRefusedError("auth failed")
    service = EmailFetcherService(
        db=None, storage=FakeStorage(),
        mailbox_factory=FakeMailbox([], raise_on_login=exc),
    )
    async with AsyncSessionLocal() as session:
        service.db = session
        cfg = await session.merge(cfg)
        for _ in range(5):
            # 清除 paused_until 模拟"冷却到期，beat 又来试一次"
            cfg.paused_until = None
            await service._record_failure(cfg, exc)
        await session.commit()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EmailConfig).where(EmailConfig.id == cfg.id)
        )
        updated = result.scalar_one()
    assert updated.consecutive_failures == 5
    assert updated.alert_level == "critical"
    assert updated.paused_until is not None


async def test_record_success_resets_failure_counters() -> None:
    """``_record_success`` 把所有退避字段重置为初始状态。"""
    cfg, _ = await _make_config(consecutive_failures=4)
    cfg.alert_level = "warning"
    cfg.paused_until = datetime.now(timezone.utc) + timedelta(seconds=60)
    cfg.last_error_summary = "previous error"

    service = EmailFetcherService(
        db=None, storage=FakeStorage(), mailbox_factory=FakeMailbox([]),
    )
    async with AsyncSessionLocal() as session:
        service.db = session
        cfg = await session.merge(cfg)
        await service._record_success(cfg)
        await session.commit()

    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EmailConfig).where(EmailConfig.id == cfg.id)
        )
        updated = result.scalar_one()
    assert updated.consecutive_failures == 0
    assert updated.alert_level == "none"
    assert updated.paused_until is None
    assert updated.last_error_summary is None


# ============================================================================
# fetch_all_active_configs
# ============================================================================


async def test_fetch_all_active_configs_skips_disabled() -> None:
    cfg_enabled, _ = await _make_config(enabled=True)
    cfg_disabled, _ = await _make_config(enabled=False)

    # 两个都给空邮件列表 → summary 应只包含 enabled
    async with AsyncSessionLocal() as session:
        # patch mailbox_factory 不可行（service 内部 import）
        # 用 monkey 思路：直接替换 EmailFetcherService 类方法
        original_collect = EmailFetcherService._collect_sync

        def stub(self, config):  # noqa: ANN001
            return ([], None)

        EmailFetcherService._collect_sync = stub  # type: ignore[assignment]
        try:
            summary = await fetch_all_active_configs(session)
        finally:
            EmailFetcherService._collect_sync = original_collect  # type: ignore[assignment]

    assert str(cfg_enabled.id) in summary
    assert str(cfg_disabled.id) not in summary
