"""录用结果（效果回流）+ 校准报告 + 招聘漏斗 集成测试（P1-5 / P1-4）。"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.candidate import Candidate
from app.models.interview import InterviewSession
from app.models.job import Job
from app.models.outcome import CandidateJobOutcome
from app.models.score import Score
from app.models.screening import ScreeningResult
from app.models.team import Team
from app.models.user import User
from tests.db_utils import purge_database


async def _purge_db() -> None:
    async with AsyncSessionLocal() as session:
        await purge_database(session)
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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


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


async def _seed_job_candidate_score(
    *,
    team_id: uuid.UUID,
    total: int | None,
    disqualified: bool = False,
    needs_review: bool = False,
    interview: bool = False,
    job_id: uuid.UUID | None = None,
) -> tuple[uuid.UUID, uuid.UUID]:
    """最小种子：job + candidate (+ screening + score + session)。

    ``job_id`` 给定时复用既有 job（同职位多候选人的场景）。
    """
    async with AsyncSessionLocal() as session:
        if job_id is not None:
            job = await session.get(Job, job_id)
            assert job is not None
        else:
            admin = (
                (
                    await session.execute(
                        select(User).where(User.team_id == team_id).limit(1)
                    )
                )
                .scalars()
                .first()
            )
            job = Job(
                team_id=team_id,
                title=f"Job-{uuid.uuid4().hex[:8]}",
                jd_text="d",
                status="active",
                created_by=admin.id if admin else None,
            )
            session.add(job)
            await session.flush()

        cand = Candidate(
            team_id=team_id,
            dedup_key=uuid.uuid4().hex,
            name=f"候选人{uuid.uuid4().hex[:4]}",
        )
        session.add(cand)
        await session.flush()

        if total is not None or disqualified or needs_review:
            session.add(
                ScreeningResult(
                    job_id=job.id,
                    candidate_id=cand.id,
                    disqualified=disqualified,
                    needs_review=needs_review,
                )
            )
        if total is not None:
            session.add(
                Score(
                    job_id=job.id,
                    candidate_id=cand.id,
                    total=total,
                    skill=total,
                    experience=total,
                    education=total,
                    stability=total,
                    potential=total,
                    model_used="mock",
                )
            )
        if interview:
            session.add(
                InterviewSession(
                    job_id=job.id,
                    candidate_id=cand.id,
                    status="scheduled",
                )
            )
        await session.commit()
        return job.id, cand.id


# ============================================================================
# 结果录入 API
# ============================================================================


class TestOutcomeAPI:
    async def test_put_then_get_roundtrip(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job_id, cand_id = await _seed_job_candidate_score(
            team_id=uuid.UUID(admin["team_id"]), total=85
        )

        resp = await client.put(
            f"/api/jobs/{job_id}/candidates/{cand_id}/outcome",
            headers=_auth(admin["token"]),
            json={"final_status": "hired", "note": "表现优秀"},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["final_status"] == "hired"
        assert body["note"] == "表现优秀"
        assert body["decided_at"] is not None

        # GET 回读
        resp = await client.get(
            f"/api/jobs/{job_id}/candidates/{cand_id}/outcome",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200
        assert resp.json()["final_status"] == "hired"

    async def test_put_is_idempotent_upsert(self, client: AsyncClient) -> None:
        """重复 PUT 更新同一行（UNIQUE job+candidate），不新增。"""
        admin = await _register_admin(client)
        job_id, cand_id = await _seed_job_candidate_score(
            team_id=uuid.UUID(admin["team_id"]), total=70
        )

        for status in ("hired", "probation_passed"):
            resp = await client.put(
                f"/api/jobs/{job_id}/candidates/{cand_id}/outcome",
                headers=_auth(admin["token"]),
                json={"final_status": status},
            )
            assert resp.status_code == 200

        async with AsyncSessionLocal() as session:
            rows = (
                (
                    await session.execute(
                        select(CandidateJobOutcome).where(
                            CandidateJobOutcome.job_id == job_id,
                            CandidateJobOutcome.candidate_id == cand_id,
                        )
                    )
                )
                .scalars()
                .all()
            )
        assert len(rows) == 1
        assert rows[0].final_status == "probation_passed"

    async def test_put_rejects_invalid_status(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job_id, cand_id = await _seed_job_candidate_score(
            team_id=uuid.UUID(admin["team_id"]), total=60
        )
        resp = await client.put(
            f"/api/jobs/{job_id}/candidates/{cand_id}/outcome",
            headers=_auth(admin["token"]),
            json={"final_status": "promoted_to_ceo"},
        )
        assert resp.status_code == 422

    async def test_get_without_outcome_returns_null(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        job_id, cand_id = await _seed_job_candidate_score(
            team_id=uuid.UUID(admin["team_id"]), total=60
        )
        resp = await client.get(
            f"/api/jobs/{job_id}/candidates/{cand_id}/outcome",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200
        assert resp.json() is None

    async def test_unauthenticated_rejected(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job_id, cand_id = await _seed_job_candidate_score(
            team_id=uuid.UUID(admin["team_id"]), total=60
        )
        resp = await client.get(
            f"/api/jobs/{job_id}/candidates/{cand_id}/outcome"
        )
        assert resp.status_code == 401


# ============================================================================
# 校准报告
# ============================================================================


class TestCalibration:
    async def test_hire_rate_increases_with_score(
        self, client: AsyncClient
    ) -> None:
        """高分段 hire_rate 应高于低分段（校准有效的直接断言）。"""
        admin = await _register_admin(client)
        team_id = uuid.UUID(admin["team_id"])

        # 同一 job 下：高分段 3 hired 1 rejected；低分段 0 hired 3 rejected
        job_id: uuid.UUID | None = None
        for total, status in [
            (92, "hired"),
            (95, "hired"),
            (98, "hired"),
            (91, "rejected"),
            (40, "rejected"),
            (45, "rejected"),
            (50, "rejected"),
        ]:
            job_id, cand_id = await _seed_job_candidate_score(
                team_id=team_id, total=total, job_id=job_id
            )
            async with AsyncSessionLocal() as session:
                session.add(
                    CandidateJobOutcome(
                        job_id=job_id,
                        candidate_id=cand_id,
                        final_status=status,
                    )
                )
                await session.commit()

        assert job_id is not None
        resp = await client.get(
            f"/api/jobs/{job_id}/calibration", headers=_auth(admin["token"])
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_with_outcome"] == 7
        buckets = {b["score_min"]: b for b in body["buckets"]}
        hi = buckets[90]
        lo = buckets[0]
        assert hi["hired"] == 3 and hi["rejected"] == 1
        assert lo["hired"] == 0 and lo["rejected"] == 3
        assert hi["hire_rate"] == 0.75
        assert lo["hire_rate"] == 0.0
        assert hi["hire_rate"] > lo["hire_rate"]


# ============================================================================
# 招聘漏斗
# ============================================================================


class TestFunnel:
    async def test_funnel_counts_end_to_end(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        team_id = uuid.UUID(admin["team_id"])

        # A: 通过 + 评分 + 面试 + hired（全链路）
        j1, c1 = await _seed_job_candidate_score(
            team_id=team_id, total=88, interview=True
        )
        # B: 通过 + 评分（无面试）
        await _seed_job_candidate_score(team_id=team_id, total=65)
        # C: 淘汰
        await _seed_job_candidate_score(
            team_id=team_id, total=None, disqualified=True
        )
        # D: 待复核
        await _seed_job_candidate_score(
            team_id=team_id, total=None, needs_review=True
        )

        async with AsyncSessionLocal() as session:
            session.add(
                CandidateJobOutcome(
                    job_id=j1, candidate_id=c1, final_status="hired"
                )
            )
            await session.commit()

        resp = await client.get(
            "/api/dashboard/funnel", headers=_auth(admin["token"])
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["total_pool"] == 4
        assert body["screened_pass"] == 2
        assert body["disqualified"] == 1
        assert body["needs_review"] == 1
        assert body["scored"] == 2
        assert body["interviewed"] == 1
        assert body["hired"] == 1

    async def test_unauthenticated_rejected(self, client: AsyncClient) -> None:
        resp = await client.get("/api/dashboard/funnel")
        assert resp.status_code == 401
