"""InterviewService 用的 Pydantic schema（任务 19）。

包含：
- ``InterviewQuestions``：LLM 输出的 5-8 题面试问题（含 dimension + question）
- ``FeedbackRequest`` / ``FeedbackOut``：HR/面试官反馈
- ``InterviewQuestionOut`` / ``BatchResponse``：interview_questions 行对外表示
"""
from __future__ import annotations

import uuid
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ============================================================================
# 类型
# ============================================================================


InterviewDimensionLiteral = Literal["skill", "project", "weakness", "culture"]
"""4 个问题维度：
- ``skill``：技能深挖
- ``project``：项目经历追问
- ``weakness``：潜在短板验证
- ``culture``：文化匹配
"""


# ============================================================================
# LLM 输出 schema
# ============================================================================


class InterviewQuestionItem(BaseModel):
    """单条面试题。"""

    model_config = ConfigDict(extra="forbid")

    dimension: InterviewDimensionLiteral
    question: str = Field(..., min_length=4, max_length=500)
    """问题文本；4-500 字，避免空 / 过长。"""

    @field_validator("question")
    @classmethod
    def _question_non_empty(cls, v: str) -> str:
        v = v.strip()
        if not v:
            raise ValueError("question cannot be empty")
        return v


class InterviewQuestions(BaseModel):
    """LLM 输出 schema：5-8 题，至少 1 条 weakness。"""

    model_config = ConfigDict(extra="forbid")

    questions: list[InterviewQuestionItem] = Field(
        ..., min_length=5, max_length=8
    )

    @field_validator("questions")
    @classmethod
    def _at_least_one_weakness(cls, v: list[InterviewQuestionItem]) -> list[InterviewQuestionItem]:
        if not any(q.dimension == "weakness" for q in v):
            raise ValueError(
                "interview questions must include at least 1 'weakness' question"
            )
        return v


# ============================================================================
# Out
# ============================================================================


class InterviewQuestionOut(BaseModel):
    """interview_questions 行的对外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    candidate_id: uuid.UUID
    job_id: uuid.UUID
    batch_id: uuid.UUID
    dimension: InterviewDimensionLiteral
    question: str
    sort_order: int
    generated_by: str | None = None
    feedback_id: uuid.UUID | None = None
    feedback: str | None = None
    rating: int | None = None


class BatchListResponse(BaseModel):
    """列出某 candidate × job 的所有 batch。"""

    batches: list[uuid.UUID]
    """按 created_at 倒序；最新在前。"""
    current_batch: uuid.UUID | None = None
    total_questions: int


class BatchResponse(BaseModel):
    """生成 / 重新生成后的响应（含本批所有题目）。"""

    batch_id: uuid.UUID
    questions: list[InterviewQuestionOut]
    is_regeneration: bool = False
    temperature: float


class InterviewQuestionListResponse(BaseModel):
    items: list[InterviewQuestionOut]
    total: int


# ============================================================================
# 反馈
# ============================================================================


class FeedbackRequest(BaseModel):
    """HR / 面试官提交反馈。"""

    feedback: str | None = Field(default=None, max_length=2000)
    rating: int | None = Field(default=None, ge=1, le=5)
    """1-5 分；None 表示暂不评分。"""

    @field_validator("feedback")
    @classmethod
    def _feedback_non_empty_if_set(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v if v else None


class FeedbackOut(BaseModel):
    """反馈的对外表示。"""

    model_config = ConfigDict(from_attributes=True)

    id: uuid.UUID
    question_id: uuid.UUID
    reviewer_id: uuid.UUID
    feedback: str | None
    rating: int | None


class FeedbackResponse(BaseModel):
    """反馈写入 / 更新后的响应（含整题最新状态）。"""

    feedback: FeedbackOut
    question: InterviewQuestionOut


__all__ = [
    "InterviewDimensionLiteral",
    "InterviewQuestionItem",
    "InterviewQuestions",
    "InterviewQuestionOut",
    "BatchListResponse",
    "BatchResponse",
    "InterviewQuestionListResponse",
    "FeedbackRequest",
    "FeedbackOut",
    "FeedbackResponse",
]
