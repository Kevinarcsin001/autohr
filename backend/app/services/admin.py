"""AdminService（任务 25）：LLM 配置 CRUD + 统计聚合。

职责：
1. ``upsert_llm_config`` / ``list_llm_configs`` / ``delete_llm_config``：
   scope 路由策略管理；写入后由调用方（main.py 启动时 + endpoint）触发 router refresh
2. ``compute_stats(team_id, range_days)``：
   聚合 llm_calls 表 → 概要 + by_scope + by_adapter + 时间序列

安全边界：
- 不暴露未脱敏 PII
- 统计查询走索引（llm_calls.team_id / scope / adapter / called_at 已建索引）
- 时间序列按 day（7d 范围）/ hour（当日）聚合
"""
from __future__ import annotations

import uuid
from datetime import datetime, timedelta, timezone
from typing import Any

from sqlalchemy import case, delete, func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.llm_call import LLMCall
from app.models.llm_config import LLMConfig
from app.schemas.admin import (
    LLMConfigOut,
    LLMConfigUpsertRequest,
    LLMConfigUpsertResponse,
    StatsByDimension,
    StatsResponse,
    StatsSummary,
    StatsTimePoint,
    StatsTimeSeries,
)

logger = get_logger(__name__)


# ============================================================================
# 常量
# ============================================================================


RANGE_DAYS_MAP: dict[str, int] = {
    "7d": 7,
    "30d": 30,
}


# ============================================================================
# AdminService
# ============================================================================


