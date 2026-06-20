"""简历库 API — 列出团队内所有简历及其处理状态。"""
from __future__ import annotations

from uuid import UUID

from fastapi import APIRouter
from pydantic import BaseModel
from sqlalchemy import select, text

from app.core.deps import CurrentUser, DbSession
from app.models.candidate import Candidate

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

    query = text("""
        SELECT
            cr.id::text AS resume_id,
            c.id::text AS candidate_id,
            cr.file_storage_key,
            cr.parse_status::text AS parse_status,
            ps.data->>'status' AS extract_status,
            s.total AS score_total,
            COALESCE(sr.job_id, any_sr.job_id)::text AS job_id,
            cr.uploaded_at::text AS uploaded_at
        FROM candidate_resumes cr
        JOIN candidates c ON c.id = cr.candidate_id
        LEFT JOIN parsed_structures ps ON ps.resume_id = cr.id
        LEFT JOIN scores s ON s.candidate_id = c.id
        LEFT JOIN screening_results sr ON sr.candidate_id = c.id AND s.job_id = sr.job_id
        LEFT JOIN LATERAL (
            SELECT sr2.job_id FROM screening_results sr2
            WHERE sr2.candidate_id = c.id
            ORDER BY sr2.created_at DESC LIMIT 1
        ) any_sr ON true
        WHERE c.team_id = :team_id
        ORDER BY cr.uploaded_at DESC
        LIMIT 200
    """)

    result = await db.execute(query, {"team_id": UUID(str(team_id))})
    rows = result.fetchall()

    # 用 ORM 查询候选人以触发 EncryptedString 解密 name / email
    candidate_ids = {UUID(row[1]) for row in rows if row[1]}
    name_map: dict[str, tuple[str | None, str | None]] = {}
    if candidate_ids:
        candidates = await db.execute(
            select(Candidate).where(Candidate.id.in_(candidate_ids))
        )
        for c in candidates.scalars().all():
            name_map[str(c.id)] = (c.name, c.email)

    items = []
    for row in rows:
        cid = row[1]
        decrypted_name, decrypted_email = name_map.get(cid, (None, None))

        # 文件名：取 file_storage_key 最后一段（team_id/uuid/uuid.pdf → uuid.pdf）
        file_storage_key = row[2] or ""
        filename = file_storage_key.rstrip("/").rsplit("/", 1)[-1] if file_storage_key else ""

        items.append(
            ResumeBankItem(
                resume_id=row[0],
                candidate_id=cid,
                candidate_name=decrypted_name,
                candidate_email=decrypted_email,
                filename=filename or "",
                parse_status=row[3] or "pending",
                extract_status=row[4],
                score_total=row[5],
                job_id=row[6],
                uploaded_at=row[7] or "",
            )
        )

    return ResumeBankResponse(items=items)
