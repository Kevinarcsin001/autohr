# Tasks Document

> Spec: `resume-screening`
> 任务依赖按编号递增；同阶段内任务可并行。
> 每个 `_Prompt` 已按 spec-workflow 规范编写，可直接喂给子 agent。
> 实施步骤：① 在本文件中把任务行 `[ ]` 改为 `[-]` → ② 实施 → ③ 用 `mcp__spec-workflow__log-implementation` 记录 → ④ 改为 `[x]`。

- [x] 1. 搭建项目脚手架与开发环境
  - Files: `frontend/`, `backend/`, `docker-compose.yml`, `.env.example`, `Makefile`
  - 内容：前端 `create-next-app`（App Router + TS + Tailwind + shadcn/ui init）；后端 FastAPI + `pyproject.toml`（uv 或 poetry）；`docker-compose.yml` 含 PostgreSQL 15、Redis 7、MinIO、backend、worker、frontend 服务；`.env.example` 列出全部环境变量
  - Purpose: 建立可一键 `docker compose up` 的开发底座，后续任务在脚手架内增量
  - _Leverage: 设计文档 `### Technical Standards` / `### Project Structure` 章节_
  - _Requirements: 全部（基础设施层）_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: DevOps / Full-stack Lead | Task: 搭建 AutoHR 项目脚手架——Next.js 14 前端、FastAPI 后端、docker-compose 编排 PostgreSQL+Redis+MinIO+backend+worker+frontend。严格按 design.md 的 Technical Standards 与 Project Structure 章节。在 tasks.md 中将本任务 [ ] 改为 [-] 表示开始。 | Restrictions: 不要在脚手架阶段实现任何业务逻辑；不要硬编码任何密钥（一律走 .env）；前端不要 lock 死包管理器，用 pnpm；不要写 mock 数据 | _Leverage: design.md 的 Technical Standards、Project Structure 章节 | _Requirements: 全部 | Success: `cp .env.example .env && docker compose up -d` 后所有服务 healthy；`make migrate` 能跑通空迁移；前端 `localhost:3000` 返回 200；后端 `localhost:8000/health` 返回 200。实施完成后调用 `log-implementation` 记录 artifacts（含 docker-compose 服务清单、Makefile targets、依赖版本），然后把 tasks.md 中本任务 [-] 改为 [x]_

- [x] 2. 后端核心基础设施（config / db / security / logging / deps）
  - Files: `backend/app/core/{config.py, db.py, security.py, logging.py, deps.py}`, `backend/app/main.py`
  - 内容：`pydantic-settings` 加载 .env；异步 SQLAlchemy 2.0 engine + sessionmaker；JWT (RS256) 工具与 bcrypt 密码哈希；structlog 配置（脱敏处理器）；FastAPI 依赖注入（current_user / db_session / team_scope）；全局异常处理 + 请求 ID 中间件 + `/health` 端点
  - Purpose: 提供所有后续 service 共用的横切关注点
  - _Leverage: design.md `### 1. AuthService` 接口约定；FastAPI 官方依赖注入模式_
  - _Requirements: 1_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: Python 后端架构师 | Task: 实现 backend/app/core 全部模块——pydantic-settings、SQLAlchemy 异步 session、JWT 工具、bcrypt、structlog（带 PII 脱敏：phone/email/id_card）、FastAPI deps（current_user / team_scope）、全局异常处理 + request_id 中间件、/health 端点。在 tasks.md 把 [ ] 改为 [-]。 | Restrictions: 不要在此任务实现具体业务路由；JWT 必须 RS256（公私钥分离）；日志中 phone/email 必须脱敏（保留前 3 后 2）；不要把 secret 写进代码或日志 | _Leverage: design.md AuthService 章节、Technical Standards | _Requirements: 1 | Success: 单元测试 `tests/core/test_security.py` 覆盖 JWT 签发/校验/过期、bcrypt 哈希；`tests/core/test_logging.py` 验证 PII 脱敏；`/health` 端点返回 200。完成后 `log-implementation` 记录核心函数与配置项，把 tasks.md 改为 [x]_

- [x] 3. 数据库 ORM Models + Alembic 初始迁移（全部 17 张表）
  - Files: `backend/app/models/*.py`（按聚合拆 user/team/job/candidate/screening/score/interview/llm/async_job/email_config/audit）, `backend/alembic/versions/0001_init.py`
  - 内容：按 design.md `## Data Models` 实现 17 张表 SQLAlchemy 模型；PII 字段（name/phone/email/password_enc 等）使用自定义 `EncryptedString` TypeDecorator 走 Fernet；UUID PK、JSONB 字段、ENUM 用 SQLAlchemy `Enum`；创建 Alembic 初始迁移；`make migrate` 与 `make upgrade` 封装
  - Purpose: 全部后续业务的数据底座
  - _Leverage: design.md `## Data Models` 全部 schema；`backend/app/core/db.py`_
  - _Requirements: 1, 2, 3, 5, 7, 8, 9, 10, 11, 12, 13, 14_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 数据库 / ORM 工程师 | Task: 按 design.md `## Data Models` 实现全部 17 张表的 SQLAlchemy 2.0 ORM 模型（含 EncryptedString TypeDecorator、UUID PK、JSONB、ENUM、唯一约束、外键）；写 Alembic 初始迁移 0001_init。tasks.md 改 [-]。 | Restrictions: 严格匹配 design.md 字段名与类型；不要在本任务写业务逻辑；EncryptedString 必须自动加密/解密（Fernet key 来自 settings）；ENUM 用 SQLAlchemy 原生 Enum（不是字符串）；时间字段一律 TIMESTAMPTZ | _Leverage: design.md Data Models、core/db.py | _Requirements: 1,2,3,5,7,8,9,10,11,12,13,14 | Success: `alembic upgrade head` 在干净 PG 上成功；`pytest tests/models/` 通过外键/唯一约束/加密读写往返测试。`log-implementation` 记录表清单与 EncryptedString 实现，tasks.md 改 [x]_

