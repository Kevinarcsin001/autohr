"""/api/teams 路由集成测试。"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.main import app


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


# ============================================================================
# 工具
# ============================================================================


async def _register_admin(client: AsyncClient, email: str = "admin@example.com") -> dict:
    """注册 admin 并返回 {token, team_id, user_id}。"""
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
    """通过 admin 邀请 + accept，返回新成员 {token, user_id}。"""
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


# ============================================================================
# GET /me
# ============================================================================


async def test_get_my_team_returns_team_detail(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.get(
        "/api/teams/me", headers={"Authorization": f"Bearer {admin['token']}"}
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["team"]["id"] == admin["team_id"]
    assert len(body["members"]) == 1
    assert body["members"][0]["role"] == "admin"


async def test_get_my_team_without_team_returns_403(client: AsyncClient) -> None:
    """用户未加入 team（非首位注册）→ 403。"""
    await _register_admin(client)  # 第一位 admin
    reg = await client.post(
        "/api/auth/register",
        json={"email": "lonely@example.com", "password": "Pass1234", "name": "L"},
    )
    token = reg.json()["tokens"]["access_token"]
    resp = await client.get("/api/teams/me", headers={"Authorization": f"Bearer {token}"})
    assert resp.status_code == 403


# ============================================================================
# GET /{team_id}/members
# ============================================================================


async def test_list_members_as_admin(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    member = await _invite_and_accept(
        client, admin["token"], admin["team_id"], "m@example.com"
    )
    resp = await client.get(
        f"/api/teams/{admin['team_id']}/members",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
    member_ids = [m["id"] for m in body]
    assert admin["user_id"] in member_ids
    assert member["user_id"] in member_ids


async def test_list_members_for_other_team_returns_403(client: AsyncClient) -> None:
    """访问他人 team / 不存在的 team：403。"""
    import uuid

    admin = await _register_admin(client, "admin1@example.com")
    # 试图访问随机 team_id（不属于自己）
    random_team_id = uuid.uuid4()
    resp = await client.get(
        f"/api/teams/{random_team_id}/members",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert resp.status_code == 403


# ============================================================================
# POST /{team_id}/invite
# ============================================================================


async def test_invite_as_admin_succeeds(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.post(
        f"/api/teams/{admin['team_id']}/invite",
        headers={"Authorization": f"Bearer {admin['token']}"},
        json={"email": "new@example.com", "role": "member", "name": "N"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["invite_token"]
    assert body["email"] == "new@example.com"


async def test_invite_as_member_returns_403(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    member = await _invite_and_accept(
        client, admin["token"], admin["team_id"], "m@example.com"
    )
    resp = await client.post(
        f"/api/teams/{admin['team_id']}/invite",
        headers={"Authorization": f"Bearer {member['token']}"},
        json={"email": "x@example.com", "role": "member"},
    )
    assert resp.status_code == 403


# ============================================================================
# PATCH /{team_id}/members/{user_id}/role
# ============================================================================


async def test_update_role_promote(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    member = await _invite_and_accept(
        client, admin["token"], admin["team_id"], "m@example.com"
    )
    resp = await client.patch(
        f"/api/teams/{admin['team_id']}/members/{member['user_id']}/role",
        headers={"Authorization": f"Bearer {admin['token']}"},
        json={"role": "admin"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["role"] == "admin"


async def test_update_role_self_returns_403(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.patch(
        f"/api/teams/{admin['team_id']}/members/{admin['user_id']}/role",
        headers={"Authorization": f"Bearer {admin['token']}"},
        json={"role": "member"},
    )
    assert resp.status_code == 403


async def test_update_role_demote_self_blocked_by_forbidden(client: AsyncClient) -> None:
    """team 仅 1 admin，降级自己 → 403（被改自己拦截先于 last-admin 检查）。"""
    admin = await _register_admin(client)
    # 没有 member，只有 1 admin
    resp = await client.patch(
        f"/api/teams/{admin['team_id']}/members/{admin['user_id']}/role",
        headers={"Authorization": f"Bearer {admin['token']}"},
        json={"role": "member"},
    )
    # 改自己先被拦截
    assert resp.status_code == 403


async def test_update_role_member_only_returns_403(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    member = await _invite_and_accept(
        client, admin["token"], admin["team_id"], "m@example.com"
    )
    resp = await client.patch(
        f"/api/teams/{admin['team_id']}/members/{admin['user_id']}/role",
        headers={"Authorization": f"Bearer {member['token']}"},
        json={"role": "member"},
    )
    assert resp.status_code == 403


# ============================================================================
# DELETE /{team_id}/members/{user_id}
# ============================================================================


async def test_remove_member_succeeds(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    member = await _invite_and_accept(
        client, admin["token"], admin["team_id"], "m@example.com"
    )
    resp = await client.delete(
        f"/api/teams/{admin['team_id']}/members/{member['user_id']}",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert resp.status_code == 204
    # member 已解绑，team 列表中不应再有
    list_resp = await client.get(
        f"/api/teams/{admin['team_id']}/members",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    member_ids = [m["id"] for m in list_resp.json()]
    assert member["user_id"] not in member_ids


async def test_remove_self_returns_403(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.delete(
        f"/api/teams/{admin['team_id']}/members/{admin['user_id']}",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert resp.status_code == 403


# ============================================================================
# GET /{team_id}/invites
# ============================================================================


async def test_list_team_invites(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    await client.post(
        f"/api/teams/{admin['team_id']}/invite",
        headers={"Authorization": f"Bearer {admin['token']}"},
        json={"email": "a@example.com", "role": "member"},
    )
    await client.post(
        f"/api/teams/{admin['team_id']}/invite",
        headers={"Authorization": f"Bearer {admin['token']}"},
        json={"email": "b@example.com", "role": "member"},
    )
    resp = await client.get(
        f"/api/teams/{admin['team_id']}/invites",
        headers={"Authorization": f"Bearer {admin['token']}"},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert len(body) == 2
