.PHONY: help install up down logs ps build rebuild migrate makemigrations test test-backend test-frontend lint format backend-shell frontend-shell db-shell redis-cli gen-keys gen-fernet clean

# 默认从 .env 加载变量（首次 cp .env.example .env 前 .env 不存在，需用 -include）
-include .env
export

help: ## 显示所有可用命令
	@grep -E '^[a-zA-Z_-]+:.*?## .*$$' $(MAKEFILE_LIST) | sort | awk 'BEGIN {FS = ":.*?## "}; {printf "\033[36m%-25s\033[0m %s\n", $$1, $$2}'

install: ## 安装前后端依赖（首次执行）
	cd frontend && pnpm install
	cd backend && uv sync || pip install -e .

up: ## 启动所有服务（后台）
	docker compose up -d

down: ## 停止所有服务
	docker compose down

logs: ## 查看所有服务日志（实时）
	docker compose logs -f --tail=100

ps: ## 查看服务状态
	docker compose ps

build: ## 构建所有镜像（不启动）
	docker compose build

rebuild: ## 强制重新构建镜像（无缓存）
	docker compose build --no-cache

migrate: ## 执行数据库迁移到最新版本
	docker compose exec backend alembic upgrade head

makemigrations: ## 创建新迁移（用法: make makemigrations msg="add users table"）
	@test -n "$(msg)" || (echo "Usage: make makemigrations msg=\"description\"" && exit 1)
	docker compose exec backend alembic revision --autogenerate -m "$(msg)"

test: ## 运行所有测试
	$(MAKE) test-backend
	$(MAKE) test-frontend

test-backend: ## 仅运行后端测试
	cd backend && uv run pytest || pytest

test-frontend: ## 仅运行前端单元测试
	cd frontend && pnpm test --run

test-e2e: ## 运行 Playwright E2E 测试
	cd frontend && pnpm test:e2e

lint: ## Lint 检查（前后端）
	cd backend && uv run ruff check . || ruff check .
	cd frontend && pnpm lint

format: ## 自动格式化代码
	cd backend && uv run ruff format . || ruff format .
	cd frontend && pnpm format

backend-shell: ## 进入 backend 容器 shell
	docker compose exec backend bash

frontend-shell: ## 进入 frontend 容器 shell
	docker compose exec frontend sh

db-shell: ## 进入 PostgreSQL psql
	docker compose exec postgres psql -U $(POSTGRES_USER) -d $(POSTGRES_DB)

redis-cli: ## 进入 redis-cli
	docker compose exec redis redis-cli

gen-keys: ## 生成 JWT RS256 密钥对
	@mkdir -p backend/keys
	@openssl genrsa -out backend/keys/private.pem 2048 2>/dev/null
	@openssl rsa -in backend/keys/private.pem -pubout -out backend/keys/public.pem 2>/dev/null
	@chmod 600 backend/keys/private.pem
	@echo "✓ Generated backend/keys/{private,public}.pem"

gen-fernet: ## 生成 Fernet 加密密钥并输出（追加到 .env）
	@python3 -c "from cryptography.fernet import Fernet; print('FERNET_KEY=' + Fernet.generate_key().decode())"

clean: ## ⚠️ 危险：停止服务并删除所有数据卷（postgres/redis/minio）
	@read -r -p "This will DELETE all data volumes. Continue? [y/N] " confirm; \
	if [ "$$confirm" = "y" ] || [ "$$confirm" = "Y" ]; then \
		docker compose down -v; \
		docker compose rm -f; \
	else \
		echo "Cancelled."; \
	fi
