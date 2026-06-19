"""/api/screening 路由集成测试（任务 16）。"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.job import Job, JobHardRequirement
from app.models.screening import ScreeningResult

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


async def _seed_full_candidate(
    team_id: str,
    *,
    education: str | None = "master",
    years_of_experience: int | None = 5,
    skills: list[str] | None = None,
    current_company: str | None = "GoodCorp",
) -> str:
    """直接写库创建 candidate，返回 id。"""
    async with AsyncSessionLocal() as session:
        c = Candidate(
            team_id=uuid.UUID(team_id),
            dedup_key=f"test:{uuid.uuid4()}",
            name="张三",
            email="zs@x.com",
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
                        "education": education, "education_confidence": 0.85,
                        "years_of_experience": years_of_experience,
                        "years_of_experience_confidence": 0.8,
                        "skills": skills or ["Python"], "skills_confidence": 0.9,
                        "expected_salary": None, "expected_salary_confidence": 0.0,
                        "current_company": current_company,
                        "current_company_confidence": 0.85,
                        "work_history": [], "work_history_confidence": 0.0,
                    },
                    "status": "extracted",
                },
            )
        )
        await session.commit()
        return str(c.id)


def _default_structure_payload(
    *,
    education: str | None = "master",
    years_of_experience: int | None = 5,
    skills: list[str] | None = None,
) -> dict:
    return {
        "education": education,
        "years_of_experience": years_of_experience,
        "skills": skills or ["Python"],
        "current_company": "GoodCorp",
    }


# ============================================================================
# 测试
# ============================================================================


class TestRunScreening:
    async def test_run_with_no_requirements_all_pass(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        # 创建 job（无 hard_requirements）
        job_resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "T", "jd_text": "x", "status": "draft"},
        )
        job_id = job_resp.json()["id"]

        cand_id = await _seed_full_candidate(admin["team_id"])

        resp = await client.post(
            "/api/screening/run",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": [cand_id]},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["processed"] == 1
        assert body["passed"] == 1
        assert body["disqualified"] == 0

    async def test_run_disqualified_candidate(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        job_resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={
                "title": "Eng",
                "jd_text": "x",
                "status": "draft",
                "hard_requirements": {
                    "min_education": "master",
                    "min_years": 10,
                    "required_skills": ["Rust"],
                },
            },
        )
        job_id = job_resp.json()["id"]

        # 候选人不达标
        cand_id = await _seed_full_candidate(
            admin["team_id"],
            education="bachelor",
            years_of_experience=2,
            skills=["Python"],
        )

        resp = await client.post(
            "/api/screening/run",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": [cand_id]},
        )
        assert resp.status_code == 200
        assert resp.json()["disqualified"] == 1

    async def test_run_unknown_job_returns_404(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        resp = await client.post(
            "/api/screening/run",
            headers=_auth(admin["token"]),
            json={"job_id": str(uuid.uuid4()), "candidate_ids": []},
        )
        assert resp.status_code == 404

    async def test_run_cross_team_candidate_filtered(
        self, client: AsyncClient
    ) -> None:
        """跨 team candidate id 应被过滤掉。"""
        admin = await _register_admin(client)
        job_resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "T", "jd_text": "x"},
        )
        job_id = job_resp.json()["id"]

        # 传一个不属于当前 team 的 candidate id
        resp = await client.post(
            "/api/screening/run",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": [str(uuid.uuid4())]},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["processed"] == 0  # 跨 team 被过滤


class TestListResults:
    async def test_list_empty(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job_resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "T", "jd_text": "x"},
        )
        job_id = job_resp.json()["id"]

        resp = await client.get(
            "/api/screening/results",
            headers=_auth(admin["token"]),
            params={"job_id": job_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    async def test_list_returns_items_with_names(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        job_resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "T", "jd_text": "x"},
        )
        job_id = job_resp.json()["id"]
        cand_id = await _seed_full_candidate(admin["team_id"])

        await client.post(
            "/api/screening/run",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": [cand_id]},
        )

        resp = await client.get(
            "/api/screening/results",
            headers=_auth(admin["token"]),
            params={"job_id": job_id},
        )
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["candidate_name"] == "张三"
        assert body["items"][0]["candidate_id"] == cand_id


class TestOverride:
    async def test_override_writes_audit(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job_resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={
                "title": "T",
                "jd_text": "x",
                "hard_requirements": {"min_education": "master"},
            },
        )
        job_id = job_resp.json()["id"]
        # 候选人学历不达标
        cand_id = await _seed_full_candidate(
            admin["team_id"], education="bachelor"
        )
        await client.post(
            "/api/screening/run",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": [cand_id]},
        )

        # 取 result id
        list_resp = await client.get(
            "/api/screening/results",
            headers=_auth(admin["token"]),
            params={"job_id": job_id},
        )
        sr_id = list_resp.json()["items"][0]["id"]
        assert list_resp.json()["items"][0]["disqualified"] is True

        # 改判
        resp = await client.patch(
            f"/api/screening/results/{sr_id}/override",
            headers=_auth(admin["token"]),
            json={
                "new_disqualified": False,
                "new_reasons": ["HR 确认符合"],
                "reason": "候选人学历符合实际",
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["screening_result"]["disqualified"] is False
        assert body["screening_result"]["manually_overridden"] is True
        assert "override_id" in body

    async def test_override_empty_reason_rejected(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        job_resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "T", "jd_text": "x"},
        )
        job_id = job_resp.json()["id"]
        cand_id = await _seed_full_candidate(admin["team_id"])
        await client.post(
            "/api/screening/run",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": [cand_id]},
        )
        sr_id = (
            (
                await client.get(
                    "/api/screening/results",
                    headers=_auth(admin["token"]),
                    params={"job_id": job_id},
                )
            )
            .json()["items"][0]["id"]
        )

        # 空 reason 触发 422
        resp = await client.patch(
            f"/api/screening/results/{sr_id}/override",
            headers=_auth(admin["token"]),
            json={
                "new_disqualified": True,
                "new_reasons": None,
                "reason": "",
            },
        )
        assert resp.status_code == 422

    async def test_override_unknown_result_404(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        resp = await client.patch(
            f"/api/screening/results/{uuid.uuid4()}/override",
            headers=_auth(admin["token"]),
            json={
                "new_disqualified": False,
                "new_reasons": [],
                "reason": "x",
            },
        )
        assert resp.status_code == 404

    async def test_list_overrides(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job_resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "T", "jd_text": "x"},
        )
        job_id = job_resp.json()["id"]
        cand_id = await _seed_full_candidate(admin["team_id"])
        await client.post(
            "/api/screening/run",
            headers=_auth(admin["token"]),
            json={"job_id": job_id, "candidate_ids": [cand_id]},
        )
        sr_id = (
            (
                await client.get(
                    "/api/screening/results",
                    headers=_auth(admin["token"]),
                    params={"job_id": job_id},
                )
            )
            .json()["items"][0]["id"]
        )

        # 改判一次
        await client.patch(
            f"/api/screening/results/{sr_id}/override",
            headers=_auth(admin["token"]),
            json={
                "new_disqualified": True,
                "new_reasons": ["r"],
                "reason": "test",
            },
        )

        resp = await client.get(
            f"/api/screening/results/{sr_id}/overrides",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 1
        assert body[0]["reason"] == "test"


class TestAuth:
    async def test_endpoints_require_auth(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/screening/run",
            json={"job_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 401

        resp = await client.get("/api/screening/results", params={"job_id": str(uuid.uuid4())})
        assert resp.status_code == 401
