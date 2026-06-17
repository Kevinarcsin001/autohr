"""0002_team_invites

Revision ID: f1c9ddd82875
Revises: 006c3ef9c10d
Create Date: 2026-06-15 11:40:46.751293

"""
from __future__ import annotations

from typing import Sequence, Union

from alembic import op
import sqlalchemy as sa
from sqlalchemy.dialects import postgresql

# revision identifiers, used by Alembic.
revision: str = 'f1c9ddd82875'
down_revision: Union[str, None] = '006c3ef9c10d'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None


def upgrade() -> None:
    op.create_table('team_invites',
    sa.Column('team_id', sa.UUID(), nullable=False),
    sa.Column('email', postgresql.CITEXT(), nullable=False),
    sa.Column('name', sa.String(), nullable=False),
    sa.Column('role', postgresql.ENUM('admin', 'member', name='user_role', create_type=False), nullable=False),
    sa.Column('invite_token', sa.String(), nullable=False),
    sa.Column('status', sa.String(), nullable=False),
    sa.Column('invited_by', sa.UUID(), nullable=False),
    sa.Column('accepted_by', sa.UUID(), nullable=True),
    sa.Column('expires_at', sa.DateTime(timezone=True), nullable=False),
    sa.Column('accepted_at', sa.DateTime(timezone=True), nullable=True),
    sa.Column('created_at', sa.DateTime(timezone=True), server_default=sa.text('now()'), nullable=False),
    sa.Column('id', sa.UUID(), nullable=False),
    sa.ForeignKeyConstraint(['accepted_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['invited_by'], ['users.id'], ondelete='SET NULL'),
    sa.ForeignKeyConstraint(['team_id'], ['teams.id'], ondelete='CASCADE'),
    sa.PrimaryKeyConstraint('id'),
    sa.UniqueConstraint('team_id', 'email', 'status', name='uq_team_invite_active')
    )
    op.create_index(op.f('ix_team_invites_email'), 'team_invites', ['email'], unique=False)
    op.create_index(op.f('ix_team_invites_expires_at'), 'team_invites', ['expires_at'], unique=False)
    op.create_index(op.f('ix_team_invites_invite_token'), 'team_invites', ['invite_token'], unique=True)
    op.create_index(op.f('ix_team_invites_status'), 'team_invites', ['status'], unique=False)
    op.create_index(op.f('ix_team_invites_team_id'), 'team_invites', ['team_id'], unique=False)


def downgrade() -> None:
    op.drop_index(op.f('ix_team_invites_team_id'), table_name='team_invites')
    op.drop_index(op.f('ix_team_invites_status'), table_name='team_invites')
    op.drop_index(op.f('ix_team_invites_invite_token'), table_name='team_invites')
    op.drop_index(op.f('ix_team_invites_expires_at'), table_name='team_invites')
    op.drop_index(op.f('ix_team_invites_email'), table_name='team_invites')
    op.drop_table('team_invites')
