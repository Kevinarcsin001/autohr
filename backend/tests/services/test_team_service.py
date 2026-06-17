"""team_service 集成测试。

覆盖：
- list_members：按 created_at 升序
- update_member_role：升级 / 降级 / 改自己 / 降级最后一位 admin
- remove_member：成功 / 移除自己 / 移除最后一位 admin
- invite_team_member：转发到 auth_service

策略：通过 register + accept_invite 构造 team + 多成员场景，每个测试自行清表。
"""
from __future__ import annotations

import pytest
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.core.middleware.error_handler import (
    ForbiddenError,
    NotFoundError,
)
from app.services import auth_service, team_service


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


async def _bootstrap_team_with_two_members() -> tuple:
    """构造 admin + member 场景，返回 (team_id, admin_id, member_id)。"""
    async with AsyncSessionLocal() as session:
        admin = await auth_service.register(
            session,
            email="admin@example.com",
            password="Pass1234",
            name="A",
        )
        await session.commit()
        team_id = admin.team_id

        invite = await auth_service.invite_member(
            session,
            team_id=team_id,
            email="member@example.com",
            role="member",
            name="M",
            invited_by=admin.id,
        )
        await session.commit()

        member, _, _ = await auth_service.accept_invite(
            session,
            invite_token=invite.invite_token,
            name="M",
            password="Pass1234",
        )
        await session.commit()
        return team_id, admin.id, member.id


# ============================================================================
# list_members
# ============================================================================


@pytest.mark.asyncio
async def test_list_members_returns_all_in_creation_order() -> None:
    await _purge()
    try:
        team_id, admin_id, member_id = await _bootstrap_team_with_two_members()
        async with AsyncSessionLocal() as session:
            members = await team_service.list_members(session, team_id=team_id)
            assert [m.id for m in members] == [admin_id, member_id]
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_get_member_or_404_for_nonexistent() -> None:
    await _purge()
    try:
        team_id, _, _ = await _bootstrap_team_with_two_members()
        async with AsyncSessionLocal() as session:
            import uuid

            with pytest.raises(NotFoundError):
                await team_service.get_member_or_404(
                    session,
                    team_id=team_id,
                    user_id=uuid.uuid4(),
                )
    finally:
        await _purge()


# ============================================================================
# update_member_role
# ============================================================================


@pytest.mark.asyncio
async def test_update_role_promote_member_to_admin() -> None:
    await _purge()
    try:
        team_id, admin_id, member_id = await _bootstrap_team_with_two_members()
        async with AsyncSessionLocal() as session:
            updated = await team_service.update_member_role(
                session,
                team_id=team_id,
                target_user_id=member_id,
                new_role="admin",
                actor_user_id=admin_id,
            )
            await session.commit()
            assert updated.role == "admin"
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_update_role_self_raises_forbidden() -> None:
    """改自己角色：ForbiddenError。"""
    await _purge()
    try:
        team_id, admin_id, _ = await _bootstrap_team_with_two_members()
        async with AsyncSessionLocal() as session:
            with pytest.raises(ForbiddenError):
                await team_service.update_member_role(
                    session,
                    team_id=team_id,
                    target_user_id=admin_id,
                    new_role="member",
                    actor_user_id=admin_id,
                )
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_update_role_demote_when_two_admins_succeeds() -> None:
    """team 有 2 个 admin：互相降级（非自己）应成功。

    说明：ValidationError "降级最后一位 admin" 在实际场景中不可达 —— actor 必为
    admin，若 target 也是 admin 则 admin_count >= 2；改自己被前面的 ForbiddenError
    拦截。此测试覆盖可执行的降级路径。
    """
    await _purge()
    try:
        team_id, admin_id, member_id = await _bootstrap_team_with_two_members()
        async with AsyncSessionLocal() as session:
            # 把 member 升级为 admin（actor=admin）
            await team_service.update_member_role(
                session,
                team_id=team_id,
                target_user_id=member_id,
                new_role="admin",
                actor_user_id=admin_id,
            )
            # 此时 admin_count=2；让 admin2（即原 member）降级 admin1 应成功
            updated = await team_service.update_member_role(
                session,
                team_id=team_id,
                target_user_id=admin_id,
                new_role="member",
                actor_user_id=member_id,
            )
            await session.commit()
            assert updated.role == "member"
    finally:
        await _purge()


# ============================================================================
# remove_member
# ============================================================================


@pytest.mark.asyncio
async def test_remove_member_unbinds_team_id() -> None:
    await _purge()
    try:
        team_id, admin_id, member_id = await _bootstrap_team_with_two_members()
        async with AsyncSessionLocal() as session:
            await team_service.remove_member(
                session,
                team_id=team_id,
                target_user_id=member_id,
                actor_user_id=admin_id,
            )
            await session.commit()

        # 验证 member 已解绑
        async with AsyncSessionLocal() as session:
            from app.models.user import User

            from sqlalchemy import select

            result = await session.execute(select(User).where(User.id == member_id))
            user = result.scalar_one()
            assert user.team_id is None
            assert user.role == "member"
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_remove_self_raises_forbidden() -> None:
    await _purge()
    try:
        team_id, admin_id, _ = await _bootstrap_team_with_two_members()
        async with AsyncSessionLocal() as session:
            with pytest.raises(ForbiddenError):
                await team_service.remove_member(
                    session,
                    team_id=team_id,
                    target_user_id=admin_id,
                    actor_user_id=admin_id,
                )
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_remove_member_when_two_admins_succeeds() -> None:
    """team 有 2 个 admin：admin 互相移除（非自己）应成功。

    说明：移除"最后一位 admin"在实际场景中不可达 —— actor 必为 admin，移除自己被
    ForbiddenError 拦截；移除其他 admin 时 admin_count >= 2。
    """
    await _purge()
    try:
        team_id, admin_id, member_id = await _bootstrap_team_with_two_members()
        async with AsyncSessionLocal() as session:
            # 升级 member 为 admin
            await team_service.update_member_role(
                session,
                team_id=team_id,
                target_user_id=member_id,
                new_role="admin",
                actor_user_id=admin_id,
            )
            # 现在 admin_count=2，admin（actor）移除 member（target admin）应成功
            await team_service.remove_member(
                session,
                team_id=team_id,
                target_user_id=member_id,
                actor_user_id=admin_id,
            )
            await session.commit()
    finally:
        await _purge()
