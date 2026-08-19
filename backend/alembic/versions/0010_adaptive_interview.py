"""interview_sessions 增 mode/adaptive_plan + interview_turns 账本表

渐进式自适应面试 M1：
- ``interview_sessions.mode``：'batch'（一次性出卷，默认）| 'adaptive'（逐题推进）
- ``interview_sessions.adaptive_plan``：启动时的匹配信号 + 分支计划快照（JSONB）
- ``interview_turns``：每个问答回合一行的账本 —— 题目/回答/评分/证据/下一题决策，
  全部决策与证据可回溯；M2 音频链路复用 audio_storage_key + transcription_status。

SQLite 兼容：mode 为 VARCHAR + server_default，不引入新 ENUM；
JSONB 经 JSONB_COMPAT 变体自动退化为 sa.JSON。

Revision ID: 0010_adaptive_interview
Revises: 0009_dim_communication
Create Date: 2026-08-18
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

from app.models._compat import GUID, JSONB_COMPAT

# revision identifiers, used by Alembic.
revision: str = "0010_adaptive_interview"
down_revision: Union[str, None] = "0009_dim_communication"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "interview_sessions",
        sa.Column("mode", sa.String(length=16), nullable=False, server_default="batch"),
    )
    op.add_column(
        "interview_sessions",
        sa.Column("adaptive_plan", JSONB_COMPAT, nullable=True),
    )
    op.create_table(
        "interview_turns",
        sa.Column("id", GUID, primary_key=True),
        sa.Column(
            "session_id",
            GUID,
            sa.ForeignKey("interview_sessions.id", ondelete="CASCADE"),
            nullable=False,
            index=True,
        ),
        sa.Column("seq", sa.Integer, nullable=False),
        sa.Column(
            "question_item_id",
            GUID,
            sa.ForeignKey("question_bank_items.id", ondelete="SET NULL"),
            nullable=True,
        ),
        sa.Column("question_text", sa.Text, nullable=False),
        sa.Column("dimension", sa.String(length=16), nullable=True),
        sa.Column(
            "category_id",
            GUID,
            sa.ForeignKey("question_categories.id", ondelete="SET NULL"),
            nullable=True,
            index=True,
        ),
        sa.Column("category_name", sa.String(length=64), nullable=True),
        sa.Column("answer_text", sa.Text, nullable=True),
        sa.Column("audio_storage_key", sa.String(length=512), nullable=True),
        sa.Column("transcription_status", sa.String(length=16), nullable=True),
        sa.Column("answered_at", sa.DateTime(timezone=True), nullable=True),
        sa.Column("rating", sa.Integer, nullable=True),
        sa.Column("rating_evidence", JSONB_COMPAT, nullable=True),
        sa.Column("rating_model", sa.String(length=64), nullable=True),
        sa.Column("next_decision", JSONB_COMPAT, nullable=True),
        sa.Column(
            "created_at", sa.DateTime(timezone=True), server_default=sa.func.now(), nullable=False
        ),
    )


def downgrade() -> None:
    op.drop_table("interview_turns")
    op.drop_column("interview_sessions", "adaptive_plan")
    op.drop_column("interview_sessions", "mode")
