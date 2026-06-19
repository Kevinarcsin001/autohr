"""DedupService 单元测试（任务 15）。

覆盖：
- compute_dedup_key 算法（name/phone/email 归一化 + sha1）
- normalize_name（NFKC + 去空白 + 小写）
- last4_phone / prefix_email
- find_by_dedup_key / find_similar
- resolve_new_candidate（create_new / merge_into / flag_for_review）
- merge（sources/resumes 转移 + merged_into + confidence 比较）
- flag_for_review + decide_match
"""
from __future__ import annotations

import uuid

import pytest
from sqlalchemy import select, text

from app.core.db import AsyncSessionLocal
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.dedup import DedupMatch
from app.models.team import Team
from app.models.user import User
from app.services.dedup import DedupService

# ============================================================================
# 纯函数单测
# ============================================================================


class TestComputeDedupKey:
    def test_normal_inputs_returns_hex_string(self) -> None:
        key = DedupService.compute_dedup_key("张三", "13800138000", "zs@example.com")
        assert isinstance(key, str)
        assert len(key) == 40  # sha1 hex

    def test_same_inputs_returns_same_key(self) -> None:
        k1 = DedupService.compute_dedup_key("张三", "13800138000", "zs@example.com")
        k2 = DedupService.compute_dedup_key("张三", "13800138000", "zs@example.com")
        assert k1 == k2

    def test_different_phone_gives_different_key(self) -> None:
        k1 = DedupService.compute_dedup_key("张三", "13800138000", "zs@example.com")
        k2 = DedupService.compute_dedup_key("张三", "13900139000", "zs@example.com")
        assert k1 != k2

    def test_different_email_prefix_gives_different_key(self) -> None:
        # email prefix 取前 4 字符；不同前缀应该不同
        k1 = DedupService.compute_dedup_key("张三", "13800138000", "zsan@test.com")
        k2 = DedupService.compute_dedup_key("张三", "13800138000", "zsi@test.com")
        assert k1 != k2

    def test_all_none_raises_value_error(self) -> None:
        with pytest.raises(ValueError, match="至少需要"):
            DedupService.compute_dedup_key(None, None, None)


class TestNormalizeName:
    def test_removes_whitespace(self) -> None:
        assert DedupService.normalize_name("张 三") == "张三"
        assert DedupService.normalize_name(" 张三 ") == "张三"

    def test_full_width_space_normalized(self) -> None:
        # NFKC 把全角空格 　 转半角空格
        assert DedupService.normalize_name("张　三") == "张三"

    def test_lowercases_ascii(self) -> None:
        assert DedupService.normalize_name("John DOE") == "johndoe"

    def test_none_returns_empty(self) -> None:
        assert DedupService.normalize_name(None) == ""
        assert DedupService.normalize_name("") == ""


class TestLast4Phone:
    def test_extracts_last_4_digits(self) -> None:
        assert DedupService.last4_phone("13800138000") == "8000"
        assert DedupService.last4_phone("+86 138-0013-8000") == "8000"

    def test_short_phone_returns_all_digits(self) -> None:
        assert DedupService.last4_phone("123") == "123"

    def test_none_returns_empty(self) -> None:
        assert DedupService.last4_phone(None) == ""
        assert DedupService.last4_phone("") == ""


class TestPrefixEmail:
    def test_takes_first_n_chars_of_local_part(self) -> None:
        assert DedupService.prefix_email("zhangsan@test.com") == "zhan"
        assert DedupService.prefix_email("AB@x.com") == "ab"

    def test_none_returns_empty(self) -> None:
        assert DedupService.prefix_email(None) == ""
        assert DedupService.prefix_email("") == ""


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


async def _make_team() -> Team:
    async with AsyncSessionLocal() as session:
        team = Team(name=f"team-{uuid.uuid4().hex[:8]}")
        session.add(team)
        await session.commit()
        await session.refresh(team)
        return team


async def _make_user() -> User:
    async with AsyncSessionLocal() as session:
        user = User(
            email=f"u-{uuid.uuid4().hex[:8]}@x.com",
            password_hash="x",
            name="test-user",
        )
        session.add(user)
        await session.commit()
        await session.refresh(user)
        return user


