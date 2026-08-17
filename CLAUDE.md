# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 常用命令

开发环境通过 `docker compose` 管理，所有命令在项目根执行：

```bash
make up              # 启动全部服务（postgres/redis/minio/backend/worker/beat/frontend/mineru）
make down            # 停止全部服务
make logs            # 实时日志
make migrate         # 执行数据库迁移（alembic upgrade head）
make makemigrations msg="..."  # 创建新迁移

make test            # 运行全部测试
make test-backend    # 仅后端 pytest（需要本地 Python 环境或容器内执行）
make test-frontend   # 仅前端 Vitest
make test-e2e        # Playwright E2E（需要完整栈运行）

make lint            # 前后端 lint（ruff + eslint）
make format          # 前后端格式化（ruff format + prettier）
make build           # 重新构建所有镜像
make install         # 安装前后端依赖（首次执行）
make gen-keys        # 生成 JWT RS256 密钥对
make gen-fernet      # 生成 Fernet 加密密钥（仅打印到 stdout，需手动把 FERNET_KEY=... 追加到 .env）
make clean           # 停止服务并删除所有数据卷
```

**独立运行**（不依赖 Docker 时）：

```bash
# 后端
cd backend && uv run pytest -k "test_name"   # 单个测试
cd backend && uv run ruff check .             # lint
cd backend && uv run mypy app/                # type check

# 前端
cd frontend && pnpm dev                       # 开发服务器 (port 3000)
cd frontend && pnpm test --run                # Vitest
cd frontend && pnpm test:e2e                  # Playwright
cd frontend && pnpm type-check                # tsc --noEmit
```

**Docker 内执行**（无需本地 Python/Node）：

```bash
docker compose exec backend pytest -k "test_name"
docker compose exec backend alembic upgrade head
docker compose exec backend alembic revision --autogenerate -m "description"
docker compose exec postgres psql -U autohr -d autohr
docker compose exec redis redis-cli
```

## 技术栈

| 层 | 技术 |
|---|---|
| 前端 | Next.js 14 (App Router) · TypeScript 5.4 · Tailwind CSS 3.4 · shadcn/ui · TanStack Query 5 · Zustand 4 |
| 后端 | Python 3.11 · FastAPI 0.110 · SQLAlchemy 2.0 (async) · Celery 5 · Pydantic v2 |
| 数据库 | PostgreSQL 15 (asyncpg, Docker/生产) · SQLite (aiosqlite, 裸跑开发默认) |
| 缓存/队列 | Redis 7 |
| 对象存储 | MinIO (开发) / S3 兼容 (生产) |
| LLM | 智谱 GLM-4-Plus + 通义千问 qwen-max，带 router + circuit breaker |
| 文档解析 | MinerU (magic-pdf) · pdfplumber · pypdf · python-docx |

## 架构概要

### 服务拓扑

```
nginx :80（仅生产，唯一对外端口）
  ├── frontend:3000  (Next.js SSR)
  └── backend:8000   (FastAPI)
        ├── worker   (Celery solo — 解析/提取/评分异步任务)
        ├── beat     (Celery beat — 邮件轮询定时调度)
        ├── postgres:5432
        ├── redis:6379
        ├── minio:9000
        └── mineru:8001  (PDF 高质量解析微服务)
```

开发环境不启动 nginx，前端直连 `localhost:${FRONTEND_PORT:-3001}`（`.env` 默认设 3001，避让本机其他 Next 项目），后端 `localhost:${BACKEND_PORT:-8000}`。本地调试直连端口：前端 `3001` / 后端 `8000` / PostgreSQL `5433`（避让本机 5432，容器内仍 5432）/ Redis `6379` / MinIO API `9000` + 控制台 `9001` / MinerU `8002`（容器内 8001）。端口默认值在 `.env` 中以 `*_PORT` 变量配置。

生产部署用 `docker-compose.prod.yml`：上述拓扑图中除 nginx:80 外的所有内部服务仅在内网 `internal` 网络互通、不暴露端口；镜像从 GHCR 拉取；后端 / worker / beat 共用同一镜像仅 `command` 不同。完整部署流程、首次上线清单、环境变量表与升级/回滚命令见 `README.md`（生产相关细节以 README 为单一权威来源）。

