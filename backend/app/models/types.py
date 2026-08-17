"""自定义 SQLAlchemy 类型：Fernet 加密字符串 + 集中 ENUM 定义。

PII 字段（candidates.name / phone / email、email_configs.password_enc、
users.password_hash 不在此列——bcrypt 自带保护）写入前自动加密、读取时自动解密。

Fernet key 来自 ``settings.FERNET_KEY``；为空时（开发期）退化为透传，
让脚手架在 ``make gen-fernet`` 之前也能跑，但生产强制要求非空。
"""

from __future__ import annotations

from typing import Any

from cryptography.fernet import Fernet
from sqlalchemy import String, TypeDecorator

from app.core.config import settings
from app.models._compat import enum_compat

# ============================================================================
# Fernet 加密字符串类型
# ============================================================================


def _get_fernet() -> Fernet | None:
    """从 settings 拿 Fernet 实例；key 为空时返回 None（开发态）。"""
    key = settings.FERNET_KEY
    if not key:
        return None
    return Fernet(key.encode("utf-8") if isinstance(key, str) else key)


class EncryptedString(TypeDecorator[str]):
    """Fernet 对称加密的字符串列。

    - 写入：明文 → ``Fernet.encrypt`` → ciphertext（base64 字符串）
    - 读取：ciphertext → ``Fernet.decrypt`` → 明文

    若 ``settings.FERNET_KEY`` 为空（开发期），退化为透传以便快速启动；
    生产环境必须设置（否则 PII 直接入库）。

    非字符串 / 非法密文原样返回，避免损坏数据导致全表读取失败。
    """

    impl = String
    cache_ok = True

    def process_bind_param(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            value = str(value)
        f = _get_fernet()
        if f is None:
            return value
        return f.encrypt(value.encode("utf-8")).decode("utf-8")

    def process_result_value(self, value: Any, dialect: Any) -> str | None:
        if value is None:
            return None
        if not isinstance(value, str):
            return value
        f = _get_fernet()
        if f is None:
            return value
        try:
            return f.decrypt(value.encode("utf-8")).decode("utf-8")
        except Exception:
            # 容错：可能是历史未加密数据或 key 切换，原样返回避免阻塞读取
            return value


# ============================================================================
# 集中 ENUM 定义（避免散落在各 model 中重复）
# ============================================================================

UserRole = enum_compat("user_role", "admin", "member")
JobStatus = enum_compat("job_status", "draft", "active", "closed")
EducationLevel = enum_compat("education_level", "high_school", "bachelor", "master", "phd")
SourceType = enum_compat("source_type", "upload", "platform", "email")
ParseStatus = enum_compat("parse_status", "pending", "success", "failed", "low_text")
ScoreReasonType = enum_compat("score_reason_type", "recommend", "disqualify")
InterviewDimension = enum_compat(
    "interview_dimension", "skill", "project", "weakness", "culture", "communication"
)
DedupMatchStatus = enum_compat("dedup_match_status", "pending", "merged", "rejected")
LLMScope = enum_compat("llm_scope", "extractor", "scorer", "reasoning", "interview")
AsyncJobType = enum_compat(
    "async_job_type",
    "parse",
    "extract",
    "screen",
    "score",
    "email_fetch",
    "export",
)
AsyncJobStatus = enum_compat("async_job_status", "queued", "running", "success", "failed", "retry")
InterviewSessionStatus = enum_compat(
    "interview_session_status", "scheduled", "in_progress", "completed"
)
HiringRecommendationEnum = enum_compat("hiring_recommendation", "hire", "reserve", "reject")


__all__ = [
    "EncryptedString",
    "UserRole",
    "JobStatus",
    "EducationLevel",
    "SourceType",
    "ParseStatus",
    "ScoreReasonType",
    "InterviewDimension",
    "DedupMatchStatus",
    "LLMScope",
    "AsyncJobType",
    "AsyncJobStatus",
    "InterviewSessionStatus",
    "HiringRecommendationEnum",
]