async def _make_candidate(
    *,
    team_id: uuid.UUID,
    name: str,
    phone: str | None = None,
    email: str | None = None,
    dedup_key: str | None = None,
) -> Candidate:
    async with AsyncSessionLocal() as session:
        c = Candidate(
            team_id=team_id,
            dedup_key=dedup_key or f"test:{uuid.uuid4()}",
            name=name,
            phone=phone,
            email=email,
        )
        session.add(c)
        await session.commit()
        await session.refresh(c)
        return c


async def _add_source_and_resume(candidate_id: uuid.UUID) -> tuple[uuid.UUID, uuid.UUID]:
    async with AsyncSessionLocal() as session:
        src = CandidateSource(candidate_id=candidate_id, source_type="upload")
        session.add(src)
        await session.flush()
        resume = CandidateResume(
            candidate_id=candidate_id,
            source_id=src.id,
            file_storage_key=f"k/{uuid.uuid4()}",
            file_mime="application/pdf",
            parse_status="success",
        )
        session.add(resume)
        await session.commit()
        await session.refresh(src)
        await session.refresh(resume)
        return src.id, resume.id


async def _add_parsed_structure(
    *,
    resume_id: uuid.UUID,
    name: str | None,
    name_confidence: float,
    phone: str | None = None,
    phone_confidence: float = 0.0,
    email: str | None = None,
    email_confidence: float = 0.0,
    status: str = "extracted",
) -> None:
    async with AsyncSessionLocal() as session:
        session.add(
            ParsedStructure(
                resume_id=resume_id,
                data={
                    "structure": {
                        "name": name, "name_confidence": name_confidence,
                        "phone": phone, "phone_confidence": phone_confidence,
                        "email": email, "email_confidence": email_confidence,
                        "education": None, "education_confidence": 0.0,
                        "years_of_experience": None,
                        "years_of_experience_confidence": 0.0,
                        "skills": [], "skills_confidence": 0.0,
                        "expected_salary": None,
                        "expected_salary_confidence": 0.0,
                        "current_company": None,
                        "current_company_confidence": 0.0,
                        "work_history": [], "work_history_confidence": 0.0,
                    },
                    "status": status,
                    "attempts": 1,
                },
            )
        )
        await session.commit()


# ============================================================================
# find_by_dedup_key / find_similar
# ============================================================================


class TestFindByDedupKey:
    async def test_finds_existing_candidate_by_exact_key(self) -> None:
        team = await _make_team()
        key = DedupService.compute_dedup_key("张三", "13800138000", "zs@example.com")
        await _make_candidate(
            team_id=team.id, name="张三", phone="13800138000",
            email="zs@example.com", dedup_key=key,
        )

        async with AsyncSessionLocal() as session:
            found = await DedupService(session).find_by_dedup_key(
                team_id=team.id, dedup_key=key
            )
        assert found is not None
        assert found.name == "张三"

    async def test_returns_none_when_no_match(self) -> None:
        team = await _make_team()
        async with AsyncSessionLocal() as session:
            found = await DedupService(session).find_by_dedup_key(
                team_id=team.id, dedup_key="nonexistent-key"
            )
        assert found is None

    async def test_excludes_merged_candidates(self) -> None:
        team = await _make_team()
        key = DedupService.compute_dedup_key("张三", "13800138000", "zs@x.com")
        c = await _make_candidate(
            team_id=team.id, name="张三", phone="13800138000",
            email="zs@x.com", dedup_key=key,
        )
        other = await _make_candidate(team_id=team.id, name="other")
        # 标记 merged_into（指向真实存在的 candidate）
        async with AsyncSessionLocal() as session:
            db_c = await session.get(Candidate, c.id)
            db_c.merged_into = other.id
            await session.commit()

        async with AsyncSessionLocal() as session:
            found = await DedupService(session).find_by_dedup_key(
                team_id=team.id, dedup_key=key
            )
        assert found is None


