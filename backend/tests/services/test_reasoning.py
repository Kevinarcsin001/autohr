"""ReasoningService 单元测试（任务 18）。

策略：
- ``FactValidator`` / ``build_disqualify_reasons`` 是纯函数 → 直接覆盖
- ``generate_recommend`` 注入 Mock adapter，覆盖：
  - 正常路径（所有 bullet 都有原文支持）
  - 部分剔除（validated=False，但保留找到支持的）
  - LLM 不可用 / schema 不合 → ReasoningError
- ``persist_disqualify`` 走 DB 集成测试
- ``_upsert_reason`` upsert 行为
"""
from __future__ import annotations

import json
import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from app.adapters.llm import LLMError, LLMSchemaError, MockAdapter
from app.adapters.llm.router import LLMRouter
from app.core.db import AsyncSessionLocal
from app.models.candidate import Candidate
from app.models.job import Job
from app.models.score import Score, ScoreReason
from app.models.team import Team
from app.models.user import User
from app.schemas.reason import RecommendReasons
from app.services.reasoning import (
    FactValidator,
    ReasoningError,
    ReasoningService,
    lookup_synonyms,
)


# ============================================================================
# FactValidator 纯函数测试
# ============================================================================


class TestFindSupport:
    def test_empty_text_returns_false(self) -> None:
        assert not FactValidator.find_support("Python 5 年经验", "")

    def test_empty_bullet_returns_false(self) -> None:
        assert not FactValidator.find_support("", "Python 简历")

    def test_direct_substring_match(self) -> None:
        text = "熟练使用 Python 与 FastAPI"
        assert FactValidator.find_support("Python 技能", text)

    def test_synonym_match(self) -> None:
        """evidence='python'，原文写 'FastAPI' → 同义词命中。"""
        text = "用 FastAPI 开发了多个项目"
        assert FactValidator.find_support(
            "技能匹配度高", text, evidence=["python"]
        )

    def test_no_match_returns_false(self) -> None:
        text = "做销售工作 5 年"
        assert not FactValidator.find_support(
            "Python 经验丰富", text, evidence=["python"]
        )

    def test_chinese_keyword_match(self) -> None:
        text = "曾在阿里巴巴担任高级工程师"
        assert FactValidator.find_support("阿里巴巴背景", text)

    def test_case_insensitive(self) -> None:
        text = "EXPERIENCED IN PYTHON"
        assert FactValidator.find_support("python skill", text)


class TestFilterSupported:
    def test_all_supported(self) -> None:
        text = "Python 与 FastAPI 经验丰富，曾在阿里巴巴工作"
        bullets = ["Python 技能", "阿里巴巴背景"]
        kept, dropped = FactValidator.filter_supported(bullets, text)
        assert kept == bullets
        assert dropped == []

    def test_partial_dropped(self) -> None:
        text = "Python 经验丰富"
        bullets = ["Python 技能", "Google 工作过"]  # Google 无支持
        kept, dropped = FactValidator.filter_supported(bullets, text)
        assert "Python 技能" in kept
        assert "Google 工作过" in dropped

    def test_with_evidence_list(self) -> None:
        text = "精通 Java 与 Spring 框架"
        bullets = ["技能匹配", "无证据条目"]
        evidence = ["java", "kubernetes"]  # kubernetes 无支持
        kept, dropped = FactValidator.filter_supported(bullets, text, evidence_list=evidence)
        assert "技能匹配" in kept
        assert "无证据条目" in dropped


class TestLookupSynonyms:
    def test_known_term(self) -> None:
        result = lookup_synonyms("python")
        assert "python" in result
        assert "fastapi" in result

    def test_unknown_term_returns_self(self) -> None:
        assert lookup_synonyms("未知技能") == ["未知技能"]

    def test_empty_returns_empty(self) -> None:
        assert lookup_synonyms("") == []
        assert lookup_synonyms("   ") == []


# ============================================================================
# build_disqualify_reasons 纯函数测试
# ============================================================================


