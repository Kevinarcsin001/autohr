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
from sqlalchemy import text

from app.core.db import AsyncSessionLocal
from app.models.candidate import Candidate, CandidateResume, CandidateSource, ParsedStructure
from app.models.job import Job, JobHardRequirement
from app.models.question_bank import QuestionBankItem, QuestionCategory
from app.models.team import Team
from app.models.user import User
from app.services.question_bank import (
    QuestionBankService,
    compute_dynamic_quotas,
    subset_sum_dp,
)

# ============================================================================
# DB 清理
# ============================================================================


async def _purge_db() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "TRUNCATE users, teams, jobs, job_hard_requirements, candidates, "
                "candidate_resumes, candidate_sources, parsed_structures, "
                "question_categories, question_bank_items, interview_questions "
                "RESTART IDENTITY CASCADE"
            )
        )
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

        rows = (
            await session.execute(
                text(
                    "SELECT dimension, generated_by FROM interview_questions "
                    "WHERE batch_id = :b"
                ),
                {"b": str(batch_id)},
            )
        ).all()
        assert len(rows) == 1
        assert rows[0][0] == "communication"
        assert rows[0][1] == "bank"
