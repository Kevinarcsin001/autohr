"""仪表盘统计 API。"""
from __future__ import annotations

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import text

from app.core.deps import CurrentUser, DbSession

router = APIRouter(prefix="/dashboard", tags=["dashboard"])


class DashboardStats(BaseModel):
    total_candidates: int = 0
    pending_candidates: int = 0
    passed_candidates: int = 0
    disqualified_candidates: int = 0
    active_jobs: int = 0
    total_jobs: int = 0
    pending_reviews: int = 0  # 待复核的候选人


@router.get("/stats", response_model=DashboardStats)
async def get_dashboard_stats(
    user: CurrentUser,
    db: DbSession,
) -> DashboardStats:
    """获取仪表盘统计数据。"""
    team_id = user.team_id
    if not team_id:
        return DashboardStats()

    team_id_str = str(team_id)

    # 候选人总数
    result = await db.execute(
        text("SELECT count(*) FROM candidates WHERE team_id = :tid"),
        {"tid": team_id},
    )
    total_candidates = result.scalar() or 0

    # 待处理候选人（无 screening_result 或 screening_result 中 disqualified=null）
    result = await db.execute(
        text("""
            SELECT count(*) FROM candidates c
            LEFT JOIN screening_results sr ON sr.candidate_id = c.id
            WHERE c.team_id = :tid AND sr.id IS NULL
        """),
        {"tid": team_id},
    )
    pending_candidates = result.scalar() or 0

    # 通过/淘汰
    result = await db.execute(
        text("""
            SELECT count(*) FROM screening_results sr
            JOIN candidates c ON c.id = sr.candidate_id
            WHERE c.team_id = :tid AND sr.disqualified = false
        """),
        {"tid": team_id},
    )
    passed_candidates = result.scalar() or 0

    result = await db.execute(
        text("""
            SELECT count(*) FROM screening_results sr
            JOIN candidates c ON c.id = sr.candidate_id
            WHERE c.team_id = :tid AND sr.disqualified = true
        """),
        {"tid": team_id},
    )
    disqualified_candidates = result.scalar() or 0

    # 职位统计
    result = await db.execute(
        text("SELECT count(*) FROM jobs WHERE team_id = :tid AND status = 'active'"),
        {"tid": team_id},
    )
    active_jobs = result.scalar() or 0

    result = await db.execute(
        text("SELECT count(*) FROM jobs WHERE team_id = :tid"),
        {"tid": team_id},
    )
    total_jobs = result.scalar() or 0

    # 待复核（有 screening_result 但 manually_overridden=false，且需要人工介入的）
    result = await db.execute(
        text("""
            SELECT count(*) FROM screening_results sr
            JOIN candidates c ON c.id = sr.candidate_id
            WHERE c.team_id = :tid AND sr.manually_overridden = false
            AND sr.disqualified = false
        """),
        {"tid": team_id},
    )
    pending_reviews = result.scalar() or 0

    return DashboardStats(
        total_candidates=total_candidates,
        pending_candidates=pending_candidates,
        passed_candidates=passed_candidates,
        disqualified_candidates=disqualified_candidates,
        active_jobs=active_jobs,
        total_jobs=total_jobs,
        pending_reviews=pending_reviews,
    )