class TestBuildDisqualifyReasons:
    def test_dedup_and_strip(self) -> None:
        result = ReasoningService.build_disqualify_reasons(
            ["学历不达标", "  学历不达标  ", "技能缺失", ""]
        )
        assert result == ["学历不达标", "技能缺失"]

    def test_max_5(self) -> None:
        result = ReasoningService.build_disqualify_reasons(
            [f"理由{i}" for i in range(10)]
        )
        assert len(result) == 5

    def test_empty_input(self) -> None:
        assert ReasoningService.build_disqualify_reasons([]) == []

    def test_specific_format_preserved(self) -> None:
        """淘汰理由必须保留 '<规则>: <值> vs <要求>' 格式。"""
        result = ReasoningService.build_disqualify_reasons(
            ["学历不达标：本科 vs 要求硕士"]
        )
        assert result == ["学历不达标：本科 vs 要求硕士"]


# ============================================================================
# generate_recommend DB 集成测试
# ============================================================================


_VALID_REASONS_JSON = json.dumps(
    {
        "bullet_points": [
            "Python 技能匹配度高",
            "FastAPI 项目经验丰富",
            "5 年工作年限达标",
        ],
        "evidence": ["python", "fastapi", "5年"],
    },
    ensure_ascii=False,
)


_PARTIAL_REASONS_JSON = json.dumps(
    {
        "bullet_points": [
            "Python 技能匹配",
            "曾在 Google 担任高管",  # Google 在原文找不到
            "FastAPI 经验丰富",
        ],
        "evidence": ["python", "google", "fastapi"],
    },
    ensure_ascii=False,
)


def _make_router(override: str | None = None) -> tuple[LLMRouter, MockAdapter]:
    mock = MockAdapter(
        response_override=override or _VALID_REASONS_JSON,
        name="mock",
        default_model="mock-model",
    )
    router = LLMRouter(
        adapters={"mock": mock},
        default_primary="mock",
        default_fallback=None,
    )
    return router, mock


async def _seed_score() -> tuple[Any, Any, Score]:
    """创建 team + job + candidate + score 行。"""
    async with AsyncSessionLocal() as session:
        team = Team(name=f"team-{uuid.uuid4().hex[:8]}")
        session.add(team)
        await session.flush()

        user = User(
            email=f"u-{uuid.uuid4().hex[:8]}@x.com",
            password_hash="x",
            name="hr",
        )
        session.add(user)
        await session.flush()

        job = Job(
            team_id=team.id,
            title="Eng",
            jd_text="招聘 Python 工程师",
            status="active",
            created_by=user.id,
        )
        session.add(job)
        await session.flush()

        candidate = Candidate(
            team_id=team.id,
            dedup_key=f"test:{uuid.uuid4()}",
            name="张三",
        )
        session.add(candidate)
        await session.flush()

        score = Score(
            job_id=job.id,
            candidate_id=candidate.id,
            total=85, skill=90, experience=80,
            education=75, stability=80, potential=85,
            model_used="mock",
        )
        session.add(score)
        await session.flush()
        await session.commit()
        return job, candidate, score


async def _purge_db() -> None:
    async with AsyncSessionLocal() as session:
        await session.execute(
            text(
                "TRUNCATE users, teams, team_invites, jobs, candidates, "
                "candidate_resumes, candidate_sources, parsed_structures, "
                "screening_results, scores, score_reasons, "
                "interview_questions, interview_feedbacks, dedup_matches, "
                "manual_overrides, llm_calls, async_jobs, audit_logs, "
                "email_configs, job_versions, job_hard_requirements "
                "RESTART IDENTITY CASCADE"
            )
        )
        await session.commit()


@pytest.fixture(autouse=True)
async def clean_db() -> None:
    await _purge_db()
    yield
    await _purge_db()


