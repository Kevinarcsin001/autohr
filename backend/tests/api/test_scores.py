"""/api/scores 路由集成测试（任务 17）。"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.adapters.llm import MockAdapter
from app.adapters.llm.router import LLMRouter
from app.core.db import AsyncSessionLocal
from app.main import app
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
from app.services.scorer import ScorerService

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


# ============================================================================
# 工具
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
    yield
    await _purge_db()


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


async def _seed_candidate_with_structure(
    team_id: str,
    *,
    name: str = "张三",
    education: str | None = "master",
    years_of_experience: int | None = 5,
    skills: list[str] | None = None,
    parsed_text: str | None = "Python 5 年经验",
) -> str:
    """直接写库创建 candidate + resume + parsed_structure，返回 candidate id。"""
    async with AsyncSessionLocal() as session:
        c = Candidate(
            team_id=uuid.UUID(team_id),
            dedup_key=f"test:{uuid.uuid4()}",
            name=name,
            email=f"u-{uuid.uuid4().hex[:6]}@x.com",
        )
        session.add(c)
        await session.flush()
        src = CandidateSource(candidate_id=c.id, source_type="upload")
        session.add(src)
        await session.flush()
        resume = CandidateResume(
            candidate_id=c.id,
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
                        "name": name, "name_confidence": 0.9,
                        "phone": "13800138000", "phone_confidence": 0.9,
                        "email": "zs@x.com", "email_confidence": 0.9,
                        "education": education,
                        "education_confidence": 0.85,
                        "years_of_experience": years_of_experience,
                        "years_of_experience_confidence": 0.8,
                        "skills": skills or ["Python"],
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
        return str(c.id)


def _patch_router_with_mock() -> tuple[MockAdapter, LLMRouter]:
    """覆盖 ScorerService 默认 router 的最简办法：返回单 mock adapter 路由。

    用法：在测试中通过 monkeypatch ScorerService._get_router 返回此 router。
    """
    mock = MockAdapter(
        response_override=_VALID_SCORES_JSON,
        name="mock",
        default_model="mock-model",
    )
    router = LLMRouter(
        adapters={"mock": mock},
        default_primary="mock",
        default_fallback=None,
    )
    return mock, router


# ============================================================================
# 测试
# ============================================================================


class TestRunScores:
    async def test_run_with_valid_candidate_writes_score(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        job_resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "Eng", "jd_text": "招聘 Python", "status": "draft"},
        )
        job_id = job_resp.json()["id"]
        cand_id = await _seed_candidate_with_structure(admin["team_id"])

        # 替换默认 router（_get_router 是同步方法）
        mock, router = _patch_router_with_mock()
        def _fake_get_router(self):  # noqa: ANN001
            return router
        monkeypatch.setattr(ScorerService, "_get_router", _fake_get_router)

        resp = await client.post(
            "/api/scores/run",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": [cand_id]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["processed"] == 1
        assert body["failed"] == 0

    async def test_run_unknown_job_404(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        resp = await client.post(
            "/api/scores/run",
            headers=_auth(admin["token"]),
            json={"job_id": str(uuid.uuid4()), "candidate_ids": []},
        )
        assert resp.status_code == 404

    async def test_run_cross_team_candidate_filtered(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        job_resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "T", "jd_text": "x"},
        )
        job_id = job_resp.json()["id"]
        # 传一个不属于当前 team 的 candidate id
        resp = await client.post(
            "/api/scores/run",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": [str(uuid.uuid4())]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["processed"] == 0
        assert body["failed"] == 0

    async def test_run_no_structure_marks_failed(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        job_resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "T", "jd_text": "x"},
        )
        job_id = job_resp.json()["id"]
        # 直接创建候选人但无 ParsedStructure
        async with AsyncSessionLocal() as session:
            c = Candidate(
                team_id=uuid.UUID(admin["team_id"]),
                dedup_key=f"test:{uuid.uuid4()}",
                name="无名",
            )
            session.add(c)
            await session.commit()
            cand_id = str(c.id)

        mock, router = _patch_router_with_mock()
        def _fake_get_router(self):  # noqa: ANN001
            return router
        monkeypatch.setattr(ScorerService, "_get_router", _fake_get_router)

        resp = await client.post(
            "/api/scores/run",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": [cand_id]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["processed"] == 0
        assert body["failed"] == 1


class TestListScores:
    async def test_list_empty(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job_resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "T", "jd_text": "x"},
        )
        job_id = job_resp.json()["id"]

        resp = await client.get(
            "/api/scores",
            headers=_auth(admin["token"]),
            params={"job_id": job_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    async def test_list_returns_items_sorted(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        job_resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "T", "jd_text": "x"},
        )
        job_id = job_resp.json()["id"]

        # 直接写 Score 行
        async with AsyncSessionLocal() as session:
            for total, name in [(70, "低分"), (90, "高分")]:
                c = Candidate(
                    team_id=uuid.UUID(admin["team_id"]),
                    dedup_key=f"test:{uuid.uuid4()}",
                    name=name,
                )
                session.add(c)
                await session.flush()
                session.add(Score(
                    job_id=uuid.UUID(job_id),
                    candidate_id=c.id,
                    total=total, skill=total, experience=total,
                    education=total, stability=total, potential=total,
                    model_used="mock",
                ))
            await session.commit()

        resp = await client.get(
            "/api/scores",
            headers=_auth(admin["token"]),
            params={"job_id": job_id},
        )
        body = resp.json()
        assert body["total"] == 2
        # 高分在前
        assert body["items"][0]["candidate_name"] == "高分"
        assert body["items"][0]["total"] == 90
        assert body["items"][1]["candidate_name"] == "低分"

    async def test_list_cross_team_404(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        # 直接构造一个不存在的 job_id（不属于当前 team）
        resp = await client.get(
            "/api/scores",
            headers=_auth(admin["token"]),
            params={"job_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 404


class TestAuth:
    async def test_endpoints_require_auth(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/scores/run",
            json={"job_id": str(uuid.uuid4()), "candidate_ids": []},
        )
        assert resp.status_code == 401

        resp = await client.get(
            "/api/scores",
            params={"job_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 401
