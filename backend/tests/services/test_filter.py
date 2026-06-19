"""FilterService 单元测试（任务 16）。

策略：
- ``FilterService.evaluate`` 是纯函数（不依赖 DB），先覆盖所有规则分支
- ``run_for_candidates`` / ``override`` 用真实 DB 集成测试
"""
from __future__ import annotations

import uuid
from typing import Any

import pytest
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.core.middleware.error_handler import ValidationError
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.job import Job, JobHardRequirement
from app.models.screening import ManualOverride, ScreeningResult
from app.models.team import Team
from app.models.user import User
from app.schemas.candidate_structure import (
    CandidateStructure,
    WorkHistoryEntry,
)
from app.services.filter import EDUCATION_RANK, FilterService

# ============================================================================
# 纯函数：evaluate
# ============================================================================


def _req(
    *,
    min_education: str | None = None,
    min_years: int | None = None,
    required_skills: list[str] | None = None,
    excluded_companies: list[str] | None = None,
) -> JobHardRequirement:
    return JobHardRequirement(
        job_id=uuid.uuid4(),
        min_education=min_education,
        min_years=min_years,
        required_skills=required_skills,
        excluded_companies=excluded_companies,
    )


def _struct(
    *,
    education: str | None = None,
    years_of_experience: int | None = None,
    skills: list[str] | None = None,
    current_company: str | None = None,
    work_history: list[dict] | None = None,
) -> CandidateStructure:
    wh = [WorkHistoryEntry(**w) for w in (work_history or [])]
    return CandidateStructure(
        education=education,
        years_of_experience=years_of_experience,
        skills=skills or [],
        current_company=current_company,
        work_history=wh,
    )


class TestEvaluateEducation:
    def test_no_requirement_passes(self) -> None:
        v = FilterService.evaluate(requirements=None, structure=_struct())
        assert not v.disqualified
        assert v.reasons == []

    def test_meets_education_passes(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(min_education="bachelor"),
            structure=_struct(education="master"),
        )
        assert not v.disqualified

    def test_below_education_disqualified(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(min_education="master"),
            structure=_struct(education="bachelor"),
        )
        assert v.disqualified
        assert any("学历不达标" in r for r in v.reasons)

    def test_missing_education_disqualified(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(min_education="bachelor"),
            structure=_struct(education=None),
        )
        assert v.disqualified
        assert any("字段缺失：学历" in r for r in v.reasons)

    def test_other_education_treated_as_missing(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(min_education="bachelor"),
            structure=_struct(education="other"),
        )
        assert v.disqualified
        assert any("字段缺失" in r for r in v.reasons)


class TestEvaluateYears:
    def test_meets_years_passes(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(min_years=3),
            structure=_struct(years_of_experience=5),
        )
        assert not v.disqualified

    def test_below_years_disqualified(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(min_years=5),
            structure=_struct(years_of_experience=2),
        )
        assert v.disqualified
        assert any("工作年限不足" in r for r in v.reasons)

    def test_missing_years_disqualified(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(min_years=3),
            structure=_struct(years_of_experience=None),
        )
        assert v.disqualified
        assert any("字段缺失：工作年限" in r for r in v.reasons)


class TestEvaluateSkills:
    def test_superset_passes(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(required_skills=["Python", "FastAPI"]),
            structure=_struct(skills=["Python", "FastAPI", "PostgreSQL"]),
        )
        assert not v.disqualified

    def test_missing_skill_disqualified(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(required_skills=["Python", "Rust"]),
            structure=_struct(skills=["Python"]),
        )
        assert v.disqualified
        assert any("技能缺失" in r for r in v.reasons)

    def test_case_insensitive(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(required_skills=["python"]),
            structure=_struct(skills=["PYTHON"]),
        )
        assert not v.disqualified

    def test_empty_skills_disqualified_as_missing(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(required_skills=["Python"]),
            structure=_struct(skills=[]),
        )
        assert v.disqualified
        assert any("字段缺失：技能" in r for r in v.reasons)


