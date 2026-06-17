"""FileUploadService 集成测试（任务 9）。

覆盖：
- intent 阶段：合法/超限/扩展名非法/批量超限
- confirm 阶段：合法上传/ MIME 不匹配/对象缺失/跨 team/幂等入队
- DB 副作用：Candidate + Source + Resume + AsyncJob 4 行写入
"""
from __future__ import annotations

import uuid
from typing import Any

import httpx
import pytest
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.models.async_job import AsyncJob
from app.models.candidate import Candidate, CandidateResume, CandidateSource
from app.models.team import Team
from app.models.user import User
from app.schemas.upload import (
    UploadConfirmItem,
    UploadIntentItem,
)
from app.services.ingestion.file_upload import FileUploadService


# ============================================================================
# Fixtures
# ============================================================================


async def _purge_all() -> None:
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
async def clean_db():
    await _purge_all()
    yield
    await _purge_all()


async def _make_team_and_admin() -> tuple[uuid.UUID, uuid.UUID]:
    """直接 INSERT team + user，绕过 register API。返回 (team_id, user_id)。"""
    async with AsyncSessionLocal() as session:
        team = Team(name=f"team-{uuid.uuid4().hex[:8]}")
        session.add(team)
        await session.flush()
        user = User(
            email=f"u{uuid.uuid4().hex[:8]}@x.com",
            password_hash="x",
            name="U",
            team_id=team.id,
            role="admin",
        )
        session.add(user)
        await session.commit()
        return team.id, user.id


def _pdf_bytes() -> bytes:
    """最小合法 PDF（magic 嗅探会识别为 application/pdf）。"""
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"trailer<</Root 1 0 R/Size 4>>\nstartxref\n0\n%%EOF"
    )


def _png_bytes() -> bytes:
    """最小合法 PNG（magic 嗅探会识别为 image/png）。"""
    return (
        b"\x89PNG\r\n\x1a\n"
        b"\x00\x00\x00\rIHDR\x00\x00\x00\x01\x00\x00\x00\x01\x08\x06\x00\x00\x00"
        b"\x1f\x15\xc4\x89"
        b"\x00\x00\x00\nIDATx\x9cc\x00\x01\x00\x00\x05\x00\x01\r\n-\xb4"
        b"\x00\x00\x00\x00IEND\xaeB`\x82"
    )


# ============================================================================
# intent
# ============================================================================


async def test_intent_ok_returns_signed_urls() -> None:
    team_id, _ = await _make_team_and_admin()
    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        items = await service.create_intent(
            team_id=team_id,
            files=[
                UploadIntentItem(
                    filename=f"a{i}.pdf", size_bytes=1024, mime_client="application/pdf"
                )
                for i in range(3)
            ],
        )
    assert len(items) == 3
    assert all(i.status == "ok" for i in items)
    assert all(i.signed_url and i.signed_url.startswith("http") for i in items)
    assert all(i.file_key.startswith(f"{team_id}/") for i in items)
    assert all(i.file_key.endswith(".pdf") for i in items)


async def test_intent_oversize_rejected() -> None:
    team_id, _ = await _make_team_and_admin()
    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        items = await service.create_intent(
            team_id=team_id,
            files=[
                UploadIntentItem(
                    filename="big.pdf",
                    size_bytes=21 * 1024 * 1024,  # 21 MB > 20 MB
                    mime_client="application/pdf",
                ),
                UploadIntentItem(
                    filename="ok.pdf",
                    size_bytes=1024,
                    mime_client="application/pdf",
                ),
            ],
        )
    statuses = {i.filename: i.status for i in items}
    assert statuses["big.pdf"] == "rejected"
    assert statuses["ok.pdf"] == "ok"
    big = next(i for i in items if i.filename == "big.pdf")
    assert big.reject_reason == "size_exceeded"


async def test_intent_zero_size_rejected() -> None:
    team_id, _ = await _make_team_and_admin()
    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        items = await service.create_intent(
            team_id=team_id,
            files=[
                UploadIntentItem(
                    filename="empty.pdf", size_bytes=0, mime_client="application/pdf"
                )
            ],
        )
    assert items[0].status == "rejected"
    assert items[0].reject_reason == "size_exceeded"


