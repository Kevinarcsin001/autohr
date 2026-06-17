# AutoHR

智能简历筛选助手 — 多用户 Web 应用，支持多源简历采集（文件上传 / 招聘平台导出 / 邮件附件抓取）、双 LLM（智谱 GLM-4 / 通义千问）解析、评分、推荐理由与面试问题生成。

## 快速开始

### 1. 准备环境变量

```bash
cp .env.example .env
```

### 2. 生成 JWT RS256 密钥对

```bash
make gen-keys
```

### 3. 生成 Fernet 加密密钥

```bash
make gen-fernet
# 把输出的 FERNET_KEY=... 追加到 .env
```

### 4. 配置 LLM API Key（必填）

编辑 `.env`：
- `ZHIPU_API_KEY=` — 智谱开放平台获取
- `DASHSCOPE_API_KEY=` — 阿里云 DashScope 获取

### 5. 启动开发环境

```bash
make up
```

启动后访问：
- **前端**: http://localhost:3001
- **后端 API 文档（Swagger）**: http://localhost:8000/docs
- **MinIO 控制台**: http://localhost:9001（用户名/密码见 .env）
- **PostgreSQL**: `localhost:5433`（避让本机其他 postgres）

### 6. 执行数据库迁移

```bash
make migrate
```

## 常用命令

```bash
make help           # 列出所有可用命令
make logs           # 查看实时日志
make ps             # 查看服务状态
make makemigrations msg="add users table"  # 创建数据库迁移
make test           # 运行所有测试
make test-backend   # 仅后端
make test-frontend  # 仅前端
make test-e2e       # Playwright E2E
make lint           # Lint 检查
make format         # 自动格式化
make db-shell       # 进入 PostgreSQL psql
make redis-cli      # 进入 redis-cli
make backend-shell  # 进入 backend 容器 shell
make clean          # ⚠️ 清理所有数据卷
```

## 技术栈

| 层 | 技术 |
|---|---|
| **前端** | Next.js 14 (App Router) · TypeScript · Tailwind · shadcn/ui · TanStack Query · Zustand |
| **后端** | Python 3.11 · FastAPI · SQLAlchemy 2.0 (async) · Celery · Pydantic v2 |
| **数据库** | PostgreSQL 15 |
| **缓存/队列** | Redis 7 |
| **对象存储** | MinIO (开发) · S3 兼容 (生产) |
| **LLM** | 智谱 GLM-4-Plus · 通义千问 qwen-max |
| **OCR** | PaddleOCR (中英文) |
| **文档解析** | pdfplumber · python-docx |

## 项目结构

```
autohr/
├── frontend/           # Next.js 应用
├── backend/            # FastAPI 应用
├── .spec-workflow/     # spec-workflow 文档（需求/设计/任务）
├── docker-compose.yml
├── Makefile
└── .env.example
```

详细架构见 `.spec-workflow/specs/resume-screening/design.md`。

## 设计文档

- **需求**: `.spec-workflow/specs/resume-screening/requirements.md`
- **设计**: `.spec-workflow/specs/resume-screening/design.md`
- **任务**: `.spec-workflow/specs/resume-screening/tasks.md`

## License

Proprietary. Internal use only.