class TestEvaluateExcludedCompanies:
    def test_no_match_passes(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(excluded_companies=["BadCorp"]),
            structure=_struct(current_company="GoodCorp"),
        )
        assert not v.disqualified

    def test_current_company_hit(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(excluded_companies=["BadCorp"]),
            structure=_struct(current_company="BadCorp"),
        )
        assert v.disqualified
        assert any("竞业排除" in r for r in v.reasons)

    def test_work_history_hit(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(excluded_companies=["BadCorp"]),
            structure=_struct(
                current_company="GoodCorp",
                work_history=[{"company": "BadCorp"}],
            ),
        )
        assert v.disqualified

    def test_case_insensitive_match(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(excluded_companies=["badcorp"]),
            structure=_struct(current_company="BADCORP"),
        )
        assert v.disqualified


class TestEvaluateMultipleRules:
    def test_multiple_failures_listed(self) -> None:
        v = FilterService.evaluate(
            requirements=_req(
                min_education="master",
                min_years=5,
                required_skills=["Python"],
            ),
            structure=_struct(
                education="bachelor",
                years_of_experience=2,
                skills=[],
            ),
        )
        assert v.disqualified
        assert len(v.reasons) == 3


class TestEducationRank:
    def test_rank_order(self) -> None:
        assert EDUCATION_RANK["high_school"] < EDUCATION_RANK["bachelor"]
        assert EDUCATION_RANK["bachelor"] < EDUCATION_RANK["master"]
        assert EDUCATION_RANK["master"] < EDUCATION_RANK["phd"]


# ============================================================================
# DB 清理 + helpers
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


async def _make_team_job_and_candidate(
    *,
    min_education: str | None = None,
    min_years: int | None = None,
    required_skills: list[str] | None = None,
    excluded_companies: list[str] | None = None,
    structure_data: dict[str, Any] | None = None,
) -> tuple[uuid.UUID, uuid.UUID, uuid.UUID]:
    """创建 team + job + hard_req + candidate + source + resume + parsed_structure。"""
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
            title="eng",
            jd_text="...",
            status="active",
            created_by=user.id,
        )
        session.add(job)
        await session.flush()

        if any([min_education, min_years, required_skills, excluded_companies]):
            session.add(
                JobHardRequirement(
                    job_id=job.id,
                    min_education=min_education,
                    min_years=min_years,
                    required_skills=required_skills,
                    excluded_companies=excluded_companies,
                )
            )
            await session.flush()

        cand = Candidate(
            team_id=team.id,
            dedup_key=f"test:{uuid.uuid4()}",
            name="张三",
            email="zs@x.com",
        )
        session.add(cand)
        await session.flush()
        src = CandidateSource(candidate_id=cand.id, source_type="upload")
        session.add(src)
        await session.flush()
        resume = CandidateResume(
            candidate_id=cand.id,
            source_id=src.id,
            file_storage_key="k",
            file_mime="application/pdf",
            parse_status="success",
        )
        session.add(resume)
        await session.flush()

        if structure_data is not None:
            session.add(
                ParsedStructure(
                    resume_id=resume.id,
                    data={"structure": structure_data, "status": "extracted"},
                )
            )
        await session.commit()
        return job.id, cand.id, user.id


def _default_structure(
    *,
    education: str | None = "master",
    years_of_experience: int | None = 5,
    skills: list[str] | None = None,
    current_company: str | None = "GoodCorp",
) -> dict[str, Any]:
    return {
        "name": "张三",
        "name_confidence": 0.9,
        "phone": "13800138000",
        "phone_confidence": 0.9,
        "email": "zs@x.com",
        "email_confidence": 0.9,
        "education": education,
        "education_confidence": 0.85,
        "years_of_experience": years_of_experience,
        "years_of_experience_confidence": 0.8,
        "skills": skills if skills is not None else ["Python"],
        "skills_confidence": 0.9,
        "expected_salary": None,
        "expected_salary_confidence": 0.0,
        "current_company": current_company,
        "current_company_confidence": 0.85,
        "work_history": [],
        "work_history_confidence": 0.0,
    }


