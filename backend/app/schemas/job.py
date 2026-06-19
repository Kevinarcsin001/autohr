"""职位（JD）相关 Pydantic schema。

约束：
- title: 1-200 字符
- jd_text: 非空（前端用 markdown 编辑器）
- status: draft | active | closed（与 JobStatus ENUM 一致）
- min_education: high_school | bachelor | master | phd
- min_years: 0-50
- required_skills / excluded_companies: 字符串数组（去重 + trim）
- llm_config: 可选，职位级 LLM 配置覆盖（model/scope/temperature 等）
"""
from __future__ import annotations

from typing import Any

from pydantic import BaseModel, ConfigDict, Field, field_validator

# ============================================================================
# 硬性条件
# ============================================================================

VALID_EDUCATION = {"high_school", "bachelor", "master", "phd"}


class HardRequirements(BaseModel):
    """结构化硬性条件。

    全部字段可选；为 None 表示「不限制」。
    required_skills 与 excluded_companies 数组写入前会去重 + trim。
    """

    min_education: str | None = Field(default=None, description="最低学历")
    min_years: int | None = Field(default=None, ge=0, le=50, description="最低工作年限")
    required_skills: list[str] | None = Field(
        default=None, description="必备技能列表"
    )
    excluded_companies: list[str] | None = Field(
        default=None, description="排除公司列表"
    )

    @field_validator("min_education")
    @classmethod
    def _check_education(cls, v: str | None) -> str | None:
        if v is not None and v not in VALID_EDUCATION:
            raise ValueError(
                f"min_education 必须是 {sorted(VALID_EDUCATION)} 之一"
            )
        return v

    @field_validator("required_skills", "excluded_companies")
    @classmethod
    def _normalize_str_list(cls, v: list[str] | None) -> list[str] | None:
        if v is None:
            return None
        seen: set[str] = set()
        out: list[str] = []
        for item in v:
            s = (item or "").strip()
            if not s or s.lower() in seen:
                continue
            seen.add(s.lower())
            out.append(s)
        return out or None


# ============================================================================
# Job 请求 / 响应
# ============================================================================


class JobCreateRequest(BaseModel):
    """创建职位。"""

    title: str = Field(min_length=1, max_length=200)
    jd_text: str = Field(min_length=1)
    status: str = Field(default="draft", pattern="^(draft|active|closed)$")
    hard_requirements: HardRequirements = Field(default_factory=HardRequirements)
    llm_config: dict[str, Any] | None = None


class JobUpdateRequest(BaseModel):
    """更新职位。

    所有字段可选；未传字段保持原值。每次更新会写 job_versions 快照。
    """

    title: str | None = Field(default=None, min_length=1, max_length=200)
    jd_text: str | None = Field(default=None, min_length=1)
    status: str | None = Field(default=None, pattern="^(draft|active|closed)$")
    hard_requirements: HardRequirements | None = None
    llm_config: dict[str, Any] | None = None


class JobOut(BaseModel):
    """职位响应（含硬性条件）。"""

    id: str
    team_id: str
    title: str
    jd_text: str
    status: str
    current_version: int
    llm_config: dict[str, Any] | None
    hard_requirements: HardRequirements
    created_by: str
    created_at: str
    updated_at: str

    model_config = ConfigDict(from_attributes=True)


class JobListItem(BaseModel):
    """列表项（不含 JD 正文，减小响应体）。"""

    id: str
    title: str
    status: str
    current_version: int
    created_at: str
    updated_at: str


class JobListResponse(BaseModel):
    """分页列表响应。"""

    items: list[JobListItem]
    page: int
    page_size: int
    total: int


class JobVersionOut(BaseModel):
    """职位版本快照。"""

    id: str
    job_id: str
    version: int
    snapshot: dict[str, Any]
    changed_by: str | None
    changed_at: str


__all__ = [
    "HardRequirements",
    "JobCreateRequest",
    "JobUpdateRequest",
    "JobOut",
    "JobListItem",
    "JobListResponse",
    "JobVersionOut",
    "VALID_EDUCATION",
]
