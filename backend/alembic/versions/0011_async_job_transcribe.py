"""async_job_type 增补 transcribe 值 + transcription_task

M2a 音频链路：面试音频转写走现有 AsyncJob 状态机（@async_task），
需要为新任务类型 ``transcribe`` 扩 PG ENUM。

- PG：``ALTER TYPE async_job_type ADD VALUE IF NOT EXISTS 'transcribe'``
- ORM：models/types.py AsyncJobType 同步增补
- SQLite：CHECK 约束由 ORM 重建时自带，迁移 no-op

Revision ID: 0011_async_job_transcribe
Revises: 0010_adaptive_interview
Create Date: 2026-08-18
"""
from typing import Union

from alembic import op

revision: str = "0011_async_job_transcribe"
down_revision: Union[str, None] = "0010_adaptive_interview"
branch_labels = None
depends_on = None


def upgrade() -> None:
    bind = op.get_bind()
    if bind.dialect.name != "postgresql":
        return
    with op.get_context().autocommit_block():
        op.execute("ALTER TYPE async_job_type ADD VALUE IF NOT EXISTS 'transcribe'")


def downgrade() -> None:
    # PG 不支持 DROP ENUM VALUE；多余值无害
    pass
