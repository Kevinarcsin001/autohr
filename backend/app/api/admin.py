"""Admin API 路由（任务 25）：LLM 配置 CRUD + 统计聚合。

端点（base: /api/admin）：
- GET    /llm-configs                  list（含全局默认）
- POST   /llm-configs                  upsert（同 team×scope 已存在则更新）
- DELETE /llm-configs/{config_id}      删除（仅 team 范围 + 全局）
- GET    /stats?range=7d|30d           LLM 调用统计聚合

权限：
- 必须 admin（``AdminUser`` 依赖）
- 所有变更操作写 ``audit_logs``（target_type='llm_config'）
- 跨 team 不可见（service 层强制 team_id 过滤）
"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter, Query, status

from app.core.deps import AdminUser, DbSession
from app.core.middleware.error_handler import NotFoundError
from app.schemas.admin import (
    LLMConfigListResponse,
    LLMConfigUpsertRequest,
    LLMConfigUpsertResponse,
    StatsRange,
    StatsResponse,
)
from app.services.admin import AdminService
from app.services.audit_log import AuditLogService

router = APIRouter(prefix="/admin", tags=["admin"])


# ============================================================================
# LLM 配置 CRUD
# ============================================================================


@router.get("/llm-configs", response_model=LLMConfigListResponse)
async def list_llm_configs(
    admin: AdminUser,
    db: DbSession,
) -> LLMConfigListResponse:
    """列出当前 team 的 LLM 配置 + 全局默认（admin only）。"""
    if admin.team_id is None:
        return LLMConfigListResponse(items=[])
    service = AdminService(db)
    items = await service.list_llm_configs(team_id=UUID(str(admin.team_id)))
    return LLMConfigListResponse(items=items)


@router.post(
    "/llm-configs",
    response_model=LLMConfigUpsertResponse,
    status_code=status.HTTP_200_OK,
)
async def upsert_llm_config(
    payload: LLMConfigUpsertRequest,
    admin: AdminUser,
    db: DbSession,
) -> LLMConfigUpsertResponse:
    """upsert LLM 配置 + 写 audit_log。

    - payload.team_id 为 None → 全局默认（admin 写）
    - 否则必须等于 admin.team_id（防跨 team 写）
    """
    if admin.team_id is None:
        raise NotFoundError(
            "当前用户未加入团队，无法配置 LLM 路由",
            resource="llm_config",
        )

    actor_team_id = UUID(str(admin.team_id))
    # 防御：如果 payload 显式指定其他 team_id，强制覆盖为本 team
    # 全局默认（team_id=None）允许通过
    if payload.team_id is not None and payload.team_id != actor_team_id:
        payload = payload.model_copy(update={"team_id": actor_team_id})

    service = AdminService(db)
    result = await service.upsert_llm_config(
        payload=payload,
        actor_team_id=actor_team_id,
    )

    # 写审计日志（before/after 简化为 payload + created 标志）
    await AuditLogService(db).log(
        actor_id=admin.id,
        action=(
            "llm_config.create" if result.created else "llm_config.update"
        ),
        target_type="llm_config",
        target_id=result.config.id,
        after={
            "scope": payload.scope,
            "team_id": str(payload.team_id) if payload.team_id else None,
            "primary": payload.primary,
            "fallback": payload.fallback,
            "model_overrides": payload.model_overrides,
            "timeout_seconds": payload.timeout_seconds,
            "circuit_breaker_failures": payload.circuit_breaker_failures,
        },
    )

    await db.commit()
    return result


@router.delete(
    "/llm-configs/{config_id}",
    status_code=status.HTTP_204_NO_CONTENT,
)
async def delete_llm_config(
    config_id: UUID,
    admin: AdminUser,
    db: DbSession,
) -> None:
    """删除 LLM 配置（仅 team 范围 + 全局；admin only）+ 写 audit_log。"""
    if admin.team_id is None:
        raise NotFoundError(
            "当前用户未加入团队",
            resource="llm_config",
        )

    actor_team_id = UUID(str(admin.team_id))
    service = AdminService(db)

    # 先查快照（用于 audit before）
    deleted = await service.delete_llm_config(
        config_id=config_id,
        team_id=actor_team_id,
    )
    if not deleted:
        raise NotFoundError(
            "LLM 配置不存在或已删除",
            resource="llm_config",
            resource_id=str(config_id),
        )

    await AuditLogService(db).log(
        actor_id=admin.id,
        action="llm_config.delete",
        target_type="llm_config",
        target_id=config_id,
        before={"config_id": str(config_id)},
    )

    await db.commit()


# ============================================================================
# 统计聚合
# ============================================================================


@router.get("/stats", response_model=StatsResponse)
async def get_stats(
    admin: AdminUser,
    db: DbSession,
    range: StatsRange = Query(default="7d"),
) -> StatsResponse:
    """查询 LLM 调用统计（admin only；按 team 隔离）。

    Query:
        range: "7d" | "30d"
    """
    if admin.team_id is None:
        # 无 team → 返回空统计（避免 403 阻塞 UI）
        from app.schemas.admin import (
            StatsByDimension,
            StatsSummary,
            StatsTimeSeries,
        )

        empty_summary = StatsSummary(
            range=range,  # type: ignore[arg-type]
            total_calls=0,
            success_count=0,
            failed_count=0,
            success_rate=0.0,
            total_tokens_in=0,
            total_tokens_out=0,
            total_cost_cny=0.0,
            p50_latency_ms=None,
            p95_latency_ms=None,
            p99_latency_ms=None,
        )
        return StatsResponse(
            summary=empty_summary,
            by_scope=StatsByDimension(dimension="scope", items=[]),
            by_adapter=StatsByDimension(dimension="adapter", items=[]),
            time_series=StatsTimeSeries(
                range=range,  # type: ignore[arg-type]
                granularity="day",
                points=[],
            ),
        )

    service = AdminService(db)
    return await service.compute_stats(
        team_id=UUID(str(admin.team_id)),
        range_key=range,
    )


__all__ = ["router", "LLMConfigListResponse"]