- [x] 4. LLM 适配器层（Base + Zhipu + Qwen + Mock + Router）
  - Files: `backend/app/adapters/llm/{base.py, zhipu.py, qwen.py, mock.py, router.py}`
  - 内容：`BaseLLMAdapter` Protocol 定义 `chat(messages, response_schema, temperature, timeout) -> LLMResponse`；`ZhipuAdapter` 用 `zhipuai` SDK 调 GLM-4-Plus；`QwenAdapter` 用 `dashscope` 调 qwen-max；两 adapter 都支持 OpenAI 兼容 JSON mode / function calling；`MockAdapter` 返回固定 JSON 用于测试；`LLMRouter` 管理 scope-based 路由（primary/fallback）、5min/3 次失败熔断、超时重试 1 次、写 `llm_calls` 表
  - Purpose: 所有需要 LLM 的 service（extractor/scorer/reasoning/interview）经此调用，确保降级与统计一致
  - _Leverage: design.md `### 11. LLMRouter`；`backend/app/models/llm_call.py`_
  - _Requirements: 7, 9, 10, 11, 13_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: Python 后端 + LLM 集成工程师 | Task: 实现 BaseLLMAdapter Protocol、ZhipuAdapter（GLM-4-Plus）、QwenAdapter（qwen-max）、MockAdapter（测试）、LLMRouter（主备切换 + 5min/3 次失败熔断 + 1 次重试 + 写 llm_calls 表）。tasks.md 改 [-]。 | Restrictions: 不要硬编码 API key（走 settings）；router 不允许在熔断期内继续打主模型；schema 输出失败时按 design.md 错误处理章节降级；不要在适配器内写业务 prompt（由调用方传入） | _Leverage: design.md LLMRouter 章节、models/llm_call.py | _Requirements: 7,9,10,11,13 | Success: `tests/adapters/test_llm_router.py` 覆盖主备切换、熔断、token 统计、超时重试；MockAdapter 单测覆盖 schema 解析。`log-implementation` 记录 BaseLLMAdapter 接口、Router 路由策略、llm_calls 写入逻辑，tasks.md 改 [x]_

- [x] 5. AuthService + API + 前端登录注册
  - Files: `backend/app/services/auth_service.py`, `backend/app/api/auth.py`, `backend/app/schemas/auth.py`, `frontend/app/(auth)/{login,register}/page.tsx`, `frontend/lib/api/auth.ts`, `frontend/hooks/useAuth.ts`, `frontend/stores/authStore.ts`
  - 内容：register/authenticate/invite_member/accept_invite；JWT access+refresh；前端登录/注册表单（shadcn Form + zod）；TanStack Query mutation；Zustand 存 access token + current user；自动刷新拦截器
  - Purpose: 完成需求 1（用户与团队管理）的认证闭环
  - _Leverage: design.md `### 1. AuthService`；`core/security.py`_
  - _Requirements: 1.1, 1.2, 1.3, 1.4_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 全栈工程师 | Task: 后端实现 AuthService（register/authenticate/invite/accept_invite）+ /api/auth 路由 + Pydantic schema；前端实现 login/register 页面 + TanStack Query mutation + Zustand authStore + 自动 refresh 拦截器。tasks.md 改 [-]。 | Restrictions: 密码最小 8 位含字母+数字；invite 走邮件 token（一次性）；前端 access token 仅存内存（Zustand）+ refresh token 存 httpOnly cookie（后端 Set-Cookie）；不要把 token 写入 localStorage | _Leverage: design.md AuthService、core/security.py、TanStack Query、shadcn/ui | _Requirements: 1.1,1.2,1.3,1.4 | Success: 后端集成测试覆盖注册→重复注册拒绝→登录→refresh→invite→accept 全流程；前端 Playwright 测试覆盖注册→登录→token 刷新。`log-implementation` 记录 API endpoints、authStore 接口、关键函数，tasks.md 改 [x]_

- [x] 6. 团队与成员管理
  - Files: `backend/app/services/team_service.py`, `backend/app/api/teams.py`, `backend/app/schemas/team.py`, `frontend/app/admin/members/page.tsx`
  - 内容：列出当前团队成员、邀请成员（接任务 5 invite_member）、移除成员、修改角色（admin/member）、当前用户切换 team scope（多 team 场景）
  - Purpose: 完成需求 1.3 的权限/角色管理
  - _Leverage: 任务 5 的 invite_member；`core/deps.team_scope`_
  - _Requirements: 1.3_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 全栈工程师 | Task: 实现 team_service + /api/teams 路由（list/invite/remove/update_role）+ 前端 admin/members 页面。tasks.md 改 [-]。 | Restrictions: 只有 admin 角色能 invite/remove/update_role；不能移除自己；不能把自己降级（防失控）；普通成员访问管理路由返回 403 | _Leverage: 任务 5、core/deps.py | _Requirements: 1.3 | Success: 集成测试覆盖 admin 邀请/移除/降级成功与普通成员被拒；前端 Playwright 覆盖 admin 操作 + 普通成员看不到管理入口。`log-implementation` 记录 endpoints 与 UI 组件，tasks.md 改 [x]_

