"""screening_results 增加 needs_review 三态筛选列

背景（评估报告 P0-3）：旧逻辑「字段缺失即淘汰」把抽取失败的候选人
直接打进淘汰池（HR 默认信任 AI 初筛，错杀基本不会被人工捞回）。
新增 needs_review 布尔列：字段缺失 / 无法判定 → 待复核（与 disqualified
互斥），HR 改判后自动清 False。存量行默认 False（视为已按旧口径处理）。

Revision ID: 0015_screening_needs_review
Revises: 0014_question_bank_provenance
Create Date: 2026-08-31
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

revision: str = "0015_screening_needs_review"
down_revision: Union[str, None] = "0014_question_bank_provenance"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "screening_results",
        sa.Column(
            "needs_review",
            sa.Boolean(),
            nullable=False,
            server_default=sa.false(),
        ),
    )


def downgrade() -> None:
    op.drop_column("screening_results", "needs_review")
