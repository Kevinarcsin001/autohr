"""0003_email_config_backoff

Revision ID: a3b7c5d9e1f2
Revises: f1c9ddd82875
Create Date: 2026-06-16 02:40:00.000000

为 email_configs 增加退避状态相关字段：
- consecutive_failures: 连续失败次数（成功后重置）
- paused_until: 暂停轮询直到何时（5 次全失败后置 30 分钟以上 + 告警）
- last_error_summary: 最后一次失败简述（不记邮件正文 / 不记密码）
- alert_level: none / warning / critical（前端展示）
"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa

# revision identifiers, used by Alembic.
revision: str = 'a3b7c5d9e1f2'
down_revision: Union[str, None] = 'f1c9ddd82875'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.add_column(
        'email_configs',
        sa.Column('consecutive_failures', sa.Integer(), nullable=False, server_default='0'),
    )
    op.add_column(
        'email_configs',
        sa.Column('paused_until', sa.DateTime(timezone=True), nullable=True),
    )
    op.add_column(
        'email_configs',
        sa.Column('last_error_summary', sa.String(), nullable=True),
    )
    op.add_column(
        'email_configs',
        sa.Column('alert_level', sa.String(), nullable=False, server_default='none'),
    )


def downgrade() -> None:
    op.drop_column('email_configs', 'alert_level')
    op.drop_column('email_configs', 'last_error_summary')
    op.drop_column('email_configs', 'paused_until')
    op.drop_column('email_configs', 'consecutive_failures')
