"""pytest 全局 fixtures。

任务 2 阶段：仅提供 RSA 密钥临时生成 + structlog 静默 fixture。
任务 3+ 将扩展 DB / Redis / Celery mock fixtures。

⚠️ 开发库防护门：集成测试的 TRUNCATE fixture 直连 ``settings.DATABASE_URL``，
若在开发库（容器内 postgres:5432/autohr）上跑会清空真实数据。收集阶段
fail-fast（见 ``_guard_dev_database``），需在开发库跑时显式设
``AUTOHR_ALLOW_DEV_DB_TESTS=1``。CI（testcontainers 临时库，库名 autohr_test）
与本地 SQLite 不受影响。
"""
from __future__ import annotations

import os
import sys
from typing import Any

import pytest

# ============================================================================
# 开发库防护门（import 时即执行，早于任何 fixture/engine 使用）
# ============================================================================


def _guard_dev_database() -> None:
    """拒绝在开发库上跑集成测试（防 TRUNCATE 清库事故）。

    判定（同时满足）：
    - DATABASE_URL 指向 PG（容器内开发库主机名为 ``postgres``，compose 服务名）
    - 库名为 ``autohr``（开发库名；CI 临时库为 ``autohr_test``，SQLite 为文件路径）
    - 未显式设 ``AUTOHR_ALLOW_DEV_DB_TESTS=1``（逃生门：有意在开发库跑时使用）
    """
    url = os.environ.get("DATABASE_URL", "")
    allowed = os.environ.get("AUTOHR_ALLOW_DEV_DB_TESTS") == "1"
    if allowed or "postgres" not in url:
        return
    # 提取库名：postgresql+asyncpg://user:pass@host:port/dbname
    path = url.rsplit("/", 1)[-1].split("?")[0]
    if path == "autohr":
        sys.exit(
            "\n🛑 拒绝在开发库上跑集成测试！\n"
            "DATABASE_URL 指向开发库 postgres/autohr，而集成测试的 TRUNCATE 会清空全部真实数据。\n"
            "  - 本地跑：用 SQLite（DATABASE_URL=sqlite+aiosqlite:///./data/test.db）\n"
            "  - CI：用 testcontainers 临时库（autohr_test）\n"
            "  - 确实要在开发库跑：AUTOHR_ALLOW_DEV_DB_TESTS=1 pytest ...（自担风险）\n"
        )


_guard_dev_database()

# ============================================================================
# 测试环境 FERNET_KEY：本地与 CI 口径对齐
# ============================================================================
# EncryptedString 在 FERNET_KEY 为空时设计上明文降级（开发期脚手架行为），
# 本地 .env 通常没有 key → 加密相关断言（PII encrypted at rest 等）必然失败。
# 这里 setdefault 注入与 CI 相同的测试 key（用户已有值时不覆盖），让
# 「本地裸跑」与「CI testcontainers」验证同一加密行为。

os.environ.setdefault(
    "FERNET_KEY", "ZDnD3sL8r6M5hV9kq2bC7fXw4pJ1aG0yY7uS8tE5oRk="
)

# ============================================================================
# pytest-asyncio 1.x: 让所有 async 测试共享 session 级 event loop
# ============================================================================


def pytest_collection_modifyitems(config: pytest.Config, items: list[pytest.Item]) -> None:
    """给每个 async 测试注入 loop_scope=session，避免与全局 async engine 的连接池跨 loop。"""
    for item in items:
        # asyncio_mode=auto 时所有 async def 测试自动加 asyncio marker
        marker = item.get_closest_marker("asyncio")
        if marker is not None and "loop_scope" not in marker.kwargs:
            item.add_marker(pytest.mark.asyncio(loop_scope="session"))


# ============================================================================
# RSA 密钥对（用于 JWT RS256 测试，用 cryptography 库生成，不依赖系统 openssl）
# ============================================================================


@pytest.fixture(scope="session")
def rsa_keys(tmp_path_factory: pytest.TempPathFactory) -> dict[str, str]:
    """生成临时 RSA 2048 密钥对，并直接修改全局 settings 实例的路径属性。

    关键点：
    - 直接 mutate ``settings`` 实例，不依赖 ``lru_cache`` 与环境变量
      （因为 ``security.py`` / ``logging.py`` 在模块加载时已经 ``from app.core.config
      import settings``，绑定了旧实例）。
    - 测试结束后还原原值，避免污染后续测试。
    """
    from cryptography.hazmat.primitives import serialization
    from cryptography.hazmat.primitives.asymmetric import rsa

    from app.core.config import settings
    from app.core.security import reset_key_cache

    keys_dir = tmp_path_factory.mktemp("jwt_keys")
    private_path = keys_dir / "private.pem"
    public_path = keys_dir / "public.pem"

    private_key = rsa.generate_private_key(public_exponent=65537, key_size=2048)

    private_path.write_bytes(
        private_key.private_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PrivateFormat.PKCS8,
            encryption_algorithm=serialization.NoEncryption(),
        )
    )
    public_path.write_bytes(
        private_key.public_key().public_bytes(
            encoding=serialization.Encoding.PEM,
            format=serialization.PublicFormat.SubjectPublicKeyInfo,
        )
    )

    # 保存原值，直接 mutate settings 实例
    original_priv_path = settings.JWT_PRIVATE_KEY_PATH
    original_pub_path = settings.JWT_PUBLIC_KEY_PATH
    settings.JWT_PRIVATE_KEY_PATH = str(private_path)
    settings.JWT_PUBLIC_KEY_PATH = str(public_path)

    # 清空 security 模块可能已缓存的密钥字符串
    reset_key_cache()

    yield {
        "private": private_path.read_text(encoding="utf-8"),
        "public": public_path.read_text(encoding="utf-8"),
        "private_path": str(private_path),
        "public_path": str(public_path),
    }

    # 还原 settings 原值
    settings.JWT_PRIVATE_KEY_PATH = original_priv_path
    settings.JWT_PUBLIC_KEY_PATH = original_pub_path
    reset_key_cache()


@pytest.fixture(autouse=True)
def reset_structlog_context() -> Any:
    """每个测试前后清理 structlog contextvars，避免跨测试污染。"""
    import structlog

    structlog.contextvars.clear_contextvars()
    yield
    structlog.contextvars.clear_contextvars()


@pytest.fixture(autouse=True)
def reset_rate_limit_buckets() -> Any:
    """每个测试前后清空认证限流计数（否则同 IP 连续注册的用例会误中 429）。"""
    from app.core import rate_limit

    rate_limit.reset()
    yield
    rate_limit.reset()
