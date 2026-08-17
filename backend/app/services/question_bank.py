"""题库服务：分类/题目 CRUD + 按分类配额凑分组卷（DP 子集和）+ 实例化为面试题
+ 候选人简历/JD 动态配额匹配。

组卷算法（subset_sum_dp）：
- 每个分类内独立做子集和，凑到该分类目标分（±tolerance 容差）
- 优先精确命中，其次容差内最接近 target
- 同分组合优先选**题目更多**的（更多 5 分题 → 覆盖面更广，单组卷约 30 题）
- 凑不到时返回缺口（deficits），由调用方决定 abort / AI 兜底

动态配额（compute_dynamic_quotas + build_candidate_signals）：
- 信号源：候选人简历 skills（权重 1.0）+ JD 硬性 required_skills（2.0）
  + JD 正文命中的分类名/slug（1.5）
- 每个分类算亲和度分：信号与分类名/slug 直接命中 + 信号在分类题目 tags 中的命中率
- 按亲和度在基准配额上放大（最高 2×），四舍五入到 5 的倍数，夹在 [min, max]，
  再归一到固定总分（默认 150，约 30 题）
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.middleware.error_handler import NotFoundError
from app.core.middleware.error_handler import ValidationError as AppValidationError
from app.models.interview import InterviewQuestion
from app.models.question_bank import QuestionBankItem, QuestionCategory

# ============================================================================
# 凑分算法（纯函数，便于单测）
# ============================================================================


def subset_sum_dp(
    items: list[QuestionBankItem],
    target: int,
    tolerance: int = 5,
) -> tuple[list[QuestionBankItem], int]:
    """在 items 内凑到 target 分（允许 ±tolerance）。

    返回 (选中题目, 实际总分)。优先精确命中 target；否则在容差区间内选最接近
    target 的组合；完全不可达返回 ([], 0)。

    同分多种组合时优先选**题目数更多**的组合（如 target=15 时选 5+5+5 而非 10+5），
    使单组卷题目数最大化（约 30 题）。字典序贪心替换：同分时保留更长路径，
    不保证全局最大题数，但实测对 5/10 分值题目足够。

    dict-based DP：dp[s] = 凑出分数 s 所选的 item index 列表，None 表示不可达。
    复杂度 O(n × hi)，n=题目数（通常 <60），hi=target+tolerance（通常 ≤155）。
    """
    if not items or target <= 0:
        return [], 0

    lo = max(0, target - tolerance)
    hi = target + tolerance
    # 上界保护：避免极端 points 导致 hi 过大（实际题目数有限，DP 表大小受 n 限制）
    dp: dict[int, list[int] | None] = {0: []}

    for i, it in enumerate(items):
        p = it.points
        if p <= 0 or p > hi:
            continue
        # 倒序遍历已可达分数，避免同一题被选多次（0-1 背包）
        for s in sorted((s for s in dp if dp[s] is not None), reverse=True):
            ns = s + p
            if ns > hi:
                continue
            old = dp.get(ns)
            new_path = (dp[s] or []) + [i]
            # 同分已有组合且题目数不少于新路径 → 保留原组合（prefer-more）
            if old is not None and len(old) >= len(new_path):
                continue
            dp[ns] = new_path

    # 候选：容差区间内所有可达分数
    candidates = [s for s, picked in dp.items() if picked is not None and lo <= s <= hi]
    if not candidates:
        return [], 0
    # 排序：优先 abs(s-target) 小，其次 s 大（同等接近时取较大总分）
    best = min(candidates, key=lambda s: (abs(s - target), -s))
    picked = [items[i] for i in (dp[best] or [])]
    return picked, best


# ============================================================================
# 动态配额（纯函数，便于单测）
# ============================================================================


_QUOTA_STEP: int = 5
"""配额步长：与题目分值体系（5/10）对齐，DP 才能精确命中。"""

_DEFAULT_TOTAL_TARGET: int = 150
"""单组卷目标总分：约 30 题（5 分题为主时 150/5=30）。"""

_MIN_QUOTA: int = 5
_MAX_QUOTA: int = 30
"""单分类配额上下限：防匹配分类独占整卷 / 被压成 0。"""


def _norm_token(s: str) -> str:
    """信号归一化：去空白、转小写。"""
    return s.strip().lower()


def _token_matches(signal: str, target: str) -> bool:
    """宽松匹配：双向子串，短 token 防误命中（拉丁 ≥3 字符 / CJK ≥2 字符）。"""
    sig, tgt = _norm_token(signal), _norm_token(target)
    if not sig or not tgt:
        return False
    min_len = 2 if any("\u4e00" <= ch <= "\u9fff" for ch in sig) else 3
    if len(sig) < min_len:
        return False
    return sig in tgt or tgt in sig


def compute_dynamic_quotas(
    categories: list[tuple[uuid.UUID, str, str, int]],
    items_tags: dict[uuid.UUID, list[list[str]]],
    signals: list[tuple[str, float]],
    *,
    total_target: int = _DEFAULT_TOTAL_TARGET,
    min_quota: int = _MIN_QUOTA,
    max_quota: int = _MAX_QUOTA,
    step: int = _QUOTA_STEP,
) -> tuple[dict[uuid.UUID, int], dict[uuid.UUID, float]]:
    """按简历/JD 信号计算各分类动态配额（纯函数）。

    Args:
        categories: [(category_id, slug, name, base_points)]，base 为静态 target_points
        items_tags: category_id → 每题的 tags 列表（用于亲和度统计）
        signals: [(信号词, 权重)]，如 [("rag", 2.0), ("python", 1.0)]
        total_target: 动态配额归一后的总分（约 30 题 = 150 分）

    Returns:
        (quotas: category_id → 配额分, scores: category_id → 亲和度分)

    算法：亲和度 = Σ(信号权重 × 命中率) + 直接命中加成；配额 = base × (1 + 亲和度/最大亲和度)，
    取 5 的倍数、夹 [min, max]，最后按比例增减凑到 total_target。
    """
    if not categories:
        return {}, {}

    # 1) 每分类亲和度分
    scores: dict[uuid.UUID, float] = {}
    for cat_id, slug, name, _base in categories:
        score = 0.0
        cat_words = [slug, name]
        cat_tags = items_tags.get(cat_id, [])
        for signal, weight in signals:
            # 直接命中：信号与 slug/name 互为子串 → 强信号加成
            if any(_token_matches(signal, w) for w in cat_words):
                score += 2.0 * weight
                continue
            # tags 命中率：该分类多少比例的题 tags 命中信号
            if cat_tags:
                hit = sum(1 for tags in cat_tags if any(_token_matches(signal, t) for t in tags))
                score += (hit / len(cat_tags)) * weight
        scores[cat_id] = round(score, 4)

    max_score = max(scores.values(), default=0.0)

    # 2) base × (1 + rel) 放大 → 取步长倍数 → 夹上下限（base=0 的分类不注入）
    def _snap(v: float) -> int:
        return max(min_quota, min(max_quota, round(v / step) * step))

    quotas: dict[uuid.UUID, int] = {}
    for cat_id, _slug, _name, base in categories:
        if base <= 0:
            quotas[cat_id] = 0
            continue
        rel = scores[cat_id] / max_score if max_score > 0 else 0.0
        quotas[cat_id] = _snap(base * (1.0 + rel))

    # 3) 归一到 total_target（在 min/max 约束内尽量逼近）
    active = [cid for cid, q in quotas.items() if q > 0]
    for _ in range(50):
        diff = total_target - sum(quotas[cid] for cid in active)
        if diff == 0:
            break
        sign = 1 if diff > 0 else -1
        moved = False
        # 按当前配额从大到小逐个调 step，优先调大分类（余量足）
        for cid in sorted(active, key=lambda c: -quotas[c]):
            new_q = quotas[cid] + sign * step
            if min_quota <= new_q <= max_quota:
                quotas[cid] = new_q
                moved = True
                if sum(quotas[c] for c in active) == total_target:
                    break
        if not moved:
            break  # 全部触界，无法再逼近

    return quotas, scores


# ============================================================================
# Service
# ============================================================================


class QuestionBankService:
    """题库 CRUD + 组卷 + 实例化。team 级隔离。"""

    def __init__(self, db: AsyncSession):
        self._db = db

    # ----- 分类 CRUD -----

    async def list_categories(
        self, *, team_id: uuid.UUID, active_only: bool = False
    ) -> list[QuestionCategory]:
        stmt = select(QuestionCategory).where(QuestionCategory.team_id == team_id)
        if active_only:
            stmt = stmt.where(QuestionCategory.is_active.is_(True))
        stmt = stmt.order_by(QuestionCategory.sort_order, QuestionCategory.name)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_category(self, *, team_id: uuid.UUID, category_id: uuid.UUID) -> QuestionCategory:
        stmt = select(QuestionCategory).where(
            QuestionCategory.team_id == team_id,
            QuestionCategory.id == category_id,
        )
        cat = (await self._db.execute(stmt)).scalar_one_or_none()
        if cat is None:
            raise NotFoundError(f"category {category_id} not found")
        return cat

    async def create_category(
        self, *, team_id: uuid.UUID, payload: dict
    ) -> QuestionCategory:
        cat = QuestionCategory(team_id=team_id, **payload)
        self._db.add(cat)
        await self._db.flush()
        return cat

    async def update_category(
        self, *, team_id: uuid.UUID, category_id: uuid.UUID, payload: dict
    ) -> QuestionCategory:
        cat = await self.get_category(team_id=team_id, category_id=category_id)
        for k, v in payload.items():
            if v is not None:
                setattr(cat, k, v)
        await self._db.flush()
        return cat

    async def delete_category(self, *, team_id: uuid.UUID, category_id: uuid.UUID) -> None:
        cat = await self.get_category(team_id=team_id, category_id=category_id)
        await self._db.delete(cat)
        await self._db.flush()

    # ----- 题目 CRUD -----

    async def list_items(
        self,
        *,
        team_id: uuid.UUID,
        category_id: uuid.UUID | None = None,
        active_only: bool = True,
    ) -> list[QuestionBankItem]:
        stmt = select(QuestionBankItem).where(QuestionBankItem.team_id == team_id)
        if category_id is not None:
            stmt = stmt.where(QuestionBankItem.category_id == category_id)
        if active_only:
            stmt = stmt.where(QuestionBankItem.is_active.is_(True))
        stmt = stmt.order_by(QuestionBankItem.category_id, QuestionBankItem.points)
        return list((await self._db.execute(stmt)).scalars().all())

    async def get_item(self, *, team_id: uuid.UUID, item_id: uuid.UUID) -> QuestionBankItem:
        stmt = select(QuestionBankItem).where(
            QuestionBankItem.team_id == team_id,
            QuestionBankItem.id == item_id,
        )
        item = (await self._db.execute(stmt)).scalar_one_or_none()
        if item is None:
            raise NotFoundError(f"question bank item {item_id} not found")
        return item

    async def create_item(self, *, team_id: uuid.UUID, payload: dict) -> QuestionBankItem:
        # 校验 category 属于同 team
        await self.get_category(team_id=team_id, category_id=payload["category_id"])
        item = QuestionBankItem(team_id=team_id, **payload)
        self._db.add(item)
        await self._db.flush()
        return item

    async def update_item(
        self, *, team_id: uuid.UUID, item_id: uuid.UUID, payload: dict
    ) -> QuestionBankItem:
        item = await self.get_item(team_id=team_id, item_id=item_id)
        if "category_id" in payload and payload["category_id"] is not None:
            await self.get_category(team_id=team_id, category_id=payload["category_id"])
        for k, v in payload.items():
            if v is not None:
                setattr(item, k, v)
        await self._db.flush()
        return item

    async def delete_item(self, *, team_id: uuid.UUID, item_id: uuid.UUID) -> None:
        item = await self.get_item(team_id=team_id, item_id=item_id)
        await self._db.delete(item)
        await self._db.flush()

    # ----- 组卷（assemble） -----

    async def assemble(
        self,
        *,
        team_id: uuid.UUID,
        quotas: dict[uuid.UUID, int] | None = None,
        tolerance: int = 5,
        exclude_question_ids: list[uuid.UUID] | None = None,
    ) -> tuple[list[QuestionBankItem], int, list[dict]]:
        """按分类配额凑分。

        返回 (选中题目, 实际总分, deficits[])。
        quotas 省略时用各 category.target_points。
        """
        categories = await self.list_categories(team_id=team_id, active_only=True)
        cat_by_id = {c.id: c for c in categories}

        # 确定每个分类的目标分
        if quotas is None:
            targets = {c.id: c.target_points for c in categories if c.target_points > 0}
        else:
            # 仅接受属于本 team 的 category
            targets = {cid: pts for cid, pts in quotas.items() if cid in cat_by_id}

        if not targets:
            raise AppValidationError("no categories with positive target points to assemble")

        exclude = set(exclude_question_ids or [])
        selected: list[QuestionBankItem] = []
        deficits: list[dict] = []
        total = 0
        target_total = 0

        for cat_id, target in targets.items():
            target_total += target
            cat = cat_by_id[cat_id]
            items = [
                it
                for it in await self.list_items(team_id=team_id, category_id=cat_id, active_only=True)
                if it.id not in exclude
            ]
            picked, actual = subset_sum_dp(items, target, tolerance=tolerance)
            selected.extend(picked)
            total += actual
            gap = target - actual
            if gap > tolerance or not picked:
                deficits.append(
                    {
                        "category_id": cat_id,
                        "category_name": cat.name,
                        "target": target,
                        "actual": actual,
                        "gap": gap,
                    }
                )

        return selected, total, deficits

    # ----- 动态配额（简历/JD 匹配）-----

    async def build_candidate_signals(
        self,
        *,
        team_id: uuid.UUID,
        candidate_id: uuid.UUID,
        job_id: uuid.UUID,
    ) -> list[tuple[str, float]]:
        """从候选人简历 + JD 提取组卷信号（词, 权重）。

        信号源（权重高→低）：
        1. JD 硬性 required_skills（2.0）——岗位明确要求，优先满足
        2. JD 正文命中的分类名/slug（1.5）——无硬性要求时的兜底
        3. 候选人简历 skills（1.0）——候选人实际技能，用于出题验证

        同词去重取最大权重。
        """
        from app.models.candidate import CandidateResume, ParsedStructure
        from app.models.job import Job, JobHardRequirement

        signals: dict[str, float] = {}

        def _add(token: str, weight: float) -> None:
            token = token.strip()
            if not token:
                return
            key = _norm_token(token)
            signals[key] = max(signals.get(key, 0.0), weight)

        # 1) JD 硬性技能
        hard = await self._db.scalar(
            select(JobHardRequirement).where(JobHardRequirement.job_id == job_id)
        )
        if hard is not None and hard.required_skills:
            for skill in hard.required_skills:
                _add(skill, 2.0)

        # 2) JD 正文命中分类名/slug
        job = await self._db.get(Job, job_id)
        if job is not None and job.jd_text:
            jd_lower = _norm_token(job.jd_text)
            for cat in await self.list_categories(team_id=team_id, active_only=True):
                for word in (cat.slug, cat.name):
                    w = word.strip().lower()
                    if len(w) >= 3 and w in jd_lower:
                        _add(word, 1.5)
                        break

        # 3) 候选人简历 skills（最新 ParsedStructure）
        stmt = (
            select(ParsedStructure.data)
            .join(CandidateResume, CandidateResume.id == ParsedStructure.resume_id)
            .where(CandidateResume.candidate_id == candidate_id)
            .order_by(CandidateResume.uploaded_at.desc())
            .limit(1)
        )
        row = (await self._db.execute(stmt)).first()
        if row is not None:
            structure = row[0].get("structure") if isinstance(row[0], dict) else None
            if isinstance(structure, dict):
                for skill in structure.get("skills") or []:
                    _add(str(skill), 1.0)

        # 按权重降序，便于前端展示与调试
        result = sorted(signals.items(), key=lambda kv: (-kv[1], kv[0]))
        return [(k, v) for k, v in result]

    async def plan_and_assemble(
        self,
        *,
        team_id: uuid.UUID,
        signals: list[tuple[str, float]] | None = None,
        quotas: dict[uuid.UUID, int] | None = None,
        tolerance: int = 5,
        exclude_question_ids: list[uuid.UUID] | None = None,
        total_target: int = _DEFAULT_TOTAL_TARGET,
    ) -> tuple[list[QuestionBankItem], int, list[dict[str, Any]], dict[str, Any]]:
        """动态配额组卷：返回 (选中题, 总分, deficits, plan)。

        plan = {"total_target", "signals", "quotas": [(category_id/name/base/quota/score/matched)]}
        signals 为空且 quotas 为空时退化为静态 target_points 组卷（plan 仍返回基准信息）。
        """
        categories = await self.list_categories(team_id=team_id, active_only=True)
        cat_tuples = [(c.id, c.slug, c.name, c.target_points) for c in categories]

        if quotas is None:
            items_tags: dict[uuid.UUID, list[list[str]]] = {}
            for c in categories:
                cat_items = await self.list_items(
                    team_id=team_id, category_id=c.id, active_only=True
                )
                items_tags[c.id] = [it.tags or [] for it in cat_items]
            quotas, scores = compute_dynamic_quotas(
                cat_tuples,
                items_tags,
                signals or [],
                total_target=total_target,
            )
        else:
            scores = {cid: 0.0 for cid, _s, _n, _b in cat_tuples}

        items, total, deficits = await self.assemble(
            team_id=team_id,
            quotas=quotas,
            tolerance=tolerance,
            exclude_question_ids=exclude_question_ids,
        )

        cat_by_id = {c.id: c for c in categories}
        plan = {
            "total_target": sum(quotas.values()),
            "signals": [
                {"signal": s, "weight": w} for s, w in (signals or [])
            ],
            "quotas": [
                {
                    "category_id": cid,
                    "category_name": cat_by_id[cid].name if cid in cat_by_id else str(cid),
                    "base_points": cat_by_id[cid].target_points if cid in cat_by_id else 0,
                    "quota_points": q,
                    "score": scores.get(cid, 0.0),
                    "matched": scores.get(cid, 0.0) > 0,
                }
                for cid, q in quotas.items()
                if q > 0
            ],
        }
        return items, total, deficits, plan

    # ----- 实例化（写入 interview_questions） -----

    async def instantiate_from_bank(
        self,
        *,
        candidate_id: uuid.UUID,
        job_id: uuid.UUID,
        session_id: uuid.UUID | None,
        items: list[QuestionBankItem],
    ) -> uuid.UUID:
        """把选中题库题实例化为 InterviewQuestion 行（新 batch_id）。返回 batch_id。"""
        if not items:
            raise AppValidationError("no items to instantiate")
        batch_id = uuid.uuid4()
        for idx, it in enumerate(items):
            self._db.add(
                InterviewQuestion(
                    candidate_id=candidate_id,
                    job_id=job_id,
                    session_id=session_id,
                    batch_id=batch_id,
                    dimension=it.dimension or "skill",
                    question=it.question,
                    sort_order=idx,
                    generated_by="bank",
                    bank_question_id=it.id,
                )
            )
        await self._db.flush()
        return batch_id


__all__ = ["QuestionBankService", "subset_sum_dp", "compute_dynamic_quotas"]
