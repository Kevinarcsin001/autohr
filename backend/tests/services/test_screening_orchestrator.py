"""ScreeningOrchestrator 单元测试（任务 20）。

策略：
- ``ProgressStore`` 是纯 in-memory → 直接覆盖 create / append / wait_next_event
- ``ScreeningOrchestrator.run`` 走 DB 集成测试（用 mock LLM）：
  - 正常路径：5 候选人 → 全 filter pass → 5 score + 5 interview → summary
  - filter 淘汰 1 个 → 4 score + 4 interview；1 disqualified
  - score 失败 1 个 → 不阻塞其他 → failed=1
  - interview 失败 1 个 → score 已写入 → failed=1
- progress 推送事件计数：total + 1 (started) + N (progress) + 1 (done)
"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from app.adapters.llm import LLMResponse
from app.adapters.llm.router import LLMRouter
from app.core.db import AsyncSessionLocal
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.interview import InterviewQuestion
from app.models.job import Job, JobHardRequirement
from app.models.score import Score
from app.models.team import Team
from app.models.user import User
from app.schemas.interview import InterviewQuestions
from app.schemas.reason import RecommendReasons
from app.schemas.score import ScoreDimensions
from app.services.interview import InterviewService
from app.services.screening_orchestrator import (
    ProgressStore,
    ScreeningOrchestrator,
    progress_store,
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
# Mock LLM：按 response_schema 类型路由（score / reason / interview）
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
            "Python 技能匹配", "FastAPI 项目经验", "5 年工作年限",
        ],
        "evidence": ["python", "fastapi", "5年"],
    },
    ensure_ascii=False,
)


_VALID_QUESTIONS_JSON = json.dumps(
    {
        "questions": [
            {"dimension": "skill", "question": "请深入聊聊 Python 项目"},
            {"dimension": "project", "question": "讲一个技术项目"},
            {"dimension": "weakness", "question": "为什么没有云经验？"},
            {"dimension": "weakness", "question": "项目数量偏少，详细说说"},
            {"dimension": "culture", "question": "团队协作怎么样？"},
        ]
    },
    ensure_ascii=False,
)


def _make_smart_router() -> LLMRouter:
    """按 response_schema 类型路由不同 JSON 的智能 mock。"""

    class _SmartMock:
        name = "mock"
        default_model = "mock-model"
        _call_count = 0
        score_should_fail = False
        interview_should_fail = False

        async def chat(
            self, *, messages, response_schema, temperature, timeout, model
        ):
            type(self)._call_count += 1
            if response_schema is ScoreDimensions:
                if type(self).score_should_fail:
                    from app.adapters.llm import LLMError
                    raise LLMError("score mock failed")
                content = _VALID_SCORES_JSON
            elif response_schema is RecommendReasons:
                content = _VALID_REASONS_JSON
            elif response_schema is InterviewQuestions:
                if type(self).interview_should_fail:
                    from app.adapters.llm import LLMSchemaError
                    raise LLMSchemaError("interview schema broken")
                content = _VALID_QUESTIONS_JSON
            else:
                content = "{}"
            parsed = (
                response_schema.model_validate_json(content)
                if response_schema is not None
                else None
            )
            return LLMResponse(
                content=content, adapter="mock", model="mock-model",
                parsed=parsed,
            )

    return LLMRouter(
        adapters={"mock": _SmartMock()},
        default_primary="mock",
        default_fallback=None,
    )


async def _seed_team_job_and_candidates(
    *,
    n_candidates: int = 5,
    add_hard_requirement: bool = False,
    disqualify_one_via_requirement: bool = False,
    parsed_text: str = "Python 与 FastAPI 5年经验",
) -> tuple[Any, Any, Any, list[Any]]:
    """创建 team + job + N candidates（每个含 resume + parsed_structure）。"""
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

        if add_hard_requirement:
            # 给第 1 个候选人故意低学历淘汰（disqualify_one_via_requirement）
            session.add(JobHardRequirement(
                job_id=job.id, min_education="master", min_years=3,
            ))

        candidates: list[Any] = []
        for i in range(n_candidates):
            cand = Candidate(
                team_id=team.id,
                dedup_key=f"test:{uuid.uuid4()}",
                name=f"cand-{i}",
            )
            session.add(cand)
            await session.flush()
            src = CandidateSource(
                candidate_id=cand.id, source_type="upload"
            )
            session.add(src)
            await session.flush()
            resume = CandidateResume(
                candidate_id=cand.id, source_id=src.id,
                file_storage_key=f"k-{i}",
                file_mime="application/pdf",
                parse_status="success",
                parsed_text=parsed_text,
            )
            session.add(resume)
            await session.flush()
            # 故意让第 1 个候选人学历不达标
            education_val = "bachelor" if (i == 0 and disqualify_one_via_requirement) else "master"
            session.add(ParsedStructure(
                resume_id=resume.id,
                data={
                    "structure": {
                        "name": f"cand-{i}", "name_confidence": 0.9,
                        "phone": "13800138000", "phone_confidence": 0.9,
                        "email": f"c{i}@x.com", "email_confidence": 0.9,
                        "education": education_val,
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
            ))
            candidates.append(cand)

        await session.commit()
        return team, user, job, candidates


# ============================================================================
# ProgressStore 纯单元测试
# ============================================================================


class TestProgressStore:
    async def test_create_writes_started_event(self) -> None:
        store = ProgressStore()
        run_id = uuid.uuid4()
        await store.create(run_id, total=5)
        events = store.get_events_after(run_id, -1)
        assert len(events) == 1
        assert events[0].type == "started"
        assert events[0].data["total"] == 5

    async def test_append_progress_increments_event_id(self) -> None:
        store = ProgressStore()
        run_id = uuid.uuid4()
        await store.create(run_id, total=2)
        cid = uuid.uuid4()
        await store.append_progress(
            run_id,
            candidate_id=cid,
            candidate_name="x",
            stage="filter",
            status="ok",
        )
        await store.append_progress(
            run_id,
            candidate_id=cid,
            candidate_name="x",
            stage="score",
            status="ok",
        )
        events = store.get_events_after(run_id, -1)
        assert len(events) == 3
        assert events[0].event_id == 0
        assert events[1].event_id == 1
        assert events[2].event_id == 2

    async def test_get_events_after_resumes(self) -> None:
        """断线重连：从 last_event_id+1 取后续。"""
        store = ProgressStore()
        run_id = uuid.uuid4()
        await store.create(run_id, total=3)
        for i in range(3):
            await store.append_progress(
                run_id,
                candidate_id=uuid.uuid4(),
                candidate_name=f"c-{i}",
                stage="filter",
                status="ok",
            )
        await store.append_done(run_id, summary={"total": 3, "passed": 3})

        # 重连：客户端只看到 event_id=2 → 取 [3, 4]
        events = store.get_events_after(run_id, 2)
        assert len(events) == 2
        assert events[0].event_id == 3
        assert events[1].event_id == 4
        assert events[1].type == "done"

    async def test_wait_next_event_blocks_until_notify(self) -> None:
        store = ProgressStore()
        run_id = uuid.uuid4()
        await store.create(run_id, total=1)

        async def _append_later():
            await asyncio.sleep(0.05)
            await store.append_progress(
                run_id,
                candidate_id=uuid.uuid4(),
                candidate_name="x",
                stage="filter",
                status="ok",
            )

        task = asyncio.create_task(_append_later())
        ev = await store.wait_next_event(run_id, after_event_id=0, timeout=1.0)
        assert ev is not None
        assert ev.type == "progress"
        await task

    async def test_wait_next_event_timeout_returns_none(self) -> None:
        store = ProgressStore()
        run_id = uuid.uuid4()
        await store.create(run_id, total=1)
        ev = await store.wait_next_event(run_id, after_event_id=0, timeout=0.05)
        assert ev is None

    async def test_is_done(self) -> None:
        store = ProgressStore()
        run_id = uuid.uuid4()
        await store.create(run_id, total=1)
        assert not store.is_done(run_id)
        await store.append_done(run_id, summary={})
        assert store.is_done(run_id)


# ============================================================================
# ScreeningOrchestrator 集成测试
# ============================================================================


def _patch_orchestrator_router(monkeypatch) -> None:
    """覆盖 InterviewService._get_router + 注入 orchestrator router。"""
    router = _make_smart_router()

    def _fake_get_router(self):  # noqa: ANN001
        return router

    monkeypatch.setattr(InterviewService, "_get_router", _fake_get_router)


class TestOrchestratorHappyPath:
    async def test_run_all_5_pass(self, monkeypatch) -> None:
        team, user, job, candidates = await _seed_team_job_and_candidates(
            n_candidates=5
        )
        router = _make_smart_router()
        run_id = uuid.uuid4()
        await progress_store.create(run_id, total=5)

        orchestrator = ScreeningOrchestrator(router=router)
        summary = await orchestrator.run(
            run_id=run_id,
            job_id=job.id,
            candidate_ids=[c.id for c in candidates],
        )

        assert summary.total == 5
        assert summary.passed == 5
        assert summary.disqualified == 0
        assert summary.failed == 0

        # 进度事件：1 started + 5*2 progress (score+interview) + 1 done = 12
        events = progress_store.get_events_after(run_id, -1)
        # 5 个候选人，每个完成时仅写 1 条 progress（最后阶段 interview）
        # filter pass → 不写；score ok → 1；interview ok → 1
        # 实际数：1 started + 5*2 + 1 done = 12
        assert len(events) == 12

        # DB：5 score + 25 interview_questions（5 候选人 × 5 题）
        async with AsyncSessionLocal() as session:
            scores = (await session.execute(
                select(Score).where(Score.job_id == job.id)
            )).scalars().all()
            questions = (await session.execute(
                select(InterviewQuestion).where(
                    InterviewQuestion.job_id == job.id
                )
            )).scalars().all()
        assert len(scores) == 5
        assert len(questions) == 25

        progress_store.drop(run_id)


class TestOrchestratorDisqualify:
    async def test_run_with_one_disqualified(self, monkeypatch) -> None:
        """1 个候选人被 filter 淘汰 → 4 score + 4 interview。"""
        team, user, job, candidates = await _seed_team_job_and_candidates(
            n_candidates=5,
            add_hard_requirement=True,
            disqualify_one_via_requirement=True,
        )
        router = _make_smart_router()
        run_id = uuid.uuid4()
        await progress_store.create(run_id, total=5)

        orchestrator = ScreeningOrchestrator(router=router)
        summary = await orchestrator.run(
            run_id=run_id,
            job_id=job.id,
            candidate_ids=[c.id for c in candidates],
        )

        assert summary.disqualified == 1
        assert summary.passed == 4
        assert summary.failed == 0

        async with AsyncSessionLocal() as session:
            scores = (await session.execute(
                select(Score).where(Score.job_id == job.id)
            )).scalars().all()
        assert len(scores) == 4  # 被淘汰的不调 score

        progress_store.drop(run_id)


class TestOrchestratorScoreFailure:
    async def test_run_with_score_failure_does_not_block(self, monkeypatch) -> None:
        """score 失败 1 个 → 该候选人标 failed；其他 4 个仍 score + interview。"""
        team, user, job, candidates = await _seed_team_job_and_candidates(
            n_candidates=5
        )
        router = _make_smart_router()

        # monkeypatch run_score：对第 3 个 candidate 抛 StructureMissing
        from app.workers import scorer_task as st_mod
        original_run_score = st_mod.run_score

        async def _failing_run_score(*, db, target_id, payload, **kw):
            if target_id == candidates[2].id:
                raise st_mod.StructureMissing("forced failure")
            return await original_run_score(
                db=db, target_id=target_id, payload=payload, **kw
            )

        monkeypatch.setattr(
            "app.services.screening_orchestrator.run_score",
            _failing_run_score,
        )

        run_id = uuid.uuid4()
        await progress_store.create(run_id, total=5)

        orchestrator = ScreeningOrchestrator(router=router)
        summary = await orchestrator.run(
            run_id=run_id,
            job_id=job.id,
            candidate_ids=[c.id for c in candidates],
        )

        assert summary.passed == 4
        assert summary.failed == 1
        assert any(
            r["stage"] == "score" for r in summary.failed_reasons
        )

        progress_store.drop(run_id)


class TestOrchestratorInterviewFailure:
    async def test_run_with_interview_failure_keeps_score(self, monkeypatch) -> None:
        """interview 失败 → score 已写入；标 failed；不阻塞。"""
        team, user, job, candidates = await _seed_team_job_and_candidates(
            n_candidates=3
        )
        router = _make_smart_router()

        # interview 第 1 次失败（candidate 1 的 interview）
        original_chat = router.adapters["mock"].chat

        call_count = {"n": 0}

        class _FailInterviewAdapter:
            name = "mock"
            default_model = "mock-model"

            async def chat(
                self, *, messages, response_schema, temperature, timeout, model
            ):
                call_count["n"] += 1
                # candidate 1: score(1) + reason(2) + interview(3) → 第 3 次失败
                if response_schema is InterviewQuestions and call_count["n"] == 3:
                    from app.adapters.llm import LLMSchemaError
                    raise LLMSchemaError("forced interview failure")
                return await original_chat(
                    messages=messages,
                    response_schema=response_schema,
                    temperature=temperature,
                    timeout=timeout,
                    model=model,
                )

        router.adapters["mock"] = _FailInterviewAdapter()

        run_id = uuid.uuid4()
        await progress_store.create(run_id, total=3)

        orchestrator = ScreeningOrchestrator(router=router)
        summary = await orchestrator.run(
            run_id=run_id,
            job_id=job.id,
            candidate_ids=[c.id for c in candidates],
        )

        assert summary.passed == 2
        assert summary.failed == 1
        # score 全部写入（interview 失败不阻塞）
        async with AsyncSessionLocal() as session:
            scores = (await session.execute(
                select(Score).where(Score.job_id == job.id)
            )).scalars().all()
        assert len(scores) == 3

        progress_store.drop(run_id)


class TestOrchestratorEmpty:
    async def test_run_empty_candidates(self) -> None:
        team, user, job, _ = await _seed_team_job_and_candidates(n_candidates=0)
        run_id = uuid.uuid4()
        await progress_store.create(run_id, total=0)

        orchestrator = ScreeningOrchestrator()
        summary = await orchestrator.run(
            run_id=run_id,
            job_id=job.id,
            candidate_ids=[],
        )

        assert summary.total == 0
        assert summary.passed == 0
        assert progress_store.is_done(run_id)
        progress_store.drop(run_id)