async def test_intent_wrong_extension_rejected() -> None:
    team_id, _ = await _make_team_and_admin()
    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        items = await service.create_intent(
            team_id=team_id,
            files=[
                UploadIntentItem(
                    filename="malware.exe",
                    size_bytes=1024,
                    mime_client="application/octet-stream",
                )
            ],
        )
    assert items[0].status == "rejected"
    assert items[0].reject_reason == "extension_not_allowed"


async def test_intent_batch_too_large_raises() -> None:
    team_id, _ = await _make_team_and_admin()
    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        with pytest.raises(ValueError, match="超出上限"):
            await service.create_intent(
                team_id=team_id,
                files=[
                    UploadIntentItem(
                        filename=f"x{i}.pdf", size_bytes=100, mime_client="application/pdf"
                    )
                    for i in range(101)
                ],
            )


# ============================================================================
# confirm：合法上传全链路
# ============================================================================


async def test_confirm_ok_writes_db_and_enqueues() -> None:
    team_id, _ = await _make_team_and_admin()
    pdf = _pdf_bytes()
    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        intent = await service.create_intent(
            team_id=team_id,
            files=[
                UploadIntentItem(
                    filename="resume.pdf",
                    size_bytes=len(pdf),
                    mime_client="application/pdf",
                )
            ],
        )
        file_key = intent[0].file_key
        upload_id = intent[0].upload_id
        signed_url = intent[0].signed_url
        assert signed_url is not None

    # 客户端 PUT 直传 MinIO
    async with httpx.AsyncClient() as client:
        resp = await client.put(
            signed_url,
            content=pdf,
            headers={"Content-Type": "application/pdf"},
        )
    assert resp.status_code == 200

    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        result = await service.confirm_uploads(
            team_id=team_id,
            items=[UploadConfirmItem(upload_id=upload_id, file_key=file_key)],
        )
        await db.commit()

    assert len(result) == 1
    assert result[0].status == "ok"
    assert result[0].resume_id is not None

    # DB 校验：4 行写入
    async with AsyncSessionLocal() as session:
        cands = (await session.execute(select(Candidate))).scalars().all()
        srcs = (await session.execute(select(CandidateSource))).scalars().all()
        resumes = (await session.execute(select(CandidateResume))).scalars().all()
        jobs = (
            await session.execute(select(AsyncJob).where(AsyncJob.task_type == "parse"))
        ).scalars().all()

    assert len(cands) == 1
    assert len(srcs) == 1
    assert len(resumes) == 1
    assert len(jobs) == 1
    assert resumes[0].file_mime == "application/pdf"
    assert resumes[0].parse_status == "pending"
    assert jobs[0].status == "queued"
    assert jobs[0].idempotency_key == f"parse:{resumes[0].id}"


# ============================================================================
# confirm：MIME 嗅探拦截伪装文件
# ============================================================================


async def test_confirm_mime_mismatch_rejected() -> None:
    """扩展名 .pdf 但内容是 PNG → 嗅探拒绝。"""
    team_id, _ = await _make_team_and_admin()
    png = _png_bytes()
    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        intent = await service.create_intent(
            team_id=team_id,
            files=[
                UploadIntentItem(
                    filename="fake.pdf",
                    size_bytes=len(png),
                    mime_client="application/pdf",
                )
            ],
        )
        file_key = intent[0].file_key
        signed_url = intent[0].signed_url
        upload_id = intent[0].upload_id

    async with httpx.AsyncClient() as client:
        await client.put(signed_url, content=png)

    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        result = await service.confirm_uploads(
            team_id=team_id,
            items=[UploadConfirmItem(upload_id=upload_id, file_key=file_key)],
        )
        await db.commit()

    assert result[0].status == "rejected"
    assert result[0].reject_reason == "mime_mismatch"

    # 不应写库
    async with AsyncSessionLocal() as session:
        cands = (await session.execute(select(Candidate))).scalars().all()
    assert len(cands) == 0


# ============================================================================
# confirm：对象缺失
# ============================================================================


async def test_confirm_object_missing_rejected() -> None:
    """未 PUT 直传就 confirm → 对象不存在。"""
    team_id, _ = await _make_team_and_admin()
    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        intent = await service.create_intent(
            team_id=team_id,
            files=[
                UploadIntentItem(
                    filename="ghost.pdf",
                    size_bytes=1024,
                    mime_client="application/pdf",
                )
            ],
        )
        # 不做 PUT，直接 confirm
        result = await service.confirm_uploads(
            team_id=team_id,
            items=[
                UploadConfirmItem(
                    upload_id=intent[0].upload_id, file_key=intent[0].file_key
                )
            ],
        )
        await db.commit()

    assert result[0].status == "rejected"
    assert result[0].reject_reason == "object_missing"


