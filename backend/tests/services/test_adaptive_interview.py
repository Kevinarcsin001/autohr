"""AdaptiveInterviewService 单元 + 集成测试（M1）。

覆盖：
- decide_next_action 纯函数：优秀深挖 / 一般换考点 / 薄弱换分支 / 预算与上限收口
- start：信号→分支排序→首题；幂等（已有 turns 返回当前状态）
- submit_answer + next：评分好→同分支深挖；评分差→标记薄弱并换分支
- 评分失败降级：回答保存 + rating_error，/next 自动重试评分
- 完成态：decision complete → session completed
- 跨 team 404
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import text

from app.adapters.llm import LLMError, LLMResponse
from app.adapters.llm.router import LLMRouter
from app.core.db import AsyncSessionLocal
from app.core.middleware.error_handler import NotFoundError, ValidationError
from app.models.candidate import Candidate, CandidateResume, CandidateSource, ParsedStructure
from app.models.interview import InterviewSession, InterviewTurn
from app.models.job import Job, JobHardRequirement
from app.models.question_bank import QuestionBankItem, QuestionCategory
from app.models.team import Team
from app.models.user import User
from app.schemas.adaptive import TurnRating
from app.services.adaptive_interview import (
    AdaptiveInterviewError,
    AdaptiveInterviewService,
    _branch_budget,
    _max_turns,
    decide_next_action,
    estimate_branch_ability,
    target_difficulty,
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


def _rating_json(rating: int) -> str:
    return json.dumps(
        {
            "rating": rating,
            "key_points_hit": ["要点A"],
            "key_points_missed": [],
            "strengths": ["讲清了链路"],
            "flaws": [],
            "follow_up_suggestion": "",
        },
        ensure_ascii=False,
    )


def _make_router(ratings: list[int], *, fail_times: int = 0) -> tuple[LLMRouter, Any]:
    """评分 mock：前 fail_times 次抛 LLMError（耗尽路由内置重试后触发服务层降级），
    之后按调用次序返回预设 rating。"""

    class _Mock:
        name = "mock"
        default_model = "mock-model"
        calls = 0

        async def chat(self, *, messages, response_schema, temperature, timeout, model):
            type(self).calls += 1
            if type(self).calls <= fail_times:
                raise LLMError("llm unavailable")
            rating = ratings.pop(0) if ratings else 3
            content = _rating_json(rating)
            parsed = (
                response_schema.model_validate_json(content)
                if response_schema is TurnRating
                else None
            )
            return LLMResponse(
                adapter="mock",
                content=content, model="mock-model", parsed=parsed, extra={},
            )

    return (
        LLMRouter(adapters={"mock": _Mock()}, default_primary="mock", default_fallback=None),
        _Mock,
    )


async def _seed_world(session: Any) -> tuple[Team, Job, Candidate, dict[str, QuestionCategory]]:
    """team/user/job/candidate + 3 个分支（rag/agent/python），每个多难度多题。"""
    team = Team(name=f"team-{uuid.uuid4().hex[:8]}")
    session.add(team)
    await session.flush()
    user = User(email=f"u-{uuid.uuid4().hex[:8]}@x.com", password_hash="x", name="hr")
    session.add(user)
    await session.flush()

    job = Job(
        team_id=team.id, title="LLM 工程师", jd_text="负责 RAG 与 Agent 开发",
        status="active", created_by=user.id,
    )
    session.add(job)
    await session.flush()
    session.add(
        JobHardRequirement(job_id=job.id, required_skills=["RAG", "LangChain"])
    )
    await session.flush()

    candidate = Candidate(team_id=team.id, dedup_key=f"t:{uuid.uuid4()}", name="王五")
    session.add(candidate)
    await session.flush()
    src = CandidateSource(candidate_id=candidate.id, source_type="upload")
    session.add(src)
    await session.flush()
    resume = CandidateResume(
        candidate_id=candidate.id, source_id=src.id, file_storage_key="k",
        file_mime="application/pdf", parse_status="success", parsed_text="RAG",
    )
    session.add(resume)
    await session.flush()
    session.add(
        ParsedStructure(
            resume_id=resume.id,
            data={"structure": {"skills": ["RAG", "Python"], "education": "bachelor"}},
        )
    )
    await session.flush()

    cats: dict[str, QuestionCategory] = {}
    specs = [
        ("rag", "RAG 检索增强", [("rag-d3", 3, ["RAG"]), ("rag-d4", 4, ["RAG"]), ("rag-d5", 5, ["RAG"])]),
        ("agent", "LLM Agent", [("agent-d3", 3, ["Agent"]), ("agent-d4", 4, ["Agent"])]),
        ("python", "Python 基础", [("py-d2", 2, ["Python"]), ("py-d3", 3, ["Python"])]),
    ]
    for slug, name, items in specs:
        cat = QuestionCategory(
            team_id=team.id, slug=slug, name=name, target_points=10, sort_order=1
        )
        session.add(cat)
        await session.flush()
        cats[slug] = cat
        for q, d, tags in items:
            session.add(
                QuestionBankItem(
                    team_id=team.id, category_id=cat.id, question=q,
                    points=5, difficulty=d, tags=tags, dimension="skill",
                    reference_answer=f"{q} 的参考答案",
                )
            )
    await session.flush()

    iv_session = InterviewSession(
        candidate_id=candidate.id, job_id=job.id, status="scheduled",
    )
    session.add(iv_session)
    await session.commit()
    return team, job, candidate, {**cats, "session": iv_session, "user": user}


def _svc(session: Any, router: LLMRouter | None = None) -> AdaptiveInterviewService:
    return AdaptiveInterviewService(session, router=router)


# ============================================================================
# 规则引擎（纯函数）
# ============================================================================


def test_decide_deepen_on_good_answer() -> None:
    d = decide_next_action(
        rating=5, branch_turns=1, last_difficulty=3,
        branch_has_items=True, total_turns=2,
    )
    assert d["action"] == "deepen"
    assert d["difficulty"] == 4


def test_decide_retry_on_average() -> None:
    d = decide_next_action(
        rating=3, branch_turns=1, last_difficulty=3,
        branch_has_items=True, total_turns=2,
    )
    assert d["action"] == "retry"
    assert d["difficulty"] == 3


def test_decide_switch_weak_on_bad() -> None:
    d = decide_next_action(
        rating=1, branch_turns=1, last_difficulty=3,
        branch_has_items=True, total_turns=2,
    )
    assert d["action"] == "switch"
    assert d.get("weak") is True


def test_decide_budget_exhausted_switches() -> None:
    d = decide_next_action(
        rating=5, branch_turns=_branch_budget(), last_difficulty=3,
        branch_has_items=True, total_turns=3,
    )
    assert d["action"] == "switch"


def test_decide_complete_at_max_turns() -> None:
    d = decide_next_action(
        rating=5, branch_turns=1, last_difficulty=3,
        branch_has_items=True, total_turns=_max_turns(),
    )
    assert d["action"] == "complete"


def test_decide_no_deepen_beyond_difficulty_5() -> None:
    d = decide_next_action(
        rating=5, branch_turns=1, last_difficulty=5,
        branch_has_items=True, total_turns=2,
    )
    assert d["action"] == "switch"


# ============================================================================
# M3：CAT 能力估计（纯函数）
# ============================================================================


def test_estimate_ability_prior_shrinkage() -> None:
    """单回合 5@d3：先验收缩防跳变 → θ≈0.69（目标难度 4，而非直冲 5）。"""
    theta = estimate_branch_ability([(5, 3)])
    assert theta is not None
    assert 0.6 < theta < 0.75
    assert target_difficulty(theta) == 4


def test_estimate_ability_converges_with_evidence() -> None:
    """多回合强表现 → θ 上升；难度高的好表现贡献更大。"""
    weak = estimate_branch_ability([(2, 3), (2, 3)])
    strong = estimate_branch_ability([(5, 3), (5, 5)])
    assert strong is not None and weak is not None
    assert strong > 0.7 > weak
    assert target_difficulty(strong) > target_difficulty(weak)


def test_estimate_ability_empty() -> None:
    assert estimate_branch_ability([]) is None
    assert target_difficulty(None) == 3


def test_decide_uses_theta_for_difficulty() -> None:
    d = decide_next_action(
        rating=5, branch_turns=1, last_difficulty=3,
        branch_has_items=True, total_turns=2, branch_theta=0.69,
    )
    assert d["action"] == "deepen"
    assert d["difficulty"] == 4
    assert d.get("theta") == 0.69


def test_decide_fallback_without_theta() -> None:
    d = decide_next_action(
        rating=5, branch_turns=1, last_difficulty=3,
        branch_has_items=True, total_turns=2,
    )
    assert d["action"] == "deepen"
    assert d["difficulty"] == 4
    assert "theta" not in d


# ============================================================================
# v2：内容追问 + 广度守卫 + 可配置
# ============================================================================


def test_decide_followup_on_strong_answer_with_anchor() -> None:
    """答得好且证据有锚点 → followup（优先于题库深挖）。"""
    d = decide_next_action(
        rating=5, branch_turns=1, last_difficulty=3,
        branch_has_items=True, total_turns=2,
        branch_theta=0.7,
        branch_followups=0, followup_anchor="提到 RRF 但没展开权重",
    )
    assert d["action"] == "followup"
    assert d.get("followup") is True
    assert d["difficulty"] == 5  # theta_d(0.7→4) + 1


def test_decide_followup_quota_exhausted_falls_to_deepen() -> None:
    """追问配额用尽 → 回退 deepen（题库深挖）。"""
    from app.core.config import settings

    d = decide_next_action(
        rating=5, branch_turns=2, last_difficulty=3,
        branch_has_items=True, total_turns=3,
        branch_theta=0.7,
        branch_followups=settings.ADAPTIVE_FOLLOWUP_QUOTA,
        followup_anchor="还有可追问点",
    )
    assert d["action"] == "deepen"


def test_decide_breadth_guard_forces_switch() -> None:
    """同分支连问达阈值 → 强制 switch（广度守卫，即使一直满分）。"""
    from app.core.config import settings

    d = decide_next_action(
        rating=5, branch_turns=settings.ADAPTIVE_BREADTH_FORCE_SWITCH,
        last_difficulty=3, branch_has_items=True, total_turns=4,
        branch_theta=0.8, branch_consecutive=settings.ADAPTIVE_BREADTH_FORCE_SWITCH,
        branch_followups=0, followup_anchor="x",
    )
    assert d["action"] == "switch"
    assert "广度" in d["reason"]


def test_config_values_effective() -> None:
    """配置化生效：追问配额/广度阈值/回合上限从 settings 读取。"""
    from app.core.config import settings

    assert settings.ADAPTIVE_FOLLOWUP_QUOTA == 10
    assert settings.ADAPTIVE_BREADTH_MIN >= 1
    assert _max_turns() == settings.ADAPTIVE_MAX_TURNS
    assert _branch_budget(None) == settings.ADAPTIVE_BRANCH_BUDGET
    assert _branch_budget(4.5) == (
        settings.ADAPTIVE_BRANCH_BUDGET + settings.ADAPTIVE_STRONG_EXTRA
    )


# ============================================================================
# 集成：start / answer / next
# ============================================================================


async def test_start_builds_branches_and_first_turn() -> None:
    async with AsyncSessionLocal() as session:
        team, _job, _cand, world = await _seed_world(session)
        router, _ = _make_router([])
        result = await _svc(session, router).start(
            team_id=team.id, session_id=world["session"].id, started_by=world["user"].id
        )
        assert result.mode == "adaptive"
        # RAG 信号(JD 硬性)→ rag 分支排最前且是首题分支
        assert result.branches[0].category_name == "RAG 检索增强"
        assert result.first_turn.category_name == "RAG 检索增强"
        assert result.first_turn.answer_text is None
        signals = {s.signal for s in result.signals}
        assert "rag" in signals and "langchain" in signals


async def test_good_answer_deepens_same_branch() -> None:
    async with AsyncSessionLocal() as session:
        team, _job, _cand, world = await _seed_world(session)
        router, _ = _make_router([5])  # 首题答得好
        svc = _svc(session, router)
        started = await svc.start(
            team_id=team.id, session_id=world["session"].id, started_by=world["user"].id
        )
        session_id = world["session"].id

        answered = await svc.submit_answer(
            team_id=team.id, session_id=session_id,
            turn_id=started.first_turn.id, answer_text="RAG 链路：解析→切分→向量化→检索→重排→生成…",
        )
        # v2.1 异步评分：submit 立即返回（rating=None，评分在 /next 完成）
        assert answered.turn.rating is None

        nxt = await svc.next_question(team_id=team.id, session_id=session_id)
        assert nxt.turn is not None
        # next 内补评后决策：证据带 follow_up_suggestion → 内容追问
        assert nxt.decision is not None and nxt.decision["action"] == "followup"
        assert nxt.turn.category_name == "RAG 检索增强"  # 同分支
        assert nxt.turn.seq == 2
        assert nxt.turn.question_item_id is None  # LLM 生成


async def test_state_ability_uses_cat_estimator() -> None:
    """能力画像用 CAT 估计器（难度加权+先验收缩），非简单均值。"""
    async with AsyncSessionLocal() as session:
        team, _job, _cand, world = await _seed_world(session)
        router, _ = _make_router([5])
        svc = _svc(session, router)
        session_id = world["session"].id
        started = await svc.start(
            team_id=team.id, session_id=session_id, started_by=world["user"].id
        )
        await svc.submit_answer(
            team_id=team.id, session_id=session_id,
            turn_id=started.first_turn.id, answer_text="完整讲清了链路与取舍",
        )
        await svc.next_question(team_id=team.id, session_id=session_id)  # 触发补评
        state = await svc.state(team_id=team.id, session_id=session_id)
        # 5@d3 → θ = (1.0·3+2.5)/(3+5) = 0.688（简单均值会是 1.0）
        assert 0.6 < state.ability.get("RAG 检索增强", 0) < 0.8


async def test_bad_answer_marks_weak_and_switches() -> None:
    async with AsyncSessionLocal() as session:
        team, _job, _cand, world = await _seed_world(session)
        router, _ = _make_router([1])  # 首题答得差
        svc = _svc(session, router)
        session_id = world["session"].id
        started = await svc.start(
            team_id=team.id, session_id=session_id, started_by=world["user"].id
        )

        await svc.submit_answer(
            team_id=team.id, session_id=session_id,
            turn_id=started.first_turn.id, answer_text="不知道，没接触过",
        )
        nxt = await svc.next_question(team_id=team.id, session_id=session_id)
        assert nxt.turn is not None
        assert nxt.turn.category_name != "RAG 检索增强"  # 换分支
        assert nxt.decision.get("weak") is True

        state = await svc.state(team_id=team.id, session_id=session_id)
        rag_branch = next(b for b in state.branches if b.category_name == "RAG 检索增强")
        assert rag_branch.status == "weak"


async def test_rating_failure_degrades_then_retried_by_next() -> None:
    async with AsyncSessionLocal() as session:
        team, _job, _cand, world = await _seed_world(session)
        # 前两次失败（耗尽路由内置 2 次重试）→ 服务层降级；/next 重试时成功给 4
        router, mock = _make_router([4], fail_times=2)
        svc = _svc(session, router)
        session_id = world["session"].id
        started = await svc.start(
            team_id=team.id, session_id=session_id, started_by=world["user"].id
        )

        res = await svc.submit_answer(
            team_id=team.id, session_id=session_id,
            turn_id=started.first_turn.id, answer_text="我讲一下 RAG 完整链路……",
        )
        # v2.1: submit 永不评分（异步），立即返回
        assert res.turn.answer_text is not None
        assert res.rating_error is None

        # /next 补评（前 2 次 LLM 失败耗尽路由重试后第 3 次成功）
        nxt = await svc.next_question(team_id=team.id, session_id=session_id)
        assert nxt.turn is not None
        assert nxt.decision is not None


async def test_next_is_idempotent_on_pending_turn() -> None:
    async with AsyncSessionLocal() as session:
        team, _job, _cand, world = await _seed_world(session)
        router, _ = _make_router([])
        svc = _svc(session, router)
        session_id = world["session"].id
        started = await svc.start(
            team_id=team.id, session_id=session_id, started_by=world["user"].id
        )
        nxt = await svc.next_question(team_id=team.id, session_id=session_id)
        assert nxt.turn is not None
        assert nxt.turn.id == started.first_turn.id  # 未回答 → 幂等返回当前题


async def test_cross_team_404() -> None:
    async with AsyncSessionLocal() as session:
        team, _job, _cand, world = await _seed_world(session)
        other = Team(name=f"other-{uuid.uuid4().hex[:6]}")
        session.add(other)
        await session.flush()
        router, _ = _make_router([])
        svc = _svc(session, router)
        with pytest.raises(NotFoundError):
            await svc.start(
                team_id=other.id, session_id=world["session"].id,
                started_by=world["user"].id,
            )


async def test_full_flow_completes_and_marks_session() -> None:
    """走完整场：差→换分支→好→深挖→…直至 complete，session 状态落 completed。"""
    async with AsyncSessionLocal() as session:
        team, _job, _cand, world = await _seed_world(session)
        # 序列：rag 差(换) → agent 好(深挖) → agent 好(预算尽,换) → python 好(预算尽,换) → 无 pending 分支
        router, _ = _make_router([1, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5, 5])
        svc = _svc(session, router)
        session_id = world["session"].id
        started = await svc.start(
            team_id=team.id, session_id=session_id, started_by=world["user"].id
        )
        turn_id = started.first_turn.id
        done = False
        for _ in range(15):
            await svc.submit_answer(
                team_id=team.id, session_id=session_id,
                turn_id=turn_id, answer_text="回答内容，讲得不错",
            )
            nxt = await svc.next_question(team_id=team.id, session_id=session_id)
            if nxt.done:
                done = True
                break
            assert nxt.turn is not None
            turn_id = nxt.turn.id
        assert done
        refreshed = await session.get(InterviewSession, session_id)
        assert refreshed is not None and refreshed.status == "completed"
        turns = (
            await session.execute(
                text("SELECT COUNT(*) FROM interview_turns WHERE session_id = :s"),
                {"s": str(session_id)},
            )
        ).scalar()
        assert int(turns or 0) <= _max_turns()
