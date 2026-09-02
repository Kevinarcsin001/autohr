# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 常用命令

开发环境通过 `docker compose` 管理，所有命令在项目根执行（Makefile 自动 `-include .env` 并 export，命令内可直接引用 `POSTGRES_USER` 等变量）：

```bash
make up              # 启动全部服务（postgres/redis/minio/backend/worker/beat/frontend/mineru）
make down            # 停止全部服务
make ps              # 查看服务状态
make logs            # 实时日志
make migrate         # 执行数据库迁移（alembic upgrade head）
make makemigrations msg="..."  # 创建新迁移

make test            # 运行全部测试
make test-backend    # 仅后端 pytest（需要本地 Python 环境或容器内执行）
make test-frontend   # 仅前端 Vitest
make test-e2e        # Playwright E2E（需要完整栈运行）

make lint            # 前后端 lint（ruff + eslint）
make format          # 前后端格式化（ruff format + prettier）
make build           # 构建所有镜像
make rebuild         # 无缓存强制重建
make install         # 安装前后端依赖（首次执行）
make gen-keys        # 生成 JWT RS256 密钥对
make gen-fernet      # 生成 Fernet 加密密钥（仅打印到 stdout，需手动把 FERNET_KEY=... 追加到 .env）
make backup-db       # 备份数据库（backups/ 下 gzip，自动轮转保留 14 份）
make db-dev-reset    # 删除开发 SQLite 库（重启 backend 自动重建表）
make clean           # 停止服务并删除所有数据卷（交互式确认）
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
├── models/                # SQLAlchemy ORM（27 模型，UUID PK，Fernet PII 列加密）
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
└── (app)/                 # 认证路由组（含 AppNav 导航；layout.tsx 内客户端守卫——token 过期自动跳登录并回跳）
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
hooks/                     # TanStack Query 自定义 hook，按领域划分（24 个，含 useAdaptiveInterview）
lib/api/                   # Axios 客户端（client.ts）+ 按领域拆分的 API 调用函数（17 个领域模块）
stores/authStore.ts        # Zustand：accessToken 仅存内存，refresh 走 httpOnly cookie
```

### 关键设计决策

**JWT RS256（非对称）**：`backend/keys/{private,public}.pem`，access token 30min 内存存储，refresh token 7d httpOnly cookie。依赖注入 `get_current_user` 自动校验。

**PII 列级加密**：`models/types.py` 中 `EncryptedString` 类型使用 Fernet 对称加密，姓名/手机/邮箱字段写入 DB 前自动加密，读取时自动解密。

**双数据库（开发 SQLite / 生产 PG）**：`.env` 默认 `DATABASE_URL=sqlite+aiosqlite:///./data/autohr.db`，**裸跑 backend（非 Docker）默认 SQLite**，免装 PostgreSQL、启动时自动 `create_all` 建表；Docker compose 栈与生产切换为 `postgresql+asyncpg://...`。代码须兼容两种方言（避免 PG 专有语法）；重置开发库用 `make db-dev-reset`（删 `data/autohr.db`）。Alembic 迁移仍以 PG 为目标，SQLite 主要服务本地快速启动与单元测试。

**NullPool for Celery**：Celery worker 使用 `asyncio.run()` 创建独立事件循环，StandardPool 会导致跨循环回池问题。因此 `db.py` 使用 `NullPool`。

**统一异步任务框架**：所有 Celery 任务（parse/extract/screen/score/export/transcribe/replay）共用 `workers/tasks.py` 的 `@async_task` 装饰器 —— 任务执行落 `models/async_job.py`（`AsyncJob` 表记录状态/进度/错误），失败自动重试退避。前端经 `hooks` 里的智能轮询感知 job 完成（如候选人上传后「解析中」标记自动刷新），新异步需求请沿用该模式而非裸写 `@celery.task`。

