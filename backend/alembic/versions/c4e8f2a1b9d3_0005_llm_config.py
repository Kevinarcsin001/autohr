"""0005_llm_config

Revision ID: c4e8f2a1b9d3
Revises: b8d2e6f4a3c1
Create Date: 2026-06-18 15:10:00.000000

任务 25：scope 级 LLM 路由配置表。

- llm_configs：(team_id NULLABLE, scope, primary, fallback, model_overrides, ...)
- UNIQUE(team_id, scope)：同 team 同 scope 仅一条覆盖
- team_id 为 NULL → 全局默认（admin 写）
- 索引：team_id + scope（路由查询热点）
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects.postgresql import JSONB, UUID

# revision identifiers, used by Alembic.
revision: str = 'c4e8f2a1b9d3'
down_revision: Union[str, None] = 'b8d2e6f4a3c1'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table(
        'llm_configs',
        sa.Column('id', UUID(as_uuid=True), primary_key=True, nullable=False),
        sa.Column(
            'team_id',
            UUID(as_uuid=True),
            sa.ForeignKey('teams.id', ondelete='CASCADE'),
            nullable=True,
        ),
        sa.Column('scope', sa.String(), nullable=False),
        sa.Column('primary', sa.String(), nullable=False),
        sa.Column('fallback', sa.String(), nullable=True),
        sa.Column('model_overrides', JSONB, nullable=True),
        sa.Column('timeout_seconds', sa.Integer(), nullable=True),
        sa.Column('circuit_breaker_failures', sa.Integer(), nullable=True),
        sa.Column(
            'created_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.Column(
            'updated_at',
            sa.DateTime(timezone=True),
            server_default=sa.func.now(),
            nullable=False,
        ),
        sa.UniqueConstraint(
            'team_id', 'scope', name='uq_llm_config_team_scope'
        ),
    )
    op.create_index(
        'ix_llm_configs_team_id',
        'llm_configs',
        ['team_id'],
    )
    op.create_index(
        'ix_llm_configs_scope',
        'llm_configs',
        ['scope'],
    )


def downgrade() -> None:
    op.drop_index('ix_llm_configs_scope', table_name='llm_configs')
    op.drop_index('ix_llm_configs_team_id', table_name='llm_configs')
    op.drop_table('llm_configs')
