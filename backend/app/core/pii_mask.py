"""PII 出境脱敏：文本进入 LLM prompt 前掩码姓名 / 手机号 / 邮箱 / 证件号。

背景（评估报告 P0-1）：简历 PII 虽在 DB 列级 Fernet 加密、日志只写 hash，
但「发送给第三方 LLM API」这一跳原本是明文——《个保法》第 23 条要求向
第三方提供个人信息需单独告知同意，裸奔不可接受。

设计原则（KISS）：
- 纯函数、无 IO，便于单测与在任何 prompt 出口复用
- 评分 / 理由 / 面试题等下游任务**不需要真实身份信息**，脱敏零信息损失
- **仅抽取阶段（extractor）不脱敏**——它必须从原文中识别 PII 字段本身

调用约定：凡经 ``wrap_untrusted`` 包裹候选人可控文本、且该文本此后不用于
抽取 PII 字段的路径，都应先过 ``mask_pii_text``。
"""
from __future__ import annotations

import re

# 顺序即替换顺序：先长模式（证件号）后短模式，避免子串被部分吞掉
_ID_CARD_RE = re.compile(r"(?<!\d)\d{17}[\dXx](?!\d)|(?<!\d)\d{15}(?!\d)")
_PHONE_RE = re.compile(r"(?<!\d)1[3-9]\d{9}(?!\d)")
_EMAIL_RE = re.compile(
    r"[A-Za-z0-9._%+-]+@[A-Za-z0-9.-]+\.[A-Za-z]{2,}"
)

_ID_CARD_TOKEN = "[证件号]"
_PHONE_TOKEN = "[手机号]"
_EMAIL_TOKEN = "[邮箱]"
_NAME_TOKEN = "该候选人"

_MIN_NAME_LEN = 2
"""短于此的字符串不做姓名替换（单字替换误伤率不可接受）。"""


def mask_pii_text(text: str, *, name: str | None = None) -> str:
    """掩码文本中的证件号 / 手机号 / 邮箱；``name`` 给定时替换为通用称谓。

    Args:
        text: 待脱敏文本（简历原文片段 / 结构化字段拼接等）。
        name: 已抽取的候选人姓名；空串与 None 均视为未知、不做替换。

    Returns:
        脱敏后的文本。找不到任何 PII 时原样返回。
    """
    if not text:
        return text

    out = _ID_CARD_RE.sub(_ID_CARD_TOKEN, text)
    out = _PHONE_RE.sub(_PHONE_TOKEN, out)
    out = _EMAIL_RE.sub(_EMAIL_TOKEN, out)

    candidate_name = (name or "").strip()
    if len(candidate_name) >= _MIN_NAME_LEN:
        out = out.replace(candidate_name, _NAME_TOKEN)

    return out


__all__ = ["mask_pii_text"]
