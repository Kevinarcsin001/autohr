"""PG/SQLite 双兼容 SQL 类型。

项目原则（见 ``models/__init__.py`` 的 CITEXT 先例）：ORM 层尽量用方言无关类型，
PG 专有特性靠 ``with_variant`` 在 PG 端附加，开发环境 SQLite 自动降级。

- 生产 (PG)：编译出的 DDL 与历史迁移等价 —— 由 ``alembic check`` 门禁保障
- 开发 (SQLite)：JSONB→TEXT(JSON)、UUID→CHAR(32)、ENUM→VARCHAR+CHECK

参考：SQLAlchemy 2.0 起内置 ``sa.Uuid``，自 2.0.0 引入。
"""
from __future__ import annotations

import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# UUID 主键：PG 原生 UUID，SQLite 降级为 CHAR(32)。sa.Uuid 自带 uuid ↔ str 转换。
GUID = sa.Uuid

# JSON：PG 端 JSONB（与历史 schema 一致），SQLite 端 TEXT(JSON)。
JSONB_COMPAT = sa.JSON().with_variant(
    postgresql.JSONB(astext_type=sa.Text()),
    "postgresql",
)

# 字符串数组：PG 端 ARRAY(String)，SQLite 端以 JSON list 存储（Python 侧仍是 list[str]）。
STRING_ARRAY_COMPAT = sa.JSON().with_variant(
    postgresql.ARRAY(sa.String()),
    "postgresql",
)

# 大小写不敏感文本：PG 端 CITEXT，SQLite 端普通 String（大小写敏感，dev 可接受）。
CITEXT_COMPAT = sa.String().with_variant(
    postgresql.CITEXT(),
    "postgresql",
)

# INET：PG 端原生 INET（保留 IP 校验语义），SQLite 端 String(45) 文本。
INET_COMPAT = sa.String(45).with_variant(
    postgresql.INET(),
    "postgresql",
)


def enum_compat(name: str, *values: str):
    """PG 原生 ENUM；SQLite 端 VARCHAR + CHECK 约束。

    PG 侧 ``create_type=False``：类型由 alembic 迁移的 ``CREATE TYPE`` 显式管理，
    避免 SA 在 create_all 时重复创建（开发环境虽不走 alembic，但保持 PG 语义一致）。
    """
    return sa.Enum(
        *values,
        name=name,
        native_enum=False,  # SQLite 端用 CHECK 约束而非原生 enum
        create_constraint=True,
    ).with_variant(
        postgresql.ENUM(*values, name=name, create_type=False),
        "postgresql",
    )


__all__ = [
    "GUID",
    "JSONB_COMPAT",
    "STRING_ARRAY_COMPAT",
    "CITEXT_COMPAT",
    "INET_COMPAT",
    "enum_compat",
]
