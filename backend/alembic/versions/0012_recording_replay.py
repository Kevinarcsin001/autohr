"""interview_turns.audio_start_ms + interview_sessions 录制回捞字段

M2b 会后回捞：
- ``interview_turns.audio_start_ms``：该题在整场录制中的起始毫秒偏移
  （面试官听回放时标注 mm:ss；区间终点 = 下一题起点）
- ``interview_sessions.recording_storage_key / recording_status``：
  整场录制文件（钉钉/腾讯会议云录制或本地录制）与处理状态

Revision ID: 0012_recording_replay
Revises: 0011_async_job_transcribe
Create Date: 2026-08-19
"""
from typing import Union

import sqlalchemy as sa
from alembic import op

revision: str = "0012_recording_replay"
down_revision: Union[str, None] = "0011_async_job_transcribe"
branch_labels = None
depends_on = None


def upgrade() -> None:
    op.add_column(
        "interview_turns",
        sa.Column("audio_start_ms", sa.Integer, nullable=True),
    )
    op.add_column(
        "interview_sessions",
        sa.Column("recording_storage_key", sa.String(length=512), nullable=True),
    )
    op.add_column(
        "interview_sessions",
        sa.Column("recording_status", sa.String(length=16), nullable=True),
    )


def downgrade() -> None:
    op.drop_column("interview_sessions", "recording_status")
    op.drop_column("interview_sessions", "recording_storage_key")
    op.drop_column("interview_turns", "audio_start_ms")
