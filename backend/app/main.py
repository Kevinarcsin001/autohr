"""FastAPI application entry point.

任务 2：完成核心基础设施挂载
- structlog 日志（PII 脱敏）
- RequestIdMiddleware
- 全局异常处理器（AppError / ValidationError / 兜底 500）
- CORS（来自 settings.CORS_ALLOWED_ORIGINS）
- lifespan：启动时 configure_logging，关闭时 dispose engine
"""
from __future__ import annotations

from contextlib import asynccontextmanager

from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from app.api.auth import router as auth_router
from app.api.email_configs import router as email_configs_router
from app.api.jobs import router as jobs_router
from app.api.platform_imports import router as platform_imports_router
from app.api.teams import router as teams_router
from app.api.uploads import router as uploads_router
from app.core.config import settings
from app.core.db import engine
from app.core.logging import configure_logging, get_logger
from app.core.middleware.error_handler import install_error_handlers
from app.core.middleware.request_id import RequestIdMiddleware


@asynccontextmanager
async def lifespan(_app: FastAPI):
    """Application lifespan handler."""
    configure_logging()
    logger = get_logger(__name__)
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
    app.add_middleware(RequestIdMiddleware)
    app.add_middleware(
        CORSMiddleware,
        allow_origins=settings.cors_origins,
        allow_credentials=True,
        allow_methods=["*"],
        allow_headers=["*"],
        expose_headers=["X-Request-ID"],
    )

    # 异常处理
    install_error_handlers(app)

    # 路由
    app.include_router(auth_router, prefix="/api")
    app.include_router(teams_router, prefix="/api")
    app.include_router(jobs_router, prefix="/api")
    app.include_router(uploads_router, prefix="/api")
    app.include_router(platform_imports_router, prefix="/api")
    app.include_router(email_configs_router, prefix="/api")

    @app.get("/health", tags=["system"])
    async def health() -> dict[str, str]:
        """Health check endpoint."""
        return {
            "status": "ok",
            "service": "autohr-backend",
            "version": "0.1.0",
        }

    @app.get("/", tags=["system"])
    async def root() -> dict[str, str]:
        """Root endpoint."""
        return {"message": "AutoHR API", "docs": "/docs"}

    return app


app = create_app()