### 后端分层

```
app/
├── main.py               # FastAPI 工厂：lifespan、中间件注册、路由注册
├── core/                  # 基础设施
│   ├── config.py          # pydantic-settings（50+ 配置项，.env 加载）
│   ├── db.py              # AsyncSession + asyncpg engine（NullPool for Celery）
│   ├── security.py        # JWT RS256（python-jose）+ bcrypt
│   ├── deps.py            # FastAPI 依赖注入（current_user/admin_user/db_session）
│   └── middleware/         # RequestId → Audit → CORS → 全局异常处理
├── models/                # SQLAlchemy ORM（26 模型，UUID PK，Fernet PII 列加密）
├── schemas/               # Pydantic v2 请求/响应 schema
├── api/                   # 19 个路由模块（auth/teams/jobs/candidates/screening/interview/question_bank/hiring/...）
├── services/              # 业务逻辑层（解析/提取/评分/去重/导出/邮件/面试/录用建议等）
│   ├── ingestion/         # 采集子包：file_upload / email_fetcher / platform_import（三种简历来源）
│   └── parser/            # 解析子包：pdf_parser / docx_parser / ocr / service（MinerU 之外的本地 fallback）
├── adapters/
│   ├── llm/               # LLM 适配器：Base → Zhipu/Qwen/Mock + Router + CircuitBreaker
│   ├── storage.py         # S3/MinIO 抽象
│   ├── mineru_parser.py   # MinerU 微服务客户端
│   ├── parser_base.py     # 文档解析基类
│   └── crypto.py          # Fernet 加解密
└── workers/               # Celery worker/beat 配置 + 异步任务
```

中间件执行顺序（LIFO 注册，实际执行顺序反转）：`RequestIdMiddleware`（最外层） → `AuditMiddleware` → `CORSMiddleware`（内层）。

### 前端分层

```
app/
├── layout.tsx             # 根布局（zh-CN, Providers 包裹）
├── (auth)/                # 未认证路由组：login, register
└── (app)/                 # 认证路由组（含 AppNav 导航）
    ├── dashboard/         # 仪表盘
    ├── jobs/              # 职位管理 + 候选人筛选 + review 录用决策
    ├── candidates/        # 候选人详情（独立路由，支持跨职位查看）
    ├── interviews/        # 面试会话（问题查看 + 反馈提交）
    ├── resumes/           # 简历库
    ├── uploads/           # 上传管理
    ├── imports/           # 平台导入
    ├── accept-invite/     # 团队邀请确认（凭 token 接受邀请加入团队）
    └── admin/             # 管理后台（audit-logs, dedup, email, llm, members, question-bank, stats）
components/
├── ui/                    # shadcn/ui 基础组件（button, input, select, tabs, dialog 等）
├── providers.tsx          # QueryClient + AuthProvider + ThemeProvider
├── AppNav.tsx             # 侧边导航
├── CandidateTable.tsx     # 候选人表格（TanStack Table 8，服务端排序/筛选/分页）
└── ...                    # 业务组件（UploadDropzone, ReasonsList, ScoreBreakdown 等）
hooks/                     # 23 个 TanStack Query 自定义 hook，按领域划分
lib/api/                   # Axios 客户端（client.ts）+ 按领域拆分的 API 调用函数（17 个领域模块）
stores/authStore.ts        # Zustand：accessToken 仅存内存，refresh 走 httpOnly cookie
```

### 关键设计决策

**JWT RS256（非对称）**：`backend/keys/{private,public}.pem`，access token 30min 内存存储，refresh token 7d httpOnly cookie。依赖注入 `get_current_user` 自动校验。

**PII 列级加密**：`models/types.py` 中 `EncryptedString` 类型使用 Fernet 对称加密，姓名/手机/邮箱字段写入 DB 前自动加密，读取时自动解密。

