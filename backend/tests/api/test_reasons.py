"""/api/reasons 路由集成测试（任务 18）。"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.score import Score, ScoreReason
from app.models.team import Team
from app.models.user import User


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


async def _seed_score_and_reason(
    team_id: str,
    *,
    reason_type: str = "recommend",
    bullet_points: list[str] | None = None,
    validated: bool = True,
) -> tuple[str, str, str]:
    """创建 job + candidate + score + reason，返回 (job_id, candidate_id, score_id, reason_id)。"""
    async with AsyncSessionLocal() as session:
        team_id_uuid = uuid.UUID(team_id)
        # team 已在 register 时创建；取出来
        from sqlalchemy import select

        team = await session.scalar(select(Team).where(Team.id == team_id_uuid))
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
            jd_text="Python",
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

        score = Score(
            job_id=job.id,
            candidate_id=candidate.id,
            total=85, skill=90, experience=80,
            education=75, stability=80, potential=85,
            model_used="mock",
        )
        session.add(score)
        await session.flush()

        reason = ScoreReason(
            score_id=score.id,
            type=reason_type,
            bullet_points=bullet_points or ["Python 技能匹配"],
            validated=validated,
        )
        session.add(reason)
        await session.commit()
        return str(job.id), str(candidate.id), str(score.id), str(reason.id)


# ============================================================================
# Tests
# ============================================================================


class TestListByScore:
    async def test_returns_reasons(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        _, _, score_id, reason_id = await _seed_score_and_reason(
            admin["team_id"],
            reason_type="recommend",
            bullet_points=["Python 技能", "FastAPI 经验"],
            validated=True,
        )

        resp = await client.get(
            f"/api/reasons/by-score/{score_id}",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total"] == 1
        item = body["items"][0]
        assert item["id"] == reason_id
        assert item["type"] == "recommend"
        assert item["validated"] is True
        assert item["bullet_points"] == ["Python 技能", "FastAPI 经验"]

    async def test_cross_team_score_returns_404(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        # 用一个不存在的 score_id（必然跨 team）
        resp = await client.get(
            f"/api/reasons/by-score/{uuid.uuid4()}",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 404

    async def test_returns_empty_when_no_reasons(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        # 创建 score 但不写 reason
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select

            user = User(
                email=f"u-{uuid.uuid4().hex[:6]}@x.com",
                password_hash="x",
                name="hr",
                team_id=uuid.UUID(admin["team_id"]),
            )
            session.add(user)
            await session.flush()
            job = Job(
                team_id=uuid.UUID(admin["team_id"]),
                title="T", jd_text="x", status="active",
                created_by=user.id,
            )
            session.add(job)
            await session.flush()
            candidate = Candidate(
                team_id=uuid.UUID(admin["team_id"]),
                dedup_key=f"test:{uuid.uuid4()}",
                name="张三",
            )
            session.add(candidate)
            await session.flush()
            score = Score(
                job_id=job.id, candidate_id=candidate.id,
                total=80, skill=70, experience=70,
                education=70, stability=70, potential=70,
            )
            session.add(score)
            await session.commit()
            score_id = str(score.id)

        resp = await client.get(
            f"/api/reasons/by-score/{score_id}",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0


class TestListByJob:
    async def test_returns_all_reasons_under_job(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job_id, _, _, _ = await _seed_score_and_reason(
            admin["team_id"], reason_type="recommend",
        )
        # 再写一个 disqualify
        async with AsyncSessionLocal() as session:
            from sqlalchemy import select

            score = await session.scalar(
                select(Score).where(Score.job_id == uuid.UUID(job_id))
            )
            session.add(ScoreReason(
                score_id=score.id,
                type="disqualify",
                bullet_points=["技能缺失：Rust"],
                validated=True,
            ))
            await session.commit()

        resp = await client.get(
            f"/api/reasons/by-job/{job_id}",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        types = {it["type"] for it in body["items"]}
        assert types == {"recommend", "disqualify"}

    async def test_cross_team_job_returns_404(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        resp = await client.get(
            f"/api/reasons/by-job/{uuid.uuid4()}",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 404


class TestAuth:
    async def test_endpoints_require_auth(self, client: AsyncClient) -> None:
        resp = await client.get(
            f"/api/reasons/by-score/{uuid.uuid4()}"
        )
        assert resp.status_code == 401

        resp = await client.get(
            f"/api/reasons/by-job/{uuid.uuid4()}"
        )
        assert resp.status_code == 401
