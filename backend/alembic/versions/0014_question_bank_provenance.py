"""question_bank_items 增加来源与审核状态列（题库自增长闭环）

- ``source``：seed（种子脚本）/ ai_followup（面试追问题沉淀）
- ``review_status``：approved（可进组卷）/ pending（待审核）/ rejected

存量题默认 (seed, approved) —— 行为完全向后兼容；AI 沉淀的新题一律
pending，审核通过后才可被动态组卷选中。

Revision ID: 0014_question_bank_provenance
Revises: 0013_dedup_team_scope
Create Date: 2026-08-27
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0014_question_bank_provenance"
down_revision: Union[str, None] = "0013_dedup_team_scope"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "question_bank_items",
        sa.Column(
            "source",
            sa.String(16),
            nullable=False,
            server_default="seed",
        ),
    )
    op.add_column(
        "question_bank_items",
        sa.Column(
            "review_status",
            sa.String(16),
            nullable=False,
            server_default="approved",
        ),
    )
    # 审核台高频查询：按状态过滤
    op.create_index(
        "ix_question_bank_items_review_status",
        "question_bank_items",
        ["review_status"],
    )


def downgrade() -> None:
    op.drop_index(
        "ix_question_bank_items_review_status", table_name="question_bank_items"
    )
    op.drop_column("question_bank_items", "review_status")
    op.drop_column("question_bank_items", "source")
