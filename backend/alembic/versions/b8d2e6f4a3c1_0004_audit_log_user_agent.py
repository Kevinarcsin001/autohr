"""0004_audit_log_user_agent

Revision ID: b8d2e6f4a3c1
Revises: a3b7c5d9e1f2
Create Date: 2026-06-18 03:30:00.000000

为 audit_logs 增加 user_agent 字段：
- 满足任务 21 约束：记录 IP 与 user-agent
- middleware 写入；老行 user_agent=NULL
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'b8d2e6f4a3c1'
down_revision: Union[str, None] = 'a3b7c5d9e1f2'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'audit_logs',
        sa.Column('user_agent', sa.String(), nullable=True),
    )


def downgrade() -> None:
    op.drop_column('audit_logs', 'user_agent')
