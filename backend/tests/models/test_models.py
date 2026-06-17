"""ORM 模型集成测试：CRUD + 外键级联 + 唯一约束 + CITEXT。

通过 docker-compose 中的 PostgreSQL 实例执行（非 mock），验证：
- candidates.phone/email 经 EncryptedString 自动加解密
- screening_results UNIQUE(job_id, candidate_id) 约束生效
- jobs.team_id 外键 ON DELETE CASCADE
- users.email CITEXT 大小写不敏感
- screening_results.manually_overridden 默认 False
- JSONB 字段读写

策略：所有 setup 内联到测试函数，避免 async fixture 跨 loop
（pytest-asyncio 1.x session loop scope 下 fixture 仍可能跨 loop）。
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text
from sqlalchemy.exc import IntegrityError

from app.core.db import AsyncSessionLocal
from app.models.async_job import AsyncJob
from app.models.audit import AuditLog
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.dedup import DedupMatch
from app.models.email_config import EmailConfig
from app.models.interview import InterviewFeedback, InterviewQuestion
from app.models.job import Job, JobHardRequirement, JobVersion
from app.models.llm_call import LLMCall
from app.models.score import Score, ScoreReason
from app.models.screening import ManualOverride, ScreeningResult
from app.models.team import Team
from app.models.user import User


# ============================================================================
# 内联辅助：在测试函数内一次性创建关联实体
# ============================================================================


async def _create_team_user_job() -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """创建 team + user + job 一条龙，返回三者 UUID。"""
    async with AsyncSessionLocal() as session:
        team = Team(name=f"team-{uuid.uuid4().hex[:8]}")
        session.add(team)
        await session.flush()

        user = User(
            email=f"user-{uuid.uuid4().hex[:8]}@example.com",
            password_hash="$2b$12$abcd",
            name="测试用户",
            role="admin",
            team_id=team.id,
        )
        session.add(user)
        await session.flush()

        job = Job(
            team_id=team.id,
            title=f"岗位-{uuid.uuid4().hex[:8]}",
            jd_text="岗位描述示例",
            status="active",
            current_version=1,
            created_by=user.id,
        )
        session.add(job)
        await session.commit()
        await session.refresh(team)
        await session.refresh(user)
        await session.refresh(job)
        return team.id, user.id, job.id


async def _create_candidate(team_id: uuid.UUID) -> uuid.UUID:
    async with AsyncSessionLocal() as session:
        c = Candidate(
            team_id=team_id,
            dedup_key=uuid.uuid4().hex,
            name="张三",
            phone="13812345678",
            email="zhangsan@example.com",
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return c.id


# ============================================================================
# PII 自动加解密往返
# ============================================================================


class TestCandidatePII:
    """candidates.name/phone/email 经 EncryptedString 自动加解密。"""

    @pytest.mark.asyncio
    async def test_pii_encrypted_at_rest(self) -> None:
        """DB 中存密文，ORM 读出明文。"""
        team_id, _, _ = await _create_team_user_job()
        cid = uuid.uuid4().hex

        async with AsyncSessionLocal() as session:
            c = Candidate(
                team_id=team_id,
                dedup_key=cid,
                name="李四",
                phone="18600001111",
                email="lisi@example.com",
            )
            session.add(c)
            await session.commit()
            await session.refresh(c)
            c_uuid = c.id

        async with AsyncSessionLocal() as session:
            raw = await session.execute(
                text(
                    "SELECT name, phone, email FROM candidates WHERE id = :id"
                ),
                {"id": str(c_uuid)},
            )
            row = raw.one()
            assert row.name != "李四"
            assert row.phone != "18600001111"
            assert row.email != "lisi@example.com"

        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(Candidate).where(Candidate.id == c_uuid)
            )
            c_loaded = result.scalar_one()
            assert c_loaded.name == "李四"
            assert c_loaded.phone == "18600001111"
            assert c_loaded.email == "lisi@example.com"


# ============================================================================
# 唯一约束
# ============================================================================


class TestUniqueConstraints:
    """UNIQUE 约束生效。"""

    @pytest.mark.asyncio
    async def test_screening_result_unique_job_candidate(self) -> None:
        """UNIQUE(job_id, candidate_id)：同对第二次插入应失败。"""
        team_id, _, job_id = await _create_team_user_job()
        candidate_id = await _create_candidate(team_id)

        async with AsyncSessionLocal() as session:
            session.add(
                ScreeningResult(
                    job_id=job_id,
                    candidate_id=candidate_id,
                    disqualified=False,
                    reasons=[],
                )
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            session.add(
                ScreeningResult(
                    job_id=job_id,
                    candidate_id=candidate_id,
                    disqualified=True,
                    reasons=["学历不达标"],
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_candidates_dedup_key_unique(self) -> None:
        """dedup_key UNIQUE。"""
        team_id, _, _ = await _create_team_user_job()
        key = uuid.uuid4().hex

        async with AsyncSessionLocal() as session:
            session.add(Candidate(team_id=team_id, dedup_key=key, name="A"))
            await session.commit()

        async with AsyncSessionLocal() as session:
            session.add(Candidate(team_id=team_id, dedup_key=key, name="B"))
            with pytest.raises(IntegrityError):
                await session.commit()

    @pytest.mark.asyncio
    async def test_async_jobs_idempotency_key_unique(self) -> None:
        idem = uuid.uuid4().hex
        async with AsyncSessionLocal() as session:
            session.add(
                AsyncJob(
                    task_type="parse",
                    status="queued",
                    attempts=0,
                    idempotency_key=idem,
                )
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            session.add(
                AsyncJob(
                    task_type="parse",
                    status="queued",
                    attempts=0,
                    idempotency_key=idem,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()


# ============================================================================
# CITEXT email
# ============================================================================


class TestCITextEmail:
    """users.email CITEXT 大小写不敏感唯一。"""

    @pytest.mark.asyncio
    async def test_email_case_insensitive_unique(self) -> None:
        team_id, _, _ = await _create_team_user_job()
        email_local = uuid.uuid4().hex[:8]

        async with AsyncSessionLocal() as session:
            session.add(
                User(
                    email=f"{email_local.capitalize()}@Example.com",
                    password_hash="$2b$12$xxx",
                    name="Alice",
                    role="member",
                    team_id=team_id,
                )
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            session.add(
                User(
                    email=f"{email_local}@example.com",
                    password_hash="$2b$12$yyy",
                    name="Alice 2",
                    role="member",
                    team_id=team_id,
                )
            )
            with pytest.raises(IntegrityError):
                await session.commit()


# ============================================================================
# 外键级联
# ============================================================================


class TestForeignKeyCascade:
    """外键 ON DELETE CASCADE。"""

    @pytest.mark.asyncio
    async def test_job_cascade_deletes_screening(self) -> None:
        team_id, _, _ = await _create_team_user_job()
        candidate_id = await _create_candidate(team_id)

        async with AsyncSessionLocal() as session:
            team = await session.get(Team, team_id)
            user = await session.execute(
                select(User).where(User.team_id == team_id).limit(1)
            )
            user_obj = user.scalar_one()
            job = Job(
                team_id=team_id,
                title="待删岗位",
                jd_text="...",
                status="draft",
                created_by=user_obj.id,
            )
            session.add(job)
            await session.flush()

            r = ScreeningResult(
                job_id=job.id,
                candidate_id=candidate_id,
                disqualified=False,
                reasons=[],
            )
            session.add(r)
            await session.commit()
            job_id = job.id

        async with AsyncSessionLocal() as session:
            j_loaded = await session.get(Job, job_id)
            assert j_loaded is not None
            await session.delete(j_loaded)
            await session.commit()

        async with AsyncSessionLocal() as session:
            leftover = (
                await session.execute(
                    select(ScreeningResult).where(ScreeningResult.job_id == job_id)
                )
            ).all()
            assert leftover == []


# ============================================================================
# 默认值与时间戳
# ============================================================================


class TestDefaults:
    """默认值与 server_default。"""

    @pytest.mark.asyncio
    async def test_screening_manually_overridden_defaults_false(self) -> None:
        team_id, _, job_id = await _create_team_user_job()
        candidate_id = await _create_candidate(team_id)

        async with AsyncSessionLocal() as session:
            r = ScreeningResult(
                job_id=job_id,
                candidate_id=candidate_id,
                disqualified=False,
                reasons=[],
            )
            session.add(r)
            await session.commit()
            await session.refresh(r)
            assert r.manually_overridden is False
            assert r.created_at is not None

    @pytest.mark.asyncio
    async def test_timestamps_auto_set(self) -> None:
        """TimestampMixin 的 created_at / updated_at 应被 DB 默认值填充。"""
        team_id, _, _ = await _create_team_user_job()

        async with AsyncSessionLocal() as session:
            ec = EmailConfig(
                team_id=team_id,
                imap_host="imap.example.com",
                imap_port=993,
                username="bot@example.com",
                password_enc="secret-password",
            )
            session.add(ec)
            await session.commit()
            await session.refresh(ec)
            assert ec.created_at is not None
            assert ec.updated_at is not None


# ============================================================================
# JSONB 字段读写
# ============================================================================


class TestJSONBFields:
    """JSONB 字段读写（reasons / similarity / payload 等）。"""

    @pytest.mark.asyncio
    async def test_screening_reasons_jsonb(self) -> None:
        team_id, _, job_id = await _create_team_user_job()
        candidate_id = await _create_candidate(team_id)

        async with AsyncSessionLocal() as session:
            r = ScreeningResult(
                job_id=job_id,
                candidate_id=candidate_id,
                disqualified=True,
                reasons=["学历不达标", "必备技能缺失"],
            )
            session.add(r)
            await session.commit()
            await session.refresh(r)
            assert r.reasons == ["学历不达标", "必备技能缺失"]
