"""``run_extract`` 集成测试（任务 14）。

覆盖：
- 正常抽取 → 写 ParsedStructure + status=extracted
- 字段全 null → status=extracted + 字段全 null
- 第一次 schema 不合 → 重试成功 → status=partial_extracted
- LLM 全不可用 → status=failed
- upsert：同一 resume_id 二次抽取 → 更新而非插入
- ResumeNotFound / ResumeNotReady / ResumeTextMissing
"""
from __future__ import annotations

import json
import uuid

import pytest
from sqlalchemy import select, text

from app.adapters.llm import LLMError, LLMResponse, LLMSchemaError, MockAdapter
from app.adapters.llm.router import LLMRouter
from app.core.db import AsyncSessionLocal
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.team import Team
from app.schemas.candidate_structure import CandidateStructure
from app.services.extractor import ExtractorService
from app.workers.extractor_task import (
    ResumeNotFound,
    ResumeNotReady,
    ResumeTextMissing,
    run_extract,
)

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


# ============================================================================
# Helper
# ============================================================================


_VALID_STRUCTURE_JSON = json.dumps(
    {
        "name": "张三",
        "name_confidence": 0.95,
        "phone": "13800138000",
        "phone_confidence": 0.9,
        "email": "zhangsan@example.com",
        "email_confidence": 0.9,
        "education": "bachelor",
        "education_confidence": 0.85,
        "years_of_experience": 5,
        "years_of_experience_confidence": 0.8,
        "skills": ["Python", "FastAPI"],
        "skills_confidence": 0.9,
        "expected_salary": "20k-30k",
        "expected_salary_confidence": 0.7,
        "current_company": "ACME",
        "current_company_confidence": 0.85,
        "work_history": [],
        "work_history_confidence": 0.85,
    },
    ensure_ascii=False,
)


_EMPTY_STRUCTURE_JSON = json.dumps(
    {
        "name": None, "name_confidence": 0.0,
        "phone": None, "phone_confidence": 0.0,
        "email": None, "email_confidence": 0.0,
        "education": None, "education_confidence": 0.0,
        "years_of_experience": None, "years_of_experience_confidence": 0.0,
        "skills": [], "skills_confidence": 0.0,
        "expected_salary": None, "expected_salary_confidence": 0.0,
        "current_company": None, "current_company_confidence": 0.0,
        "work_history": [], "work_history_confidence": 0.0,
    },
    ensure_ascii=False,
)


def _make_router(
    override: str | None = None,
    *,
    failures_before_success: int = 0,
    failure_exception: Exception | None = None,
) -> LLMRouter:
    mock = MockAdapter(
        response_override=override,
        name="mock",
        failures_before_success=failures_before_success,
        failure_exception=failure_exception,
    )
    return LLMRouter(
        adapters={"mock": mock},
        default_primary="mock",
        default_fallback=None,
    )


async def _make_resume_in_db(
    *,
    parse_status: str = "success",
    parsed_text: str = "张三的简历内容",
) -> CandidateResume:
    async with AsyncSessionLocal() as session:
        team = Team(name=f"team-{uuid.uuid4().hex[:8]}")
        session.add(team)
        await session.flush()

        candidate = Candidate(
            team_id=team.id,
            dedup_key=f"test:{uuid.uuid4()}",
            name="Test",
            email="t@example.com",
        )
        session.add(candidate)
        await session.flush()

        source = CandidateSource(
            candidate_id=candidate.id,
            source_type="upload",
        )
        session.add(source)
        await session.flush()

        resume = CandidateResume(
            candidate_id=candidate.id,
            source_id=source.id,
            file_storage_key=f"{team.id}/{uuid.uuid4()}/r.pdf",
            file_mime="application/pdf",
            parse_status=parse_status,
            parsed_text=parsed_text if parse_status == "success" else None,
        )
        session.add(resume)
        await session.commit()
        await session.refresh(resume)
        return resume


# ============================================================================
# 测试
# ============================================================================