class TestGenerateRecommend:
    async def test_happy_path_writes_validated_reason(self) -> None:
        """3 条 bullet 全部能在原文找到 → validated=True。"""
        job, candidate, score = await _seed_score()
        router, _ = _make_router()

        async with AsyncSessionLocal() as session:
            # 重新 fetch score（attach 到 session）
            score_obj = await session.get(Score, score.id)
            service = ReasoningService(session, router=router)
            result = await service.generate_recommend(
                score=score_obj,
                job_title=job.title,
                jd_text=job.jd_text,
                resume_text="Python 与 FastAPI 5年经验，精通微服务",
            )
            await session.commit()

        assert result.type == "recommend"
        assert len(result.bullet_points) == 3
        assert result.validated is True

        async with AsyncSessionLocal() as session:
            rows = (await session.execute(
                select(ScoreReason).where(ScoreReason.score_id == score.id)
            )).scalars().all()
        assert len(rows) == 1
        assert rows[0].type == "recommend"
        assert rows[0].validated is True
        assert len(rows[0].bullet_points) == 3

    async def test_partial_drops_unsupported(self) -> None:
        """3 条 bullet 中 Google 无原文支持 → 剔除 + validated=False。"""
        job, candidate, score = await _seed_score()
        router, _ = _make_router(override=_PARTIAL_REASONS_JSON)

        async with AsyncSessionLocal() as session:
            score_obj = await session.get(Score, score.id)
            service = ReasoningService(session, router=router)
            result = await service.generate_recommend(
                score=score_obj,
                job_title=job.title,
                jd_text=job.jd_text,
                resume_text="Python 与 FastAPI 经验丰富",
            )
            await session.commit()

        assert result.validated is False
        assert len(result.bullet_points) == 2  # Google 被剔除
        assert all("Google" not in b for b in result.bullet_points)

    async def test_all_dropped_keeps_one_with_validated_false(self) -> None:
        """所有 bullet 都没原文支持 → 至少保留 1 条 + validated=False。"""
        bad_json = json.dumps(
            {
                "bullet_points": [
                    "Google 工作过",
                    "Stanford 博士",
                    "NASA 项目经验",
                ],
                "evidence": ["google", "stanford", "nasa"],
            },
            ensure_ascii=False,
        )
        job, candidate, score = await _seed_score()
        router, _ = _make_router(override=bad_json)

        async with AsyncSessionLocal() as session:
            score_obj = await session.get(Score, score.id)
            service = ReasoningService(session, router=router)
            result = await service.generate_recommend(
                score=score_obj,
                job_title=job.title,
                jd_text=job.jd_text,
                resume_text="无相关内容",
            )
            await session.commit()

        assert result.validated is False
        assert len(result.bullet_points) >= 1  # 至少保留 1 条提示 HR

    async def test_schema_error_raises(self) -> None:
        bad = MockAdapter(
            response_override="{not valid",
            name="mock",
            default_model="mock",
        )
        router = LLMRouter(adapters={"mock": bad}, default_primary="mock")
        job, candidate, score = await _seed_score()

        async with AsyncSessionLocal() as session:
            score_obj = await session.get(Score, score.id)
            service = ReasoningService(session, router=router)
            with pytest.raises(ReasoningError):
                await service.generate_recommend(
                    score=score_obj,
                    job_title=job.title,
                    jd_text=job.jd_text,
                    resume_text="Python",
                )

    async def test_llm_unavailable_raises(self) -> None:
        bad = MockAdapter(
            failures_before_success=100,
            failure_exception=LLMError("all down"),
            name="mock",
            default_model="mock",
        )
        router = LLMRouter(adapters={"mock": bad}, default_primary="mock")
        job, candidate, score = await _seed_score()

        async with AsyncSessionLocal() as session:
            score_obj = await session.get(Score, score.id)
            service = ReasoningService(session, router=router)
            with pytest.raises(ReasoningError):
                await service.generate_recommend(
                    score=score_obj,
                    job_title=job.title,
                    jd_text=job.jd_text,
                    resume_text="Python",
                )


# ============================================================================
# persist_disqualify 测试
# ============================================================================


