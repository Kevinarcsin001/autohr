"""0008_question_bank

Revision ID: f9a4c6b2d8e1
Revises: e7b2c4a9f1d3
Create Date: 2026-08-12 10:00:00.000000

面试题库：可配置分类 + 带分值预设题目，支持按分类配额凑 100 分组卷。

- question_categories：分类 lookup（team 隔离，可增删，target_points 默认配额）
- question_bank_items：题目（category_id FK，points 分值，dimension 可空复用）
- interview_questions 新增 bank_question_id（SET NULL，标记题目来源）
"""
from __future__ import annotations

from typing import Sequence, Union

import sqlalchemy as sa
from alembic import op

# revision identifiers, used by Alembic.
revision: str = "f9a4c6b2d8e1"
down_revision: Union[str, None] = "e7b2c4a9f1d3"
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # question_categories 表
    op.create_table(
        "question_categories",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("name", sa.String(length=64), nullable=False),
        sa.Column("slug", sa.String(length=64), nullable=False),
        sa.Column("target_points", sa.Integer(), nullable=False),
        sa.Column("sort_order", sa.Integer(), nullable=False),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.PrimaryKeyConstraint("id"),
        sa.UniqueConstraint("team_id", "slug", name="uq_question_category_team_slug"),
    )
    op.create_index(op.f("ix_question_categories_team_id"), "question_categories", ["team_id"], unique=False)

    # question_bank_items 表
    op.create_table(
        "question_bank_items",
        sa.Column("id", sa.Uuid(), nullable=False),
        sa.Column("team_id", sa.Uuid(), nullable=False),
        sa.Column("category_id", sa.Uuid(), nullable=False),
        sa.Column(
            "dimension",
            sa.Enum("skill", "project", "weakness", "culture", name="interview_dimension", native_enum=False),
            nullable=True,
        ),
        sa.Column("question", sa.Text(), nullable=False),
        sa.Column("points", sa.Integer(), nullable=False),
        sa.Column("difficulty", sa.Integer(), nullable=True),
        sa.Column("tags", sa.JSON(), nullable=True),
        sa.Column("reference_answer", sa.Text(), nullable=True),
        sa.Column("created_by", sa.Uuid(), nullable=True),
        sa.Column("is_active", sa.Boolean(), nullable=False),
        sa.Column("created_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.Column("updated_at", sa.DateTime(timezone=True), server_default=sa.text("now()"), nullable=False),
        sa.ForeignKeyConstraint(["team_id"], ["teams.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["category_id"], ["question_categories.id"], ondelete="CASCADE"),
        sa.ForeignKeyConstraint(["created_by"], ["users.id"], ondelete="SET NULL"),
        sa.PrimaryKeyConstraint("id"),
    )
    op.create_index(op.f("ix_question_bank_items_team_id"), "question_bank_items", ["team_id"], unique=False)
    op.create_index(op.f("ix_question_bank_items_category_id"), "question_bank_items", ["category_id"], unique=False)

    # interview_questions 新增 bank_question_id
    op.add_column("interview_questions", sa.Column("bank_question_id", sa.Uuid(), nullable=True))
    op.create_index(
        op.f("ix_interview_questions_bank_question_id"),
        "interview_questions",
        ["bank_question_id"],
        unique=False,
    )
    op.create_foreign_key(
        "fk_interview_questions_bank_question_id",
        "interview_questions",
        "question_bank_items",
        ["bank_question_id"],
        ["id"],
        ondelete="SET NULL",
    )


def downgrade() -> None:
    op.drop_constraint("fk_interview_questions_bank_question_id", "interview_questions", type_="foreignkey")
    op.drop_index(op.f("ix_interview_questions_bank_question_id"), table_name="interview_questions")
    op.drop_column("interview_questions", "bank_question_id")

    op.drop_index(op.f("ix_question_bank_items_category_id"), table_name="question_bank_items")
    op.drop_index(op.f("ix_question_bank_items_team_id"), table_name="question_bank_items")
    op.drop_table("question_bank_items")

    op.drop_index(op.f("ix_question_categories_team_id"), table_name="question_categories")
    op.drop_table("question_categories")
