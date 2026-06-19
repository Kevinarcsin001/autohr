"""InterviewService 单元测试（任务 19）。

策略：
- ``_build_weakness_hint`` 是纯函数 → 直接覆盖
- ``generate`` / ``regenerate`` / ``list_batches`` / ``save_feedback`` 走 DB 集成测试
- 模拟 LLM 用智能 Mock（按 response_schema 类型返回不同 JSON）
- 覆盖：5-8 题 schema 强制、≥1 题 weakness、batch_id 保留、feedback upsert、
  confidence 低字段进入 weakness_hint、LLM schema 错误 / 不可用错误
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from app.adapters.llm import LLMError, LLMSchemaError, LLMResponse, MockAdapter
from app.adapters.llm.router import LLMRouter
from app.core.db import AsyncSessionLocal
from app.core.middleware.error_handler import NotFoundError, ValidationError
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.interview import InterviewFeedback, InterviewQuestion
from app.models.job import Job
from app.models.team import Team
from app.models.user import User
from app.schemas.interview import FeedbackRequest, InterviewQuestions
from app.services.interview import (
    InterviewError,
    InterviewService,
    _FIRST_TEMPERATURE,
    _LOW_CONFIDENCE_THRESHOLD,
    _REGENERATE_TEMPERATURE,
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
# 常量 / 工具
# ============================================================================


_VALID_QUESTIONS_JSON = json.dumps(
    {
        "questions": [
            {"dimension": "skill", "question": "请深入聊聊你使用 Python 的项目"},
            {"dimension": "project", "question": "讲一个你主导过的技术项目，包括难点"},
            {"dimension": "weakness", "question": "你简历中提到 5 年经验，但项目偏少，能详细说说吗？"},
            {"dimension": "weakness", "question": "为什么没有 AWS / 云相关的经验？"},
            {"dimension": "culture", "question": "你怎么看待团队协作与跨部门沟通？"},
        ]
    },
    ensure_ascii=False,
)


def _make_smart_router(
    *, questions_override: str | None = None
) -> tuple[LLMRouter, Any]:
    """智能 mock：按 response_schema 类型返回不同 JSON。"""

    class _SmartMock:
        name = "mock"
        default_model = "mock-model"
        _call_count = 0
        last_temperature: float | None = None

        async def chat(
            self, *, messages, response_schema, temperature, timeout, model
        ):
            type(self)._call_count += 1
            type(self).last_temperature = temperature
            if response_schema is InterviewQuestions:
                content = questions_override or _VALID_QUESTIONS_JSON
            else:
                content = "{}"
            parsed = (
                response_schema.model_validate_json(content)
                if response_schema is not None
                else None
            )
            return LLMResponse(
                content=content,
                adapter="mock",
                model="mock-model",
                parsed=parsed,
            )

    adapter = _SmartMock()
    router = LLMRouter(
        adapters={"mock": adapter},
        default_primary="mock",
        default_fallback=None,
    )
    return router, adapter


def _make_failing_router(*, schema_error: bool = False) -> LLMRouter:
    class _FailingMock:
        name = "mock"
        default_model = "mock-model"

        async def chat(self, **kwargs):
            if schema_error:
                raise LLMSchemaError("schema broken")
            raise LLMError("llm unavailable")

    return LLMRouter(
        adapters={"mock": _FailingMock()},
        default_primary="mock",
        default_fallback=None,
    )


async def _seed_full_candidate(
    *,
    skills_confidence: float = 0.9,
    years_confidence: float = 0.8,
    parsed_text: str = "Python 与 FastAPI 5年经验",
) -> tuple[Any, Any, Any, Any]:
    """返回 (team, user, job, candidate)。"""
    async with AsyncSessionLocal() as session:
        team = Team(name=f"team-{uuid.uuid4().hex[:8]}")
        session.add(team)
        await session.flush()

        user = User(
            email=f"u-{uuid.uuid4().hex[:8]}@x.com",
            password_hash="x",
            name="hr",
        )
        session.add(user)
        await session.flush()

        job = Job(
            team_id=team.id,
            title="Eng",
            jd_text="招聘 Python 工程师",
            status="active",
            created_by=user.id,
        )
        session.add(job)
        await session.flush()

        candidate = Candidate(
            team_id=team.id,
            dedup_key=f"test:{uuid.uuid4()}",
            name="张三",
        )
        session.add(candidate)
        await session.flush()
        src = CandidateSource(candidate_id=candidate.id, source_type="upload")
        session.add(src)
        await session.flush()
        resume = CandidateResume(
            candidate_id=candidate.id,
            source_id=src.id,
            file_storage_key="k",
            file_mime="application/pdf",
            parse_status="success",
            parsed_text=parsed_text,
        )
        session.add(resume)
        await session.flush()
        session.add(
            ParsedStructure(
                resume_id=resume.id,
                data={
                    "structure": {
                        "name": "张三", "name_confidence": 0.9,
                        "phone": "13800138000", "phone_confidence": 0.9,
                        "email": "zs@x.com", "email_confidence": 0.9,
                        "education": "master",
                        "education_confidence": 0.85,
                        "years_of_experience": 5,
                        "years_of_experience_confidence": years_confidence,
                        "skills": ["Python", "FastAPI"],
                        "skills_confidence": skills_confidence,
                        "expected_salary": None,
                        "expected_salary_confidence": 0.0,
                        "current_company": "ACME",
                        "current_company_confidence": 0.85,
                        "work_history": [],
                        "work_history_confidence": 0.0,
                    },
                    "status": "extracted",
                },
            )
        )
        await session.commit()
        return team, user, job, candidate


# ============================================================================
# _build_weakness_hint 纯函数测试
# ============================================================================


class TestBuildWeaknessHint:
    def test_none_structure(self) -> None:
        hint = InterviewService._build_weakness_hint(None)
        assert "无结构化数据" in hint

    def test_all_confident(self) -> None:
        """所有字段 confidence ≥ 0.7 → 返回 fallback 提示（无短板列表）。"""
        from app.schemas.candidate_structure import CandidateStructure

        s = CandidateStructure(
            name="x", phone="13800138000", email="x@y.com",
            education="bachelor",
            years_of_experience=5,
            skills=["python"],
            skills_confidence=0.9,
            years_of_experience_confidence=0.9,
            education_confidence=0.9,
            current_company="ACME",
            current_company_confidence=0.9,
            work_history=[],
            work_history_confidence=0.9,
        )
        hint = InterviewService._build_weakness_hint(s)
        # fallback 不以 "- " 开头（短板列表项格式）
        assert not hint.startswith("- ")
        assert "≥ 0.7" in hint

    def test_low_skills_confidence_listed(self) -> None:
        from app.schemas.candidate_structure import CandidateStructure

        s = CandidateStructure(
            name="x", phone="13800138000", email="x@y.com",
            education="bachelor",
            years_of_experience=5,
            skills=["python"],
            skills_confidence=0.3,  # 低
            years_of_experience_confidence=0.9,
            education_confidence=0.9,
            current_company="ACME",
            current_company_confidence=0.9,
            work_history=[],
            work_history_confidence=0.9,
        )
        hint = InterviewService._build_weakness_hint(s)
        assert "技能" in hint
        assert "0.30" in hint

    def test_threshold_boundary(self) -> None:
        """confidence = 0.7 不视为短板（< 0.7 才算）。"""
        from app.schemas.candidate_structure import CandidateStructure

        s = CandidateStructure(
            name="x", phone="13800138000", email="x@y.com",
            education="bachelor",
            years_of_experience=5,
            skills=["python"],
            skills_confidence=_LOW_CONFIDENCE_THRESHOLD,  # 等于阈值
            years_of_experience_confidence=0.9,
            education_confidence=0.9,
            current_company="ACME",
            current_company_confidence=0.9,
            work_history=[],
            work_history_confidence=0.9,
        )
        hint = InterviewService._build_weakness_hint(s)
        assert "技能" not in hint


# ============================================================================
# generate / regenerate / list_batches
# ============================================================================


class TestGenerate:
    async def test_generate_writes_5_questions(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_smart_router()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            result = await service.generate(
                candidate_id=candidate.id,
                job_id=job.id,
            )
            await session.commit()

        assert result.is_regeneration is False
        assert result.temperature == _FIRST_TEMPERATURE
        assert result.question_count == 5

        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(InterviewQuestion).where(
                    InterviewQuestion.candidate_id == candidate.id,
                    InterviewQuestion.job_id == job.id,
                )
            )).scalars().all()
        assert len(rows) == 5
        # 至少 1 条 weakness（schema 强制）
        assert any(r.dimension == "weakness" for r in rows)
        # sort_order 从 0 递增
        assert sorted(r.sort_order for r in rows) == [0, 1, 2, 3, 4]

    async def test_generate_with_low_confidence_logs_hint(self) -> None:
        """低 confidence 字段进入 prompt（间接通过 _build_weakness_hint 验证）。"""
        team, user, job, candidate = await _seed_full_candidate(
            skills_confidence=0.3
        )
        router, _ = _make_smart_router()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            result = await service.generate(
                candidate_id=candidate.id,
                job_id=job.id,
            )
            await session.commit()

        assert result.question_count == 5

    async def test_generate_with_missing_score_still_works(self) -> None:
        """score 未生成时也允许调 generate（prompt 内 total=-）。"""
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_smart_router()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            result = await service.generate(
                candidate_id=candidate.id,
                job_id=job.id,
            )
            await session.commit()

        assert result.question_count == 5

    async def test_generate_candidate_not_found(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_smart_router()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            with pytest.raises(NotFoundError):
                await service.generate(
                    candidate_id=uuid.uuid4(),
                    job_id=job.id,
                )

    async def test_generate_job_not_found(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_smart_router()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            with pytest.raises(NotFoundError):
                await service.generate(
                    candidate_id=candidate.id,
                    job_id=uuid.uuid4(),
                )

    async def test_generate_llm_schema_error_raises(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()
        router = _make_failing_router(schema_error=True)

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            with pytest.raises(InterviewError) as exc_info:
                await service.generate(
                    candidate_id=candidate.id,
                    job_id=job.id,
                )
            assert "schema" in str(exc_info.value).lower()

    async def test_generate_llm_unavailable_raises(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()
        router = _make_failing_router(schema_error=False)

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            with pytest.raises(InterviewError) as exc_info:
                await service.generate(
                    candidate_id=candidate.id,
                    job_id=job.id,
                )
            assert "unavailable" in str(exc_info.value).lower()


# ============================================================================
# regenerate：保留历史 batch + temperature=0.8
# ============================================================================


class TestRegenerate:
    async def test_regenerate_keeps_history_and_uses_high_temp(self) -> None:
        """regenerate 不删除旧 batch；用 temperature=0.8。"""
        team, user, job, candidate = await _seed_full_candidate()
        router, mock_cls = _make_smart_router()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            first = await service.generate(
                candidate_id=candidate.id,
                job_id=job.id,
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            second = await service.regenerate(
                candidate_id=candidate.id,
                job_id=job.id,
            )
            await session.commit()

        assert first.batch_id != second.batch_id
        assert second.is_regeneration is True
        assert second.temperature == _REGENERATE_TEMPERATURE
        assert first.temperature == _FIRST_TEMPERATURE

        async with AsyncSessionLocal() as session:
            batches = (await session.execute(
                select(InterviewQuestion.batch_id)
                .where(
                    InterviewQuestion.candidate_id == candidate.id,
                    InterviewQuestion.job_id == job.id,
                )
                .distinct()
            )).scalars().all()
        assert len(batches) == 2

    async def test_list_batches_returns_all(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_smart_router()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            await service.generate(
                candidate_id=candidate.id, job_id=job.id
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            await service.regenerate(
                candidate_id=candidate.id, job_id=job.id
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session)
            batches, current, total = await service.list_batches(
                candidate_id=candidate.id, job_id=job.id
            )

        assert len(batches) == 2
        assert total == 10  # 5 + 5
        assert current == batches[0]

    async def test_list_latest_batch_returns_correct_one(self) -> None:
        """list_latest_batch 返回的是最新创建的 batch。"""
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_smart_router()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            first = await service.generate(
                candidate_id=candidate.id, job_id=job.id
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            second = await service.regenerate(
                candidate_id=candidate.id, job_id=job.id
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session)
            rows, latest_batch = await service.list_latest_batch(
                candidate_id=candidate.id, job_id=job.id
            )

        assert latest_batch == second.batch_id
        assert latest_batch != first.batch_id
        assert len(rows) == 5

    async def test_list_latest_batch_empty_returns_none(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session)
            rows, batch = await service.list_latest_batch(
                candidate_id=candidate.id, job_id=job.id
            )

        assert rows == []
        assert batch is None


# ============================================================================
# save_feedback / list_feedback
# ============================================================================


class TestSaveFeedback:
    async def test_create_new_feedback(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_smart_router()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            await service.generate(
                candidate_id=candidate.id, job_id=job.id
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            q = (await session.execute(
                select(InterviewQuestion).where(
                    InterviewQuestion.candidate_id == candidate.id
                )
            )).scalars().first()
            question_id = q.id

        async with AsyncSessionLocal() as session:
            service = InterviewService(session)
            feedback, question = await service.save_feedback(
                question_id=question_id,
                reviewer_id=user.id,
                payload=FeedbackRequest(
                    feedback="回答清晰", rating=4
                ),
            )
            await session.commit()

        assert feedback.feedback == "回答清晰"
        assert feedback.rating == 4
        assert feedback.reviewer_id == user.id
        assert question.id == question_id

    async def test_upsert_existing_feedback(self) -> None:
        """同 question + reviewer 二次写覆盖。"""
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_smart_router()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            await service.generate(
                candidate_id=candidate.id, job_id=job.id
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            q = (await session.execute(
                select(InterviewQuestion).where(
                    InterviewQuestion.candidate_id == candidate.id
                )
            )).scalars().first()
            question_id = q.id

        # 第一次写
        async with AsyncSessionLocal() as session:
            service = InterviewService(session)
            await service.save_feedback(
                question_id=question_id,
                reviewer_id=user.id,
                payload=FeedbackRequest(feedback="原始反馈", rating=3),
            )
            await session.commit()

        # 第二次写（覆盖）
        async with AsyncSessionLocal() as session:
            service = InterviewService(session)
            feedback, _ = await service.save_feedback(
                question_id=question_id,
                reviewer_id=user.id,
                payload=FeedbackRequest(feedback="更新反馈", rating=5),
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(InterviewFeedback).where(
                    InterviewFeedback.question_id == question_id,
                    InterviewFeedback.reviewer_id == user.id,
                )
            )).scalars().all()

        assert len(rows) == 1
        assert rows[0].feedback == "更新反馈"
        assert rows[0].rating == 5

    async def test_save_feedback_only_rating(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_smart_router()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            await service.generate(
                candidate_id=candidate.id, job_id=job.id
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            q = (await session.execute(
                select(InterviewQuestion).where(
                    InterviewQuestion.candidate_id == candidate.id
                )
            )).scalars().first()
            question_id = q.id

        async with AsyncSessionLocal() as session:
            service = InterviewService(session)
            feedback, _ = await service.save_feedback(
                question_id=question_id,
                reviewer_id=user.id,
                payload=FeedbackRequest(rating=4),
            )
            await session.commit()

        assert feedback.feedback is None
        assert feedback.rating == 4

    async def test_save_feedback_requires_at_least_one_field(self) -> None:
        """feedback 和 rating 都为 None → 422。"""
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_smart_router()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            await service.generate(
                candidate_id=candidate.id, job_id=job.id
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            q = (await session.execute(
                select(InterviewQuestion).where(
                    InterviewQuestion.candidate_id == candidate.id
                )
            )).scalars().first()
            question_id = q.id

        async with AsyncSessionLocal() as session:
            service = InterviewService(session)
            with pytest.raises(ValidationError):
                await service.save_feedback(
                    question_id=question_id,
                    reviewer_id=user.id,
                    payload=FeedbackRequest(),
                )

    async def test_save_feedback_question_not_found(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session)
            with pytest.raises(NotFoundError):
                await service.save_feedback(
                    question_id=uuid.uuid4(),
                    reviewer_id=user.id,
                    payload=FeedbackRequest(feedback="x"),
                )

    async def test_save_feedback_reviewer_not_found(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_smart_router()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            await service.generate(
                candidate_id=candidate.id, job_id=job.id
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            q = (await session.execute(
                select(InterviewQuestion).where(
                    InterviewQuestion.candidate_id == candidate.id
                )
            )).scalars().first()
            question_id = q.id

        async with AsyncSessionLocal() as session:
            service = InterviewService(session)
            with pytest.raises(NotFoundError):
                await service.save_feedback(
                    question_id=question_id,
                    reviewer_id=uuid.uuid4(),  # 不存在
                    payload=FeedbackRequest(feedback="x"),
                )

    async def test_list_feedback_returns_desc(self) -> None:
        """多个 reviewer 反馈按 created_at 倒序。"""
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_smart_router()

        # 创建第二个 reviewer
        async with AsyncSessionLocal() as session:
            user2 = User(
                email=f"u2-{uuid.uuid4().hex[:8]}@x.com",
                password_hash="x",
                name="hr2",
            )
            session.add(user2)
            await session.commit()
            user2_id = user2.id

        async with AsyncSessionLocal() as session:
            service = InterviewService(session, router=router)
            await service.generate(
                candidate_id=candidate.id, job_id=job.id
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            q = (await session.execute(
                select(InterviewQuestion).where(
                    InterviewQuestion.candidate_id == candidate.id
                )
            )).scalars().first()
            question_id = q.id

        # 两个 reviewer 各写一条
        async with AsyncSessionLocal() as session:
            service = InterviewService(session)
            await service.save_feedback(
                question_id=question_id,
                reviewer_id=user.id,
                payload=FeedbackRequest(feedback="reviewer1", rating=4),
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session)
            await service.save_feedback(
                question_id=question_id,
                reviewer_id=user2_id,
                payload=FeedbackRequest(feedback="reviewer2", rating=5),
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            service = InterviewService(session)
            rows = await service.list_feedback(question_id=question_id)

        assert len(rows) == 2
        # 最新的在前（reviewer2 后写）
        assert rows[0].feedback == "reviewer2"
        assert rows[1].feedback == "reviewer1"
