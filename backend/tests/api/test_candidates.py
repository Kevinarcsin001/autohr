"""/api/candidates 路由集成测试（任务 15）。"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.candidate import Candidate, CandidateResume, CandidateSource
from app.models.team import Team
from app.models.user import User
from app.services.dedup import DedupService

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


async def _seed_candidate_in_team(team_id: str, name: str = "张三") -> str:
    """直接写库创建 candidate，返回 id。"""
    async with AsyncSessionLocal() as session:
        c = Candidate(
            team_id=uuid.UUID(team_id),
            dedup_key=f"test:{uuid.uuid4()}",
            name=name,
            phone="13800138000",
            email=f"{name}@x.com",
        )
        session.add(c)
        await session.flush()
        src = CandidateSource(candidate_id=c.id, source_type="upload")
        session.add(src)
        await session.flush()
        resume = CandidateResume(
            candidate_id=c.id,
            source_id=src.id,
            file_storage_key=f"k/{uuid.uuid4()}",
            file_mime="application/pdf",
            parse_status="success",
        )
        session.add(resume)
        await session.commit()
        return str(c.id)


# ============================================================================
# 列表
# ============================================================================


async def test_list_dedup_matches_empty(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.get(
        "/api/candidates/dedup-matches", headers=_auth(admin["token"])
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["items"] == []
    assert body["total"] == 0


async def test_list_dedup_matches_returns_pending(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    team_id = admin["team_id"]
    a_id = await _seed_candidate_in_team(team_id, name="A")
    b_id = await _seed_candidate_in_team(team_id, name="B")

    async with AsyncSessionLocal() as session:
        await DedupService(session).flag_for_review(
            candidate_a=uuid.UUID(a_id),
            candidate_b=uuid.UUID(b_id),
            similarity={"phone_match": 1.0},
        )
        await session.commit()

    resp = await client.get(
        "/api/candidates/dedup-matches", headers=_auth(admin["token"])
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["total"] == 1
    assert body["items"][0]["status"] == "pending"
    assert body["items"][0]["similarity"] == {"phone_match": 1.0}
    # 候选人姓名回填
    assert body["items"][0]["name_a"] in {"A", "B"}
    assert body["items"][0]["name_b"] in {"A", "B"}


# ============================================================================
# 合并
# ============================================================================


async def test_merge_returns_summary(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    team_id = admin["team_id"]
    src_id = await _seed_candidate_in_team(team_id, name="src")
    dst_id = await _seed_candidate_in_team(team_id, name="dst")

    resp = await client.post(
        "/api/candidates/merge",
        headers=_auth(admin["token"]),
        json={"src_id": src_id, "dst_id": dst_id},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["merged_id"] == dst_id
    assert body["archived_id"] == src_id
    assert body["sources_moved"] >= 1
    assert body["resumes_moved"] >= 1

    # 验证 src 已 merged_into
    async with AsyncSessionLocal() as session:
        src = await session.get(Candidate, uuid.UUID(src_id))
    assert str(src.merged_into) == dst_id


async def test_merge_cross_team_returns_404(client: AsyncClient) -> None:
    """非首位注册用户无 team；用直接 DB 写入另一个 team + candidate。
    admin_a 试图合并 B team 的 dst → 应 404（不暴露存在性）。
    """
    admin = await _register_admin(client)

    # 直接在 DB 创建一个独立 team + candidate
    async with AsyncSessionLocal() as session:
        other_team = Team(name="other-team")
        session.add(other_team)
        await session.flush()
        other_candidate = Candidate(
            team_id=other_team.id,
            dedup_key=f"test:{uuid.uuid4()}",
            name="Other",
        )
        session.add(other_candidate)
        await session.commit()
        other_id = str(other_candidate.id)

    # admin 用自己的 src 试图合并 other_id → 应 404
    src_id = await _seed_candidate_in_team(admin["team_id"])
    resp = await client.post(
        "/api/candidates/merge",
        headers=_auth(admin["token"]),
        json={"src_id": src_id, "dst_id": other_id},
    )
    assert resp.status_code == 404


async def test_merge_unknown_candidate_returns_404(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    dst = await _seed_candidate_in_team(admin["team_id"])
    resp = await client.post(
        "/api/candidates/merge",
        headers=_auth(admin["token"]),
        json={"src_id": str(uuid.uuid4()), "dst_id": dst},
    )
    assert resp.status_code == 404


# ============================================================================
# 决议
# ============================================================================


async def test_decide_match_merged(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    team_id = admin["team_id"]
    a_id = await _seed_candidate_in_team(team_id, name="A")
    b_id = await _seed_candidate_in_team(team_id, name="B")

    async with AsyncSessionLocal() as session:
        match = await DedupService(session).flag_for_review(
            candidate_a=uuid.UUID(a_id),
            candidate_b=uuid.UUID(b_id),
            similarity={"k": 1},
        )
        await session.commit()
        match_id = str(match.id)

    resp = await client.patch(
        f"/api/candidates/dedup-matches/{match_id}",
        headers=_auth(admin["token"]),
        json={"decision": "merged"},
    )
    assert resp.status_code == 200, resp.text
    body = resp.json()
    assert body["status"] == "merged"


async def test_decide_match_rejected(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    team_id = admin["team_id"]
    a_id = await _seed_candidate_in_team(team_id, name="A")
    b_id = await _seed_candidate_in_team(team_id, name="B")

    async with AsyncSessionLocal() as session:
        match = await DedupService(session).flag_for_review(
            candidate_a=uuid.UUID(a_id),
            candidate_b=uuid.UUID(b_id),
            similarity={"k": 1},
        )
        await session.commit()
        match_id = str(match.id)

    resp = await client.patch(
        f"/api/candidates/dedup-matches/{match_id}",
        headers=_auth(admin["token"]),
        json={"decision": "rejected"},
    )
    assert resp.status_code == 200, resp.text
    assert resp.json()["status"] == "rejected"


async def test_decide_unknown_match_returns_404(client: AsyncClient) -> None:
    admin = await _register_admin(client)
    resp = await client.patch(
        f"/api/candidates/dedup-matches/{uuid.uuid4()}",
        headers=_auth(admin["token"]),
        json={"decision": "rejected"},
    )
    assert resp.status_code == 404


async def test_endpoints_require_auth(client: AsyncClient) -> None:
    resp = await client.get("/api/candidates/dedup-matches")
    assert resp.status_code == 401

    resp = await client.post(
        "/api/candidates/merge",
        json={"src_id": str(uuid.uuid4()), "dst_id": str(uuid.uuid4())},
    )
    assert resp.status_code == 401