class TestFindSimilar:
    async def test_name_plus_phone_match(self) -> None:
        team = await _make_team()
        # candidate A: 同名同手机不同 email prefix → 应被相似匹配
        await _make_candidate(
            team_id=team.id, name="张三", phone="13800138000",
            email="zs1@x.com", dedup_key="key-a",
        )
        async with AsyncSessionLocal() as session:
            service = DedupService(session)
            suspects = await service.find_similar(
                team_id=team.id, name="张三", phone="13800138000",
                email="zs2@x.com",  # 不同 prefix
            )
        assert len(suspects) == 1
        assert suspects[0].name == "张三"

    async def test_name_plus_email_prefix_match(self) -> None:
        team = await _make_team()
        await _make_candidate(
            team_id=team.id, name="张三", phone="13800138000",
            email="zsan@x.com", dedup_key="key-a",
        )
        async with AsyncSessionLocal() as session:
            suspects = await DedupService(session).find_similar(
                team_id=team.id, name="张三", phone="13999999999",
                email="zsan@y.com",  # 同 prefix
            )
        assert len(suspects) == 1

    async def test_different_name_excluded(self) -> None:
        team = await _make_team()
        await _make_candidate(
            team_id=team.id, name="李四", phone="13800138000",
            email="zs@x.com", dedup_key="key-a",
        )
        async with AsyncSessionLocal() as session:
            suspects = await DedupService(session).find_similar(
                team_id=team.id, name="张三", phone="13800138000",
                email="zs@x.com",
            )
        assert len(suspects) == 0


# ============================================================================
# resolve_new_candidate
# ============================================================================


class TestResolveNewCandidate:
    async def test_create_new_when_no_match(self) -> None:
        team = await _make_team()
        async with AsyncSessionLocal() as session:
            service = DedupService(session)
            resolution = await service.resolve_new_candidate(
                team_id=team.id, name="新候选人",
                phone="13800138000", email="new@x.com",
            )
        assert resolution.action == "create_new"
        assert resolution.primary is None
        assert resolution.dedup_key is not None

    async def test_merge_into_when_exact_key_exists(self) -> None:
        team = await _make_team()
        key = DedupService.compute_dedup_key("张三", "13800138000", "zs@x.com")
        existing = await _make_candidate(
            team_id=team.id, name="张三", phone="13800138000",
            email="zs@x.com", dedup_key=key,
        )

        async with AsyncSessionLocal() as session:
            service = DedupService(session)
            resolution = await service.resolve_new_candidate(
                team_id=team.id, name="张三",
                phone="13800138000", email="zs@x.com",
            )
        assert resolution.action == "merge_into"
        assert resolution.primary is not None
        assert resolution.primary.id == existing.id

    async def test_flag_for_review_when_multiple_similar(self) -> None:
        team = await _make_team()
        # 两条同名同手机不同 email prefix 的现有候选（dedup_key 不同）
        await _make_candidate(
            team_id=team.id, name="张三", phone="13800138000",
            email="a@x.com", dedup_key="k1",
        )
        await _make_candidate(
            team_id=team.id, name="张三", phone="13800138000",
            email="b@x.com", dedup_key="k2",
        )
        async with AsyncSessionLocal() as session:
            service = DedupService(session)
            resolution = await service.resolve_new_candidate(
                team_id=team.id, name="张三",
                phone="13800138000", email="c@x.com",
            )
        assert resolution.action == "flag_for_review"
        assert resolution.suspects is not None
        assert len(resolution.suspects) == 2

    async def test_all_missing_returns_create_new(self) -> None:
        team = await _make_team()
        async with AsyncSessionLocal() as session:
            service = DedupService(session)
            resolution = await service.resolve_new_candidate(
                team_id=team.id, name=None, phone=None, email=None,
            )
        assert resolution.action == "create_new"


# ============================================================================
# flag_for_review
# ============================================================================


class TestFlagForReview:
    async def test_writes_pending_match(self) -> None:
        team = await _make_team()
        a = await _make_candidate(team_id=team.id, name="A")
        b = await _make_candidate(team_id=team.id, name="B")

        async with AsyncSessionLocal() as session:
            match = await DedupService(session).flag_for_review(
                candidate_a=a.id,
                candidate_b=b.id,
                similarity={"phone_match": 1.0},
            )
            await session.commit()

        # 验证 candidate_a < candidate_b（规范化顺序）
        assert match.candidate_a == min(a.id, b.id)
        assert match.candidate_b == max(a.id, b.id)
        assert match.status == "pending"
        assert match.similarity == {"phone_match": 1.0}

    async def test_same_id_raises(self) -> None:
        from app.core.middleware.error_handler import ValidationError

        cid = uuid.uuid4()
        async with AsyncSessionLocal() as session:
            # B017：使用具体异常类，避免断言 blind Exception
            with pytest.raises(ValidationError):
                await DedupService(session).flag_for_review(
                    candidate_a=cid, candidate_b=cid, similarity={}
                )


