"""Application configuration loaded from environment variables.

任务 1 提供最小可运行版本（仅基本字段）；
任务 2 将扩展完整字段（CORS、邮件、LLM Router 等）并补齐校验。
"""
from __future__ import annotations

from functools import lru_cache

from pydantic import Field
from pydantic_settings import BaseSettings, SettingsConfigDict


class Settings(BaseSettings):
    """Application settings, all loaded from environment."""

    model_config = SettingsConfigDict(
        env_file=".env",
        env_file_encoding="utf-8",
        case_sensitive=False,
        extra="ignore",
    )

    # === General ===
    ENVIRONMENT: str = Field(default="development", description="运行环境")
    LOG_LEVEL: str = Field(default="INFO", description="日志级别")
    SECRET_KEY: str = Field(default="change-me", min_length=16)

    # === Database ===
    DATABASE_URL: str = Field(
        default="postgresql+asyncpg://autohr:autohr_dev@localhost:5432/autohr"
    )

    # === Redis ===
    REDIS_URL: str = Field(default="redis://localhost:6379/0")

    # === JWT (RS256) ===
    JWT_ALGORITHM: str = Field(default="RS256")
    JWT_ACCESS_TOKEN_EXPIRE_MINUTES: int = Field(default=30)
    JWT_REFRESH_TOKEN_EXPIRE_DAYS: int = Field(default=7)
    JWT_PRIVATE_KEY_PATH: str = Field(default="./keys/private.pem")
    JWT_PUBLIC_KEY_PATH: str = Field(default="./keys/public.pem")

    # === Fernet (PII 列加密) ===
    FERNET_KEY: str = Field(default="")

    # === MinIO / S3 ===
    MINIO_ENDPOINT: str = Field(default="localhost:9000")
    # 用于生成签名 URL 的外部地址（浏览器可访问）；不设置则回退到 MINIO_ENDPOINT
    MINIO_PUBLIC_ENDPOINT: str = Field(default="")
    MINERU_ENDPOINT: str = Field(default="http://mineru:8001")
    MINIO_ACCESS_KEY: str = Field(default="autohr")
    MINIO_SECRET_KEY: str = Field(default="autohr_dev_secret")
    MINIO_BUCKET: str = Field(default="resumes")
    MINIO_SECURE: bool = Field(default=False)
    # 签名 URL 默认过期（5 分钟，design.md 要求）；service 层 helper 强制 1 ≤ expires ≤ 3600
    STORAGE_SIGNED_URL_EXPIRE_SECONDS: int = Field(default=300, ge=1, le=3600)
    STORAGE_CONNECT_TIMEOUT_SECONDS: float = Field(default=5.0, gt=0)
    STORAGE_READ_TIMEOUT_SECONDS: float = Field(default=30.0, gt=0)

    # === LLM ===
    ZHIPU_API_KEY: str = Field(default="")
    ZHIPU_MODEL: str = Field(default="glm-4-plus")
    DASHSCOPE_API_KEY: str = Field(default="")
    QWEN_MODEL: str = Field(default="qwen-max")
    LLM_PRIMARY: str = Field(default="zhipu")
    LLM_FALLBACK: str = Field(default="qwen")
    LLM_TIMEOUT_SECONDS: int = Field(default=30)
    LLM_MAX_RETRIES: int = Field(default=1)
    LLM_CIRCUIT_BREAKER_FAILURES: int = Field(default=3)
    LLM_CIRCUIT_BREAKER_COOLDOWN_SECONDS: int = Field(default=300)

    # === Upload ===
    MAX_UPLOAD_FILE_SIZE_MB: int = Field(default=20)
    MAX_UPLOAD_BATCH_SIZE: int = Field(default=100)
    # 服务端 MIME 嗅探白名单（python-magic 实测结果必须 ∈ 此集合）
    UPLOAD_ALLOWED_MIME_TYPES: str = Field(
        default=(
            "application/pdf,"
            "application/msword,"
            "application/vnd.openxmlformats-officedocument.wordprocessingml.document,"
            "image/png,image/jpeg"
        )
    )
    # intent 阶段扩展名快速过滤白名单（仅作快速拒绝路径，真正校验在 confirm 嗅探）
    UPLOAD_ALLOWED_EXTENSIONS: str = Field(
        default="pdf,doc,docx,png,jpg,jpeg"
    )
    # confirm 阶段嗅探的字节数（足够 magic 识别，又不过度 IO）
    UPLOAD_SNIFF_BYTES: int = Field(default=2048, ge=256, le=65536)
    # 前端直传并发上限（同时 NEXT_PUBLIC_UPLOAD_MAX_CONCURRENCY 注入到客户端）
    UPLOAD_MAX_CONCURRENCY: int = Field(default=4, ge=1, le=16)

    # === Platform Import ===
    PLATFORM_DETECT_MIN_CONFIDENCE: float = Field(
        default=0.5, ge=0.0, le=1.0, description="平台识别置信度阈值"
    )
    PLATFORM_SUPPORT_FEEDBACK_URL: str = Field(
        default="https://github.com/your-org/autohr/issues/new",
        description="不支持的平台格式时给用户的反馈入口",
    )

    # === Email ===
    EMAIL_POLL_INTERVAL_MIN: int = Field(default=15)

    # === OCR / Parser ===
    PADDLE_OCR_LANG: str = Field(default="ch")
    PDF_TEXT_DENSITY_THRESHOLD: int = Field(default=100)
    PARSE_MIN_TEXT_LENGTH: int = Field(default=50)
    # OCR 后端：paddle / stub（无 paddleocr 安装时自动降级）
    OCR_BACKEND: str = Field(default="stub")

    # === Service ports ===
    BACKEND_PORT: int = Field(default=8000)
    FRONTEND_PORT: int = Field(default=3001)

    # === CORS（逗号分隔的来源列表） ===
    CORS_ALLOWED_ORIGINS: str = Field(
        default="http://localhost:3001,http://127.0.0.1:3001"
    )

    @property
    def cors_origins(self) -> list[str]:
        """CORS 来源列表（拆分 + 去空格）。"""
        return [o.strip() for o in self.CORS_ALLOWED_ORIGINS.split(",") if o.strip()]


@lru_cache(maxsize=1)
def get_settings() -> Settings:
    """Cached settings accessor."""
    return Settings()


# 便捷别名
settings = get_settings()
