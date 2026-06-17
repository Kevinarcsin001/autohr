"""pytest 全局 fixtures。

任务 2 阶段：仅提供 RSA 密钥临时生成 + structlog 静默 fixture。
任务 3+ 将扩展 DB / Redis / Celery mock fixtures。
"""
from __future__ import annotations

from typing import Any

import pytest


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
