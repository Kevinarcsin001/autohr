"""ScorerService 单元测试（任务 17）。

策略：
- 注入 Mock LLM adapter（zhipu 模拟 + qwen 模拟）覆盖正常 / fallback 路径
- 排序键纯函数测试覆盖需求 9.3（同分 → skill → experience → name 字典序）
- DB 集成测试覆盖 upsert + list_by_job
"""
from __future__ import annotations

import json
import uuid
from decimal import Decimal
from typing import Any

import pytest
from sqlalchemy import select, text

from app.adapters.llm import (
    LLMError,
    LLMResponse,
    MockAdapter,
)
from app.adapters.llm.router import LLMRouter
from app.core.db import AsyncSessionLocal
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.job import Job, JobHardRequirement
from app.models.score import Score
from app.models.team import Team
from app.models.user import User
from app.schemas.candidate_structure import CandidateStructure
from app.schemas.score import ScoreDimensions
from app.services.scorer import (
    ScorerError,
    ScorerService,
    ScoringInput,
    build_scoring_snippet,
    score_sort_key,
)


# ============================================================================
# 常量
# ============================================================================


_VALID_SCORES_JSON = json.dumps(
    {
        "total": 85,
        "skill": 90,
        "experience": 80,
        "education": 75,
        "stability": 80,
        "potential": 85,
    },
    ensure_ascii=False,
)


# ============================================================================
# 公共工具
# ============================================================================


def _make_router_with_mock(
    *,
    name: str = "mock",
    override: str | None = None,
    failures_before_success: int = 0,
    failure_exc: Exception | None = None,
    default_model: str = "mock-model",
) -> tuple[LLMRouter, MockAdapter]:
    """单 adapter 路由。"""
    mock = MockAdapter(
        response_override=override,
        failures_before_success=failures_before_success,
        failure_exception=failure_exc,
        name=name,
        default_model=default_model,
    )
    router = LLMRouter(
        adapters={name: mock},
        default_primary=name,
        default_fallback=None,
    )
    return router, mock


def _make_fallback_router(
    *,
    primary_override: str | None = None,
    primary_failures: int = 0,
    fallback_override: str | None = None,
) -> tuple[LLMRouter, MockAdapter, MockAdapter]:
    """双 adapter 路由（primary=zhipu，fallback=qwen）用于 fallback 测试。"""
    primary = MockAdapter(
        response_override=primary_override,
        failures_before_success=primary_failures,
        failure_exception=LLMError("zhipu down"),
        name="zhipu",
        default_model="glm-4-plus",
    )
    fallback = MockAdapter(
        response_override=fallback_override,
        name="qwen",
        default_model="qwen-max",
    )
    router = LLMRouter(
        adapters={"zhipu": primary, "qwen": fallback},
        default_primary="zhipu",
        default_fallback="qwen",
    )
    return router, primary, fallback


def _sample_structure() -> CandidateStructure:
    return CandidateStructure(
        name="张三",
        education="master",
        years_of_experience=5,
        skills=["Python", "FastAPI"],
        current_company="ACME",
    )


def _sample_input(
    *,
    job_id: uuid.UUID | None = None,
    candidate_id: uuid.UUID | None = None,
    jd_text: str = "招聘后端工程师，需 Python 5 年经验",
) -> ScoringInput:
    return ScoringInput(
        job_id=job_id or uuid.uuid4(),
        candidate_id=candidate_id or uuid.uuid4(),
        job_title="后端工程师",
        jd_text=jd_text,
        structure=_sample_structure(),
        resume_snippet="Python 5 年经验，FastAPI 项目...",
    )


# ============================================================================
# score_sort_key 纯函数测试
# ============================================================================


class TestScoreSortKey:
    def test_total_desc(self) -> None:
        a = score_sort_key(total=80, skill=70, experience=70, name="A")
        b = score_sort_key(total=90, skill=70, experience=70, name="B")
        assert sorted([a, b]) == [b, a]  # 90 排前

    def test_tie_total_skill_desc(self) -> None:
        a = score_sort_key(total=80, skill=70, experience=70, name="A")
        b = score_sort_key(total=80, skill=90, experience=70, name="B")
        assert sorted([a, b]) == [b, a]  # skill 高排前

    def test_tie_total_skill_experience_desc(self) -> None:
        a = score_sort_key(total=80, skill=70, experience=60, name="A")
        b = score_sort_key(total=80, skill=70, experience=80, name="B")
        assert sorted([a, b]) == [b, a]  # exp 高排前

    def test_full_tie_name_asc(self) -> None:
        a = score_sort_key(total=80, skill=70, experience=70, name="张三")
        b = score_sort_key(total=80, skill=70, experience=70, name="李四")
        assert sorted([a, b]) == [a, b]  # 张三 < 李四

    def test_none_skill_treated_as_zero(self) -> None:
        a = score_sort_key(total=80, skill=None, experience=70, name="A")
        b = score_sort_key(total=80, skill=50, experience=70, name="B")
        assert sorted([a, b]) == [b, a]  # None=0 比 50 低


