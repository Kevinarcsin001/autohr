"""0007_seed_demo_jobs

Revision ID: e7b2c4a9f1d3
Revises: d8a3f7e2c1b4
Create Date: 2026-06-22 11:00:00.000000

内置演示职位：AI实习生、AI应用初级工程师、AI应用高级工程师、AI产品经理。
"""
from __future__ import annotations

import uuid
from typing import Sequence, Union

from alembic import op

revision: str = 'e7b2c4a9f1d3'
down_revision: Union[str, None] = 'd8a3f7e2c1b4'
branch_labels: Union[str, Sequence[str], None] = None
depends_on: Union[str, Sequence[str], None] = None

DEMO_JOBS = [
    {
        "title": "AI实习生",
        "jd_text": """岗位职责：
1. 协助团队进行 AI 模型的数据清洗、标注和预处理工作
2. 参与大语言模型（LLM）的 prompt 工程和效果评估
3. 协助构建和维护 AI 应用的知识库和数据集
4. 编写简单的 Python 脚本进行数据分析和自动化处理
5. 参与 AI 产品的功能测试和用户反馈收集

任职要求：
1. 计算机、人工智能、数学等相关专业本科在读或应届
2. 熟悉 Python 编程，了解基本的机器学习和深度学习概念
3. 对大语言模型（ChatGPT、Claude 等）有使用经验
4. 具备良好的数据分析能力和逻辑思维
5. 每周至少实习 4 天，实习期不少于 3 个月
6. 有 GitHub 项目或 Kaggle 竞赛经验者优先""",
        "min_education": "bachelor",
        "min_years": 0,
        "required_skills": ["Python", "机器学习基础", "数据分析", "Prompt Engineering"],
    },
    {
        "title": "AI应用初级工程师",
        "jd_text": """岗位职责：
1. 基于公司 AI 平台开发和维护智能应用，包括 RAG 问答系统、智能客服等
2. 使用 FastAPI / Flask 开发 AI 应用后端服务
3. 参与 LLM API 的调用、优化和效果评估
4. 编写技术文档和单元测试
5. 配合产品和算法团队，将 AI 能力落地到具体业务场景

任职要求：
1. 计算机相关专业本科及以上学历，1-3 年工作经验
2. 熟练掌握 Python，熟悉 FastAPI 或 Flask 等 Web 框架
3. 了解大语言模型（LLM）的基本原理和常见应用模式（RAG、Agent 等）
4. 熟悉 PostgreSQL / Redis 等数据库
5. 具备良好的工程习惯：Git 协作、代码规范、单元测试
6. 有 AI 应用开发经验或开源项目贡献者优先""",
        "min_education": "bachelor",
        "min_years": 1,
        "required_skills": ["Python", "FastAPI", "PostgreSQL", "LLM应用开发"],
    },
    {
        "title": "AI应用高级工程师",
        "jd_text": """岗位职责：
1. 主导公司 AI 应用产品的架构设计与技术选型
2. 设计并实现高性能的 RAG 系统和 Agent 工作流
3. 负责 LLM 应用的 prompt 工程优化、模型微调和效果评估体系搭建
4. 带领小团队完成 AI 应用的从 0 到 1 开发与交付
5. 跟踪前沿 AI 技术（多模态、Agent、MCP 协议等）并推动落地
6. 制定 AI 应用开发规范、代码审查和技术分享

任职要求：
1. 计算机相关专业本科及以上学历，5 年以上开发经验，其中 2 年以上 AI 相关
2. 精通 Python，具备扎实的系统设计能力
3. 深入理解 LLM 原理，有丰富的 RAG、Agent、Function Calling 实战经验
4. 精通 PostgreSQL、Redis、消息队列等基础设施
5. 有带领小型技术团队（3-5 人）的经验
6. 熟悉 Docker / K8s 容器化部署，了解 CI/CD 流程
7. 具备优秀的沟通能力和技术方案撰写能力""",
        "min_education": "bachelor",
        "min_years": 5,
        "required_skills": ["Python", "LLM架构设计", "RAG系统", "Agent开发", "团队管理"],
    },
    {
        "title": "AI产品经理",
        "jd_text": """岗位职责：
1. 负责公司 AI 产品线的需求分析、功能规划和迭代管理
2. 深入理解 LLM / AI 技术能力边界，将技术能力转化为产品方案
3. 撰写 PRD，协调算法、工程、设计团队推进产品落地
4. 跟踪 AI 行业动态和竞品分析，制定产品差异化策略
5. 负责产品数据分析和用户反馈收集，驱动产品优化
6. 向客户和内部团队进行产品方案宣讲

任职要求：
1. 本科及以上学历，3 年以上产品经理经验
2. 对 AI / LLM 技术有深入理解，能与技术团队高效沟通
3. 具备优秀的逻辑分析和数据驱动决策能力
4. 有 B2B SaaS 或企业级产品经验者优先
5. 有 AI 产品（如智能客服、AI 辅助工具等）0-1 经验者优先
6. 优秀的跨团队沟通协作和文档撰写能力""",
        "min_education": "bachelor",
        "min_years": 3,
        "required_skills": ["产品规划", "AI/LLM理解", "数据分析", "PRD撰写", "跨团队协作"],
    },
]


