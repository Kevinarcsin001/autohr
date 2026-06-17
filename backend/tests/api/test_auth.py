"""认证 API 集成测试（httpx.AsyncClient + ASGITransport）。

使用异步 client 而非 TestClient，所有 await 跑在同一 event loop，
避免全局 asyncpg 连接池跨 loop（"attached to a different loop"）。

覆盖：
- POST /api/auth/register   返回 AuthResponse + 设置 refresh cookie
- POST /api/auth/login      CITEXT 大小写不敏感
- POST /api/auth/refresh    cookie 兜底 body
- POST /api/auth/logout     清除 cookie
- POST /api/auth/invite     admin only
- POST /api/auth/accept-invite
"""
from __future__ import annotations

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.main import app


async def _purge_db() -> None:
    """TRUNCATE 所有业务表（CASCADE 解决 jobs.created_by NOT NULL 问题）。"""
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
    """异步 HTTP 客户端 + 启动/关闭 lifespan（让 engine 在同一 loop 初始化）。

    注意：因为 app 内 engine是模块级单例，且已绑定到首次访问它的 loop，
    所以这里需要复用同一 loop。pytest-asyncio session-scoped loop 保证这一点。
    """
    transport = ASGITransport(app=app)
    async with AsyncClient(transport=transport, base_url="http://test") as ac:
        # 触发 lifespan startup
        await ac.get("/health")
        yield ac


@pytest.fixture(autouse=True)
async def clean_db():
    """每个测试前后清空 DB。"""
    await _purge_db()
    yield
    await _purge_db()


# ============================================================================
# /register
# ============================================================================


async def test_register_first_user_admin_with_team(client: AsyncClient) -> None:
    """首位注册：role=admin，team_id 非空。"""
    resp = await client.post(
        "/api/auth/register",
        json={"email": "first@example.com", "password": "Pass1234", "name": "F"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["role"] == "admin"
    assert body["user"]["team_id"] is not None
    assert body["tokens"]["access_token"]
    assert body["tokens"]["refresh_token"]
    cookies = resp.headers.get_list("set-cookie")
    assert any("autohr_refresh=" in c for c in cookies)


async def test_register_second_user_member_no_team(client: AsyncClient) -> None:
    """第二位注册：role=member，无 team。"""
    await client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "password": "Pass1234", "name": "A"},
    )
    resp = await client.post(
        "/api/auth/register",
        json={"email": "b@example.com", "password": "Pass1234", "name": "B"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["user"]["role"] == "member"
    assert body["user"]["team_id"] is None


async def test_register_duplicate_returns_409(client: AsyncClient) -> None:
    """重复邮箱：409 Conflict。"""
    payload = {"email": "dup@example.com", "password": "Pass1234", "name": "A"}
    await client.post("/api/auth/register", json=payload)
    resp = await client.post("/api/auth/register", json=payload)
    assert resp.status_code == 409


async def test_register_weak_password_returns_422(client: AsyncClient) -> None:
    """密码不合规（无数字）：422 ValidationError。"""
    resp = await client.post(
        "/api/auth/register",
        json={"email": "x@example.com", "password": "abcdefgh", "name": "X"},
    )
    assert resp.status_code == 422


# ============================================================================
# /login
# ============================================================================


async def test_login_case_insensitive_email(client: AsyncClient) -> None:
    """CITEXT 大小写不敏感登录。"""
    await client.post(
        "/api/auth/register",
        json={"email": "Case.User@Example.com", "password": "Pass1234", "name": "A"},
    )
    resp = await client.post(
        "/api/auth/login",
        json={"email": "case.user@example.com", "password": "Pass1234"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["tokens"]["access_token"]


async def test_login_wrong_password_returns_401(client: AsyncClient) -> None:
    """密码错误：401。"""
    await client.post(
        "/api/auth/register",
        json={"email": "u@example.com", "password": "Pass1234", "name": "A"},
    )
    resp = await client.post(
        "/api/auth/login",
        json={"email": "u@example.com", "password": "Wrong123"},
    )
    assert resp.status_code == 401


# ============================================================================
# /refresh
# ============================================================================


async def test_refresh_via_cookie_returns_new_access(client: AsyncClient) -> None:
    """refresh cookie 自动带，返回新 access。"""
    await client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "password": "Pass1234", "name": "A"},
    )
    resp = await client.post("/api/auth/refresh")
    assert resp.status_code == 200, resp.text
    assert resp.json()["access_token"]


async def test_refresh_via_body_returns_new_access(client: AsyncClient) -> None:
    """非浏览器客户端用 body 兜底。"""
    reg = await client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "password": "Pass1234", "name": "A"},
    )
    refresh = reg.json()["tokens"]["refresh_token"]
    client.cookies.clear()
    resp = await client.post("/api/auth/refresh", json={"refresh_token": refresh})
    assert resp.status_code == 200
    assert resp.json()["access_token"]


