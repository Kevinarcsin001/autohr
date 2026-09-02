"""FilterService（任务 16）：硬性条件筛选（三态裁决）。

规则（纯逻辑，不调 LLM）：
- ``min_education``：学历等级映射（``high_school < bachelor < master < phd``），
  候选人 education 等级 < 要求 → 淘汰
- ``min_years``：候选人 years_of_experience < min_years → 淘汰
- ``required_skills``：同义词等价归一后候选人 skills 必须覆盖 required_skills
  （"js" ≡ "JavaScript"；经 ``core/synonyms.py`` 严格等价展开）
- ``excluded_companies``：current_company 或 work_history 任一公司命中 → 淘汰

三态裁决（评估报告 P0-3，替代旧「缺失即淘汰」）：
- **disqualified**：有明确证据不达标（学历等级低 / 年限不足 / 同义词展开后仍缺技能 / 竞业命中）
- **needs_review**：字段缺失或无法判定（学历为 other、年限未抽到、技能全空）
  —— 抽取失败不该等于候选人不合格，进「待复核」由 HR 人工裁决
- **通过**：无任何理由

数据落点：``screening_results.disqualified``（硬淘汰）+
``screening_results.needs_review``（待复核），两者互斥且可同时为 False（通过）。

HR 改判流程：
- 改 ``screening_results.disqualified`` + ``reasons``，同时清 ``needs_review``
- 同时写 ``manual_overrides``（actor_id + old_value + new_value + reason）
- 标记 ``screening_results.manually_overridden = True``
- 不删除原 ``screening_results`` 行（保留作为历史）

约束：
- 不调用任何 LLM
- 学历等级映射 ``high_school < bachelor < master < phd``
- HR 改判必须记 actor/old/new/reason
"""
from __future__ import annotations

import uuid
from dataclasses import dataclass, field
from typing import Any

from sqlalchemy import select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.core.middleware.error_handler import NotFoundError, ValidationError
from app.core.synonyms import skills_satisfied
from app.models.candidate import Candidate, CandidateResume, ParsedStructure
from app.models.job import JobHardRequirement
from app.models.screening import ManualOverride, ScreeningResult
from app.schemas.candidate_structure import CandidateStructure

logger = get_logger(__name__)


# ============================================================================
# 常量：学历等级映射
# ============================================================================


EDUCATION_RANK: dict[str, int] = {
    "high_school": 1,
    "bachelor": 2,
    "master": 3,
    "phd": 4,
}
"""学历等级映射：``high_school(1) < bachelor(2) < master(3) < phd(4)``。"""

# 'other' 视为 0（无法判定 → 字段缺失逻辑处理）


# ============================================================================
# 数据类
# ============================================================================


@dataclass
class FilterVerdict:
    """单候选人 × 单 job 的筛选裁决（三态：淘汰 / 待复核 / 通过）。

    - ``disqualified=True``：有明确证据不达标 → 淘汰池
    - ``disqualified=False & needs_review=True``：字段缺失/无法判定 → 待复核池
    - 两者均 False：通过
    ``reasons`` 合并两类原因（硬伤在前），仅作展示用途。
    """

    disqualified: bool
    needs_review: bool = False
    hard_reasons: list[str] = field(default_factory=list)
    missing_reasons: list[str] = field(default_factory=list)

    @property
    def reasons(self) -> list[str]:
        """合并原因列表（展示用）：硬伤在前、缺失在后。"""
        return self.hard_reasons + self.missing_reasons


# ============================================================================
# FilterService
# ============================================================================