**LLM 熔断器**：3 次失败 / 5 分钟窗口 → 5 分钟冷却期，冷却期自动切换到 fallback 适配器。

**PII 出境脱敏**：简历原文/姓名进 LLM prompt 前必须过 `core/pii_mask.py` 的 `mask_pii_text`（姓名→「该候选人」，手机/邮箱/证件号掩码）——《个保法》合规要求，**extractor 豁免**（它必须识别 PII 本身）、adaptive 考生现场回答豁免。新增 LLM 出口路径时沿用。

**评分 total 权重固定**：LLM 只打 5 维度分，`total` 一律由 `services/scorer.py` 的 `compute_total` 按 `SCORE_WEIGHT_*` env 权重重算（和≠1 自动归一化），不采信 LLM 给的 total——保证候选人横向可比。

**三态筛选**：`ScreeningResult.needs_review`（迁移 0015）——字段缺失/学历 other/技能全空 → 待复核而非直接淘汰；`disqualified` 仅保留有明确证据的不达标。技能匹配走 `core/synonyms.py` 严格等价归一（"js"≡"JavaScript"）；**勿与 reasoning.py 的相关性表混用**（python→django 是证据搜索用，混入硬筛会放宽一票否决）。HR 改判（`manually_overridden=True`）优先于机器重跑，重跑筛选跳过改判行。

**效果回流闭环**：`models/outcome.py` 的 `CandidateJobOutcome`（迁移 0016，per job+candidate 唯一）存 HR 录入的最终结果（hired/probation_passed/rejected/withdrawn）；`GET /api/jobs/{id}/calibration` 出评分×结果校准报告（hire_rate 应随分段单调递增，不单调=权重该调）；`GET /api/dashboard/funnel` 出筛选池→通过→评分→面试→录用漏斗 + 渠道质量。与 `hiring_recommendations`（AI 建议）语义分离：一个是推测，一个是 ground truth。

**SSE 实时推送**：筛选流水线进度通过 Server-Sent Events 推送到前端，非 WebSocket。

**去重键**：`sha1(normalize(name) + last4(phone) + prefix(email))` 用于跨源简历去重。

**Celery pool=solo**：开发环境 worker 使用 solo pool，避免 fork + asyncio 兼容问题。

**面试问题生成**：评分完成后前端按需触发（非自动），首次 `temperature=0.3`，regenerate 用 `temperature=0.8` 且保留历史 batch。覆盖 4 维度（技术/项目/沟通/文化），对低 confidence 技能强制追问。反馈按 question_id + reviewer_id upsert，rating 1-5。

**动态组卷（约 30 题/组）**：`services/question_bank.py` 的 `plan_and_assemble` + `build_candidate_signals` + 纯函数 `compute_dynamic_quotas` —— 信号源：JD 硬性 `required_skills`（w=2.0）> JD 正文命中分类名（w=1.5）> 候选人简历 skills（w=1.0）> work_history 反扫（w=0.8，用分类词表扫职位/描述，捕获未写进 skills 的技术栈）；信号→分类匹配为两层：子串命中（强度 1.0）+ Dice bigram 模糊命中（阈值 0.35、强度 0.5，如「调模型」→「模型微调」，真 embedding 可在 `_signal_match_strength` 处叠加）；按信号在分类 tags/名称上的命中率算亲和度，配额在基准上放大（上限 2×）、5 分步长、夹 [5,30]，归一到 150 分（约 30 题）。分类内选题两阶段：tags 命中信号的相关题优先 DP 入选，中性题补缺口。`subset_sum_dp` 同分组合优先**题目更多**（如 15 分选 5+5+5 而非 10+5）。静态配额基准见 `scripts/question_bank_data/_categories.json`（合计 155，16 分类）；种子脚本幂等 upsert，现库 600 题/16 分类（含独立的 LangChain/LangGraph 分类，便于 JD 中出现框架关键词时被动态匹配点亮）。compose 端点（`POST /api/interview/sessions/{id}/compose`）默认 `dynamic=true`；assemble 预览端点支持 `dynamic+session_id`，响应携带 `plan`（信号 + 配额调整）。