# ============================================================================
# build_scoring_snippet 测试
# ============================================================================


class TestBuildScoringSnippet:
    def test_none_returns_empty(self) -> None:
        assert build_scoring_snippet(None) == ""

    def test_empty_returns_empty(self) -> None:
        assert build_scoring_snippet("") == ""

    def test_short_text_returned_as_is(self) -> None:
        text = "短简历内容"
        assert build_scoring_snippet(text) == text

    def test_long_text_truncated_with_head_and_tail(self) -> None:
        long_text = "x" * 5000
        snippet = build_scoring_snippet(long_text, max_chars=1000)
        assert len(snippet) < len(long_text)
        assert "truncated" in snippet
        # 头部 + 尾部都有
        assert snippet.startswith("x")
        assert snippet.endswith("x")


# ============================================================================
# ScorerService.score 单测（不需要 DB 也能跑；用 spy 注入 db）
# ============================================================================


class TestScoreHappyPath:
    async def test_normal_scoring_writes_score(self) -> None:
        """正常路径 → 评分写库 + model_used 反映 mock 模型名。"""
        router, mock = _make_router_with_mock(override=_VALID_SCORES_JSON)

        # 用真实 DB session（pytest-asyncio fixture 提供）
        async with AsyncSessionLocal() as session:
            team, job, candidate = await _seed_minimal(session)

            service = ScorerService(session, router=router)
            result = await service.score(_sample_input(
                job_id=job.id, candidate_id=candidate.id
            ))
            await session.commit()

        assert result.dimensions.total == 85
        assert result.dimensions.skill == 90
        assert result.model_used == "mock-model"
        # mock 只调一次
        assert mock.call_count == 1

        async with AsyncSessionLocal() as session:
            score = await session.scalar(
                select(Score).where(
                    Score.job_id == job.id,
                    Score.candidate_id == candidate.id,
                )
            )
        assert score is not None
        assert score.total == 85
        assert score.skill == 90
        assert score.model_used == "mock-model"

    async def test_prompt_does_not_include_full_resume(self) -> None:
        """简历超长 → 截断后传入 prompt（不整体塞进去）。"""
        router, mock = _make_router_with_mock(override=_VALID_SCORES_JSON)

        # spy 拦截 chat 调用
        captured: list[str] = []
        original_chat = mock.chat

        async def spy_chat(*, messages, **kw):  # noqa: ANN001
            captured.append(messages[1].content)
            return await original_chat(messages=messages, **kw)

        from unittest.mock import patch

        with patch.object(mock, "chat", side_effect=spy_chat):
            async with AsyncSessionLocal() as session:
                team, job, candidate = await _seed_minimal(session)
                service = ScorerService(session, router=router)
                long_snippet = "简历正文 " * 1000  # ~6k 字符
                await service.score(ScoringInput(
                    job_id=job.id, candidate_id=candidate.id,
                    job_title="后端", jd_text="JD" * 100,
                    structure=_sample_structure(),
                    resume_snippet=long_snippet,
                ))

        assert len(captured) == 1
        prompt_text = captured[0]
        # 截断标记必须出现
        assert "truncated" in prompt_text
        # 不应包含原始超长正文（粗略检查：长 prompt < 原文长度）
        assert len(prompt_text) < len(long_snippet) + 1000

    async def test_prompt_includes_jd_and_structured_fields(self) -> None:
        """prompt 必须含 JD 与结构化字段，便于 LLM 引用具体事实。"""
        router, mock = _make_router_with_mock(override=_VALID_SCORES_JSON)

        captured: list[str] = []
        original_chat = mock.chat

        async def spy_chat(*, messages, **kw):  # noqa: ANN001
            captured.append(messages[1].content)
            return await original_chat(messages=messages, **kw)

        from unittest.mock import patch

        with patch.object(mock, "chat", side_effect=spy_chat):
            async with AsyncSessionLocal() as session:
                team, job, candidate = await _seed_minimal(session)
                service = ScorerService(session, router=router)
                await service.score(_sample_input(
                    job_id=job.id, candidate_id=candidate.id,
                    jd_text="JD 关键词：Python 微服务架构",
                ))

        prompt_text = captured[0]
        assert "JD 关键词：Python 微服务架构" in prompt_text
        assert "张三" in prompt_text  # 结构化字段 name
        assert "Python, FastAPI" in prompt_text  # skills
        assert "master" in prompt_text  # education


# ============================================================================
# Fallback 测试（需求 9.4）
# ============================================================================


class TestFallback:
    async def test_primary_failure_uses_fallback_and_records_model(self) -> None:
        """primary 全失败 → 切 fallback → model_used = fallback 的模型。"""
        router, primary, fallback = _make_fallback_router(
            primary_failures=5,  # primary 永远失败
            fallback_override=_VALID_SCORES_JSON,
        )

        async with AsyncSessionLocal() as session:
            team, job, candidate = await _seed_minimal(session)
            service = ScorerService(session, router=router)
            result = await service.score(_sample_input(
                job_id=job.id, candidate_id=candidate.id
            ))
            await session.commit()

        # model_used 必须反映实际使用模型（qwen-max）
        assert result.model_used == "qwen-max"
        assert primary.call_count >= 1
        assert fallback.call_count == 1

        async with AsyncSessionLocal() as session:
            score = await session.scalar(
                select(Score).where(
                    Score.job_id == job.id,
                    Score.candidate_id == candidate.id,
                )
            )
        assert score is not None
        assert score.model_used == "qwen-max"


