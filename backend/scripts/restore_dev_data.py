"""开发库数据恢复脚本：team + admin + demo 职位 + 题库种子。

用途：开发库被测试 TRUNCATE 清空后（集成测试的 autouse fixture 直连
``DATABASE_URL``，历史上发生过两次清库事故），一键恢复基础演示数据。

幂等：
- team/admin：不存在才建（按 name/email 查重）
- demo 职位：按 (team, title) 查重，复用 0007 迁移的 DEMO_JOBS 数据
- 题库：复用 seed_question_bank.py 的幂等 upsert（按 question 文本查重）

用法（容器内）：
    python scripts/restore_dev_data.py [--admin-email x@y.com --admin-password ...]

注意：仅用于开发库。生产数据请走备份恢复，勿用本脚本。
"""
from __future__ import annotations

import argparse
import asyncio
import importlib.util
import sys
import uuid
from pathlib import Path

# 让脚本能 import app
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from sqlalchemy import select, text  # noqa: E402

from app.core.db import AsyncSessionLocal  # noqa: E402
from app.core.security import hash_password  # noqa: E402
from app.models.team import Team  # noqa: E402
from app.models.user import User  # noqa: E402

ALEMBIC_DIR = Path(__file__).resolve().parent.parent / "alembic" / "versions"


def _load_demo_jobs() -> list[dict]:
    """从 0007 迁移文件加载 DEMO_JOBS（单一数据源，避免复制漂移）。"""
    spec = importlib.util.spec_from_file_location(
        "m0007", ALEMBIC_DIR / "0007_seed_demo_jobs.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    return list(mod.DEMO_JOBS)  # type: ignore[attr-defined]


async def _ensure_team_admin(session, admin_email: str, admin_password: str) -> Team:
    team = (await session.execute(select(Team).order_by(Team.created_at))).scalars().first()
    if team is None:
        team = Team(name="我的团队")
        session.add(team)
        await session.flush()
        print(f"✓ 创建 team: {team.name} ({team.id})")
    else:
        print(f"• team 已存在: {team.name} ({team.id})")

    # 悬空用户挂回：清库事故后存量用户的 team_id 可能为 NULL 或指向已消失的 team,
    # 不修复则一切团队职能接口 403「当前用户未加入任何团队」
    dangling = (
        await session.execute(
            select(User).where(
                (User.team_id.is_(None))
                | ~User.team_id.in_(select(Team.id))
            )
        )
    ).scalars().all()
    if dangling:
        for u in dangling:
            u.team_id = team.id
        await session.flush()
        print(f"✓ 悬空用户挂回团队: {len(dangling)} 个 ({', '.join(u.email for u in dangling)})")

    user = (
        await session.execute(select(User).where(User.email == admin_email))
    ).scalars().first()
    if user is None:
        user = User(
            email=admin_email,
            password_hash=hash_password(admin_password),
            name="Admin",
            role="admin",
            team_id=team.id,
        )
        session.add(user)
        await session.flush()
        print(f"✓ 创建 admin: {admin_email}")
    else:
        print(f"• admin 已存在: {admin_email}")
    return team


async def _seed_demo_jobs(session, team: Team) -> int:
    """复用 0007 的 DEMO_JOBS,幂等插入 jobs + hard_requirements + versions。"""
    user = (
        await session.execute(select(User).order_by(User.created_at))
    ).scalars().first()
    if user is None:
        print("⚠ 无用户，跳过 demo 职位")
        return 0

    inserted = 0
    for job in _load_demo_jobs():
        esc = job["title"].replace("'", "''")
        exists = (
            await session.execute(
                text(f"SELECT 1 FROM jobs WHERE title='{esc}' AND team_id='{team.id}'")
            )
        ).first()
        if exists:
            continue
        jid, rid, vid = uuid.uuid4(), uuid.uuid4(), uuid.uuid4()
        jd = job["jd_text"].replace("'", "''")
        skills = "{" + ",".join(job["required_skills"]) + "}"
        await session.execute(
            text(
                f"INSERT INTO jobs (id, team_id, title, jd_text, status, current_version, "
                f"created_by, created_at, updated_at) VALUES ('{jid}','{team.id}','{esc}',"
                f"'{jd}','active',1,'{user.id}',NOW(),NOW())"
            )
        )
        await session.execute(
            text(
                f"INSERT INTO job_hard_requirements (id, job_id, min_education, min_years, "
                f"required_skills) VALUES ('{rid}','{jid}','{job['min_education']}',"
                f"{job['min_years']},'{skills}')"
            )
        )
        snap = '{{"title":"{}","status":"active"}}'.format(esc)
        await session.execute(
            text(
                f"INSERT INTO job_versions (id, job_id, version, snapshot, changed_by, "
                f"changed_at) VALUES ('{vid}','{jid}',1,'{snap}'::jsonb,'{user.id}',NOW())"
            )
        )
        inserted += 1
    print(f"✓ demo 职位: 新增 {inserted} 个（已存在跳过）")
    return inserted


async def main() -> None:
    parser = argparse.ArgumentParser(description="开发库基础数据一键恢复")
    parser.add_argument("--admin-email", default="admin@autohr.dev")
    parser.add_argument("--admin-password", default="Autohr@2026")
    args = parser.parse_args()

    async with AsyncSessionLocal() as session:
        team = await _ensure_team_admin(session, args.admin_email, args.admin_password)
        await _seed_demo_jobs(session, team)
        await session.commit()

    # 题库种子（幂等 upsert，输出各分类统计）
    spec = importlib.util.spec_from_file_location(
        "seed_qb", Path(__file__).resolve().parent / "seed_question_bank.py"
    )
    mod = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(mod)  # type: ignore[union-attr]
    await mod.seed(team_name=team.name)


if __name__ == "__main__":
    asyncio.run(main())
