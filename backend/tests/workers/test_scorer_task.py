"""``run_score`` celery handler 编排测试（任务 17）。

覆盖：
- 正常路径 → 写 scores 行
- candidate 不存在 / 没有 ParsedStructure / job 不存在 → 永久失败
- 注入 router 验证 model_used + llm_call_id
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from app.adapters.llm import MockAdapter
from app.adapters.llm.router import LLMRouter
from app.core.db import AsyncSessionLocal
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.job import Job
from app.models.score import Score
from app.models.team import Team
from app.models.user import User
from app.workers.scorer_task import (
    CandidateNotFound,
    JobNotFound,
    StructureMissing,
    run_score,
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
# Helpers
# ============================================================================


_VALID_SCORES_JSON = json.dumps(
    {
        "total": 85,
        "skill": 90,
        "experience": 80,
        "education": 75,
        "stability": 80,
        "potential": 85,
    },
    ensure_ascii=False,
)


def _make_router(
    override: str | None = None,
) -> tuple[LLMRouter, MockAdapter]:
    mock = MockAdapter(
        response_override=override or _VALID_SCORES_JSON,
        name="mock",
        default_model="mock-model",
    )
    router = LLMRouter(
        adapters={"mock": mock},
        default_primary="mock",
        default_fallback=None,
    )
    return router, mock


async def _seed_full_candidate(
    *,
    with_structure: bool = True,
    parsed_text: str | None = "Python 5 年经验 FastAPI",
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

        if with_structure:
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
                            "years_of_experience_confidence": 0.8,
                            "skills": ["Python", "FastAPI"],
                            "skills_confidence": 0.9,
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
# Tests
# ============================================================================


class TestRunScoreHappyPath:
    async def test_normal_path_writes_score(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()
        router, mock = _make_router()

        async with AsyncSessionLocal() as session:
            summary = await run_score(
                db=session,
                target_id=candidate.id,
                payload={"job_id": str(job.id), "team_id": str(team.id)},
                router=router,
            )
            await session.commit()

        assert summary["total"] == 85
        assert summary["model_used"] == "mock-model"

        async with AsyncSessionLocal() as session:
            score = await session.scalar(
                select(Score).where(
                    Score.job_id == job.id,
                    Score.candidate_id == candidate.id,
                )
            )
        assert score is not None
        assert score.total == 85
        assert score.skill == 90
        assert score.model_used == "mock-model"
        # llm_call_id 应被写入（router._log_call 回填）
        assert score.llm_call_id is not None


class TestRunScoreErrors:
    async def test_candidate_not_found(self) -> None:
        team, user, job, _ = await _seed_full_candidate()
        router, _ = _make_router()

        async with AsyncSessionLocal() as session:
            with pytest.raises(CandidateNotFound):
                await run_score(
                    db=session,
                    target_id=uuid.uuid4(),
                    payload={"job_id": str(job.id)},
                    router=router,
                )

    async def test_job_not_found(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_router()

        async with AsyncSessionLocal() as session:
            with pytest.raises(JobNotFound):
                await run_score(
                    db=session,
                    target_id=candidate.id,
                    payload={"job_id": str(uuid.uuid4())},
                    router=router,
                )

    async def test_structure_missing(self) -> None:
        team, user, job, candidate = await _seed_full_candidate(with_structure=False)
        router, _ = _make_router()

        async with AsyncSessionLocal() as session:
            with pytest.raises(StructureMissing):
                await run_score(
                    db=session,
                    target_id=candidate.id,
                    payload={"job_id": str(job.id)},
                    router=router,
                )

    async def test_payload_missing_job_id(self) -> None:
        team, user, job, candidate = await _seed_full_candidate()
        router, _ = _make_router()

        async with AsyncSessionLocal() as session:
            with pytest.raises(ValueError):
                await run_score(
                    db=session,
                    target_id=candidate.id,
                    payload={},
                    router=router,
                )
