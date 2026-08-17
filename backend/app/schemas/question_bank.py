"""题库 Pydantic schema：分类 CRUD + 题目 CRUD + 组卷（assemble）请求/响应。

组卷（assemble）核心契约：
- 请求携带 ``quotas``（category_id → 目标分），省略时用各 category.target_points
- 响应返回选中题目 + 实际总分 + 各分类缺口（deficits）
- 凑不满 100 时由调用方决定：abort / 接受缺口 / AI 兜底
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

from app.schemas.interview import InterviewDimensionLiteral

# ============================================================================
# 分类（QuestionCategory）
# ============================================================================


class CategoryBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=64)
    slug: str = Field(..., min_length=1, max_length=64, pattern=r"^[a-z0-9_-]+$")
    target_points: int = Field(default=0, ge=0, le=100)
    sort_order: int = Field(default=0, ge=0)


class CategoryCreate(CategoryBase):
    pass


class CategoryUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    name: str | None = Field(default=None, min_length=1, max_length=64)
    target_points: int | None = Field(default=None, ge=0, le=100)
    sort_order: int | None = Field(default=None, ge=0)
    is_active: bool | None = None


class CategoryOut(CategoryBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    is_active: bool


# ============================================================================
# 题目（QuestionBankItem）
# ============================================================================


class ItemBase(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: uuid.UUID
    dimension: InterviewDimensionLiteral | None = None
    question: str = Field(..., min_length=4, max_length=1000)
    points: int = Field(..., ge=1, le=100)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    tags: list[str] | None = None
    reference_answer: str | None = Field(default=None, max_length=2000)

    @field_validator("question")
    @classmethod
    def _question_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question cannot be empty")
        return v


class ItemCreate(ItemBase):
    pass


class ItemUpdate(BaseModel):
    model_config = ConfigDict(extra="forbid")

    category_id: uuid.UUID | None = None
    dimension: InterviewDimensionLiteral | None = None
    question: str | None = Field(default=None, min_length=4, max_length=1000)
    points: int | None = Field(default=None, ge=1, le=100)
    difficulty: int | None = Field(default=None, ge=1, le=5)
    tags: list[str] | None = None
    reference_answer: str | None = Field(default=None, max_length=2000)
    is_active: bool | None = None


class ItemOut(ItemBase):
    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    team_id: uuid.UUID
    is_active: bool


# ============================================================================
# 组卷（assemble）
# ============================================================================


class SignalInfo(BaseModel):
    """动态匹配信号（简历/JD 提取的技能词）。"""

    signal: str
    weight: float


class QuotaPlanItem(BaseModel):
    """某分类的动态配额计划项。"""

    category_id: uuid.UUID
    category_name: str
    base_points: int
    quota_points: int
    score: float
    matched: bool


class AssemblePlan(BaseModel):
    """动态组卷计划：信号 + 各分类配额调整（静态组卷时为 None）。"""

    total_target: int
    signals: list[SignalInfo]
    quotas: list[QuotaPlanItem]


class AssembleRequest(BaseModel):
    """按分类配额凑分组卷请求。

    ``quotas`` 省略时：动态模式用简历/JD 匹配后的动态配额；否则用各 category.target_points。
    ``tolerance`` 允许的分数容差（默认 ±5）。
    ``dynamic=true`` 需携带 ``session_id``（从中解析候选人/职位提取信号）。
    """

    model_config = ConfigDict(extra="forbid")

    quotas: dict[uuid.UUID, int] | None = Field(
        default=None, description="category_id → 目标分；省略则动态计算或用各分类 target_points"
    )
    tolerance: int = Field(default=5, ge=0, le=20)
    exclude_question_ids: list[uuid.UUID] | None = Field(
        default=None, description="组卷时排除的题目 id（避免对同一候选人重复出题）"
    )
    dynamic: bool = Field(
        default=False, description="按候选人简历 + JD 动态匹配配额（需 session_id）"
    )
    session_id: uuid.UUID | None = Field(
        default=None, description="面试会话 id（dynamic=true 时必填，用于提取信号）"
    )


class CategoryDeficit(BaseModel):
    """某分类凑分缺口。"""

    category_id: uuid.UUID
    category_name: str
    target: int
    actual: int
    gap: int


class AssembleResponse(BaseModel):
    """组卷结果：选中题目 + 实际总分 + 各分类缺口 + 动态配额计划。"""

    items: list[ItemOut]
    actual_total: int
    target_total: int
    deficits: list[CategoryDeficit]
    plan: AssemblePlan | None = None


# ============================================================================
# 实例化（instantiate：把选中题目写入 interview_questions）
# ============================================================================


ComposeSource = Literal["bank", "ai_fallback"]
"""组卷来源：题库选题 / AI 兜底补充。"""


__all__ = [
    "AssemblePlan",
    "AssembleRequest",
    "AssembleResponse",
    "CategoryBase",
    "CategoryCreate",
    "CategoryDeficit",
    "CategoryUpdate",
    "CategoryOut",
    "ItemBase",
    "ItemCreate",
    "ItemUpdate",
    "ItemOut",
    "QuotaPlanItem",
    "SignalInfo",
    "ComposeSource",
]
