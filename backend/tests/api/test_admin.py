"""Admin API 集成测试（任务 25）。

策略：
1. **权限**：admin only；member → 403；无 token → 401
2. **LLM 配置 CRUD**：
   - list：返回 team 自有 + 全局默认（NULL team_id）
   - upsert：insert（created=True）+ update（created=False）
   - delete：仅自己的；跨 team 拒绝
3. **审计日志**：upsert/delete 写 audit_logs（action=llm_config.create/update/delete）
4. **统计聚合**：
   - 空 → 0 + None 分位数
   - 有数据 → summary/by_scope/by_adapter/time_series 正确
   - 7d/30d 范围切换
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone

import pytest
from httpx import ASGITransport, AsyncClient
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.core.security import create_access_token
from app.main import app
from app.models.audit import AuditLog
from app.models.llm_call import LLMCall
from app.models.llm_config import LLMConfig
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
                "manual_overrides, llm_calls, llm_configs, async_jobs, "
                "audit_logs, email_configs, job_versions, job_hard_requirements "
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


async def _create_member_in_team(team_id: str) -> str:
    async with AsyncSessionLocal() as session:
        user = User(
            email=f"m-{uuid.uuid4().hex[:6]}@x.com",
            password_hash="x",
            name="member",
            role="member",
            team_id=uuid.UUID(team_id),
        )
        session.add(user)
        await session.commit()
        return create_access_token(
            subject=user.id,
            extra_claims={
                "team_id": team_id,
                "role": "member",
                "email": user.email,
            },
        )


async def _create_admin_in_other_team() -> tuple[str, str]:
    """新建另一个 team + admin，返回 (token, team_id)。"""
    async with AsyncSessionLocal() as session:
        team = Team(name=f"other-{uuid.uuid4().hex[:6]}")
        session.add(team)
        await session.flush()
        user = User(
            email=f"o-{uuid.uuid4().hex[:6]}@x.com",
            password_hash="x",
            name="other-admin",
            role="admin",
            team_id=team.id,
        )
        session.add(user)
        await session.commit()
        token = create_access_token(
            subject=user.id,
            extra_claims={
                "team_id": str(team.id),
                "role": "admin",
                "email": user.email,
            },
        )
        return token, str(team.id)


async def _insert_llm_call(
    *,
    team_id: uuid.UUID,
    scope: str = "extractor",
    adapter: str = "zhipu",
    success: bool = True,
    called_at: datetime | None = None,
    tokens_in: int = 100,
    tokens_out: int = 200,
    cost_cny: float = 0.05,
    latency_ms: int = 800,
) -> None:
    """直接写 llm_calls 表用于 stats 测试。"""
    async with AsyncSessionLocal() as session:
        session.add(
            LLMCall(
                adapter=adapter,
                model="test-model",
                scope=scope,
                tokens_in=tokens_in,
                tokens_out=tokens_out,
                latency_ms=latency_ms,
                cost_cny=cost_cny,
                success=success,
                team_id=team_id,
                called_at=called_at or datetime.now(timezone.utc),
            )
        )
        await session.commit()


# ============================================================================
# 权限
# ============================================================================


class TestAdminPermissions:
    async def test_member_returns_403_on_configs(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        member_token = await _create_member_in_team(admin["team_id"])
        resp = await client.get(
            "/api/admin/llm-configs", headers=_auth(member_token)
        )
        assert resp.status_code == 403, resp.text

    async def test_member_returns_403_on_stats(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        member_token = await _create_member_in_team(admin["team_id"])
        resp = await client.get("/api/admin/stats", headers=_auth(member_token))
        assert resp.status_code == 403, resp.text

    async def test_unauthorized_returns_401(self, client: AsyncClient) -> None:
        resp = await client.get("/api/admin/llm-configs")
        assert resp.status_code == 401

    async def test_admin_can_list(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        resp = await client.get(
            "/api/admin/llm-configs", headers=_auth(admin["token"])
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert "items" in body
        assert isinstance(body["items"], list)


# ============================================================================
# LLM 配置 CRUD
# ============================================================================


class TestLLMConfigCRUD:
    async def test_upsert_insert_creates_row(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        resp = await client.post(
            "/api/admin/llm-configs",
            headers=_auth(admin["token"]),
            json={
                "scope": "extractor",
                "primary": "zhipu",
                "fallback": "qwen",
                "team_id": admin["team_id"],
            },
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["created"] is True
        assert body["config"]["scope"] == "extractor"
        assert body["config"]["primary"] == "zhipu"
        assert body["config"]["fallback"] == "qwen"

    async def test_upsert_update_keeps_created_false(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        # 第一次 insert
        r1 = await client.post(
            "/api/admin/llm-configs",
            headers=_auth(admin["token"]),
            json={
                "scope": "scorer",
                "primary": "zhipu",
                "team_id": admin["team_id"],
            },
        )
        assert r1.status_code == 200
        first_id = r1.json()["config"]["id"]

        # 第二次 upsert：改 primary
        r2 = await client.post(
            "/api/admin/llm-configs",
            headers=_auth(admin["token"]),
            json={
                "scope": "scorer",
                "primary": "qwen",
                "fallback": "zhipu",
                "team_id": admin["team_id"],
            },
        )
        assert r2.status_code == 200, r2.text
        body = r2.json()
        assert body["created"] is False
        assert body["config"]["id"] == first_id  # 同一行
        assert body["config"]["primary"] == "qwen"
        assert body["config"]["fallback"] == "zhipu"

    async def test_upsert_global_default_when_team_id_null(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        resp = await client.post(
            "/api/admin/llm-configs",
            headers=_auth(admin["token"]),
            json={
                "scope": "reasoning",
                "primary": "zhipu",
                "team_id": None,
            },
        )
        assert resp.status_code == 200, resp.text
        assert resp.json()["config"]["team_id"] is None

    async def test_upsert_fends_off_cross_team_payload(
        self, client: AsyncClient
    ) -> None:
        """payload.team_id 指向别 team → service 强制覆盖为 actor_team_id。"""
        admin = await _register_admin(client)
        evil_team = str(uuid.uuid4())
        resp = await client.post(
            "/api/admin/llm-configs",
            headers=_auth(admin["token"]),
            json={
                "scope": "interview",
                "primary": "mock",
                "team_id": evil_team,
            },
        )
        assert resp.status_code == 200
        # 落库到 actor 的 team，而非 evil_team
        assert resp.json()["config"]["team_id"] == admin["team_id"]

    async def test_list_includes_global_default(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        # 写全局默认
        await client.post(
            "/api/admin/llm-configs",
            headers=_auth(admin["token"]),
            json={
                "scope": "extractor",
                "primary": "zhipu",
                "team_id": None,
            },
        )
        # 写 team 级
        await client.post(
            "/api/admin/llm-configs",
            headers=_auth(admin["token"]),
            json={
                "scope": "scorer",
                "primary": "qwen",
                "team_id": admin["team_id"],
            },
        )

        resp = await client.get(
            "/api/admin/llm-configs", headers=_auth(admin["token"])
        )
        assert resp.status_code == 200
        items = resp.json()["items"]
        # 两条都应见到
        scopes = {(i["scope"], i["team_id"]) for i in items}
        assert ("extractor", None) in scopes
        assert ("scorer", admin["team_id"]) in scopes

    async def test_list_excludes_other_team(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        other_token, other_team_id = await _create_admin_in_other_team()
        # 别 team 写一条
        await client.post(
            "/api/admin/llm-configs",
            headers=_auth(other_token),
            json={
                "scope": "extractor",
                "primary": "qwen",
                "team_id": other_team_id,
            },
        )
        # 自己 team 写一条
        await client.post(
            "/api/admin/llm-configs",
            headers=_auth(admin["token"]),
            json={
                "scope": "scorer",
                "primary": "zhipu",
                "team_id": admin["team_id"],
            },
        )

        resp = await client.get(
            "/api/admin/llm-configs", headers=_auth(admin["token"])
        )
        items = resp.json()["items"]
        team_ids = {i["team_id"] for i in items}
        assert other_team_id not in team_ids
        # 仅见到 全局 NULL（若有）或自己 team
        for tid in team_ids:
            assert tid is None or tid == admin["team_id"]

    async def test_delete_own_config(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        create_resp = await client.post(
            "/api/admin/llm-configs",
            headers=_auth(admin["token"]),
            json={
                "scope": "extractor",
                "primary": "zhipu",
                "team_id": admin["team_id"],
            },
        )
        config_id = create_resp.json()["config"]["id"]

        del_resp = await client.delete(
            f"/api/admin/llm-configs/{config_id}",
            headers=_auth(admin["token"]),
        )
        assert del_resp.status_code == 204, del_resp.text

        # 再列已不存在
        list_resp = await client.get(
            "/api/admin/llm-configs", headers=_auth(admin["token"])
        )
        ids = [i["id"] for i in list_resp.json()["items"]]
        assert config_id not in ids

    async def test_delete_other_team_returns_404(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        other_token, other_team_id = await _create_admin_in_other_team()
        # 别 team 写（显式传 team_id 才能落到 other_team 而非全局默认）
        create_resp = await client.post(
            "/api/admin/llm-configs",
            headers=_auth(other_token),
            json={
                "scope": "extractor",
                "primary": "qwen",
                "team_id": other_team_id,
            },
        )
        config_id = create_resp.json()["config"]["id"]

        # 自己尝试删 → 404（service 拒绝跨 team）
        del_resp = await client.delete(
            f"/api/admin/llm-configs/{config_id}",
            headers=_auth(admin["token"]),
        )
        assert del_resp.status_code == 404

    async def test_delete_nonexistent_returns_404(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        resp = await client.delete(
            f"/api/admin/llm-configs/{uuid.uuid4()}",
            headers=_auth(admin["token"]),
        )
        assert resp.status_code == 404


# ============================================================================
# 审计日志
# ============================================================================


class TestAdminAuditLog:
    async def test_upsert_writes_audit(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        await client.post(
            "/api/admin/llm-configs",
            headers=_auth(admin["token"]),
            json={
                "scope": "extractor",
                "primary": "zhipu",
                "team_id": admin["team_id"],
            },
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "llm_config.create"
                )
            )
            rows = result.scalars().all()
            assert len(rows) == 1
            assert str(rows[0].actor_id) == admin["user_id"]
            assert rows[0].target_type == "llm_config"
            assert rows[0].after["scope"] == "extractor"

    async def test_update_writes_audit(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        # 第一次 create
        await client.post(
            "/api/admin/llm-configs",
            headers=_auth(admin["token"]),
            json={
                "scope": "scorer",
                "primary": "zhipu",
                "team_id": admin["team_id"],
            },
        )
        # 第二次 update
        await client.post(
            "/api/admin/llm-configs",
            headers=_auth(admin["token"]),
            json={
                "scope": "scorer",
                "primary": "qwen",
                "team_id": admin["team_id"],
            },
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "llm_config.update"
                )
            )
            rows = result.scalars().all()
            assert len(rows) == 1
            assert rows[0].after["primary"] == "qwen"

    async def test_delete_writes_audit(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        create_resp = await client.post(
            "/api/admin/llm-configs",
            headers=_auth(admin["token"]),
            json={
                "scope": "extractor",
                "primary": "zhipu",
                "team_id": admin["team_id"],
            },
        )
        config_id = create_resp.json()["config"]["id"]
        await client.delete(
            f"/api/admin/llm-configs/{config_id}",
            headers=_auth(admin["token"]),
        )
        async with AsyncSessionLocal() as session:
            result = await session.execute(
                select(AuditLog).where(
                    AuditLog.action == "llm_config.delete"
                )
            )
            rows = result.scalars().all()
            assert len(rows) == 1


# ============================================================================
# 统计聚合
# ============================================================================


class TestAdminStats:
    async def test_empty_stats_returns_zero(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        resp = await client.get(
            "/api/admin/stats?range=7d", headers=_auth(admin["token"])
        )
        assert resp.status_code == 200, resp.text
        body = resp.json()
        assert body["summary"]["total_calls"] == 0
        assert body["summary"]["success_rate"] == 0.0
        assert body["summary"]["p50_latency_ms"] is None
        assert body["by_scope"]["items"] == []
        assert body["by_adapter"]["items"] == []
        assert body["time_series"]["points"] == []

    async def test_stats_summary_with_data(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        team_id = uuid.UUID(admin["team_id"])
        await _insert_llm_call(team_id=team_id, success=True, tokens_in=200, tokens_out=400, cost_cny=0.1)
        await _insert_llm_call(team_id=team_id, success=False, tokens_in=0, tokens_out=0, cost_cny=0.0)
        await _insert_llm_call(team_id=team_id, success=True, adapter="qwen", scope="scorer")

        resp = await client.get(
            "/api/admin/stats?range=7d", headers=_auth(admin["token"])
        )
        assert resp.status_code == 200
        body = resp.json()
        s = body["summary"]
        assert s["total_calls"] == 3
        assert s["success_count"] == 2
        assert s["failed_count"] == 1
        assert s["success_rate"] == round(2 / 3, 4)
        assert s["total_tokens_in"] == 300  # 200 + 0 + 100（默认）
        assert s["total_tokens_out"] == 600  # 400 + 0 + 200

    async def test_stats_by_scope_and_adapter(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        team_id = uuid.UUID(admin["team_id"])
        await _insert_llm_call(team_id=team_id, scope="extractor", adapter="zhipu")
        await _insert_llm_call(team_id=team_id, scope="extractor", adapter="zhipu")
        await _insert_llm_call(team_id=team_id, scope="scorer", adapter="qwen")

        resp = await client.get(
            "/api/admin/stats?range=7d", headers=_auth(admin["token"])
        )
        body = resp.json()
        scope_items = {i["key"]: i for i in body["by_scope"]["items"]}
        assert scope_items["extractor"]["total_calls"] == 2
        assert scope_items["scorer"]["total_calls"] == 1

        adapter_items = {i["key"]: i for i in body["by_adapter"]["items"]}
        assert adapter_items["zhipu"]["total_calls"] == 2
        assert adapter_items["qwen"]["total_calls"] == 1

    async def test_stats_time_series_bucketed_by_day(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        team_id = uuid.UUID(admin["team_id"])
        now = datetime.now(timezone.utc)
        # 今天 2 条
        await _insert_llm_call(team_id=team_id, called_at=now, success=True)
        await _insert_llm_call(team_id=team_id, called_at=now, success=False)
        # 2 天前 1 条
        await _insert_llm_call(
            team_id=team_id, called_at=now - timedelta(days=2), success=True
        )

        resp = await client.get(
            "/api/admin/stats?range=7d", headers=_auth(admin["token"])
        )
        body = resp.json()
        points = body["time_series"]["points"]
        assert body["time_series"]["granularity"] == "day"
        # 至少 2 个 bucket（今天 + 2 天前）
        assert len(points) >= 2
        # 今天的 bucket 应有 2 条
        today_bucket = max(points, key=lambda p: p["timestamp"])
        assert today_bucket["total_calls"] == 2
        assert today_bucket["success_count"] == 1
        assert today_bucket["failed_count"] == 1

    async def test_stats_respects_team_isolation(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        other_token, other_team_id = await _create_admin_in_other_team()
        # 别 team 写数据
        await _insert_llm_call(
            team_id=uuid.UUID(other_team_id), success=True
        )
        # 自己 team 不写任何

        resp = await client.get(
            "/api/admin/stats?range=7d", headers=_auth(admin["token"])
        )
        body = resp.json()
        assert body["summary"]["total_calls"] == 0  # 自己 team 0 条

    async def test_stats_30d_includes_older_data(
        self, client: AsyncClient
    ) -> None:
        admin = await _register_admin(client)
        team_id = uuid.UUID(admin["team_id"])
        now = datetime.now(timezone.utc)
        # 10 天前
        await _insert_llm_call(
            team_id=team_id,
            called_at=now - timedelta(days=10),
            success=True,
        )

        # 7d 看不到
        r7 = await client.get(
            "/api/admin/stats?range=7d", headers=_auth(admin["token"])
        )
        assert r7.json()["summary"]["total_calls"] == 0

        # 30d 看得到
        r30 = await client.get(
            "/api/admin/stats?range=30d", headers=_auth(admin["token"])
        )
        assert r30.json()["summary"]["total_calls"] == 1

    async def test_stats_percentile(self, client: AsyncClient) -> None:
        admin = await _register_admin(client)
        team_id = uuid.UUID(admin["team_id"])
        # 写入 10 条不同 latency
        for i in range(1, 11):
            await _insert_llm_call(
                team_id=team_id,
                latency_ms=i * 100,
                success=True,
            )
        resp = await client.get(
            "/api/admin/stats?range=7d", headers=_auth(admin["token"])
        )
        body = resp.json()
        s = body["summary"]
        # latency: 100, 200, ..., 1000
        # p50 ≈ median(100..1000) = 500-600 区间
        assert s["p50_latency_ms"] is not None
        assert 400 <= s["p50_latency_ms"] <= 700
        # p95 ≈ 950+
        assert s["p95_latency_ms"] is not None
        assert s["p95_latency_ms"] >= 900
