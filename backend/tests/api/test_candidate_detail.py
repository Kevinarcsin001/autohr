"""/api/candidates/{id}/detail + /resume-url + /activity 集成测试（任务 24）。

覆盖：
- detail happy path：candidate + screening + score + structure + resume 一次返回
- detail 跨 team → 404；未登录 → 401
- resume-url：返回签名 URL（含 X-Amz-Signature）+ expires_at 在 5min 附近
- resume-url：候选人无 resume → 404
- activity：触发 override 后查 activity，type=override 项存在；audit_logs 同
- activity 分页 / 跨 team → 403/404
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.audit import AuditLog
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.job import Job
from app.models.score import Score
from app.models.screening import ManualOverride, ScreeningResult
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


async def _seed_candidate(
    *,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    with_resume: bool = True,
    with_structure: bool = True,
    with_score: bool = True,
    with_screening: bool = True,
    total: int = 88,
    education: str = "master",
    years: int = 5,
    skills: list[str] | None = None,
    parsed_text: str = "Python FastAPI 五年经验",
) -> tuple[Job, Candidate, CandidateResume | None, ScreeningResult | None]:
    """创建完整候选链路（job + candidate + source + resume + structure + screening + score）。"""
    async with AsyncSessionLocal() as session:
        job = Job(
            team_id=team_id, title="Eng", jd_text="Python",
            status="active", created_by=user_id,
        )
        session.add(job)
        await session.flush()

        cand = Candidate(
            team_id=team_id,
            dedup_key=f"test:{uuid.uuid4()}",
            name="张三",
            phone="13800000001",
            email="zs@example.com",
        )
        session.add(cand)
        await session.flush()

        src = CandidateSource(
            candidate_id=cand.id, source_type="upload"
        )
        session.add(src)
        await session.flush()

        resume: CandidateResume | None = None
        if with_resume:
            resume = CandidateResume(
                candidate_id=cand.id, source_id=src.id,
                file_storage_key=f"team-{team_id}/resume-{cand.id}.pdf",
                file_mime="application/pdf",
                parse_status="success", parsed_text=parsed_text,
            )
            session.add(resume)
            await session.flush()

            if with_structure:
                session.add(ParsedStructure(
                    resume_id=resume.id,
                    data={
                        "structure": {
                            "name": "张三",
                            "education": education,
                            "years_of_experience": years,
                            "skills": skills if skills else ["Python", "FastAPI"],
                            "current_company": "ACME",
                        },
                        "status": "extracted",
                    },
                ))

        screening: ScreeningResult | None = None
        if with_screening:
            screening = ScreeningResult(
                job_id=job.id, candidate_id=cand.id,
                disqualified=False,
            )
            session.add(screening)
            await session.flush()

        if with_score:
            session.add(Score(
                job_id=job.id, candidate_id=cand.id,
                total=total, skill=total - 5, experience=total - 3,
                education=total - 8, stability=total - 2, potential=total - 1,
                model_used="mock",
            ))

        await session.commit()
        return job, cand, resume, screening


# ============================================================================
# GET /detail
# ============================================================================


class TestGetDetail:
    async def test_happy_path_returns_aggregation(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        job, cand, _, _ = await _seed_candidate(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )

        resp = await client.get(
            f"/api/candidates/{cand.id}/detail",
            headers=_auth(admin["token"]),
            params={"job_id": str(job.id)},
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()

        # candidate 基础字段
        assert body["candidate"]["id"] == str(cand.id)
        assert body["candidate"]["name"] == "张三"
        assert body["candidate"]["phone"] == "13800000001"
        assert body["candidate"]["email"] == "zs@example.com"
        assert body["candidate"]["source_type"] == "upload"

        # screening
        assert body["screening_result"]["disqualified"] is False

        # score
        assert body["score"]["total"] == 88
        assert body["score"]["model_used"] == "mock"

        # parsed_structure
        assert body["parsed_structure"]["education"] == "master"
        assert body["parsed_structure"]["years_of_experience"] == 5
        assert "Python" in body["parsed_structure"]["skills"]

        # resume
        assert body["resume"]["parsed_text"] == "Python FastAPI 五年经验"
        assert body["resume"]["mime_type"] == "application/pdf"
        assert body["resume"]["file_storage_key"].endswith(".pdf")

    async def test_unauthenticated_returns_401(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get(
            f"/api/candidates/{uuid.uuid4()}/detail",
            params={"job_id": str(uuid.uuid4())},
        )
        assert resp.status_code == 401

    async def test_cross_team_candidate_404(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        # 在其他 team 创建候选人
        async with AsyncSessionLocal() as session:
            other_team = Team(name=f"o-{uuid.uuid4().hex[:6]}")
            session.add(other_team)
            await session.flush()
            other_user = User(
                email="o@x.com", password_hash="x",
                name="o", role="admin", team_id=other_team.id,
            )
            session.add(other_user)
            await session.flush()
            job = Job(
                team_id=other_team.id, title="x", jd_text="x",
                status="active", created_by=other_user.id,
            )
            session.add(job)
            await session.flush()
            cand = Candidate(
                team_id=other_team.id,
                dedup_key=f"o:{uuid.uuid4()}",
                name="secret",
            )
            session.add(cand)
            await session.commit()

        resp = await client.get(
            f"/api/candidates/{cand.id}/detail",
            headers=_auth(admin["token"]),
            params={"job_id": str(job.id)},
        )
        assert resp.status_code == 404

    async def test_candidate_without_score_or_screening(
        self, client: AsyncClient
    ) -> None:
        """pending 候选（无 score / screening）→ 字段为 null。"""
        admin = await _register_admin(client)
        job, cand, _, _ = await _seed_candidate(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
            with_score=False,
            with_screening=False,
        )
        resp = await client.get(
            f"/api/candidates/{cand.id}/detail",
            headers=_auth(admin["token"]),
            params={"job_id": str(job.id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["screening_result"] is None
        assert body["score"] is None
        # resume + structure 仍存在
        assert body["resume"] is not None
        assert body["parsed_structure"] is not None

    async def test_candidate_without_resume(
        self, client: AsyncClient
    ) -> None:
        """无 resume 候选 → resume / parsed_structure 为 null。"""
        admin = await _register_admin(client)
        job, cand, _, _ = await _seed_candidate(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
            with_resume=False,
        )
        resp = await client.get(
            f"/api/candidates/{cand.id}/detail",
            headers=_auth(admin["token"]),
            params={"job_id": str(job.id)},
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["resume"] is None
        assert body["parsed_structure"] is None


# ============================================================================
# GET /resume-url
# ============================================================================


class TestResumeUrl:
    async def test_returns_signed_url(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        _, cand, _, _ = await _seed_candidate(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        resp = await client.get(
            f"/api/candidates/{cand.id}/resume-url",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # MinIO/S3 签名 URL 特征
        assert "X-Amz-Signature" in body["url"]
        assert "X-Amz-Expires" in body["url"]
        # expires_at 在 now + 5min 附近（容差 ±30s）
        expires_at = datetime.fromisoformat(
            body["expires_at"].replace("Z", "+00:00")
        )
        delta = (expires_at - datetime.now(timezone.utc)).total_seconds()
        assert 240 <= delta <= 360, f"delta={delta}"

    async def test_no_resume_returns_404(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        _, cand, _, _ = await _seed_candidate(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
            with_resume=False,
        )
        resp = await client.get(
            f"/api/candidates/{cand.id}/resume-url",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 404

    async def test_cross_team_404(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        # 在其他 team 创建候选人
        async with AsyncSessionLocal() as session:
            other_team = Team(name=f"o-{uuid.uuid4().hex[:6]}")
            session.add(other_team)
            await session.flush()
            cand = Candidate(
                team_id=other_team.id,
                dedup_key=f"o:{uuid.uuid4()}",
                name="secret",
            )
            session.add(cand)
            await session.commit()
        resp = await client.get(
            f"/api/candidates/{cand.id}/resume-url",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 404

    async def test_unauthenticated_returns_401(
        self, client: AsyncClient
    ) -> None:
        resp = await client.get(
            f"/api/candidates/{uuid.uuid4()}/resume-url",
        )
        assert resp.status_code == 401


# ============================================================================
# GET /activity
# ============================================================================


class TestActivity:
    async def test_empty_activity_returns_empty_list(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        _, cand, _, _ = await _seed_candidate(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        resp = await client.get(
            f"/api/candidates/{cand.id}/activity",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["items"] == []
        assert body["total"] == 0

    async def test_activity_includes_audit_logs(
        self, client: AsyncClient
    ) -> None:
        """audit_logs 表中 target_type=candidate 的记录应被聚合。"""
        admin = await _register_admin(client)
        _, cand, _, _ = await _seed_candidate(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        # 手动写 audit_log（target=candidate）
        async with AsyncSessionLocal() as session:
            session.add(AuditLog(
                actor_id=uuid.UUID(admin["user_id"]),
                action="candidate.update",
                target_type="candidate",
                target_id=cand.id,
                before={"name": "old"},
                after={"name": "new"},
            ))
            await session.commit()

        resp = await client.get(
            f"/api/candidates/{cand.id}/activity",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 1
        assert body["items"][0]["type"] == "audit_log"
        assert body["items"][0]["action"] == "candidate.update"
        assert body["items"][0]["actor_id"] == admin["user_id"]
        assert "before" in body["items"][0]["details"]
        assert "after" in body["items"][0]["details"]

    async def test_activity_includes_manual_overrides(
        self, client: AsyncClient
    ) -> None:
        """通过 FilterService 写 manual_override → activity 应出现 type=override 项。"""
        admin = await _register_admin(client)
        _, cand, _, screening = await _seed_candidate(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        assert screening is not None

        # 通过 PATCH /api/screening/results/{id}/override 触发 override
        resp = await client.patch(
            f"/api/screening/results/{screening.id}/override",
            headers=_auth(admin["token"]),
            json={
                "new_disqualified": True,
                "new_reasons": ["HR 复核不通过"],
                "reason": "面试反馈差",
            },
        )
        assert resp.status_code == 200, resp.text

        # 查 activity
        resp = await client.get(
            f"/api/candidates/{cand.id}/activity",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        # 至少一条 type=override
        overrides = [it for it in body["items"] if it["type"] == "override"]
        assert len(overrides) >= 1
        assert overrides[0]["action"] == "screening.override"
        assert overrides[0]["actor_id"] == admin["user_id"]
        # details 含 new_value
        assert overrides[0]["details"]["new_value"] is not None
        # summary 含 "HR 改判"
        assert "改判" in overrides[0]["summary"]

    async def test_activity_sorted_desc_by_created_at(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        _, cand, _, _ = await _seed_candidate(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        # 写两条 audit，间隔一定时间（microsecond 精度足够）
        async with AsyncSessionLocal() as session:
            from datetime import timedelta
            t1 = datetime.now(timezone.utc) - timedelta(minutes=10)
            t2 = datetime.now(timezone.utc)
            session.add(AuditLog(
                actor_id=uuid.UUID(admin["user_id"]),
                action="candidate.first",
                target_type="candidate", target_id=cand.id,
                created_at=t1,
            ))
            session.add(AuditLog(
                actor_id=uuid.UUID(admin["user_id"]),
                action="candidate.second",
                target_type="candidate", target_id=cand.id,
                created_at=t2,
            ))
            await session.commit()

        resp = await client.get(
            f"/api/candidates/{cand.id}/activity",
            headers=_auth(admin["token"]),
        )
        body = resp.json()
        # 倒序：second 在前
        assert body["items"][0]["action"] == "candidate.second"
        assert body["items"][1]["action"] == "candidate.first"

    async def test_activity_pagination(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        _, cand, _, _ = await _seed_candidate(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        # 写 5 条 audit
        async with AsyncSessionLocal() as session:
            for i in range(5):
                session.add(AuditLog(
                    actor_id=uuid.UUID(admin["user_id"]),
                    action=f"candidate.act{i}",
                    target_type="candidate", target_id=cand.id,
                ))
            await session.commit()

        resp = await client.get(
            f"/api/candidates/{cand.id}/activity",
            headers=_auth(admin["token"]),
            params={"page": 1, "page_size": 2},
        )
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 5

    async def test_activity_cross_team_404(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        async with AsyncSessionLocal() as session:
            other_team = Team(name=f"o-{uuid.uuid4().hex[:6]}")
            session.add(other_team)
            await session.flush()
            cand = Candidate(
                team_id=other_team.id,
                dedup_key=f"o:{uuid.uuid4()}",
                name="secret",
            )
            session.add(cand)
            await session.commit()
        resp = await client.get(
            f"/api/candidates/{cand.id}/activity",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 404

    async def test_no_team_returns_403(
        self, client: AsyncClient
    ) -> None:
        """无 team 用户访问 → 403。"""
        # 注册第二个用户并主动不绑定 team（通过直接创建 User）
        async with AsyncSessionLocal() as session:
            from app.models.user import User as UserModel
            orphan = UserModel(
                email="orphan@example.com",
                password_hash="$2b$12$dummy",
                name="orphan", role="member",
                team_id=None,
            )
            session.add(orphan)
            await session.commit()
            orphan_id = orphan.id

        # 直接生成 token（绕过注册流程）
        from app.core.security import create_access_token
        token = create_access_token(
            subject=orphan_id,
            extra_claims={"team_id": None, "role": "member"},
        )

        resp = await client.get(
            f"/api/candidates/{uuid.uuid4()}/activity",
            headers=_auth(token),
        )
        assert resp.status_code == 403
