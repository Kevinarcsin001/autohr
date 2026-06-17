"""招聘平台导入 schema（任务 10）。

支持的平台：
- boss：Boss 直聘
- zhipin：（保留，等价 boss 命名空间兼容）
- zhilian：智联招聘
- liepin：猎聘

字段映射后的标准结构（CandidateStructure），所有平台 mapper 输出必须经此模型校验。
"""
from __future__ import annotations

from typing import Literal
from uuid import UUID

from pydantic import BaseModel, ConfigDict, Field, field_validator

Platform = Literal["boss", "zhilian", "liepin"]
"""识别出的平台类型；null 表示不支持。"""

PlatformPackageKind = Literal["excel", "attachment_zip"]
"""包类型：
- excel：结构化 Excel → 直接映射 CandidateStructure 跳过 OCR
- attachment_zip：简历附件包（PDF/Word/图片）→ 走任务 9/13 解析链路
"""


# ============================================================================
# 标准候选人结构（所有 mapper 必须产出此模型；缺失字段 → None）
# ============================================================================


class CandidateStructure(BaseModel):
    """跨平台标准化候选人结构。

    所有字段可选 —— 不同平台能提供的字段不同；
    必须 ≥ name + (phone 或 email) 才视为有效（mapper 层校验）。
    """

    model_config = ConfigDict(extra="forbid")

    name: str = Field(..., min_length=1, max_length=100)
    phone: str | None = Field(default=None, max_length=30)
    email: str | None = Field(default=None, max_length=200)
    gender: Literal["male", "female", "unknown"] | None = None
    age: int | None = Field(default=None, ge=0, le=150)
    education: (
        Literal["high_school", "bachelor", "master", "phd", "other"] | None
    ) = None
    years_experience: int | None = Field(default=None, ge=0, le=70)
    applied_position: str | None = Field(default=None, max_length=200)
    current_company: str | None = Field(default=None, max_length=200)
    current_title: str | None = Field(default=None, max_length=200)
    location: str | None = Field(default=None, max_length=200)
    source_url: str | None = Field(default=None, max_length=500)
    raw: dict[str, str] | None = Field(
        default=None, description="未映射的原始列（key=列名, value=单元格值）"
    )

    @field_validator("phone", "email")
    @classmethod
    def _strip(cls, v: str | None) -> str | None:
        if v is None:
            return None
        v = v.strip()
        return v or None


# ============================================================================
# 检测结果
# ============================================================================


class DetectionSignal(BaseModel):
    """单条识别证据（用于审计 / debug）。"""

    model_config = ConfigDict(extra="forbid")

    source: Literal["filename", "header", "zip_member", "sniff"]
    weight: float
    matched: str


class DetectionResult(BaseModel):
    """``detect_platform`` 返回。"""

    model_config = ConfigDict(extra="forbid")

    platform: Platform | None = Field(
        ..., description="None 表示所有平台得分均低于阈值"
    )
    confidence: float = Field(..., ge=0.0, le=1.0)
    package_kind: PlatformPackageKind | None = None
    threshold: float
    signals: list[DetectionSignal]
    scores: dict[Platform, float]


# ============================================================================
# 导入结果
# ============================================================================


class ImportedCandidateItem(BaseModel):
    """单个候选人的导入结果。"""

    model_config = ConfigDict(extra="forbid")

    candidate_id: UUID | None = None
    resume_id: UUID | None = None
    name: str
    status: Literal["ok", "rejected"]
    reject_reason: Literal[
        "invalid_structure",
        "missing_identity",
        "storage_error",
        "duplicate",
        "unknown",
    ] | None = None


class PlatformImportResult(BaseModel):
    """``import_package`` 整体返回。"""

    model_config = ConfigDict(extra="forbid")

    platform: Platform
    package_kind: PlatformPackageKind
    candidates: list[ImportedCandidateItem]
    imported: int
    rejected: int


class UnsupportedPlatformErrorDetail(BaseModel):
    """422 响应体（不支持的平台）。"""

    model_config = ConfigDict(extra="forbid")

    code: Literal["unsupported_platform"] = "unsupported_platform"
    message: str
    detection: DetectionResult
    support_feedback_url: str


__all__ = [
    "Platform",
    "PlatformPackageKind",
    "CandidateStructure",
    "DetectionSignal",
    "DetectionResult",
    "ImportedCandidateItem",
    "PlatformImportResult",
    "UnsupportedPlatformErrorDetail",
]
