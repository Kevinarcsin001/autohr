"""auth_service 集成测试。

通过 docker-compose 提供的真实 PostgreSQL 实例执行。
覆盖：
- register（首位 admin + team；后续 member；重复注册 ConflictError）
- authenticate（CITEXT 大小写不敏感；密码错误 UnauthorizedError）
- refresh_access_token（refresh 校验通过；过期/类型错误 UnauthorizedError）
- invite_member（pending 重复 ConflictError；已是成员 ConflictError）
- accept_invite（成功；token 复用 UnauthorizedError；过期 UnauthorizedError）

策略：每个测试函数自行清空相关表，避免相互依赖。
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest

from app.core.db import AsyncSessionLocal
from app.core.middleware.error_handler import ConflictError, NotFoundError, UnauthorizedError
from app.services import auth_service


# ============================================================================
# 工具：清表
# ============================================================================


async def _purge() -> None:
    """清空 team_invites / users / teams 及其级联表。

    注意：``jobs.created_by`` 是 NOT NULL 但外键 SET NULL，删除 user 会
    触发 SET NULL → NotNullViolation；必须先 TRUNCATE jobs 或用 CASCADE。
    """
    async with AsyncSessionLocal() as session:
        from sqlalchemy import text

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


# ============================================================================
# register
# ============================================================================


@pytest.mark.asyncio
async def test_register_first_user_becomes_admin_with_team() -> None:
    """首位注册：role=admin，自动创建默认 team。"""
    await _purge()
    try:
        async with AsyncSessionLocal() as session:
            user = await auth_service.register(
                session,
                email="first@example.com",
                password="Pass1234",
                name="首位",
            )
            await session.commit()
            assert user.role == "admin"
            assert user.team_id is not None
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_register_subsequent_user_is_member_without_team() -> None:
    """非首位注册：role=member，无 team。"""
    await _purge()
    try:
        async with AsyncSessionLocal() as session:
            await auth_service.register(
                session,
                email="first@example.com",
                password="Pass1234",
                name="首位",
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            second = await auth_service.register(
                session,
                email="second@example.com",
                password="Pass1234",
                name="第二",
            )
            await session.commit()
            assert second.role == "member"
            assert second.team_id is None
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_register_duplicate_email_raises_conflict() -> None:
    """重复邮箱：ConflictError。"""
    await _purge()
    try:
        async with AsyncSessionLocal() as session:
            await auth_service.register(
                session,
                email="dup@example.com",
                password="Pass1234",
                name="A",
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            with pytest.raises(ConflictError):
                await auth_service.register(
                    session,
                    email="DUP@example.com",  # CITEXT 大小写不敏感
                    password="Pass1234",
                    name="B",
                )
    finally:
        await _purge()


# ============================================================================
# authenticate
# ============================================================================


@pytest.mark.asyncio
async def test_authenticate_case_insensitive_email() -> None:
    """CITEXT：大小写不同的邮箱都能登录。"""
    await _purge()
    try:
        async with AsyncSessionLocal() as session:
            await auth_service.register(
                session,
                email="Case.Sens@Example.com",
                password="Pass1234",
                name="A",
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            user, access, refresh = await auth_service.authenticate(
                session,
                email="case.sens@example.com",
                password="Pass1234",
            )
            assert user.email.lower() == "case.sens@example.com"
            assert access and refresh
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_authenticate_wrong_password_raises_unauthorized() -> None:
    """密码错误：UnauthorizedError。"""
    await _purge()
    try:
        async with AsyncSessionLocal() as session:
            await auth_service.register(
                session,
                email="user@example.com",
                password="Pass1234",
                name="A",
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            with pytest.raises(UnauthorizedError):
                await auth_service.authenticate(
                    session,
                    email="user@example.com",
                    password="WrongPass1",
                )
    finally:
        await _purge()


# ============================================================================
# refresh_access_token
# ============================================================================


@pytest.mark.asyncio
async def test_refresh_token_returns_new_access(rsa_keys) -> None:  # noqa: F811
    """refresh token 能换新 access。"""
    await _purge()
    try:
        async with AsyncSessionLocal() as session:
            user = await auth_service.register(
                session,
                email="r@example.com",
                password="Pass1234",
                name="A",
            )
            await session.commit()
            _, _, refresh = await auth_service.authenticate(
                session,
                email="r@example.com",
                password="Pass1234",
            )
        async with AsyncSessionLocal() as session:
            new_access = await auth_service.refresh_access_token(
                session,
                refresh_token=refresh,
            )
            assert new_access
            # 解析 access，sub 应该是 user.id
            from app.core.security import decode_token

            payload = decode_token(new_access, expected_type="access")
            assert payload["sub"] == str(user.id)
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_refresh_token_with_access_token_rejected(rsa_keys) -> None:  # noqa: F811
    """把 access token 当 refresh 用应被拒绝。"""
    await _purge()
    try:
        async with AsyncSessionLocal() as session:
            await auth_service.register(
                session,
                email="a@example.com",
                password="Pass1234",
                name="A",
            )
            await session.commit()
            _, access, _ = await auth_service.authenticate(
                session,
                email="a@example.com",
                password="Pass1234",
            )
        async with AsyncSessionLocal() as session:
            with pytest.raises(UnauthorizedError):
                await auth_service.refresh_access_token(
                    session,
                    refresh_token=access,
                )
    finally:
        await _purge()


# ============================================================================
# invite_member
# ============================================================================


@pytest.mark.asyncio
async def test_invite_member_creates_pending_invite() -> None:
    """admin 发起邀请，invite_token 生成。"""
    await _purge()
    try:
        async with AsyncSessionLocal() as session:
            admin = await auth_service.register(
                session,
                email="admin@example.com",
                password="Pass1234",
                name="A",
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            invite = await auth_service.invite_member(
                session,
                team_id=admin.team_id,
                email="new@example.com",
                role="member",
                name="新员工",
                invited_by=admin.id,
            )
            await session.commit()
            assert invite.invite_token
            assert invite.status == "pending"
            assert invite.expires_at > datetime.now(timezone.utc)
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_invite_duplicate_pending_raises_conflict() -> None:
    """同 team + email 已有 pending：ConflictError。"""
    await _purge()
    try:
        async with AsyncSessionLocal() as session:
            admin = await auth_service.register(
                session,
                email="admin@example.com",
                password="Pass1234",
                name="A",
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            await auth_service.invite_member(
                session,
                team_id=admin.team_id,
                email="new@example.com",
                role="member",
                name="新",
                invited_by=admin.id,
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            with pytest.raises(ConflictError):
                await auth_service.invite_member(
                    session,
                    team_id=admin.team_id,
                    email="NEW@example.com",  # CITEXT
                    role="member",
                    name="新",
                    invited_by=admin.id,
                )
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_invite_existing_team_member_raises_conflict() -> None:
    """已被邀请的 email 后续再次邀请（不同状态）：先 accept 再邀请新的 member，
    member 重新被邀请应 ConflictError。"""
    await _purge()
    try:
        async with AsyncSessionLocal() as session:
            admin = await auth_service.register(
                session,
                email="admin@example.com",
                password="Pass1234",
                name="A",
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            invite = await auth_service.invite_member(
                session,
                team_id=admin.team_id,
                email="new@example.com",
                role="member",
                name="新",
                invited_by=admin.id,
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            await auth_service.accept_invite(
                session,
                invite_token=invite.invite_token,
                name="新员工",
                password="Pass1234",
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            with pytest.raises(ConflictError):
                await auth_service.invite_member(
                    session,
                    team_id=admin.team_id,
                    email="new@example.com",
                    role="member",
                    name="新",
                    invited_by=admin.id,
                )
    finally:
        await _purge()


# ============================================================================
# accept_invite
# ============================================================================


@pytest.mark.asyncio
async def test_accept_invite_creates_user_and_joins_team(rsa_keys) -> None:  # noqa: F811
    """接受邀请：用户被创建，role/team_id 来自 invite。"""
    await _purge()
    try:
        async with AsyncSessionLocal() as session:
            admin = await auth_service.register(
                session,
                email="admin@example.com",
                password="Pass1234",
                name="A",
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            invite = await auth_service.invite_member(
                session,
                team_id=admin.team_id,
                email="new@example.com",
                role="member",
                name="新",
                invited_by=admin.id,
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            user, _, _ = await auth_service.accept_invite(
                session,
                invite_token=invite.invite_token,
                name="新员工",
                password="Pass1234",
            )
            await session.commit()
            assert user.email == "new@example.com"
            assert user.role == "member"
            assert user.team_id == admin.team_id
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_accept_invite_token_one_time(rsa_keys) -> None:  # noqa: F811
    """token 一次性：accept 后再次使用应 UnauthorizedError。"""
    await _purge()
    try:
        async with AsyncSessionLocal() as session:
            admin = await auth_service.register(
                session,
                email="admin@example.com",
                password="Pass1234",
                name="A",
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            invite = await auth_service.invite_member(
                session,
                team_id=admin.team_id,
                email="new@example.com",
                role="member",
                name="新",
                invited_by=admin.id,
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            await auth_service.accept_invite(
                session,
                invite_token=invite.invite_token,
                name="新员工",
                password="Pass1234",
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            with pytest.raises(UnauthorizedError):
                await auth_service.accept_invite(
                    session,
                    invite_token=invite.invite_token,
                    name="新员工",
                    password="Pass1234",
                )
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_accept_invite_expired_raises(rsa_keys) -> None:  # noqa: F811
    """过期邀请：UnauthorizedError。"""
    await _purge()
    try:
        async with AsyncSessionLocal() as session:
            admin = await auth_service.register(
                session,
                email="admin@example.com",
                password="Pass1234",
                name="A",
            )
            await session.commit()
        async with AsyncSessionLocal() as session:
            invite = await auth_service.invite_member(
                session,
                team_id=admin.team_id,
                email="new@example.com",
                role="member",
                name="新",
                invited_by=admin.id,
            )
            # 直接把过期时间设到过去
            invite.expires_at = datetime.now(timezone.utc) - timedelta(seconds=1)
            await session.commit()
        async with AsyncSessionLocal() as session:
            with pytest.raises(UnauthorizedError):
                await auth_service.accept_invite(
                    session,
                    invite_token=invite.invite_token,
                    name="新员工",
                    password="Pass1234",
                )
    finally:
        await _purge()


@pytest.mark.asyncio
async def test_accept_invite_nonexistent_token_raises_not_found() -> None:
    """token 不存在：NotFoundError。"""
    async with AsyncSessionLocal() as session:
        with pytest.raises(NotFoundError):
            await auth_service.accept_invite(
                session,
                invite_token="nonexistent-token-" + uuid.uuid4().hex,
                name="X",
                password="Pass1234",
            )
