"""/api/uploads 路由集成测试（任务 9）。"""
from __future__ import annotations

import uuid

import httpx
import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.async_job import AsyncJob


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


def _auth(token: str) -> dict:
    return {"Authorization": f"Bearer {token}"}


def _pdf_bytes() -> bytes:
    return (
        b"%PDF-1.4\n"
        b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
        b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 100 100]>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \n"
        b"trailer<</Root 1 0 R/Size 4>>\nstartxref\n0\n%%EOF"
    )


# ============================================================================
# POST /api/uploads/intent
# ============================================================================


async def test_intent_unauthenticated_returns_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/uploads/intent",
        json={"files": [{"filename": "a.pdf", "size_bytes": 100, "mime_client": "application/pdf"}]},
    )
    assert resp.status_code == 401


async def test_intent_ok_returns_signed_urls(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/uploads/intent",
        headers=_auth(admin["token"]),
        json={
            "files": [
                {"filename": "r1.pdf", "size_bytes": 1024, "mime_client": "application/pdf"},
                {"filename": "r2.pdf", "size_bytes": 1024, "mime_client": "application/pdf"},
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 2
    assert body["rejected"] == 0
    assert all(it["status"] == "ok" for it in body["items"])
    assert all(it["signed_url"] for it in body["items"])


async def test_intent_oversize_rejected_per_item(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/uploads/intent",
        headers=_auth(admin["token"]),
        json={
            "files": [
                {"filename": "big.pdf", "size_bytes": 21 * 1024 * 1024, "mime_client": "application/pdf"},
                {"filename": "ok.pdf", "size_bytes": 100, "mime_client": "application/pdf"},
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["accepted"] == 1
    assert body["rejected"] == 1
    rejected = next(it for it in body["items"] if it["status"] == "rejected")
    assert rejected["reject_reason"] == "size_exceeded"


async def test_intent_wrong_extension_rejected(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/uploads/intent",
        headers=_auth(admin["token"]),
        json={
            "files": [
                {"filename": "malware.exe", "size_bytes": 100, "mime_client": "application/x-msdownload"}
            ]
        },
    )
    body = resp.json()
    assert body["items"][0]["status"] == "rejected"
    assert body["items"][0]["reject_reason"] == "extension_not_allowed"


async def test_intent_batch_too_large_returns_422(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/uploads/intent",
        headers=_auth(admin["token"]),
        json={
            "files": [
                {"filename": f"x{i}.pdf", "size_bytes": 100, "mime_client": "application/pdf"}
                for i in range(101)
            ]
        },
    )
    assert resp.status_code == 422


async def test_intent_empty_files_returns_422(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/uploads/intent",
        headers=_auth(admin["token"]),
        json={"files": []},
    )
    assert resp.status_code == 422


# ============================================================================
# POST /api/uploads/confirm
# ============================================================================


async def test_confirm_full_flow_writes_db(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    pdf = _pdf_bytes()
    intent = (
        await client.post(
            "/api/uploads/intent",
            headers=_auth(admin["token"]),
            json={
                "files": [
                    {"filename": "r.pdf", "size_bytes": len(pdf), "mime_client": "application/pdf"}
                ]
            },
        )
    ).json()
    file_key = intent["items"][0]["file_key"]
    signed_url = intent["items"][0]["signed_url"]
    upload_id = intent["items"][0]["upload_id"]

    # 客户端直传
    async with httpx.AsyncClient() as c:
        put_resp = await c.put(signed_url, content=pdf)
    assert put_resp.status_code == 200

    # confirm
    resp = await client.post(
        "/api/uploads/confirm",
        headers=_auth(admin["token"]),
        json={"items": [{"upload_id": upload_id, "file_key": file_key}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["confirmed"] == 1
    assert body["items"][0]["status"] == "ok"
    assert body["items"][0]["resume_id"]

    # DB 副作用
    async with AsyncSessionLocal() as session:
        jobs = (
            await session.execute(select(AsyncJob).where(AsyncJob.task_type == "parse"))
        ).scalars().all()
    assert len(jobs) == 1
    assert jobs[0].status == "queued"


async def test_confirm_object_missing(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    intent = (
        await client.post(
            "/api/uploads/intent",
            headers=_auth(admin["token"]),
            json={
                "files": [
                    {"filename": "ghost.pdf", "size_bytes": 100, "mime_client": "application/pdf"}
                ]
            },
        )
    ).json()
    # 不 PUT，直接 confirm
    resp = await client.post(
        "/api/uploads/confirm",
        headers=_auth(admin["token"]),
        json={
            "items": [
                {
                    "upload_id": intent["items"][0]["upload_id"],
                    "file_key": intent["items"][0]["file_key"],
                }
            ]
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["status"] == "rejected"
    assert body["items"][0]["reject_reason"] == "object_missing"


async def test_confirm_cross_team_rejected(client: AsyncClient) -> None:
    """team_a 上传，team_b 用户调 confirm → cross_team rejected。

    构造 team_b admin：直接 SQL 创建独立 team + user，再用该 user 的密码登录拿 token。
    """
    from app.core.security import hash_password
    from app.models.team import Team
    from app.models.user import User

    admin_a = await _register_admin(client, email="a@example.com")

    # 直接 SQL 创建 team_b + admin_b（绕过 register "首位用户才建 team" 限制）
    async with AsyncSessionLocal() as session:
        team_b = Team(name=f"team_b-{uuid.uuid4().hex[:8]}")
        session.add(team_b)
        await session.flush()
        admin_b_email = f"b{uuid.uuid4().hex[:8]}@example.com"
        admin_b = User(
            email=admin_b_email,
            password_hash=hash_password("Pass1234"),
            name="B",
            role="admin",
            team_id=team_b.id,
        )
        session.add(admin_b)
        await session.commit()

    # admin_b 登录拿 token
    login_resp = await client.post(
        "/api/auth/login",
        json={"email": admin_b_email, "password": "Pass1234"},
    )
    assert login_resp.status_code == 200, login_resp.text
    token_b = login_resp.json()["tokens"]["access_token"]

    pdf = _pdf_bytes()
    intent = (
        await client.post(
            "/api/uploads/intent",
            headers=_auth(admin_a["token"]),
            json={
                "files": [
                    {"filename": "r.pdf", "size_bytes": len(pdf), "mime_client": "application/pdf"}
                ]
            },
        )
    ).json()
    file_key = intent["items"][0]["file_key"]
    upload_id = intent["items"][0]["upload_id"]

    async with httpx.AsyncClient() as c:
        await c.put(intent["items"][0]["signed_url"], content=pdf)

    # team_b 用户调 confirm team_a 的 file_key
    resp = await client.post(
        "/api/uploads/confirm",
        headers=_auth(token_b),
        json={"items": [{"upload_id": upload_id, "file_key": file_key}]},
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["items"][0]["status"] == "rejected"
    assert body["items"][0]["reject_reason"] == "cross_team"
