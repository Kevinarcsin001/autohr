# syntax=docker/dockerfile:1.6
FROM python:3.11-slim AS base

ENV PYTHONUNBUFFERED=1 \
    PYTHONDONTWRITEBYTECODE=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1 \
    UV_NO_CACHE=1 \
    PIP_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    UV_INDEX_URL=https://pypi.tuna.tsinghua.edu.cn/simple \
    PIP_TRUSTED_HOST=pypi.tuna.tsinghua.edu.cn

WORKDIR /app

# 切换 apt 清华镜像源（兼容 Debian trixie deb822 格式与 bookworm 旧格式）
RUN if [ -f /etc/apt/sources.list.d/debian.sources ]; then \
        sed -i \
            -e 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' \
            -e 's|http://security.debian.org|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
            /etc/apt/sources.list.d/debian.sources; \
    fi && \
    if [ -f /etc/apt/sources.list ]; then \
        sed -i \
            -e 's|http://deb.debian.org|https://mirrors.tuna.tsinghua.edu.cn|g' \
            -e 's|http://security.debian.org|https://mirrors.tuna.tsinghua.edu.cn/debian-security|g' \
            /etc/apt/sources.list; \
    fi

# 安装运行时依赖（移除 build-essential / *-dev 包：
# cryptography、pydantic-core、asyncpg 等均提供 arm64 wheel，不需要本地编译）
# - libmagic1: python-magic MIME 嗅探
# - libglib2.0-0 / libsm6 / libgl1 / libgomp1: PaddleOCR / OpenCV 运行时
# - poppler-utils: pdfplumber 部分功能
# - curl: 健康检查
# 用 BuildKit cache mount 加速重复构建
RUN --mount=type=cache,target=/var/cache/apt,sharing=locked \
    --mount=type=cache,target=/var/lib/apt,sharing=locked \
    apt-get update \
    && apt-get install -y --no-install-recommends \
        ca-certificates \
        libmagic1 \
        libglib2.0-0 \
        libsm6 \
        libxext6 \
        libgl1 \
        libgomp1 \
        poppler-utils \
        curl \
    && rm -rf /var/lib/apt/lists/*

# uv 加速依赖管理
RUN pip install --no-cache-dir uv

# 先 COPY pyproject + README + app 占位（让 editable 安装能找到 packages=["app"] + readme）
COPY pyproject.toml README.md ./
COPY app/__init__.py ./app/__init__.py

# pip cache mount 加速重复构建；wheel 优先（不需要 build-essential）
RUN --mount=type=cache,target=/root/.cache/uv,sharing=locked \
    --mount=type=cache,target=/root/.cache/pip,sharing=locked \
    uv pip install --system -e ".[dev]" || pip install -e ".[dev]"

# ---------------- development ----------------
FROM base AS development
EXPOSE 8000
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--reload"]

# ---------------- production ----------------
FROM base AS production
EXPOSE 8000
COPY . .
CMD ["uvicorn", "app.main:app", "--host", "0.0.0.0", "--port", "8000", "--workers", "4"]
