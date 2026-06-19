"""/api/screening/pipeline 路由 + SSE 集成测试（任务 20）。"""
from __future__ import annotations

import asyncio
import json
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.adapters.llm import LLMResponse
from app.adapters.llm.router import LLMRouter
from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.interview import InterviewQuestion
from app.models.job import Job
from app.models.score import Score
from app.models.team import Team
from app.models.user import User
from app.schemas.interview import InterviewQuestions
from app.schemas.reason import RecommendReasons
from app.schemas.score import ScoreDimensions
from app.services.interview import InterviewService
from app.services.screening_orchestrator import progress_store

# ============================================================================
# DB 清理 / fixtures
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


@pytest.fixture
async def client():
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        yield ac


@pytest.fixture(autouse=True)
async def clean_db():
    await _purge_db()
    # 清进度存储（防 cross-test 污染）
    for run_id in list(progress_store._events.keys()):
        progress_store.drop(run_id)
    yield
    await _purge_db()
    for run_id in list(progress_store._events.keys()):
        progress_store.drop(run_id)


# ============================================================================
# Mock LLM router（patch InterviewService + orchestrator）
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
        "bullet_points": ["Python 技能匹配", "FastAPI 经验", "5 年经验"],
        "evidence": ["python", "fastapi", "5年"],
    },
    ensure_ascii=False,
)
_VALID_QUESTIONS_JSON = json.dumps(
    {
        "questions": [
            {"dimension": "skill", "question": "Python 项目深聊"},
            {"dimension": "project", "question": "技术项目"},
            {"dimension": "weakness", "question": "为什么没云经验"},
            {"dimension": "weakness", "question": "项目数偏少"},
            {"dimension": "culture", "question": "团队协作"},
        ]
    },
    ensure_ascii=False,
)


def _make_router() -> LLMRouter:
    class _SmartMock:
        name = "mock"
        default_model = "mock-model"

        async def chat(self, *, messages, response_schema, temperature, timeout, model):
            if response_schema is ScoreDimensions:
                content = _VALID_SCORES_JSON
            elif response_schema is RecommendReasons:
                content = _VALID_REASONS_JSON
            elif response_schema is InterviewQuestions:
                content = _VALID_QUESTIONS_JSON
            else:
                content = "{}"
            parsed = response_schema.model_validate_json(content)
            return LLMResponse(
                content=content, adapter="mock", model="mock-model",
                parsed=parsed,
            )

    return LLMRouter(
        adapters={"mock": _SmartMock()},
        default_primary="mock",
        default_fallback=None,
    )


def _patch_router(monkeypatch) -> LLMRouter:
    router = _make_router()

    def _fake_get_router(self):  # noqa: ANN001
        return router

    monkeypatch.setattr(InterviewService, "_get_router", _fake_get_router)
    return router


# ============================================================================
# 工具
# ============================================================================


async def _register_admin(
    client: AsyncClient, email: str = "admin@example.com"
) -> dict:
    resp = await client.post(
        "/api/auth/register",
        json={"email": email, "password": "Pass1234", "name": "Admin"},
    )
    body = resp.json()
    return {
        "token": body["tokens"]["access_token"],
        "team_id": body["user"]["team_id"],
        "user_id": body["user"]["id"],
    }


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


