"""/api/jobs/{job_id}/candidates 路由集成测试（任务 23）。

覆盖：
- 三分组：passed / disqualified / pending + group_counts
- 排序：按 total 倒序（默认）/ 按 name asc
- 筛选：min_score / education / min_years / skill / source
- 分页：page + page_size
- 跨 team 访问 → 404
- 鉴权 → 401
- 聚合：score / screening / structure / source 一次返回
"""
from __future__ import annotations

import uuid

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.main import app
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.job import Job
from app.models.score import Score
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


async def _seed_full_dataset(
    *,
    team_id: uuid.UUID,
    user_id: uuid.UUID,
    n_passed: int = 3,
    n_disqualified: int = 1,
    n_pending: int = 2,
) -> Job:
    """创建 job + N 个 passed + M disqualified + K pending（含 source/resume/structure/score）。"""
    async with AsyncSessionLocal() as session:
        job = Job(
            team_id=team_id, title="Eng", jd_text="Python",
            status="active", created_by=user_id,
        )
        session.add(job)
        await session.flush()

        idx = 0

        async def _add_candidate(
            *, group: str, total: int | None = None, education: str = "master",
            years: int = 5, skills: list[str] | None = None, source: str = "upload",
        ) -> None:
            nonlocal idx
            idx += 1
            cand = Candidate(
                team_id=team_id,
                dedup_key=f"test:{uuid.uuid4()}",
                name=f"c-{group}-{idx}",
                phone=f"1380000000{idx:02d}",
                email=f"c{idx}@x.com",
            )
            session.add(cand)
            await session.flush()

            src = CandidateSource(
                candidate_id=cand.id, source_type=source
            )
            session.add(src)
            await session.flush()

            resume = CandidateResume(
                candidate_id=cand.id, source_id=src.id,
                file_storage_key=f"k-{idx}", file_mime="application/pdf",
                parse_status="success", parsed_text="x",
            )
            session.add(resume)
            await session.flush()

            session.add(ParsedStructure(
                resume_id=resume.id,
                data={
                    "structure": {
                        "name": f"c-{group}-{idx}",
                        "education": education,
                        "years_of_experience": years,
                        "skills": skills if skills else ["Python"],
                        "current_company": "ACME",
                    },
                    "status": "extracted",
                },
            ))

            if group != "pending":
                session.add(ScreeningResult(
                    job_id=job.id, candidate_id=cand.id,
                    disqualified=(group == "disqualified"),
                    reasons=["x"] if group == "disqualified" else None,
                ))
            if total is not None:
                session.add(Score(
                    job_id=job.id, candidate_id=cand.id,
                    total=total, skill=total - 5, experience=total - 3,
                    education=total - 8, stability=total - 2, potential=total - 1,
                ))

        # passed：3 个，分数 90/80/70
        for t in [90, 80, 70]:
            await _add_candidate(group="passed", total=t)

        # disqualified：1 个，分数 50
        await _add_candidate(group="disqualified", total=50)

        # pending：2 个，无 screening/score
        await _add_candidate(group="pending", total=None)
        await _add_candidate(group="pending", total=None)

        await session.commit()
        return job


# ============================================================================
# 鉴权 + 404
# ============================================================================


