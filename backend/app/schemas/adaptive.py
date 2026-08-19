"""渐进式自适应面试 Pydantic schema（M1）。

四个端点的契约：
- POST /sessions/{id}/adaptive/start   → AdaptiveStartOut（计划 + 首题）
- GET  /sessions/{id}/adaptive/state   → AdaptiveStateOut（画像/分支/时间线）
- POST /sessions/{id}/adaptive/answer  → AdaptiveAnswerOut（评分 + 决策预览）
- GET  /sessions/{id}/adaptive/next    → AdaptiveNextOut（下一题或完成）
"""
from __future__ import annotations

import uuid
from datetime import datetime
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

# ============================================================================
# 回合（Turn）
# ============================================================================


class TurnOut(BaseModel):
    """一个问答回合的对外视图。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    seq: int
    question_item_id: uuid.UUID | None = None
    question_text: str
    dimension: str | None = None
    category_id: uuid.UUID | None = None
    category_name: str | None = None
    answer_text: str | None = None
    answered_at: datetime | None = None
    audio_storage_key: str | None = None
    transcription_status: str | None = None
    rating: int | None = Field(default=None, ge=1, le=5)
    rating_evidence: dict | None = None
    rating_model: str | None = None
    next_decision: dict | None = None


# ============================================================================
# 评分（LLM 结构化输出 schema，兼作 API 内嵌结构）
# ============================================================================


class TurnRating(BaseModel):
    """LLM 对单题回答的结构化评分（对照 reference_answer）。"""

    model_config = ConfigDict(extra="forbid")

    rating: int = Field(..., ge=1, le=5, description="综合评分 1-5")
    key_points_hit: list[str] = Field(default_factory=list, description="答到的要点")
    key_points_missed: list[str] = Field(default_factory=list, description="遗漏的要点")
    strengths: list[str] = Field(default_factory=list, description="亮点（引用回答原文）")
    flaws: list[str] = Field(default_factory=list, description="问题（引用回答原文）")
    follow_up_suggestion: str = Field(default="", description="给选题器的追问建议")


# ============================================================================
# 分支与状态
# ============================================================================


BranchStatus = Literal["pending", "active", "done", "weak", "exhausted"]


class SignalItem(BaseModel):
    signal: str
    weight: float


class BranchState(BaseModel):
    """分支（= 题库分类）的实时状态。"""

    category_id: uuid.UUID
    category_name: str
    score: float = Field(default=0.0, description="启动时亲和度")
    status: BranchStatus = "pending"
    turns_count: int = 0
    avg_rating: float | None = None
    last_difficulty: int | None = None


class AdaptiveStateOut(BaseModel):
    """工作台大屏：分支进度 + 时间线 + 能力画像。"""

    session_id: uuid.UUID
    mode: str
    status: str
    total_turns: int
    answered_turns: int
    plan_signals: list[SignalItem]
    branches: list[BranchState]
    turns: list[TurnOut]
    ability: dict[str, float] = Field(default_factory=dict)
    """分支名 → 能力估计（当前 = 该分支平均分/5，M3 升级为 CAT θ）。"""
    done: bool
    done_reason: str | None = None


# ============================================================================
# 端点请求/响应
# ============================================================================


class AdaptiveStartOut(BaseModel):
    session_id: uuid.UUID
    mode: str
    signals: list[SignalItem]
    branches: list[BranchState]
    first_turn: TurnOut


class AdaptiveAnswerRequest(BaseModel):
    turn_id: uuid.UUID
    answer_text: str = Field(..., min_length=1, max_length=20000)


class AdaptiveAnswerOut(BaseModel):
    turn: TurnOut
    rating_error: str | None = None
    """评分失败原因（回答已保存，可重试 /next 自动补评）。"""


class AdaptiveNextOut(BaseModel):
    turn: TurnOut | None = None
    done: bool = False
    done_reason: str | None = None
    decision: dict | None = None
    """上一题的决策（为什么问下一题），供工作台展示选题理由。"""


__all__ = [
    "AdaptiveAnswerOut",
    "AdaptiveAnswerRequest",
    "AdaptiveNextOut",
    "AdaptiveStartOut",
    "AdaptiveStateOut",
    "BranchState",
    "BranchStatus",
    "SignalItem",
    "TurnOut",
    "TurnRating",
]