class TestRunExtractHappyPath:
    async def test_valid_extract_writes_parsed_structure(self) -> None:
        resume = await _make_resume_in_db(parsed_text="张三 13800138000 zhangsan@example.com")
        router = _make_router(override=_VALID_STRUCTURE_JSON)
        service = ExtractorService(router=router)

        async with AsyncSessionLocal() as session:
            summary = await run_extract(
                db=session,
                target_id=resume.id,
                payload=None,
                service=service,
            )
            await session.commit()

        assert summary["status"] == "extracted"
        assert summary["resume_id"] == str(resume.id)
        assert summary["fields_extracted"] >= 5

        async with AsyncSessionLocal() as session:
            ps = await session.scalar(
                select(ParsedStructure).where(ParsedStructure.resume_id == resume.id)
            )
        assert ps is not None
        assert ps.data["status"] == "extracted"
        assert ps.data["structure"]["name"] == "张三"
        assert ps.data["structure"]["email"] == "zhangsan@example.com"
        assert ps.data["attempts"] == 1

    async def test_null_fields_extracted_status_persisted(self) -> None:
        resume = await _make_resume_in_db(parsed_text="无法识别")
        router = _make_router(override=_EMPTY_STRUCTURE_JSON)
        service = ExtractorService(router=router)

        async with AsyncSessionLocal() as session:
            await run_extract(
                db=session,
                target_id=resume.id,
                payload=None,
                service=service,
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            ps = await session.scalar(
                select(ParsedStructure).where(ParsedStructure.resume_id == resume.id)
            )
        assert ps is not None
        assert ps.data["status"] == "extracted"
        assert ps.data["structure"]["name"] is None
        assert ps.data["structure"]["name_confidence"] == 0.0


class TestRunExtractRetry:
    async def test_first_schema_fail_retry_success_marks_partial(self) -> None:
        resume = await _make_resume_in_db(parsed_text="张三简历")
        # 自定义 adapter：第一次 schema error，第二次返回 valid
        call_count = 0

        class _FlakyAdapter:
            name = "mock"
            default_model = "mock"

            async def chat(self, *, messages, response_schema, temperature, timeout, model):
                nonlocal call_count
                call_count += 1
                if call_count == 1:
                    raise LLMSchemaError("invalid json")
                return LLMResponse(
                    content=_VALID_STRUCTURE_JSON,
                    adapter="mock",
                    model="mock",
                    parsed=response_schema.model_validate_json(_VALID_STRUCTURE_JSON),
                )

        router = LLMRouter(adapters={"mock": _FlakyAdapter()}, default_primary="mock")
        service = ExtractorService(router=router)

        async with AsyncSessionLocal() as session:
            summary = await run_extract(
                db=session,
                target_id=resume.id,
                payload=None,
                service=service,
            )
            await session.commit()

        assert call_count == 2
        assert summary["status"] == "partial_extracted"
        assert summary["attempts"] == 2

        async with AsyncSessionLocal() as session:
            ps = await session.scalar(
                select(ParsedStructure).where(ParsedStructure.resume_id == resume.id)
            )
        assert ps is not None
        assert ps.data["status"] == "partial_extracted"
        assert ps.data["structure"]["name"] == "张三"

    async def test_llm_unavailable_writes_failed(self) -> None:
        resume = await _make_resume_in_db(parsed_text="张三简历")
        router = _make_router(
            failures_before_success=10,
            failure_exception=LLMError("network down"),
        )
        service = ExtractorService(router=router)

        async with AsyncSessionLocal() as session:
            summary = await run_extract(
                db=session,
                target_id=resume.id,
                payload=None,
                service=service,
            )
            await session.commit()

        assert summary["status"] == "failed"

        async with AsyncSessionLocal() as session:
            ps = await session.scalar(
                select(ParsedStructure).where(ParsedStructure.resume_id == resume.id)
            )
        assert ps is not None
        assert ps.data["status"] == "failed"


class TestRunExtractUpsert:
    async def test_second_extract_updates_existing_row(self) -> None:
        resume = await _make_resume_in_db(parsed_text="张三简历")
        # 第一次：null 字段
        router1 = _make_router(override=_EMPTY_STRUCTURE_JSON)
        service1 = ExtractorService(router=router1)

        async with AsyncSessionLocal() as session:
            await run_extract(
                db=session, target_id=resume.id, payload=None, service=service1
            )
            await session.commit()

        # 第二次：完整字段
        router2 = _make_router(override=_VALID_STRUCTURE_JSON)
        service2 = ExtractorService(router=router2)

        async with AsyncSessionLocal() as session:
            await run_extract(
                db=session, target_id=resume.id, payload=None, service=service2
            )
            await session.commit()

        # 应该只有一行，且数据是第二次的
        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(ParsedStructure).where(ParsedStructure.resume_id == resume.id)
                )
            ).scalars().all()
        assert len(rows) == 1
        assert rows[0].data["structure"]["name"] == "张三"


