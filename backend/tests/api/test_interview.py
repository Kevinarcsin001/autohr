"""/api/interview 路由集成测试（任务 19）。

覆盖：
- POST /generate 生成 5 题
- POST /regenerate 重新生成（temperature=0.8，保留历史）
- GET /questions 列出（最新 batch / 指定 batch_id）
- GET /batches 列出所有 batch
- POST /questions/{id}/feedback 写反馈 + upsert
- GET /questions/{id}/feedback 列反馈
- 跨 team 返回 404
"""
from __future__ import annotations

import json
import uuid

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
from app.models.team import Team
from app.models.user import User
from app.schemas.interview import InterviewQuestions
from app.services.interview import InterviewService

# ============================================================================
# DB 清理 / client
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


# ============================================================================
# 工具
# ============================================================================


_VALID_QUESTIONS_JSON = json.dumps(
    {
        "questions": [
            {"dimension": "skill", "question": "请深入聊聊你使用 Python 的项目"},
            {"dimension": "project", "question": "讲一个你主导过的技术项目"},
            {"dimension": "weakness", "question": "为什么没有云相关经验？"},
            {"dimension": "weakness", "question": "项目数量偏少，能详细说说吗？"},
            {"dimension": "culture", "question": "你怎么看待团队协作？"},
        ]
    },
    ensure_ascii=False,
)


def _make_smart_router() -> LLMRouter:
    """按 response_schema 类型返回不同 JSON 的智能 mock。"""

    class _SmartMock:
        name = "mock"
        default_model = "mock-model"

        async def chat(self, *, messages, response_schema, temperature, timeout, model):
            if response_schema is InterviewQuestions:
                content = _VALID_QUESTIONS_JSON
            else:
                content = "{}"
            parsed = response_schema.model_validate_json(content)
            return LLMResponse(
                content=content, adapter="mock", model="mock-model", parsed=parsed,
            )

    return LLMRouter(
        adapters={"mock": _SmartMock()},
        default_primary="mock",
        default_fallback=None,
    )


def _patch_router(monkeypatch) -> None:
    """覆盖 InterviewService._get_router 用智能 mock。"""
    router = _make_smart_router()

    def _fake_get_router(self):  # noqa: ANN001
        return router

    monkeypatch.setattr(InterviewService, "_get_router", _fake_get_router)


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


async def _seed_job_and_candidate(team_id: str) -> tuple[str, str]:
    """创建 job + candidate + resume + parsed_structure，返回 (job_id, candidate_id)。"""
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
            title="Eng",
            jd_text="招聘 Python 工程师",
            status="active",
            created_by=user.id,
        )
        session.add(job)
        await session.flush()
        candidate = Candidate(
            team_id=team_id_uuid,
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
            parsed_text="Python 与 FastAPI 5年经验",
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
            )
        )
        await session.commit()
        return str(job.id), str(candidate.id)


# ============================================================================
# Tests: POST /generate
# ============================================================================