**双数据库（开发 SQLite / 生产 PG）**：`.env` 默认 `DATABASE_URL=sqlite+aiosqlite:///./data/autohr.db`，**裸跑 backend（非 Docker）默认 SQLite**，免装 PostgreSQL、启动时自动 `create_all` 建表；Docker compose 栈与生产切换为 `postgresql+asyncpg://...`。代码须兼容两种方言（避免 PG 专有语法）；重置开发库用 `make db-dev-reset`（删 `data/autohr.db`）。Alembic 迁移仍以 PG 为目标，SQLite 主要服务本地快速启动与单元测试。

**NullPool for Celery**：Celery worker 使用 `asyncio.run()` 创建独立事件循环，StandardPool 会导致跨循环回池问题。因此 `db.py` 使用 `NullPool`。

**LLM 熔断器**：3 次失败 / 5 分钟窗口 → 5 分钟冷却期，冷却期自动切换到 fallback 适配器。

**SSE 实时推送**：筛选流水线进度通过 Server-Sent Events 推送到前端，非 WebSocket。

**去重键**：`sha1(normalize(name) + last4(phone) + prefix(email))` 用于跨源简历去重。

**Celery pool=solo**：开发环境 worker 使用 solo pool，避免 fork + asyncio 兼容问题。

**面试问题生成**：评分完成后前端按需触发（非自动），首次 `temperature=0.3`，regenerate 用 `temperature=0.8` 且保留历史 batch。覆盖 4 维度（技术/项目/沟通/文化），对低 confidence 技能强制追问。反馈按 question_id + reviewer_id upsert，rating 1-5。

**AI 录用建议**：基于简历 + JD + 面试反馈 + 评分，LLM 生成 hire/reserve/reject 三级建议 + 核心理由 + 潜在风险 + 试用期关注点，per interview_session 唯一。**注意路由挂载**：`hiring_router` 在 `main.py` 中挂在 `/api/interview` 前缀下（`include_router(hiring_router, prefix="/api/interview")`），即录用建议端点形如 `/api/interview/sessions/{id}/recommendation`，而非 `/api/hiring/...` —— 因为录用建议逻辑上是 interview_session 的子资源。

**面试会话数据模型**：`models/interview.py` 三层聚合 —— `InterviewSession`（一次面试的会话单位，评分+面试题生成后自动创建，`status=scheduled`）→ `InterviewQuestion`（AI 生成的题，按 `batch_id` 分批，regenerate 产生新 batch）→ `InterviewFeedback`（按 `question_id + reviewer_id` upsert，rating 1-5）。`question.session_id` / `feedback.session_id` 可空且 `ondelete=SET NULL`，是为兼容 session 引入前的旧数据；新代码路径均应关联 session。

**题库（question_bank）**：独立功能域 —— `models/question_bank.py`（`QuestionCategory` 分类 + `QuestionBankItem` 题目），由管理后台 `(app)/admin/question-bank/` 维护；面试出题时可从题库选题/组卷。对应迁移 `0008_question_bank` + `0009_interview_dimension_communication`（enum 增补 `communication` 维度 + 题库 dimension 列 VARCHAR(8)→(16)）。跨前后端一致：`api/question_bank.py` + `services/question_bank.py` + 前端 `lib/api/questionBank.ts` + `hooks/useQuestionBank.ts` + 组件 `QuestionComposeButton.tsx`。

**动态组卷（约 30 题/组）**：`services/question_bank.py` 的 `plan_and_assemble` + `build_candidate_signals` + 纯函数 `compute_dynamic_quotas` —— 信号源：JD 硬性 `required_skills`（w=2.0）> JD 正文命中分类名（w=1.5）> 候选人简历 skills（w=1.0）；按信号在分类 tags/名称上的命中率算亲和度，配额在基准上放大（上限 2×）、5 分步长、夹 [5,30]，归一到 150 分（约 30 题）。`subset_sum_dp` 同分组合优先**题目更多**（如 15 分选 5+5+5 而非 10+5）。静态配额基准见 `scripts/question_bank_data/_categories.json`（合计 155）；种子脚本幂等 upsert，现庋 536 题/15 分类。compose 端点（`POST /api/interview/sessions/{id}/compose`）默认 `dynamic=true`；assemble 预览端点支持 `dynamic+session_id`，响应携带 `plan`（信号 + 配额调整）。