- [x] 7. JobService + API + 前端职位 CRUD
  - Files: `backend/app/services/job_service.py`, `backend/app/api/jobs.py`, `backend/app/schemas/job.py`, `frontend/app/jobs/{page,[id]/page}.tsx`, `frontend/components/JobForm.tsx`
  - 内容：create/update/list/get；编辑时写 job_versions 快照；hard_requirements 结构化字段（min_education/min_years/required_skills/excluded_companies）；职位级 llm_config 覆盖；前端职位列表（按 status 过滤）+ 创建/编辑表单（含硬性条件子表单）
  - Purpose: 完成需求 2
  - _Leverage: design.md `### 2. JobService`、`## Data Models` 的 jobs/job_versions/job_hard_requirements_
  - _Requirements: 2.1, 2.2, 2.3, 2.4_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 全栈工程师 | Task: 实现 JobService（create/update/list/get + 写 job_versions 快照 + 结构化 hard_requirements）+ /api/jobs 路由 + 前端职位列表与创建/编辑表单（含硬性条件 UI）。tasks.md 改 [-]。 | Restrictions: 已完成评分结果不自动重算（即使硬性条件变更）；编辑必须写版本快照（含 before/after）；列表分页（默认 20/页）；硬性条件的 ENUM 不允许越界值；前端 JD 文本用 markdown 编辑器 | _Leverage: design.md JobService、Data Models、shadcn/ui Form | _Requirements: 2.1,2.2,2.3,2.4 | Success: 集成测试覆盖创建→编辑触发快照→列表过滤→详情；前端 Playwright 覆盖新建职位含硬性条件。`log-implementation` 记录 endpoints、JobForm props、版本快照逻辑，tasks.md 改 [x]_

- [x] 8. 对象存储适配器 + 文件加密
  - Files: `backend/app/adapters/{storage.py, crypto.py}`
  - 内容：`Storage` 类封装 S3 兼容操作（put/get/生成签名 URL），开发用 MinIO；`crypto.py` 提供 Fernet 工具（任务 3 已用）+ 文件级 AES-256 加密辅助；上传走 SSE；下载走短期签名 URL（默认 5 分钟）
  - Purpose: 简历文件持久化与安全下载基础
  - _Leverage: design.md `### Technical Standards` 加密章节；任务 3 EncryptedString_
  - _Requirements: 3, 14（安全下载）_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 后端 / 存储工程师 | Task: 实现 Storage 适配器（put/get/list/signed_url，S3 兼容，开发 MinIO）+ 文件级加密辅助 + 短期签名 URL（5min 默认）。tasks.md 改 [-]。 | Restrictions: 不允许明文存储简历文件（必须 SSE 或客户端加密）；签名 URL 必须包含过期时间且服务端校验；不要把对象存储凭据写日志 | _Leverage: design.md Technical Standards 加密章节、boto3 | _Requirements: 3, 14 | Success: 集成测试覆盖 put→get 往返、签名 URL 过期拒绝访问、跨 team 访问 404。`log-implementation` 记录 Storage 接口与签名 URL 实现，tasks.md 改 [x]_

- [x] 9. FileUploadAdapter + 上传 API + 前端上传中心
  - Files: `backend/app/services/ingestion/file_upload.py`, `backend/app/api/uploads.py`, `frontend/app/uploads/page.tsx`, `frontend/components/UploadDropzone.tsx`
  - 内容：批量上传校验（PDF/DOC/DOCX/PNG/JPG/JPEG，单文件 20MB，单批 100 份）；先创建 upload_intent 拿签名 URL → 客户端直传 MinIO → confirm_upload 写 candidate_resumes（parse_status=pending）+ 入 async_jobs 队列；前端拖拽批量上传 + 进度列表 + 失败重试
  - Purpose: 完成需求 3
  - _Leverage: 任务 8 Storage；任务 12 async_jobs；前端 react-dropzone_
  - _Requirements: 3.1, 3.2, 3.3, 3.4_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 全栈工程师 | Task: 后端实现 file_upload adapter（校验 + 写 candidate_resumes + 入 async_jobs 队列）+ /api/uploads 路由（intent/confirm）；前端实现拖拽上传 + 进度列表 + 单文件失败重试。tasks.md 改 [-]。 | Restrictions: MIME 嗅探不能只看扩展名（用 python-magic）；单文件超限前端拒绝；批次中单文件失败不阻塞其他；上传并发度前端默认 4 | _Leverage: 任务 8 Storage、任务 12 async_jobs、react-dropzone | _Requirements: 3.1,3.2,3.3,3.4 | Success: 集成测试覆盖合法上传/超限拒绝/批量部分失败；前端 Playwright 覆盖拖拽 5 文件上传含 1 非法类型。`log-implementation` 记录 endpoints、UploadDropzone props、async_jobs 入队逻辑，tasks.md 改 [x]_

