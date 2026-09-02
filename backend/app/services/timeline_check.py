"""简历时间线核验（纯规则，不调 LLM）。

背景（评估报告 P1-6）：AI 优化简历泛滥，评分完全基于简历自述；
工作经历时间线重叠是最容易规则化检测的真实性信号。产出 warning
供 HR 参考——**只提示不定罪**，重叠也可能由兼职/笔误造成。

设计原则（KISS + 零误报优先）：
- 日期解析不出的条目**跳过不报**（不臆造问题）
- 检测仅覆盖：时间区间两两重叠；空窗/倒序等暂不做（YAGNI）
- 纯函数无 IO，供 candidate_detail 等下游复用
"""
from __future__ import annotations

import re
from dataclasses import dataclass

from app.schemas.candidate_structure import WorkHistoryEntry

# 支持格式：2020-03 / 2020 / 2020年3月 / 2020/03 / 2020.03 / 2020-03-15
_DATE_RE = re.compile(
    r"^\s*(\d{4})\s*[年./-]?\s*(?:(\d{1,2})\s*[月./-]?)?\s*(?:\d{1,2}\s*日?)?\s*$"
)
_PRESENT_WORDS = frozenset({"present", "now", "至今", "现在", "当前", "目前"})


@dataclass(frozen=True)
class _Period:
    """半开区间 [start_month, end_month)；end=None 表示至今。"""

    index: int
    company: str
    start_month: int  # YYYYMM
    end_month: int | None


def _parse_month(raw: str | None) -> int | None:
    """宽松解析日期字符串 → YYYYMM 整数；解析失败 / 至今类词 → None。"""
    if not raw:
        return None
    text = raw.strip()
    if not text:
        return None
    if text.lower() in _PRESENT_WORDS:
        return None
    m = _DATE_RE.match(text)
    if m is None:
        return None
    year = int(m.group(1))
    month = int(m.group(2)) if m.group(2) else 1
    if not 1 <= month <= 12 or not 1900 <= year <= 2100:
        return None
    return year * 100 + month


def _label(entry: WorkHistoryEntry, index: int) -> str:
    return (entry.company or "").strip() or f"第{index + 1}段经历"


def _to_period(entry: WorkHistoryEntry, index: int) -> _Period | None:
    start = _parse_month(entry.start_date)
    if start is None:
        return None  # 起点解析不出 → 无法判定，跳过（零误报优先）
    end = _parse_month(entry.end_date)
    # 显式 end 早于 start：日期填反/不可信，同样跳过不报
    if end is not None and end < start:
        return None
    return _Period(
        index=index,
        company=_label(entry, index),
        start_month=start,
        end_month=end,
    )


def detect_timeline_issues(
    work_history: list[WorkHistoryEntry] | None,
) -> list[str]:
    """检测工作经历时间区间重叠 → 中文 warning 列表。

    规则：
    - 按起始时间排序后两两比较；前段 end ≥ 后段 start 即重叠
    - end 为「至今」的开放区间与后序经历必然重叠（除非后段更晚开始——仍会命中）
    - 解析不出起点的条目不参与检测

    Returns:
        warning 描述列表（无人为上限，一般 0-2 条）；输入为空 → 空列表。
    """
    if not work_history:
        return []

    periods: list[_Period] = []
    for i, entry in enumerate(work_history):
        p = _to_period(entry, i)
        if p is not None:
            periods.append(p)
    periods.sort(key=lambda p: p.start_month)

    warnings: list[str] = []
    for i in range(len(periods) - 1):
        cur, nxt = periods[i], periods[i + 1]
        overlap = cur.end_month is None or cur.end_month >= nxt.start_month
        if overlap:
            warnings.append(
                f"时间线疑似重叠：「{cur.company}」与「{nxt.company}」的"
                f"在职时间区间存在交叠，建议面试时核实"
            )
    return warnings


__all__ = ["detect_timeline_issues"]
