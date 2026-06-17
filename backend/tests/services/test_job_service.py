"""job_service 集成测试。

覆盖：
- create_job：team 隔离；写 v1 快照；初始化 JobHardRequirement
- update_job：跨 team 403；current_version 自增；写新快照；hard_requirements 整体替换
- get_job：跨 team 403
- list_jobs：team 隔离；status 过滤；分页
- list_versions：历史快照按 version 倒序
- delete_job：跨 team 403；级联清理 versions / hard_requirements

策略：通过 register 构造 admin/team，直接调用 service。
"""
from __future__ import annotations

from uuid import uuid4

import pytest
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.core.middleware.error_handler import ForbiddenError, NotFoundError
from app.models.job import Job, JobHardRequirement, JobVersion
from app.schemas.job import (
    HardRequirements,
    JobCreateRequest,
    JobUpdateRequest,
)
from app.services import auth_service, job_service
from sqlalchemy import select


# ============================================================================
# 工具
# ============================================================================


async def _purge() -> None:
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


async def _bootstrap_team_with_admin() -> tuple:
    """构造首位 admin + team，返回 (team_id, admin_user)。"""
    async with AsyncSessionLocal() as session:
        admin = await auth_service.register(
            session,
            email="admin@example.com",
            password="Pass1234",
            name="A",
        )
        await session.commit()
        return admin.team_id, admin


def _make_create(
    title: str = "J1",
    jd_text: str = "JD body",
    *,
    status: str = "draft",
    hard: HardRequirements | None = None,
    llm_config: dict | None = None,
) -> JobCreateRequest:
    return JobCreateRequest(
        title=title,
        jd_text=jd_text,
        status=status,
        hard_requirements=hard or HardRequirements(),
        llm_config=llm_config,
    )


@pytest.fixture(autouse=True)
async def clean_db():
    await _purge()
    yield
    await _purge()


# ============================================================================
# create_job
# ============================================================================


async def test_create_job_initializes_v1_snapshot_and_hard_requirements() -> None:
    team_id, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        hard = HardRequirements(
            min_education="bachelor",
            min_years=3,
            required_skills=["Python", "FastAPI"],
            excluded_companies=["BadCorp"],
        )
        payload = _make_create(hard=hard)
        job = await job_service.create_job(
            session,
            team_id=team_id,
            created_by=admin.id,
            payload=payload,
        )
        await session.commit()

        assert job.current_version == 1
        assert job.title == "J1"
        assert job.team_id == team_id

        # 验证 v1 快照
        v1 = (
            await session.execute(
                select(JobVersion).where(
                    JobVersion.job_id == job.id, JobVersion.version == 1
                )
            )
        ).scalar_one()
        assert v1.snapshot["title"] == "J1"
        assert v1.snapshot["hard_requirements"]["min_education"] == "bachelor"
        assert v1.snapshot["hard_requirements"]["required_skills"] == ["Python", "FastAPI"]
        assert v1.changed_by == admin.id

        # 验证 hard_requirements 表
        h = (
            await session.execute(
                select(JobHardRequirement).where(JobHardRequirement.job_id == job.id)
            )
        ).scalar_one()
        assert h.min_education == "bachelor"
        assert h.min_years == 3
        assert h.required_skills == ["Python", "FastAPI"]
        assert h.excluded_companies == ["BadCorp"]


async def test_create_job_with_empty_hard_requirements_still_creates_record() -> None:
    """空 hard_requirements 也建记录（便于后续 update）。"""
    team_id, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        payload = _make_create()
        job = await job_service.create_job(
            session,
            team_id=team_id,
            created_by=admin.id,
            payload=payload,
        )
        await session.commit()

        h = await job_service.get_hard_requirements(session, job.id)
        assert h.min_education is None
        assert h.min_years is None
        assert h.required_skills is None
        assert h.excluded_companies is None


# ============================================================================
# update_job
# ============================================================================