async def _seed_candidates(
    team_id: str, n: int = 3
) -> tuple[str, list[str]]:
    async with AsyncSessionLocal() as session:
        team_id_uuid = uuid.UUID(team_id)
        user = User(
            email=f"u-{uuid.uuid4().hex[:6]}@x.com",
            password_hash="x",
            name="hr",
            team_id=team_id_uuid,
        )
        session.add(user)
        await session.flush()
        job = Job(
            team_id=team_id_uuid,
            title="Eng", jd_text="Python", status="active",
            created_by=user.id,
        )
        session.add(job)
        await session.flush()
        cands: list[str] = []
        for i in range(n):
            c = Candidate(
                team_id=team_id_uuid,
                dedup_key=f"t:{uuid.uuid4()}",
                name=f"c-{i}",
            )
            session.add(c)
            await session.flush()
            src = CandidateSource(candidate_id=c.id, source_type="upload")
            session.add(src)
            await session.flush()
            r = CandidateResume(
                candidate_id=c.id, source_id=src.id,
                file_storage_key=f"k-{i}",
                file_mime="application/pdf",
                parse_status="success",
                parsed_text=f"Python 与 FastAPI {i+1}年经验",
            )
            session.add(r)
            await session.flush()
            session.add(ParsedStructure(
                resume_id=r.id,
                data={
                    "structure": {
                        "name": f"c-{i}", "name_confidence": 0.9,
                        "phone": "13800138000", "phone_confidence": 0.9,
                        "email": f"c{i}@x.com", "email_confidence": 0.9,
                        "education": "master", "education_confidence": 0.85,
                        "years_of_experience": 5,
                        "years_of_experience_confidence": 0.8,
                        "skills": ["Python"],
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
            cands.append(str(c.id))
        await session.commit()
        return str(job.id), cands


# ============================================================================
# POST /pipeline（异步触发）
# ============================================================================


async def _wait_for_done(run_id: str, timeout: float = 30.0) -> bool:
    deadline = asyncio.get_event_loop().time() + timeout
    while asyncio.get_event_loop().time() < deadline:
        if progress_store.is_done(uuid.UUID(run_id)):
            return True
        await asyncio.sleep(0.05)
    return False


class TestTriggerPipeline:
    async def test_trigger_returns_run_id(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """trigger 返回 202 + run_id；DB 落地由测试手动驱动 orchestrator。"""
        admin = await _register_admin(client)
        router = _patch_router(monkeypatch)
        job_id, cand_ids = await _seed_candidates(admin["team_id"], n=3)

        # ASGITransport 会同步等待 BackgroundTasks；patch 成 noop 让 endpoint 立即返回，
        # 流水线由测试代码手动驱动（用 mock router）
        from app.api import screening as screening_mod

        async def _noop_background(*args, **kwargs):
            return None

        monkeypatch.setattr(
            screening_mod, "_run_pipeline_in_background", _noop_background
        )

        resp = await client.post(
            "/api/screening/pipeline",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": cand_ids},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert "run_id" in body
        assert body["total"] == 3
        assert body["job_id"] == job_id

        # 手动驱动 orchestrator 完成 pipeline（与生产 background 路径一致）
        run_id = uuid.UUID(body["run_id"])
        from app.services.screening_orchestrator import ScreeningOrchestrator
        orchestrator = ScreeningOrchestrator(router=router)
        await orchestrator.run(
            run_id=run_id,
            job_id=uuid.UUID(job_id),
            candidate_ids=[uuid.UUID(c) for c in cand_ids],
        )

        # DB：3 score + 15 interview questions
        async with AsyncSessionLocal() as session:
            scores = (await session.execute(
                select(Score).where(Score.job_id == uuid.UUID(job_id))
            )).scalars().all()
            questions = (await session.execute(
                select(InterviewQuestion).where(
                    InterviewQuestion.job_id == uuid.UUID(job_id)
                )
            )).scalars().all()
        assert len(scores) == 3
        assert len(questions) == 15

    async def test_trigger_unauthorized(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/screening/pipeline",
            json={"job_id": str(uuid.uuid4()), "candidate_ids": [str(uuid.uuid4())]},
        )
        assert resp.status_code == 401

    async def test_trigger_cross_team_404(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        async with AsyncSessionLocal() as session:
            other_team = Team(name=f"t-{uuid.uuid4().hex[:6]}")
            session.add(other_team)
            await session.flush()
            user2 = User(
                email=f"u-{uuid.uuid4().hex[:6]}@x.com",
                password_hash="x", name="hr2", team_id=other_team.id,
            )
            session.add(user2)
            await session.flush()
            job = Job(
                team_id=other_team.id, title="T", jd_text="x",
                status="active", created_by=user2.id,
            )
            session.add(job)
            await session.commit()
            job_id = str(job.id)

        resp = await client.post(
            "/api/screening/pipeline",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": [str(uuid.uuid4())]},
        )
        assert resp.status_code == 404


# ============================================================================
# GET /pipeline/{run_id}/summary
# ============================================================================


class TestPipelineSummary:
    async def test_get_summary_after_done(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        router = _patch_router(monkeypatch)
        job_id, cand_ids = await _seed_candidates(admin["team_id"], n=2)

        from app.api import screening as screening_mod

        async def _noop_background(*args, **kwargs):
            return None

        monkeypatch.setattr(
            screening_mod, "_run_pipeline_in_background", _noop_background
        )

        # trigger 返回 run_id
        resp = await client.post(
            "/api/screening/pipeline",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": cand_ids},
        )
        run_id = resp.json()["run_id"]

        # 手动驱动 orchestrator
        from app.services.screening_orchestrator import ScreeningOrchestrator
        orchestrator = ScreeningOrchestrator(router=router)
        await orchestrator.run(
            run_id=uuid.UUID(run_id),
            job_id=uuid.UUID(job_id),
            candidate_ids=[uuid.UUID(c) for c in cand_ids],
        )

        summary = await client.get(
            f"/api/screening/pipeline/{run_id}/summary",
            headers=_auth(admin["token"]),
        )
        assert summary.status_code == 200
        body = summary.json()
        assert body["total"] == 2
        assert body["passed"] == 2


# ============================================================================
# GET /pipeline/{run_id}/events（SSE）
# ============================================================================


class TestPipelineSSE:
    async def test_sse_resume_via_last_event_id(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """断线重连：run 先跑完，再 SSE 重连取后续事件。"""
        admin = await _register_admin(client)
        router = _patch_router(monkeypatch)
        job_id, cand_ids = await _seed_candidates(admin["team_id"], n=2)

        from app.api import screening as screening_mod

        async def _noop_background(*args, **kwargs):
            return None

        monkeypatch.setattr(
            screening_mod, "_run_pipeline_in_background", _noop_background
        )

        trigger = await client.post(
            "/api/screening/pipeline",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": cand_ids},
        )
        run_id = trigger.json()["run_id"]

        # 手动跑 orchestrator
        from app.services.screening_orchestrator import ScreeningOrchestrator
        orchestrator = ScreeningOrchestrator(router=router)
        await orchestrator.run(
            run_id=uuid.UUID(run_id),
            job_id=uuid.UUID(job_id),
            candidate_ids=[uuid.UUID(c) for c in cand_ids],
        )

        # 客户端假装只看到 event_id=0（started）；从 1 开始重连
        async with client.stream(
            "GET",
            f"/api/screening/pipeline/{run_id}/events",
            headers={
                **_auth(admin["token"]),
                "Last-Event-ID": "0",
            },
        ) as response:
            assert response.status_code == 200
            received_ids: list[int] = []
            async for line in response.aiter_lines():
                if line.startswith("id: "):
                    received_ids.append(int(line[4:].strip()))

        # 重连后从 event_id=1 开始
        assert min(received_ids) >= 1
        # 事件总数 = 1 started + 4 progress (2 cands × 2) + 1 done = 6
        # 从 1 开始应到 5（done 的 id）
        assert max(received_ids) >= 5

    async def test_sse_unknown_run_returns_empty(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """未知 run_id → SSE 立即结束（progress_store.has_run=False 早退）。"""
        admin = await _register_admin(client)

        async with client.stream(
            "GET",
            f"/api/screening/pipeline/{uuid.uuid4()}/events",
            headers=_auth(admin["token"]),
        ) as response:
            assert response.status_code == 200
            lines: list[str] = []
            async for line in response.aiter_lines():
                lines.append(line)
            # 未知 run_id 立即关闭流，无任何事件 / ping
            assert lines == []
