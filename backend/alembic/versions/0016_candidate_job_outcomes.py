"""candidate_job_outcomes 表：最终用人结果落库（效果回流闭环）

背景（评估报告 P1-5）：评分模型此前无用人结果回流，永远无法校准。
新建 per (job_id, candidate_id) 唯一的结果表，HR 人工录入
hired / probation_passed / rejected / withdrawn，支撑评分校准报告
与招聘漏斗统计。

Revision ID: 0016_candidate_job_outcomes
Revises: 0015_screening_needs_review
Create Date: 2026-08-31
"""
from typing import Union

from alembic import op
import sqlalchemy as sa

from app.models._compat import GUID

revision: str = "0016_candidate_job_outcomes"
down_revision: Union[str, None] = "0015_screening_needs_review"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.create_table(
        "candidate_job_outcomes",
        sa.Column("id", GUID(), primary_key=True),
        sa.Column(
            "job_id",
            GUID(),
            sa.ForeignKey("jobs.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column(
            "candidate_id",
            GUID(),
            sa.ForeignKey("candidates.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("final_status", sa.String(32), nullable=False),
        sa.Column(
            "decided_by",
            GUID(),
            sa.ForeignKey("users.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("decided_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("note", sa.Text(), nullable=True),
        sa.Column(
            "created_at",
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            "job_id", "candidate_id", name="uq_outcome_job_candidate"
        ),
    )


def downgrade() -> None:
    op.drop_table("candidate_job_outcomes")
