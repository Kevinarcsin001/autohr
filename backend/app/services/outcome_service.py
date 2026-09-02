"""OutcomeService：最终用人结果录入 + 评分校准报告 + 招聘漏斗统计。

闭环（评估报告 P1-5 / P1-4 最小闭环）：
- ``upsert_outcome``：HR 录入 ground truth（hired / rejected / ...）
- ``calibration_report``：分数段 × 结果分布——分数越高 hire_rate 越高，
  说明评分模型有区分力；否则 P0-2 的权重配置就需要重调
- ``funnel_stats``：筛选池 → 通过 → 评分 → 面试 → 录用 转化 + 渠道质量

口径约定：
- 漏斗「筛选池」= 与 job 存在 screening_result 或 score 的 distinct 候选人
- 「录用」= outcome.final_status ∈ {hired, probation_passed}
"""
from __future__ import annotations

import uuid
from datetime import datetime, timezone

from sqlalchemy import func, select
from sqlalchemy.ext.asyncio import AsyncSession

from app.models.candidate import Candidate, CandidateSource
from app.models.interview import InterviewSession
from app.models.job import Job
from app.models.outcome import CandidateJobOutcome
from app.models.score import Score
from app.models.screening import ScreeningResult
from app.schemas.outcome import (
    CalibrationBucket,
    CalibrationReport,
    ChannelQuality,
    FunnelStats,
)

_SCORE_BUCKETS: list[tuple[int, int]] = [
    (0, 59),
    (60, 74),
    (75, 89),
    (90, 100),
]
"""校准分数段（左闭右闭）。"""

_HIRED_STATUSES = ("hired", "probation_passed")


class OutcomeError(Exception):
    """Outcome 顶层错误。"""


