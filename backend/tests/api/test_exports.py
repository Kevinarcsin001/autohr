"""/api/exports 路由集成测试（任务 22）。

覆盖：
- POST /api/exports/ 同步导出（小数据集）
- POST /api/exports/ 异步导出（mock 阈值后入 async_jobs）
- GET  /api/exports/jobs/{job_id} 查询异步状态
- GET  /api/exports/download?file_key=...
- 跨 team → 404
- 鉴权（401）
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.async_job import AsyncJob
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.interview import InterviewQuestion
from app.models.job import Job
from app.models.score import Score, ScoreReason
from app.models.screening import ScreeningResult
from app.models.team import Team
from app.models.user import User

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
    yield
    await _purge_db()


# ============================================================================
# 工具
# ============================================================================


class _FakeStorage:
    """与 service 测试同样的 fake；用于替换 ExportService 单例 storage。"""

    def __init__(self) -> None:
        self._store: dict[str, bytes] = {}
        self.put_calls: list[tuple[str, bytes, str, bool]] = []
        self.signed_url_calls: list[tuple[str, int | None, str]] = []

    async def put(
        self, key: str, data: bytes, *, mime: str, encrypt: bool = True
    ) -> None:
        self.put_calls.append((key, data, mime, encrypt))
        self._store[key] = data

    async def get(self, key: str) -> bytes:
        return self._store[key]

    async def exists(self, key: str) -> bool:
        return key in self._store

    async def delete(self, key: str) -> None:
        self._store.pop(key, None)

    async def signed_url(
        self, key: str, *, expires: int | None = None, method: str = "GET"
    ) -> str:
        self.signed_url_calls.append((key, expires, method))
        return f"https://fake.local/{key}?expires={expires}&method={method}"


@pytest.fixture
async def fake_storage(monkeypatch):
    """全局替换 ExportService 内部 storage 单例。"""
    fake = _FakeStorage()
    import app.services.export as export_mod
    monkeypatch.setattr(
        export_mod, "get_storage", lambda: fake
    )
    return fake


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


async def _seed_job_with_candidates(
    *, team_id: uuid.UUID, user_id: uuid.UUID, n: int = 3
) -> Job:
    """直接写 DB 创建 job + N candidates（含 source/resume/structure/score）。"""
    async with AsyncSessionLocal() as session:
        job = Job(
            team_id=team_id, title="Eng", jd_text="Python",
            status="active", created_by=user_id,
        )
        session.add(job)
        await session.flush()

        for i in range(n):
            cand = Candidate(
                team_id=team_id,
                dedup_key=f"t:{uuid.uuid4()}",
                name=f"c-{i}",
                phone=f"1380000000{i}",
                email=f"c{i}@x.com",
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
                file_storage_key=f"k-{i}", file_mime="application/pdf",
                parse_status="success", parsed_text="x",
            )
            session.add(resume)
            await session.flush()

            session.add(ParsedStructure(
                resume_id=resume.id,
                data={
                    "structure": {
                        "name": f"c-{i}",
                        "phone": f"1380000000{i}",
                        "email": f"c{i}@x.com",
                        "education": "master",
                        "years_of_experience": 5,
                        "current_company": "ACME",
                        "skills": ["Python"],
                    },
                    "status": "extracted",
                },
            ))

            session.add(ScreeningResult(
                job_id=job.id, candidate_id=cand.id, disqualified=False,
            ))

            score = Score(
                job_id=job.id, candidate_id=cand.id,
                total=80 + i, skill=85, experience=80, education=75,
                stability=80, potential=85,
            )
            session.add(score)
            await session.flush()
            session.add(ScoreReason(
                score_id=score.id, type="recommend",
                bullet_points=["Python 匹配"],
            ))

            session.add(InterviewQuestion(
                candidate_id=cand.id, job_id=job.id,
                batch_id=uuid.uuid4(), dimension="skill",
                question="聊 Python", sort_order=0,
            ))

        await session.commit()
        return job


# ============================================================================
# POST /api/exports/
# ============================================================================


class TestRequestExport:
    async def test_unauthenticated_returns_401(
        self, client: AsyncClient, fake_storage
    ) -> None:
        resp = await client.post(
            "/api/exports/",
            json={"job_id": str(uuid.uuid4()), "format": "xlsx"},
        )
        assert resp.status_code == 401

    async def test_sync_export_small_dataset(
        self, client: AsyncClient, fake_storage
    ) -> None:
        admin = await _register_admin(client)
        job = await _seed_job_with_candidates(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
            n=3,
        )
        resp = await client.post(
            "/api/exports/",
            headers=_auth(admin["token"]),
            json={"job_id": str(job.id), "format": "xlsx"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["mode"] == "sync"
        assert body["download_url"].startswith("https://fake.local/")
        assert body["expires_in"] == 300
        assert body["row_count"] == 3
        assert body["file_size"] > 0

    async def test_async_export_when_threshold_low(
        self, client: AsyncClient, fake_storage, monkeypatch
    ) -> None:
        """阈值降到 1，2 个候选人 → 异步入队。"""
        import app.services.export as export_mod
        monkeypatch.setattr(export_mod, "EXPORT_ASYNC_THRESHOLD", 1)

        admin = await _register_admin(client)
        job = await _seed_job_with_candidates(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
            n=2,
        )
        resp = await client.post(
            "/api/exports/",
            headers=_auth(admin["token"]),
            json={"job_id": str(job.id), "format": "xlsx"},
        )
        assert resp.status_code == 202, resp.text
        body = resp.json()
        assert body["mode"] == "async"
        assert "job_id" in body
        assert body["row_count"] == 2

        # async_jobs 表应有 1 行
        async with AsyncSessionLocal() as session:
            jobs = (
                await session.execute(
                    select(AsyncJob).where(AsyncJob.task_type == "export")
                )
            ).scalars().all()
            assert len(jobs) == 1
            assert jobs[0].status == "queued"

    async def test_cross_team_job_404(
        self, client: AsyncClient, fake_storage
    ) -> None:
        """job 属于其他 team → 404（不暴露存在性）。"""
        admin = await _register_admin(client)
        job = await _seed_job_with_candidates(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
            n=1,
        )
        # 用另一 team 的用户访问
        async with AsyncSessionLocal() as session:
            other_team = Team(name=f"o-{uuid.uuid4().hex[:6]}")
            session.add(other_team)
            await session.flush()
            other_user = User(
                email="o@x.com", password_hash="x",
                name="o", role="admin", team_id=other_team.id,
            )
            session.add(other_user)
            await session.commit()

            from app.core.security import create_access_token
            other_token = create_access_token(
                subject=other_user.id,
                extra_claims={
                    "team_id": str(other_team.id),
                    "role": "admin",
                    "email": other_user.email,
                },
            )

        resp = await client.post(
            "/api/exports/",
            headers=_auth(other_token),
            json={"job_id": str(job.id), "format": "xlsx"},
        )
        assert resp.status_code == 404, resp.text


# ============================================================================
# GET /api/exports/jobs/{job_id}
# ============================================================================


class TestGetExportStatus:
    async def test_unauthenticated_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(f"/api/exports/jobs/{uuid.uuid4()}")
        assert resp.status_code == 401

    async def test_not_found(self, client: AsyncClient, fake_storage) -> None:
        admin = await _register_admin(client)
        resp = await client.get(
            f"/api/exports/jobs/{uuid.uuid4()}",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 404

    async def test_cross_team_404(
        self, client: AsyncClient, fake_storage, monkeypatch
    ) -> None:
        """异步 export job 的 payload.team_id 与当前 user 不一致 → 404。"""
        import app.services.export as export_mod
        monkeypatch.setattr(export_mod, "EXPORT_ASYNC_THRESHOLD", 1)

        admin = await _register_admin(client)
        job = await _seed_job_with_candidates(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
            n=2,
        )
        # 触发异步入队
        await client.post(
            "/api/exports/",
            headers=_auth(admin["token"]),
            json={"job_id": str(job.id), "format": "xlsx"},
        )

        async with AsyncSessionLocal() as session:
            async_job = (
                await session.execute(
                    select(AsyncJob).where(AsyncJob.task_type == "export")
                )
            ).scalar_one()

        # 用其他 team 用户访问
        async with AsyncSessionLocal() as session:
            other_team = Team(name=f"o-{uuid.uuid4().hex[:6]}")
            session.add(other_team)
            await session.flush()
            other_user = User(
                email="o2@x.com", password_hash="x",
                name="o", role="admin", team_id=other_team.id,
            )
            session.add(other_user)
            await session.commit()

            from app.core.security import create_access_token
            other_token = create_access_token(
                subject=other_user.id,
                extra_claims={
                    "team_id": str(other_team.id),
                    "role": "admin",
                    "email": other_user.email,
                },
            )

        resp = await client.get(
            f"/api/exports/jobs/{async_job.id}",
            headers=_auth(other_token),
        )
        assert resp.status_code == 404

    async def test_returns_status_queued(
        self, client: AsyncClient, fake_storage, monkeypatch
    ) -> None:
        import app.services.export as export_mod
        monkeypatch.setattr(export_mod, "EXPORT_ASYNC_THRESHOLD", 1)

        admin = await _register_admin(client)
        job = await _seed_job_with_candidates(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
            n=2,
        )
        await client.post(
            "/api/exports/",
            headers=_auth(admin["token"]),
            json={"job_id": str(job.id), "format": "xlsx"},
        )

        async with AsyncSessionLocal() as session:
            async_job = (
                await session.execute(
                    select(AsyncJob).where(AsyncJob.task_type == "export")
                )
            ).scalar_one()

        resp = await client.get(
            f"/api/exports/jobs/{async_job.id}",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["status"] == "queued"
        assert body["job_id"] == str(async_job.id)
        # 任务还没跑，file_key 应为 None
        assert body["file_key"] is None


# ============================================================================
# GET /api/exports/download
# ============================================================================


class TestDownloadUrl:
    async def test_unauthenticated_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(
            "/api/exports/download", params={"file_key": "x"}
        )
        assert resp.status_code == 401

    async def test_cross_team_404(
        self, client: AsyncClient, fake_storage
    ) -> None:
        """file_key 前缀不属于当前 team → 404。"""
        admin = await _register_admin(client)
        bad_key = f"exports/{uuid.uuid4()}/{uuid.uuid4()}/x.xlsx"
        resp = await client.get(
            "/api/exports/download",
            headers=_auth(admin["token"]),
            params={"file_key": bad_key},
        )
        assert resp.status_code == 404

    async def test_returns_signed_url(
        self, client: AsyncClient, fake_storage
    ) -> None:
        admin = await _register_admin(client)
        job = await _seed_job_with_candidates(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
            n=2,
        )
        # 触发同步导出获取 file_key
        resp = await client.post(
            "/api/exports/",
            headers=_auth(admin["token"]),
            json={"job_id": str(job.id), "format": "xlsx"},
        )
        file_key = resp.json()["file_key"]

        resp = await client.get(
            "/api/exports/download",
            headers=_auth(admin["token"]),
            params={"file_key": file_key},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["download_url"].startswith("https://fake.local/")
        assert body["expires_in"] == 300
