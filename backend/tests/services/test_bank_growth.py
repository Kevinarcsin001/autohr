"""题库自增长闭环测试：追问题沉淀 → 审核 → 组卷隔离 + 会后报告聚合。"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select

from app.core.db import AsyncSessionLocal
from app.models.candidate import Candidate
from app.models.interview import InterviewSession, InterviewTurn
from app.models.job import Job
from app.models.question_bank import QuestionBankItem, QuestionCategory
from app.models.team import Team
from app.models.user import User
from app.services.adaptive_interview import AdaptiveInterviewService
from app.services.question_bank import QuestionBankService
from tests.db_utils import purge_database


async def _purge_db() -> None:
    async with AsyncSessionLocal() as session:
        await purge_database(session)


@pytest.fixture(autouse=True)
async def clean_db() -> None:
    await _purge_db()
    yield
    await _purge_db()


async def _seed_team_with_followup_turn() -> tuple[uuid.UUID, uuid.UUID]:
    """建 team + category + session + 一条 AI 追问回合（已评分、有证据）。"""
    async with AsyncSessionLocal() as session:
        team = Team(name="growth-test")
        session.add(team)
        await session.flush()

        cat = QuestionCategory(
            team_id=team.id, slug="rag", name="RAG 检索增强", target_points=10, sort_order=1
        )
        session.add(cat)
        await session.flush()

        candidate = Candidate(team_id=team.id, dedup_key=f"t:{uuid.uuid4()}", name="张三")
        session.add(candidate)
        await session.flush()

        admin_user = User(
            email="growth-admin@example.com",
            password_hash="x",
            name="面试官",
            role="admin",
            team_id=team.id,
        )
        session.add(admin_user)
        await session.flush()

        job = Job(
            team_id=team.id,
            title="后端工程师",
            jd_text="RAG 经验优先",
            created_by=admin_user.id,
        )
        session.add(job)
        await session.flush()

        sess = InterviewSession(
            candidate_id=candidate.id, job_id=job.id, status="in_progress"
        )
        session.add(sess)
        await session.flush()

        turn = InterviewTurn(
            session_id=sess.id,
            seq=1,
            question_item_id=None,  # AI 现场生成的追问
            question_text="你说 Chunking 用了语义分段，为什么不用固定窗口？",
            dimension="skill",
            category_id=cat.id,
            category_name=cat.name,
            answer_text="因为语义分段能保留主题完整性……",
            rating=4,
            rating_evidence={
                "strengths": ["对比了两种分段策略", "给出量化对比"],
                "misses": ["未提及 token 预算"],
                "anchor_quote": "语义分段",
                "is_followup": True,
            },
        )
        session.add(turn)
        await session.commit()
        return team.id, sess.id


async def test_promote_creates_pending_bank_item() -> None:
    team_id, session_id = await _seed_team_with_followup_turn()
    async with AsyncSessionLocal() as db:
        svc = AdaptiveInterviewService(db)
        item = await svc.promote_turn_to_bank(
            team_id=team_id, session_id=session_id,
            turn_id=(await db.execute(select(InterviewTurn))).scalars().first().id,
            user_id=None,
        )
        assert item.source == "ai_followup"
        assert item.review_status == "pending"
        assert "语义分段" in (item.reference_answer or "")
        assert "token 预算" in (item.reference_answer or "")
        await db.commit()


async def test_promote_is_idempotent() -> None:
    team_id, session_id = await _seed_team_with_followup_turn()
    async with AsyncSessionLocal() as db:
        svc = AdaptiveInterviewService(db)
        turn_id = (await db.execute(select(InterviewTurn))).scalars().first().id
        first = await svc.promote_turn_to_bank(
            team_id=team_id, session_id=session_id, turn_id=turn_id
        )
        second = await svc.promote_turn_to_bank(
            team_id=team_id, session_id=session_id, turn_id=turn_id
        )
        assert first.id == second.id
        await db.commit()


async def test_pending_items_excluded_from_assemble() -> None:
    """pending 的沉淀题不进组卷；审核 approved 后可进。"""
    team_id, _sid = await _seed_team_with_followup_turn()
    async with AsyncSessionLocal() as db:
        cat = (
            await db.execute(
                select(QuestionCategory).where(QuestionCategory.team_id == team_id)
            )
        ).scalar_one()
        svc = QuestionBankService(db)
        await svc.create_item(
            team_id=team_id,
            payload={"category_id": cat.id, "question": "正式题", "points": 10,
                     "source": "seed", "review_status": "approved"},
        )
        await svc.create_item(
            team_id=team_id,
            payload={"category_id": cat.id, "question": "待审沉淀题", "points": 10,
                     "source": "ai_followup", "review_status": "pending"},
        )
        await db.commit()

        items = await svc.list_items(
            team_id=team_id, category_id=cat.id, active_only=True, approved_only=True
        )
        assert [it.question for it in items] == ["正式题"]

        # 审核 pending → approved 后进入组卷池
        pending_item = (
            await db.execute(
                select(QuestionBankItem).where(
                    QuestionBankItem.review_status == "pending"
                )
            )
        ).scalar_one()
        pending_item.review_status = "approved"
        await db.commit()
        items_after = await svc.list_items(
            team_id=team_id, category_id=cat.id, active_only=True, approved_only=True
        )
        assert {it.question for it in items_after} == {"正式题", "待审沉淀题"}


async def test_report_aggregates_timeline_and_profile() -> None:
    team_id, session_id = await _seed_team_with_followup_turn()
    async with AsyncSessionLocal() as db:
        svc = AdaptiveInterviewService(db)
        report = await svc.build_report(team_id=team_id, session_id=session_id)
        assert report["progress"]["answered"] == 1
        assert report["progress"]["followups"] == 1
        assert report["recommendation"] is None
        assert len(report["timeline"]) == 1
        assert report["timeline"][0]["type"] == "followup"
        assert report["timeline"][0]["rating"] == 4
        # 分支画像：θ 由 (rating=4, difficulty 缺省 3) 估计
        assert len(report["profile"]) == 1
        assert report["profile"][0]["category_name"] == "RAG 检索增强"
        assert 0.0 < report["profile"][0]["theta"] < 1.0