- [x] 10. PlatformImportAdapter（招聘平台导出识别）
  - Files: `backend/app/services/ingestion/platform_import.py`, `backend/app/schemas/platform.py`
  - 内容：detect_platform(file) 通过文件名/内部结构特征识别 Boss/智联/猎聘；标准结构化（JSON/Excel）→ 直接映射到 CandidateStructure 跳过 OCR；简历附件包 → 走任务 9/13 的解析链路；不支持的格式返回明确错误
  - Purpose: 完成需求 4
  - _Leverage: 任务 9 file_upload；任务 14 Extractor_
  - _Requirements: 4.1, 4.2, 4.3, 4.4_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 后端工程师 | Task: 实现 platform_import adapter（detect_platform + 三大平台映射规则 + 附件包分流 + 不支持格式报错）。tasks.md 改 [-]。 | Restrictions: detect 必须有最低置信度阈值（避免误判）；不支持的格式返回 422 + 用户反馈入口；映射后的 CandidateStructure 必须经过 schema 校验；不要在此任务写解析器（复用任务 13/14） | _Leverage: 任务 9、任务 14 Extractor、pandas/openpyxl | _Requirements: 4.1,4.2,4.3,4.4 | Success: 集成测试覆盖 3 个平台各 1 个 fixture 包的导入；不支持格式返回 422。`log-implementation` 记录平台识别规则与字段映射表，tasks.md 改 [x]_

- [x] 11. EmailFetcherAdapter + IMAP 配置 + Celery beat 定时
  - Files: `backend/app/services/ingestion/email_fetcher.py`, `backend/app/api/email_configs.py`, `backend/app/schemas/email.py`, `frontend/app/admin/email/page.tsx`, `backend/app/workers/scheduler.py`
  - 内容：email_configs CRUD（admin）+ IMAP 凭据加密存储；EmailFetcher 每 N 分钟拉新邮件（去重 Message-ID）→ 识别简历附件 → 入 candidate_resumes + async_jobs；Celery beat 每 15min 扫描 enabled email_config 触发；认证失败退避重试 5 次后暂停 + 前端告警
  - Purpose: 完成需求 5
  - _Leverage: 任务 8 Storage；任务 12 async_jobs；imap_tools_
  - _Requirements: 5.1, 5.2, 5.3, 5.4_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 全栈工程师 | Task: 后端实现 email_configs CRUD + EmailFetcher（IMAP 拉取 + Message-ID 去重 + 附件入库）+ Celery beat 调度 + 失败退避；前端 admin/email 页面配置邮箱并显示抓取状态/告警。tasks.md 改 [-]。 | Restrictions: IMAP 密码必须加密存储；凭据错误退避序列 15s/60s/300s/15min/30min 共 5 次，全失败暂停 + in-app 告警；不要把邮件正文写日志；附件识别需结合文件类型 + 关键词（如 "简历"/"resume"） | _Leverage: 任务 8、任务 12、imap_tools | _Requirements: 5.1,5.2,5.3,5.4 | Success: 集成测试用 mock IMAP server 覆盖正常抓取/重复邮件跳过/认证失败重试+暂停。`log-implementation` 记录 endpoints、调度配置、退避序列，tasks.md 改 [x]_

- [x] 12. Celery workers + async_jobs 断点续作
  - Files: `backend/app/workers/{celery_app.py, tasks.py}`, `backend/app/services/async_job_service.py`
  - 内容：Celery 配置（broker=Redis, backend=Redis）；通用任务包装函数（idempotency_key 去重、状态机 queued→running→success/failed/retry、最大重试 3、错误捕获）；定义任务签名：parse_resume/extract_structured/run_screening/score_candidate/fetch_emails/run_export；beat schedule（邮件抓取）
  - Purpose: 所有重计算任务的执行底座
  - _Leverage: design.md `## Architecture` 异步流水线；`async_jobs` 表_
  - _Requirements: 3.3, 5.1, 6, 7, 8, 9, 14_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 后端 / 任务系统工程师 | Task: 实现 Celery app + 通用任务包装（idempotency_key 去重 + async_jobs 状态机 + 重试策略）+ 6 个任务签名（具体逻辑由后续任务填充）+ beat schedule。tasks.md 改 [-]。 | Restrictions: 任务必须幂等（基于 idempotency_key + target_id）；进程重启后 queued/running 状态任务必须能恢复（启动时把 running 重置为 queued）；最大重试 3 次；不要在任务函数内直接调用外部 SDK（应通过 adapter） | _Leverage: design.md 异步流水线、async_jobs 表 | _Requirements: 3.3,5.1,6,7,8,9,14 | Success: 单元测试覆盖幂等性、状态机、重启恢复；beat schedule 在 docker compose up 后生效。`log-implementation` 记录 6 个任务签名、状态机、idempotency 实现，tasks.md 改 [x]_

