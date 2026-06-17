"""/api/email-configs 路由集成测试（任务 11）。

覆盖：
- POST/GET/PATCH/DELETE CRUD（admin only）
- password 永不回显
- GET /status 返回运行状态摘要
- PATCH clear_alert=True 清除退避状态
- 非 admin 用户访问 → 403
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.core.security import hash_password
from app.main import app
from app.models.email_config import EmailConfig
from app.models.team import Team
from app.models.user import User


# ============================================================================
# fixtures
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


async def _register_member(client: AsyncClient, admin_token: str) -> dict:
    """注册一个 member 用户（先注册成 admin，再降权 → 模拟 member）。"""
    # 通过 admin 邀请 + 接受邀请太繁琐；直接 SQL 创建
    async with AsyncSessionLocal() as session:
        result = await session.execute(select(User).where(User.role == "admin"))
        admin = result.scalars().first()
        member_email = f"member-{uuid.uuid4().hex[:8]}@example.com"
        member = User(
            email=member_email,
            password_hash=hash_password("Pass1234"),
            name="Member",
            role="member",
            team_id=admin.team_id,
        )
        session.add(member)
        await session.commit()

    login = await client.post(
        "/api/auth/login",
        json={"email": member_email, "password": "Pass1234"},
    )
    return {"token": login.json()["tokens"]["access_token"]}


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _create_payload() -> dict:
    return {
        "imap_host": "imap.qq.com",
        "imap_port": 993,
        "username": "box@example.com",
        "password": "super-secret",
        "poll_interval_min": 15,
        "enabled": True,
    }


# ============================================================================
# POST /api/email-configs/
# ============================================================================


async def test_create_unauthenticated_returns_401(client: AsyncClient) -> None:
    resp = await client.post("/api/email-configs/", json=_create_payload())
    assert resp.status_code == 401


async def test_create_member_forbidden(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    member = await _register_member(client, admin["token"])
    resp = await client.post(
        "/api/email-configs/", headers=_auth(member["token"]), json=_create_payload()
    )
    assert resp.status_code == 403


async def test_create_ok_and_omits_password(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/email-configs/", headers=_auth(admin["token"]), json=_create_payload()
    )
    assert resp.status_code == 201, resp.text
    body = resp.json()
    assert body["imap_host"] == "imap.qq.com"
    assert body["imap_port"] == 993
    assert body["username"] == "box@example.com"
    assert body["poll_interval_min"] == 15
    assert body["enabled"] is True
    # 关键：password 永不回显
    assert "password" not in body
    assert "password_enc" not in body


async def test_create_twice_conflict(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp1 = await client.post(
        "/api/email-configs/", headers=_auth(admin["token"]), json=_create_payload()
    )
    assert resp1.status_code == 201
    resp2 = await client.post(
        "/api/email-configs/", headers=_auth(admin["token"]), json=_create_payload()
    )
    assert resp2.status_code == 409


# ============================================================================
# GET /api/email-configs/
# ============================================================================


async def test_get_returns_none_before_create(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.get("/api/email-configs/", headers=_auth(admin["token"]))
    assert resp.status_code == 200
    assert resp.json() is None


async def test_get_returns_config_after_create(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    await client.post(
        "/api/email-configs/", headers=_auth(admin["token"]), json=_create_payload()
    )
    resp = await client.get("/api/email-configs/", headers=_auth(admin["token"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["imap_host"] == "imap.qq.com"
    assert "password" not in body


# ============================================================================
# GET /api/email-configs/status
# ============================================================================


async def test_status_unconfigured(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.get("/api/email-configs/status", headers=_auth(admin["token"]))
    assert resp.status_code == 200
    body = resp.json()
    assert body["configured"] is False
    assert body["enabled"] is False
    assert body["is_paused"] is False
    assert body["alert_level"] == "none"


async def test_status_healthy_after_create(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    await client.post(
        "/api/email-configs/", headers=_auth(admin["token"]), json=_create_payload()
    )
    resp = await client.get("/api/email-configs/status", headers=_auth(admin["token"]))
    body = resp.json()
    assert body["configured"] is True
    assert body["enabled"] is True
    assert body["is_paused"] is False
    assert body["alert_level"] == "none"
    assert body["next_scheduled_in_seconds"] == 15 * 60


async def test_status_reflects_paused_and_alert(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    create_resp = await client.post(
        "/api/email-configs/", headers=_auth(admin["token"]), json=_create_payload()
    )
    cfg_id = create_resp.json()["id"]

    # 直接 SQL 把 cfg 置成 critical 暂停
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EmailConfig).where(EmailConfig.id == uuid.UUID(cfg_id))
        )
        cfg = result.scalar_one()
        cfg.consecutive_failures = 5
        cfg.alert_level = "critical"
        cfg.paused_until = datetime.now(timezone.utc) + timedelta(minutes=20)
        await session.commit()

    resp = await client.get("/api/email-configs/status", headers=_auth(admin["token"]))
    body = resp.json()
    assert body["is_paused"] is True
    assert body["alert_level"] == "critical"
    assert body["consecutive_failures"] == 5
    # paused 时不调度
    assert body["next_scheduled_in_seconds"] is None


# ============================================================================
# PATCH /api/email-configs/
# ============================================================================


async def test_patch_updates_fields_except_password_echo(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    await client.post(
        "/api/email-configs/", headers=_auth(admin["token"]), json=_create_payload()
    )
    resp = await client.patch(
        "/api/email-configs/",
        headers=_auth(admin["token"]),
        json={
            "imap_host": "imap.gmail.com",
            "imap_port": 993,
            "poll_interval_min": 30,
            "enabled": False,
            "password": "new-secret",
        },
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["imap_host"] == "imap.gmail.com"
    assert body["poll_interval_min"] == 30
    assert body["enabled"] is False
    assert "password" not in body


async def test_patch_clear_alert_resets_backoff(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    create_resp = await client.post(
        "/api/email-configs/", headers=_auth(admin["token"]), json=_create_payload()
    )
    cfg_id = create_resp.json()["id"]

    # 置为 critical 暂停
    async with AsyncSessionLocal() as session:
        result = await session.execute(
            select(EmailConfig).where(EmailConfig.id == uuid.UUID(cfg_id))
        )
        cfg = result.scalar_one()
        cfg.consecutive_failures = 5
        cfg.alert_level = "critical"
        cfg.paused_until = datetime.now(timezone.utc) + timedelta(minutes=20)
        cfg.last_error_summary = "auth fail"
        await session.commit()

    resp = await client.patch(
        "/api/email-configs/",
        headers=_auth(admin["token"]),
        json={"clear_alert": True},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["consecutive_failures"] == 0
    assert body["alert_level"] == "none"
    assert body["paused_until"] is None
    assert body["last_error_summary"] is None


# ============================================================================
# DELETE /api/email-configs/
# ============================================================================


async def test_delete_removes_config(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    await client.post(
        "/api/email-configs/", headers=_auth(admin["token"]), json=_create_payload()
    )
    resp = await client.delete("/api/email-configs/", headers=_auth(admin["token"]))
    assert resp.status_code == 204

    # GET 现在返回 None
    get_resp = await client.get("/api/email-configs/", headers=_auth(admin["token"]))
    assert get_resp.json() is None


async def test_delete_when_not_configured_returns_404(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.delete("/api/email-configs/", headers=_auth(admin["token"]))
    assert resp.status_code == 404
