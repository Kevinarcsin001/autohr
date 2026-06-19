"""``run_score`` 端到端任务级测试（任务 18 集成）。

覆盖：
- score 完成后自动生成 recommend（候选人未被 filter 淘汰）
- score 完成后自动生成 disqualify（ScreeningResult.disqualified=True）
- Reasoning 失败不阻塞 score 已写入的结果
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
from app.models.score import Score, ScoreReason
from app.models.screening import ScreeningResult
from app.models.team import Team
from app.models.user import User
from app.workers.scorer_task import run_score

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
        "total": 85, "skill": 90, "experience": 80,
        "education": 75, "stability": 80, "potential": 85,
    },
    ensure_ascii=False,
)


_VALID_REASONS_JSON = json.dumps(
    {
        "bullet_points": [
            "Python 技能匹配",
            "FastAPI 项目经验",
            "5 年工作年限达标",
        ],
        "evidence": ["python", "fastapi", "5年"],
    },
    ensure_ascii=False,
)


def _make_router(
    *,
    scorer_override: str = _VALID_SCORES_JSON,
    reasoning_override: str = _VALID_REASONS_JSON,
) -> LLMRouter:
    """智能 mock：按 response_schema 类型返回不同 JSON。"""
    from app.adapters.llm import LLMResponse
    from app.schemas.reason import RecommendReasons
    from app.schemas.score import ScoreDimensions

    class _SmartMock:
        name = "mock"
        default_model = "mock-model"
        _call_count = 0

        async def chat(
            self, *, messages, response_schema, temperature, timeout, model
        ):
            type(self)._call_count += 1
            if response_schema is ScoreDimensions:
                content = scorer_override
            elif response_schema is RecommendReasons:
                content = reasoning_override
            else:
                content = "{}"
            parsed = (
                response_schema.model_validate_json(content)
                if response_schema is not None
                else None
            )
            return LLMResponse(
                content=content,
                adapter="mock", model="mock-model",
                parsed=parsed,
            )

    return LLMRouter(
        adapters={"mock": _SmartMock()},
        default_primary="mock",
        default_fallback=None,
    )


async def _seed_full_candidate(
    *,
    parsed_text: str = "Python 与 FastAPI 5年经验",
    with_screening_disqualified: bool = False,
    screening_reasons: list[str] | None = None,
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
        if with_screening_disqualified:
            session.add(ScreeningResult(
                job_id=job.id,
                candidate_id=candidate.id,
                disqualified=True,
                reasons=screening_reasons or ["学历不达标：本科 vs 要求硕士"],
                manually_overridden=False,
            ))
        await session.commit()
        return team, user, job, candidate


# ============================================================================
# Tests
# ============================================================================


class TestRunScoreAutoReason:
    async def test_recommend_generated_for_passing_candidate(self) -> None:
        """未淘汰 → 自动生成 recommend + 写 score_reasons。"""
        team, user, job, candidate = await _seed_full_candidate()
        router = _make_router()

        async with AsyncSessionLocal() as session:
            summary = await run_score(
                db=session,
                target_id=candidate.id,
                payload={"job_id": str(job.id)},
                router=router,
            )
            await session.commit()

        assert summary["reason_status"] == "recommend_generated"
        assert summary["total"] == 85

        async with AsyncSessionLocal() as session:
            score = await session.scalar(
                select(Score).where(
                    Score.job_id == job.id,
                    Score.candidate_id == candidate.id,
                )
            )
            assert score is not None
            reason = await session.scalar(
                select(ScoreReason).where(
                    ScoreReason.score_id == score.id,
                    ScoreReason.type == "recommend",
                )
            )
        assert reason is not None
        assert reason.validated is True
        assert len(reason.bullet_points) == 3

    async def test_disqualify_generated_for_filtered_candidate(self) -> None:
        """已淘汰 → 自动生成 disqualify，**不调 LLM**，用 FilterService.reasons。"""
        team, user, job, candidate = await _seed_full_candidate(
            with_screening_disqualified=True,
            screening_reasons=["学历不达标：本科 vs 要求硕士"],
        )
        router = _make_router()

        async with AsyncSessionLocal() as session:
            summary = await run_score(
                db=session,
                target_id=candidate.id,
                payload={"job_id": str(job.id)},
                router=router,
            )
            await session.commit()

        assert summary["reason_status"] == "disqualify_written"

        async with AsyncSessionLocal() as session:
            score = await session.scalar(
                select(Score).where(
                    Score.job_id == job.id,
                    Score.candidate_id == candidate.id,
                )
            )
            assert score is not None
            reason = await session.scalar(
                select(ScoreReason).where(
                    ScoreReason.score_id == score.id,
                    ScoreReason.type == "disqualify",
                )
            )
        assert reason is not None
        assert reason.bullet_points == ["学历不达标：本科 vs 要求硕士"]
        assert reason.validated is True

    async def test_reasoning_failure_does_not_block_score(self) -> None:
        """Reasoning 抛错 → score 仍写入；reason_status 含 'failed'。"""
        # 用 schema 错误让 reasoning 失败（scorer 的合法 JSON 对 RecommendReasons 是非法的）
        from app.adapters.llm import LLMSchemaError

        class _FakeAdapter:
            name = "mock"
            default_model = "mock"
            _call_count = 0

            async def chat(
                self, *, messages, response_schema, temperature, timeout, model
            ):
                type(self)._call_count += 1
                # 第 1 次（scorer）返回合法；第 2 次（reasoning）抛 schema error
                if type(self)._call_count == 1:
                    from app.adapters.llm import LLMResponse
                    parsed = response_schema.model_validate_json(_VALID_SCORES_JSON)
                    return LLMResponse(
                        content=_VALID_SCORES_JSON,
                        adapter="mock", model="mock",
                        parsed=parsed,
                    )
                raise LLMSchemaError("reasoning schema broken")

        adapter = _FakeAdapter()
        router = LLMRouter(
            adapters={"mock": adapter},
            default_primary="mock",
            default_fallback=None,
        )

        team, user, job, candidate = await _seed_full_candidate()

        async with AsyncSessionLocal() as session:
            summary = await run_score(
                db=session,
                target_id=candidate.id,
                payload={"job_id": str(job.id)},
                router=router,
            )
            await session.commit()

        assert summary["total"] == 85  # score 已写入
        assert "reasoning_failed" in summary["reason_status"]

        async with AsyncSessionLocal() as session:
            score = await session.scalar(
                select(Score).where(
                    Score.job_id == job.id,
                    Score.candidate_id == candidate.id,
                )
            )
            assert score is not None
            reasons = (await session.execute(
                select(ScoreReason).where(ScoreReason.score_id == score.id)
            )).scalars().all()
        # reason 没写入（reasoning 失败）
        assert reasons == []