- [x] 13. ParserService（PDF/Word/OCR）
  - Files: `backend/app/services/parser/{pdf_parser.py, docx_parser.py, ocr.py, __init__.py}`, `backend/app/workers/parser_task.py`
  - 内容：PDF 优先用 pdfplumber 文本层；字符密度低于阈值（如 100 字/页）回退 PaddleOCR；Word 用 python-docx 提取正文+表格；图片走 PaddleOCR 中英文；提取文本 `< 50 字符` 标记 parse_status='low_text'；损坏文件标记 'failed'；Celery 任务包装（接任务 12）
  - Purpose: 完成需求 6
  - _Leverage: 设计文档 `### 4. ParserService`；PaddleOCR、pdfplumber、python-docx_
  - _Requirements: 6.1, 6.2, 6.3, 6.4_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 后端 / 文档解析工程师 | Task: 实现 ParserService（PDF 文本层 + 密度阈值回退 OCR、Word 正文+表格、图片 OCR）+ Celery parser_task。tasks.md 改 [-]。 | Restrictions: PaddleOCR 模型懒加载（首次调用初始化）；PDF 文本层字符密度阈值（100 字/页）可配置；解析失败必须保留原文件 + 写 parse_error；不要在内存中保存全文（流式写库）；PaddleOCR 进程内调用（不依赖外部服务） | _Leverage: design.md ParserService、任务 12 | _Requirements: 6.1,6.2,6.3,6.4 | Success: 用 9 个 fixture（3 PDF/3 Word/3 图片）单测覆盖；密度阈值回退分支单测；low_text 与 failed 状态单测。`log-implementation` 记录 ParserService 接口、密度阈值、OCR 模型版本，tasks.md 改 [x]_

- [x] 14. ExtractorService（结构化字段抽取）
  - Files: `backend/app/services/extractor.py`, `backend/app/schemas/candidate_structure.py`, `backend/app/workers/extractor_task.py`
  - 内容：定义 CandidateStructure Pydantic schema（name/phone/email/education/years_of_experience/skills[]/expected_salary/current_company/work_history[]/confidence per field）；prompt 构造（system + user）；调用 LLMRouter（scope='extractor'）；JSON mode + Pydantic 校验 + 失败重试一次（带 schema 提示）；写 parsed_structures；Celery 任务（接任务 12）
  - Purpose: 完成需求 7
  - _Leverage: 任务 4 LLMRouter；任务 13 ParserService 输出_
  - _Requirements: 7.1, 7.2, 7.3, 7.4_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 后端 / Prompt 工程师 | Task: 实现 CandidateStructure Pydantic schema（每字段附 confidence）+ Extractor service（prompt 构造 + LLMRouter 调用 + schema 校验 + 失败重试 + 持久化）+ Celery 任务。tasks.md 改 [-]。 | Restrictions: 字段无法确定时填 null + confidence=0，绝不臆造；prompt 中必须显式要求 JSON 输出 + 给出 schema 示例；schema 校验失败重试一次（带错误反馈），仍失败标 'partial_extracted'；不要把完整简历原文写日志（脱敏后写） | _Leverage: 任务 4 LLMRouter、任务 13 ParserService | _Requirements: 7.1,7.2,7.3,7.4 | Success: 用 MockAdapter 单测覆盖正常抽取/null 字段/schema 不合重试；集成测试用 1 个真实 fixture 简历（Mock LLM）端到端。`log-implementation` 记录 CandidateStructure schema、prompt 模板、重试逻辑，tasks.md 改 [x]_

- [ ] 15. DedupService
  - Files: `backend/app/services/dedup.py`
  - 内容：计算 dedup_key = sha1(normalize(name) + last4(phone) + prefix(email))；新候选人入库前查匹配；命中 1 条 → 合并（新简历作为 candidate_resumes 追加，结构化字段按 confidence 取胜更新 candidates 主字段）；命中 ≥ 2 → 写 dedup_matches 状态 'pending_review'；提供 merge(a, b) / flag_for_review API
  - Purpose: 完成需求 12
  - _Leverage: design.md `### 6. DedupService`_
  - _Requirements: 12.1, 12.2, 12.3_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 后端工程师 | Task: 实现 DedupService（dedup_key 计算 + 匹配 + 合并 + 多对一标记 pending_review）+ /api/candidates/merge 路由。tasks.md 改 [-]。 | Restrictions: 合并时 candidates 主字段更新需 confidence 比较逻辑（高 confidence 覆盖低，相同则保留旧值）；不要自动合并疑似候选（必须人工 review）；姓名归一化（去空格/全半角/拼音小写）；不要保留重复 candidate_resumes（合并后引用更新到主候选人） | _Leverage: design.md DedupService、models | _Requirements: 12.1,12.2,12.3 | Success: 单测覆盖同名同手机合并、多对一标记、姓名归一化；集成测试模拟同人多次投递。`log-implementation` 记录 dedup_key 算法、合并策略、merge endpoint，tasks.md 改 [x]_

- [ ] 16. FilterService（硬性条件筛选）
  - Files: `backend/app/services/filter.py`, `backend/app/api/screening.py`
  - 内容：纯逻辑（不调 LLM）；输入 job.hard_requirements + candidate.parsed_structure；逐条比对（学历等级映射、最低年限、必备技能集合包含、竞业排除）；任一不满足 disqualified=true + reasons[]；字段缺失默认 disqualified + "字段缺失" 标记；写 screening_results；提供 manual override API + 写 manual_overrides
  - Purpose: 完成需求 8
  - _Leverage: 任务 14 Extractor 输出；任务 7 HardRequirements_
  - _Requirements: 8.1, 8.2, 8.3, 8.4_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 后端工程师 | Task: 实现 FilterService（硬性条件逐条比对 + 字段缺失标记 + 持久化 screening_results）+ manual override API + manual_overrides 记录。tasks.md 改 [-]。 | Restrictions: 不调用任何 LLM（纯规则）；字段缺失默认 disqualified + 显式标记 "字段缺失"；学历等级映射（`high_school < bachelor < master < phd`）；HR 改判必须记 actor/old/new/reason；不要直接修改原 screening_results（写 manual_overrides 并设 manually_overridden=true） | _Leverage: 任务 7、任务 14 | _Requirements: 8.1,8.2,8.3,8.4 | Success: 单测覆盖学历不达标/技能缺失/字段缺失/HR 改判；集成测试端到端。`log-implementation` 记录 FilterService 接口、规则集、override endpoint，tasks.md 改 [x]_

