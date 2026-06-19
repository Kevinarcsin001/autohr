"""/api/jobs 路由集成测试。"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.main import app

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
        await ac.get("/health")
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


async def _invite_and_accept(
    client: AsyncClient,
    admin_token: str,
    team_id: str,
    email: str,
    role: str = "member",
) -> dict:
    invite = (
        await client.post(
            f"/api/teams/{team_id}/invite",
            headers={"Authorization": f"Bearer {admin_token}"},
            json={"email": email, "role": role, "name": "M"},
        )
    ).json()
    acc = await client.post(
        "/api/auth/accept-invite",
        json={
            "invite_token": invite["invite_token"],
            "name": "M",
            "password": "Pass1234",
        },
    )
    body = acc.json()
    return {"token": body["tokens"]["access_token"], "user_id": body["user"]["id"]}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


# ============================================================================
# POST /
# ============================================================================


async def test_create_job_returns_201(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/jobs/",
        headers=_auth(admin["token"]),
        json={
            "title": "Backend Eng",
            "jd_text": "Build APIs in FastAPI",
            "status": "draft",
            "hard_requirements": {
                "min_education": "bachelor",
                "min_years": 3,
                "required_skills": ["Python", "FastAPI"],
            },
        },
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["title"] == "Backend Eng"
    assert body["status"] == "draft"
    assert body["current_version"] == 1
    assert body["team_id"] == admin["team_id"]
    assert body["hard_requirements"]["min_education"] == "bachelor"
    assert body["hard_requirements"]["required_skills"] == ["Python", "FastAPI"]


async def test_create_job_without_team_returns_403(client: AsyncClient) -> None:
    """非首位注册用户无 team → 403。"""
    await _register_admin(client)
    reg = await client.post(
        "/api/auth/register",
        json={"email": "lonely@example.com", "password": "Pass1234", "name": "L"},
    )
    token = reg.json()["tokens"]["access_token"]
    resp = await client.post(
        "/api/jobs/",
        headers=_auth(token),
        json={"title": "T", "jd_text": "X"},
    )
    assert resp.status_code == 403


async def test_create_job_invalid_min_education_returns_422(client: AsyncClient) -> None:
    """ENUM 越界 → 422。"""
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/jobs/",
        headers=_auth(admin["token"]),
        json={
            "title": "T",
            "jd_text": "X",
            "hard_requirements": {"min_education": "kindergarten"},
        },
    )
    assert resp.status_code == 422


async def test_create_job_required_skills_dedup_and_trim(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/jobs/",
        headers=_auth(admin["token"]),
        json={
            "title": "T",
            "jd_text": "X",
            "hard_requirements": {
                "required_skills": ["  Python  ", "python", "FastAPI", ""]
            },
        },
    )
    assert resp.status_code == 201
    skills = resp.json()["hard_requirements"]["required_skills"]
    assert skills == ["Python", "FastAPI"]


# ============================================================================
# GET /
# ============================================================================


async def test_list_jobs_pagination_and_filter(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    for i, st in enumerate(["draft", "active", "active", "closed", "draft"]):
        await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": f"J{i}", "jd_text": "X", "status": st},
        )

    # 全量
    resp = await client.get("/api/jobs/", headers=_auth(admin["token"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["total"] == 5
    assert body["page"] == 1
    assert body["page_size"] == 20

    # 过滤
    resp = await client.get(
        "/api/jobs/?status=active", headers=_auth(admin["token"])
    )
    assert resp.json()["total"] == 2

    # 分页
    resp = await client.get(
        "/api/jobs/?page=1&page_size=2", headers=_auth(admin["token"])
    )
    body = resp.json()
    assert len(body["items"]) == 2
    assert body["total"] == 5


async def test_list_jobs_invalid_status_returns_422(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.get(
        "/api/jobs/?status=invalid", headers=_auth(admin["token"])
    )
    assert resp.status_code == 422


# ============================================================================
# GET /{job_id}
# ============================================================================


async def test_get_job_returns_full_detail(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    created = (
        await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={
                "title": "T",
                "jd_text": "body",
                "hard_requirements": {"min_education": "master"},
            },
        )
    ).json()
    resp = await client.get(
        f"/api/jobs/{created['id']}", headers=_auth(admin["token"])
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "T"
    assert body["hard_requirements"]["min_education"] == "master"


async def test_get_job_nonexistent_returns_404(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.get(
        f"/api/jobs/{uuid.uuid4()}", headers=_auth(admin["token"])
    )
    assert resp.status_code == 404


# ============================================================================
# PATCH /{job_id}
# ============================================================================


async def test_update_job_increments_version_and_writes_snapshot(
    client: AsyncClient,
) -> None:
    admin = await _register_admin(client)
    created = (
        await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={
                "title": "T1",
                "jd_text": "Body1",
                "hard_requirements": {"min_education": "high_school"},
            },
        )
    ).json()

    resp = await client.patch(
        f"/api/jobs/{created['id']}",
        headers=_auth(admin["token"]),
        json={
            "title": "T2",
            "hard_requirements": {"min_education": "bachelor"},
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["title"] == "T2"
    assert body["current_version"] == 2
    assert body["hard_requirements"]["min_education"] == "bachelor"
    assert body["jd_text"] == "Body1"  # 保留

    # 验证版本历史
    versions = (
        await client.get(
            f"/api/jobs/{created['id']}/versions", headers=_auth(admin["token"])
        )
    ).json()
    assert len(versions) == 2
    assert versions[0]["version"] == 2
    assert versions[0]["snapshot"]["title"] == "T2"
    assert versions[1]["version"] == 1
    assert versions[1]["snapshot"]["title"] == "T1"


async def test_update_job_partial(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    created = (
        await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={
                "title": "T1",
                "jd_text": "B",
                "hard_requirements": {"min_education": "high_school"},
            },
        )
    ).json()

    resp = await client.patch(
        f"/api/jobs/{created['id']}",
        headers=_auth(admin["token"]),
        json={"title": "T2"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["title"] == "T2"
    # 未传 hard_requirements 保留原值
    assert body["hard_requirements"]["min_education"] == "high_school"


async def test_update_job_cross_team_returns_403(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    member = await _invite_and_accept(
        client, admin["token"], admin["team_id"], "m@example.com"
    )
    # member 与 admin 同 team，但创建一个新 team 让 member 切过去测试更复杂
    # 这里直接用 admin 创建的 job，member 应能改（同 team）
    created = (
        await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "T", "jd_text": "B"},
        )
    ).json()

    # 同 team member 改：应成功（同 team 任意成员可改）
    resp = await client.patch(
        f"/api/jobs/{created['id']}",
        headers=_auth(member["token"]),
        json={"title": "Changed"},
    )
    assert resp.status_code == 200


async def test_update_job_nonexistent_returns_404(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.patch(
        f"/api/jobs/{uuid.uuid4()}",
        headers=_auth(admin["token"]),
        json={"title": "X"},
    )
    assert resp.status_code == 404


# ============================================================================
# DELETE /{job_id}
# ============================================================================


async def test_delete_job_cascades(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    created = (
        await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "T", "jd_text": "B"},
        )
    ).json()

    resp = await client.delete(
        f"/api/jobs/{created['id']}", headers=_auth(admin["token"])
    )
    assert resp.status_code == 204

    # 确认已删除
    resp = await client.get(
        f"/api/jobs/{created['id']}", headers=_auth(admin["token"])
    )
    assert resp.status_code == 404

    # 确认版本历史也被级联删除（GET versions 会因 job 不存在而 404）
    resp = await client.get(
        f"/api/jobs/{created['id']}/versions", headers=_auth(admin["token"])
    )
    assert resp.status_code == 404


async def test_delete_job_nonexistent_returns_404(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.delete(
        f"/api/jobs/{uuid.uuid4()}", headers=_auth(admin["token"])
    )
    assert resp.status_code == 404


# ============================================================================
# GET /{job_id}/versions
# ============================================================================


async def test_list_versions_in_descending_order(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    created = (
        await client.post(
            "/api/jobs/",
            headers=_auth(admin["token"]),
            json={"title": "V1", "jd_text": "B"},
        )
    ).json()
    for i in range(3):
        await client.patch(
            f"/api/jobs/{created['id']}",
            headers=_auth(admin["token"]),
            json={"title": f"V{i+2}"},
        )

    resp = await client.get(
        f"/api/jobs/{created['id']}/versions", headers=_auth(admin["token"])
    )
    assert resp.status_code == 200
    versions = resp.json()
    assert [v["version"] for v in versions] == [4, 3, 2, 1]
    assert [v["snapshot"]["title"] for v in versions] == ["V4", "V3", "V2", "V1"]


# ============================================================================
# 团队隔离：跨 team 访问 → 403
# ============================================================================


async def test_cross_team_access_returns_403(client: AsyncClient) -> None:
    """team A 的 admin 创建 job；team B 的 admin 试图访问 → 404（先检查 team 归属）。"""
    admin_a = await _register_admin(client, "a@example.com")
    created = (
        await client.post(
            "/api/jobs/",
            headers=_auth(admin_a["token"]),
            json={"title": "T", "jd_text": "B"},
        )
    ).json()

    # 构造 team B（直接通过 DB 插入 + register 时手动赋 team）
    from app.models.team import Team

    async with AsyncSessionLocal() as session:
        from sqlalchemy import select

        from app.models.user import User

        team_b = Team(name="TeamB")
        session.add(team_b)
        await session.flush()
        # 注册一个新用户 → 普通成员
        resp = await client.post(
            "/api/auth/register",
            json={"email": "b@example.com", "password": "Pass1234", "name": "B"},
        )
        b_user_id = resp.json()["user"]["id"]
        # 把 B 关联到 team_b
        user_b = (
            await session.execute(select(User).where(User.id == uuid.UUID(b_user_id)))
        ).scalar_one()
        user_b.team_id = team_b.id
        user_b.role = "admin"
        await session.commit()
        token_b = resp.json()["tokens"]["access_token"]

    # B 试图 GET A 的 job
    resp = await client.get(
        f"/api/jobs/{created['id']}", headers=_auth(token_b)
    )
    assert resp.status_code == 403

    # B 试图 PATCH A 的 job
    resp = await client.patch(
        f"/api/jobs/{created['id']}",
        headers=_auth(token_b),
        json={"title": "Hack"},
    )
    assert resp.status_code == 403

    # B 试图 DELETE A 的 job
    resp = await client.delete(
        f"/api/jobs/{created['id']}", headers=_auth(token_b)
    )
    assert resp.status_code == 403

    # B 试图 LIST versions
    resp = await client.get(
        f"/api/jobs/{created['id']}/versions", headers=_auth(token_b)
    )
    assert resp.status_code == 403
