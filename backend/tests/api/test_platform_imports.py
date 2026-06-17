"""/api/platform-imports 路由集成测试（任务 10）。"""
from __future__ import annotations

import io
import uuid
import zipfile

import pytest
from httpx import ASGITransport, AsyncClient
from openpyxl import Workbook
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.candidate import Candidate


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


def _boss_excel_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.title = "Boss直聘"
    ws.append(["姓名", "电话", "邮箱", "学历", "工作年限"])
    ws.append(["张三", "13800138000", "z@x.com", "本科", 5])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


def _unknown_excel_bytes() -> bytes:
    wb = Workbook()
    ws = wb.active
    ws.append(["foo", "bar", "baz"])
    ws.append(["a", "b", "c"])
    buf = io.BytesIO()
    wb.save(buf)
    return buf.getvalue()


# ============================================================================
# POST /detect
# ============================================================================


async def test_detect_unauthenticated_returns_401(client: AsyncClient) -> None:
    resp = await client.post(
        "/api/platform-imports/detect",
        files={"file": ("boss.xlsx", _boss_excel_bytes(), "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet")},
    )
    assert resp.status_code == 401


async def test_detect_boss_returns_200(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/platform-imports/detect",
        headers=_auth(admin["token"]),
        files={
            "file": (
                "boss.xlsx",
                _boss_excel_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["platform"] == "boss"
    assert body["package_kind"] == "excel"


async def test_detect_unsupported_returns_200_with_null_platform(
    client: AsyncClient,
) -> None:
    """detect 端点永远 200（仅检测），unsupported 由 import 端点返回 422。"""
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/platform-imports/detect",
        headers=_auth(admin["token"]),
        files={
            "file": (
                "random.xlsx",
                _unknown_excel_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert resp.status_code == 200
    body = resp.json()
    assert body["platform"] is None


# ============================================================================
# POST /
# ============================================================================


async def test_import_boss_excel_writes_db(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/platform-imports/",
        headers=_auth(admin["token"]),
        files={
            "file": (
                "boss.xlsx",
                _boss_excel_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["platform"] == "boss"
    assert body["imported"] == 1
    assert len(body["candidates"]) == 1
    assert body["candidates"][0]["name"] == "张三"

    async with AsyncSessionLocal() as session:
        cands = (await session.execute(select(Candidate))).scalars().all()
    assert len(cands) == 1


async def test_import_unsupported_returns_422(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/platform-imports/",
        headers=_auth(admin["token"]),
        files={
            "file": (
                "random.xlsx",
                _unknown_excel_bytes(),
                "application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
            )
        },
    )
    assert resp.status_code == 422
    body = resp.json()
    err = body["error"]
    assert err["code"] == "unsupported_platform"
    # error_handler 把 context 字段透传：含 detection + feedback_url
    assert "support_feedback_url" in err


async def test_import_empty_file_returns_422(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/platform-imports/",
        headers=_auth(admin["token"]),
        files={"file": ("empty.xlsx", b"", "application/octet-stream")},
    )
    assert resp.status_code == 422


async def test_import_attachment_zip(client: AsyncClient) -> None:
    pdf_bytes = (
        b"%PDF-1.4\n1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
        b"xref\n0 4\n0000000000 65535 f \ntrailer<</Root 1 0 R>>\n%%EOF"
    )
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w") as zf:
        zf.writestr("boss_resume.pdf", pdf_bytes)
    admin = await _register_admin(client)
    resp = await client.post(
        "/api/platform-imports/",
        headers=_auth(admin["token"]),
        files={"file": ("boss.zip", buf.getvalue(), "application/zip")},
    )
    assert resp.status_code == 201
    body = resp.json()
    assert body["package_kind"] == "attachment_zip"
    assert body["imported"] == 1