class OutcomeService:
    """最终用人结果 + 校准 + 漏斗。"""

    def __init__(self, db: AsyncSession) -> None:
        self._db = db

    # ----- 结果录入 -----

    async def upsert_outcome(
        self,
        *,
        job_id: uuid.UUID,
        candidate_id: uuid.UUID,
        final_status: str,
        decided_by: uuid.UUID | None,
        note: str | None = None,
    ) -> CandidateJobOutcome:
        """upsert 结果行（UNIQUE job_id + candidate_id）；decided_at 刷新为当前。"""
        existing = await self._db.scalar(
            select(CandidateJobOutcome).where(
                CandidateJobOutcome.job_id == job_id,
                CandidateJobOutcome.candidate_id == candidate_id,
            )
        )
        now = datetime.now(timezone.utc)
        if existing is not None:
            existing.final_status = final_status
            existing.decided_by = decided_by
            existing.decided_at = now
            existing.note = note
            return existing

        row = CandidateJobOutcome(
            job_id=job_id,
            candidate_id=candidate_id,
            final_status=final_status,
            decided_by=decided_by,
            decided_at=now,
            note=note,
        )
        self._db.add(row)
        await self._db.flush()
        return row

    async def get_outcome(
        self, *, job_id: uuid.UUID, candidate_id: uuid.UUID
    ) -> CandidateJobOutcome | None:
        return await self._db.scalar(
            select(CandidateJobOutcome).where(
                CandidateJobOutcome.job_id == job_id,
                CandidateJobOutcome.candidate_id == candidate_id,
            )
        )

    # ----- 评分校准报告 -----

    async def calibration_report(
        self,
        *,
        team_id: uuid.UUID,
        job_id: uuid.UUID | None = None,
    ) -> CalibrationReport:
        """分数段 × 结果分布。

        校准逻辑：``hire_rate`` 应随分数段单调递增；若不单调，说明
        评分权重（SCORE_WEIGHT_*）与实际用人标准偏离，需要重调。
        """
        stmt = (
            select(Score.total, CandidateJobOutcome.final_status)
            .select_from(Score)
            .join(
                CandidateJobOutcome,
                (CandidateJobOutcome.candidate_id == Score.candidate_id)
                & (CandidateJobOutcome.job_id == Score.job_id),
            )
            .join(Job, Job.id == Score.job_id)
            .where(Job.team_id == team_id)
        )
        if job_id is not None:
            stmt = stmt.where(Score.job_id == job_id)

        rows = (await self._db.execute(stmt)).all()
        buckets = [
            CalibrationBucket(score_min=lo, score_max=hi) for lo, hi in _SCORE_BUCKETS
        ]
        for total, status in rows:
            if total is None:
                continue
            for bucket in buckets:
                if bucket.score_min <= total <= bucket.score_max:
                    bucket.total += 1
                    if status in _HIRED_STATUSES:
                        bucket.hired += 1
                    elif status == "rejected":
                        bucket.rejected += 1
                    else:
                        bucket.other += 1
                    break

        return CalibrationReport(
            job_id=job_id,
            buckets=buckets,
            total_with_outcome=len(rows),
        )

    # ----- 招聘漏斗 -----

    async def funnel_stats(
        self,
        *,
        team_id: uuid.UUID,
        job_id: uuid.UUID | None = None,
    ) -> FunnelStats:
        """漏斗 + 渠道质量（口径见模块 docstring）。"""
        # 筛选池：与 team 的 job 有 screening_result 或 score 的 distinct 候选人
        base_cids = (
            select(ScreeningResult.candidate_id)
            .join(Job, Job.id == ScreeningResult.job_id)
            .where(Job.team_id == team_id)
        )
        scored_cids = (
            select(Score.candidate_id)
            .join(Job, Job.id == Score.job_id)
            .where(Job.team_id == team_id)
        )
        if job_id is not None:
            base_cids = base_cids.where(ScreeningResult.job_id == job_id)
            scored_cids = scored_cids.where(Score.job_id == job_id)

        pool_ids = {
            row[0] for row in (await self._db.execute(base_cids.union(scored_cids)))
        }
        total_pool = len(pool_ids)

        # 筛选三态计数
        sr_stmt = (
            select(
                ScreeningResult.disqualified,
                ScreeningResult.needs_review,
                func.count(ScreeningResult.id),
            )
            .join(Job, Job.id == ScreeningResult.job_id)
            .where(Job.team_id == team_id)
            .group_by(ScreeningResult.disqualified, ScreeningResult.needs_review)
        )
        if job_id is not None:
            sr_stmt = sr_stmt.where(ScreeningResult.job_id == job_id)

        passed = needs_review = disqualified = 0
        for dis, nr, cnt in (await self._db.execute(sr_stmt)).all():
            if dis:
                disqualified += int(cnt)
            elif nr:
                needs_review += int(cnt)
            else:
                passed += int(cnt)

        # 评分 / 面试 / 录用计数
        scored_q = (
            select(func.count(func.distinct(Score.candidate_id)))
            .select_from(Score)
            .join(Job, Job.id == Score.job_id)
            .where(Job.team_id == team_id)
        )
        interview_q = (
            select(func.count(func.distinct(InterviewSession.candidate_id)))
            .select_from(InterviewSession)
            .join(Job, Job.id == InterviewSession.job_id)
            .where(Job.team_id == team_id)
        )
        hired_q = (
            select(func.count(func.distinct(CandidateJobOutcome.candidate_id)))
            .select_from(CandidateJobOutcome)
            .join(Job, Job.id == CandidateJobOutcome.job_id)
            .where(
                Job.team_id == team_id,
                CandidateJobOutcome.final_status.in_(_HIRED_STATUSES),
            )
        )
        if job_id is not None:
            scored_q = scored_q.where(Score.job_id == job_id)
            interview_q = interview_q.where(InterviewSession.job_id == job_id)
            hired_q = hired_q.where(CandidateJobOutcome.job_id == job_id)

        scored = int((await self._db.execute(scored_q)).scalar_one() or 0)
        interviewed = int((await self._db.execute(interview_q)).scalar_one() or 0)
        hired = int((await self._db.execute(hired_q)).scalar_one() or 0)

        channels = await self._channel_quality(team_id=team_id, job_id=job_id)

        return FunnelStats(
            job_id=job_id,
            total_pool=total_pool,
            screened_pass=passed,
            needs_review=needs_review,
            disqualified=disqualified,
            scored=scored,
            interviewed=interviewed,
            hired=hired,
            channels=channels,
        )

    async def _channel_quality(
        self, *, team_id: uuid.UUID, job_id: uuid.UUID | None
    ) -> list[ChannelQuality]:
        """渠道质量：latest source 分组 → 总数 / 通过 / 录用。

        渠道口径与 candidate_list 一致：每个候选人的最新 CandidateSource。
        候选量级 < 10 万，通过/录用细分在 Python 层聚合（可接受，双方言安全）。
        """
        # 每候选人最新来源（口径与 candidate_list 一致：fetched_at 倒序取第一条）
        latest_sq = (
            select(
                CandidateSource.candidate_id.label("cid"),
                CandidateSource.source_type.label("stype"),
            )
            .order_by(
                CandidateSource.candidate_id,
                CandidateSource.fetched_at.desc(),
            )
            .distinct(CandidateSource.candidate_id)
        ).subquery()

        rows = (
            await self._db.execute(
                select(
                    latest_sq.c.stype,
                    func.count(func.distinct(latest_sq.c.cid)),
                )
                .join(Candidate, Candidate.id == latest_sq.c.cid)
                .where(Candidate.team_id == team_id)
                .group_by(latest_sq.c.stype)
            )
        ).all()

        by_type: dict[str, ChannelQuality] = {}
        for stype, cnt in rows:
            key = stype or "unknown"
            by_type[key] = ChannelQuality(
                source_type=key, total=int(cnt or 0)
            )

        if not by_type:
            return []

        # 通过 / 录用按渠道细分：按 (stype, candidate) 去重后计数，
        # 避免 distinct(stype) 把多人折叠为每渠道一行
        pass_stmt = (
            select(latest_sq.c.stype, latest_sq.c.cid)
            .join(
                ScreeningResult,
                ScreeningResult.candidate_id == latest_sq.c.cid,
            )
            .join(Candidate, Candidate.id == latest_sq.c.cid)
            .join(Job, Job.id == ScreeningResult.job_id)
            .where(
                Candidate.team_id == team_id,
                ScreeningResult.disqualified.is_(False),
                ScreeningResult.needs_review.is_(False),
            )
            .distinct()
        )
        if job_id is not None:
            pass_stmt = pass_stmt.where(ScreeningResult.job_id == job_id)
        for stype, _cid in (await self._db.execute(pass_stmt)).all():
            key = stype or "unknown"
            if key in by_type:
                by_type[key].screened_pass += 1

        hire_stmt = (
            select(latest_sq.c.stype, latest_sq.c.cid)
            .join(
                CandidateJobOutcome,
                CandidateJobOutcome.candidate_id == latest_sq.c.cid,
            )
            .join(Candidate, Candidate.id == latest_sq.c.cid)
            .join(Job, Job.id == CandidateJobOutcome.job_id)
            .where(
                Candidate.team_id == team_id,
                CandidateJobOutcome.final_status.in_(_HIRED_STATUSES),
            )
            .distinct()
        )
        if job_id is not None:
            hire_stmt = hire_stmt.where(CandidateJobOutcome.job_id == job_id)
        for stype, _cid in (await self._db.execute(hire_stmt)).all():
            key = stype or "unknown"
            if key in by_type:
                by_type[key].hired += 1

        return sorted(by_type.values(), key=lambda c: c.total, reverse=True)


__all__ = ["OutcomeService", "OutcomeError"]
