"""题库服务：分类/题目 CRUD + 按分类配额凑 100 分组卷（DP 子集和）+ 实例化为面试题。

组卷算法（subset_sum_dp）：
- 每个分类内独立做子集和，凑到该分类目标分（±tolerance 容差）
- 优先精确命中，其次容差内最接近 target
- 凑不到时返回缺口（deficits），由调用方决定 abort / AI 兜底
"""
from __future__ import annotations

import uuid

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

    dict-based DP：dp[s] = 凑出分数 s 所选的 item index 列表，None 表示不可达。
    复杂度 O(n × hi)，n=题目数（通常 <50），hi=target+tolerance（通常 ≤105）。
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
            if ns > hi or ns in dp:
                continue
            dp[ns] = (dp[s] or []) + [i]

    # 候选：容差区间内所有可达分数
    candidates = [s for s, picked in dp.items() if picked is not None and lo <= s <= hi]
    if not candidates:
        return [], 0
    # 排序：优先 abs(s-target) 小，其次 s 大（同等接近时取较大总分）
    best = min(candidates, key=lambda s: (abs(s - target), -s))
    picked = [items[i] for i in (dp[best] or [])]
    return picked, best


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


__all__ = ["QuestionBankService", "subset_sum_dp"]