def _escape_sql(s: str) -> str:
    return s.replace("'", "''")


def upgrade() -> None:
    conn = op.get_bind()
    team_row = conn.exec_driver_sql(
        "SELECT id FROM teams ORDER BY created_at ASC LIMIT 1"
    ).first()
    user_row = conn.exec_driver_sql(
        "SELECT id FROM users ORDER BY created_at ASC LIMIT 1"
    ).first()
    if team_row is None or user_row is None:
        return

    team_id = str(team_row[0])
    user_id = str(user_row[0])

    for job in DEMO_JOBS:
        title_esc = _escape_sql(job["title"])
        existing = conn.exec_driver_sql(
            f"SELECT 1 FROM jobs WHERE title = '{title_esc}' AND team_id = '{team_id}' LIMIT 1"
        ).first()
        if existing:
            continue

        jid = str(uuid.uuid4())
        jd_esc = _escape_sql(job["jd_text"])
        skills_arr = "{" + ",".join(job["required_skills"]) + "}"
        req_id = str(uuid.uuid4())
        ver_id = str(uuid.uuid4())

        conn.exec_driver_sql(
            f"INSERT INTO jobs (id, team_id, title, jd_text, status, current_version, created_by, created_at, updated_at) "
            f"VALUES ('{jid}', '{team_id}', '{title_esc}', '{jd_esc}', 'active', 1, '{user_id}', NOW(), NOW())"
        )
        conn.exec_driver_sql(
            f"INSERT INTO job_hard_requirements (id, job_id, min_education, min_years, required_skills) "
            f"VALUES ('{req_id}', '{jid}', '{job['min_education']}', {job['min_years']}, '{skills_arr}')"
        )
        snapshot = (
            '{"title":"%s","status":"active","hard_requirements":{"min_education":"%s","min_years":%d,"required_skills":%s}}'
            % (
                title_esc,
                job["min_education"],
                job["min_years"],
                "[" + ",".join('"%s"' % s for s in job["required_skills"]) + "]",
            )
        )
        conn.exec_driver_sql(
            f"INSERT INTO job_versions (id, job_id, version, snapshot, changed_by, changed_at) "
            f"VALUES ('{ver_id}', '{jid}', 1, '{snapshot}'::jsonb, '{user_id}', NOW())"
        )


def downgrade() -> None:
    conn = op.get_bind()
    for job in DEMO_JOBS:
        title_esc = _escape_sql(job["title"])
        conn.exec_driver_sql(
            f"DELETE FROM job_hard_requirements WHERE job_id IN (SELECT id FROM jobs WHERE title = '{title_esc}')"
        )
        conn.exec_driver_sql(
            f"DELETE FROM job_versions WHERE job_id IN (SELECT id FROM jobs WHERE title = '{title_esc}')"
        )
        conn.exec_driver_sql(f"DELETE FROM jobs WHERE title = '{title_esc}'")
