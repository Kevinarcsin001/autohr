"""Audit Log API 集成测试（任务 21）。

策略：
1. **middleware 自动审计**：写方法（POST/PUT/PATCH/DELETE）成功响应 → audit_log 行
2. **/api/audit-logs** admin 才能访问；member → 403
3. **team 隔离**：跨 team audit_logs 不可见
4. **过滤参数**：action / target_type / actor_id 生效
5. **auth 路径跳过**：/api/auth/login 等不写 audit
6. **GET 不审计**：只写方法才审计
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.core.security import create_access_token
from app.main import app
from app.models.audit import AuditLog
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


async def _create_member_in_team(team_id: str) -> tuple[str, str]:
    """在已存在 team 内手工塞一个 member 用户并签 token。"""
    async with AsyncSessionLocal() as session:
        user = User(
            email=f"m-{uuid.uuid4().hex[:6]}@x.com",
            password_hash="x",
            name="member",
            role="member",
            team_id=uuid.UUID(team_id),
        )
        session.add(user)
        await session.commit()
        token = create_access_token(
            subject=user.id,
            extra_claims={
                "team_id": team_id,
                "role": "member",
                "email": user.email,
            },
        )
        return token, str(user.id)


async def _create_admin_in_other_team() -> tuple[str, str, str]:
    """新建另一个 team + admin。"""
    async with AsyncSessionLocal() as session:
        team = Team(name=f"other-{uuid.uuid4().hex[:6]}")
        session.add(team)
        await session.flush()
        user = User(
            email=f"o-{uuid.uuid4().hex[:6]}@x.com",
            password_hash="x",
            name="other-admin",
            role="admin",
            team_id=team.id,
        )
        session.add(user)
        await session.commit()
        token = create_access_token(
            subject=user.id,
            extra_claims={
                "team_id": str(team.id),
                "role": "admin",
                "email": user.email,
            },
        )
        return token, str(team.id), str(user.id)


# ============================================================================
# 权限：admin only
# ============================================================================


class TestAuditLogPermissions:
    async def test_member_returns_403(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        member_token, _ = await _create_member_in_team(admin["team_id"])
        resp = await client.get(
            "/api/audit-logs/", headers=_auth(member_token)
        )
        assert resp.status_code == 403, resp.text

    async def test_unauthorized_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get("/api/audit-logs/")
        assert resp.status_code == 401

    async def test_admin_can_list(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        resp = await client.get(
            "/api/audit-logs/", headers=_auth(admin["token"])
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body
        assert "total" in body


# ============================================================================
# middleware 自动审计
# ============================================================================


class TestAuditMiddleware:
    async def test_write_method_recorded(self, client: AsyncClient) -> None:
        """POST /api/jobs 触发 middleware 写 audit_log。"""
        admin = await _register_admin(client)
        resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={
                "title": "Eng",
                "jd_text": "Python",
                "status": "active",
                "hard_requirements": {
                    "min_education": "master",
                    "min_years": 3,
                    "required_skills": ["Python"],
                    "excluded_companies": [],
                },
            },
        )
        assert resp.status_code == 201, resp.text

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(AuditLog).where(AuditLog.action == "POST /api/jobs/")
            )
            rows = result.scalars().all()
            assert len(rows) == 1
            assert str(rows[0].actor_id) == admin["user_id"]

    async def test_get_not_recorded(self, client: AsyncClient) -> None:
        """GET 不审计。"""
        admin = await _register_admin(client)
        await client.get("/api/jobs/", headers=_auth(admin["token"]))

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(AuditLog))
            rows = result.scalars().all()
            assert len(rows) == 0

    async def test_failed_write_not_recorded(self, client: AsyncClient) -> None:
        """4xx/5xx 不审计。"""
        admin = await _register_admin(client)
        # 缺 jd_text → 422
        resp = await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "Eng"},
        )
        assert resp.status_code == 422

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(AuditLog))
            rows = result.scalars().all()
            assert len(rows) == 0

    async def test_auth_paths_skipped(self, client: AsyncClient) -> None:
        """register/login/refresh 不审计。"""
        await _register_admin(client)
        await client.post(
            "/api/auth/login",
            json={"email": "admin@example.com", "password": "Pass1234"},
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(select(AuditLog))
            rows = result.scalars().all()
            assert len(rows) == 0

    async def test_records_ip_and_user_agent(self, client: AsyncClient) -> None:
        """middleware 记录 IP + user-agent（通过 X-Forwarded-For）。"""
        admin = await _register_admin(client)
        resp = await client.post(
            "/api/jobs/",
            headers={
                **_auth(admin["token"]),
                "X-Forwarded-For": "203.0.113.5",
                "User-Agent": "TestAgent/1.0",
            },
            json={
                "title": "Eng",
                "jd_text": "Python",
                "status": "active",
                "hard_requirements": {
                    "min_education": None,
                    "min_years": None,
                    "required_skills": [],
                    "excluded_companies": [],
                },
            },
        )
        assert resp.status_code == 201

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(AuditLog))
            row = result.scalars().first()
            assert row is not None
            assert str(row.ip) == "203.0.113.5"
            assert row.user_agent == "TestAgent/1.0"


# ============================================================================
# team 隔离 + 过滤
# ============================================================================


class TestAuditLogQuery:
    async def test_team_isolation(self, client: AsyncClient) -> None:
        """team1 admin 看不到 team2 的 audit_logs。"""
        admin1 = await _register_admin(client)
        admin2_token, _, _ = await _create_admin_in_other_team()

        # team1 写
        await client.post(
            "/api/jobs/",
            headers=_auth(admin1["token"]),
            json={
                "title": "T1", "jd_text": "x", "status": "active",
                "hard_requirements": {
                    "min_education": None, "min_years": None,
                    "required_skills": [], "excluded_companies": [],
                },
            },
        )
        # team2 写
        await client.post(
            "/api/jobs/",
            headers=_auth(admin2_token),
            json={
                "title": "T2", "jd_text": "x", "status": "active",
                "hard_requirements": {
                    "min_education": None, "min_years": None,
                    "required_skills": [], "excluded_companies": [],
                },
            },
        )

        # team1 admin 查
        resp = await client.get(
            "/api/audit-logs/", headers=_auth(admin1["token"])
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        # 只看到 team1 自己的写
        assert all(
            i["actor_id"] == admin1["user_id"] for i in items
        )

    async def test_filter_by_action(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        # 写 2 个 job
        for _ in range(2):
            await client.post(
                "/api/jobs/",
                headers=_auth(admin["token"]),
                json={
                    "title": "E", "jd_text": "x", "status": "active",
                    "hard_requirements": {
                        "min_education": None, "min_years": None,
                        "required_skills": [], "excluded_companies": [],
                    },
                },
            )

        resp = await client.get(
            "/api/audit-logs/?action=POST%20/api/jobs/",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 2
        for it in body["items"]:
            assert it["action"] == "POST /api/jobs/"

    async def test_filter_by_actor_id(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={
                "title": "E", "jd_text": "x", "status": "active",
                "hard_requirements": {
                    "min_education": None, "min_years": None,
                    "required_skills": [], "excluded_companies": [],
                },
            },
        )

        resp = await client.get(
            f"/api/audit-logs/?actor_id={admin['user_id']}",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] >= 1
        for it in body["items"]:
            assert it["actor_id"] == admin["user_id"]

    async def test_pagination(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        # 写 5 个 job
        for _ in range(5):
            await client.post(
                "/api/jobs/",
                headers=_auth(admin["token"]),
                json={
                    "title": "E", "jd_text": "x", "status": "active",
                    "hard_requirements": {
                        "min_education": None, "min_years": None,
                        "required_skills": [], "excluded_companies": [],
                    },
                },
            )

        resp = await client.get(
            "/api/audit-logs/?page=1&page_size=2",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200
        body = resp.json()
        assert body["total"] == 5
        assert len(body["items"]) == 2