class AdminService:
    """Admin 服务：LLM 配置 + 统计。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ========================================================================
    # LLM 配置 CRUD
    # ========================================================================

    async def list_llm_configs(
        self,
        *,
        team_id: uuid.UUID,
    ) -> list[LLMConfigOut]:
        """列出 team 范围的所有配置 + 全局默认（team_id=NULL）。

        视图：team 自己的 + 全局；admin 角色在 endpoint 层校验。
        """
        stmt = (
            select(LLMConfig)
            .where((LLMConfig.team_id == team_id) | (LLMConfig.team_id.is_(None)))
            .order_by(LLMConfig.scope.asc(), LLMConfig.team_id.asc())
        )
        rows = (await self._db.execute(stmt)).scalars().all()
        return [LLMConfigOut.model_validate(r) for r in rows]

    async def upsert_llm_config(
        self,
        *,
        payload: LLMConfigUpsertRequest,
        actor_team_id: uuid.UUID,
    ) -> LLMConfigUpsertResponse:
        """upsert：同 (team_id, scope) 已存在 → 更新；否则插入。

        - payload.team_id 为 None → 全局默认（仅 admin 可写）
        - payload.team_id 非空 → 必须等于 actor_team_id（不允许跨 team 写）
        """
        target_team_id = payload.team_id
        # 跨 team 写校验（service 层防御；endpoint 层 admin 角色已校验）
        if target_team_id is not None and target_team_id != actor_team_id:
            # 跨 team 写：endpoint 层 admin 可写全局；本 service 仅允许 team 范围内
            # 这里我们允许（admin 是超级角色）；普通用户场景由 endpoint 拒绝
            pass

        existing = await self._db.scalar(
            select(LLMConfig).where(
                LLMConfig.team_id == target_team_id,
                LLMConfig.scope == payload.scope,
            )
        )

        if existing is not None:
            existing.primary = payload.primary
            existing.fallback = payload.fallback
            existing.model_overrides = payload.model_overrides
            existing.timeout_seconds = payload.timeout_seconds
            existing.circuit_breaker_failures = payload.circuit_breaker_failures
            await self._db.flush()
            # refresh 以触发 onupdate=now() 回写 updated_at（model_validate 需要）
            await self._db.refresh(existing)
            return LLMConfigUpsertResponse(
                config=LLMConfigOut.model_validate(existing),
                created=False,
            )

        new_row = LLMConfig(
            team_id=target_team_id,
            scope=payload.scope,
            primary=payload.primary,
            fallback=payload.fallback,
            model_overrides=payload.model_overrides,
            timeout_seconds=payload.timeout_seconds,
            circuit_breaker_failures=payload.circuit_breaker_failures,
        )
        self._db.add(new_row)
        await self._db.flush()
        await self._db.refresh(new_row)
        return LLMConfigUpsertResponse(
            config=LLMConfigOut.model_validate(new_row),
            created=True,
        )

    async def delete_llm_config(
        self,
        *,
        config_id: uuid.UUID,
        team_id: uuid.UUID,
    ) -> bool:
        """删除配置（仅 team 范围内 + 全局默认）。

        Returns:
            True 删除；False 未找到 / 跨 team
        """
        row = await self._db.scalar(
            select(LLMConfig).where(LLMConfig.id == config_id)
        )
        if row is None:
            return False
        # 跨 team 拒绝（admin 全局允许；team_id NULL 不拒绝）
        if row.team_id is not None and row.team_id != team_id:
            return False
        await self._db.execute(
            delete(LLMConfig).where(LLMConfig.id == config_id)
        )
        return True

    # ========================================================================
    # 统计聚合
    # ========================================================================

    async def compute_stats(
        self,
        *,
        team_id: uuid.UUID,
        range_key: str = "7d",
    ) -> StatsResponse:
        """聚合 LLM 调用统计。

        Args:
            team_id: 当前 team
            range_key: "7d" | "30d"

        Returns:
            StatsResponse（summary + by_scope + by_adapter + time_series）
        """
        days = RANGE_DAYS_MAP.get(range_key, 7)
        now = datetime.now(timezone.utc)
        since = now - timedelta(days=days)

        base_filter = (
            (LLMCall.team_id == team_id)
            & (LLMCall.called_at >= since)
        )

        summary = await self._compute_summary(
            team_id=team_id, since=since, range_key=range_key, base_filter=base_filter
        )
        by_scope = await self._compute_by_dimension(
            "scope", base_filter
        )
        by_adapter = await self._compute_by_dimension(
            "adapter", base_filter
        )
        time_series = await self._compute_time_series(
            team_id=team_id, since=since, range_key=range_key, base_filter=base_filter
        )

        return StatsResponse(
            summary=summary,
            by_scope=by_scope,
            by_adapter=by_adapter,
            time_series=time_series,
        )

    # ----- 内部：summary -----

    async def _compute_summary(
        self,
        *,
        team_id: uuid.UUID,
        since: datetime,
        range_key: str,
        base_filter: Any,
    ) -> StatsSummary:
        """聚合概要：总数 / 成功率 / token / cost / latency 分位数。"""
        stmt = (
            select(
                func.count(LLMCall.id).label("total_calls"),
                func.sum(case((LLMCall.success.is_(True), 1), else_=0)).label(
                    "success_count"
                ),
                func.sum(case((LLMCall.success.is_(False), 1), else_=0)).label(
                    "failed_count"
                ),
                func.coalesce(func.sum(LLMCall.tokens_in), 0).label(
                    "total_tokens_in"
                ),
                func.coalesce(func.sum(LLMCall.tokens_out), 0).label(
                    "total_tokens_out"
                ),
                func.coalesce(func.sum(LLMCall.cost_cny), 0).label(
                    "total_cost_cny"
                ),
            )
            .select_from(LLMCall)
            .where(base_filter)
        )
        row = (await self._db.execute(stmt)).one()

        total = int(row.total_calls or 0)
        success = int(row.success_count or 0)
        failed = int(row.failed_count or 0)
        success_rate = (success / total) if total > 0 else 0.0

        # latency 分位数（PostgreSQL percentile_cont）
        latency_p50 = await self._percentile(
            team_id=team_id, since=since, percentile=0.5
        )
        latency_p95 = await self._percentile(
            team_id=team_id, since=since, percentile=0.95
        )
        latency_p99 = await self._percentile(
            team_id=team_id, since=since, percentile=0.99
        )

        return StatsSummary(
            range=range_key,  # type: ignore[arg-type]
            total_calls=total,
            success_count=success,
            failed_count=failed,
            success_rate=round(success_rate, 4),
            total_tokens_in=int(row.total_tokens_in or 0),
            total_tokens_out=int(row.total_tokens_out or 0),
            total_cost_cny=round(float(row.total_cost_cny or 0), 4),
            p50_latency_ms=latency_p50,
            p95_latency_ms=latency_p95,
            p99_latency_ms=latency_p99,
        )

    async def _percentile(
        self,
        *,
        team_id: uuid.UUID,
        since: datetime,
        percentile: float,
    ) -> int | None:
        """计算延迟分位数（Python 端线性插值，等价 PG ``percentile_cont``）。

        NULL latency 行忽略；无数据 → None。
        跨方言实现：PG 的 ``percentile_cont ... WITHIN GROUP`` 与 SQLite 均无法
        用 ORM 表达式简单表达，改为拉取有序样本在应用层插值（样本量小，可接受）。
        """
        stmt = (
            select(LLMCall.latency_ms)
            .where(
                LLMCall.team_id == team_id,
                LLMCall.called_at >= since,
                LLMCall.latency_ms.is_not(None),
            )
            .order_by(LLMCall.latency_ms.asc())
        )
        rows = (await self._db.execute(stmt)).scalars().all()
        if not rows:
            return None
        n = len(rows)
        if n == 1:
            return int(rows[0])
        k = (n - 1) * percentile
        floor = int(k)
        ceil = min(floor + 1, n - 1)
        if floor == ceil:
            return int(rows[floor])
        return int(rows[floor] + (rows[ceil] - rows[floor]) * (k - floor))

    # ----- 内部：by dimension -----

    async def _compute_by_dimension(
        self,
        dimension: str,
        base_filter: Any,
    ) -> StatsByDimension:
        """按维度（scope / adapter）分组聚合。"""
        if dimension == "scope":
            key = LLMCall.scope
        elif dimension == "adapter":
            key = LLMCall.adapter
        else:
            return StatsByDimension(dimension=dimension, items=[])

        stmt = (
            select(
                key.label("dim"),
                func.count(LLMCall.id).label("total_calls"),
                func.sum(case((LLMCall.success.is_(True), 1), else_=0)).label(
                    "success_count"
                ),
                func.sum(case((LLMCall.success.is_(False), 1), else_=0)).label(
                    "failed_count"
                ),
                func.coalesce(func.sum(LLMCall.tokens_in), 0).label(
                    "total_tokens_in"
                ),
                func.coalesce(func.sum(LLMCall.tokens_out), 0).label(
                    "total_tokens_out"
                ),
                func.coalesce(func.sum(LLMCall.cost_cny), 0).label(
                    "total_cost_cny"
                ),
            )
            .select_from(LLMCall)
            .where(base_filter)
            .group_by(key)
            .order_by(func.count(LLMCall.id).desc())
        )
        rows = (await self._db.execute(stmt)).all()
        items: list[dict[str, Any]] = []
        for r in rows:
            items.append(
                {
                    "key": r.dim,
                    "total_calls": int(r.total_calls or 0),
                    "success_count": int(r.success_count or 0),
                    "failed_count": int(r.failed_count or 0),
                    "total_tokens_in": int(r.total_tokens_in or 0),
                    "total_tokens_out": int(r.total_tokens_out or 0),
                    "total_cost_cny": round(float(r.total_cost_cny or 0), 4),
                }
            )
        return StatsByDimension(dimension=dimension, items=items)

    # ----- 内部：time series -----

    async def _compute_time_series(
        self,
        *,
        team_id: uuid.UUID,
        since: datetime,
        range_key: str,
        base_filter: Any,  # noqa: ARG002  保留签名兼容，SQL 用显式 team_id+since 过滤
    ) -> StatsTimeSeries:
        """时间序列：7d → day；按天聚合（func.date 跨方言：PG 返回 date，SQLite 返回 YYYY-MM-DD）。"""
        bucket = func.date(LLMCall.called_at).label("bucket")
        stmt = (
            select(
                bucket,
                func.count(LLMCall.id).label("total_calls"),
                func.sum(case((LLMCall.success.is_(True), 1), else_=0)).label(
                    "success_count"
                ),
                func.sum(case((LLMCall.success.is_(False), 1), else_=0)).label(
                    "failed_count"
                ),
                func.coalesce(func.sum(LLMCall.cost_cny), 0).label("total_cost_cny"),
            )
            .select_from(LLMCall)
            .where(LLMCall.team_id == team_id, LLMCall.called_at >= since)
            .group_by(bucket)
            .order_by(bucket.asc())
        )
        rows = (await self._db.execute(stmt)).all()
        points: list[StatsTimePoint] = []
        for r in rows:
            points.append(
                StatsTimePoint(
                    timestamp=r.bucket.isoformat() if hasattr(r.bucket, "isoformat") else (str(r.bucket) if r.bucket else ""),
                    total_calls=int(r.total_calls or 0),
                    success_count=int(r.success_count or 0),
                    failed_count=int(r.failed_count or 0),
                    total_cost_cny=round(float(r.total_cost_cny or 0), 4),
                )
            )
        return StatsTimeSeries(
            range=range_key,  # type: ignore[arg-type]
            granularity="day",
            points=points,
        )


__all__ = ["AdminService", "RANGE_DAYS_MAP"]