**自适应面试（adaptive，M1）**：`services/adaptive_interview.py` —— 渐进式逐题推进：简历/JD 信号（复用 build_candidate_signals）→ 分支=题库分类按亲和度排序；每答一题 LLM 对照题库 reference_answer 结构化评分（1-5+要点命中/遗漏+证据），`decide_next_action` 纯函数决策：优秀（4-5）同分支难度+1 深挖 / 一般（3）换考点 / 差（1-2）标记薄弱换分支；switch 优先未问分支且排除薄弱。**引擎 v2 全参数走 env**（`core/config.py` 中 `ADAPTIVE_*` 字段）：回合区间 `[ADAPTIVE_MIN_TURNS=16, ADAPTIVE_MAX_TURNS=24]`（未达下限不提前收卷）、分支基础预算 `ADAPTIVE_BRANCH_BUDGET=4` + 强分支追加 `ADAPTIVE_STRONG_EXTRA=2`。v2 新增两类推进动作：**内容追问**（从考生回答提取的考点 `follow_up_suggestion` 作锚点，LLM `_generate_followup` 生成追问题，配额 `ADAPTIVE_FOLLOWUP_QUOTA`，独立模型/温度可配）与**广度守卫**（覆盖分类数低于 `ADAPTIVE_BREADTH_MIN` 时强制换分支）。**面试官控制权**：`POST .../adaptive/direct` 自然语言指令（「问问他 RAG」「来道简单的」→ `parse_directive` 解析出分支/难度后强制出题）；`POST .../adaptive/direct/audio` **语音指挥**（同步 ASR 转写后走同一指令链，≤15s 录音）；`GET .../adaptive/preview` 候选题预览（当前目标难度 + 信号相关度排序的备选题）。

**题库自增长闭环**：`POST .../adaptive/turns/{id}/promote` 把面试中 LLM 现场生成的追问题沉淀为题库候选（`QuestionBankItem.source='ai_followup'` + `review_status='pending'`，迁移 0014）；管理端 `GET /question-bank/categories/{id}/items?review_status=pending` 审核 + `POST /items/{id}/review` 通过/否决；**所有出题/组卷路径一律 `approved_only=True`**（list_items 强制约定，pending/rejected 不进正式卷子）。沉淀幂等（同 team+分类+题面去重），参考答案由评分证据合成（优秀要点/常见遗漏/回答摘录/提问锚点）。会后报告 `GET /sessions/{id}/report` 聚合逐题轨迹 + 分支画像 θ + 完成度 + 录用建议。账本表 `interview_turns` 每题一行（题目/回答/评分/证据/决策，状态全由 turns 推导）；三层评分降级（路由重试→服务层存回答→/next 自动补评）。API：`POST/GET /api/interview/sessions/{id}/adaptive/{start,state,answer,next}`（全部幂等）。前端工作台 `interviews/[sessionId]/adaptive/`（分支进度/能力画像/答题卡/回合时间线/音频录制）。M2a 音频已通：虚拟声卡双源录音（设备选择：物理麦=面试官/虚拟声卡=会议声）→ `POST /adaptive/audio` 存 MinIO → Celery `transcribe_turn`（迁移 0011 扩 enum）→ asr 容器（faster-whisper small int8 + silero VAD，initial_prompt 注入职位+信号领域词）→ 回填 answer_text → 自动评分；`transcription_status` 全程可见。**连续监听模式**（前端 `ContinuousRecorder.tsx`）：VAD 自动切片零点击，说话人物理分离（哪路设备有声记为谁）。asr 容器仿 mineru 模式（asr-service/，端口 8010，模型卷 asr_models）。M2b 会后回捞已通：上传整场录制（`POST /adaptive/recording`，500MB 上限）→ 面试官逐题打点起始时间（`PATCH /adaptive/turns/{id}/offset`，mm:ss；区间终点=下一题起点）→ Celery `replay_recording`（asr `/transcribe-segments` 一次上传多区间 ffmpeg 切片转写）→ 逐题回填 answer+自动评分；`recording_status`/`audio_start_ms` 全程可见（迁移 0012）。M3 CAT 已上：`estimate_branch_ability`（难度加权能力估计 θ∈[0,1]，先验收缩防小样本跳变）+ `target_difficulty`（Fisher 信息最大：题难度≈能力水平，θ→b=1+θ·4）；deepen/retry 目标难度由 θ 驱动并在 decision 透出 theta，不传 θ 时回退旧规则（向后兼容）；能力画像同估计器。对应迁移 `0010_adaptive_interview` + `0011_async_job_transcribe`。

