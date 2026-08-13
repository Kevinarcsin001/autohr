"""异步 SQLAlchemy 2.0 engine 与 AsyncSession factory。

任务 2 仅提供基础设施（engine + session + get_db 依赖）。
任务 3 将在此 base 上定义全部 ORM models。
"""
from __future__ import annotations

from collections.abc import AsyncGenerator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.ext.asyncio import (
    AsyncSession,
    async_sessionmaker,
    create_async_engine,
)
from sqlalchemy.orm import DeclarativeBase
from sqlalchemy.pool import NullPool

from app.core.config import settings


class Base(DeclarativeBase):
    """SQLAlchemy 声明式基类，所有 ORM 模型继承自此类。"""


engine = create_async_engine(
    settings.DATABASE_URL,
    echo=settings.ENVIRONMENT == "development",
    pool_pre_ping=True,
    pool_recycle=3600,
    # Celery worker 使用 asyncio.run() 每次创建新 event loop；
    # NullPool 避免跨 event loop 的连接池问题
    poolclass=NullPool,
)

# SQLite 开发环境：backend/worker/beat 多进程共享同一 db 文件，
# 启用 WAL（并发读不阻塞写）+ busy_timeout（写锁竞争等待）；FK 默认关闭需显式开启。
if settings.DATABASE_URL.startswith("sqlite"):

    @event.listens_for(engine.sync_engine, "connect")
    def _set_sqlite_pragma(dbapi_conn, _record):  # noqa: ANN001
        cur = dbapi_conn.cursor()
        cur.execute("PRAGMA journal_mode=WAL")
        cur.execute("PRAGMA busy_timeout=5000")
        cur.execute("PRAGMA foreign_keys=ON")
        cur.execute("PRAGMA synchronous=NORMAL")
        cur.close()


async def init_dev_schema() -> None:
    """开发环境 SQLite 自动建表（幂等）；生产 PG 走 alembic 迁移。

    在 main.py lifespan 启动时调用；此时所有 models 已 import 注册到 metadata。
    非 SQLite 环境直接返回（空操作）。
    """
    if not settings.DATABASE_URL.startswith("sqlite"):
        return
    # SQLite 文件库：确保父目录存在（SQLite 不自动创建目录）
    _db_path = settings.DATABASE_URL.split(":///", 1)[-1]
    if _db_path and _db_path not in ("", ":memory:"):
        _parent = Path(_db_path).parent
        _parent.mkdir(parents=True, exist_ok=True)
    async with engine.begin() as conn:
        await conn.run_sync(Base.metadata.create_all)

AsyncSessionLocal = async_sessionmaker(
    bind=engine,
    class_=AsyncSession,
    expire_on_commit=False,
    autoflush=False,
    autocommit=False,
)


async def get_db() -> AsyncGenerator[AsyncSession, None]:
    """FastAPI 依赖：提供事务性 AsyncSession。

    用法：
        @app.get("/items")
        async def list_items(db: Annotated[AsyncSession, Depends(get_db)]):
            ...

    正常 yield 后自动 commit；异常自动 rollback。
    """
    async with AsyncSessionLocal() as session:
        try:
            yield session
            await session.commit()
        except Exception:
            await session.rollback()
            raise