class TestPersistDisqualify:
    async def test_writes_disqualify_reason(self) -> None:
        _, _, score = await _seed_score()

        async with AsyncSessionLocal() as session:
            service = ReasoningService(session)
            result = await service.persist_disqualify(
                score_id=score.id,
                filter_reasons=[
                    "学历不达标：本科 vs 要求硕士",
                    "工作年限不足：2 年 vs 要求 ≥ 5 年",
                ],
            )
            await session.commit()

        assert result.type == "disqualify"
        assert result.validated is True
        assert "学历不达标" in result.bullet_points[0]

        async with AsyncSessionLocal() as session:
            row = await session.scalar(
                select(ScoreReason).where(
                    ScoreReason.score_id == score.id,
                    ScoreReason.type == "disqualify",
                )
            )
        assert row is not None
        assert row.validated is True

    async def test_empty_filter_reasons_fallback(self) -> None:
        """filter_reasons 为空时也至少留 1 条提示。"""
        _, _, score = await _seed_score()

        async with AsyncSessionLocal() as session:
            service = ReasoningService(session)
            result = await service.persist_disqualify(
                score_id=score.id,
                filter_reasons=[],
            )
            await session.commit()

        assert len(result.bullet_points) == 1
        assert "硬性筛选" in result.bullet_points[0]


# ============================================================================
# upsert 行为测试
# ============================================================================


class TestUpsertReason:
    async def test_second_call_overwrites(self) -> None:
        """同 score_id + type 二次调用 → 更新而非插入。"""
        job, candidate, score = await _seed_score()
        router, _ = _make_router()

        async with AsyncSessionLocal() as session:
            score_obj = await session.get(Score, score.id)
            service = ReasoningService(session, router=router)
            await service.generate_recommend(
                score=score_obj,
                job_title=job.title,
                jd_text=job.jd_text,
                resume_text="Python 5年经验",
            )
            await session.commit()

            # 第二次（覆盖）
            await service.generate_recommend(
                score=score_obj,
                job_title=job.title,
                jd_text=job.jd_text,
                resume_text="Python 5年经验 FastAPI",
            )
            await session.commit()

            rows = (await session.execute(
                select(ScoreReason).where(
                    ScoreReason.score_id == score.id,
                    ScoreReason.type == "recommend",
                )
            )).scalars().all()

        assert len(rows) == 1

    async def test_recommend_and_disqualify_coexist(self) -> None:
        """同 score 下 recommend 和 disqualify 各保留 1 行。"""
        job, candidate, score = await _seed_score()
        router, _ = _make_router()

        async with AsyncSessionLocal() as session:
            score_obj = await session.get(Score, score.id)
            service = ReasoningService(session, router=router)
            await service.generate_recommend(
                score=score_obj,
                job_title=job.title,
                jd_text=job.jd_text,
                resume_text="Python",
            )
            await service.persist_disqualify(
                score_id=score.id,
                filter_reasons=["技能缺失：Rust"],
            )
            await session.commit()

            rows = (await session.execute(
                select(ScoreReason).where(ScoreReason.score_id == score.id)
            )).scalars().all()

        types = {r.type for r in rows}
        assert types == {"recommend", "disqualify"}


# ============================================================================
# list 测试
# ============================================================================


class TestList:
    async def test_list_by_score(self) -> None:
        _, _, score = await _seed_score()

        async with AsyncSessionLocal() as session:
            service = ReasoningService(session)
            await service.persist_disqualify(
                score_id=score.id,
                filter_reasons=["学历不达标"],
            )
            await session.commit()

            rows = await service.list_by_score(score_id=score.id)

        assert len(rows) == 1
        assert rows[0].type == "disqualify"

    async def test_list_by_job(self) -> None:
        job, _, score = await _seed_score()

        async with AsyncSessionLocal() as session:
            service = ReasoningService(session)
            await service.persist_disqualify(
                score_id=score.id,
                filter_reasons=["学历不达标"],
            )
            await session.commit()

            rows = await service.list_by_job(job_id=job.id)

        assert len(rows) == 1
        reason, score_obj = rows[0]
        assert reason.type == "disqualify"
        assert score_obj.job_id == job.id
