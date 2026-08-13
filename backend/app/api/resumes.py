"""简历库 API — 列出团队内所有简历及其处理状态。"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select

from app.core.deps import CurrentUser, DbSession
from app.models.candidate import Candidate, CandidateResume, ParsedStructure
from app.models.score import Score
from app.models.screening import ScreeningResult

router = APIRouter(prefix="/resumes", tags=["resumes"])


class ResumeBankItem(BaseModel):
    resume_id: str
    candidate_id: str | None = None
    candidate_name: str | None = None
    candidate_email: str | None = None
    filename: str
    parse_status: str
    extract_status: str | None = None
    score_total: int | None = None
    job_id: str | None = None
    uploaded_at: str

    class Config:
        from_attributes = True


class ResumeBankResponse(BaseModel):
    items: list[ResumeBankItem]


@router.get("/", response_model=ResumeBankResponse)
async def list_resumes(
    user: CurrentUser,
    db: DbSession,
) -> ResumeBankResponse:
    """列出当前团队所有简历及处理状态。"""
    team_id = user.team_id
    if not team_id:
        return ResumeBankResponse(items=[])

    # ORM 批量查询（跨方言：PG / SQLite 均可），避免原 text() SQL 的 ::cast / ->> / LATERAL
    resumes = (
        await db.execute(
            select(CandidateResume)
            .join(Candidate, Candidate.id == CandidateResume.candidate_id)
            .where(Candidate.team_id == team_id)
            .order_by(CandidateResume.uploaded_at.desc())
            .limit(200)
        )
    ).scalars().all()
    if not resumes:
        return ResumeBankResponse(items=[])

    resume_ids = [r.id for r in resumes]
    candidate_ids = {r.candidate_id for r in resumes}

    # 候选人姓名/邮箱（触发 EncryptedString 解密）
    candidates = (
        await db.execute(
            select(Candidate).where(Candidate.id.in_(candidate_ids))
        )
    ).scalars().all()
    name_map: dict[UUID, tuple[str | None, str | None]] = {
        c.id: (c.name, c.email) for c in candidates
    }

    # 解析结构（extract_status）—— 按 resume_id 索引
    ps_rows = (
        await db.execute(
            select(ParsedStructure.resume_id, ParsedStructure.data).where(
                ParsedStructure.resume_id.in_(resume_ids)
            )
        )
    ).all()
    extract_map: dict[UUID, str | None] = {
        r.resume_id: (r.data.get("status") if isinstance(r.data, dict) else None)
        for r in ps_rows
    }

    # 评分 —— 取每个 candidate 最高 total（一人可能在多 job 下有多个 score）
    score_rows = (
        await db.execute(
            select(Score.candidate_id, Score.total, Score.job_id)
            .where(Score.candidate_id.in_(candidate_ids))
            .order_by(Score.candidate_id, Score.total.desc())
        )
    ).all()
    score_map: dict[UUID, tuple[int, UUID]] = {}
    for s in score_rows:
        # 首条即该 candidate 最高分（已按 total desc 排序）
        if s.candidate_id not in score_map:
            score_map[s.candidate_id] = (s.total, s.job_id)

    # 筛选结果 job_id —— 同一 (candidate, job) 仅一条最新；取每个 candidate 最近一条
    sr_rows = (
        await db.execute(
            select(ScreeningResult.candidate_id, ScreeningResult.job_id)
            .where(ScreeningResult.candidate_id.in_(candidate_ids))
            .order_by(ScreeningResult.candidate_id, ScreeningResult.created_at.desc())
        )
    ).all()
    sr_job_map: dict[UUID, UUID] = {}
    for sr in sr_rows:
        if sr.candidate_id not in sr_job_map:
            sr_job_map[sr.candidate_id] = sr.job_id

    items: list[ResumeBankItem] = []
    for r in resumes:
        decrypted_name, decrypted_email = name_map.get(r.candidate_id, (None, None))
        # 文件名：取 file_storage_key 最后一段（team_id/uuid/uuid.pdf → uuid.pdf）
        key = r.file_storage_key or ""
        filename = key.rstrip("/").rsplit("/", 1)[-1] if key else ""

        # job_id 优先用与评分同 job 的筛选结果，否则回退到最近一条筛选
        score_total, score_job_id = score_map.get(r.candidate_id, (None, None))
        job_id = score_job_id if score_job_id else sr_job_map.get(r.candidate_id)

        items.append(
            ResumeBankItem(
                resume_id=str(r.id),
                candidate_id=str(r.candidate_id),
                candidate_name=decrypted_name,
                candidate_email=decrypted_email,
                filename=filename,
                parse_status=r.parse_status or "pending",
                extract_status=extract_map.get(r.id),
                score_total=score_total,
                job_id=str(job_id) if job_id else None,
                uploaded_at=r.uploaded_at.isoformat() if r.uploaded_at else "",
            )
        )

    return ResumeBankResponse(items=items)
