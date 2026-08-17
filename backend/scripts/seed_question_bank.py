"""面试题库种子脚本：从 question_bank_data/*.json 批量加载分类与题目入库。

数据组织（与硬编码解耦，便于分批扩充到 1000 题）：
    question_bank_data/
    ├── _categories.json     # 全部分类声明（slug/name/target_points/sort_order），target 合计 = 100
    └── {slug}.json          # 每个分类的题目（items[]），可逐步增加；缺失某个 slug.json 视为该分类暂未填充

每个 {slug}.json 结构：
    {
      "category": {"slug": "...", "name": "...", "target_points": N, "sort_order": N},
      "items": [
        {"question": "...", "points": 10, "difficulty": 3, "tags": [...],
         "dimension": "skill", "reference_answer": "..."},
        ...
      ]
    }

幂等执行：按 (team, slug) 去重分类，按 (team, category, question 文本) 去重题目。
重复运行只插新题，不覆盖已有题目的 points（避免破坏已凑好的卷子）。

用法：
    cd backend
    uv run python scripts/seed_question_bank.py                 # 默认 team（首个）
    uv run python scripts/seed_question_bank.py --team-name X   # 指定 team 名称

题目与参考答案为原创编写，基于 2024-2025 公开技术资料的常见考点方向，
覆盖各领域高频知识点。不照搬任何单一来源原文。

配额设计（各分类 target_points 合计 155，约 31 题）：见 _categories.json。
组卷（assemble）用 DP 子集和按 target 在各分类内凑分，同分组合优先题目更多者；
分值以 5/10 为主；动态组卷（plan_and_assemble）按简历/JD 信号重分配额后归一 150 分。
"""
from __future__ import annotations

import argparse
import asyncio
import json
import sys
from pathlib import Path

# 让脚本能 import app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select  # noqa: E402

from app.core.db import AsyncSessionLocal, engine  # noqa: E402
from app.models.question_bank import QuestionBankItem, QuestionCategory  # noqa: E402
from app.models.team import Team  # noqa: E402
from app.services.question_bank import QuestionBankService  # noqa: E402

DATA_DIR = Path(__file__).resolve().parent / "question_bank_data"


# ============================================================================
# 数据加载
# ============================================================================


def _load_categories() -> list[dict]:
    """从 _categories.json 加载全部分类声明。"""
    path = DATA_DIR / "_categories.json"
    if not path.exists():
        raise SystemExit(f"未找到分类声明文件：{path}")
    data = json.loads(path.read_text(encoding="utf-8"))
    cats = data.get("categories", [])
    if not cats:
        raise SystemExit(f"{path} 中未声明任何分类")
    total = sum(c["target_points"] for c in cats)
    print(f"分类声明：{len(cats)} 个，target_points 合计 = {total}"
          + (" ✓" if total == 100 else f" ⚠（建议=100）"))
    return cats


def _load_items(slug: str) -> tuple[dict | None, list[dict]]:
    """从 {slug}.json 加载该分类的 category 覆盖信息与题目列表。

    文件不存在 → (None, [])，表示该分类暂未填充（分类仍会创建，仅无题）。
    """
    path = DATA_DIR / f"{slug}.json"
    if not path.exists():
        return None, []
    data = json.loads(path.read_text(encoding="utf-8"))
    return data.get("category"), data.get("items", [])


# ============================================================================
# 种子逻辑
# ============================================================================


async def _resolve_team(session, team_name: str | None) -> Team:
    """定位目标 team：按 name 或取首个 team；没有则报错引导。"""
    if team_name:
        stmt = select(Team).where(Team.name == team_name)
    else:
        stmt = select(Team).order_by(Team.created_at).limit(1)
    team = (await session.execute(stmt)).scalar_one_or_none()
    if team is None:
        raise SystemExit(
            "未找到 team。请先注册一个团队/用户，或用 --team-name 指定。\n"
            "(SQLite 开发库可用: rm data/autohr.db 后重启 backend 注册账号)"
        )
    return team