class FilterService:
    """硬性条件筛选（淘汰制）。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ----- 纯逻辑：逐条规则（便于单测，不依赖 DB） -----

    @staticmethod
    def evaluate(
        *,
        requirements: JobHardRequirement | None,
        structure: CandidateStructure | None,
    ) -> FilterVerdict:
        """逐条比对硬性条件 → 三态裁决（不写库）。

        Args:
            requirements: ``JobHardRequirement`` 行；为 None 或全空 → 不约束 → 通过
            structure: 候选人 ``CandidateStructure``；为 None → 字段全缺失

        Returns:
            ``FilterVerdict``（disqualified / needs_review / 通过 三态）
        """
        hard: list[str] = []
        missing: list[str] = []

        if requirements is None:
            return FilterVerdict(disqualified=False)

        # 1. 学历：等级低 = 硬伤；缺失/other/未知 = 待复核
        if requirements.min_education is not None:
            req_rank = EDUCATION_RANK.get(requirements.min_education, 99)
            cand_edu = structure.education if structure else None
            if not cand_edu or cand_edu == "other" or cand_edu not in EDUCATION_RANK:
                missing.append(
                    f"字段缺失：学历（要求 {requirements.min_education}）——待人工复核"
                )
            elif EDUCATION_RANK[cand_edu] < req_rank:
                hard.append(
                    f"学历不达标：{cand_edu} vs 要求 {requirements.min_education}"
                )

        # 2. 工作年限：不足 = 硬伤；缺失 = 待复核
        if requirements.min_years is not None:
            cand_years = structure.years_of_experience if structure else None
            if cand_years is None:
                missing.append(
                    f"字段缺失：工作年限（要求 ≥ {requirements.min_years} 年）——待人工复核"
                )
            elif cand_years < requirements.min_years:
                hard.append(
                    f"工作年限不足：{cand_years} 年 vs 要求 ≥ {requirements.min_years} 年"
                )

        # 3. 必备技能：同义词等价展开后仍缺 = 硬伤；技能全空（疑似抽取失败）= 待复核
        if requirements.required_skills:
            req_set = {s.strip().lower() for s in requirements.required_skills if s and s.strip()}
            cand_skills = structure.skills if structure else []
            cand_set = {s.strip().lower() for s in cand_skills if s and s.strip()}
            if not cand_set:
                missing.append(
                    f"字段缺失：技能（要求包含 {sorted(req_set)}）——待人工复核"
                )
            else:
                unsatisfied = [
                    r for r in sorted(req_set) if not skills_satisfied(r, cand_set)
                ]
                if unsatisfied:
                    hard.append(f"技能缺失：缺少 {unsatisfied}")

        # 4. 竞业排除（current_company + work_history 任一命中）
        if requirements.excluded_companies:
            excluded_set = {
                c.strip().lower()
                for c in requirements.excluded_companies
                if c and c.strip()
            }
            cand_companies: set[str] = set()
            if structure:
                if structure.current_company:
                    cand_companies.add(structure.current_company.strip().lower())
                for wh in structure.work_history:
                    if wh.company:
                        cand_companies.add(wh.company.strip().lower())
            hits = cand_companies & excluded_set
            if hits:
                hard.append(f"竞业排除：命中 {sorted(hits)}")

        disqualified = bool(hard)
        needs_review = not disqualified and bool(missing)
        return FilterVerdict(
            disqualified=disqualified,
            needs_review=needs_review,
            hard_reasons=hard,
            missing_reasons=missing,
        )

    # ----- 主入口：批量跑筛选并写库 -----

    async def run_for_candidates(
        self,
        *,
        job_id: uuid.UUID,
        candidate_ids: list[uuid.UUID],
    ) -> dict[str, int]:
        """对指定候选人们跑硬性筛选，写 / 更新 ``screening_results``。

        Returns:
            ``{"processed": N, "disqualified": M, "passed": N-M}``
        """
        if not candidate_ids:
            return {"processed": 0, "disqualified": 0, "passed": 0}

        # 1. 取 job 的硬性条件
        req = await self._db.scalar(
            select(JobHardRequirement).where(
                JobHardRequirement.job_id == job_id
            )
        )
        # req 可能为 None（job 无硬性条件约束）→ 全部通过

        # 2. 取每个候选人的最新 ParsedStructure
        structures_by_cand = await self._fetch_latest_structures(candidate_ids)

        # 3. 逐条评估 + upsert screening_results
        disqualified_count = 0
        for cid in candidate_ids:
            structure = structures_by_cand.get(cid)
            cand_struct_obj: CandidateStructure | None = None
            if structure is not None:
                try:
                    cand_struct_obj = CandidateStructure.model_validate(
                        structure.data.get("structure", {})
                    )
                except Exception:  # noqa: BLE001
                    cand_struct_obj = None

            verdict = self.evaluate(requirements=req, structure=cand_struct_obj)

            # HR 改判保护：人工结论优先于机器重跑（需求 8.4 人工结论是终局口径）；
            # 否则 pipeline 重跑会静默冲掉 HR 改判，manually_overridden 标记与结论脱节。
            # 计数按人工结论走，保证 passed = processed - disqualified 口径依然成立
            existing = await self._db.scalar(
                select(ScreeningResult).where(
                    ScreeningResult.job_id == job_id,
                    ScreeningResult.candidate_id == cid,
                )
            )
            if existing is not None and existing.manually_overridden:
                if existing.disqualified:
                    disqualified_count += 1
                continue

            await self._upsert_result(
                job_id=job_id,
                candidate_id=cid,
                disqualified=verdict.disqualified,
                needs_review=verdict.needs_review,
                reasons=verdict.reasons,
            )
            if verdict.disqualified:
                disqualified_count += 1

        await self._db.flush()

        processed = len(candidate_ids)
        logger.info(
            "screening_completed",
            job_id=str(job_id),
            processed=processed,
            disqualified=disqualified_count,
            passed=processed - disqualified_count,
        )
        return {
            "processed": processed,
            "disqualified": disqualified_count,
            "passed": processed - disqualified_count,
        }

    async def _fetch_latest_structures(
        self, candidate_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, ParsedStructure]:
        """取每个 candidate 的最新 ParsedStructure（按 uploaded_at 倒序）。"""
        if not candidate_ids:
            return {}

        # 子查询：每个 candidate 取最新 resume_id
        # 直接 JOIN + DISTINCT ON 简化
        stmt = (
            select(ParsedStructure, CandidateResume.candidate_id)
            .join(
                CandidateResume,
                CandidateResume.id == ParsedStructure.resume_id,
            )
            .where(CandidateResume.candidate_id.in_(candidate_ids))
            .order_by(
                CandidateResume.candidate_id,
                CandidateResume.uploaded_at.desc(),
            )
        )
        result = await self._db.execute(stmt)
        out: dict[uuid.UUID, ParsedStructure] = {}
        for ps, cand_id in result.all():
            if cand_id not in out:  # 取第一条（最新）
                out[cand_id] = ps
        return out

    async def _upsert_result(
        self,
        *,
        job_id: uuid.UUID,
        candidate_id: uuid.UUID,
        disqualified: bool,
        needs_review: bool,
        reasons: list[str],
    ) -> ScreeningResult:
        """upsert screening_results（UNIQUE job_id + candidate_id）。

        已存在则更新 disqualified + needs_review + reasons；保留 manually_overridden 不变。
        """
        existing = await self._db.scalar(
            select(ScreeningResult).where(
                ScreeningResult.job_id == job_id,
                ScreeningResult.candidate_id == candidate_id,
            )
        )
        if existing is not None:
            existing.disqualified = disqualified
            existing.needs_review = needs_review
            existing.reasons = reasons
            return existing

        new = ScreeningResult(
            job_id=job_id,
            candidate_id=candidate_id,
            disqualified=disqualified,
            needs_review=needs_review,
            reasons=reasons,
            manually_overridden=False,
        )
        self._db.add(new)
        await self._db.flush()
        return new

    # ----- 列表 -----

    async def list_results(
        self,
        *,
        job_id: uuid.UUID,
        only_disqualified: bool | None = None,
        only_needs_review: bool | None = None,
        limit: int = 100,
        offset: int = 0,
    ) -> tuple[list[tuple[ScreeningResult, str | None]], int]:
        """列出 job 的筛选结果（带候选人姓名）。

        Args:
            only_disqualified: True=仅淘汰；False=仅非淘汰；None=不过滤。
            only_needs_review: True=仅待复核；None=不过滤。
                与 ``only_disqualified`` 同时为 True 时取交集（实际为空集，
                两状态互斥）。

        Returns:
            ``([(result, candidate_name), ...], total)``
        """
        stmt = (
            select(ScreeningResult, Candidate.name)
            .join(Candidate, Candidate.id == ScreeningResult.candidate_id)
            .where(ScreeningResult.job_id == job_id)
        )
        if only_disqualified is not None:
            stmt = stmt.where(
                ScreeningResult.disqualified == only_disqualified
            )
        if only_needs_review:
            stmt = stmt.where(ScreeningResult.needs_review.is_(True))
        stmt = stmt.order_by(ScreeningResult.created_at.desc())

        # total
        count_stmt = (
            select(ScreeningResult)
            .where(ScreeningResult.job_id == job_id)
        )
        if only_disqualified is not None:
            count_stmt = count_stmt.where(
                ScreeningResult.disqualified == only_disqualified
            )
        if only_needs_review:
            count_stmt = count_stmt.where(ScreeningResult.needs_review.is_(True))
        total = len((await self._db.execute(count_stmt)).scalars().all())

        stmt = stmt.limit(limit).offset(offset)
        result = await self._db.execute(stmt)
        rows = [(r, name) for r, name in result.all()]
        return rows, total

    # ----- HR 改判 -----

    async def override(
        self,
        *,
        screening_result_id: uuid.UUID,
        actor_id: uuid.UUID,
        new_disqualified: bool,
        new_reasons: list[str] | None,
        reason: str,
    ) -> tuple[ScreeningResult, ManualOverride]:
        """HR 改判 disqualified。

        - 保存原值到 ``ManualOverride.old_value``
        - 更新 ``ScreeningResult.disqualified`` + ``reasons`` + ``manually_overridden=True``
        - 写 ``ManualOverride`` 审计行
        """
        if not reason.strip():
            raise ValidationError("改判必须填写 reason")

        sr = await self._db.get(ScreeningResult, screening_result_id)
        if sr is None:
            raise NotFoundError(
                f"screening_result {screening_result_id} 不存在",
                resource="screening_result",
            )

        old_value: dict[str, Any] = {
            "disqualified": sr.disqualified,
            "needs_review": sr.needs_review,
            "reasons": sr.reasons,
        }
        new_value: dict[str, Any] = {
            "disqualified": new_disqualified,
            "needs_review": False,
            "reasons": new_reasons,
        }

        sr.disqualified = new_disqualified
        sr.reasons = new_reasons
        # HR 已人工裁决 → 待复核状态消除
        sr.needs_review = False
        sr.manually_overridden = True

        override = ManualOverride(
            screening_result_id=sr.id,
            actor_id=actor_id,
            old_value=old_value,
            new_value=new_value,
            reason=reason,
        )
        self._db.add(override)
        await self._db.flush()

        logger.info(
            "screening_overridden",
            screening_result_id=str(sr.id),
            actor_id=str(actor_id),
            old_disqualified=old_value["disqualified"],
            new_disqualified=new_disqualified,
        )
        return sr, override

    async def list_overrides(
        self, *, screening_result_id: uuid.UUID
    ) -> list[ManualOverride]:
        """列出某个 screening_result 的所有改判历史。"""
        stmt = (
            select(ManualOverride)
            .where(ManualOverride.screening_result_id == screening_result_id)
            .order_by(ManualOverride.created_at.desc())
        )
        result = await self._db.execute(stmt)
        return list(result.scalars().all())


__all__ = ["FilterService", "FilterVerdict", "EDUCATION_RANK"]
