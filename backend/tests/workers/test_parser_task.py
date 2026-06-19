"""``run_parse`` 集成测试（任务 13）。

覆盖：
- 正常 PDF 解析 → DB 写 parsed_text + parse_status=success
- low_text → parse_status=low_text
- failed → parse_status=failed + parse_error
- 损坏文件 → parse_status=failed（不重试）
- ResumeNotFound（target_id 不存在）→ raise（上层 mark_failed）
- StorageObjectMissing（MinIO 无文件）→ raise
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.models.async_job import AsyncJob
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
)
from app.models.team import Team
from app.services.parser import ParserService
from app.services.parser.ocr import OCRAdapter
from app.workers.parser_task import (
    ResumeNotFound,
    StorageObjectMissing,
    run_parse,
)

# ============================================================================
# Fake
# ============================================================================


class _FakeStorage:
    def __init__(self, *, returns: bytes | Exception = b"") -> None:
        self._returns = returns
        self.calls: list[str] = []

    async def get(self, key: str) -> bytes:
        self.calls.append(key)
        if isinstance(self._returns, Exception):
            raise self._returns
        return self._returns


class _FakeOCR(OCRAdapter):
    def __init__(self, returns: str) -> None:
        self._returns = returns

    @property
    def backend_name(self) -> str:
        return "fake"

    async def extract(self, image_bytes: bytes, *, langs: tuple[str, ...]) -> str:
        return self._returns


# ============================================================================
# Fixture：构造一个 PDF / scanned PDF / 损坏字节
# ============================================================================


def _make_text_pdf(text: str) -> bytes:
    from fpdf import FPDF

    pdf = FPDF()
    pdf.set_font("helvetica", size=12)
    pdf.add_page()
    for i in range(0, len(text), 80):
        pdf.cell(0, 10, text[i : i + 80])
        pdf.ln(5)
    return bytes(pdf.output())


def _make_scanned_pdf() -> bytes:
    import io

    from PIL import Image

    img = Image.new("RGB", (200, 300), color="white")
    buf = io.BytesIO()
    img.save(buf, format="PDF")
    return buf.getvalue()


# ============================================================================
# DB 清理
# ============================================================================


async def _purge_db() -> None:
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


async def _make_resume_in_db() -> tuple[str, CandidateResume]:
    """创建 team + candidate + source + resume 行；返回 (file_key, resume)。"""
    async with AsyncSessionLocal() as session:
        team = Team(name=f"team-{uuid.uuid4().hex[:8]}")
        session.add(team)
        await session.flush()

        candidate = Candidate(
            team_id=team.id,
            dedup_key=f"test:{uuid.uuid4()}",
            name="Test Candidate",
            email="test@example.com",
        )
        session.add(candidate)
        await session.flush()

        source = CandidateSource(
            candidate_id=candidate.id,
            source_type="upload",
        )
        session.add(source)
        await session.flush()

        file_key = f"{team.id}/{uuid.uuid4()}/resume.pdf"
        resume = CandidateResume(
            candidate_id=candidate.id,
            source_id=source.id,
            file_storage_key=file_key,
            file_mime="application/pdf",
            parse_status="pending",
        )
        session.add(resume)
        await session.commit()
        await session.refresh(resume)
        return file_key, resume


# ============================================================================
# 测试
# ============================================================================


class TestRunParse:
    async def test_successful_parse_writes_parsed_text_and_success_status(self) -> None:
        file_key, resume = await _make_resume_in_db()
        # 文本层足够 + 密度 OK
        pdf = _make_text_pdf("Hello world candidate profile " * 10)

        async with AsyncSessionLocal() as session:
            summary = await run_parse(
                db=session,
                storage=_FakeStorage(returns=pdf),
                target_id=resume.id,
                payload={"file_key": file_key, "mime": "application/pdf"},
                parser=ParserService(ocr=_FakeOCR("ignored")),
            )
            await session.commit()

        assert summary["status"] == "success"
        assert summary["text_len"] > 0

        async with AsyncSessionLocal() as session:
            updated = await session.get(CandidateResume, resume.id)
        assert updated.parsed_text is not None
        assert updated.parse_status == "success"
        assert updated.parse_error is None

    async def test_low_text_status_persisted(self) -> None:
        file_key, resume = await _make_resume_in_db()
        # scanned PDF + fake OCR 返回短文本 → low_text
        scanned = _make_scanned_pdf()
        fake_ocr = _FakeOCR("very short")  # 10 chars

        async with AsyncSessionLocal() as session:
            await run_parse(
                db=session,
                storage=_FakeStorage(returns=scanned),
                target_id=resume.id,
                payload={"file_key": file_key, "mime": "application/pdf"},
                parser=ParserService(ocr=fake_ocr),
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            updated = await session.get(CandidateResume, resume.id)
        assert updated.parse_status == "low_text"
        assert updated.parsed_text == "very short"

    async def test_failed_status_persisted_on_corrupt_input(self) -> None:
        file_key, resume = await _make_resume_in_db()

        async with AsyncSessionLocal() as session:
            await run_parse(
                db=session,
                storage=_FakeStorage(returns=b"not a real pdf"),
                target_id=resume.id,
                payload={"file_key": file_key, "mime": "application/pdf"},
                parser=ParserService(ocr=_FakeOCR("ignored")),
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            updated = await session.get(CandidateResume, resume.id)
        assert updated.parse_status == "failed"
        assert updated.parse_error is not None
        assert updated.parsed_text is None

    async def test_resume_not_found_raises(self) -> None:
        with pytest.raises(ResumeNotFound):
            async with AsyncSessionLocal() as session:
                await run_parse(
                    db=session,
                    storage=_FakeStorage(returns=b"x"),
                    target_id=uuid.uuid4(),  # 不存在
                    payload={"file_key": "k", "mime": "application/pdf"},
                    parser=ParserService(ocr=_FakeOCR("ignored")),
                )

    async def test_storage_missing_raises(self) -> None:
        file_key, resume = await _make_resume_in_db()
        broken_storage = _FakeStorage(returns=FileNotFoundError("not in minio"))

        with pytest.raises(StorageObjectMissing):
            async with AsyncSessionLocal() as session:
                await run_parse(
                    db=session,
                    storage=broken_storage,
                    target_id=resume.id,
                    payload={"file_key": file_key, "mime": "application/pdf"},
                    parser=ParserService(ocr=_FakeOCR("ignored")),
                )

    async def test_missing_payload_raises_value_error(self) -> None:
        file_key, resume = await _make_resume_in_db()

        with pytest.raises(ValueError):
            async with AsyncSessionLocal() as session:
                await run_parse(
                    db=session,
                    storage=_FakeStorage(returns=b""),
                    target_id=resume.id,
                    payload=None,
                    parser=ParserService(ocr=_FakeOCR("ignored")),
                )

    async def test_missing_file_key_in_payload_raises(self) -> None:
        file_key, resume = await _make_resume_in_db()

        with pytest.raises(ValueError):
            async with AsyncSessionLocal() as session:
                await run_parse(
                    db=session,
                    storage=_FakeStorage(returns=b""),
                    target_id=resume.id,
                    payload={"mime": "application/pdf"},  # 缺 file_key
                    parser=ParserService(ocr=_FakeOCR("ignored")),
                )
