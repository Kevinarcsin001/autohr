"""测试库清理工具：双方言清库，表清单从 ORM metadata 自省（不再手抄漏表）。"""
from __future__ import annotations

from sqlalchemy import text
from sqlalchemy.ext.asyncio import AsyncSession

_schema_ready = False
"""进程内只建一次表：create_all 是隐式写事务，每次 purge 都跑会与业务
写入争 SQLite 写锁（database is locked 的主要来源之一）。"""


async def purge_database(session: AsyncSession) -> None:
    """确保 schema 存在后清空全部业务表。

    先幂等建表（``init_dev_schema``，进程内仅首次执行）：ASGITransport
    不触发 lifespan，SQLite 本地跑无人负责 create_all；PG/CI 下该函数
    对非 sqlite URL 直接返回（schema 由 alembic 负责）。

    清库方言分流：
    - PostgreSQL: ``TRUNCATE ... RESTART IDENTITY CASCADE``（单语句原子）
    - SQLite: 逐表 ``DELETE``（SQLite 无 TRUNCATE）；依赖 ``reversed(sorted_tables)``
      的拓扑序保证外键约束下删除顺序合法
    """
    global _schema_ready

    if not _schema_ready:
        from app.core.db import init_dev_schema

        await init_dev_schema()
        _schema_ready = True

    from app.models.base import Base

    # sorted_tables 为依赖拓扑序，reversed 后先删被依赖方
    tables = [t.name for t in reversed(Base.metadata.sorted_tables)]
    if not tables:
        return

    bind = session.get_bind()
    backend = bind.url.get_backend_name() if bind is not None else "postgresql"

    if backend == "postgresql":
        await session.execute(
            text(f'TRUNCATE {", ".join(tables)} RESTART IDENTITY CASCADE')
        )
    else:
        for table in tables:
            await session.execute(text(f'DELETE FROM "{table}"'))
    await session.commit()
