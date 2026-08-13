"""0006_interview_session

Revision ID: d8a3f7e2c1b4
Revises: c4e8f2a1b9d3
Create Date: 2026-06-22 10:00:00.000000

面试会话 + AI 录用建议支持。

- interview_sessions：一次面试的完整记录
- hiring_recommendations：AI 生成的录用建议
- interview_questions / interview_feedbacks 新增 session_id（nullable，兼容存量数据）
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'd8a3f7e2c1b4'
down_revision: Union[str, None] = 'c4e8f2a1b9d3'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    # ENUM 类型（幂等：ORM 启动时已创建，迁移仅兜底）
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE interview_session_status AS ENUM ('scheduled', 'in_progress', 'completed'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    )
    op.execute(
        "DO $$ BEGIN "
        "CREATE TYPE hiring_recommendation AS ENUM ('hire', 'reserve', 'reject'); "
        "EXCEPTION WHEN duplicate_object THEN NULL; "
        "END $$"
    )

    # interview_sessions 表
    op.create_table(
        'interview_sessions',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('candidate_id', sa.UUID(), nullable=False),
        sa.Column('job_id', sa.UUID(), nullable=False),
        sa.Column('status', postgresql.ENUM('scheduled', 'in_progress', 'completed', name='interview_session_status', create_type=False), nullable=False),
        sa.Column('interviewer_id', sa.UUID(), nullable=True),
        sa.Column('overall_notes', sa.Text(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.Column('updated_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['candidate_id'], ['candidates.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['job_id'], ['jobs.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['interviewer_id'], ['users.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_interview_sessions_candidate_id'), 'interview_sessions', ['candidate_id'], unique=False)
    op.create_index(op.f('ix_interview_sessions_job_id'), 'interview_sessions', ['job_id'], unique=False)
    op.create_index(op.f('ix_interview_sessions_status'), 'interview_sessions', ['status'], unique=False)
    op.create_index(op.f('ix_interview_sessions_interviewer_id'), 'interview_sessions', ['interviewer_id'], unique=False)

    # hiring_recommendations 表
    op.create_table(
        'hiring_recommendations',
        sa.Column('id', sa.UUID(), nullable=False),
        sa.Column('session_id', sa.UUID(), nullable=False),
        sa.Column('recommendation', postgresql.ENUM('hire', 'reserve', 'reject', name='hiring_recommendation', create_type=False), nullable=False),
        sa.Column('reasons', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('risks', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('probation_focus', postgresql.JSONB(astext_type=sa.Text()), nullable=True),
        sa.Column('llm_call_id', sa.UUID(), nullable=True),
        sa.Column('generated_by', sa.String(), nullable=True),
        sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
        sa.ForeignKeyConstraint(['session_id'], ['interview_sessions.id'], ondelete='CASCADE'),
        sa.ForeignKeyConstraint(['llm_call_id'], ['llm_calls.id'], ondelete='SET NULL'),
        sa.PrimaryKeyConstraint('id'),
    )
    op.create_index(op.f('ix_hiring_recommendations_session_id'), 'hiring_recommendations', ['session_id'], unique=True)

    # interview_questions 新增 session_id
    op.add_column('interview_questions', sa.Column('session_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_interview_questions_session_id'), 'interview_questions', ['session_id'], unique=False)
    op.create_foreign_key(
        'fk_interview_questions_session_id',
        'interview_questions', 'interview_sessions',
        ['session_id'], ['id'], ondelete='SET NULL',
    )

    # interview_feedbacks 新增 session_id
    op.add_column('interview_feedbacks', sa.Column('session_id', sa.UUID(), nullable=True))
    op.create_index(op.f('ix_interview_feedbacks_session_id'), 'interview_feedbacks', ['session_id'], unique=False)
    op.create_foreign_key(
        'fk_interview_feedbacks_session_id',
        'interview_feedbacks', 'interview_sessions',
        ['session_id'], ['id'], ondelete='SET NULL',
    )


def downgrade() -> None:
    op.drop_constraint('fk_interview_feedbacks_session_id', 'interview_feedbacks', type_='foreignkey')
    op.drop_index(op.f('ix_interview_feedbacks_session_id'), table_name='interview_feedbacks')
    op.drop_column('interview_feedbacks', 'session_id')

    op.drop_constraint('fk_interview_questions_session_id', 'interview_questions', type_='foreignkey')
    op.drop_index(op.f('ix_interview_questions_session_id'), table_name='interview_questions')
    op.drop_column('interview_questions', 'session_id')

    op.drop_index(op.f('ix_hiring_recommendations_session_id'), table_name='hiring_recommendations')
    op.drop_table('hiring_recommendations')

    op.drop_index(op.f('ix_interview_sessions_interviewer_id'), table_name='interview_sessions')
    op.drop_index(op.f('ix_interview_sessions_status'), table_name='interview_sessions')
    op.drop_index(op.f('ix_interview_sessions_job_id'), table_name='interview_sessions')
    op.drop_index(op.f('ix_interview_sessions_candidate_id'), table_name='interview_sessions')
    op.drop_table('interview_sessions')

    op.execute("DROP TYPE IF EXISTS hiring_recommendation")
    op.execute("DROP TYPE IF EXISTS interview_session_status")