# ============================================================================
# run_for_candidates
# ============================================================================


class TestRunForCandidates:
    async def test_candidate_passing_all_rules(self) -> None:
        job_id, cand_id, _ = await _make_team_job_and_candidate(
            min_education="bachelor",
            min_years=3,
            required_skills=["Python"],
            structure_data=_default_structure(),
        )
        async with AsyncSessionLocal() as session:
            summary = await FilterService(session).run_for_candidates(
                job_id=job_id, candidate_ids=[cand_id]
            )
            await session.commit()

        assert summary == {"processed": 1, "disqualified": 0, "passed": 1}

        async with AsyncSessionLocal() as session:
            sr = await session.scalar(
                select(ScreeningResult).where(
                    ScreeningResult.job_id == job_id,
                    ScreeningResult.candidate_id == cand_id,
                )
            )
        assert sr is not None
        assert not sr.disqualified
        assert sr.reasons == []
        assert not sr.manually_overridden

    async def test_candidate_disqualified_with_reasons(self) -> None:
        job_id, cand_id, _ = await _make_team_job_and_candidate(
            min_education="master",
            min_years=10,
            required_skills=["Rust"],
            structure_data=_default_structure(
                education="bachelor",
                years_of_experience=2,
                skills=["Python"],
            ),
        )
        async with AsyncSessionLocal() as session:
            summary = await FilterService(session).run_for_candidates(
                job_id=job_id, candidate_ids=[cand_id]
            )
            await session.commit()

        assert summary["disqualified"] == 1
        async with AsyncSessionLocal() as session:
            sr = await session.scalar(
                select(ScreeningResult).where(
                    ScreeningResult.job_id == job_id,
                    ScreeningResult.candidate_id == cand_id,
                )
            )
        assert sr.disqualified
        assert len(sr.reasons) == 3  # 学历 + 年限 + 技能

    async def test_missing_structure_treats_as_field_missing(self) -> None:
        """无 ParsedStructure → 所有有要求的字段都标"字段缺失"。"""
        job_id, cand_id, _ = await _make_team_job_and_candidate(
            min_education="bachelor",
            min_years=3,
            required_skills=["Python"],
            structure_data=None,
        )
        async with AsyncSessionLocal() as session:
            await FilterService(session).run_for_candidates(
                job_id=job_id, candidate_ids=[cand_id]
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            sr = await session.scalar(
                select(ScreeningResult).where(
                    ScreeningResult.job_id == job_id,
                    ScreeningResult.candidate_id == cand_id,
                )
            )
        assert sr.disqualified
        assert all("字段缺失" in r for r in sr.reasons)

    async def test_no_hard_requirements_passes_all(self) -> None:
        """job 没设硬性条件 → 全部通过。"""
        job_id, cand_id, _ = await _make_team_job_and_candidate(
            structure_data=_default_structure()
        )
        async with AsyncSessionLocal() as session:
            summary = await FilterService(session).run_for_candidates(
                job_id=job_id, candidate_ids=[cand_id]
            )
            await session.commit()

        assert summary["passed"] == 1

    async def test_upsert_updates_existing_row(self) -> None:
        """同一 job+candidate 二次跑筛选 → 更新而非插入。"""
        job_id, cand_id, _ = await _make_team_job_and_candidate(
            min_education="bachelor",
            structure_data=_default_structure(education="bachelor"),
        )
        async with AsyncSessionLocal() as session:
            await FilterService(session).run_for_candidates(
                job_id=job_id, candidate_ids=[cand_id]
            )
            await session.commit()
            await FilterService(session).run_for_candidates(
                job_id=job_id, candidate_ids=[cand_id]
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            rows = (
                await session.execute(
                    select(ScreeningResult).where(
                        ScreeningResult.job_id == job_id,
                        ScreeningResult.candidate_id == cand_id,
                    )
                )
            ).scalars().all()
        assert len(rows) == 1

    async def test_empty_candidate_ids_returns_zero(self) -> None:
        async with AsyncSessionLocal() as session:
            summary = await FilterService(session).run_for_candidates(
                job_id=uuid.uuid4(), candidate_ids=[]
            )
        assert summary == {"processed": 0, "disqualified": 0, "passed": 0}


# ============================================================================
# override
# ============================================================================


class TestOverride:
    async def test_override_writes_audit_and_updates_result(self) -> None:
        job_id, cand_id, user_id = await _make_team_job_and_candidate(
            min_education="master",
            structure_data=_default_structure(education="bachelor"),
        )
        async with AsyncSessionLocal() as session:
            await FilterService(session).run_for_candidates(
                job_id=job_id, candidate_ids=[cand_id]
            )
            await session.commit()
            sr = await session.scalar(
                select(ScreeningResult).where(
                    ScreeningResult.job_id == job_id,
                    ScreeningResult.candidate_id == cand_id,
                )
            )
            sr_id = sr.id
            old_disqualified = sr.disqualified
            assert old_disqualified  # 应该被淘汰

        async with AsyncSessionLocal() as session:
            service = FilterService(session)
            updated_sr, override = await service.override(
                screening_result_id=sr_id,
                actor_id=user_id,
                new_disqualified=False,
                new_reasons=["HR 确认学历符合"],
                reason="候选人实际是 master，自动抽取错",
            )
            await session.commit()

        assert updated_sr.disqualified is False
        assert updated_sr.manually_overridden is True
        assert override.old_value["disqualified"] is True
        assert override.new_value["disqualified"] is False
        assert override.actor_id == user_id
        assert "候选人实际" in override.reason

    async def test_override_empty_reason_rejected(self) -> None:
        job_id, cand_id, user_id = await _make_team_job_and_candidate(
            structure_data=_default_structure()
        )
        async with AsyncSessionLocal() as session:
            await FilterService(session).run_for_candidates(
                job_id=job_id, candidate_ids=[cand_id]
            )
            await session.commit()
            sr = await session.scalar(
                select(ScreeningResult).where(
                    ScreeningResult.job_id == job_id,
                    ScreeningResult.candidate_id == cand_id,
                )
            )

        async with AsyncSessionLocal() as session:
            with pytest.raises(ValidationError):
                await FilterService(session).override(
                    screening_result_id=sr.id,
                    actor_id=user_id,
                    new_disqualified=True,
                    new_reasons=None,
                    reason="   ",
                )

    async def test_override_unknown_result_raises(self) -> None:
        async with AsyncSessionLocal() as session:
            from app.core.middleware.error_handler import NotFoundError

            with pytest.raises(NotFoundError):
                await FilterService(session).override(
                    screening_result_id=uuid.uuid4(),
                    actor_id=uuid.uuid4(),
                    new_disqualified=False,
                    new_reasons=None,
                    reason="x",
                )

    async def test_multiple_overrides_history(self) -> None:
        job_id, cand_id, user_id = await _make_team_job_and_candidate(
            structure_data=_default_structure()
        )
        async with AsyncSessionLocal() as session:
            await FilterService(session).run_for_candidates(
                job_id=job_id, candidate_ids=[cand_id]
            )
            await session.commit()
            sr = await session.scalar(
                select(ScreeningResult).where(
                    ScreeningResult.job_id == job_id,
                    ScreeningResult.candidate_id == cand_id,
                )
            )
            sr_id = sr.id

        async with AsyncSessionLocal() as session:
            service = FilterService(session)
            await service.override(
                screening_result_id=sr_id, actor_id=user_id,
                new_disqualified=True, new_reasons=["r1"], reason="first",
            )
            await session.commit()
            await service.override(
                screening_result_id=sr_id, actor_id=user_id,
                new_disqualified=False, new_reasons=["r2"], reason="second",
            )
            await session.commit()
            overrides = await service.list_overrides(screening_result_id=sr_id)

        assert len(overrides) == 2
        # 倒序：second 在前
        assert overrides[0].reason == "second"
        assert overrides[1].reason == "first"
