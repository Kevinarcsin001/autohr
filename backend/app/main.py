"""FastAPI application entry point.

任务 2：完成核心基础设施挂载
- structlog 日志（PII 脱敏）
- RequestIdMiddleware
- 全局异常处理器（AppError / ValidationError / 兜底 500）
- CORS（来自 settings.CORS_ALLOWED_ORIGINS）
- lifespan：启动时 configure_logging，关闭时 dispose engine
"""

from __future__ import annotations

from collections.abc import AsyncIterator
from contextlib import asynccontextmanager

from fastapi import FastAPI, status
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

from app.api.admin import router as admin_router
from app.api.audit_logs import router as audit_logs_router
from app.api.auth import router as auth_router
from app.api.candidates import router as candidates_router
from app.api.dashboard import router as dashboard_router
from app.api.email_configs import router as email_configs_router
from app.api.exports import router as exports_router
from app.api.hiring import router as hiring_router
from app.api.interview import router as interview_router
from app.api.job_candidates import router as job_candidates_router
from app.api.jobs import router as jobs_router
from app.api.platform_imports import router as platform_imports_router
from app.api.question_bank import router as question_bank_router
from app.api.reasons import router as reasons_router
from app.api.resumes import router as resumes_router
from app.api.scores import router as scores_router
from app.api.screening import router as screening_router
from app.api.teams import router as teams_router
from app.api.uploads import router as uploads_router
from app.core.config import settings
from app.core.db import engine, init_dev_schema
from app.core.deps import DbSession
from app.core.logging import configure_logging, get_logger
from app.core.middleware.audit import AuditMiddleware
from app.core.middleware.error_handler import install_error_handlers
from app.core.middleware.request_id import RequestIdMiddleware

logger = get_logger(__name__)


@asynccontextmanager
async def lifespan(_app: FastAPI) -> AsyncIterator[None]:
    """Application lifespan handler."""
    configure_logging()
    logger = get_logger(__name__)
    # 开发环境 SQLite：启动自动建表（生产 PG 走 alembic，此函数空操作）
    await init_dev_schema()
    logger.info(
        "backend_starting",
        environment=settings.ENVIRONMENT,
        log_level=settings.LOG_LEVEL,
    )
    yield
    logger.info("backend_shutting_down")
    await engine.dispose()


def create_app() -> FastAPI:
    """Application factory."""
    app = FastAPI(
        title="AutoHR API",
        description="智能简历筛选助手 API",
        version="0.1.0",
        lifespan=lifespan,
        docs_url="/docs",
        redoc_url="/redoc",
        openapi_url="/openapi.json",
    )

    # 中间件（执行顺序 LIFO：后添加的先执行）
    # RequestId 必须先添加（最外层），异常处理才能取到 request_id
    # AuditMiddleware 在最内层（拿到 response 后才写审计）
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(AuditMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["GET", "POST", "PATCH", "PUT", "DELETE", "OPTIONS"],
        allow_headers=["Authorization", "Content-Type", "X-Request-ID"],
        expose_headers=["X-Request-ID"],
    )

    # 异常处理
    install_error_handlers(app)

    # 路由
    app.include_router(auth_router, prefix="/api")
    app.include_router(teams_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")
    app.include_router(job_candidates_router, prefix="/api")
    app.include_router(candidates_router, prefix="/api")
    app.include_router(screening_router, prefix="/api")
    app.include_router(scores_router, prefix="/api")
    app.include_router(dashboard_router, prefix="/api")
    app.include_router(reasons_router, prefix="/api")
    app.include_router(resumes_router, prefix="/api")
    app.include_router(interview_router, prefix="/api")
    app.include_router(question_bank_router, prefix="/api")
    app.include_router(hiring_router, prefix="/api/interview")
    app.include_router(uploads_router, prefix="/api")
    app.include_router(platform_imports_router, prefix="/api")
    app.include_router(email_configs_router, prefix="/api")
    app.include_router(audit_logs_router, prefix="/api")
    app.include_router(exports_router, prefix="/api")
    app.include_router(admin_router, prefix="/api")

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """存活探针（liveness）：进程在即返回 200，不探测任何依赖。

        容器 healthcheck / LB 用；依赖是否可用看 /health/ready。
        """
        return {
            "status": "ok",
            "service": "autohr-backend",
            "version": "0.1.0",
        }

    @app.get("/health/ready", tags=["system"])
    async def health_ready(db: DbSession) -> JSONResponse:
        """就绪探针（readiness）：逐一探测 DB / Redis，任一不可达即 503。

        供运维与告警消费（容器重启策略不要挂它——启动早期依赖可能未就绪，
        挂上会形成重启循环）。
        """
        checks: dict[str, str] = {}

        # DB：SELECT 1（异步 engine 直接复用请求会话绑定的连接池）
        try:
            from sqlalchemy import text as _sql_text

            await db.execute(_sql_text("SELECT 1"))
            checks["database"] = "ok"
        except Exception as exc:  # noqa: BLE001 - 探针必须吞掉任何异常并如实上报
            logger.warning("ready_check_db_failed", error=str(exc)[:120])
            checks["database"] = f"error: {str(exc)[:80]}"

        # Redis：PING（Celery broker 同一实例；单连接短超时避免探针被拖死）
        redis_url = settings.REDIS_URL
        try:
            import redis.asyncio as aioredis

            client = aioredis.from_url(redis_url, socket_timeout=1.0)  # type: ignore[no-untyped-call]
            try:
                await client.ping()
                checks["redis"] = "ok"
            finally:
                await client.aclose()
        except Exception as exc:  # noqa: BLE001
            logger.warning("ready_check_redis_failed", error=str(exc)[:120])
            checks["redis"] = f"error: {str(exc)[:80]}"

        ok = all(v == "ok" for v in checks.values())
        return JSONResponse(
            status_code=status.HTTP_200_OK if ok else status.HTTP_503_SERVICE_UNAVAILABLE,
            content={"status": "ready" if ok else "degraded", **checks},
        )

    @app.get("/", tags=["system"])
    async def root() -> dict[str, str]:
        """Root endpoint."""
        return {"message": "AutoHR API", "docs": "/docs"}

    return app


app = create_app()