# ============================================================================
# Schema 错误 / LLM 错误
# ============================================================================


class TestScorerErrors:
    async def test_schema_error_raises_scorer_error(self) -> None:
        """LLM 返回非合法 schema → ScorerError。"""
        from app.adapters.llm import LLMSchemaError

        bad = MockAdapter(
            response_override="{not valid",
            name="mock",
            default_model="mock",
        )
        router = LLMRouter(adapters={"mock": bad}, default_primary="mock")

        async with AsyncSessionLocal() as session:
            team, job, candidate = await _seed_minimal(session)
            service = ScorerService(session, router=router)
            with pytest.raises(ScorerError):
                await service.score(_sample_input(
                    job_id=job.id, candidate_id=candidate.id
                ))

    async def test_llm_all_failed_raises_scorer_error(self) -> None:
        bad = MockAdapter(
            failures_before_success=100,
            failure_exception=LLMError("all down"),
            name="mock",
            default_model="mock",
        )
        router = LLMRouter(adapters={"mock": bad}, default_primary="mock")

        async with AsyncSessionLocal() as session:
            team, job, candidate = await _seed_minimal(session)
            service = ScorerService(session, router=router)
            with pytest.raises(ScorerError):
                await service.score(_sample_input(
                    job_id=job.id, candidate_id=candidate.id
                ))


# ============================================================================
# Upsert 测试
# ============================================================================


class TestUpsertScore:
    async def test_second_call_updates_existing_row(self) -> None:
        """同 job+candidate 二次评分 → 更新而非插入。"""
        router, _ = _make_router_with_mock(override=_VALID_SCORES_JSON)

        async with AsyncSessionLocal() as session:
            team, job, candidate = await _seed_minimal(session)
            service = ScorerService(session, router=router)
            await service.score(_sample_input(
                job_id=job.id, candidate_id=candidate.id
            ))
            await session.commit()
            # 改一下 mock 返回再调一次
            router.adapters["mock"]._override = json.dumps({
                "total": 70, "skill": 70, "experience": 70,
                "education": 70, "stability": 70, "potential": 70,
            }, ensure_ascii=False)
            await service.score(_sample_input(
                job_id=job.id, candidate_id=candidate.id
            ))
            await session.commit()

            rows = (await session.execute(
                select(Score).where(
                    Score.job_id == job.id,
                    Score.candidate_id == candidate.id,
                )
            )).scalars().all()

        assert len(rows) == 1
        assert rows[0].total == 70


# ============================================================================
# list_by_job 测试（排序需求 9.3）
# ============================================================================


class TestListByJob:
    async def test_order_total_skill_experience_name(self) -> None:
        """构造 4 个候选人：total 拉开 / 同 total 拉开 skill / 同 total+skill 拉开 exp /
        全相同靠 name 字典序。"""
        async with AsyncSessionLocal() as session:
            team, job, _ = await _seed_minimal(session)

            # 直接写 Score 行（绕过 LLM）
            candidates_data = [
                # (total, skill, exp, name)
                (90, 80, 80, "AAA"),
                (90, 80, 80, "BBB"),  # 同 total+skill+exp → name 字典序
                (90, 80, 70, "CCC"),  # 同 total+skill，exp 低
                (90, 70, 80, "DDD"),  # 同 total，skill 低
                (80, 90, 90, "EEE"),  # total 低
            ]
            for total, skill, exp, name in candidates_data:
                c = Candidate(
                    team_id=team.id,
                    dedup_key=f"test:{uuid.uuid4()}",
                    name=name,
                )
                session.add(c)
                await session.flush()
                session.add(Score(
                    job_id=job.id, candidate_id=c.id,
                    total=total, skill=skill, experience=exp,
                    education=70, stability=70, potential=70,
                    model_used="mock",
                ))
            await session.commit()

            service = ScorerService(session)
            rows, total = await service.list_by_job(job_id=job.id)

        assert total == 5
        names = [name for _, name in rows]
        # 期望顺序：
        # 90+80+80+AAA → 90+80+80+BBB → 90+80+70+CCC → 90+70+80+DDD → 80+...
        assert names == ["AAA", "BBB", "CCC", "DDD", "EEE"]


# ============================================================================
# 公共 DB seed 辅助
# ============================================================================


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


async def _seed_minimal(
    session,
) -> tuple[Any, Any, Any]:
    """创建 team + user + job + candidate，返回 (team, job, candidate)。"""
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
        title="eng",
        jd_text="...",
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
    await session.commit()
    return team, job, candidate