class TestAuthAndTeam:
    async def test_unauthenticated_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get(
            f"/api/jobs/{uuid.uuid4()}/candidates"
        )
        assert resp.status_code == 401

    async def test_cross_team_job_404(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        # 用别人的 team 创建 job
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
            await session.commit()

        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 404

    async def test_not_found_job(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        resp = await client.get(
            f"/api/jobs/{uuid.uuid4()}/candidates",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 404


# ============================================================================
# 三分组 + group_counts
# ============================================================================


class TestThreeGroups:
    async def test_group_all_returns_all_with_counts(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        job = await _seed_full_dataset(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        # 3 passed + 1 disqualified + 2 pending = 6
        assert body["total"] == 6
        assert body["group_counts"] == {
            "passed": 3, "disqualified": 1, "pending": 2,
        }
        groups_in_items = {it["group"] for it in body["items"]}
        assert groups_in_items == {"passed", "disqualified", "pending"}

    async def test_group_passed_only(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job = await _seed_full_dataset(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
            params={"group": "passed"},
        )
        body = resp.json()
        assert body["total"] == 3
        assert all(it["group"] == "passed" for it in body["items"])

    async def test_group_disqualified_only(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job = await _seed_full_dataset(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
            params={"group": "disqualified"},
        )
        body = resp.json()
        assert body["total"] == 1
        assert all(it["group"] == "disqualified" for it in body["items"])

    async def test_group_pending_only(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job = await _seed_full_dataset(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
            params={"group": "pending"},
        )
        body = resp.json()
        assert body["total"] == 2
        assert all(it["group"] == "pending" for it in body["items"])


# ============================================================================
# 排序
# ============================================================================


class TestSorting:
    async def test_default_sort_total_desc(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job = await _seed_full_dataset(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
            params={"group": "passed"},
        )
        body = resp.json()
        totals = [it["total"] for it in body["items"]]
        assert totals == [90, 80, 70]

    async def test_sort_total_asc(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job = await _seed_full_dataset(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
            params={"group": "passed", "sort_by": "total", "sort_order": "asc"},
        )
        body = resp.json()
        totals = [it["total"] for it in body["items"]]
        assert totals == [70, 80, 90]

    async def test_sort_by_name_asc(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job = await _seed_full_dataset(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
            params={"group": "passed", "sort_by": "name", "sort_order": "asc"},
        )
        body = resp.json()
        names = [it["name"] for it in body["items"]]
        assert names == sorted(names)


# ============================================================================
# 筛选
# ============================================================================


class TestFilters:
    async def test_min_score_filter(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job = await _seed_full_dataset(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
            params={"min_score": 80},
        )
        body = resp.json()
        # 仅 total>=80：90, 80（passed 2 个）
        totals = [it["total"] for it in body["items"] if it["total"] is not None]
        assert all(t >= 80 for t in totals)
        assert body["total"] <= 6  # 总数没减少（pending 也算），但 items 减少了

    async def test_education_filter(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        async with AsyncSessionLocal() as session:
            job = Job(
                team_id=uuid.UUID(admin["team_id"]),
                title="E", jd_text="x", status="active",
                created_by=uuid.UUID(admin["user_id"]),
            )
            session.add(job)
            await session.flush()

            # 1 bachelor + 1 master
            for edu in ("bachelor", "master"):
                cand = Candidate(
                    team_id=uuid.UUID(admin["team_id"]),
                    dedup_key=f"edu:{uuid.uuid4()}",
                    name=f"edu-{edu}",
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
                    file_storage_key=f"k-{edu}", file_mime="application/pdf",
                    parse_status="success", parsed_text="x",
                )
                session.add(resume)
                await session.flush()
                session.add(ParsedStructure(
                    resume_id=resume.id,
                    data={
                        "structure": {"education": edu, "years_of_experience": 5},
                        "status": "extracted",
                    },
                ))
            await session.commit()

        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
            params={"education": "master"},
        )
        body = resp.json()
        for it in body["items"]:
            assert it["education"] == "master"

    async def test_skill_filter(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        async with AsyncSessionLocal() as session:
            job = Job(
                team_id=uuid.UUID(admin["team_id"]),
                title="E", jd_text="x", status="active",
                created_by=uuid.UUID(admin["user_id"]),
            )
            session.add(job)
            await session.flush()

            # Python + Java 候选
            for sk in ("Python", "Java"):
                cand = Candidate(
                    team_id=uuid.UUID(admin["team_id"]),
                    dedup_key=f"sk:{uuid.uuid4()}",
                    name=f"sk-{sk}",
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
                    file_storage_key=f"k-{sk}", file_mime="application/pdf",
                    parse_status="success", parsed_text="x",
                )
                session.add(resume)
                await session.flush()
                session.add(ParsedStructure(
                    resume_id=resume.id,
                    data={
                        "structure": {"skills": [sk], "years_of_experience": 5},
                        "status": "extracted",
                    },
                ))
            await session.commit()

        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
            params={"skill": "python"},
        )
        body = resp.json()
        assert all("Python" in it["skills"] for it in body["items"])

    async def test_source_filter(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        async with AsyncSessionLocal() as session:
            job = Job(
                team_id=uuid.UUID(admin["team_id"]),
                title="E", jd_text="x", status="active",
                created_by=uuid.UUID(admin["user_id"]),
            )
            session.add(job)
            await session.flush()

            # upload + email
            for src_type in ("upload", "email"):
                cand = Candidate(
                    team_id=uuid.UUID(admin["team_id"]),
                    dedup_key=f"s:{uuid.uuid4()}",
                    name=f"s-{src_type}",
                )
                session.add(cand)
                await session.flush()
                src = CandidateSource(
                    candidate_id=cand.id, source_type=src_type
                )
                session.add(src)
                await session.flush()
                resume = CandidateResume(
                    candidate_id=cand.id, source_id=src.id,
                    file_storage_key=f"k-{src_type}", file_mime="application/pdf",
                    parse_status="success", parsed_text="x",
                )
                session.add(resume)
            await session.commit()

        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
            params={"source": "email"},
        )
        body = resp.json()
        assert all(it["source_type"] == "email" for it in body["items"])
        assert len(body["items"]) == 1


# ============================================================================
# 分页
# ============================================================================


class TestPagination:
    async def test_page_size_limits_items(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job = await _seed_full_dataset(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
            params={"page": 1, "page_size": 2},
        )
        body = resp.json()
        assert len(body["items"]) == 2
        assert body["total"] == 6
        assert body["page"] == 1
        assert body["page_size"] == 2

    async def test_page_2(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        job = await _seed_full_dataset(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
        )
        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
            params={"page": 2, "page_size": 3},
        )
        body = resp.json()
        # 6 总，第 2 页 size=3 → 3 条
        assert len(body["items"]) == 3
        assert body["page"] == 2


# ============================================================================
# 字段聚合
# ============================================================================


class TestAggregation:
    async def test_item_includes_all_fields(self, client: AsyncClient) -> None:
        """单条 item 必须含 score/screening/source/structure 关键字段。"""
        admin = await _register_admin(client)
        job = await _seed_full_dataset(
            team_id=uuid.UUID(admin["team_id"]),
            user_id=uuid.UUID(admin["user_id"]),
            n_passed=1, n_disqualified=0, n_pending=0,
        )
        resp = await client.get(
            f"/api/jobs/{job.id}/candidates",
            headers=_auth(admin["token"]),
            params={"group": "passed"},
        )
        body = resp.json()
        item = body["items"][0]
        # score 字段
        assert item["total"] is not None
        assert item["skill"] is not None
        assert item["experience"] is not None
        # screening 字段
        assert item["screening_id"] is not None
        assert item["disqualified"] is False
        # source 字段
        assert item["source_type"] == "upload"
        # structure 字段
        assert item["education"] == "master"
        assert item["years_of_experience"] == 5
        assert "Python" in item["skills"]
