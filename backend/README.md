# AutoHR Backend

FastAPI + Celery 后端服务。

## 本地开发

### 在容器中（推荐）

```bash
# 在项目根目录
make up
make migrate
```

### 不使用 Docker

```bash
cd backend
uv sync            # 或 pip install -e ".[dev]"
uv run uvicorn app.main:app --reload
```

## 目录结构

```
backend/
├── app/
│   ├── main.py             # FastAPI 入口（任务 1 最小占位）
│   ├── core/               # config / db / security / logging / deps（任务 2）
│   ├── models/             # SQLAlchemy ORM（任务 3）
│   ├── schemas/            # Pydantic 请求/响应
│   ├── services/           # 业务编排
│   ├── adapters/           # LLM / 存储 / OCR / 邮件适配器
│   ├── api/                # FastAPI 路由
│   └── workers/            # Celery 任务（任务 12）
├── alembic/                # 数据库迁移
├── tests/
└── pyproject.toml
```

## 测试

```bash
make test-backend
```

## Lint

```bash
make lint
```