async def test_update_job_increments_version_and_writes_new_snapshot() -> None:
    team_id, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        job = await job_service.create_job(
            session,
            team_id=team_id,
            created_by=admin.id,
            payload=_make_create(),
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        # 重新查 job
        job = (await session.execute(select(Job).where(Job.id == job.id))).scalar_one()
        update_payload = JobUpdateRequest(
            title="J2",
            jd_text="Updated JD",
            status="active",
            hard_requirements=HardRequirements(
                min_education="master", required_skills=["Go"]
            ),
            llm_config={"model": "glm-4.6", "temperature": 0.3},
        )
        updated = await job_service.update_job(
            session, job_id=job.id, actor=admin, payload=update_payload
        )
        await session.commit()

        assert updated.title == "J2"
        assert updated.jd_text == "Updated JD"
        assert updated.status == "active"
        assert updated.current_version == 2
        assert updated.llm_config == {"model": "glm-4.6", "temperature": 0.3}

        # 验证新快照
        v2 = (
            await session.execute(
                select(JobVersion).where(
                    JobVersion.job_id == job.id, JobVersion.version == 2
                )
            )
        ).scalar_one()
        assert v2.snapshot["title"] == "J2"
        assert v2.snapshot["status"] == "active"
        assert v2.snapshot["hard_requirements"]["min_education"] == "master"
        assert v2.snapshot["hard_requirements"]["required_skills"] == ["Go"]

        # 验证 hard_requirements 已更新
        h = await job_service.get_hard_requirements(session, job.id)
        assert h.min_education == "master"
        assert h.required_skills == ["Go"]


async def test_update_job_partial_fields_keep_original() -> None:
    """部分字段未传（None）保留原值。"""
    team_id, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        job = await job_service.create_job(
            session,
            team_id=team_id,
            created_by=admin.id,
            payload=_make_create(
                hard=HardRequirements(min_education="bachelor", min_years=3)
            ),
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        job = (await session.execute(select(Job).where(Job.id == job.id))).scalar_one()
        # 只传 title，hard_requirements=None（保持原值）
        update_payload = JobUpdateRequest(title="NewTitle")
        updated = await job_service.update_job(
            session, job_id=job.id, actor=admin, payload=update_payload
        )
        await session.commit()

        assert updated.title == "NewTitle"
        assert updated.jd_text == "JD body"  # 保留
        assert updated.status == "draft"  # 保留
        assert updated.current_version == 2

        h = await job_service.get_hard_requirements(session, job.id)
        assert h.min_education == "bachelor"
        assert h.min_years == 3


async def test_update_job_hard_requirements_whole_replace() -> None:
    """hard_requirements 整体替换（清空未传字段）。"""
    team_id, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        job = await job_service.create_job(
            session,
            team_id=team_id,
            created_by=admin.id,
            payload=_make_create(
                hard=HardRequirements(
                    min_education="bachelor",
                    min_years=3,
                    required_skills=["Python"],
                    excluded_companies=["X"],
                )
            ),
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        job = (await session.execute(select(Job).where(Job.id == job.id))).scalar_one()
        # 只传 min_years=5；其他应被清空
        update_payload = JobUpdateRequest(
            hard_requirements=HardRequirements(min_years=5)
        )
        updated = await job_service.update_job(
            session, job_id=job.id, actor=admin, payload=update_payload
        )
        await session.commit()

        h = await job_service.get_hard_requirements(session, updated.id)
        assert h.min_education is None  # 被清空
        assert h.min_years == 5
        assert h.required_skills is None  # 被清空
        assert h.excluded_companies is None


async def test_update_job_cross_team_returns_forbidden() -> None:
    """跨 team 改 → 403。"""
    from app.models.team import Team

    team_id, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        job = await job_service.create_job(
            session,
            team_id=team_id,
            created_by=admin.id,
            payload=_make_create(),
        )
        await session.commit()

    # 创建另一个 admin + team
    async with AsyncSessionLocal() as session:
        team_b = Team(name="Team B")
        session.add(team_b)
        await session.flush()
        other = await auth_service.register(
            session,
            email="other@example.com",
            password="Pass1234",
            name="O",
        )
        other.team_id = team_b.id
        other.role = "admin"
        await session.commit()

    async with AsyncSessionLocal() as session:
        # 重新加载 other 对象（actor 必须绑定到本 session）
        actor = (
            await session.execute(select(Job).where(Job.id == job.id))
        ).scalar_one()
        from app.models.user import User

        other_user = (
            await session.execute(select(User).where(User.email == "other@example.com"))
        ).scalar_one()
        with pytest.raises(ForbiddenError):
            await job_service.update_job(
                session,
                job_id=job.id,
                actor=other_user,
                payload=JobUpdateRequest(title="Hacked"),
            )


async def test_update_job_nonexistent_returns_not_found() -> None:
    _, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        with pytest.raises(NotFoundError):
            await job_service.update_job(
                session,
                job_id=uuid4(),
                actor=admin,
                payload=JobUpdateRequest(title="X"),
            )


# ============================================================================
# get_job
# ============================================================================


async def test_get_job_returns_job() -> None:
    team_id, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        created = await job_service.create_job(
            session,
            team_id=team_id,
            created_by=admin.id,
            payload=_make_create(),
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        fetched = await job_service.get_job(session, job_id=created.id, actor=admin)
        assert fetched.id == created.id


async def test_get_job_cross_team_returns_forbidden() -> None:
    from app.models.team import Team

    team_id, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        job = await job_service.create_job(
            session,
            team_id=team_id,
            created_by=admin.id,
            payload=_make_create(),
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        team_b = Team(name="Team B")
        session.add(team_b)
        await session.flush()
        other = await auth_service.register(
            session,
            email="other@example.com",
            password="Pass1234",
            name="O",
        )
        other.team_id = team_b.id
        other.role = "admin"
        await session.commit()

    async with AsyncSessionLocal() as session:
        from app.models.user import User

        other_user = (
            await session.execute(
                select(User).where(User.email == "other@example.com")
            )
        ).scalar_one()
        with pytest.raises(ForbiddenError):
            await job_service.get_job(session, job_id=job.id, actor=other_user)


async def test_get_job_nonexistent_returns_not_found() -> None:
    _, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        with pytest.raises(NotFoundError):
            await job_service.get_job(session, job_id=uuid4(), actor=admin)


# ============================================================================
# list_jobs
# ============================================================================


async def test_list_jobs_team_isolation() -> None:
    """team A 看不到 team B 的 job。"""
    from app.models.team import Team

    team_a, admin_a = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        await job_service.create_job(
            session,
            team_id=team_a,
            created_by=admin_a.id,
            payload=_make_create(title="A1"),
        )
        await session.commit()

    # 构造 team B（直接 INSERT，因首位 admin 已占据"首位"逻辑）
    async with AsyncSessionLocal() as session:
        team_b = Team(name="Team B")
        session.add(team_b)
        await session.flush()
        admin_b = await auth_service.register(
            session,
            email="b@example.com",
            password="Pass1234",
            name="B",
        )
        # 把 admin_b 关联到 team_b（绕开"首位"逻辑）
        admin_b.team_id = team_b.id
        admin_b.role = "admin"
        await session.commit()
        team_b_id = team_b.id
        admin_b_id = admin_b.id
        await job_service.create_job(
            session,
            team_id=team_b_id,
            created_by=admin_b_id,
            payload=_make_create(title="B1"),
        )
        await job_service.create_job(
            session,
            team_id=team_b_id,
            created_by=admin_b_id,
            payload=_make_create(title="B2"),
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        items, total = await job_service.list_jobs(session, team_id=team_b_id)
        assert total == 2
        titles = {j.title for j in items}
        assert titles == {"B1", "B2"}


async def test_list_jobs_status_filter() -> None:
    team_id, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        await job_service.create_job(
            session, team_id=team_id, created_by=admin.id, payload=_make_create(status="draft")
        )
        await job_service.create_job(
            session, team_id=team_id, created_by=admin.id, payload=_make_create(status="active")
        )
        await job_service.create_job(
            session, team_id=team_id, created_by=admin.id, payload=_make_create(status="active")
        )
        await job_service.create_job(
            session, team_id=team_id, created_by=admin.id, payload=_make_create(status="closed")
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        _, total_all = await job_service.list_jobs(session, team_id=team_id)
        assert total_all == 4

        _, total_active = await job_service.list_jobs(
            session, team_id=team_id, status_filter="active"
        )
        assert total_active == 2

        _, total_closed = await job_service.list_jobs(
            session, team_id=team_id, status_filter="closed"
        )
        assert total_closed == 1


async def test_list_jobs_pagination() -> None:
    team_id, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        for i in range(5):
            await job_service.create_job(
                session,
                team_id=team_id,
                created_by=admin.id,
                payload=_make_create(title=f"J{i}"),
            )
        await session.commit()

    async with AsyncSessionLocal() as session:
        items_p1, total = await job_service.list_jobs(
            session, team_id=team_id, page=1, page_size=2
        )
        assert total == 5
        assert len(items_p1) == 2

        items_p2, _ = await job_service.list_jobs(
            session, team_id=team_id, page=2, page_size=2
        )
        assert len(items_p2) == 2

        items_p3, _ = await job_service.list_jobs(
            session, team_id=team_id, page=3, page_size=2
        )
        assert len(items_p3) == 1

        # 不同页不应有重复
        ids_p1 = {j.id for j in items_p1}
        ids_p2 = {j.id for j in items_p2}
        assert ids_p1.isdisjoint(ids_p2)


# ============================================================================
# list_versions
# ============================================================================


async def test_list_versions_returns_descending_order() -> None:
    team_id, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        job = await job_service.create_job(
            session, team_id=team_id, created_by=admin.id, payload=_make_create()
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        for i in range(3):
            job_obj = (
                await session.execute(select(Job).where(Job.id == job.id))
            ).scalar_one()
            await job_service.update_job(
                session,
                job_id=job_obj.id,
                actor=admin,
                payload=JobUpdateRequest(title=f"V{i+2}"),
            )
            await session.commit()

    async with AsyncSessionLocal() as session:
        versions = await job_service.list_versions(session, job_id=job.id)
        assert len(versions) == 4  # v1 + 3 次更新
        assert [v.version for v in versions] == [4, 3, 2, 1]


# ============================================================================
# delete_job
# ============================================================================


async def test_delete_job_cascades_versions_and_hard_requirements() -> None:
    team_id, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        job = await job_service.create_job(
            session,
            team_id=team_id,
            created_by=admin.id,
            payload=_make_create(),
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        job_obj = (
            await session.execute(select(Job).where(Job.id == job.id))
        ).scalar_one()
        await job_service.update_job(
            session,
            job_id=job_obj.id,
            actor=admin,
            payload=JobUpdateRequest(title="V2"),
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        await job_service.delete_job(session, job_id=job.id, actor=admin)
        await session.commit()

    async with AsyncSessionLocal() as session:
        # 确认 job / versions / hard_requirements 全部被级联删除
        assert (
            await session.execute(select(Job).where(Job.id == job.id))
        ).scalar_one_or_none() is None
        assert (
            await session.execute(
                select(JobVersion).where(JobVersion.job_id == job.id)
            )
        ).scalars().all() == []
        assert (
            await session.execute(
                select(JobHardRequirement).where(JobHardRequirement.job_id == job.id)
            )
        ).scalars().all() == []


async def test_delete_job_cross_team_returns_forbidden() -> None:
    from app.models.team import Team

    team_id, admin = await _bootstrap_team_with_admin()
    async with AsyncSessionLocal() as session:
        job = await job_service.create_job(
            session, team_id=team_id, created_by=admin.id, payload=_make_create()
        )
        await session.commit()

    async with AsyncSessionLocal() as session:
        team_b = Team(name="Team B")
        session.add(team_b)
        await session.flush()
        other = await auth_service.register(
            session,
            email="other@example.com",
            password="Pass1234",
            name="O",
        )
        other.team_id = team_b.id
        other.role = "admin"
        await session.commit()

    async with AsyncSessionLocal() as session:
        from app.models.user import User

        other_user = (
            await session.execute(
                select(User).where(User.email == "other@example.com")
            )
        ).scalar_one()
        with pytest.raises(ForbiddenError):
            await job_service.delete_job(session, job_id=job.id, actor=other_user)
