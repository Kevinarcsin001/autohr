"""CandidateListService（任务 23）：候选人列表聚合查询。

为前端三分组列表页提供一次性返回（Candidate + Screening + Score + Structure）。

三分组定义：
- ``passed``：有 screening_result 且 disqualified=false
- ``disqualified``：有 screening_result 且 disqualified=true
- ``pending``：无 screening_result 行

实现（两步法）：
1. **主查询**（DB 层）：Candidate LEFT JOIN Score/Screening/latest Source；
   应用 group + min/max_score 过滤 + 排序（评分维度 NULLS LAST）+ 分页
2. **结构化字段批量取**：按 candidate_id 拉 latest ParsedStructure（每行一次子查询）；
   在 Python 层合并 + 应用 education/years/skill 过滤（不破坏分页，因典型页内 50 条）

设计权衡：
- 技能 / 学历 / 年限过滤在 Python 层做（JSONB 数组操作复杂；候选数小可接受）
- 分页 + 评分排序在 DB 层做（性能关键）
- group_counts 用独立查询（不受过滤影响）
"""
from __future__ import annotations

import uuid
from typing import Any

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.core.logging import get_logger
from app.models.candidate import (
    Candidate,
    CandidateResume,
    CandidateSource,
    ParsedStructure,
)
from app.models.score import Score
from app.models.screening import ScreeningResult
from app.schemas.candidate_list import (
    CandidateListFilters,
    CandidateListItem,
)

logger = get_logger(__name__)


DEFAULT_PAGE_SIZE = 50