- [ ] 17. ScorerService（综合评分 + 子维度）
  - Files: `backend/app/services/scorer.py`, `backend/app/schemas/score.py`, `backend/app/workers/scorer_task.py`
  - 内容：prompt 构造（JD + 结构化字段 + 简历关键片段）；调用 LLMRouter（scope='scorer'）输出 JSON `{total, skill, experience, education, stability, potential}`；schema 校验；写 scores（含 model_used + llm_call_id）；Celery 任务（接任务 12）；失败由 LLMRouter 自动 fallback
  - Purpose: 完成需求 9
  - _Leverage: 任务 4 LLMRouter；任务 16 Filter（仅通过者进入评分）_
  - _Requirements: 9.1, 9.2, 9.3, 9.4_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 后端 / Prompt 工程师 | Task: 实现 ScorerService（prompt + LLMRouter scope='scorer' + 子维度 schema + 持久化 scores 含 model_used）+ Celery 任务。tasks.md 改 [-]。 | Restrictions: 评分必须是 0-100 整数；prompt 要求 LLM 引用具体简历片段作为打分依据；同分排名二级排序 skill>experience>name；fallback 触发后 scores.model_used 必须反映真实使用模型；不要把简历原文整体塞进 prompt（取关键片段）以省 token | _Leverage: 任务 4、任务 16 | _Requirements: 9.1,9.2,9.3,9.4 | Success: MockAdapter 单测覆盖正常评分 + fallback；同分排序单测；集成测试用 1 候选人端到端。`log-implementation` 记录 prompt 模板、子维度 schema、fallback 行为，tasks.md 改 [x]_

- [ ] 18. ReasoningService（推荐理由 + 事实校验）
  - Files: `backend/app/services/reasoning.py`, `backend/app/schemas/reason.py`
  - 内容：与评分同批调用（或紧接其后）生成 3-5 条推荐理由；硬性淘汰者生成淘汰理由指向被违反条件；事实一致性校验：对每条理由在 raw_text 中查找支持片段（字符串匹配 + 简单同义词词典），找不到则剔除；持久化 score_reasons（含 validated 标记）
  - Purpose: 完成需求 10
  - _Leverage: 任务 17 Scorer；任务 16 Filter_
  - _Requirements: 10.1, 10.2, 10.3_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 后端 / Prompt + NLP 工程师 | Task: 实现 ReasoningService（推荐理由生成 + 淘汰理由 + 事实一致性校验）+ 持久化 score_reasons。tasks.md 改 [-]。 | Restrictions: 理由格式必须是要点（bullet）3-5 条；事实校验必须找到原文支持（字符串匹配 + 简单同义词），找不到则剔除并在 validated=false；淘汰理由必须明确指向硬性条件（如 "学历不达标：本科 vs 要求硕士"）；不要把无法验证的"事实"输出给用户 | _Leverage: 任务 17、任务 16 | _Requirements: 10.1,10.2,10.3 | Success: 单测覆盖推荐理由生成/事实校验剔除/淘汰理由生成；集成测试覆盖端到端。`log-implementation` 记录 prompt、事实校验算法、reason schema，tasks.md 改 [x]_

- [ ] 19. InterviewService（面试问题生成）
  - Files: `backend/app/services/interview.py`, `backend/app/schemas/interview.py`, `backend/app/api/interview.py`
  - 内容：评分完成后生成 5-8 个问题（覆盖技能/项目/短板/文化）；命中短板（如某必备技能 confidence 低）至少 1 条追问；regenerate 接口（temperature=0.8 + 保留历史 batch）；HR 反馈接口（写 interview_feedbacks）
  - Purpose: 完成需求 11
  - _Leverage: 任务 17 Scorer；任务 14 Extractor_
  - _Requirements: 11.1, 11.2, 11.3, 11.4_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 后端 / Prompt 工程师 | Task: 实现 InterviewService（生成 5-8 题 + 短板追问 + regenerate + 反馈记录）+ /api/interview 路由。tasks.md 改 [-]。 | Restrictions: 必备技能 `confidence < 0.7` 时至少 1 条短板追问；regenerate 必须 temperature=0.8 并保留历史 batch_id；反馈记录 reviewer_id + rating 1-5 + 文本；不要在前端默认显示反馈输入框（仅面试后开启） | _Leverage: 任务 17、任务 14 | _Requirements: 11.1,11.2,11.3,11.4 | Success: MockAdapter 单测覆盖正常生成/短板识别/regenerate；集成测试覆盖反馈记录。`log-implementation` 记录 prompt、问题维度 schema、endpoints，tasks.md 改 [x]_

