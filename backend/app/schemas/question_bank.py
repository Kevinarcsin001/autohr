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


class AssembleRequest(BaseModel):
    """按分类配额凑 100 分组卷请求。

    ``quotas`` 省略时用各 category.target_points 之和作为目标。
    ``tolerance`` 允许的分数容差（默认 ±5）。
    """

    model_config = ConfigDict(extra="forbid")

    quotas: dict[uuid.UUID, int] | None = Field(
        default=None, description="category_id → 目标分；省略则用各分类 target_points"
    )
    tolerance: int = Field(default=5, ge=0, le=20)
    exclude_question_ids: list[uuid.UUID] | None = Field(
        default=None, description="组卷时排除的题目 id（避免对同一候选人重复出题）"
    )


class CategoryDeficit(BaseModel):
    """某分类凑分缺口。"""

    category_id: uuid.UUID
    category_name: str
    target: int
    actual: int
    gap: int


class AssembleResponse(BaseModel):
    """组卷结果：选中题目 + 实际总分 + 各分类缺口。"""

    items: list[ItemOut]
    actual_total: int
    target_total: int
    deficits: list[CategoryDeficit]


# ============================================================================
# 实例化（instantiate：把选中题目写入 interview_questions）
# ============================================================================


ComposeSource = Literal["bank", "ai_fallback"]
"""组卷来源：题库选题 / AI 兜底补充。"""


__all__ = [
    "CategoryBase",
    "CategoryCreate",
    "CategoryUpdate",
    "CategoryOut",
    "ItemBase",
    "ItemCreate",
    "ItemUpdate",
    "ItemOut",
    "AssembleRequest",
    "CategoryDeficit",
    "AssembleResponse",
    "ComposeSource",
]
