"""CandidateStructure Pydantic schema（任务 14）。

设计要点（需求 7.2）：
- **每个字段都附 ``<field>_confidence`` 评分（0.0-1.0）**，不臆造
- 字段无法确定时填 ``null`` + confidence=0
- ``raw_text`` 字段单独存（实际存 ParsedStructure.data 不带 raw_text，
  由 service 层脱敏后单独记录/丢弃，避免 PII 入日志）

字段集合（对齐 design.md `### 5. ExtractorService`）：
- name / phone / email / education / years_of_experience /
  skills[] / expected_salary / current_company / work_history[]
"""
from __future__ import annotations

from typing import Literal

from pydantic import BaseModel, ConfigDict, Field

EducationLevelLiteral = Literal[
    "high_school", "bachelor", "master", "phd", "other"
]


class WorkHistoryEntry(BaseModel):
    """工作经历条目（公司 / 职位 / 时间 / 描述）。"""

    model_config = ConfigDict(extra="ignore")

    company: str | None = None
    title: str | None = None
    start_date: str | None = None  # 自由格式："2020-03" / "2020年3月"
    end_date: str | None = None  # 同上；当前在职可填 "present"
    description: str | None = None


class CandidateStructure(BaseModel):
    """LLM 抽取的候选人结构化字段（每字段附 confidence）。"""

    model_config = ConfigDict(extra="forbid")

    # 基础身份
    name: str | None = None
    name_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    phone: str | None = None
    phone_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    email: str | None = None
    email_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # 教育与经验
    education: EducationLevelLiteral | None = None
    education_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    years_of_experience: int | None = Field(default=None, ge=0, le=80)
    years_of_experience_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # 技能与薪资
    skills: list[str] = Field(default_factory=list)
    skills_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    expected_salary: str | None = None  # 自由格式："20k-30k" / "面议"
    expected_salary_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # 当前公司
    current_company: str | None = None
    current_company_confidence: float = Field(default=0.0, ge=0.0, le=1.0)

    # 工作经历
    work_history: list[WorkHistoryEntry] = Field(default_factory=list)
    work_history_confidence: float = Field(default=0.0, ge=0.0, le=1.0)


# ============================================================================
# 抽取状态（任务 14 专用，区别于 parse_status）
# ============================================================================


ExtractStatus = Literal["extracted", "partial_extracted", "failed"]
"""- ``extracted``：完整结构化（schema 校验通过）
- ``partial_extracted``：第一次 schema 不合 → 重试仍不合 → 降级部分字段
- ``failed``：LLM 调用失败 / 解析错误
"""


__all__ = [
    "CandidateStructure",
    "WorkHistoryEntry",
    "EducationLevelLiteral",
    "ExtractStatus",
]