# ============================================================================
# merge
# ============================================================================


class TestMerge:
    async def test_merge_moves_sources_and_resumes(self) -> None:
        team = await _make_team()
        src = await _make_candidate(team_id=team.id, name="src")
        dst = await _make_candidate(team_id=team.id, name="dst")
        src_src_id, src_resume_id = await _add_source_and_resume(src.id)
        dst_src_id, dst_resume_id = await _add_source_and_resume(dst.id)

        async with AsyncSessionLocal() as session:
            service = DedupService(session)
            sources_moved, resumes_moved, fields = await service.merge(
                src_id=src.id, dst_id=dst.id
            )
            await session.commit()

        assert sources_moved == 1
        assert resumes_moved == 1
        assert fields == []  # 无 ParsedStructure → 不更新主字段

        # 验证 src 已 merged_into
        async with AsyncSessionLocal() as session:
            src_after = await session.get(Candidate, src.id)
            assert src_after.merged_into == dst.id

            # 验证 sources 已转移
            db_src = await session.get(CandidateSource, src_src_id)
            assert db_src.candidate_id == dst.id

            # 验证 resumes 已转移
            db_resume = await session.get(CandidateResume, src_resume_id)
            assert db_resume.candidate_id == dst.id

    async def test_merge_updates_master_fields_by_confidence(self) -> None:
        """src 有高 confidence 字段 → 合并后 dst.name 被更新。"""
        team = await _make_team()
        src = await _make_candidate(team_id=team.id, name="src原始")
        dst = await _make_candidate(team_id=team.id, name="dst原始")

        _, src_resume_id = await _add_source_and_resume(src.id)
        _, dst_resume_id = await _add_source_and_resume(dst.id)

        # src ParsedStructure: name="张三真实" conf=0.95
        await _add_parsed_structure(
            resume_id=src_resume_id, name="张三真实", name_confidence=0.95
        )
        # dst ParsedStructure: name="旧名" conf=0.5
        await _add_parsed_structure(
            resume_id=dst_resume_id, name="旧名", name_confidence=0.5
        )

        async with AsyncSessionLocal() as session:
            _, _, fields = await DedupService(session).merge(
                src_id=src.id, dst_id=dst.id
            )
            await session.commit()

        assert "name" in fields
        async with AsyncSessionLocal() as session:
            dst_after = await session.get(Candidate, dst.id)
        assert dst_after.name == "张三真实"

    async def test_merge_keeps_dst_when_src_confidence_lower(self) -> None:
        """src confidence 较低 → 不更新 dst 主字段（即使 dst 的 ParsedStructure
        name 不同，candidate.name 保持原占位值）。
        """
        team = await _make_team()
        src = await _make_candidate(team_id=team.id, name="src")
        dst = await _make_candidate(team_id=team.id, name="dst")

        _, src_resume_id = await _add_source_and_resume(src.id)
        _, dst_resume_id = await _add_source_and_resume(dst.id)

        await _add_parsed_structure(
            resume_id=src_resume_id, name="低分", name_confidence=0.3
        )
        await _add_parsed_structure(
            resume_id=dst_resume_id, name="高分", name_confidence=0.9
        )

        async with AsyncSessionLocal() as session:
            _, _, fields = await DedupService(session).merge(
                src_id=src.id, dst_id=dst.id
            )
            await session.commit()

        assert "name" not in fields
        async with AsyncSessionLocal() as session:
            dst_after = await session.get(Candidate, dst.id)
        # dst.name 保持原占位（不被 src 覆盖；dst 自己 ParsedStructure
        # 的 name 也不会自动同步到 candidates 主字段）
        assert dst_after.name == "dst"

    async def test_merge_rejects_already_merged_src(self) -> None:
        team = await _make_team()
        src = await _make_candidate(team_id=team.id, name="src")
        dst = await _make_candidate(team_id=team.id, name="dst")
        # 先合并 src → dst
        async with AsyncSessionLocal() as session:
            await DedupService(session).merge(src_id=src.id, dst_id=dst.id)
            await session.commit()

        # 再次尝试合并 src → 另一个 candidate
        other = await _make_candidate(team_id=team.id, name="other")
        from app.core.middleware.error_handler import ValidationError

        async with AsyncSessionLocal() as session:
            with pytest.raises(ValidationError):
                await DedupService(session).merge(src_id=src.id, dst_id=other.id)

    async def test_merge_cross_team_rejected(self) -> None:
        team1 = await _make_team()
        team2 = await _make_team()
        src = await _make_candidate(team_id=team1.id, name="src")
        dst = await _make_candidate(team_id=team2.id, name="dst")

        from app.core.middleware.error_handler import ValidationError

        async with AsyncSessionLocal() as session:
            with pytest.raises(ValidationError):
                await DedupService(session).merge(src_id=src.id, dst_id=dst.id)