**AI 录用建议**：基于简历 + JD + 面试反馈 + 评分，LLM 生成 hire/reserve/reject 三级建议 + 核心理由 + 潜在风险 + 试用期关注点，per interview_session 唯一。**注意路由挂载**：`hiring_router` 在 `main.py` 中挂在 `/api/interview` 前缀下（`include_router(hiring_router, prefix="/api/interview")`），即录用建议端点形如 `/api/interview/sessions/{id}/recommendation`，而非 `/api/hiring/...` —— 因为录用建议逻辑上是 interview_session 的子资源。

**面试会话数据模型**：`models/interview.py` 三层聚合 —— `InterviewSession`（一次面试的会话单位，评分+面试题生成后自动创建，`status=scheduled`）→ `InterviewQuestion`（AI 生成的题，按 `batch_id` 分批，regenerate 产生新 batch）→ `InterviewFeedback`（按 `question_id + reviewer_id` upsert，rating 1-5）。`question.session_id` / `feedback.session_id` 可空且 `ondelete=SET NULL`，是为兼容 session 引入前的旧数据；新代码路径均应关联 session。

**题库（question_bank）**：独立功能域 —— `models/question_bank.py`（`QuestionCategory` 分类 + `QuestionBankItem` 题目），由管理后台 `(app)/admin/question-bank/` 维护；面试出题时可从题库选题/组卷。对应迁移 `0008_question_bank` + `0009_interview_dimension_communication`（enum 增补 `communication` 维度 + 题库 dimension 列 VARCHAR(8)→(16)）。跨前后端一致：`api/question_bank.py` + `services/question_bank.py` + 前端 `lib/api/questionBank.ts` + `hooks/useQuestionBank.ts` + 组件 `QuestionComposeButton.tsx`。

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

- **后端 pytest**：`asyncio_mode = "auto"`，session 级 RSA 密钥对 fixture，要求 ≥70% 覆盖率。`backend/tests/` 下按 `api/services/adapters/models/workers/core` 分目录。本地裸跑（SQLite）口径：清库统一走 `tests/db_utils.purge_database`（双方言 + metadata 自省 + 进程内一次性建表）；PG-only 测试用 `_is_pg()` skip 标记；**本地全量存在 ~6 个已知 `database is locked` 残余**（跨文件测试的 SQLite 单写锁死锁，已实验证实 15s 超时无效、属本地环境固有限制），**CI testcontainers-PG 为唯一裁决口径**。
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

`main` 合并后自动构建 **4 个**多平台镜像（amd64 + arm64）推送到 GHCR（`ghcr.io/<GITHUB_REPO>/{backend,frontend,mineru,asr}:latest`）。后端 / worker / beat 共用同一镜像，仅 `command` 不同。

### 设计文档

完整的需求/设计/任务文档见 `.spec-workflow/specs/resume-screening/`：
- `requirements.md` — 功能需求
- `design.md` — 详细架构设计（近 600 行）
- `tasks.md` — 26 个已完成的实现任务
- `Implementation Logs/` — 各任务实现日志
