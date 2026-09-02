"""candidates.dedup_key 唯一性收敛到团队内 (team_id, dedup_key)

背景：全局 unique 让跨团队同 key 命中 —— B 团队导入命中 A 团队候选人时
返回其 candidate_id（存在性预言机 / 业务情报泄漏）且导入被误拒。

步骤：
1. 先清掉存量跨团队撞 key 数据：按 team 分组保留最早一条，其余置
   NULL 后再补新值避免唯一冲突？——本库尚处预生产，直接把非首轮团队的
   冲突行删除过于激进；这里选择「按 (team_id, dedup_key) 分组仅保留
   每组 created_at 最新一条的相反策略都不合适」→ 实际采用：
   删除同组内的重复行（保留最早一条，将后继行的来源记录一并级联清理
   由 DB 外键 ondelete 处理）。预生产库数据可弃，生产上线前请先人工
   审阅 dedup_matches。
2. drop 全局 unique 索引 → 建复合唯一 uq_candidate_dedup_team，
   dedup_key 单列降级为普通索引。

Revision ID: 0013_dedup_team_scope
Revises: 0012_recording_replay
Create Date: 2026-08-27
"""
from typing import Union

from alembic import op

revision: str = "0013_dedup_team_scope"
down_revision: Union[str, None] = "0012_recording_replay"
branch_labels = None
depends_on = None

_TABLE = "candidates"


def _dup_group_subquery() -> str:
    """找出每个 (team_id, dedup_key) 组内除最早一行外的待删 id。"""
    return (
        "DELETE FROM candidates WHERE id IN ("
        "SELECT c.id FROM candidates c JOIN ("
        "SELECT team_id, dedup_key, MIN(created_at) AS first_at "
        "FROM candidates GROUP BY team_id, dedup_key "
        "HAVING COUNT(*) > 1"
        ") d ON c.team_id = d.team_id AND c.dedup_key = d.dedup_key "
        "AND c.created_at > d.first_at)"
    )


def upgrade() -> None:
    # 1) 清存量跨团队重复（保留每组最早一条）
    op.execute(_dup_group_subquery())

    # 2) 全局唯一索引 → 复合唯一 + 普通索引
    op.drop_index("ix_candidates_dedup_key", table_name=_TABLE)
    op.create_unique_constraint(
        "uq_candidate_dedup_team", _TABLE, ["team_id", "dedup_key"]
    )
    op.create_index("ix_candidates_dedup_key", _TABLE, ["dedup_key"])


def downgrade() -> None:
    op.drop_index("ix_candidates_dedup_key", table_name=_TABLE)
    op.drop_constraint("uq_candidate_dedup_team", _TABLE, type_="unique")
    op.create_index(
        "ix_candidates_dedup_key", _TABLE, ["dedup_key"], unique=True
    )