# ============================================================================
# list_pending_matches / decide_match
# ============================================================================


class TestListAndDecide:
    async def test_list_pending_returns_team_matches(self) -> None:
        team = await _make_team()
        a = await _make_candidate(team_id=team.id, name="A")
        b = await _make_candidate(team_id=team.id, name="B")

        async with AsyncSessionLocal() as session:
            await DedupService(session).flag_for_review(
                candidate_a=a.id, candidate_b=b.id, similarity={"k": 1}
            )
            await session.commit()
            matches = await DedupService(session).list_pending_matches(team_id=team.id)
        assert len(matches) == 1
        assert matches[0].status == "pending"

    async def test_decide_merged_calls_merge(self) -> None:
        team = await _make_team()
        a = await _make_candidate(team_id=team.id, name="A")
        b = await _make_candidate(team_id=team.id, name="B")
        await _add_source_and_resume(a.id)
        await _add_source_and_resume(b.id)
        actor = await _make_user()

        async with AsyncSessionLocal() as session:
            service = DedupService(session)
            match = await service.flag_for_review(
                candidate_a=a.id, candidate_b=b.id, similarity={}
            )
            await session.commit()
            updated = await service.decide_match(
                match_id=match.id, decision="merged", actor_id=actor.id
            )
            await session.commit()

        assert updated.status == "merged"
        assert updated.decided_by == actor.id

        # candidate_b（id 较大）被合并到 candidate_a
        async with AsyncSessionLocal() as session:
            a_after = await session.get(Candidate, min(a.id, b.id))
            b_after = await session.get(Candidate, max(a.id, b.id))
        assert b_after.merged_into == a_after.id
        assert a_after.merged_into is None

    async def test_decide_rejected_keeps_candidates(self) -> None:
        team = await _make_team()
        a = await _make_candidate(team_id=team.id, name="A")
        b = await _make_candidate(team_id=team.id, name="B")
        actor = await _make_user()

        async with AsyncSessionLocal() as session:
            service = DedupService(session)
            match = await service.flag_for_review(
                candidate_a=a.id, candidate_b=b.id, similarity={}
            )
            await session.commit()
            await service.decide_match(
                match_id=match.id, decision="rejected", actor_id=actor.id
            )
            await session.commit()

        async with AsyncSessionLocal() as session:
            a_after = await session.get(Candidate, a.id)
            b_after = await session.get(Candidate, b.id)
            matches = (
                await session.execute(select(DedupMatch))
            ).scalars().all()
        assert a_after.merged_into is None
        assert b_after.merged_into is None
        assert matches[0].status == "rejected"

    async def test_decide_already_decided_rejected(self) -> None:
        team = await _make_team()
        a = await _make_candidate(team_id=team.id, name="A")
        b = await _make_candidate(team_id=team.id, name="B")
        actor = await _make_user()

        from app.core.middleware.error_handler import ValidationError

        async with AsyncSessionLocal() as session:
            service = DedupService(session)
            match = await service.flag_for_review(
                candidate_a=a.id, candidate_b=b.id, similarity={}
            )
            await session.commit()
            await service.decide_match(
                match_id=match.id, decision="rejected", actor_id=actor.id
            )
            await session.commit()

            with pytest.raises(ValidationError):
                await service.decide_match(
                    match_id=match.id, decision="merged", actor_id=actor.id
                )