# ============================================================================
# confirm：跨 team 访问
# ============================================================================


async def test_confirm_cross_team_rejected() -> None:
    team_a, _ = await _make_team_and_admin()
    team_b, _ = await _make_team_and_admin()
    pdf = _pdf_bytes()
    # team_a 上传
    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        intent = await service.create_intent(
            team_id=team_a,
            files=[
                UploadIntentItem(
                    filename="r.pdf",
                    size_bytes=len(pdf),
                    mime_client="application/pdf",
                )
            ],
        )
        file_key = intent[0].file_key
        signed_url = intent[0].signed_url
        upload_id = intent[0].upload_id

    async with httpx.AsyncClient() as client:
        await client.put(signed_url, content=pdf)

    # team_b 试图 confirm team_a 的 file_key
    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        result = await service.confirm_uploads(
            team_id=team_b,
            items=[UploadConfirmItem(upload_id=upload_id, file_key=file_key)],
        )
        await db.commit()

    assert result[0].status == "rejected"
    assert result[0].reject_reason == "cross_team"


# ============================================================================
# confirm：部分批次失败不阻塞
# ============================================================================


async def test_confirm_partial_batch_ok() -> None:
    """3 个文件：1 个伪装 PNG，2 个合法 PDF —— 合法的应正常写库。"""
    team_id, _ = await _make_team_and_admin()
    pdf = _pdf_bytes()
    png = _png_bytes()
    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        intent = await service.create_intent(
            team_id=team_id,
            files=[
                UploadIntentItem(
                    filename=f"r{i}.pdf",
                    size_bytes=len(pdf),
                    mime_client="application/pdf",
                )
                for i in range(2)
            ]
            + [
                UploadIntentItem(
                    filename="fake.pdf",
                    size_bytes=len(png),
                    mime_client="application/pdf",
                )
            ],
        )
        # 客户端 PUT 三个
        for i, payload in enumerate([pdf, pdf, png]):
            async with httpx.AsyncClient() as client:
                resp = await client.put(intent[i].signed_url, content=payload)
            assert resp.status_code == 200

        result = await service.confirm_uploads(
            team_id=team_id,
            items=[
                UploadConfirmItem(
                    upload_id=it.upload_id, file_key=it.file_key
                )
                for it in intent
            ],
        )
        await db.commit()

    statuses = [r.status for r in result]
    assert statuses.count("ok") == 2
    assert statuses.count("rejected") == 1


# ============================================================================
# confirm：幂等入队
# ============================================================================


async def test_confirm_idempotent_enqueue() -> None:
    """同一 file_key 重复 confirm：第二次返回 duplicate_enqueue，不重复入队。"""
    team_id, _ = await _make_team_and_admin()
    pdf = _pdf_bytes()
    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        intent = await service.create_intent(
            team_id=team_id,
            files=[
                UploadIntentItem(
                    filename="r.pdf",
                    size_bytes=len(pdf),
                    mime_client="application/pdf",
                )
            ],
        )
        file_key = intent[0].file_key
        upload_id = intent[0].upload_id

    async with httpx.AsyncClient() as client:
        await client.put(intent[0].signed_url, content=pdf)

    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        first = await service.confirm_uploads(
            team_id=team_id,
            items=[UploadConfirmItem(upload_id=upload_id, file_key=file_key)],
        )
        await db.commit()
    assert first[0].status == "ok"

    async with AsyncSessionLocal() as db:
        service = FileUploadService(db)
        second = await service.confirm_uploads(
            team_id=team_id,
            items=[UploadConfirmItem(upload_id=upload_id, file_key=file_key)],
        )
        await db.commit()
    assert second[0].status == "rejected"
    assert second[0].reject_reason == "duplicate_enqueue"

    # 仅 1 个 AsyncJob
    async with AsyncSessionLocal() as session:
        jobs = (
            await session.execute(select(AsyncJob).where(AsyncJob.task_type == "parse"))
        ).scalars().all()
    assert len(jobs) == 1