async def _upsert_category(session, team_id, slug: str, name: str, target: int, sort: int) -> QuestionCategory:
    """幂等：按 (team, slug) 查找，存在则更新 target/sort/name，不存在则新建。"""
    stmt = select(QuestionCategory).where(
        QuestionCategory.team_id == team_id,
        QuestionCategory.slug == slug,
    )
    cat = (await session.execute(stmt)).scalar_one_or_none()
    if cat is None:
        cat = QuestionCategory(
            team_id=team_id, slug=slug, name=name,
            target_points=target, sort_order=sort,
        )
        session.add(cat)
    else:
        cat.name = name
        cat.target_points = target
        cat.sort_order = sort
    await session.flush()
    return cat


async def _upsert_item(session, team_id, category_id, spec: dict) -> tuple[QuestionBankItem, bool]:
    """幂等：按 (team, category, question 文本) 查找。返回 (item, created)。"""
    stmt = select(QuestionBankItem).where(
        QuestionBankItem.team_id == team_id,
        QuestionBankItem.category_id == category_id,
        QuestionBankItem.question == spec["question"],
    )
    item = (await session.execute(stmt)).scalar_one_or_none()
    if item is None:
        item = QuestionBankItem(
            team_id=team_id,
            category_id=category_id,
            question=spec["question"],
            points=spec["points"],
            difficulty=spec.get("difficulty"),
            dimension=spec.get("dimension"),
            tags=spec.get("tags"),
            reference_answer=spec.get("reference_answer"),
        )
        session.add(item)
        await session.flush()
        return item, True
    # 已存在则刷新可变字段（不覆盖 points 以免破坏已凑好的卷子，除非显式变更）
    item.reference_answer = spec.get("reference_answer")
    item.difficulty = spec.get("difficulty")
    item.dimension = spec.get("dimension")
    item.tags = spec.get("tags")
    await session.flush()
    return item, False


async def seed(team_name: str | None) -> None:
    categories = _load_categories()

    async with AsyncSessionLocal() as session:
        team = await _resolve_team(session, team_name)
        print(f"目标 team: {team.name} ({team.id})")

        # 1. 分类（全部创建，含暂未填充题目的分类）
        cat_by_slug: dict[str, QuestionCategory] = {}
        for c in categories:
            cat = await _upsert_category(
                session, team.id, c["slug"], c["name"], c["target_points"], c["sort_order"]
            )
            cat_by_slug[c["slug"]] = cat

        # 2. 题目（按 {slug}.json 填充，允许部分分类暂无文件）
        total_new = 0
        total_skip = 0
        grand_total_points = 0
        for c in categories:
            slug = c["slug"]
            cat = cat_by_slug[slug]
            _, items = _load_items(slug)
            if not items:
                print(f"  [{slug}] {cat.name}：暂未填充（待扩充）")
                continue
            cat_new = 0
            for spec in items:
                _, created = await _upsert_item(session, team.id, cat.id, spec)
                if created:
                    total_new += 1
                    cat_new += 1
                else:
                    total_skip += 1
            cat_points = sum(it["points"] for it in items)
            grand_total_points += cat_points
            gap = "✓" if cat_points >= c["target_points"] else f"⚠ 题库总分 {cat_points} < target {c['target_points']}"
            print(f"  [{slug}] {cat.name}：{len(items)} 题（新增 {cat_new}），题库总分 {cat_points} {gap}")

        await session.commit()
        print(f"\n✓ 完成：新增 {total_new} 题，已存在跳过 {total_skip} 题，题库总题数 {total_new + total_skip}")

        # 3. assemble 验证（静态配额）
        svc = QuestionBankService(session)
        picked, actual, deficits = await svc.assemble(team_id=team.id)
        static_total = sum(c["target_points"] for c in categories)
        print(
            f"\n组卷验证：选中 {len(picked)} 题，实际总分 {actual} / {static_total}，"
            f"缺口分类 {len(deficits)} 个"
        )
        if deficits:
            for d in deficits:
                print(f"  ⚠ {d['category_name']}: 目标 {d['target']}，实际 {d['actual']}，缺 {d['gap']}")
            print("  （缺口分类需继续扩充题目至题库总分 ≥ target）")
        else:
            print(f"  ℹ 无缺口，凑到 {actual} 分")

    await engine.dispose()


def main() -> None:
    parser = argparse.ArgumentParser(description="从 JSON 批量填充题库种子数据")
    parser.add_argument("--team-name", default=None, help="目标 team 名称（默认首个 team）")
    args = parser.parse_args()
    asyncio.run(seed(args.team_name))


if __name__ == "__main__":
    main()