class CandidateListService:
    """候选人列表聚合服务。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    async def list_for_job(
        self,
        *,
        team_id: uuid.UUID,
        job_id: uuid.UUID,
        filters: CandidateListFilters,
    ) -> tuple[list[CandidateListItem], int, dict[str, int]]:
        """拉 job 内候选人列表。

        Returns:
            ``(items, total, group_counts)`` —
              - items: 当前 group + 过滤 + 排序 + 分页后的列表
              - total: 当前 group + score 过滤下的总数
              - group_counts: 三分组各自总数（不受任何过滤影响，用于 tab 显示）
        """
        # 1) 主查询：Candidate + Score + Screening + latest Source
        rows = await self._fetch_main_rows(team_id, job_id, filters)

        # 2) 批量取 latest ParsedStructure
        candidate_ids = [r.id for r in rows]
        structures = await self._fetch_latest_structures(candidate_ids)

        # 3) 合并为列表项 + Python 层过滤（education/years/skill）
        items: list[CandidateListItem] = []
        for row in rows:
            structure_data = structures.get(row.id) or {}
            inner = (
                structure_data.get("structure", {})
                if isinstance(structure_data, dict)
                else {}
            )
            item = self._row_to_item(row, inner)
            if self._matches_python_filters(item, filters):
                items.append(item)

        # 4) name 排序在 Python 层稳定化
        if filters.sort_by == "name":
            items = sorted(
                items, key=lambda it: it.name, reverse=(filters.sort_order == "desc")
            )

        # 5) total：仅 group + score 过滤（DB 层）
        total = await self._count_filtered(team_id, job_id, filters)

        # 6) group_counts：job 维度的三分组总数
        group_counts = await self._count_groups(team_id, job_id)

        return items, total, group_counts

    # ----- 主查询 -----

    async def _fetch_main_rows(
        self,
        team_id: uuid.UUID,
        job_id: uuid.UUID,
        filters: CandidateListFilters,
    ) -> list[Any]:
        """DB 层拉 Candidate + Score + Screening + latest Source。"""

        # latest source 子查询（distinct on candidate_id）
        # PostgreSQL 要求 DISTINCT ON 表达式必须匹配 ORDER BY 的初始列
        latest_source_sq = (
            select(
                CandidateSource.candidate_id.label("cid"),
                CandidateSource.id.label("source_id"),
                CandidateSource.source_type.label("source_type"),
            )
            .order_by(
                CandidateSource.candidate_id,
                CandidateSource.fetched_at.desc(),
            )
            .distinct(CandidateSource.candidate_id)
        ).subquery()

        stmt = (
            select(
                Candidate.id.label("id"),
                Candidate.name.label("name"),
                Candidate.email.label("email"),
                Candidate.phone.label("phone"),
                Candidate.created_at.label("created_at"),
                latest_source_sq.c.source_id.label("source_id"),
                latest_source_sq.c.source_type.label("source_type"),
                ScreeningResult.id.label("screening_id"),
                ScreeningResult.disqualified.label("disqualified"),
                ScreeningResult.reasons.label("screening_reasons"),
                ScreeningResult.manually_overridden.label("manually_overridden"),
                Score.id.label("score_id"),
                Score.total.label("total"),
                Score.skill.label("skill"),
                Score.experience.label("experience"),
                Score.education.label("score_education"),
                Score.stability.label("stability"),
                Score.potential.label("potential"),
                Score.model_used.label("model_used"),
            )
            .select_from(Candidate)
            .outerjoin(
                latest_source_sq,
                latest_source_sq.c.cid == Candidate.id,
            )
            .outerjoin(
                ScreeningResult,
                (ScreeningResult.candidate_id == Candidate.id)
                & (ScreeningResult.job_id == job_id),
            )
            .outerjoin(
                Score,
                (Score.candidate_id == Candidate.id) & (Score.job_id == job_id),
            )
            .where(Candidate.team_id == team_id)
        )

        # group 过滤
        if filters.group == "passed":
            stmt = stmt.where(ScreeningResult.disqualified.is_(False))
        elif filters.group == "disqualified":
            stmt = stmt.where(ScreeningResult.disqualified.is_(True))
        elif filters.group == "pending":
            stmt = stmt.where(ScreeningResult.id.is_(None))

        # source 过滤（DB 层，命中 latest source 子查询）
        if filters.source:
            stmt = stmt.where(latest_source_sq.c.source_type == filters.source)

        # score 区间
        if filters.min_score is not None:
            stmt = stmt.where(Score.total >= filters.min_score)
        if filters.max_score is not None:
            stmt = stmt.where(Score.total <= filters.max_score)

        # 排序（DB 层；name 留给 Python 稳定化）
        stmt = self._apply_db_sort(stmt, filters)

        # 分页
        offset = (filters.page - 1) * filters.page_size
        stmt = stmt.limit(filters.page_size).offset(offset)

        result = await self._db.execute(stmt)
        return list(result.all())

    def _apply_db_sort(self, stmt, filters: CandidateListFilters):
        col_map = {
            "total": Score.total,
            "skill": Score.skill,
            "experience": Score.experience,
            "education": Score.education,
            "stability": Score.stability,
            "potential": Score.potential,
        }
        if filters.sort_by in col_map:
            col = col_map[filters.sort_by]
            if filters.sort_order == "asc":
                stmt = stmt.order_by(col.asc().nulls_last())
            else:
                stmt = stmt.order_by(col.desc().nulls_last())
            # 二级稳定：name asc
            stmt = stmt.order_by(Candidate.name.asc())
        # sort_by == "name"：完全交给 Python 层
        return stmt

    # ----- 结构化字段批量取 -----

    async def _fetch_latest_structures(
        self, candidate_ids: list[uuid.UUID]
    ) -> dict[uuid.UUID, dict[str, Any]]:
        """按 candidate_id 拉 latest ParsedStructure.data。"""
        if not candidate_ids:
            return {}

        # 对每个 candidate 取最新 resume_id（按 uploaded_at desc）
        latest_resume_sq = (
            select(
                CandidateResume.candidate_id.label("cid"),
                CandidateResume.id.label("resume_id"),
            )
            .where(CandidateResume.candidate_id.in_(candidate_ids))
            .order_by(
                CandidateResume.candidate_id,
                CandidateResume.uploaded_at.desc(),
            )
            .distinct(CandidateResume.candidate_id)
        ).subquery()

        stmt = (
            select(
                latest_resume_sq.c.cid.label("cid"),
                ParsedStructure.data.label("data"),
            )
            .select_from(latest_resume_sq)
            .outerjoin(
                ParsedStructure,
                ParsedStructure.resume_id == latest_resume_sq.c.resume_id,
            )
        )
        result = await self._db.execute(stmt)
        out: dict[uuid.UUID, dict[str, Any]] = {}
        for cid, data in result.all():
            if data is not None:
                out[cid] = data
        return out

    # ----- 计数 -----

    async def _count_filtered(
        self, team_id: uuid.UUID, job_id: uuid.UUID, filters: CandidateListFilters
    ) -> int:
        """当前 group + score 过滤下的总数（与 _fetch_main_rows 同条件）。"""
        stmt = (
            select(func.count(Candidate.id))
            .select_from(Candidate)
            .outerjoin(
                ScreeningResult,
                (ScreeningResult.candidate_id == Candidate.id)
                & (ScreeningResult.job_id == job_id),
            )
            .outerjoin(
                Score,
                (Score.candidate_id == Candidate.id) & (Score.job_id == job_id),
            )
            .where(Candidate.team_id == team_id)
        )

        if filters.group == "passed":
            stmt = stmt.where(ScreeningResult.disqualified.is_(False))
        elif filters.group == "disqualified":
            stmt = stmt.where(ScreeningResult.disqualified.is_(True))
        elif filters.group == "pending":
            stmt = stmt.where(ScreeningResult.id.is_(None))

        if filters.min_score is not None:
            stmt = stmt.where(Score.total >= filters.min_score)
        if filters.max_score is not None:
            stmt = stmt.where(Score.total <= filters.max_score)

        result = await self._db.execute(stmt)
        return int(result.scalar_one())

    async def _count_groups(
        self, team_id: uuid.UUID, job_id: uuid.UUID
    ) -> dict[str, int]:
        """三分组各自总数（按 job 维度；不受其他过滤影响）。"""
        passed = await self._db.scalar(
            select(func.count(ScreeningResult.id)).where(
                ScreeningResult.job_id == job_id,
                ScreeningResult.disqualified.is_(False),
            )
        )
        disqualified = await self._db.scalar(
            select(func.count(ScreeningResult.id)).where(
                ScreeningResult.job_id == job_id,
                ScreeningResult.disqualified.is_(True),
            )
        )
        total_candidates = await self._db.scalar(
            select(func.count(Candidate.id)).where(Candidate.team_id == team_id)
        )
        screened_total = await self._db.scalar(
            select(func.count(ScreeningResult.id)).where(
                ScreeningResult.job_id == job_id
            )
        )
        pending = max(
            0, int(total_candidates or 0) - int(screened_total or 0)
        )
        return {
            "passed": int(passed or 0),
            "disqualified": int(disqualified or 0),
            "pending": pending,
        }

    # ----- 行 → item -----

    def _row_to_item(self, row, structure: dict[str, Any]) -> CandidateListItem:
        if row.screening_id is None:
            group = "pending"
        elif row.disqualified:
            group = "disqualified"
        else:
            group = "passed"

        return CandidateListItem(
            id=row.id,
            name=row.name or "",
            email=row.email,
            phone=row.phone,
            source_type=row.source_type,
            source_id=row.source_id,
            screening_id=row.screening_id,
            disqualified=row.disqualified,
            screening_reasons=row.screening_reasons,
            manually_overridden=bool(row.manually_overridden),
            score_id=row.score_id,
            total=row.total,
            skill=row.skill,
            experience=row.experience,
            education_score=row.score_education,
            stability=row.stability,
            potential=row.potential,
            model_used=row.model_used,
            education=structure.get("education"),
            years_of_experience=structure.get("years_of_experience"),
            current_company=structure.get("current_company"),
            skills=list(structure.get("skills") or []),
            group=group,
            created_at=row.created_at.isoformat() if row.created_at else "",
        )

    # ----- Python 层过滤（education / years / skill） -----

    def _matches_python_filters(
        self, item: CandidateListItem, filters: CandidateListFilters
    ) -> bool:
        if filters.education and item.education != filters.education:
            return False
        if filters.min_years is not None:
            if item.years_of_experience is None:
                return False
            if item.years_of_experience < filters.min_years:
                return False
        if filters.max_years is not None:
            if item.years_of_experience is None:
                return False
            if item.years_of_experience > filters.max_years:
                return False
        if filters.skill:
            needle = filters.skill.lower()
            if not any(
                needle in (s or "").lower() for s in item.skills
            ):
                return False
        return True


__all__ = ["CandidateListService", "DEFAULT_PAGE_SIZE"]
