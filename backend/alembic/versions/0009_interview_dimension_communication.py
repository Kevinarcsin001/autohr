"""interview_dimension 增加 communication 值

背景：题库 behavioral（行为面）分类的题目使用 ``dimension="communication"``，
而 ``interview_questions.dimension`` 是 PG 原生 ENUM（0006 创建，仅 4 个值），
导致从题库组卷实例化时写入失败：
    ERROR: invalid input value for enum interview_dimension: "communication"

本迁移同时修两处：
1. PG ENUM ``interview_dimension`` 增补 ``communication`` 值（interview_questions.dimension）
2. ``question_bank_items.dimension`` 列从 VARCHAR(8)（0008 由 sa.Enum(native_enum=False) 
   按旧最长值 culture=8 生成）加宽到 VARCHAR(16)，否则 communication（13 字符）种子写入即炸

- PG：``ALTER TYPE ... ADD VALUE IF NOT EXISTS``（幂等；autocommit block，
  因为 ADD VALUE 在旧版 PG 中不能处于事务内）+ ``ALTER COLUMN ... TYPE VARCHAR(16)``
- SQLite：dev 库经 create_all 建表，长度不生效且新定义已含 communication，迁移为 no-op

downgrade：PG 不支持直接删除 ENUM 值（需重建类型 + 全表改写），降级为 no-op。

Revision ID: 0009_dim_communication
Revises: f9a4c6b2d8e1
Create Date: 2025-08-13
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "0009_dim_communication"
down_revision: Union[str, None] = "f9a4c6b2d8e1"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        # SQLite 开发库：类型由 ORM create_all 管理，无原生 ENUM，跳过
        return
    with op.get_context().autocommit_block():
        op.execute(
            "ALTER TYPE interview_dimension ADD VALUE IF NOT EXISTS 'communication'"
        )
    # 题库表 dimension 列：VARCHAR(8) → VARCHAR(16)，容纳 communication
    op.alter_column(
        "question_bank_items",
        "dimension",
        type_=sa.String(length=16),
        existing_type=sa.String(length=8),
        existing_nullable=True,
    )


def downgrade() -> None:
    # PG 不支持 DROP ENUM VALUE；降级保留多余值无害（不再写入即不可见）
    pass