async def test_refresh_with_invalid_token_returns_401(client: AsyncClient) -> None:
    """无效 refresh：401。"""
    resp = await client.post(
        "/api/auth/refresh", json={"refresh_token": "invalid-token-xyz"}
    )
    assert resp.status_code == 401


async def test_logout_clears_cookie(client: AsyncClient) -> None:
    """logout 清除 refresh cookie。"""
    await client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "password": "Pass1234", "name": "A"},
    )
    resp = await client.post("/api/auth/logout")
    assert resp.status_code == 204
    cookies = resp.headers.get_list("set-cookie")
    assert any("autohr_refresh=" in c and ("Max-Age=0" in c or "deleted" in c) for c in cookies)


# ============================================================================
# /invite + /accept-invite
# ============================================================================


async def _admin_token(client: AsyncClient) -> str:
    """快捷：注册首位（admin）→ 返回 access token。"""
    reg = await client.post(
        "/api/auth/register",
        json={"email": "admin@example.com", "password": "Pass1234", "name": "A"},
    )
    return reg.json()["tokens"]["access_token"]


async def test_invite_requires_admin(client: AsyncClient) -> None:
    """非 admin 调用 /invite 应 403。"""
    await client.post(
        "/api/auth/register",
        json={"email": "admin@example.com", "password": "Pass1234", "name": "A"},
    )
    member = await client.post(
        "/api/auth/register",
        json={"email": "m@example.com", "password": "Pass1234", "name": "M"},
    )
    member_token = member.json()["tokens"]["access_token"]
    resp = await client.post(
        "/api/auth/invite",
        headers={"Authorization": f"Bearer {member_token}"},
        json={"email": "x@example.com", "role": "member"},
    )
    assert resp.status_code == 403


async def test_invite_returns_token(client: AsyncClient) -> None:
    """admin 调用 /invite 返回 InviteOut。"""
    token = await _admin_token(client)
    resp = await client.post(
        "/api/auth/invite",
        headers={"Authorization": f"Bearer {token}"},
        json={"email": "new@example.com", "role": "member", "name": "新"},
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["invite_token"]
    assert body["email"] == "new@example.com"


async def test_accept_invite_full_flow(client: AsyncClient) -> None:
    """admin 邀请 → accept → 用户加入 team。"""
    token = await _admin_token(client)
    invite = (
        await client.post(
            "/api/auth/invite",
            headers={"Authorization": f"Bearer {token}"},
            json={"email": "new@example.com", "role": "member", "name": "新"},
        )
    ).json()

    accept = await client.post(
        "/api/auth/accept-invite",
        json={
            "invite_token": invite["invite_token"],
            "name": "新员工",
            "password": "Pass1234",
        },
    )
    assert accept.status_code == 200, accept.text
    body = accept.json()
    assert body["user"]["email"] == "new@example.com"
    assert body["user"]["role"] == "member"
    assert body["user"]["team_id"]


async def test_accept_invite_one_time(client: AsyncClient) -> None:
    """token 一次性：再次 accept 应 401。"""
    token = await _admin_token(client)
    invite = (
        await client.post(
            "/api/auth/invite",
            headers={"Authorization": f"Bearer {token}"},
            json={"email": "new@example.com", "role": "member"},
        )
    ).json()

    await client.post(
        "/api/auth/accept-invite",
        json={
            "invite_token": invite["invite_token"],
            "name": "X",
            "password": "Pass1234",
        },
    )
    second = await client.post(
        "/api/auth/accept-invite",
        json={
            "invite_token": invite["invite_token"],
            "name": "Y",
            "password": "Pass1234",
        },
    )
    assert second.status_code == 401


async def test_get_me_returns_current_user(client: AsyncClient) -> None:
    """GET /api/auth/me 返回当前登录用户。"""
    reg = await client.post(
        "/api/auth/register",
        json={"email": "a@example.com", "password": "Pass1234", "name": "Alice"},
    )
    token = reg.json()["tokens"]["access_token"]
    resp = await client.get(
        "/api/auth/me", headers={"Authorization": f"Bearer {token}"}
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["email"] == "a@example.com"
    assert body["name"] == "Alice"


async def test_get_me_without_token_returns_401(client: AsyncClient) -> None:
    """无 token 调 /api/auth/me：401。"""
    resp = await client.get("/api/auth/me")
    assert resp.status_code == 401
