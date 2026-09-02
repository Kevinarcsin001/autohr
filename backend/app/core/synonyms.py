"""共享同义词表：技能 / 学历常见别名归一。

背景（评估报告 P0-3）：硬筛一票否决环节原本用精确字符串匹配，
"js" ≠ "JavaScript"、"大模型" ≠ "LLM" 会系统性错杀候选人。
本模块提供**严格等价**归一，供 FilterService 技能匹配使用。

⚠️ 语义边界（勿混用）：reasoning.py 另有一份 `_SYNONYMS` 是
**相关性扩展**表（python → django/flask，用于扩大事实证据搜索，
宁宽勿漏）。两者语义不同——若把相关性表用于硬筛，会把
「要求 Python、只会 Django」错误判为达标，放宽一票否决条件。

设计原则（KISS）：
- 只收录**语义等价**的别名组；相似但不同的技术（MySQL/PostgreSQL）不合并
- 纯函数 + 无 IO，可直接复用
- 匹配一律基于小写归一（中文无大小写，无副作用）
"""
from __future__ import annotations

# 语义等价别名组：组内任一词与其它词互相等价
_SYNONYM_GROUPS: tuple[frozenset[str], ...] = (
    # 学历
    frozenset({"high_school", "高中", "中专"}),
    frozenset({"bachelor", "本科", "学士"}),
    frozenset({"master", "硕士", "研究生"}),
    frozenset({"phd", "博士", "doctor"}),
    # 技能高频别名
    frozenset({"js", "javascript", "es6"}),
    frozenset({"ts", "typescript"}),
    frozenset({"py", "python"}),
    frozenset({"golang", "go"}),
    frozenset({"k8s", "kubernetes"}),
    frozenset({"llm", "大模型", "大型语言模型"}),
    frozenset({"aigc", "生成式ai", "生成式人工智能"}),
    frozenset({"nlp", "自然语言处理"}),
    frozenset({"cv", "计算机视觉"}),
)

# term(小写) → 所属组展开集（含自身）；启动时构建一次，查询 O(1)
_INDEX: dict[str, frozenset[str]] = {}
for _group in _SYNONYM_GROUPS:
    for _term in _group:
        _existing = _INDEX.get(_term)
        _INDEX[_term] = _existing | _group if _existing else _group


def expand_term(term: str) -> frozenset[str]:
    """返回术语的同义词展开集（小写归一，含自身）。

    无别名收录的术语返回仅含自身小写的单元素集。
    """
    key = term.strip().lower()
    if not key:
        return frozenset()
    return _INDEX.get(key, frozenset({key}))


def skills_satisfied(required: str, candidate_skills: set[str]) -> bool:
    """判断单条必备技能是否被候选人技能集满足（同义词展开后求交）。

    Args:
        required: JD 必备技能项（原文，大小写不敏感）。
        candidate_skills: 候选人技能集（小写归一后的集合）。
    """
    required_terms = expand_term(required)
    if not required_terms:
        return False
    for cand in candidate_skills:
        if expand_term(cand) & required_terms:
            return True
    return False


def expand_skills(skills: set[str]) -> set[str]:
    """把技能集合展开为同义词并集（用于批量比较场景）。"""
    out: set[str] = set()
    for s in skills:
        out |= expand_term(s)
    return out


__all__ = ["expand_term", "skills_satisfied", "expand_skills"]