- [ ] 20. 编排服务（Screen+Score+Reason+Interview 一体触发）
  - Files: `backend/app/services/screening_orchestrator.py`, `backend/app/api/screening.py`
  - 内容：run_screening(job_id, candidate_ids[]) 编排：① 调 Filter → ② 通过者入 Scorer 队列 → ③ Scorer 完成后触发 Reasoning + Interview（并行）；前端 /api/screening/run 提交 + SSE/WebSocket 进度推送（candidates 总数/已完成/失败）
  - Purpose: 把任务 16-19 串成可一键触发的流水线
  - _Leverage: 任务 12 Celery、任务 16-19_
  - _Requirements: 8, 9, 10, 11_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 后端 / 编排工程师 | Task: 实现 screening_orchestrator（Filter → Scorer → Reasoning+Interview 并行）+ /api/screening/run + SSE 进度推送。tasks.md 改 [-]。 | Restrictions: 任一阶段失败不阻塞其他候选人（错误聚合到 async_jobs.failed_reasons）；SSE 必须支持断线重连（基于 Last-Event-ID）；orchestrator 不允许直接调 LLM adapter（必须走各 service） | _Leverage: 任务 12、16、17、18、19 | _Requirements: 8,9,10,11 | Success: 集成测试用 5 候选人 mock 触发 run_screening 验证全流程；SSE 进度单测覆盖推送与断线。`log-implementation` 记录 orchestrator 接口、SSE 端点、错误聚合策略，tasks.md 改 [x]_

- [ ] 21. AuditLogService + middleware
  - Files: `backend/app/services/audit_log.py`, `backend/app/core/middleware/audit.py`
  - 内容：AuditLogService.log(actor, action, target_type, target_id, before, after)；FastAPI middleware 拦截所有写方法（POST/PUT/PATCH/DELETE）+ 显式 service 调用补充业务语义 action；前端查询 /api/audit-logs（admin only）
  - Purpose: 满足非功能需求（可靠/安全）+ 需求 8.4 改判追溯
  - _Leverage: `audit_logs` 表_
  - _Requirements: 8.4, 安全非功能_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 后端 / 安全工程师 | Task: 实现 AuditLogService + FastAPI middleware（拦截写方法 + 显式 service 调用）+ /api/audit-logs 查询（admin only）。tasks.md 改 [-]。 | Restrictions: middleware 只记录写方法且只记录成功响应；敏感字段（password/token）必须脱敏；查询接口强制 admin；audit_logs 不可删除（应用层禁止 DELETE）；记录 IP 与 user-agent | _Leverage: models/audit_log.py | _Requirements: 8.4, 安全 | Success: 集成测试覆盖改判/职位更新/成员邀请均写审计；普通成员查 audit-logs 返回 403。`log-implementation` 记录 middleware 拦截规则、敏感字段白名单、endpoint，tasks.md 改 [x]_

- [ ] 22. ExportService（Excel 异步导出）
  - Files: `backend/app/services/export.py`, `backend/app/api/exports.py`, `frontend/components/ExportButton.tsx`
  - 内容：request_export(job_id, filters) 入 async_jobs；run_export 用 openpyxl 生成 xlsx（含可见字段 + 评分 + 理由 + 面试问题）；行数 > 阈值（如 5000）异步生成 + 邮件通知；download_url 返回 5min 签名 URL；跨 team 访问 404
  - Purpose: 完成需求 14.3
  - _Leverage: 任务 8 Storage；任务 12 async_jobs；openpyxl_
  - _Requirements: 14.3, 14.4_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 后端工程师 | Task: 实现 ExportService（request + run via Celery + 大文件异步邮件通知 + 签名 URL 下载）+ /api/exports 路由 + 前端 ExportButton。tasks.md 改 [-]。 | Restrictions: 行数 > 5000 强制异步；导出内容仅包含当前用户可见字段（按权限）；下载 URL 5min 过期；跨 team 访问返回 404；不要在前端直连对象存储 | _Leverage: 任务 8、任务 12、openpyxl | _Requirements: 14.3,14.4 | Success: 集成测试覆盖小文件同步导出、大文件异步 + 邮件、跨 team 404。`log-implementation` 记录 endpoints、Excel schema、阈值配置，tasks.md 改 [x]_

- [ ] 23. 前端候选人列表页（三分组 + 排序 + 筛选 + 列自定义）
  - Files: `frontend/app/jobs/[id]/candidates/page.tsx`, `frontend/components/CandidateTable.tsx`, `frontend/components/CandidateFilters.tsx`
  - 内容：三分组（通过/淘汰/待复核）+ 按总分倒序默认排序 + 子维度切换 + 任意字段筛选（学历、年限、技能、来源、评分区间）+ 表格密度切换 + 列自定义 + 保存视图（localStorage）；SSE 订阅筛选进度
  - Purpose: 完成需求 14.1, 9.2
  - _Leverage: 任务 20 orchestrator SSE；TanStack Table_
  - _Requirements: 9.2, 14.1_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 前端工程师 | Task: 实现候选人列表页（三分组 + 排序 + 筛选 + 列自定义 + 保存视图 + SSE 进度订阅）。tasks.md 改 [-]。 | Restrictions: 列表分页（默认 50/页）+ 无限滚动可选；筛选条件持久化到 URL query（便于分享）；表格必须支持键盘导航；不要在前端做评分计算（一律走后端） | _Leverage: 任务 20、TanStack Table、shadcn/ui | _Requirements: 9.2,14.1 | Success: Playwright 覆盖排序/筛选/列自定义/视图保存/ESE 进度更新；Lighthouse 性能评分 ≥ 85。`log-implementation` 记录页面路由、组件树、TanStack Table 配置，tasks.md 改 [x]_