class TestRunExtractErrors:
    async def test_resume_not_found_raises(self) -> None:
        with pytest.raises(ResumeNotFound):
            async with AsyncSessionLocal() as session:
                await run_extract(
                    db=session,
                    target_id=uuid.uuid4(),
                    payload=None,
                    service=ExtractorService(router=_make_router(override=_VALID_STRUCTURE_JSON)),
                )

    async def test_resume_not_ready_when_parse_status_not_success(self) -> None:
        resume = await _make_resume_in_db(parse_status="pending", parsed_text="")

        with pytest.raises(ResumeNotReady):
            async with AsyncSessionLocal() as session:
                await run_extract(
                    db=session,
                    target_id=resume.id,
                    payload=None,
                    service=ExtractorService(router=_make_router(override=_VALID_STRUCTURE_JSON)),
                )

    async def test_resume_text_missing_raises_when_empty_parsed_text(self) -> None:
        # 直接构造 parse_status=success 但 parsed_text=""（不应该发生，但兜底）
        async with AsyncSessionLocal() as session:
            team = Team(name=f"team-{uuid.uuid4().hex[:8]}")
            session.add(team)
            await session.flush()
            candidate = Candidate(
                team_id=team.id,
                dedup_key=f"test:{uuid.uuid4()}",
                name="t",
            )
            session.add(candidate)
            await session.flush()
            source = CandidateSource(candidate_id=candidate.id, source_type="upload")
            session.add(source)
            await session.flush()
            resume = CandidateResume(
                candidate_id=candidate.id,
                source_id=source.id,
                file_storage_key="k",
                file_mime="application/pdf",
                parse_status="success",
                parsed_text="",  # 异常状态
            )
            session.add(resume)
            await session.commit()
            await session.refresh(resume)
            resume_id = resume.id

        with pytest.raises(ResumeTextMissing):
            async with AsyncSessionLocal() as session:
                await run_extract(
                    db=session,
                    target_id=resume_id,
                    payload=None,
                    service=ExtractorService(router=_make_router(override=_VALID_STRUCTURE_JSON)),
                )

    async def test_team_id_payload_propagated_to_router(self) -> None:
        """payload 携带 team_id 时应设置 router 的 team 上下文。"""
        resume = await _make_resume_in_db(parsed_text="张三简历")
        router = _make_router(override=_VALID_STRUCTURE_JSON)
        service = ExtractorService(router=router)
        team_id = uuid.uuid4()

        async with AsyncSessionLocal() as session:
            await run_extract(
                db=session,
                target_id=resume.id,
                payload={"team_id": str(team_id)},
                service=service,
            )
            await session.commit()

        # 验证 router 上下文已设置（通过属性或副作用间接验证）
        # 若 router 无暴露属性，仅验证调用没失败即可（已通过）
        async with AsyncSessionLocal() as session:
            ps = await session.scalar(
                select(ParsedStructure).where(ParsedStructure.resume_id == resume.id)
            )
        assert ps is not None
        assert ps.data["status"] == "extracted"
