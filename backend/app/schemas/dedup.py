"""DedupService 用的 Pydantic schema（任务 15）。

包含：
- ``DedupMatchOut``：dedup_matches 行的对外表示
- ``MergeRequest``：合并 src → dst 请求体
- ``MergeResponse``：合并结果摘要
- ``DedupDecisionRequest``：HR 审核 dedup_match 的决定（merged / rejected）
"""
from __future__ import annotations

import uuid
from typing import Any, Literal

from pydantic import BaseModel, ConfigDict, Field


# ============================================================================
# Out
# ============================================================================


class DedupMatchOut(BaseModel):
    """dedup_matches 行的对外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_a: uuid.UUID
    candidate_b: uuid.UUID
    similarity: dict[str, Any]
    status: Literal["pending", "merged", "rejected"]
    decided_by: uuid.UUID | None = None


class DedupMatchListItem(BaseModel):
    """dedup_matches 列表项（带候选人姓名便于 HR 识别）。"""

    id: uuid.UUID
    candidate_a: uuid.UUID
    candidate_b: uuid.UUID
    name_a: str | None
    name_b: str | None
    similarity: dict[str, Any]
    status: Literal["pending", "merged", "rejected"]


class DedupMatchListResponse(BaseModel):
    items: list[DedupMatchListItem]
    total: int


# ============================================================================
# 请求
# ============================================================================


class MergeRequest(BaseModel):
    """合并候选人请求。

    ``src_id`` 的所有 source / resume / parsed_structure 转移到 ``dst_id``，
    src.merged_into 指向 dst，src 不再可被检索。
    """

    src_id: uuid.UUID
    dst_id: uuid.UUID
    reason: str | None = Field(default=None, max_length=500)


class MergeResponse(BaseModel):
    """合并结果。"""

    merged_id: uuid.UUID  # = dst_id（合并后保留的候选人）
    archived_id: uuid.UUID  # = src_id（merged_into 指向 dst）
    sources_moved: int
    resumes_moved: int
    fields_updated: list[str]  # 哪些主字段被 confidence 比较更新


class DedupDecisionRequest(BaseModel):
    """HR 审核 pending dedup_match。"""

    decision: Literal["merged", "rejected"]
    reason: str | None = Field(default=None, max_length=500)


__all__ = [
    "DedupMatchOut",
    "DedupMatchListItem",
    "DedupMatchListResponse",
    "MergeRequest",
    "MergeResponse",
    "DedupDecisionRequest",
]