- [ ] 24. 前端候选人详情页
  - Files: `frontend/app/candidates/[id]/page.tsx`, `frontend/components/{ResumePreview, StructuredFields, ScoreBreakdown, ReasonsList, InterviewQuestions}.tsx`
  - 内容：左侧原始简历预览（PDF.js / 图片）+ 右侧 tab：结构化字段 / 评分细项（含雷达图）/ 推荐理由（含"查看依据"高亮原文）/ 面试问题（含反馈输入）；底部操作日志（含改判历史）；HR 改判按钮 + 弹窗填理由
  - Purpose: 完成需求 14.2, 8.4, 11.4
  - _Leverage: 任务 16-21 后端 API；PDF.js_
  - _Requirements: 8.4, 10, 11.4, 14.2_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 前端工程师 | Task: 实现候选人详情页（简历预览 + 结构化字段 tab + 评分细项雷达图 + 推荐理由含依据高亮 + 面试问题含反馈 + 操作日志 + HR 改判）。tasks.md 改 [-]。 | Restrictions: "查看依据"必须高亮原文 span（基于 reasoning 携带的 char_offset 或 substring）；改判必须填理由；面试反馈输入默认折叠；详情页加载性能 LCP ≤ 2.5s | _Leverage: 任务 16-21 API、PDF.js、recharts | _Requirements: 8.4,10,11.4,14.2 | Success: Playwright 覆盖：打开详情 → 切 tab → 点击查看依据高亮 → 提交反馈 → HR 改判并查看日志。`log-implementation` 记录路由、tab 组件树、改判流程，tasks.md 改 [x]_

- [ ] 25. Admin 后台（成员/LLM/邮箱/统计）
  - Files: `frontend/app/admin/{members,llm,email,stats}/page.tsx`, `backend/app/api/admin.py`
  - 内容：成员管理（接任务 6）+ LLM 配置（primary/fallback/scope overrides/单模型超时/熔断阈值）+ 邮箱配置（接任务 11）+ 统计（token 用量、延迟 P95、成本、成功率，按 scope/模型/时间分组图表）
  - Purpose: 完成需求 13 + 管理需求
  - _Leverage: 任务 4 LLMRouter；任务 6 团队；任务 11 邮箱；`llm_calls` 表_
  - _Requirements: 13.1, 13.2, 13.3, 13.4_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: 全栈工程师 | Task: 后端 /api/admin 路由（llm_config CRUD + stats 聚合查询）+ 前端 admin 4 个子页面（成员/LLM/邮箱/统计）。tasks.md 改 [-]。 | Restrictions: admin 路由强制 admin 角色；LLM 配置变更需写 audit_log；统计查询必须走索引（按 called_at + scope 复合索引）；图表默认 7 天可切 30 天；不要在前端做大数据聚合 | _Leverage: 任务 4、6、11、audit_logs、llm_calls | _Requirements: 13.1,13.2,13.3,13.4 | Success: Playwright 覆盖 admin 4 页面 CRUD + 统计图表渲染；权限边界测试。`log-implementation` 记录 endpoints、4 个 admin 页面组件、统计 SQL 聚合，tasks.md 改 [x]_

- [ ] 26. 测试套件 + CI/CD + 生产部署
  - Files: `backend/tests/**`, `frontend/__tests__/**`, `frontend/e2e/**`, `.github/workflows/{backend,frontend,e2e}.yml`, `docker-compose.prod.yml`, `README.md`
  - 内容：后端 pytest 覆盖率 ≥ 70% + testcontainers 集成测试；前端 Vitest + React Testing Library；Playwright 4 大 E2E 场景（详见 design.md `### End-to-End Testing`）；GitHub Actions（后端测试/前端测试/E2E/构建推送镜像）；生产 docker-compose（含 nginx 反向代理、PostgreSQL 持久卷、MinIO、backend/worker/beat/frontend）；README 部署文档
  - Purpose: 满足非功能需求（可靠/性能）+ 上线就绪
  - _Leverage: design.md `## Testing Strategy` 全部_
  - _Requirements: 全部（验证层）_
  - _Prompt: Implement the task for spec resume-screening, first run spec-workflow-guide to get the workflow guide then implement the task: Role: QA / DevOps 工程师 | Task: 完成测试套件（后端 pytest 覆盖率 ≥70% + testcontainers；前端 Vitest；Playwright 4 大 E2E）+ GitHub Actions 三条流水线 + 生产 docker-compose + README 部署文档。tasks.md 改 [-]。 | Restrictions: 不要降低覆盖率门槛（`< 70%` CI 失败）；E2E 在 CI 必须能跑（headless + docker-compose 服务）；生产 compose 必须含健康检查 + 资源限制；README 必须包含环境变量清单与初始化命令；不要在生产 compose 暴露数据库端口 | _Leverage: design.md Testing Strategy | _Requirements: 全部 | Success: GitHub Actions 全绿；本地 `docker compose -f docker-compose.prod.yml up` 起得来且 `/health` 全部 200；E2E 4 场景全部通过。`log-implementation` 记录 CI 工作流、E2E 场景清单、生产服务拓扑，tasks.md 改 [x]_