class TestGenerate:
    async def test_generate_returns_5_questions(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        _patch_router(monkeypatch)
        job_id, cand_id = await _seed_job_and_candidate(admin["team_id"])

        resp = await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["is_regeneration"] is False
        assert body["temperature"] == 0.3
        assert len(body["questions"]) == 5
        # 至少 1 条 weakness
        assert any(q["dimension"] == "weakness" for q in body["questions"])
        # batch_id 非空
        assert body["batch_id"]

    async def test_generate_candidate_not_found_404(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        _patch_router(monkeypatch)
        _, _ = await _seed_job_and_candidate(admin["team_id"])
        job_id = (await _seed_job_and_candidate(admin["team_id"]))[0]

        resp = await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": str(uuid.uuid4()), "job_id": job_id},
        )
        assert resp.status_code == 404

    async def test_generate_unauthorized_401(self, client: AsyncClient) -> None:
        resp = await client.post(
            "/api/interview/generate",
            json={"candidate_id": str(uuid.uuid4()), "job_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 401


# ============================================================================
# Tests: POST /regenerate
# ============================================================================


class TestRegenerate:
    async def test_regenerate_creates_new_batch(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        _patch_router(monkeypatch)
        job_id, cand_id = await _seed_job_and_candidate(admin["team_id"])

        first = await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        assert first.status_code == 200
        first_batch = first.json()["batch_id"]

        second = await client.post(
            "/api/interview/regenerate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        assert second.status_code == 200
        second_body = second.json()
        assert second_body["is_regeneration"] is True
        assert second_body["temperature"] == 0.8
        assert second_body["batch_id"] != first_batch
        assert len(second_body["questions"]) == 5


# ============================================================================
# Tests: GET /questions + /batches
# ============================================================================


class TestList:
    async def test_list_questions_returns_latest_batch(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        _patch_router(monkeypatch)
        job_id, cand_id = await _seed_job_and_candidate(admin["team_id"])

        first = await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        first_batch = first.json()["batch_id"]
        await client.post(
            "/api/interview/regenerate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )

        resp = await client.get(
            "/api/interview/questions",
            headers=_auth(admin["token"]),
            params={"candidate_id": cand_id, "job_id": job_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        # 最新 batch（regenerate 的）
        assert all(q["batch_id"] != first_batch for q in body["items"])

    async def test_list_questions_specific_batch(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        _patch_router(monkeypatch)
        job_id, cand_id = await _seed_job_and_candidate(admin["team_id"])

        first = await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        first_batch = first.json()["batch_id"]
        await client.post(
            "/api/interview/regenerate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )

        resp = await client.get(
            "/api/interview/questions",
            headers=_auth(admin["token"]),
            params={
                "candidate_id": cand_id,
                "job_id": job_id,
                "batch_id": first_batch,
            },
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        # 全部属于第一批
        assert all(q["batch_id"] == first_batch for q in body["items"])

    async def test_list_questions_empty(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job_id, cand_id = await _seed_job_and_candidate(admin["team_id"])

        resp = await client.get(
            "/api/interview/questions",
            headers=_auth(admin["token"]),
            params={"candidate_id": cand_id, "job_id": job_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 0
        assert body["items"] == []

    async def test_list_batches_returns_all(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        _patch_router(monkeypatch)
        job_id, cand_id = await _seed_job_and_candidate(admin["team_id"])

        await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        await client.post(
            "/api/interview/regenerate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )

        resp = await client.get(
            "/api/interview/batches",
            headers=_auth(admin["token"]),
            params={"candidate_id": cand_id, "job_id": job_id},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body["batches"]) == 2
        assert body["total_questions"] == 10
        assert body["current_batch"] == body["batches"][0]


# ============================================================================
# Tests: POST /questions/{id}/feedback
# ============================================================================


class TestSaveFeedback:
    async def test_save_feedback_creates(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        _patch_router(monkeypatch)
        job_id, cand_id = await _seed_job_and_candidate(admin["team_id"])

        gen = await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        question_id = gen.json()["questions"][0]["id"]

        resp = await client.post(
            f"/api/interview/questions/{question_id}/feedback",
            headers=_auth(admin["token"]),
            json={"feedback": "回答清晰", "rating": 4},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["feedback"]["feedback"] == "回答清晰"
        assert body["feedback"]["rating"] == 4
        assert body["feedback"]["reviewer_id"] == admin["user_id"]

    async def test_save_feedback_upsert_overwrites(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        _patch_router(monkeypatch)
        job_id, cand_id = await _seed_job_and_candidate(admin["team_id"])

        gen = await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        question_id = gen.json()["questions"][0]["id"]

        # 第一次
        await client.post(
            f"/api/interview/questions/{question_id}/feedback",
            headers=_auth(admin["token"]),
            json={"feedback": "原始", "rating": 3},
        )
        # 第二次覆盖
        resp = await client.post(
            f"/api/interview/questions/{question_id}/feedback",
            headers=_auth(admin["token"]),
            json={"feedback": "更新", "rating": 5},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["feedback"]["feedback"] == "更新"
        assert body["feedback"]["rating"] == 5

    async def test_save_feedback_only_rating(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        _patch_router(monkeypatch)
        job_id, cand_id = await _seed_job_and_candidate(admin["team_id"])

        gen = await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        question_id = gen.json()["questions"][0]["id"]

        resp = await client.post(
            f"/api/interview/questions/{question_id}/feedback",
            headers=_auth(admin["token"]),
            json={"rating": 4},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["feedback"]["rating"] == 4
        assert body["feedback"]["feedback"] is None

    async def test_save_feedback_requires_field(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """feedback + rating 都为空 → 422。"""
        admin = await _register_admin(client)
        _patch_router(monkeypatch)
        job_id, cand_id = await _seed_job_and_candidate(admin["team_id"])

        gen = await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        question_id = gen.json()["questions"][0]["id"]

        resp = await client.post(
            f"/api/interview/questions/{question_id}/feedback",
            headers=_auth(admin["token"]),
            json={},
        )
        assert resp.status_code == 422

    async def test_save_feedback_invalid_rating(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """rating > 5 → 422。"""
        admin = await _register_admin(client)
        _patch_router(monkeypatch)
        job_id, cand_id = await _seed_job_and_candidate(admin["team_id"])

        gen = await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        question_id = gen.json()["questions"][0]["id"]

        resp = await client.post(
            f"/api/interview/questions/{question_id}/feedback",
            headers=_auth(admin["token"]),
            json={"rating": 6},
        )
        assert resp.status_code == 422


# ============================================================================
# Tests: GET /questions/{id}/feedback
# ============================================================================


class TestListFeedback:
    async def test_list_feedback_returns_desc(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        admin = await _register_admin(client)
        _patch_router(monkeypatch)
        job_id, cand_id = await _seed_job_and_candidate(admin["team_id"])

        gen = await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        question_id = gen.json()["questions"][0]["id"]

        await client.post(
            f"/api/interview/questions/{question_id}/feedback",
            headers=_auth(admin["token"]),
            json={"feedback": "第一条", "rating": 4},
        )
        # 注册第二个用户写反馈
        admin2 = await _register_admin(client, email="admin2@example.com")
        # 把 admin2 加入同一 team（绕过 register 自动创建新 team 的问题）
        async with AsyncSessionLocal() as session:
            user2 = await session.get(User, uuid.UUID(admin2["user_id"]))
            user2.team_id = uuid.UUID(admin["team_id"])
            await session.commit()

        await client.post(
            f"/api/interview/questions/{question_id}/feedback",
            headers=_auth(admin2["token"]),
            json={"feedback": "第二条", "rating": 5},
        )

        resp = await client.get(
            f"/api/interview/questions/{question_id}/feedback",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert len(body) == 2
        # 最新的在前
        assert body[0]["feedback"] == "第二条"
        assert body[1]["feedback"] == "第一条"


# ============================================================================
# Tests: 跨 team
# ============================================================================


class TestCrossTeam:
    async def test_cross_team_question_404(
        self, client: AsyncClient, monkeypatch
    ) -> None:
        """题目不属于当前 team → 404。"""
        admin = await _register_admin(client)
        _patch_router(monkeypatch)
        job_id, cand_id = await _seed_job_and_candidate(admin["team_id"])

        gen = await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        question_id = gen.json()["questions"][0]["id"]

        # 创建另一个 team 的 user
        async with AsyncSessionLocal() as session:
            other_team = Team(name=f"t-{uuid.uuid4().hex[:6]}")
            session.add(other_team)
            await session.flush()
            user2 = User(
                email=f"u-{uuid.uuid4().hex[:6]}@x.com",
                password_hash="x",
                name="hr2",
                team_id=other_team.id,
            )
            session.add(user2)
            await session.commit()
            user2_id = str(user2.id)

        # 给 user2 签 token
        from app.core.security import create_access_token

        token2 = create_access_token(
            subject=user2_id,
            extra_claims={"team_id": None, "role": "member"},
        )

        resp = await client.get(
            f"/api/interview/questions/{question_id}/feedback",
            headers=_auth(token2),
        )
        assert resp.status_code == 404

    async def test_cross_team_candidate_404(
        self, client: AsyncClient
    ) -> None:
        """candidate 跨 team → 404。"""
        admin = await _register_admin(client)

        # 创建另一 team 的 candidate
        async with AsyncSessionLocal() as session:
            other_team = Team(name=f"t-{uuid.uuid4().hex[:6]}")
            session.add(other_team)
            await session.flush()
            user = User(
                email=f"u-{uuid.uuid4().hex[:6]}@x.com",
                password_hash="x",
                name="hr",
                team_id=other_team.id,
            )
            session.add(user)
            await session.flush()
            job = Job(
                team_id=other_team.id,
                title="T", jd_text="x", status="active",
                created_by=user.id,
            )
            session.add(job)
            await session.flush()
            cand = Candidate(
                team_id=other_team.id,
                dedup_key=f"test:{uuid.uuid4()}",
                name="x",
            )
            session.add(cand)
            await session.commit()
            job_id = str(job.id)
            cand_id = str(cand.id)

        resp = await client.post(
            "/api/interview/generate",
            headers=_auth(admin["token"]),
            json={"candidate_id": cand_id, "job_id": job_id},
        )
        assert resp.status_code == 404
