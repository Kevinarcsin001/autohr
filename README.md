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

## 生产部署

### 服务拓扑

生产通过 `docker-compose.prod.yml` 部署：

```
                 ┌──────────────────────────────────┐
                 │  nginx :80（唯一对外端口）        │
                 └───────────┬──────────────────────┘
                             │
        ┌────────────────────┼─────────────────────┐
        ▼                    ▼                     ▼
   frontend:3000        backend:8000           （静态/SSR）
                       (FastAPI + workers)
                             │
        ┌──────────┬─────────┼──────────┬──────────┐
        ▼          ▼         ▼          ▼          ▼
   postgres     redis     minio       worker      beat
   (5432)       (6379)    (9000)      (celery)    (celery)
```

- 所有内部服务仅在内网 `internal` 网络互通，**不暴露端口**
- 镜像从 GHCR 拉取：`ghcr.io/<owner>/<repo>/{backend,frontend}:latest`
- 后端 / worker / beat 共用同一镜像，仅 `command` 不同

### 首次部署

```bash
# 1. 准备生产密钥（务必使用强随机值，切勿复用开发值）
cp .env.example .env.prod
#   - 把 POSTGRES_PASSWORD / MINIO_SECRET_KEY / SECRET_KEY / FERNET_KEY 全部换为强随机
#   - 设置 GITHUB_REPO=<owner>/<repo>
#   - 填入 ZHIPU_API_KEY / DASHSCOPE_API_KEY

# 2. 生成 JWT 密钥对（与 backend/keys/ 共用）
make gen-keys

# 3. 拉取镜像并启动
docker compose -f docker-compose.prod.yml up -d

# 4. 执行数据库迁移
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head

# 5. 健康检查
curl http://localhost/healthz        # nginx：{"status":"ok"}
curl http://localhost/api/health     # backend：/health
```

### CI/CD

GitHub Actions 三条流水线（见 `.github/workflows/ci.yml`）：

1. **backend-test** — ruff lint + pytest（覆盖率门槛 70%）+ alembic 迁移验证
2. **frontend-test** — ESLint + tsc + Vitest + Next build
3. **e2e** — 完整 docker-compose 起栈 + Playwright 4 场景

`main` 分支合并后自动构建并推送 backend/frontend 镜像到 GHCR，多平台（amd64 + arm64）。

### 部署清单（上线前确认）

- [ ] `.env.prod` 中所有密钥使用强随机值（`openssl rand -hex 32`）
- [ ] `backend/keys/{private,public}.pem` 已生成且权限 600
- [ ] `POSTGRES_PASSWORD` 与 `MINIO_SECRET_KEY` 至少 32 字节
- [ ] 服务器开放端口 80（如需 HTTPS，前置一层 Caddy / Traefik 自动签证书）
- [ ] 首次部署执行 `alembic upgrade head`
- [ ] `/healthz` 与 `/api/health` 均返回 200
- [ ] CORS_ALLOWED_ORIGINS 配置为正式域名

### 升级流程

```bash
# 拉取最新镜像
docker compose -f docker-compose.prod.yml pull

# 滚动重启
docker compose -f docker-compose.prod.yml up -d

# 执行新迁移（如有）
docker compose -f docker-compose.prod.yml exec backend alembic upgrade head

# 回滚（如需）
docker compose -f docker-compose.prod.yml rollback   # 等价 docker compose up -d --no-deps backend@sha256:<prev>
```

### 环境变量清单

完整变量见 [`.env.example`](./.env.example)。生产关键变量：

| 变量 | 必填 | 说明 |
|---|---|---|
| `SECRET_KEY` | ✅ | 至少 32 字节，JWT 签名兜底 |
| `POSTGRES_PASSWORD` | ✅ | PostgreSQL 强随机密码 |
| `FERNET_KEY` | ✅ | PII 字段加密（手机号等） |
| `ZHIPU_API_KEY` | ⚠️ | 智谱 GLM；缺失将无法解析 |
| `DASHSCOPE_API_KEY` | ⚠️ | 通义千问；作为 fallback |
| `MINIO_SECRET_KEY` | ✅ | 对象存储根密码 |
| `MINIO_KMS_SECRET_KEY` | ✅ | SSE-S3 加密用 KMS 密钥 |
| `GITHUB_REPO` | ✅ | 生产拉取镜像路径 |
| `CORS_ALLOWED_ORIGINS` | ✅ | 正式域名 |

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
