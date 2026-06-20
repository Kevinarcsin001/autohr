"""异步 SQLAlchemy 2.0 engine 与 AsyncSession factory。

任务 2 仅提供基础设施（engine + session + get_db 依赖）。
任务 3 将在此 base 上定义全部 ORM models。
"""
from __future__ import annotations

from collections.abc import AsyncGenerator

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
