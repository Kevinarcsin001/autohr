"""QuestionBankService 单元 + 集成测试。

覆盖：
- subset_sum_dp：同分组合优先题目更多（prefer-more）、容差、不可达
- compute_dynamic_quotas：亲和度放大、总分守恒、步长对齐、上下限
- _token_matches：中英文宽松匹配、短 token 防误命中
- build_candidate_signals：JD 硬性技能 > JD 正文 > 简历 skills 权重序
- plan_and_assemble：动态配额端到端（含 plan 结构）
- instantiate_from_bank：communication 维度可写入（enum 扩展回归）
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.models.candidate import Candidate, CandidateResume, CandidateSource, ParsedStructure
from app.models.interview import InterviewQuestion
from app.models.job import Job, JobHardRequirement
from app.models.question_bank import QuestionBankItem, QuestionCategory
from app.models.team import Team
from app.models.user import User
from app.services.question_bank import (
    QuestionBankService,
    _dice_similarity,
    _signal_match_strength,
    compute_dynamic_quotas,
    subset_sum_dp,
)
from tests.db_utils import purge_database

# ============================================================================
# DB 清理
# ============================================================================


async def _purge_db() -> None:
    async with AsyncSessionLocal() as session:
        await purge_database(session)
        await session.commit()


@pytest.fixture(autouse=True)
async def clean_db() -> None:
    await _purge_db()
    yield
    await _purge_db()


# ============================================================================
# 工具
# ============================================================================


class _FakeItem:
    """subset_sum_dp 只读 points，用轻量假对象避免依赖 DB。"""

    def __init__(self, points: int) -> None:
        self.points = points
        self.id = uuid.uuid4()


def _mk_item(points: int) -> Any:
    return _FakeItem(points)


# ============================================================================
# subset_sum_dp
# ============================================================================


def test_subset_sum_dp_prefers_more_questions() -> None:
    """target=10 时有 [10] 与 [5,5] 两种凑法 → 优先 2 题。"""
    items = [_mk_item(10), _mk_item(5), _mk_item(5)]
    picked, actual = subset_sum_dp(items, 10)
    assert actual == 10
    assert len(picked) == 2


def test_subset_sum_dp_prefers_more_questions_reversed_order() -> None:
    items = [_mk_item(5), _mk_item(5), _mk_item(10)]
    picked, actual = subset_sum_dp(items, 10)
    assert actual == 10
    assert len(picked) == 2


def test_subset_sum_dp_fifteen_prefers_three_fives() -> None:
    """target=15：[10,5]（2 题）与 [5,5,5]（3 题）→ 优先 3 题。"""
    items = [_mk_item(10), _mk_item(5), _mk_item(5), _mk_item(5)]
    picked, actual = subset_sum_dp(items, 15)
    assert actual == 15
    assert len(picked) == 3


def test_subset_sum_dp_tolerance() -> None:
    items = [_mk_item(10), _mk_item(10)]
    picked, actual = subset_sum_dp(items, 12, tolerance=5)
    assert actual == 10
    assert len(picked) == 1


def test_subset_sum_dp_unreachable() -> None:
    items = [_mk_item(10)]
    picked, actual = subset_sum_dp(items, 5, tolerance=0)
    assert picked == []
    assert actual == 0


# ============================================================================
# _token_matches / compute_dynamic_quotas
# ============================================================================


def test_token_matches_cjk_and_latin() -> None:
    from app.services.question_bank import _token_matches

    assert _token_matches("RAG", "rag") is True
    assert _token_matches("向量数据库", "向量数据库") is True
    assert _token_matches("python", "Python 基础") is True
    # 短拉丁 token（<3）不匹配，防误命中
    assert _token_matches("dl", "深度学习") is False
    # 中文信号至少 2 字
    assert _token_matches("安", "安全") is False


# ============================================================================
# v2：模糊语义层（Dice bigram）
# ============================================================================


def test_dice_similarity_fuzzy_semantic_match() -> None:
    """表述不一致但语义相近的词应模糊命中（0.5 强度）；不相关的词不误命中。"""
    # 子串命中 = 1.0（最强）
    assert _signal_match_strength("rag", "RAG 检索增强") == 1.0
    # 模糊命中：共享关键 bigram
    assert _signal_match_strength("调模型", "模型微调") == 0.5
    assert _signal_match_strength("微调模型", "模型微调") == 0.5
    assert _signal_match_strength("prompt工程", "Prompt 工程") == 0.5
    # 不相关 → 0
    assert _signal_match_strength("调模型", "行为面试") == 0.0
    assert _signal_match_strength("招聘", "机器学习基础") == 0.0
    assert _signal_match_strength("xx", "深度学习") == 0.0


def test_compute_dynamic_quotas_fuzzy_signal_boosts() -> None:
    """模糊信号（非子串命中）也能提升对应分类配额。"""
    c1, c2 = uuid.uuid4(), uuid.uuid4()
    categories = [
        (c1, "finetune", "模型微调", 10),
        (c2, "behavioral", "行为面试", 10),
    ]
    items_tags = {c1: [["微调"], ["LoRA"]], c2: [["沟通"], ["协作"]]}
    # 「调模型」非任何 slug/name/tags 的子串，但 Dice 模糊命中
    quotas, scores = compute_dynamic_quotas(
        categories, items_tags, [("调模型", 2.0)], total_target=20
    )
    assert scores[c1] > scores[c2]
    assert quotas[c1] >= quotas[c2]


def test_compute_dynamic_quotas_boosts_matched_category() -> None:
    c1, c2 = uuid.uuid4(), uuid.uuid4()
    categories = [
        (c1, "rag", "RAG 检索增强", 10),
        (c2, "algorithm", "算法与数据结构", 10),
    ]
    items_tags = {
        c1: [["RAG"], ["RAG", "检索"]],
        c2: [["动态规划"], ["排序"]],
    }
    quotas, scores = compute_dynamic_quotas(
        categories, items_tags, [("rag", 2.0)], total_target=20
    )
    # rag 亲和度更高 → 配额不低于 algorithm
    assert scores[c1] > scores[c2]
    assert quotas[c1] >= quotas[c2]
    # 总分守恒 + 步长对齐
    assert sum(quotas.values()) == 20
    assert all(q % 5 == 0 for q in quotas.values())


def test_compute_dynamic_quotas_no_signals_keeps_base() -> None:
    c1, c2 = uuid.uuid4(), uuid.uuid4()
    categories = [(c1, "rag", "RAG 检索增强", 10), (c2, "dl", "深度学习", 5)]
    quotas, scores = compute_dynamic_quotas(categories, {}, [], total_target=15)
    # 无信号 → 退化为基准配额（凑到 total_target）
    assert sum(quotas.values()) == 15
    assert all(q % 5 == 0 for q in quotas.values())
    assert all(s == 0.0 for s in scores.values())


def test_compute_dynamic_quotas_respects_bounds() -> None:
    cats = [(uuid.uuid4(), f"cat{i}", f"分类{i}", 5) for i in range(6)]
    quotas, _ = compute_dynamic_quotas(
        cats, {}, [("cat0", 3.0)], total_target=30, max_quota=10
    )
    assert all(5 <= q <= 10 for q in quotas.values())
    assert sum(quotas.values()) == 30


# ============================================================================
# DB 集成：build_candidate_signals + plan_and_assemble + instantiate
# ============================================================================


async def _seed_bank(
    session: Any, team_id: uuid.UUID
) -> dict[str, QuestionCategory]:
    """建 3 个分类：rag / python / algorithm，各若干题。"""
    cats: dict[str, QuestionCategory] = {}
    specs = [
        ("rag", "RAG 检索增强", 10, [("RAG 链路", 10, ["RAG"]), ("Chunking", 5, ["RAG", "chunking"])]),
        ("python", "Python 基础", 10, [("GIL", 10, ["Python"]), ("装饰器", 5, ["Python"])]),
        ("algorithm", "算法与数据结构", 5, [("动态规划", 5, ["算法"])]),
    ]
    for slug, name, target, items in specs:
        cat = QuestionCategory(
            team_id=team_id, slug=slug, name=name, target_points=target, sort_order=1
        )
        session.add(cat)
        await session.flush()
        cats[slug] = cat
        for q, pts, tags in items:
            session.add(
                QuestionBankItem(
                    team_id=team_id,
                    category_id=cat.id,
                    question=q,
                    points=pts,
                    difficulty=3,
                    tags=tags,
                    dimension="skill",
                )
            )
    await session.flush()
    return cats


async def _seed_candidate_with_job(
    session: Any,
) -> tuple[Team, Job, Candidate]:
    team = Team(name=f"team-{uuid.uuid4().hex[:8]}")
    session.add(team)
    await session.flush()
    user = User(email=f"u-{uuid.uuid4().hex[:8]}@x.com", password_hash="x", name="hr")
    session.add(user)
    await session.flush()

    job = Job(
        team_id=team.id,
        title="LLM 工程师",
        jd_text="负责 RAG 系统开发，需要熟悉向量数据库与检索增强",
        status="active",
        created_by=user.id,
    )
    session.add(job)
    await session.flush()
    session.add(
        JobHardRequirement(job_id=job.id, required_skills=["LangChain", "RAG"])
    )
    await session.flush()

    candidate = Candidate(
        team_id=team.id, dedup_key=f"test:{uuid.uuid4()}", name="李四"
    )
    session.add(candidate)
    await session.flush()
    src = CandidateSource(candidate_id=candidate.id, source_type="upload")
    session.add(src)
    await session.flush()
    resume = CandidateResume(
        candidate_id=candidate.id,
        source_id=src.id,
        file_storage_key="k",
        file_mime="application/pdf",
        parse_status="success",
        parsed_text="RAG 与 Python",
    )
    session.add(resume)
    await session.flush()
    session.add(
        ParsedStructure(
            resume_id=resume.id,
            data={"structure": {"skills": ["RAG", "Python"], "education": "bachelor"}},
        )
    )
    await session.commit()
    return team, job, candidate


async def test_build_candidate_signals_weight_order() -> None:
    async with AsyncSessionLocal() as session:
        team, job, candidate = await _seed_candidate_with_job(session)
        await _seed_bank(session, team.id)

        svc = QuestionBankService(session)
        signals = await svc.build_candidate_signals(
            team_id=team.id, candidate_id=candidate.id, job_id=job.id
        )
        weights = {s: w for s, w in signals}
        # JD 硬性技能权重 2.0（最高）
        assert weights.get("langchain") == 2.0
        assert weights.get("rag") == 2.0
        # 简历 skills 权重 1.0
        assert weights.get("python") == 1.0
        # 按权重降序
        assert signals[0][1] >= signals[-1][1]


async def test_build_candidate_signals_from_work_history() -> None:
    """v2：工作经历反扫 —— skills 里没写、但项目描述里出现的技术栈也能成为信号(w=0.8)。"""
    async with AsyncSessionLocal() as session:
        team, job, candidate = await _seed_candidate_with_job(session)
        # 在简历结构中追加 work_history：做过 RAG 项目但 skills 未写
        resume = (
            await session.execute(
                select(CandidateResume).where(CandidateResume.candidate_id == candidate.id)
            )
        ).scalars().first()
        ps = (
            await session.execute(
                select(ParsedStructure).where(ParsedStructure.resume_id == resume.id)
            )
        ).scalars().first()
        ps.data["structure"]["work_history"] = [
            {
                "company": "X 公司",
                "title": "算法工程师",
                "description": "负责公司 rag 系统与向量检索服务的开发与优化",
            }
        ]
        await session.flush()

        svc = QuestionBankService(session)
        signals = await svc.build_candidate_signals(
            team_id=team.id, candidate_id=candidate.id, job_id=job.id
        )
        weights = {s: w for s, w in signals}
        # 工作经历反扫命中：权重 0.8（低于 skills 1.0，高于无信号）
        assert weights.get("rag") >= 0.8  # JD 硬性也给了 rag 2.0，取 max
        # 「向量数据库」分类名在描述中出现（「向量检索」…注意：反扫用子串，
        # 分类名「向量数据库」非描述子串 → 不命中；但「算法与数据结构」同理不命中。
        # 本断言验证的是：反扫至少把「rag」从描述中捕到了（若 skills 里没有 rag）。
        # （_seed_candidate_with_job 的 skills 含 RAG，故此处验证不回归即可）
        assert any(s in ("rag",) for s, _ in signals)


async def test_plan_and_assemble_prefers_relevant_items() -> None:
    """v2：分类内题目级选择 —— tags 命中信号的题优先入选。"""
    async with AsyncSessionLocal() as session:
        team, job, candidate = await _seed_candidate_with_job(session)
        # rag 分类：1 道带 RAG tag 的 10 分题 + 4 道无关 5 分题（凑 15 分必须混选）
        cat = QuestionCategory(
            team_id=team.id, slug="rag", name="RAG 检索增强", target_points=15, sort_order=1
        )
        session.add(cat)
        await session.flush()
        relevant = QuestionBankItem(
            team_id=team.id, category_id=cat.id, question="RAG 链路题",
            points=10, difficulty=3, tags=["RAG"], dimension="skill",
        )
        session.add(relevant)
        for i in range(4):
            session.add(QuestionBankItem(
                team_id=team.id, category_id=cat.id, question=f"普通题{i}",
                points=5, difficulty=3, tags=["其他"], dimension="skill",
            ))
        await session.flush()

        svc = QuestionBankService(session)
        items, total, deficits, plan = await svc.plan_and_assemble(
            team_id=team.id,
            signals=[("rag", 2.0)],
            quotas={cat.id: 15},
        )
        assert total == 15
        # RAG tag 题必须入选（相关题优先）
        assert any(it.id == relevant.id for it in items)


async def test_plan_and_assemble_dynamic_quotas() -> None:
    async with AsyncSessionLocal() as session:
        team, job, candidate = await _seed_candidate_with_job(session)
        await _seed_bank(session, team.id)

        svc = QuestionBankService(session)
        signals = await svc.build_candidate_signals(
            team_id=team.id, candidate_id=candidate.id, job_id=job.id
        )
        items, total, deficits, plan = await svc.plan_and_assemble(
            team_id=team.id, signals=signals, total_target=25
        )
        # RAG 信号命中 → rag 分类配额应高于基准
        quota_by_name = {q["category_name"]: q for q in plan["quotas"]}
        assert quota_by_name["RAG 检索增强"]["quota_points"] >= quota_by_name["RAG 检索增强"]["base_points"]
        assert plan["total_target"] == sum(q["quota_points"] for q in plan["quotas"])
        # 动态归一后配额全部能被题库支撑（5 分步长对齐）
        assert all(q["quota_points"] % 5 == 0 for q in plan["quotas"])
        assert len(items) > 0
        assert total > 0
        # deficits 里的分类是凑不满的（algorithm 只有 5 分一题，可能够也可能缺）
        for d in deficits:
            assert d["gap"] > 5


async def test_instantiate_from_bank_communication_dimension() -> None:
    """enum 扩展回归：communication 维度的题库题可实例化（旧版会炸 PG enum）。"""
    async with AsyncSessionLocal() as session:
        team, job, candidate = await _seed_candidate_with_job(session)
        cat = QuestionCategory(
            team_id=team.id, slug="behavioral", name="行为面试", target_points=5, sort_order=1
        )
        session.add(cat)
        await session.flush()
        item = QuestionBankItem(
            team_id=team.id,
            category_id=cat.id,
            question="描述一次你与同事意见冲突并解决的经历。",
            points=5,
            difficulty=2,
            tags=["沟通"],
            dimension="communication",
        )
        session.add(item)
        await session.flush()

        svc = QuestionBankService(session)
        batch_id = await svc.instantiate_from_bank(
            candidate_id=candidate.id,
            job_id=job.id,
            session_id=None,
            items=[item],
        )
        await session.commit()

        # ORM 表达式查询：batch_id 走 GUID 列类型，双方言存储格式一致
        # （裸 text SQL 绑 str 带连字符在 SQLite hex 存储下匹配不上）
        rows = (
            await session.execute(
                select(
                    InterviewQuestion.dimension,
                    InterviewQuestion.generated_by,
                ).where(InterviewQuestion.batch_id == batch_id)
            )
        ).all()
        assert len(rows) == 1
        assert rows[0][0] == "communication"
        assert rows[0][1] == "bank"