**LLM Router**：`adapters/llm/router.py` 按 scope（resume/extraction/scoring/interview/hiring）路由到不同适配器，`_json.py` 提供结构化 JSON 输出封装，`circuit_breaker` 内置熔断逻辑。

### 数据库迁移

使用 Alembic，迁移文件在 `backend/alembic/versions/`。通过 Docker 容器执行：

```bash
docker compose exec backend alembic upgrade head        # 应用所有未执行的迁移
docker compose exec backend alembic revision --autogenerate -m "描述"  # 自动生成迁移
docker compose exec backend alembic downgrade -1        # 回滚最近一次迁移
```

CI 会验证迁移能否正常运行（`alembic upgrade head`）。

**迁移文件命名**：`versions/` 中 `0001` / `0006` / `0007` / `0008` 用数字前缀（推荐风格，新建迁移请沿用 `0009_xxx.py`），`0002`–`0005` 用 hash 前缀（历史遗留）。alembic 靠 `revision` / `down_revision` 链表而非文件名排序，命名混合不影响执行。`0007_seed_demo_jobs` 是**种子数据迁移**（插入 demo 职位），非 schema 变更，回滚前请确认环境；`0008_question_bank` 引入题库表。

### 测试

- **后端 pytest**：`asyncio_mode = "auto"`，session 级 RSA 密钥对 fixture，要求 ≥70% 覆盖率。`backend/tests/` 下按 `api/services/adapters/models/workers/core` 分目录。
- **前端 Vitest**：jsdom 环境，`vitest.setup.ts` 提供 jest-dom matchers 和浏览器 API mock。测试文件在 `components/__tests__/` 和 `lib/__tests__/`。
- **E2E Playwright**：Chromium only，CI 2 次重试，失败时上传 trace。4 个 spec 覆盖 auth/candidates/candidate-detail/admin。

### 代码质量与 lint 约定

**Ruff（后端）**：`line-length=100`、`target-version=py311`。关键豁免（见 `backend/pyproject.toml`）：`RUF001/002/003`（中文标点/全角字符——项目大量用中文，故中文注释与字符串不触发 lint）；`alembic/versions/*` 完全豁免（迁移文件无需整理 import/命名）；`tests/**` 豁免 `F401`/`F841`；`B008`（FastAPI `Depends()` 必备模式）。

**mypy（后端）**：`strict = true` + pydantic plugin；后端业务代码须通过严格类型检查（`alembic/versions/` 已 exclude）。

**pytest（后端）**：`asyncio_mode = "auto"`，`asyncio_default_*_loop_scope = "session"`（全 session 共享事件循环，fixture 不应假设每测试新建循环）；`addopts` 默认带 `--cov=app`，覆盖率门槛 ≥70%；CI 用 `testcontainers` 起临时 PG/Redis。

**注释语言**：代码库注释以中文为主，新增注释保持中文以统一风格。

### CI/CD

GitHub Actions 三条流水线（`.github/workflows/ci.yml`）：

1. **backend-test** — `ruff` + `pytest`（≥70% 覆盖率门槛）+ `alembic upgrade head` 迁移验证
2. **frontend-test** — ESLint + `tsc --noEmit` + Vitest + Next build
3. **e2e** — 完整 docker-compose 起栈 + Playwright（Chromium，4 spec，2 次重试）

`main` 合并后自动构建多平台镜像（amd64 + arm64）推送到 GHCR（`ghcr.io/<GITHUB_REPO>/{backend,frontend}:latest`）。后端 / worker / beat 共用同一镜像，仅 `command` 不同。

### 设计文档

完整的需求/设计/任务文档见 `.spec-workflow/specs/resume-screening/`：
- `requirements.md` — 功能需求
- `design.md` — 详细架构设计（近 600 行）
- `tasks.md` — 26 个已完成的实现任务
- `Implementation Logs/` — 各任务实现日志
