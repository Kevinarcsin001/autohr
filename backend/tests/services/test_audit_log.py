"""AuditLogService 单元测试（任务 21）。

策略：
1. ``_redact`` 脱敏逻辑（敏感 key + 字符串）
2. ``log()`` 写入；失败不抛异常
3. ``list_logs()`` team 隔离 + 多维过滤 + 分页
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.models.audit import AuditLog
from app.models.team import Team
from app.models.user import User
from app.services.audit_log import (
    AuditLogService,
    _is_sensitive_key,
    _redact,
)

# ============================================================================
# DB 清理
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


@pytest.fixture(autouse=True)
async def clean_db() -> None:
    await _purge_db()
    yield
    await _purge_db()


# ============================================================================
# _redact 单元测试
# ============================================================================


class TestRedact:
    def test_redact_sensitive_key_in_dict(self) -> None:
        out = _redact({"password": "secret", "name": "Alice"})
        assert out == {"password": "[REDACTED]", "name": "Alice"}

    def test_redact_password_hash(self) -> None:
        out = _redact({"password_hash": "$2b$12$...", "email": "x@y.com"})
        assert out["password_hash"] == "[REDACTED]"
        assert out["email"] == "x@y.com"

    def test_redact_nested_dict(self) -> None:
        out = _redact({"user": {"token": "abc", "name": "Bob"}, "id": 1})
        assert out["user"]["token"] == "[REDACTED]"
        assert out["user"]["name"] == "Bob"

    def test_redact_list_recursive(self) -> None:
        out = _redact([{"api_key": "k1"}, {"name": "ok"}])
        assert out[0]["api_key"] == "[REDACTED]"
        assert out[1]["name"] == "ok"

    def test_redact_case_insensitive(self) -> None:
        out = _redact({"PASSWORD": "x", "Api_Key": "y"})
        assert out["PASSWORD"] == "[REDACTED]"
        assert out["Api_Key"] == "[REDACTED]"

    def test_redact_sensitive_substring_in_value(self) -> None:
        out = _redact({"raw": "password=hunter2"})
        assert out["raw"] == "[REDACTED]"

    def test_redact_keeps_normal_values(self) -> None:
        out = _redact({"name": "Alice", "id": 42, "active": True})
        assert out == {"name": "Alice", "id": 42, "active": True}

    def test_is_sensitive_key(self) -> None:
        assert _is_sensitive_key("password") is True
        assert _is_sensitive_key("Token") is True
        assert _is_sensitive_key("api_key") is True
        assert _is_sensitive_key("name") is False
        assert _is_sensitive_key("email") is False


# ============================================================================
# AuditLogService.log
# ============================================================================


class TestAuditLogServiceWrite:
    async def test_log_writes_row(self) -> None:
        team, users = await _seed_team_with_users()
        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            actor = users[0].id
            entry = await service.log(
                actor_id=actor,
                action="job.update",
                target_type="job",
                target_id=uuid.uuid4(),
                before={"title": "v1"},
                after={"title": "v2"},
                ip="10.0.0.1",
                user_agent="Mozilla/5.0",
            )
            await session.commit()
            assert entry is not None
            assert entry.id is not None

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(AuditLog))
            rows = result.scalars().all()
            assert len(rows) == 1
            assert rows[0].action == "job.update"
            assert str(rows[0].ip) == "10.0.0.1"
            assert rows[0].user_agent == "Mozilla/5.0"
            assert rows[0].before == {"title": "v1"}
            assert rows[0].after == {"title": "v2"}

    async def test_log_redacts_sensitive_fields(self) -> None:
        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            await service.log(
                actor_id=None,
                action="user.update",
                before={"password": "plain", "name": "Alice"},
                after={"password": "[REDACTED]", "name": "Alice"},
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            result = await session.execute(select(AuditLog))
            row = result.scalars().first()
            assert row.before["password"] == "[REDACTED]"
            assert row.before["name"] == "Alice"
            assert row.after["password"] == "[REDACTED]"

    async def test_log_actor_id_none_for_system(self) -> None:
        """系统级操作（无 token）actor_id=NULL。"""
        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            entry = await service.log(
                actor_id=None,
                action="system.cleanup",
            )
            await session.commit()
            assert entry is not None
            assert entry.actor_id is None

    async def test_log_with_real_user_actor(self) -> None:
        """actor_id 必须是真实 user（FK 约束）。"""
        team, users = await _seed_team_with_users()
        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            entry = await service.log(
                actor_id=users[0].id,
                action="job.create",
                target_type="job",
                target_id=uuid.uuid4(),
                after={"title": "Eng"},
            )
            await session.commit()
            assert entry is not None
            assert entry.actor_id == users[0].id

    async def test_log_failure_returns_none_not_raise(self) -> None:
        """写失败仅返回 None，不抛异常。"""
        # 制造失败：传一个非法的 target_id 类型（dict 而非 UUID）
        # 注：service 应捕获任何异常
        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            # 关闭 session 触发 flush 失败
            await session.close()
            entry = await service.log(
                actor_id=uuid.uuid4(),
                action="test.action",
            )
            assert entry is None


# ============================================================================
# AuditLogService.list_logs（team 隔离 + 过滤 + 分页）
# ============================================================================


async def _seed_team_with_users(
    *, team_name: str = "T1", n_users: int = 2
) -> tuple[Any, list[Any]]:
    """创建 team + n 个 user。"""
    async with AsyncSessionLocal() as session:
        team = Team(name=team_name)
        session.add(team)
        await session.flush()
        users = []
        for i in range(n_users):
            u = User(
                email=f"u-{uuid.uuid4().hex[:6]}-{i}@x.com",
                password_hash="x",
                name=f"u-{i}",
                team_id=team.id,
            )
            session.add(u)
            users.append(u)
        await session.commit()
        return team, users


class TestAuditLogList:
    async def test_list_filters_by_team(self) -> None:
        """team1 看不到 team2 的 audit_logs。"""
        team1, users1 = await _seed_team_with_users(team_name="T1")
        team2, users2 = await _seed_team_with_users(team_name="T2")

        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            # team1 的 user 写 3 条
            for _ in range(3):
                await service.log(
                    actor_id=users1[0].id, action="job.update"
                )
            # team2 的 user 写 2 条
            for _ in range(2):
                await service.log(
                    actor_id=users2[0].id, action="job.update"
                )
            await session.commit()

        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            items, total = await service.list_logs(team_id=team1.id)
            assert total == 3
            assert len(items) == 3
            for it in items:
                assert it.actor_id == users1[0].id

    async def test_list_filter_by_action(self) -> None:
        team, users = await _seed_team_with_users()
        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            await service.log(actor_id=users[0].id, action="job.create")
            await service.log(actor_id=users[0].id, action="job.update")
            await service.log(actor_id=users[0].id, action="job.update")
            await session.commit()

        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            items, total = await service.list_logs(
                team_id=team.id, action="job.update"
            )
            assert total == 2
            for it in items:
                assert it.action == "job.update"

    async def test_list_filter_by_target_type_and_id(self) -> None:
        team, users = await _seed_team_with_users()
        tid = uuid.uuid4()
        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            await service.log(
                actor_id=users[0].id,
                action="job.update",
                target_type="job",
                target_id=tid,
            )
            await service.log(
                actor_id=users[0].id,
                action="screening.override",
                target_type="screening_result",
                target_id=uuid.uuid4(),
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            items, total = await service.list_logs(
                team_id=team.id, target_type="job", target_id=tid
            )
            assert total == 1
            assert items[0].action == "job.update"

    async def test_list_filter_by_actor_id(self) -> None:
        team, users = await _seed_team_with_users(n_users=2)
        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            await service.log(actor_id=users[0].id, action="a")
            await service.log(actor_id=users[1].id, action="b")
            await session.commit()

        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            items, total = await service.list_logs(
                team_id=team.id, actor_id=users[0].id
            )
            assert total == 1
            assert items[0].actor_id == users[0].id

    async def test_list_pagination(self) -> None:
        team, users = await _seed_team_with_users()
        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            for i in range(15):
                await service.log(actor_id=users[0].id, action=f"a{i}")
            await session.commit()

        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            items, total = await service.list_logs(
                team_id=team.id, page=2, page_size=10
            )
            assert total == 15
            assert len(items) == 5  # 第 2 页 = 15 - 10

    async def test_list_ordered_by_created_at_desc(self) -> None:
        team, users = await _seed_team_with_users()
        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            for i in range(3):
                await service.log(actor_id=users[0].id, action=f"a{i}")
                # 强制创建时间不同（依赖数据库 server_default）
                import asyncio
                await asyncio.sleep(0.01)
                await session.commit()

        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            items, _ = await service.list_logs(team_id=team.id)
            assert items[0].created_at >= items[1].created_at
            assert items[1].created_at >= items[2].created_at

    async def test_list_excludes_null_actor(self) -> None:
        """actor_id=NULL 的系统级日志不返回给任何 team。"""
        team, users = await _seed_team_with_users()
        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            await service.log(actor_id=users[0].id, action="user.action")
            await service.log(actor_id=None, action="system.cleanup")
            await session.commit()

        async with AsyncSessionLocal() as session:
            service = AuditLogService(session)
            items, total = await service.list_logs(team_id=team.id)
            assert total == 1
            assert all(it.actor_id is not None for it in items)
